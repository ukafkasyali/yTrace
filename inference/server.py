"""Local, single-inference HTTP bridge; inference itself lives in runtime.py."""
from __future__ import annotations

import argparse
import bisect
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import math
from pathlib import Path
import threading
import time
import traceback
from urllib.parse import parse_qs, unquote, urlsplit
import uuid

try:
    from .cnn_runtime import CnnRuntime
except ImportError:  # pragma: no cover - supports `python inference/server.py`
    from cnn_runtime import CnnRuntime

DATASET_ID = "zenodo-21927431"
MAX_BODY = 16_384
TERMINAL = {"answer.completed", "query.error", "query.cancelled"}


class ApiError(Exception):
    def __init__(self, status, code, message, retryable=False):
        super().__init__(message)
        self.status = status
        self.error = {"code": code, "message": message, "retryable": retryable}


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_data(data):
    """Fail startup on malformed telemetry instead of serving invented replacements."""
    recording = data["recording"]
    duration = recording["durationSeconds"]
    if not finite(duration) or duration <= 0 or recording["channelCount"] != 7:
        raise ValueError("Invalid recording metadata")
    for key in ("sampleRateHz", "displaySampleRateHz"):
        if not finite(recording[key]) or recording[key] <= 0:
            raise ValueError("Invalid sampling rate")
    ids = [c["id"] for c in data["channels"]]
    if len(ids) != 7 or len(set(ids)) != 7:
        raise ValueError("Expected seven distinct channels")
    detail = data["detail"]
    if not (finite(detail["startSeconds"]) and finite(detail["endSeconds"]) and
            0 <= detail["startSeconds"] < detail["endSeconds"] <= duration):
        raise ValueError("Invalid detail bounds")
    for source in (data, detail):
        times = source["times"]
        if not times or any(not finite(t) or (i > 0 and t <= times[i - 1]) for i, t in enumerate(times)):
            raise ValueError("Timestamps must be finite and strictly increasing")
        lower = detail["startSeconds"] if source is detail else 0
        upper = detail["endSeconds"] if source is detail else duration
        if times[0] < lower or times[-1] > upper:
            raise ValueError("Timestamps exceed declared bounds")
        if [c["id"] for c in source["channels"]] != ids:
            raise ValueError("Channel identities differ")
        for c in source["channels"]:
            if not c["unit"] or len(c["values"]) != len(times) or any(not finite(v) for v in c["values"]):
                raise ValueError("Invalid signal values")
    for event in data["events"]:
        if event["kind"] != "publisher_annotation" or not finite(event["timeSeconds"]) or not 0 <= event["timeSeconds"] <= duration:
            raise ValueError("Invalid publisher marker")


def envelope_indices(values, budget):
    """Retain original extrema samples and endpoints without interpolation."""
    n = len(values)
    if n <= budget:
        return list(range(n))
    if budget == 1:
        return [max(range(n), key=lambda i: abs(values[i]))]
    if budget == 2:
        return [0, n - 1]
    if budget == 3:
        return [0, max(range(1, n - 1), key=lambda i: abs(values[i])), n - 1]
    selected = {0, n - 1}
    buckets = (budget - 2) // 2
    for b in range(buckets):
        first = 1 + b * (n - 2) // buckets
        last = 1 + (b + 1) * (n - 2) // buckets
        selected.add(min(range(first, last), key=values.__getitem__))
        selected.add(max(range(first, last), key=values.__getitem__))
    return sorted(selected)


def measured_summary(series):
    """Summarise exact submitted samples; annotations are never used as evidence."""
    rows = []
    for channel in series:
        values = channel["values"]
        if values:
            lo, hi = min(values), max(values)
            rows.append({"channel": channel["channelId"], "range": hi - lo,
                         "peak": max(abs(value) for value in values)})
    return sorted(rows, key=lambda row: (row["range"], row["peak"]), reverse=True)


def present_generation(generation, summary):
    """Make imperfect generative output legible without promoting it to fact."""
    ranges = ", ".join(
        f"{row['channel'].replace('joint_', 'Joint ')} ({row['range']:.3f} Nm range)"
        for row in summary[:3]
    )
    # Smoke checkpoints may append malformed JSON. Preserve readable text and retain
    # the original generation separately in the response payload for debugging.
    lead = " ".join(generation.split("Answer:", 1)[0].split())
    interpretation = f"OpenTSLM generated: {lead}" if lead else "OpenTSLM returned no readable natural-language interpretation."
    return (
        f"Measured in this selected window\nLargest observed torque ranges: {ranges}.\n\n"
        f"OpenTSLM interpretation\n{interpretation} Treat this generated interpretation as a lead, not a verified event explanation."
    )


class Job:
    def __init__(self, request):
        self.id = uuid.uuid4().hex
        self.request = request
        self.cancelled = threading.Event()
        self.condition = threading.Condition()
        self.events = []
        self.finished_at = None

    def emit(self, kind, payload):
        with self.condition:
            if self.finished_at is not None:
                return
            self.events.append({"id": str(len(self.events) + 1), "queryId": self.id,
                                "type": kind, "payload": payload})
            if kind in TERMINAL:
                self.finished_at = time.monotonic()
            self.condition.notify_all()


class Bridge:
    def __init__(self, runtime, data_path, cnn_runtime=None):
        self.runtime = runtime
        self.cnn_runtime = cnn_runtime or CnnRuntime()
        raw = Path(data_path).read_bytes()
        self.data = json.loads(raw)
        validate_data(self.data)
        self.revision = hashlib.sha256(raw).hexdigest()
        self.lock = threading.Lock()
        self.jobs = {}
        self.active = None
        self.finished_limit = 100
        self.finished_ttl = 1800

    def prune(self):
        # Caller holds self.lock. A cancelled worker remains active until it exits.
        finished = sorted((j for j in self.jobs.values() if j.finished_at is not None and j.id != self.active),
                          key=lambda j: j.finished_at)
        excess = max(0, len(finished) - self.finished_limit)
        now = time.monotonic()
        for index, job in enumerate(finished):
            if index < excess or now - job.finished_at > self.finished_ttl:
                self.jobs.pop(job.id, None)

    def get_job(self, query_id):
        with self.lock:
            self.prune()
            job = self.jobs.get(query_id)
        if job is None:
            raise ApiError(404, "QUERY_NOT_FOUND", "The query does not exist or has expired.")
        return job

    def validate_window(self, window, raw_only=False):
        if not isinstance(window, dict):
            raise ApiError(400, "INVALID_WINDOW", "A telemetry window is required.")
        if window.get("datasetId") != DATASET_ID or window.get("recordingId") != self.data["recording"]["id"]:
            raise ApiError(404, "RECORDING_NOT_FOUND", "Unknown dataset or recording.")
        start, end = window.get("startSec"), window.get("endSec")
        duration = self.data["recording"]["durationSeconds"]
        if not finite(start) or not finite(end) or not 0 <= start < end <= duration:
            raise ApiError(400, "INVALID_WINDOW", "Choose a nonempty interval within the recording.")
        ids = window.get("channelIds")
        known = {c["id"] for c in self.data["channels"]}
        if not isinstance(ids, list) or not 1 <= len(ids) <= 7 or any(not isinstance(i, str) or i not in known for i in ids) or len(set(ids)) != len(ids):
            raise ApiError(400, "INVALID_CHANNELS", "Select one to seven distinct known channels.")
        detail = self.data["detail"]
        if raw_only and (start < detail["startSeconds"] or end > detail["endSeconds"]):
            raise ApiError(422, "RAW_DATA_UNAVAILABLE", "Inference requires a window entirely within the raw 4–9 second excerpt.")
        if raw_only and end - start > 2 + 1e-9:
            raise ApiError(422, "WINDOW_TOO_LONG", "Inference windows must be at most two seconds.")
        return {"datasetId": DATASET_ID, "recordingId": self.data["recording"]["id"],
                "startSec": start, "endSec": end, "channelIds": list(ids)}

    def signals(self, window, max_points=None):
        detail = self.data["detail"]
        raw = window["startSec"] >= detail["startSeconds"] and window["endSec"] <= detail["endSeconds"]
        source = detail if raw else self.data
        lo = bisect.bisect_left(source["times"], window["startSec"])
        hi = bisect.bisect_left(source["times"], window["endSec"])
        if hi <= lo:
            raise ApiError(422, "EMPTY_WINDOW", "There are no samples in this interval.")
        times = source["times"][lo:hi]
        channels = {c["id"]: c for c in source["channels"]}
        series = []
        decimated = max_points is not None and len(times) > max_points
        for channel_id in window["channelIds"]:
            values = channels[channel_id]["values"][lo:hi]
            indices = envelope_indices(values, max_points) if decimated else range(len(times))
            series.append({"channelId": channel_id, "timeSec": [times[i] for i in indices],
                           "values": [values[i] for i in indices]})
        result = {"window": window, "series": series, "resolution": "raw" if raw and not decimated else "display"}
        if not raw or decimated:
            result["aggregation"] = ("Raw excerpt" if raw else "100 Hz overview subsampling") + ("; per-channel min/max display envelope" if decimated else "; short peaks may be absent")
        return result

    def start(self, request):
        if not isinstance(request, dict):
            raise ApiError(400, "INVALID_QUERY", "Expected a query object.")
        mode = request.get("mode")
        if mode not in ("direct", "assistant"):
            raise ApiError(422, "MODE_UNAVAILABLE", "Choose direct OpenTSLM or the telemetry assistant.")
        requested_model = request.get("modelId")
        if mode == "assistant":
            selected_model, selected_runtime = "opentslm", self.runtime
        elif requested_model in ("opentslm", self.runtime.model_id):
            selected_model, selected_runtime = "opentslm", self.runtime
        elif requested_model in ("cnn-1d", self.cnn_runtime.model_id) and self.cnn_runtime.enabled:
            selected_model, selected_runtime = "cnn-1d", self.cnn_runtime
        else:
            raise ApiError(422, "MODEL_UNAVAILABLE", "The selected model is not connected.")
        question = request.get("question")
        if not isinstance(question, str) or not question.strip() or len(question) > 4000:
            raise ApiError(400, "INVALID_QUERY", "Enter a question of one to 4000 characters.")
        window = self.validate_window(request.get("window"), raw_only=True)
        playhead = request.get("playheadSec")
        if not finite(playhead) or not window["endSec"] <= playhead <= self.data["recording"]["durationSeconds"]:
            raise ApiError(400, "FUTURE_CONTEXT", "The query window must not extend beyond the playback cursor.")
        if not selected_runtime.ready:
            reason = selected_runtime.error or "Model loading"
            raise ApiError(503, "MODEL_NOT_READY", reason, True)
        series = self.signals(window)["series"]
        if selected_model == "cnn-1d":
            try:
                selected_runtime.validate_series(series)
            except ValueError as error:
                raise ApiError(422, "INVALID_CNN_INPUT", str(error)) from error
        cleaned = {"mode": mode, "modelId": selected_model, "question": question.strip(),
                   "window": window, "playheadSec": playhead}
        with self.lock:
            self.prune()
            if self.active is not None:
                raise ApiError(409, "MODEL_BUSY", "Another inference is still running. Try again after it completes.", True)
            job = Job(cleaned)
            self.jobs[job.id] = job
            self.active = job.id
        threading.Thread(target=self.run, args=(job, series), daemon=True).start()
        return {"queryId": job.id, "streamUrl": f"/api/queries/{job.id}/events"}

    def run(self, job, series):
        call_id = f"inference-{job.id}"
        try:
            if job.cancelled.is_set():
                return
            summary = measured_summary(series)
            if job.request["mode"] == "assistant":
                measurement_id = f"measure-{job.id}"
                job.emit("tool.started", {"callId": measurement_id, "tool": "measurement_summary", "label": "Measuring torque ranges in the selected samples"})
                job.emit("tool.completed", {"callId": measurement_id, "summary": "Calculated per-joint torque ranges from the selected raw telemetry."})
            if job.request["modelId"] == "cnn-1d":
                job.emit("tool.started", {"callId": call_id, "tool": "cnn-1d", "label": "Running 1D CNN on selected raw telemetry"})
                prediction = self.cnn_runtime.predict(series, job.cancelled)
                if job.cancelled.is_set():
                    return
                job.emit("tool.completed", {"callId": call_id, "summary": "1D CNN classification completed."})
                evidence = []
                onset = prediction.get("onset_sample")
                if onset is not None:
                    start = job.request["window"]["startSec"] + float(onset) / 1000
                    end = min(job.request["window"]["endSec"], start + 0.05)
                    evidence.append({"id": f"cnn-onset-{job.id}", "window": {**job.request["window"], "startSec": start, "endSec": end},
                                     "label": "CNN predicted onset", "source": f"cnn-1d@{self.cnn_runtime.revision}"})
                job.emit("answer.completed", {"modelId": "cnn-1d", "modelRevision": self.cnn_runtime.revision,
                                                "labels": prediction["labels"], "evidence": evidence})
                return
            job.emit("tool.started", {"callId": call_id, "tool": "opentslm", "label": "Running OpenTSLM on selected raw telemetry"})
            answer = self.runtime.generate(job.request, series, job.cancelled)
            if job.cancelled.is_set():
                return
            if not isinstance(answer, str) or not answer.strip() or len(answer) > 32_000:
                raise ValueError("Runtime returned invalid or oversized output")
            job.emit("tool.completed", {"callId": call_id, "summary": "OpenTSLM generation completed; explanation has not been independently verified."})
            job.emit("answer.completed", {"answer": present_generation(answer, summary), "modelOutput": answer, "modelId": self.runtime.model_id,
                     "modelRevision": self.runtime.revision, "inputTrace": getattr(self.runtime, "last_trace", None),
                     "evidence": [{"id": f"input-{job.id}",
                     "window": job.request["window"], "label": "Input telemetry (not a verified explanation)",
                     "source": f"{self.runtime.model_id}@{self.runtime.revision}"}], "measurements": summary[:3]})
        except Exception:
            traceback.print_exc()
            if not job.cancelled.is_set():
                job.emit("query.error", {"code": "INFERENCE_FAILED", "message": "Model inference failed. See server logs for details.", "retryable": True})
        finally:
            if job.cancelled.is_set():
                job.emit("query.cancelled", {})
            with self.lock:
                if self.active == job.id:
                    self.active = None
                self.prune()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    @property
    def bridge(self):
        return self.server.bridge

    def log_message(self, format, *args):
        # Do not log questions, request bodies, or credentials.
        pass

    def json_response(self, status, data=None):
        body = b"" if status == 204 else json.dumps(data, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        if body:
            self.wfile.write(body)

    def handle_api(self, method):
        try:
            url = urlsplit(self.path)
            parts = [unquote(p) for p in url.path.strip("/").split("/")]
            if parts[:1] != ["api"]:
                raise ApiError(404, "NOT_FOUND", "Unknown API endpoint.")
            route = parts[1:]
            if method == "GET":
                self.get(route, parse_qs(url.query))
            elif method == "POST" and route == ["queries"]:
                if self.headers.get("Transfer-Encoding"):
                    raise ApiError(400, "INVALID_BODY", "Chunked request bodies are not supported.")
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    raise ApiError(400, "INVALID_BODY", "Invalid Content-Length.") from None
                if not 0 < size <= MAX_BODY:
                    raise ApiError(413, "BODY_TOO_LARGE", "Query body must be between one byte and 16 KiB.")
                if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                    raise ApiError(415, "INVALID_CONTENT_TYPE", "Use application/json.")
                self.connection.settimeout(10)
                try:
                    request = json.loads(self.rfile.read(size))
                except (ValueError, UnicodeDecodeError, TimeoutError):
                    raise ApiError(400, "INVALID_JSON", "Query body must contain valid JSON.") from None
                self.json_response(202, self.bridge.start(request))
            elif method == "DELETE" and len(route) == 2 and route[0] == "queries":
                job = self.bridge.get_job(route[1])
                with job.condition:
                    if job.finished_at is None:
                        job.cancelled.set()
                        job.emit("query.cancelled", {})
                self.json_response(204)
            else:
                raise ApiError(404, "NOT_FOUND", "This capability is not connected.")
        except ApiError as error:
            self.json_response(error.status, {"error": error.error})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            traceback.print_exc()
            self.json_response(500, {"error": {"code": "SERVER_ERROR", "message": "The service could not complete the request.", "retryable": True}})

    def do_GET(self):
        self.handle_api("GET")

    def do_POST(self):
        self.handle_api("POST")

    def do_DELETE(self):
        self.handle_api("DELETE")

    def get(self, route, query):
        bridge = self.bridge
        data = bridge.data
        recording = data["recording"]
        if route == ["health"]:
            self.json_response(200, {"ready": bool(bridge.runtime.ready), "modelId": bridge.runtime.model_id,
                                    "revision": bridge.runtime.revision,
                                    "status": "ready" if bridge.runtime.ready else "unavailable" if bridge.runtime.error else "loading"})
        elif route == ["datasets"]:
            self.json_response(200, [{"id": DATASET_ID, "name": "KUKA accidental collision telemetry",
                                    "sourceUrl": recording["sourceUrl"], "revision": bridge.revision}])
        elif len(route) == 3 and route[0] == "datasets" and route[2] == "recordings":
            if route[1] != DATASET_ID:
                raise ApiError(404, "DATASET_NOT_FOUND", "Unknown dataset.")
            self.json_response(200, [{"id": recording["id"], "datasetId": DATASET_ID, "name": recording["name"],
                     "durationSec": recording["durationSeconds"], "channels": [{"id": c["id"], "name": c["name"],
                     "unit": c["unit"], "sampleRateHz": recording["sampleRateHz"]} for c in data["channels"]]}])
        elif route == ["models"]:
            self.json_response(200, [{"id": "assistant", "label": "Telemetry assistant", "available": bool(bridge.runtime.ready),
                     "capabilities": ["language"], "revision": bridge.runtime.revision,
                     **({} if bridge.runtime.ready else {"reason": "Model unavailable; check server logs" if bridge.runtime.error else "Model loading"})},
                    {"id": "cnn-1d", "label": "1D CNN", "available": bool(bridge.cnn_runtime.ready),
                     "capabilities": ["classification", "localization"], "revision": bridge.cnn_runtime.revision,
                     **({} if bridge.cnn_runtime.ready else {"reason": bridge.cnn_runtime.error or "Model loading"})},
                    {"id": "opentslm", "label": "OpenTSLM", "available": bool(bridge.runtime.ready),
                     "capabilities": ["language", "classification", "localization"], "revision": bridge.runtime.revision,
                     **({} if bridge.runtime.ready else {"reason": "Model unavailable; check server logs" if bridge.runtime.error else "Model loading"})}])
        elif len(route) == 3 and route[0] == "recordings":
            if route[1] != recording["id"]:
                raise ApiError(404, "RECORDING_NOT_FOUND", "Unknown recording.")
            if route[2] == "events":
                self.json_response(200, [{"id": e["id"], "recordingId": recording["id"], "startSec": e["timeSeconds"],
                     "channelIds": [], "label": e["label"], "origin": "publisher_annotation", "source": e["source"]} for e in data["events"]])
            elif route[2] == "signals":
                try:
                    window = {"datasetId": DATASET_ID, "recordingId": recording["id"],
                              "startSec": float(query["startSec"][0]), "endSec": float(query["endSec"][0]),
                              "channelIds": query["channelIds"][0].split(",")}
                    max_points = int(query.get("maxPoints", ["2000"])[0])
                except (KeyError, ValueError, IndexError):
                    raise ApiError(400, "INVALID_WINDOW", "Supply valid interval, channel IDs and point budget.") from None
                if not 1 <= max_points <= 200_000:
                    raise ApiError(400, "INVALID_WINDOW", "Point budget must be between one and 200000.")
                self.json_response(200, bridge.signals(bridge.validate_window(window), max_points))
            else:
                raise ApiError(404, "NOT_FOUND", "Unknown recording endpoint.")
        elif len(route) == 3 and route[0] == "queries" and route[2] == "events":
            self.stream(bridge.get_job(route[1]))
        else:
            raise ApiError(404, "NOT_FOUND", "This capability is not connected.")

    def stream(self, job):
        try:
            last_id = int(self.headers.get("Last-Event-ID", "0"))
            if last_id < 0:
                raise ValueError()
        except ValueError:
            raise ApiError(400, "INVALID_EVENT_ID", "Last-Event-ID must be a nonnegative integer.") from None
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        while True:
            with job.condition:
                pending = [event for event in job.events if int(event["id"]) > last_id]
                if not pending and job.finished_at is None:
                    job.condition.wait(timeout=10)
                    pending = [event for event in job.events if int(event["id"]) > last_id]
                done = job.finished_at is not None
            for event in pending:
                self.wfile.write(f"id: {event['id']}\nevent: {event['type']}\ndata: {json.dumps(event, allow_nan=False)}\n\n".encode())
                last_id = int(event["id"])
            if not pending and not done:
                self.wfile.write(b": keepalive\n\n")
            self.wfile.flush()
            if done:
                return


def make_server(runtime, host="127.0.0.1", port=8000, data_path=None, load_runtime=True, cnn_runtime=None):
    path = data_path or Path(__file__).resolve().parents[1] / "frontend/public/data/kuka-demo.json"
    bridge = Bridge(runtime, path, cnn_runtime=cnn_runtime)
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    server.bridge = bridge
    if load_runtime:
        threading.Thread(target=runtime.load, daemon=True).start()
        if bridge.cnn_runtime.enabled:
            threading.Thread(target=bridge.cnn_runtime.load, daemon=True).start()
    return server


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--data", type=Path)
    args = parser.parse_args()
    try:
        from .runtime import Runtime
    except ImportError:
        from runtime import Runtime
    server = make_server(Runtime(), host=args.host, port=args.port, data_path=args.data, cnn_runtime=CnnRuntime())
    print(f"Inference bridge listening on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
