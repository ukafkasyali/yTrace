import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from data_sourcing.config import Settings
from data_sourcing.models import ApprovalRequest, CreateSourcingRun, RunStatus, SourcingManifest
from data_sourcing.service import SourcingService
from data_sourcing.storage import ApprovedSourceNotFound, ApprovedSourceStore, IdempotencyStore

BRIEF = (
    "Find robot collision and contact time series from "
    "https://github.com/zhang-zengjie/robot-raw-collision-signals"
)


def test_legacy_manifest_defaults_to_schema_1_0_without_assets(tmp_path: Path) -> None:
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
    store = ApprovedSourceStore(tmp_path / "approved.sqlite3")
    approved_source_id = store.record_approval(manifest)
    assert store.get(approved_source_id).is_acquisition_ready is False
    store.close()


def approved_manifest(*, revision: str, approved_at: datetime | None = None) -> SourcingManifest:
    return SourcingManifest(
        schema_version="1.1",
        run_id=str(uuid4()),
        candidate_id="ds_0123456789ab",
        name="Robot signal dataset",
        canonical_url="https://zenodo.org/records/123",
        revision=f"ZENODO:{revision}",
        source_kind="ZENODO",
        source_revision=revision,
        assets=[
            {
                "assetId": "asset_0123456789abcdef",
                "name": "signals.csv",
                "role": "DATA",
                "sizeBytes": 100,
                "providerLocator": f"zenodo:123:{revision}:signals.csv",
                "downloadUrl": "https://zenodo.org/api/files/123/signals.csv",
            }
        ],
        license_id="cc-by-4.0",
        labels=["collision"],
        file_extensions=[".csv"],
        total_size_bytes=100,
        evidence_ids=["ev_0123456789abcdef"],
        limitations=[],
        approved_at=approved_at or datetime.now(UTC),
    )


def test_approved_source_store_groups_approval_history_by_revision(tmp_path: Path) -> None:
    store = ApprovedSourceStore(tmp_path / "approved.sqlite3")
    first = approved_manifest(revision="123.r1")
    second = approved_manifest(
        revision="123.r1",
        approved_at=first.approved_at + timedelta(seconds=1),
    )
    changed = approved_manifest(
        revision="123.r2",
        approved_at=first.approved_at + timedelta(seconds=2),
    )

    first_id = store.record_approval(first)
    second_id = store.record_approval(second)
    changed_id = store.record_approval(changed)
    page = store.list(page=1, page_size=1)
    next_page = store.list(page=2, page_size=1)

    assert first_id == second_id
    assert changed_id != first_id
    assert page.pagination.total_items == 2
    assert page.pagination.total_pages == 2
    assert page.data[0].approved_source_id != next_page.data[0].approved_source_id
    original = store.get(first_id)
    assert original.approval_count == 2
    assert {event.sourcing_run_id for event in original.approvals} == {
        first.run_id,
        second.run_id,
    }
    store.close()


def test_deleted_source_stays_out_of_catalog_until_a_new_approval(tmp_path: Path) -> None:
    store = ApprovedSourceStore(tmp_path / "approved.sqlite3")
    first = approved_manifest(revision="123.r1")
    approved_source_id = store.record_approval(first)

    store.delete(approved_source_id)
    store.delete(approved_source_id)
    assert store.list(page=1, page_size=20).pagination.total_items == 0
    try:
        store.get(approved_source_id)
    except ApprovedSourceNotFound:
        pass
    else:
        raise AssertionError("Deleted approved source remained readable")

    store.record_approval(first, restore_deleted=False)
    assert store.list(page=1, page_size=20).pagination.total_items == 0

    next_approval = first.model_copy(
        update={"run_id": str(uuid4()), "approved_at": first.approved_at + timedelta(seconds=1)}
    )
    store.record_approval(next_approval)
    assert store.list(page=1, page_size=20).pagination.total_items == 1
    store.close()


def test_catalog_reconciles_approved_run_after_catalog_loss(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path / "persisted")
    first = SourcingService(settings)
    run_id, _ = first.create_run(CreateSourcingRun(brief=BRIEF), "catalog-reconcile")
    first.execute_run(run_id)
    candidate_id = first.artifacts.read_run(run_id).assessments[0].candidate_id
    first.approve(run_id, ApprovalRequest(decision="APPROVE", candidate_id=candidate_id))
    first.close()
    settings.approved_sources_path.unlink()

    second = SourcingService(settings)
    page = second.approved_sources.list(page=1, page_size=20)

    assert page.pagination.total_items == 1
    assert page.data[0].approval_count == 1
    second.close()


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
