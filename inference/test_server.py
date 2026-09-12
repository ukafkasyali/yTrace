import copy
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from inference.server import DATASET_ID, MAX_BODY, envelope_indices, make_server


class FakeRuntime:
    ready = True
    error = ""
    model_id = "test/checkpoint"
    revision = "test-revision"

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.release.set()
        self.failure = False
        self.received = None

    def load(self):
        self.ready = True

    def generate(self, request, series, cancelled):
        self.received = (request, series)
        self.entered.set()
        self.release.wait(3)
        if self.failure:
            raise RuntimeError("private runtime detail")
        return 'Answer: {"contact":true}\nEvidence: generated test evidence.'


def fixture():
    times = [i / 2 for i in range(21)]
    channels = [{"id": f"joint_{i}", "name": f"Joint {i}", "unit": "Nm", "values": [t * i for t in times]} for i in range(1, 8)]
    return {"recording": {"id": "recording", "name": "Test recording", "durationSeconds": 10,
            "channelCount": 7, "sampleRateHz": 2, "displaySampleRateHz": 2, "sourceUrl": "https://example.test"},
            "times": times, "channels": channels,
            "detail": {"startSeconds": 4, "endSeconds": 9, "times": times[8:18],
                       "channels": [{**c, "values": c["values"][8:18]} for c in channels]},
            "events": [{"id": "event", "timeSeconds": 6, "kind": "publisher_annotation", "label": "secret label", "source": "publisher"}]}


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "data.json"
        self.path.write_text(json.dumps(fixture()))
        self.runtime = FakeRuntime()
        self.server = make_server(self.runtime, port=0, data_path=self.path, load_runtime=False)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.request = {"mode": "direct", "modelId": "opentslm", "question": "Describe torque changes",
            "playheadSec": 8, "window": {"datasetId": DATASET_ID, "recordingId": "recording",
            "startSec": 4, "endSec": 5.5, "channelIds": ["joint_1", "joint_2"]}}

    def tearDown(self):
        self.runtime.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def http(self, method, path, data=None, raw=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=4)
        body = raw if raw is not None else json.dumps(data) if data is not None else None
        connection.request(method, path, body=body, headers=headers or ({"Content-Type": "application/json"} if body else {}))
        response = connection.getresponse()
        status, payload = response.status, response.read().decode()
        connection.close()
        return status, json.loads(payload) if payload and response.getheader("Content-Type") == "application/json" else payload

    def start(self):
        status, job = self.http("POST", "/api/queries", self.request)
        self.assertEqual(status, 202, job)
        return job

    def events(self, job):
        status, stream = self.http("GET", job["streamUrl"])
        self.assertEqual(status, 200)
        return [json.loads(line[6:]) for line in stream.splitlines() if line.startswith("data: ")]

    def test_catalog_models_and_health(self):
        self.assertEqual(self.http("GET", "/api/datasets")[1][0]["id"], DATASET_ID)
        self.assertEqual(len(self.http("GET", f"/api/datasets/{DATASET_ID}/recordings")[1][0]["channels"]), 7)
        self.assertTrue(self.http("GET", "/api/models")[1][0]["available"])
        self.runtime.ready = False
        self.assertFalse(self.http("GET", "/api/health")[1]["ready"])
        self.assertFalse(self.http("GET", "/api/models")[1][0]["available"])
        self.assertEqual(self.http("POST", "/api/queries", self.request)[0], 503)

    def test_half_open_windows_and_display_resolution(self):
        path = "/api/recordings/recording/signals?channelIds=joint_1&maxPoints=100&startSec=4&endSec=5"
        status, result = self.http("GET", path)
        self.assertEqual(status, 200)
        self.assertEqual(result["series"][0]["timeSec"], [4, 4.5])
        self.assertEqual(result["resolution"], "raw")
        result = self.http("GET", path.replace("startSec=4", "startSec=3"))[1]
        self.assertEqual(result["resolution"], "display")
        self.assertIn("aggregation", result)
        self.assertEqual(self.http("GET", path.replace("maxPoints=100", "maxPoints=1"))[1]["resolution"], "display")

    def test_actual_runtime_stream_and_no_publisher_labels(self):
        self.request["events"] = [{"label": "injected label"}]
        job = self.start()
        events = self.events(job)
        self.assertEqual([e["type"] for e in events], ["tool.started", "tool.completed", "answer.completed"])
        self.assertEqual([e["id"] for e in events], ["1", "2", "3"])
        answer = events[-1]["payload"]
        self.assertIn("Measured in this selected window", answer["answer"])
        self.assertIn("OpenTSLM predicts external contact", answer["answer"])
        self.assertIn('"contact":true', answer["modelOutput"])
        self.assertEqual(answer["modelRevision"], "test-revision")
        self.assertIn("not a verified explanation", answer["evidence"][0]["label"])
        request, series = self.runtime.received
        self.assertNotIn("events", request)
        self.assertNotIn("secret label", json.dumps(self.runtime.received))
        self.assertEqual(series[0]["timeSec"], [4, 4.5, 5])
        self.assertEqual(series[0]["values"], [4, 4.5, 5])

    def test_assistant_orchestrates_measurement_and_opentslm(self):
        self.request.pop("modelId")
        self.request["mode"] = "assistant"
        events = self.events(self.start())
        self.assertEqual([event["type"] for event in events], ["tool.started", "tool.completed", "tool.started", "tool.completed", "answer.completed"])
        self.assertEqual(events[0]["payload"]["tool"], "measurement_summary")
        self.assertEqual(events[2]["payload"]["tool"], "opentslm")
        self.assertIn("Largest observed torque ranges", events[-1]["payload"]["answer"])

    def test_cancel_retains_busy_slot_until_runtime_exits(self):
        self.runtime.release.clear()
        job = self.start()
        self.assertTrue(self.runtime.entered.wait(1))
        self.assertEqual(self.http("DELETE", "/api/queries/" + job["queryId"])[0], 204)
        self.assertEqual(self.events(job)[-1]["type"], "query.cancelled")
        self.assertEqual(self.http("POST", "/api/queries", self.request)[0], 409)
        self.runtime.release.set()
        for _ in range(100):
            if self.server.bridge.active is None:
                break
            time.sleep(.005)
        self.assertIsNone(self.server.bridge.active)
        self.assertNotIn("answer.completed", [e["type"] for e in self.events(job)])
        self.start()

    def test_malformed_future_unknown_and_unsupported_queries(self):
        cases = [("window.startSec", 3, "RAW_DATA_UNAVAILABLE"),
                 ("window.endSec", 7, "WINDOW_TOO_LONG"), ("playheadSec", 5, "FUTURE_CONTEXT"),
                 ("window.recordingId", "missing", "RECORDING_NOT_FOUND"),
                 ("window.channelIds", ["unknown"], "INVALID_CHANNELS"),
                 ("window.channelIds", ["joint_1", "joint_1"], "INVALID_CHANNELS"),
                 ("window.startSec", float("nan"), "INVALID_WINDOW"),
                 ("window.startSec", True, "INVALID_WINDOW"), ("mode", "unsupported", "MODE_UNAVAILABLE"),
                 ("modelId", "cnn-1d", "MODEL_UNAVAILABLE"), ("question", "", "INVALID_QUERY")]
        for key, value, code in cases:
            with self.subTest(code=code, key=key):
                request = copy.deepcopy(self.request)
                if "." in key:
                    group, key = key.split(".")
                    request[group][key] = value
                else:
                    request[key] = value
                status, error = self.http("POST", "/api/queries", request)
                self.assertGreaterEqual(status, 400)
                self.assertEqual(error["error"]["code"], code)

    def test_body_limits_and_json_errors(self):
        self.assertEqual(self.http("POST", "/api/queries", raw="x" * (MAX_BODY + 1))[0], 413)
        self.assertEqual(self.http("POST", "/api/queries", raw="{")[0], 400)
        self.assertEqual(self.http("POST", "/api/queries", raw="{}", headers={"Content-Type": "text/plain"})[0], 415)

    def test_failure_is_generic_and_terminal(self):
        self.runtime.failure = True
        events = self.events(self.start())
        self.assertEqual(events[-1]["type"], "query.error")
        self.assertNotIn("private runtime detail", json.dumps(events))
        self.assertNotIn("answer.completed", [e["type"] for e in events])

    def test_completed_job_storage_is_bounded_and_expires(self):
        self.server.bridge.finished_limit = 2
        for _ in range(4):
            job = self.start()
            self.events(job)
        self.assertLessEqual(len(self.server.bridge.jobs), 2)
        self.server.bridge.finished_ttl = -1
        self.assertEqual(self.http("GET", job["streamUrl"])[0], 404)

    def test_bad_source_fails_startup(self):
        data = fixture()
        data["detail"]["channels"][0]["values"][0] = float("nan")
        self.path.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            make_server(self.runtime, port=0, data_path=self.path, load_runtime=False)

    def test_envelope_preserves_extrema_and_budget(self):
        values = [0] * 100
        values[25], values[75] = 99, -88
        indices = envelope_indices(values, 20)
        self.assertLessEqual(len(indices), 20)
        self.assertTrue({0, 25, 75, 99}.issubset(indices))

    def test_actual_fixture_catalog_and_frontend_default_query(self):
        self.server.shutdown()
        self.server.server_close()
        self.server = make_server(self.runtime, port=0, load_runtime=False)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        status, datasets = self.http("GET", "/api/datasets")
        self.assertEqual(status, 200)
        self.assertEqual(datasets[0]["id"], "zenodo-21927431")
        status, recordings = self.http("GET", "/api/datasets/zenodo-21927431/recordings")
        self.assertEqual(status, 200)
        recording = recordings[0]
        self.assertEqual(recording["id"], "05-28-21-25")
        self.assertTrue(recording["name"])
        self.assertEqual(len(recording["channels"]), 7)
        channels = [channel["id"] for channel in recording["channels"]]
        prefix = "/api/recordings/05-28-21-25"
        status, signals = self.http("GET", prefix + "/signals?startSec=0&endSec=170&channelIds=" + ",".join(channels) + "&maxPoints=200000")
        self.assertEqual(status, 200)
        self.assertEqual(signals["resolution"], "display")
        self.assertEqual(len(signals["series"]), 7)
        self.assertEqual(len(signals["series"][0]["timeSec"]), 17000)
        status, markers = self.http("GET", prefix + "/events")
        self.assertEqual(status, 200)
        self.assertEqual(len(markers), 35)
        self.assertTrue(all(marker["source"] and marker["origin"] == "publisher_annotation" for marker in markers))
        self.request = {"mode": "direct", "modelId": "opentslm", "question": "Describe how the joint torque signals change during this interval.",
                        "playheadSec": 8, "window": {"datasetId": "zenodo-21927431", "recordingId": "05-28-21-25",
                        "startSec": 5.787, "endSec": 6.811, "channelIds": channels}}
        events = self.events(self.start())
        self.assertEqual(events[-1]["type"], "answer.completed")
        request, series = self.runtime.received
        self.assertEqual(request["window"], self.request["window"])
        self.assertEqual([len(channel["values"]) for channel in series], [1024] * 7)
        self.assertEqual(series[0]["timeSec"][0], 5.787)
        self.assertEqual(series[0]["timeSec"][-1], 6.81)


if __name__ == "__main__":
    unittest.main()
