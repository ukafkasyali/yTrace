from pathlib import Path

from data_sourcing.config import Settings
from data_sourcing.models import ApprovalRequest, CreateSourcingRun, RunStatus
from data_sourcing.service import SourcingService

BRIEF = (
    "Find robot collision and contact time series from "
    "https://github.com/zhang-zengjie/robot-raw-collision-signals"
)


def test_approval_resumes_from_sqlite_after_service_restart(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path / "persisted")
    first = SourcingService(settings)
    run_id, _ = first.create_run(CreateSourcingRun(brief=BRIEF), "restart-test")
    first.execute_run(run_id)
    paused = first.artifacts.read_run(run_id)
    assert paused.status is RunStatus.AWAITING_APPROVAL
    candidate_id = paused.recommended_candidate_id
    first.close()

    second = SourcingService(settings)
    completed = second.approve(
        run_id,
        ApprovalRequest(decision="APPROVE", candidate_id=candidate_id),
    )

    assert completed.status is RunStatus.APPROVED
    assert second.artifacts.read_manifest(run_id).candidate_id == candidate_id
    second.close()
