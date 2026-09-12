"""Trusted, on-demand access to original KUKA external-torque recordings.

This adapter deliberately has no annotation-to-model path.  It reads a requested
window from the source MAT file only after validating the complete time axis and
the source bytes recorded at discovery.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path


CHANNEL_IDS = tuple(f"joint_{index}" for index in range(1, 8))
SOURCE_URL = "https://zenodo.org/records/21927431"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class RawRecording:
    recording_id: str
    archive: str
    torque_path: Path
    marker_path: Path
    torque_sha256: str
    marker_sha256: str
    duration_seconds: float
    event_indices: tuple[int, ...]

    @property
    def source_id(self) -> str:
        return f"{self.archive}/{self.recording_id}"

    def _matrix(self):
        """Return validated original samples, refusing files changed after discovery."""
        try:
            import numpy as np
            from scipy.io import loadmat
        except ImportError as error:  # pragma: no cover - deployment dependency
            raise RuntimeError("Raw-recording access requires numpy and scipy.") from error
        if _sha256(self.torque_path) != self.torque_sha256:
            raise ValueError("Raw torque source changed after catalogue validation")
        matrix = np.asarray(loadmat(self.torque_path)["MsrExtTrq"])
        if matrix.ndim != 2 or matrix.shape[0] != 8 or matrix.shape[1] < 2:
            raise ValueError("Raw torque source must be an 8-row time-plus-seven-joint matrix")
        if not np.issubdtype(matrix.dtype, np.number) or not np.isfinite(matrix).all():
            raise ValueError("Raw torque source contains non-finite values")
        times = matrix[0]
        if not np.allclose(np.diff(times), 0.001, rtol=0.0, atol=1e-9):
            raise ValueError("Raw torque source is missing samples or is not a contiguous 1 kHz recording")
        return times, matrix[1:]

    def raw_window(self, start_seconds: float, end_seconds: float, channel_ids: list[str]):
        """Return exact source-array values for a half-open interval."""
        import numpy as np

        times, values = self._matrix()
        if (not np.isfinite(start_seconds) or not np.isfinite(end_seconds)
                or not 0 <= start_seconds < end_seconds <= self.duration_seconds + 1e-9):
            raise ValueError("Requested window is outside the recording")
        # Decimal JSON boundaries and MATLAB timestamps may differ by a few ULPs.
        # Tolerance is one millionth of a sample; never resample or change values.
        lo = int(np.searchsorted(times, start_seconds - 1e-9, side="left"))
        hi = int(np.searchsorted(times, end_seconds - 1e-9, side="left"))
        selected_times = times[lo:hi]
        if not len(selected_times):
            raise ValueError("Raw source has no samples in this interval")
        if (abs(selected_times[0] - start_seconds) > 1e-8
                or abs(selected_times[-1] + .001 - end_seconds) > 1e-8):
            raise ValueError("Raw source does not cover the requested half-open interval")
        if len(selected_times) > 1 and not np.allclose(np.diff(selected_times), 0.001, rtol=0.0, atol=1e-9):
            raise ValueError("Raw source has a gap in the requested interval")
        indices = [CHANNEL_IDS.index(channel_id) for channel_id in channel_ids]
        return selected_times.tolist(), values[indices, lo:hi].tolist()

    def display_window(self, start_seconds: float, end_seconds: float, channel_ids: list[str]):
        """Return a 100 Hz display-only view; never label it raw."""
        times, values = self.raw_window(start_seconds, end_seconds, channel_ids)
        return times[::10], [row[::10] for row in values]

    def replay_data(self):
        """Make a compatible replay response without exposing a full raw recording."""
        times, values = self.display_window(0.0, self.duration_seconds, list(CHANNEL_IDS))
        detail_end = min(1.024, self.duration_seconds)
        detail_times, detail_values = self.raw_window(0.0, detail_end, list(CHANNEL_IDS))
        channels = lambda rows: [
            {"id": channel_id, "name": f"Joint {index + 1}", "unit": "Nm", "values": row}
            for index, (channel_id, row) in enumerate(zip(CHANNEL_IDS, rows))
        ]
        return {
            "recording": self.metadata(),
            "times": times,
            "channels": channels(values),
            "detail": {"startSeconds": 0.0, "endSeconds": detail_end,
                       "times": detail_times, "channels": channels(detail_values)},
            "events": self.events(),
        }

    def metadata(self):
        return {"id": self.recording_id, "name": f"KUKA {self.archive} recording {self.recording_id}",
                "durationSeconds": self.duration_seconds, "sampleRateHz": 1000,
                "displaySampleRateHz": 100, "sourceUrl": SOURCE_URL, "archive": self.archive,
                "channelCount": 7, "eventCount": len(self.event_indices),
                "sourceSha256": self.torque_sha256}

    def events(self):
        return [{"id": f"{self.recording_id}-marker-{position + 1}",
                 "timeSeconds": (index - 1) / 1000, "kind": "publisher_annotation",
                 "label": f"Publisher {self.archive} marker {position + 1}",
                 "source": f"{self.source_id}/JK_moments.mat (1-based sample index)"}
                for position, index in enumerate(self.event_indices)]


class RawRecordingCatalog:
    """Discover validated recordings under ``<root>/<archive>/<recording-id>``."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise ValueError(f"Raw recording root is not a directory: {self.root}")
        recordings = {}
        for torque_path in sorted(self.root.glob("*/*/JK_MsrExtTrq.mat")):
            record = self._read_record(torque_path)
            if record.recording_id in recordings:
                raise ValueError(f"Duplicate raw recording identity: {record.recording_id}")
            recordings[record.recording_id] = record
        if not recordings:
            raise ValueError("Raw recording root contains no JK_MsrExtTrq.mat sources")
        self.recordings = recordings

    def _read_record(self, torque_path: Path) -> RawRecording:
        try:
            import numpy as np
            from scipy.io import loadmat
        except ImportError as error:  # pragma: no cover - deployment dependency
            raise RuntimeError("Raw-recording access requires numpy and scipy.") from error
        run_dir = torque_path.parent
        marker_path = run_dir / "JK_moments.mat"
        if not marker_path.is_file():
            raise ValueError(f"Raw recording is missing publisher markers: {run_dir}")
        matrix = np.asarray(loadmat(torque_path)["MsrExtTrq"])
        if matrix.ndim != 2 or matrix.shape[0] != 8 or matrix.shape[1] < 2:
            raise ValueError(f"Invalid seven-channel torque matrix: {torque_path}")
        if not np.issubdtype(matrix.dtype, np.number) or not np.isfinite(matrix).all():
            raise ValueError(f"Non-finite raw torque values: {torque_path}")
        times = matrix[0]
        if times[0] != 0.0 or not np.allclose(np.diff(times), 0.001, rtol=0.0, atol=1e-9):
            raise ValueError(f"Raw recording has missing/gapped/non-1kHz timestamps: {torque_path}")
        raw_markers = np.asarray(loadmat(marker_path)["JK_moments"]).reshape(-1)
        if not np.issubdtype(raw_markers.dtype, np.number) or not np.isfinite(raw_markers).all() or not np.all(raw_markers == np.floor(raw_markers)):
            raise ValueError(f"Invalid publisher marker indices: {marker_path}")
        markers = tuple(int(value) for value in raw_markers)
        if any(index < 1 or index > len(times) for index in markers):
            raise ValueError(f"Publisher marker outside raw recording: {marker_path}")
        return RawRecording(recording_id=run_dir.name, archive=run_dir.parent.name, torque_path=torque_path,
                            marker_path=marker_path, torque_sha256=_sha256(torque_path),
                            marker_sha256=_sha256(marker_path), duration_seconds=float(times[-1] + 0.001),
                            event_indices=markers)
