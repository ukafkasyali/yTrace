"""Local, single-inference HTTP bridge; inference itself lives in runtime.py."""
from __future__ import annotations

import argparse
import bisect
import hashlib
import gzip
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
    from .raw_recordings import CHANNEL_IDS, RawRecordingCatalog
except ImportError:  # pragma: no cover - direct script execution
    from raw_recordings import CHANNEL_IDS, RawRecordingCatalog

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
    """Render supported model fields without presenting predictions as measurements."""
    ranges = ", ".join(
        f"{row['channel'].replace('joint_', 'Joint ')} ({row['range']:.3f} Nm range)"
        for row in summary[:3]
    )
    answer_at = generation.casefold().rfind("answer:")
    brace = generation.find("{", answer_at if answer_at >= 0 else 0)
    if brace < 0:
        raise ValueError("OpenTSLM output has no structured answer")
    try:
        prediction, _ = json.JSONDecoder().raw_decode(generation[brace:])
    except json.JSONDecodeError as error:
        raise ValueError("OpenTSLM output has invalid structured answer") from error
    if not isinstance(prediction, dict):
        raise ValueError("OpenTSLM structured answer is not an object")
    lines = []
    contact = prediction.get("contact")
    event_type = prediction.get("event_type")
    if isinstance(contact, bool):
        lines.append("OpenTSLM predicts external contact." if contact else "OpenTSLM predicts free motion without external contact.")
    if event_type in ("free", "intentional", "accidental"):
        lines.append(f"Interaction class: {event_type}.")
    strongest = prediction.get("strongest_joint")
    if isinstance(strongest, str) and strongest:
        lines.append(f"Strongest predicted disturbance: {strongest}.")
    affected = prediction.get("affected_joints")
    if isinstance(affected, list) and affected and all(isinstance(item, str) for item in affected):
        lines.append(f"Affected joints: {', '.join(affected)}.")
    onset = prediction.get("onset_ms")
    if isinstance(onset, (int, float)) and not isinstance(onset, bool):
        lines.append(f"Predicted onset: {onset:g} ms after the window starts.")
    evidence_start, evidence_end = prediction.get("evidence_start_ms"), prediction.get("evidence_end_ms")
    if all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in (evidence_start, evidence_end)):
        lines.append(f"Predicted evidence interval: {evidence_start:g}–{evidence_end:g} ms.")
    if not lines:
        raise ValueError("OpenTSLM output contains no supported prediction fields")
    interpretation = " ".join(lines)
    return (
        f"Measured in this selected window\nLargest observed torque ranges: {ranges}.\n\n"
        f"OpenTSLM interpretation\n{interpretation} Treat these generated predictions as leads, not verified physical facts."
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
    def __init__(self, runtime, data_path, catalog_path=None, raw_root=None):
        self.runtime = runtime
        raw = Path(data_path).read_bytes()
        self.data = json.loads(raw)
        validate_data(self.data)
        self.recordings = {self.data["recording"]["id"]: self.data}
        self.raw_recordings = {}
        self.cases = []
        identity = hashlib.sha256(raw)
        if catalog_path is not None:
            catalog_path = Path(catalog_path)
            catalog_raw = catalog_path.read_bytes()
            catalog = json.loads(catalog_raw)
            identity.update(catalog_raw)
            for filename in catalog["recordings"]:
                content = gzip.decompress((catalog_path.parent / filename).read_bytes())
                item = json.loads(content)
                validate_data(item)
                rid = item["recording"]["id"]
                if rid in self.recordings:
                    raise ValueError("Duplicate recording identity")
                self.recordings[rid] = item
                identity.update(content)
            self.cases = catalog["cases"]
            for case in self.cases:
                self.validate_window({"datasetId": DATASET_ID, "recordingId": case["recordingId"],
                    "startSec": case["interval"]["start"], "endSec": case["interval"]["end"],
                    "channelIds": [f"joint_{i}" for i in range(1, 8)]}, raw_only=True)
        if raw_root is not None:
            raw_catalog = RawRecordingCatalog(raw_root)
            self.raw_recordings = raw_catalog.recordings
            # Source-backed recordings can extend a demo excerpt but must retain its
            # stable recording ID and existing display/annotation contract.
            identity.update("".join(sorted(record.torque_sha256 + record.marker_sha256
                                            for record in self.raw_recordings.values())).encode())
        self.revision = identity.hexdigest()
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
        if (window.get("datasetId") != DATASET_ID or
                window.get("recordingId") not in self.recordings and window.get("recordingId") not in self.raw_recordings):
            raise ApiError(404, "RECORDING_NOT_FOUND", "Unknown dataset or recording.")
        data = self.recordings.get(window["recordingId"])
        raw_recording = self.raw_recordings.get(window["recordingId"])
        start, end = window.get("startSec"), window.get("endSec")
        duration = raw_recording.duration_seconds if raw_recording else data["recording"]["durationSeconds"]
        if not finite(start) or not finite(end) or not 0 <= start < end <= duration:
            raise ApiError(400, "INVALID_WINDOW", "Choose a nonempty interval within the recording.")
        ids = window.get("channelIds")
        known = set(CHANNEL_IDS) if raw_recording else {c["id"] for c in data["channels"]}
        if not isinstance(ids, list) or not 1 <= len(ids) <= 7 or any(not isinstance(i, str) or i not in known for i in ids) or len(set(ids)) != len(ids):
            raise ApiError(400, "INVALID_CHANNELS", "Select one to seven distinct known channels.")
        detail = data["detail"] if data else None
        if raw_only and not raw_recording and (start < detail["startSeconds"] or end > detail["endSeconds"]):
            raise ApiError(422, "RAW_DATA_UNAVAILABLE", f"Inference requires raw samples within [{detail['startSeconds']:g}, {detail['endSeconds']:g}) seconds.")
        if raw_only:
            if ids != list(CHANNEL_IDS):
                raise ApiError(422, "MODEL_INPUT_SHAPE", "OpenTSLM requires Joint 1 through Joint 7 in order.")
            if abs(end - start - 1.024) > 1e-8:
                raise ApiError(422, "MODEL_INPUT_SHAPE", "Select exactly 1.024 seconds (1024 raw samples per joint).")
            try:
                times = raw_recording.raw_window(start, end, ids)[0] if raw_recording else detail["times"][bisect.bisect_left(detail["times"], start):bisect.bisect_left(detail["times"], end)]
            except ValueError as error:
                raise ApiError(422, "RAW_DATA_UNAVAILABLE", "The requested source window is unavailable or gapped.") from error
            if ((raw_recording is None and data["recording"]["sampleRateHz"] != 1000) or len(times) != 1024 or
                    any(abs(t - (start + i / 1000)) > 1e-8 for i, t in enumerate(times))):
                raise ApiError(422, "MODEL_INPUT_SHAPE", "OpenTSLM requires 1024 contiguous raw samples at 1 kHz, aligned to the selected start.")
        return {"datasetId": DATASET_ID, "recordingId": window["recordingId"],
                "startSec": start, "endSec": end, "channelIds": list(ids)}

    def signals(self, window, max_points=None):
        raw_recording = self.raw_recordings.get(window["recordingId"])
        if raw_recording:
            try:
                # Keep broad retrievals display-only, while allowing a caller to
                # explicitly retrieve one model-sized source window at raw rate.
                raw_requested = (window["endSec"] - window["startSec"] <= 1.024 + 1e-8 and
                                 (max_points is None or max_points >= 1024))
                if raw_requested:
                    times, rows = raw_recording.raw_window(window["startSec"], window["endSec"], window["channelIds"])
                else:
                    times, rows = raw_recording.display_window(window["startSec"], window["endSec"], window["channelIds"])
            except ValueError as error:
                raise ApiError(422, "RAW_DATA_UNAVAILABLE", "The requested source window is unavailable or gapped.") from error
            decimated = max_points is not None and len(times) > max_points
            series = []
            for channel_id, values in zip(window["channelIds"], rows):
                indices = envelope_indices(values, max_points) if decimated else range(len(times))
                series.append({"channelId": channel_id, "timeSec": [times[index] for index in indices],
                               "values": [values[index] for index in indices]})
            result = {"window": window, "series": series,
                      "resolution": "raw" if raw_requested and not decimated else "display"}
            if result["resolution"] == "display":
                result["aggregation"] = "100 Hz overview subsampling; short peaks may be absent"
                if decimated:
                    result["aggregation"] += "; per-channel min/max display envelope"
            return result
        data = self.recordings[window["recordingId"]]
        detail = data["detail"]
        raw = window["startSec"] >= detail["startSeconds"] and window["endSec"] <= detail["endSeconds"]
        source = detail if raw else data
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
        if mode == "direct" and request.get("modelId") not in ("opentslm", self.runtime.model_id):
            raise ApiError(422, "MODEL_UNAVAILABLE", "The selected model is not connected.")
        question = request.get("question")
        if not isinstance(question, str) or not question.strip() or len(question) > 4000:
            raise ApiError(400, "INVALID_QUERY", "Enter a question of one to 4000 characters.")
        window = self.validate_window(request.get("window"), raw_only=True)
        playhead = request.get("playheadSec")
        duration = (self.raw_recordings[window["recordingId"]].duration_seconds
                    if window["recordingId"] in self.raw_recordings
                    else self.recordings[window["recordingId"]]["recording"]["durationSeconds"])
        if not finite(playhead) or not window["endSec"] <= playhead <= duration:
            raise ApiError(400, "FUTURE_CONTEXT", "The query window must not extend beyond the playback cursor.")
        if not self.runtime.ready:
            raise ApiError(503, "MODEL_NOT_READY", "The model is loading or unavailable. Check service health.", True)
        series = self.signals(window)["series"]
        cleaned = {"mode": mode, "modelId": "opentslm", "question": question.strip(),
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
        if route == ["demo-cases"]:
            self.json_response(200, bridge.cases)
        elif route == ["health"]:
            self.json_response(200, {"ready": bool(bridge.runtime.ready), "modelId": bridge.runtime.model_id,
                                    "revision": bridge.runtime.revision,
                                    "status": "ready" if bridge.runtime.ready else "unavailable" if bridge.runtime.error else "loading"})
        elif route == ["datasets"]:
            self.json_response(200, [{"id": DATASET_ID, "name": "KUKA contact-event telemetry",
                                    "sourceUrl": recording["sourceUrl"], "revision": bridge.revision}])
        elif len(route) == 3 and route[0] == "datasets" and route[2] == "recordings":
            if route[1] != DATASET_ID:
                raise ApiError(404, "DATASET_NOT_FOUND", "Unknown dataset.")
            records = []
            recording_ids = list(bridge.recordings) + sorted(set(bridge.raw_recordings) - set(bridge.recordings))
            for recording_id in recording_ids:
                source = bridge.raw_recordings.get(recording_id)
                if source:
                    recording = source.metadata()
                    channels = [{"id": channel_id, "name": f"Joint {index}", "unit": "Nm", "sampleRateHz": 1000}
                                for index, channel_id in enumerate(CHANNEL_IDS, 1)]
                else:
                    item = bridge.recordings[recording_id]
                    recording = item["recording"]
                    channels = [{"id": c["id"], "name": c["name"], "unit": c["unit"],
                                 "sampleRateHz": recording["sampleRateHz"]} for c in item["channels"]]
                records.append({"id": recording["id"], "datasetId": DATASET_ID, "name": recording["name"],
                                "durationSec": recording["durationSeconds"], "channels": channels})
            self.json_response(200, records)
        elif route == ["models"]:
            self.json_response(200, [{"id": "assistant", "label": "Telemetry assistant", "available": bool(bridge.runtime.ready),
                     "capabilities": ["language"], "revision": bridge.runtime.revision,
                     **({} if bridge.runtime.ready else {"reason": "Model unavailable; check server logs" if bridge.runtime.error else "Model loading"})},
                    {"id": "opentslm", "label": "OpenTSLM", "available": bool(bridge.runtime.ready),
                     "capabilities": ["language"], "revision": bridge.runtime.revision,
                     **({} if bridge.runtime.ready else {"reason": "Model unavailable; check server logs" if bridge.runtime.error else "Model loading"})}])
        elif len(route) == 3 and route[0] == "recordings":
            if route[1] not in bridge.recordings and route[1] not in bridge.raw_recordings:
                raise ApiError(404, "RECORDING_NOT_FOUND", "Unknown recording.")
            source = bridge.raw_recordings.get(route[1])
            if route[2] == "replay":
                self.json_response(200, bridge.recordings.get(route[1]) or source.replay_data())
            elif route[2] == "events":
                data = bridge.recordings.get(route[1])
                events = source.events() if source else data["events"]
                self.json_response(200, [{"id": e["id"], "recordingId": route[1], "startSec": e["timeSeconds"],
                     "channelIds": [], "label": e["label"], "origin": "publisher_annotation", "source": e["source"]} for e in events])
            elif route[2] == "signals":
                try:
                    window = {"datasetId": DATASET_ID, "recordingId": route[1],
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


def make_server(runtime, host="127.0.0.1", port=8000, data_path=None, raw_root=None, load_runtime=True):
    path = data_path or Path(__file__).resolve().parents[1] / "frontend/public/data/kuka-demo.json"
    bridge = Bridge(runtime, path, Path(__file__).with_name("demo_cases.json") if data_path is None else None, raw_root)
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    server.bridge = bridge
    if load_runtime:
        threading.Thread(target=runtime.load, daemon=True).start()
    return server


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--data", type=Path)
    parser.add_argument("--raw-root", type=Path, help="trusted local KUKA source root; never expose it through the API")
    parser.add_argument(
        "--no-load-runtime",
        action="store_true",
        help="serve fixture data without loading or downloading a model",
    )
    args = parser.parse_args()
    try:
        from .runtime import Runtime
    except ImportError:
        from runtime import Runtime
    runtime = Runtime()
    if args.no_load_runtime:
        runtime.error = "Model loading disabled for local fixture mode"
    server = make_server(
        runtime,
        host=args.host,
        port=args.port,
        data_path=args.data,
        raw_root=args.raw_root,
        load_runtime=not args.no_load_runtime,
    )
    print(f"Inference bridge listening on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
