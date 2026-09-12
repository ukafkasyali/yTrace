from pathlib import Path

import numpy as np
from scipy.io import savemat
from timenet.reader import TimeFReader
from timenet.registry import DatasetVersion

from dataset_profiler.datasets import KukaContactPart2Hints
from dataset_profiler.profiler import profile_dataset
from dataset_profiler.timef_validation import validate_kuka_contact_part2
from dataset_profiler.timenet import build_kuka_contact_part2
from dataset_profiler.timenet.kuka_collision import KukaRunRef
from dataset_profiler.timenet.kuka_contact import KukaContactPart2Connector


def _source(tmp_path: Path) -> KukaRunRef:
    run = tmp_path / "contact-batch-01" / "03-15-12-53"
    run.mkdir(parents=True)
    time_axis = np.arange(5, dtype=np.float64) / 1000
    savemat(run / "JsmoExp.mat", {"rt_tout": time_axis[:, None]})
    savemat(run / "JK_MsrExtTrq.mat", {"MsrExtTrq": np.vstack([time_axis, np.arange(35).reshape(7, 5)])})
    savemat(run / "JK_PosMsr.mat", {"PosMsr": np.vstack([time_axis, np.arange(100, 135).reshape(7, 5)])})
    savemat(run / "JK_moments.mat", {"JK_moments": np.array([[1], [3], [5]])})
    (run / "ReadMe.txt").write_text("Collision(ball):\n", encoding="utf-8")
    return KukaRunRef(
        run_dir=run,
        source_run_id="contact-batch-01/03-15-12-53",
        source_files=tuple(sorted(path.relative_to(tmp_path).as_posix() for path in run.iterdir())),
        source_subset="contact-batch-01",
    )


def test_part2_identity_and_contact_annotations(tmp_path):
    ref = _source(tmp_path)
    connector = KukaContactPart2Connector()
    assert connector.metadata().dataset_id == "kuka/contact-part2"

    record = connector.convert([ref]).records[0]
    assert len(record.time_series) == 14
    contacts = [a for a in record.annotations if a.key == "intentional_contact"]
    assert [a.value["matlab_index"] for a in contacts] == [1, 3, 5]
    assert [a.value["python_index"] for a in contacts] == [0, 2, 4]
    assert [a.value["timestamp_seconds"] for a in contacts] == [0.0, 0.002, 0.004]
    assert all(a.value["source_variable"] == "JK_moments" for a in contacts)
    assert [a.span.start_us for a in contacts] == [0, 2000, 4000]


def test_part2_timef_round_trip_and_raw_validation(tmp_path):
    ref = _source(tmp_path / "source")
    source_root = ref.run_dir.parent
    profile = profile_dataset(source_root, "kuka/contact-part2", hints=KukaContactPart2Hints())
    profile_path = tmp_path / "profile.json"
    profile.write_json(profile_path)

    version_dir = build_kuka_contact_part2(source_root, tmp_path / "registry")
    with TimeFReader(DatasetVersion.open_local(version_dir)) as reader:
        restored = reader.read()
    record = restored.records[0]
    assert len(record.time_series) == 14
    assert len([a for a in record.annotations if a.key == "intentional_contact"]) == 3
    np.testing.assert_array_equal(record.time_series[0].to_numpy(), np.arange(5, dtype=np.float64))

    report = validate_kuka_contact_part2(tmp_path / "registry", profile_path, source_root)
    assert report.passed
