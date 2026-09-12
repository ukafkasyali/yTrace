from pathlib import Path

import numpy as np
from scipy.io import savemat
from timenet.connectors import BaseConnector
from timenet.reader import TimeFReader
from timenet.registry import DatasetVersion

from dataset_profiler.timenet import build_kuka_collision_part1, build_kuka_timef_dataset
from dataset_profiler.timenet.kuka_collision import KukaCollisionPart1Connector, KukaRunRef


def _source(tmp_path: Path) -> KukaRunRef:
    run = tmp_path / "collision-batch-01" / "03-15-12-53"
    run.mkdir(parents=True)
    time_axis = np.arange(5, dtype=np.float64) / 1000
    savemat(run / "JsmoExp.mat", {"rt_tout": time_axis[:, None]})
    savemat(run / "JK_MsrExtTrq.mat", {"MsrExtTrq": np.vstack([time_axis, np.arange(35).reshape(7, 5)])})
    savemat(run / "JK_PosMsr.mat", {"PosMsr": np.vstack([time_axis, np.arange(100, 135).reshape(7, 5)])})
    savemat(run / "JK_moments.mat", {"JK_moments": np.array([[1], [3], [5]])})
    (run / "ReadMe.txt").write_text("fixture", encoding="utf-8")
    return KukaRunRef(
        run_dir=run,
        source_run_id="collision-batch-01/03-15-12-53",
        source_files=tuple(sorted(path.relative_to(tmp_path).as_posix() for path in run.iterdir())),
        source_subset="collision-batch-01",
    )


def _convert(tmp_path: Path):
    return KukaCollisionPart1Connector().convert([_source(tmp_path)])


def test_is_native_compatible_connector():
    assert isinstance(KukaCollisionPart1Connector(), BaseConnector)


def test_metadata_uses_part_identity_not_batch_identity():
    metadata = KukaCollisionPart1Connector().metadata()
    assert metadata.dataset_id == "kuka/collision-part1"
    assert str(metadata.license) == "MIT"


def test_one_run_becomes_one_record_with_fourteen_scalar_series(tmp_path):
    dataset = _convert(tmp_path)
    record = dataset.records[0]
    assert len(dataset.records) == 1
    assert record.subject_ids == ()
    assert len(record.time_series) == 14
    assert {series.spec.spec_type for series in record.time_series} == {
        "measured_external_joint_torque", "measured_joint_position"
    }
    assert all(series.n_values == 5 for series in record.time_series)
    assert all(series.time_axis.period_us == 1000 for series in record.time_series)
    assert dataset.tasks == ()


def test_values_are_loaded_by_the_shared_parser(tmp_path):
    record = _convert(tmp_path).records[0]
    torque = [s for s in record.time_series if s.spec.spec_type == "measured_external_joint_torque"]
    position = [s for s in record.time_series if s.spec.spec_type == "measured_joint_position"]
    np.testing.assert_array_equal(torque[0].to_numpy(), np.arange(5, dtype=np.float64))
    np.testing.assert_array_equal(position[0].to_numpy(), np.arange(100, 105, dtype=np.float64))


def test_position_series_use_radians(tmp_path):
    record = _convert(tmp_path).records[0]
    position = [s for s in record.time_series if s.spec.spec_type == "measured_joint_position"]
    assert len(position) == 7
    assert {str(series.spec.unit_value) for series in position} == {"radian"}


def test_collision_indices_and_timestamps_are_explicit(tmp_path):
    collisions = [a for a in _convert(tmp_path).records[0].annotations if a.key == "collision"]
    assert [a.value["matlab_index"] for a in collisions] == [1, 3, 5]
    assert [a.value["python_index"] for a in collisions] == [0, 2, 4]
    assert [a.value["timestamp_seconds"] for a in collisions] == [0.0, 0.002, 0.004]
    assert [a.span.start_us for a in collisions] == [0, 2000, 4000]


def test_provenance_and_inferred_position_unit_are_machine_readable(tmp_path):
    by_key = {a.key: a.value for a in _convert(tmp_path).records[0].annotations}
    assert by_key["source_run_id"] == "collision-batch-01/03-15-12-53"
    assert "collision-batch-01/03-15-12-53/JK_moments.mat" in by_key["source_files"]
    assert by_key["unit_resolution"] == {
        "spec_type": "measured_joint_position",
        "unit": "radian",
        "unit_resolution": "inferred",
        "confidence": "high",
    }


def test_direct_integration_round_trip(tmp_path):
    ref = _source(tmp_path / "source")
    version_dir = build_kuka_collision_part1(ref.run_dir.parent.parent, tmp_path / "registry")
    with TimeFReader(DatasetVersion.open_local(version_dir)) as reader:
        restored = reader.read()
    record = restored.records[0]
    assert len(record.time_series) == 14
    assert len([a for a in record.annotations if a.key == "collision"]) == 3
    assert next(a.value for a in record.annotations if a.key == "source_run_id") == ref.source_run_id
    np.testing.assert_array_equal(record.time_series[0].to_numpy(), np.arange(5, dtype=np.float64))


def test_unified_builder_selects_part1_identity(tmp_path):
    ref = _source(tmp_path / "source")
    version_dir = build_kuka_timef_dataset(
        "kuka/collision-part1", ref.run_dir.parent.parent, tmp_path / "registry"
    )
    with TimeFReader(DatasetVersion.open_local(version_dir)) as reader:
        restored = reader.read()
    assert restored.metadata.dataset_id == "kuka/collision-part1"


def test_unified_builder_rejects_unknown_identity(tmp_path):
    try:
        build_kuka_timef_dataset("kuka/not-a-part", tmp_path, tmp_path / "registry")
    except ValueError as error:
        assert "kuka/collision-part1" in str(error)
        assert "kuka/contact-part2" in str(error)
    else:
        raise AssertionError("unsupported dataset identity was accepted")
