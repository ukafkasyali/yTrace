"""Build deterministic demo excerpts from original MATLAB recordings (requires scipy).

Run: python -m inference.build_demo_cases --raw-root PATH --splits PATH
The source recordings were selected before running inference. These recordings
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
        ("collision", "accidental", "04-22-15-33", "Collision · reversed J3 motion"),
        ("collision", "accidental", "04-22-15-53", "Collision · reversed J1 + J2 motion"),
        ("collision", "accidental", "05-26-14-28", "Collision · reversed J1 motion"),
    ]:
        is_added = rid not in ("03-15-12-53", "03-22-11-18")
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
        raw_start = max(0, round((start - 2) * 1000)) if is_added else 0
        raw_end = min(len(times), raw_start + 8000)
        overview, raw = slice(None, None, 10), slice(raw_start, raw_end)
        data = {"recording": {"id": rid, "name": f"KUKA {semantics} recording {rid}",
            "durationSeconds": duration, "sampleRateHz": 1000, "displaySampleRateHz": 100,
            "sourceUrl": "https://zenodo.org/records/21927431", "archive": folder,
            "channelCount": 7, "eventCount": len(moments)},
            "times": times[overview].tolist(), "channels": channels(overview),
            "detail": {"startSeconds": float(times[raw_start]) if is_added else 0, "endSeconds": float(times[raw_end-1] + .001) if is_added else 8, "times": times[raw].tolist(), "channels": channels(raw)},
            "events": [{"id": f"{rid}-marker-{i+1}", "timeSeconds": float(times[index]),
                "kind": "publisher_annotation", "label": f"Publisher {semantics} marker {i+1}",
                "source": f"{folder}/{rid}/JK_moments.mat (1-based sample index)"} for i, index in enumerate(moments)]}
        content = json.dumps(data, separators=(",", ":"), allow_nan=False).encode()
        filename = f"demo_data/{rid}.json.gz"
        (destination / "demo_data").mkdir(exist_ok=True)
        (destination / filename).write_bytes(gzip.compress(content, mtime=0))
        filenames.append(filename)
        cases.append({"id": f"accidental-{rid}" if is_added else semantics, "title": title, "recordingId": rid,
            "interval": {"start": start, "end": round(start + 1.024, 3)},
            "note": f"Publisher {semantics} experiment · {split} recording. The marker is an annotation; the model makes its own prediction."})
        provenance.append({"recordingId": rid, "split": split, "fixtureSha256": hashlib.sha256(content).hexdigest(),
            "sources": [{"path": f"{folder}/{rid}/{p.name}", "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in [torque_path, markers_path]]})
        if rid == "03-15-12-53":
            if event_time < 3.024:
                raise ValueError("Free-motion example must be separated from the first annotation")
            free = {"id": "free", "title": "Normal / free motion", "recordingId": rid,
                "interval": {"start": 1, "end": 2.024},
                "note": "Annotation-free interval before the first contact marker · training recording. Absence of a marker does not prove absence of contact."}
    cases.append(free)
    catalog = {"selection": "Original two audited recordings plus three train recordings with different measured trajectories from public collision-batches 14 and 28, selected by joint-position comparison rather than model predictions. Reversed motion is relative to original 05-28-21-25. First publisher marker in each; free interval fixed at [1, 2.024). Batch-14 MD5: 38f464111c534f1ec2be0c6f0a117d2c; batch-28 MD5: 3dc2369c63d6ac41200e9e221581b96d.",
               "recordings": filenames, "cases": cases, "provenance": provenance}
    (destination / "demo_cases.json").write_text(json.dumps(catalog, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--splits", type=Path, required=True)
    args = parser.parse_args()
    build(args.raw_root, args.splits, Path(__file__).parent)
