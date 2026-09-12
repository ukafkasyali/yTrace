"""Exercise a running real service; no weights, credentials or mocks in this client."""
import argparse
import json
from pathlib import Path
import time
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000/api")
    parser.add_argument("--output", default="inference/smoke-result.json")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    with urlopen(base + "/models", timeout=10) as response:
        models = json.load(response)
    model = next((m for m in models if m["id"] == "opentslm" and m["available"]), None)
    if not model:
        raise SystemExit("OpenTSLM is not ready. Check /api/health and the server log.")
    request = {
        "mode": "direct", "modelId": "opentslm",
        "question": "Describe how the joint torque signals change during this interval.",
        "window": {
            "datasetId": "zenodo-21927431", "recordingId": "05-28-21-25",
            "startSec": 5.787, "endSec": 6.811,
            "channelIds": [f"joint_{n}" for n in range(1, 8)],
        },
        "playheadSec": 8,
    }
    began = time.monotonic()
    created = Request(base + "/queries", data=json.dumps(request).encode(),
                      headers={"Content-Type": "application/json"})
    with urlopen(created, timeout=10) as response:
        query = json.load(response)
    stream_url = urljoin(base + "/", query["streamUrl"])
    if urlsplit(stream_url).netloc != urlsplit(base).netloc:
        raise SystemExit("Unexpected stream origin")
    events, event = [], {}
    with urlopen(stream_url, timeout=180) as response:
        for line in response:
            text = line.decode().rstrip("\r\n")
            if text.startswith("data:"):
                event = json.loads(text[5:].strip())
            if not text and event:
                if event.get("queryId") != query["queryId"]:
                    raise SystemExit("Query identity mismatch")
                events.append(event)
                kind = event["type"]
                if kind in ("query.error", "query.cancelled"):
                    raise SystemExit(json.dumps(event))
                if kind == "answer.completed":
                    break
                event = {}
    if not events or events[-1]["type"] != "answer.completed":
        raise SystemExit("Stream closed without completed inference")
    payload = events[-1]["payload"]
    if not payload.get("answer", "").strip() or not payload.get("modelRevision"):
        raise SystemExit("Missing answer or checkpoint identity")
    evidence = payload.get("evidence", [])
    if not any(e.get("window") == request["window"] for e in evidence):
        raise SystemExit("Evidence does not identify the requested historical interval")
    trace = payload.get("inputTrace")
    if not trace or trace.get("window") != request["window"] or trace.get("samplesPerChannel") != 1024:
        raise SystemExit("Missing exact-input receipt from the real runtime")
    result = {"purpose": "Connection smoke test, not a KUKA accuracy benchmark",
              "request": request, "model": model, "events": events,
              "roundTripMs": round((time.monotonic() - began) * 1000)}
    Path(args.output).write_text(json.dumps(result, indent=2) + "\n")
    print(payload["answer"])
    print(f"\nSaved traceable result to {args.output}")


if __name__ == "__main__":
    main()
