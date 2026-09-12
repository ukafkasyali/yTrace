#!/usr/bin/env python3
"""Create a measured KUKA dashboard fixture. Requires numpy, scipy, tar and zstd.

Usage: python3 scripts/prepare_demo_data.py [--archive /path/to/archive.tar.zst]
Without --archive, curl downloads the public source into a temporary directory.
Only the selected MAT members are read; the archive is never extracted to disk.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tempfile

import numpy as np
from scipy.io import loadmat

RECORD = "05-28-21-25"
ARCHIVE = "collision-batch-42.tar.zst"
SOURCE = "https://zenodo.org/records/21927431"
DOWNLOAD = f"https://zenodo.org/api/records/21927431/files/{ARCHIVE}/content"
ARCHIVE_SHA256 = "e98aa696f891d19b38f2c423296113757d55f43214a9abe3ce14c56560e2554a"


def member(archive, filename, key):
    payload = subprocess.check_output(
        ["tar", "--zstd", "-xOf", str(archive), f"{RECORD}/{filename}"]
    )
    return loadmat(io.BytesIO(payload))[key]


def build(archive):
    checksum = hashlib.sha256(Path(archive).read_bytes()).hexdigest()
    if checksum != ARCHIVE_SHA256:
        raise ValueError(f"Archive checksum does not match publisher SHA256SUMS: {checksum}")
    raw = member(archive, "JK_MsrExtTrq.mat", "MsrExtTrq")
    marks = member(archive, "JK_moments.mat", "JK_moments").flatten()
    assert raw.shape == (8, 170001), f"Unexpected dimensions: {raw.shape}"
    assert np.isfinite(raw).all(), "Non-finite source samples"
    times, signals = raw[0], raw[1:]
    assert np.all(np.diff(times) > 0), "Non-increasing timestamps"
    assert np.allclose(np.diff(times), 0.001, atol=1e-9), "Unexpected sampling"
    assert len(marks) == 35, "Unexpected annotation count"
    marker_times = marks / 1000.0
    assert np.all((marker_times >= times[0]) & (marker_times <= times[-1]))

    def channel_data(selection):
        return [dict(id=f"joint_{i+1}", name=f"Joint {i+1}", unit="Nm",
                     values=np.round(values[selection], 6).tolist())
                for i, values in enumerate(signals)]

    # Timestamps are exact milliseconds; rounding avoids binary boundary drift.
    millis = np.rint(times * 1000).astype(np.int64)
    def interval(start, end):
        return (millis >= round(start * 1000)) & (millis < round(end * 1000))

    window = interval(5.787, 6.811)
    reference = interval(5.787, 6.087)
    comparison = interval(6.187, 6.487)
    ranges, departures, variability = [], [], []
    for i, values in enumerate(signals):
        channel_id = f"joint_{i+1}"
        w, r, c = values[window], values[reference], values[comparison]
        baseline = np.median(r)
        deviation = c - baseline
        extreme = np.argmax(np.abs(deviation))
        ranges.append(dict(channelId=channel_id, min=float(w.min()), max=float(w.max()),
                           range=float(np.ptp(w)), minTime=float(times[window][w.argmin()]),
                           maxTime=float(times[window][w.argmax()])))
        departures.append(dict(channelId=channel_id, baselineMedian=float(baseline),
                               absoluteDeparture=float(abs(deviation[extreme])),
                               signedDeparture=float(deviation[extreme]),
                               time=float(times[comparison][extreme])))
        variability.append(dict(channelId=channel_id,
                                referenceRms=float(np.sqrt(np.mean((r-np.median(r))**2))),
                                comparisonRms=float(np.sqrt(np.mean((c-np.median(c))**2)))))
    detail = interval(4, 9)
    return dict(
        recording=dict(id=RECORD, name="KUKA · accidental collision recording",
                       durationSeconds=float(times[-1]-times[0]), sampleRateHz=1000,
                       displaySampleRateHz=100, sourceUrl=SOURCE, archive=ARCHIVE,
                       channelCount=7, eventCount=len(marks)),
        times=np.round(times[::10], 3).tolist(), channels=channel_data(slice(None, None, 10)),
        events=[dict(id=f"marker-{i+1}", timeSeconds=float(t), kind="publisher_annotation",
                     label=f"Event {i+1:02d}", source="JK_moments")
                for i, t in enumerate(marker_times)],
        detail=dict(startSeconds=4, endSeconds=9, times=np.round(times[detail], 3).tolist(),
                    channels=channel_data(detail)),
        evidence=dict(window=dict(start=5.787, end=6.811), reference=dict(start=5.787, end=6.087),
                      comparison=dict(start=6.187, end=6.487), ranges=ranges,
                      departures=departures, variability=variability),
        provenance=dict(sourceArchiveSha256=checksum,
                        sourceSampleCount=len(times), signal="MsrExtTrq", signed=True,
                        markerConvention="JK_moments / 1000 seconds; precise physical onset unverified",
                        overviewMethod="Every tenth original sample; display only, may omit short extrema",
                        detailInterval="[4,9) seconds; original 1000 Hz",
                        intervalConvention="Half-open [start,end)",
                        signalDecimals=6, modelOutputs=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--output", type=Path,
                        default=Path(__file__).resolve().parents[1]/"public/data/kuka-demo.json")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="kuka-fixture-") as directory:
        archive = args.archive or Path(directory)/ARCHIVE
        if args.archive is None:
            subprocess.run(["/usr/bin/curl", "--fail", "--location", DOWNLOAD,
                            "--output", str(archive)], check=True)
        fixture = build(archive)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(fixture, separators=(",", ":"), allow_nan=False)+"\n")
    print(f"Wrote {args.output}: {args.output.stat().st_size:,} bytes; "
          f"{len(fixture['times'])} overview samples, {len(fixture['detail']['times'])} detail samples.")


if __name__ == "__main__":
    main()
