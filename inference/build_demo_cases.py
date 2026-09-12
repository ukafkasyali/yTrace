"""Build deterministic demo excerpts from original MATLAB recordings (requires scipy).

Run: python -m inference.build_demo_cases --raw-root PATH --splits PATH
The two source recordings were selected before running inference. These recordings
illustrate the interface; their split is disclosed and no benchmark is changed.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path


def build(raw_root, splits_path, destination):
    import numpy as np
    from scipy.io import loadmat

    splits = json.loads(splits_path.read_text())
    destination.mkdir(parents=True, exist_ok=True)
    filenames, cases, provenance = [], [], []
    for folder, semantics, rid, title in [
        ("collision", "accidental", "03-15-12-53", "Collision"),
        ("contact", "intentional", "03-22-11-18", "Intentional contact"),
    ]:
        source = raw_root / folder / rid
        torque_path, markers_path = source / "JK_MsrExtTrq.mat", source / "JK_moments.mat"
        matrix = loadmat(torque_path)["MsrExtTrq"]
        moments = loadmat(markers_path)["JK_moments"].reshape(-1).astype(int) - 1
        times, values = matrix[0], matrix[1:]
        if values.shape[0] != 7 or not np.isfinite(matrix).all() or not np.allclose(np.diff(times), .001, atol=1e-9, rtol=0):
            raise ValueError("Expected seven finite synchronized torque channels at 1 kHz")
        split = splits[f"{semantics}/{rid}"]
        event_time = float(times[moments[0]])
        start = round(event_time - .4, 3)
        duration = float(times[-1] + .001)
        def channels(indices):
            return [{"id": f"joint_{i+1}", "name": f"Joint {i+1}", "unit": "Nm", "values": row[indices].tolist()} for i, row in enumerate(values)]
        overview, raw = slice(None, None, 10), slice(0, 8000)
        data = {"recording": {"id": rid, "name": f"KUKA {semantics} recording {rid}",
            "durationSeconds": duration, "sampleRateHz": 1000, "displaySampleRateHz": 100,
            "sourceUrl": "https://zenodo.org/records/21927431", "archive": folder,
            "channelCount": 7, "eventCount": len(moments)},
            "times": times[overview].tolist(), "channels": channels(overview),
            "detail": {"startSeconds": 0, "endSeconds": 8, "times": times[raw].tolist(), "channels": channels(raw)},
            "events": [{"id": f"{rid}-marker-{i+1}", "timeSeconds": float(times[index]),
                "kind": "publisher_annotation", "label": f"Publisher {semantics} marker {i+1}",
                "source": f"{folder}/{rid}/JK_moments.mat (1-based sample index)"} for i, index in enumerate(moments)]}
        content = json.dumps(data, separators=(",", ":"), allow_nan=False).encode()
        filename = f"demo_data/{rid}.json.gz"
        (destination / "demo_data").mkdir(exist_ok=True)
        (destination / filename).write_bytes(gzip.compress(content, mtime=0))
        filenames.append(filename)
        cases.append({"id": semantics, "title": title, "recordingId": rid,
            "interval": {"start": start, "end": round(start + 1.024, 3)},
            "note": f"Publisher {semantics} experiment · {split} recording. The marker is an annotation; the model makes its own prediction."})
        provenance.append({"recordingId": rid, "split": split, "fixtureSha256": hashlib.sha256(content).hexdigest(),
            "sources": [{"path": f"{folder}/{rid}/{p.name}", "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in [torque_path, markers_path]]})
        if folder == "collision":
            if event_time < 3.024:
                raise ValueError("Free-motion example must be separated from the first annotation")
            free = {"id": "free", "title": "Normal / free motion", "recordingId": rid,
                "interval": {"start": 1, "end": 2.024},
                "note": "Annotation-free interval before the first contact marker · training recording. Absence of a marker does not prove absence of contact."}
    cases.append(free)
    catalog = {"selection": "First publisher marker in each of two previously audited recordings (split disclosed); free interval fixed at [1, 2.024) before inference. No selection by model result.",
               "recordings": filenames, "cases": cases, "provenance": provenance}
    (destination / "demo_cases.json").write_text(json.dumps(catalog, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--splits", type=Path, required=True)
    args = parser.parse_args()
    build(args.raw_root, args.splits, Path(__file__).parent)
