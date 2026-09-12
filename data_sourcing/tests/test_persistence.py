from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from data_sourcing.config import Settings
from data_sourcing.models import ApprovalRequest, CreateSourcingRun, RunStatus
from data_sourcing.service import SourcingService
from data_sourcing.storage import IdempotencyStore

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


def test_concurrent_idempotency_claims_share_one_run(tmp_path: Path) -> None:
    store = IdempotencyStore(tmp_path / "idempotency.sqlite3")
    request = CreateSourcingRun(brief=BRIEF)

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(
            pool.map(
                lambda proposed: store.claim("concurrent-key", request, proposed),
                [str(uuid4()), str(uuid4())],
            )
        )

    assert claims[0][0] == claims[1][0]
    assert sorted(created for _, created in claims) == [False, True]
    store.close()
