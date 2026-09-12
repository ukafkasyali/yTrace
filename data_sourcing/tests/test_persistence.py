import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from data_sourcing.config import Settings
from data_sourcing.models import ApprovalRequest, CreateSourcingRun, RunStatus, SourcingManifest
from data_sourcing.service import SourcingService
from data_sourcing.storage import IdempotencyStore

BRIEF = (
    "Find robot collision and contact time series from "
    "https://github.com/zhang-zengjie/robot-raw-collision-signals"
)


def test_legacy_manifest_defaults_to_schema_1_0_without_assets() -> None:
    manifest = SourcingManifest.model_validate(
        {
            "runId": str(uuid4()),
            "candidateId": "ds_0123456789ab",
            "name": "Legacy dataset",
            "canonicalUrl": "https://zenodo.org/records/123",
            "revision": "ZENODO:123.r1",
            "licenseId": "cc-by-4.0",
            "labels": ["collision"],
            "fileExtensions": [".mat"],
            "evidenceIds": ["ev_0123456789abcdef"],
            "limitations": [],
        }
    )

    assert manifest.schema_version == "1.0"
    assert manifest.source_kind is None
    assert manifest.assets == []


def test_approval_resumes_from_sqlite_after_service_restart(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path / "persisted")
    first = SourcingService(settings)
    run_id, _ = first.create_run(CreateSourcingRun(brief=BRIEF), "restart-test")
    first.execute_run(run_id)
    paused = first.artifacts.read_run(run_id)
    assert paused.status is RunStatus.AWAITING_APPROVAL
    candidate_id = paused.assessments[0].candidate_id
    first.close()

    second = SourcingService(settings)
    completed = second.approve(
        run_id,
        ApprovalRequest(decision="APPROVE", candidate_id=candidate_id),
    )

    assert completed.status is RunStatus.APPROVED
    assert second.artifacts.read_manifest(run_id).candidate_id == candidate_id
    second.close()


def test_saved_numeric_assessment_is_migrated_to_categorical_contract(
    tmp_path: Path,
) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path / "persisted")
    service = SourcingService(settings)
    run_id, _ = service.create_run(CreateSourcingRun(brief=BRIEF), "legacy-score")
    service.execute_run(run_id)
    run_path = settings.runs_dir / run_id / "run.json"
    payload = json.loads(run_path.read_text(encoding="utf-8"))
    legacy_assessment = payload["assessments"][0]
    legacy_assessment.pop("suitabilityLevel")
    legacy_assessment.pop("suitabilityFactors")
    legacy_assessment.pop("metPreferredRequirementIds")
    legacy_assessment.pop("unmetPreferredRequirementIds")
    legacy_assessment.pop("authoritativeSourceKind")
    legacy_assessment["score"] = {
        "taskFit": 35,
        "trainingReadiness": 20,
        "acquisitionIntegrity": 15,
        "provenanceDocumentation": 10,
        "integrationReadiness": 10,
        "licenseClarity": 5,
        "evidenceConsistency": 3,
    }
    legacy_assessment["totalScore"] = 98
    legacy_assessment["tier"] = "RECOMMEND"
    run_path.write_text(json.dumps(payload), encoding="utf-8")

    migrated = service.artifacts.read_run(run_id)
    assessment = migrated.assessments[0]
    serialized = assessment.model_dump(by_alias=True)

    assert assessment.suitability_level.value == "MEDIUM"
    assert assessment.suitability_factors
    assert "score" not in serialized
    assert "totalScore" not in serialized
    assert "tier" not in serialized
    service.close()


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
