"""TimeNet connector for the raw KUKA collision/contact corpus.

Set ``ROBOT_COLLISION_RAW_ROOT`` to reuse an existing download.  Otherwise the
connector downloads both Zenodo records into its TimeNet cache directory.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from timenet.connectors import BaseConnector
from timenet.dataset import TimeFDataset, TimeSeries
from timenet.dataset.axis import RegularAxis
from timenet.types import (
    Annotation,
    AnswerTask,
    ClassificationTask,
    DataSource,
    LocalizationMode,
    TemporalLocalizationTask,
    TimePoint,
    TimeSeriesSpec,
    ureg,
)

from robot_observability.config import DataConfig
from robot_observability.constants import JOINT_NAMES
from robot_observability.data.prepare import (
    _free_joint_thresholds,
    _metadata_for_window,
    _session_specs,
    calculate_train_normalization,
)
from robot_observability.data.raw import RawSessionRef, discover_sessions, load_session
from robot_observability.data.splits import stratified_session_split
from robot_observability.data.zenodo import download_raw_corpus

_SOURCE = DataSource(
    data_source_type="experimental",
    name="KUKA LWR4+ collision/contact experiments",
    provider="Zengjie Zhang et al.",
)
_TORQUE = TimeSeriesSpec(
    spec_type="external_joint_torque",
    name="Measured external joint torque",
    unit_value=ureg.newton * ureg.meter,
    data_source=_SOURCE,
)
_AXIS = RegularAxis.from_rate_hz(1000)


class RobotCollisionConnector(BaseConnector[RawSessionRef]):
    """Convert raw continuous sessions to leakage-aware, queryable windows."""

    def __init__(self) -> None:
        self.config_path = Path(os.environ.get("ROBOT_COLLISION_DATA_CONFIG", "configs/data.yaml"))

    def download(self, cache_dir: Path) -> list[RawSessionRef]:
        configured = os.environ.get("ROBOT_COLLISION_RAW_ROOT")
        root = Path(configured) if configured else download_raw_corpus(cache_dir / "raw", workers=4)
        refs = discover_sessions(root)
        if not refs:
            raise FileNotFoundError(f"No complete sessions found below {root}")
        return refs

    def convert(self, raw_refs: list[RawSessionRef]) -> TimeFDataset:
        config = DataConfig.from_yaml(self.config_path)
        split_map = stratified_session_split(
            raw_refs,
            train_fraction=config.split.train,
            validation_fraction=config.split.validation,
            seed=config.seed,
        )
        _, scale = calculate_train_normalization(raw_refs, split_map, config)
        thresholds = _free_joint_thresholds(raw_refs, split_map, scale, config)
        dataset = TimeFDataset(metadata=self.metadata())

        for ref in raw_refs:
            session = load_session(ref, config.sampling_hz)
            split = split_map[ref.session_id]
            for spec in _session_specs(session, split, config):
                raw_window = session.torque_nm[spec.start_sample : spec.start_sample + config.window_samples]
                streams = tuple(
                    TimeSeries.from_values(
                        raw_window[:, joint_index],
                        spec=_TORQUE,
                        signal=joint,
                        time_axis=_AXIS.at_index(spec.start_sample),
                        source_id=spec.session_id,
                        time_series_id=f"{spec.record_id}/{joint}",
                    )
                    for joint_index, joint in enumerate(JOINT_NAMES)
                )
                record = dataset.add_record(time_series=streams, record_id=spec.record_id)
                record.add_annotations(
                    [
                        Annotation(
                            key="recording_id", value=spec.session_id, id=f"{spec.record_id}/recording"
                        ),
                        Annotation(key="split", value=split, id=f"{spec.record_id}/split"),
                        Annotation(
                            key="event_type", value=spec.event_type, id=f"{spec.record_id}/event-type"
                        ),
                    ]
                )
                semantics = ClassificationTask(target=spec.event_type, id=f"{spec.record_id}/semantics")
                dataset.add_task(record, semantics)
                metadata = _metadata_for_window(spec, raw_window, scale, thresholds)
                answer = {
                    "contact": metadata["contact"],
                    "event_type": spec.event_type,
                    "onset_ms": spec.onset_sample,
                    "strongest_joint": metadata["strongest_joint"],
                    "affected_joints": metadata["affected_joints"],
                    "evidence_start_ms": metadata["evidence_start_ms"],
                    "evidence_end_ms": metadata["evidence_end_ms"],
                }
                rationale = (
                    "No sustained external-torque disturbance exceeds the train-free evidence threshold."
                    if spec.onset_sample is None
                    else f"The manual marker occurs at {spec.onset_sample} ms; "
                    f"{metadata['strongest_joint']} has the largest calibrated top-5%-mean disturbance."
                )
                dataset.add_task(
                    record,
                    AnswerTask(
                        prompt="Diagnose this robot telemetry window and return the structured observability fields.",
                        target=json.dumps(answer, separators=(",", ":")),
                        rationale=rationale,
                        from_tasks=(semantics,),
                        id=f"{spec.record_id}/answer",
                    ),
                )
                if spec.event_sample_absolute is not None:
                    onset_annotation = Annotation(
                        key="manual_event_onset",
                        span=TimePoint.seconds(spec.event_sample_absolute / config.sampling_hz),
                        id=f"{spec.record_id}/onset",
                    )
                    record.add_annotation(onset_annotation)
                    dataset.add_task(
                        record,
                        TemporalLocalizationTask(
                            prompt="When did external contact begin?",
                            mode=LocalizationMode.SPARSE,
                            target_annotation_ids=(onset_annotation.id,),
                            id=f"{spec.record_id}/localization",
                        ),
                    )
        return dataset


CONNECTOR = RobotCollisionConnector
