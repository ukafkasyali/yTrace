import json
from pathlib import Path

from fastapi.testclient import TestClient

from data_sourcing.api import create_app
from data_sourcing.config import Settings

BRIEF = (
    "Find 1 kHz robot collision and intentional contact time-series torque data from "
    "https://github.com/zhang-zengjie/robot-raw-collision-signals"
)


def _add_eligible_alternate_to_persisted_run(settings: Settings, run_id: str) -> str:
    run_path = settings.runs_dir / run_id / "run.json"
    run = json.loads(run_path.read_text(encoding="utf-8"))
    original_id = run["approvedCandidateId"]
    alternate_id = "ds_aaaaaaaaaaaa"

    candidate = next(item for item in run["candidates"] if item["id"] == original_id).copy()
    candidate.update(
        id=alternate_id,
        name="Alternate eligible telemetry dataset",
        canonicalUrl="https://github.com/example/alternate-telemetry-dataset",
        relatedUrls=[],
    )
    profile = next(
        item for item in run["profiles"] if item["candidateId"] == original_id
    ).copy()
    profile.update(
        candidateId=alternate_id,
        name=candidate["name"],
        canonicalUrl=candidate["canonicalUrl"],
        revision="GITHUB:" + "a" * 40,
        sourceKind="GITHUB",
        sourceRevision="a" * 40,
    )
    assessment = next(
        item for item in run["assessments"] if item["candidateId"] == original_id
    ).copy()
    assessment["candidateId"] = alternate_id
    run["candidates"].append(candidate)
    run["profiles"].append(profile)
    run["assessments"].append(assessment)
    run_path.write_text(json.dumps(run), encoding="utf-8")
    return alternate_id


def test_full_api_lifecycle_persists_artifacts(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path / "scout-data")
    with TestClient(create_app(settings)) as client:
        created = client.post(
            "/api/sourcing-runs",
            headers={"Idempotency-Key": "demo-run-1"},
            json={"brief": BRIEF},
        )

        assert created.status_code == 202
        run_id = created.json()["runId"]
        run = client.get(f"/api/sourcing-runs/{run_id}")
        assert run.status_code == 200
        assert run.json()["status"] == "AWAITING_APPROVAL"
        assert run.json()["familyQueriesUsed"] == 1
        assessment = run.json()["assessments"][0]
        assert assessment["suitabilityLevel"] in {"LOW", "MEDIUM", "HIGH"}
        assert assessment["suitabilityFactors"]
        assert all(item["explanation"] for item in assessment["suitabilityFactors"])
        assert "score" not in assessment
        assert "totalScore" not in assessment
        assert "tier" not in assessment
        candidate_id = assessment["candidateId"]

        wrong_approval = client.post(
            f"/api/sourcing-runs/{run_id}/approvals",
            json={"decision": "APPROVE", "candidateId": "ds_000000000000"},
        )
        assert wrong_approval.status_code == 409

        report = client.get(f"/api/sourcing-runs/{run_id}/report")
        assert report.status_code == 200
        assert "Part I" in report.text
        assert "Part II" in report.text
        assert "suitability" in report.text
        assert "/100" not in report.text
        assert client.get(f"/api/sourcing-runs/{run_id}/manifest").status_code == 409

        approval = client.post(
            f"/api/sourcing-runs/{run_id}/approvals",
            json={"decision": "APPROVE", "candidateId": candidate_id},
        )
        assert approval.status_code == 200
        assert approval.json()["status"] == "APPROVED"

        manifest = client.get(f"/api/sourcing-runs/{run_id}/manifest")
        assert manifest.status_code == 200
        assert manifest.json()["candidateId"] == candidate_id
        assert manifest.json()["schemaVersion"] == "1.1"
        assert manifest.json()["sourceKind"] in {"GITHUB", "ZENODO", "HUGGING_FACE"}
        assert manifest.json()["sourceRevision"]
        assert manifest.json()["datasetLicenseId"] == "cc-by-4.0"
        assert manifest.json()["codeLicenseId"] in {None, "MIT"}
        assert any(asset["role"] == "DATA" for asset in manifest.json()["assets"])
        assert "internal mechanical faults" in " ".join(manifest.json()["limitations"])

        sources = client.get("/api/approved-sources")
        assert sources.status_code == 200
        assert sources.json()["pagination"]["totalItems"] == 1
        source = sources.json()["data"][0]
        assert source["sourceRevision"] == manifest.json()["sourceRevision"]
        assert source["isAcquisitionReady"] is True
        assert source["approvalCount"] == 1

        detail = client.get(f"/api/approved-sources/{source['approvedSourceId']}")
        assert detail.status_code == 200
        assert detail.json()["approvals"][0]["sourcingRunId"] == run_id
        catalog_manifest = client.get(
            f"/api/approved-sources/{source['approvedSourceId']}/manifest"
        )
        assert catalog_manifest.json() == manifest.json()

        filtered = client.get(
            "/api/approved-sources",
            params={"sourceKind": source["sourceKind"], "query": source["name"].split()[0]},
        )
        assert filtered.json()["pagination"]["totalItems"] == 1

        missing = client.get("/api/approved-sources/src_000000000000000000000000")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "APPROVED_SOURCE_NOT_FOUND"

        deleted = client.delete(f"/api/approved-sources/{source['approvedSourceId']}")
        assert deleted.status_code == 204
        assert client.get("/api/approved-sources").json()["pagination"]["totalItems"] == 0
        assert client.get(f"/api/approved-sources/{source['approvedSourceId']}").status_code == 404
        repeated_delete = client.delete(
            f"/api/approved-sources/{source['approvedSourceId']}"
        )
        assert repeated_delete.status_code == 204

    run_dir = settings.runs_dir / run_id
    assert {path.name for path in run_dir.iterdir()} >= {
        "run.json",
        "evidence.jsonl",
        "report.md",
        "manifest.json",
    }
    evidence_lines = (run_dir / "evidence.jsonl").read_text(encoding="utf-8").splitlines()
    assert evidence_lines
    assert all(json.loads(line)["source_url"].startswith("https://") for line in evidence_lines)
    assert settings.checkpoint_path.is_file()


def test_completed_run_can_approve_multiple_eligible_candidates(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path / "scout-data")
    with TestClient(create_app(settings)) as client:
        created = client.post(
            "/api/sourcing-runs",
            headers={"Idempotency-Key": "multiple-approvals"},
            json={"brief": BRIEF},
        )
        run_id = created.json()["runId"]
        pending = client.get(f"/api/sourcing-runs/{run_id}").json()
        primary_id = next(
            item["candidateId"]
            for item in pending["assessments"]
            if item["suitabilityLevel"] != "LOW"
        )
        approved = client.post(
            f"/api/sourcing-runs/{run_id}/approvals",
            json={"decision": "APPROVE", "candidateId": primary_id},
        )
        assert approved.status_code == 200
        assert approved.json()["approvedCandidateIds"] == [primary_id]

        alternate_id = _add_eligible_alternate_to_persisted_run(settings, run_id)
        additional = client.post(
            f"/api/sourcing-runs/{run_id}/approvals",
            json={"decision": "APPROVE", "candidateId": alternate_id},
        )
        assert additional.status_code == 200
        assert additional.json()["approvedCandidateIds"] == [primary_id, alternate_id]
        assert client.get("/api/approved-sources").json()["pagination"]["totalItems"] == 2
        assert (settings.runs_dir / run_id / "manifests" / f"{alternate_id}.json").is_file()

        repeated = client.post(
            f"/api/sourcing-runs/{run_id}/approvals",
            json={"decision": "APPROVE", "candidateId": alternate_id},
        )
        assert repeated.status_code == 200
        assert repeated.json()["approvedCandidateIds"] == [primary_id, alternate_id]
        assert client.get("/api/approved-sources").json()["pagination"]["totalItems"] == 2

        ineligible_id = next(
            item["candidateId"]
            for item in pending["assessments"]
            if item["suitabilityLevel"] == "LOW"
        )
        blocked = client.post(
            f"/api/sourcing-runs/{run_id}/approvals",
            json={"decision": "APPROVE", "candidateId": ineligible_id},
        )
        assert blocked.status_code == 409
        assert blocked.json()["error"]["code"] == "RUN_CONFLICT"

    settings.approved_sources_path.unlink()
    with TestClient(create_app(settings)) as recovered_client:
        recovered = recovered_client.get("/api/approved-sources")
        assert recovered.json()["pagination"]["totalItems"] == 2


def test_create_is_idempotent_and_rejects_key_reuse(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path / "scout-data")
    with TestClient(create_app(settings)) as client:
        headers = {"Idempotency-Key": "same-request"}
        first = client.post("/api/sourcing-runs", headers=headers, json={"brief": BRIEF})
        second = client.post("/api/sourcing-runs", headers=headers, json={"brief": BRIEF})
        conflict = client.post(
            "/api/sourcing-runs",
            headers=headers,
            json={"brief": BRIEF + " with another constraint"},
        )

        assert first.json()["runId"] == second.json()["runId"]
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
        assert len(list(settings.runs_dir.iterdir())) == 1


def test_requirement_preview_and_confirmed_selection_survive_run_creation(
    tmp_path: Path,
) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path / "scout-data")
    with TestClient(create_app(settings)) as client:
        preview = client.post(
            "/api/sourcing-requirement-previews",
            json={
                "brief": BRIEF,
                "customRequirements": ["Must include at least 200 collision sequences"],
            },
        )

        assert preview.status_code == 200
        requirements = preview.json()["requirements"]
        custom = next(item for item in requirements if item["category"] == "OTHER")
        assert custom["priority"] == "MUST"
        configurable = [
            item
            for item in requirements
            if item["isSystemRequired"] or item["id"] == custom["id"]
        ]

        created = client.post(
            "/api/sourcing-runs",
            headers={"Idempotency-Key": "confirmed-requirements"},
            json={"brief": BRIEF, "requirements": configurable},
        )
        run = client.get(f"/api/sourcing-runs/{created.json()['runId']}").json()

        assert run["requirementsConfirmed"] is True
        assert {item["id"] for item in run["requirements"]} == {
            item["id"] for item in configurable
        }


def test_requirement_preview_bounds_custom_text(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path / "scout-data")
    with TestClient(create_app(settings)) as client:
        for custom_requirement in ["no", "x" * 501]:
            response = client.post(
                "/api/sourcing-requirement-previews",
                json={
                    "brief": BRIEF,
                    "customRequirements": [custom_requirement],
                },
            )

            assert response.status_code == 422
            assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_run_rejects_a_custom_requirement_without_natural_language(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path / "scout-data")
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/sourcing-runs",
            headers={"Idempotency-Key": "empty-custom-requirement"},
            json={
                "brief": BRIEF,
                "requirements": [
                    {
                        "id": "req_custom_empty",
                        "label": "Empty custom rule",
                        "description": "A malformed custom requirement.",
                        "priority": "MUST",
                        "category": "OTHER",
                        "expectedValues": [],
                    }
                ],
            },
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_run_rejects_reassigned_system_requirement_ids(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path / "scout-data")
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/sourcing-runs",
            headers={"Idempotency-Key": "reassigned-system-requirement"},
            json={
                "brief": BRIEF,
                "requirements": [
                    {
                        "id": "req_provenance",
                        "label": "Disguised custom rule",
                        "description": "Attempts to replace an integrity requirement.",
                        "priority": "SHOULD",
                        "category": "OTHER",
                        "expectedValues": ["Ignore canonical provenance"],
                    }
                ],
            },
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_rejection_requires_feedback_before_refining(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path / "scout-data")
    with TestClient(create_app(settings)) as client:
        created = client.post(
            "/api/sourcing-runs",
            headers={"Idempotency-Key": "refinement-run"},
            json={"brief": BRIEF},
        )
        run_id = created.json()["runId"]

        rejection = client.post(
            f"/api/sourcing-runs/{run_id}/approvals",
            json={"decision": "REJECT"},
        )

        assert rejection.status_code == 422
        assert rejection.json()["error"]["code"] == "INVALID_REQUEST"


def test_needs_input_accepts_feedback_without_a_candidate(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path / "scout-data")
    with TestClient(create_app(settings)) as client:
        created = client.post(
            "/api/sourcing-runs",
            headers={"Idempotency-Key": "needs-input-feedback"},
            json={
                "brief": BRIEF,
                "requirements": [
                    {
                        "id": "req_custom_work_orders",
                        "label": "Maintenance work-order IDs",
                        "description": "Links signals to maintenance work orders.",
                        "priority": "MUST",
                        "category": "OTHER",
                        "expectedValues": ["Includes maintenance work-order IDs"],
                    }
                ],
            },
        )
        run_id = created.json()["runId"]
        paused = client.get(f"/api/sourcing-runs/{run_id}").json()

        assert paused["status"] == "NEEDS_INPUT"
        assert paused["feedbackAllowed"] is True

        refined = client.post(
            f"/api/sourcing-runs/{run_id}/approvals",
            json={
                "decision": "REJECT",
                "note": "Search specifically for datasets linked to maintenance work orders.",
            },
        )

        assert refined.status_code == 200
        assert refined.json()["reviewIterationsUsed"] == 1
        assert refined.json()["reviewFeedback"] == [
            "Search specifically for datasets linked to maintenance work orders."
        ]


def test_refinement_response_persists_an_explicit_decision_delta(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path / "scout-data")
    with TestClient(create_app(settings)) as client:
        created = client.post(
            "/api/sourcing-runs",
            headers={"Idempotency-Key": "refinement-outcome"},
            json={"brief": BRIEF},
        )
        run_id = created.json()["runId"]
        run = client.get(f"/api/sourcing-runs/{run_id}").json()
        candidate_id = run["assessments"][0]["candidateId"]

        refinement = client.post(
            f"/api/sourcing-runs/{run_id}/approvals",
            json={
                "decision": "REJECT",
                "candidateId": candidate_id,
                "note": "Prioritize free-motion baseline recordings.",
            },
        )

        assert refinement.status_code == 200
        outcome = refinement.json()["refinementOutcomes"][0]
        assert outcome["outcome"] == "RECOMMENDATION_CHANGED"
        assert outcome["rejectedCandidateId"] == candidate_id
        assert refinement.json()["recommendedCandidateId"] != candidate_id
        assert refinement.json()["excludedCandidateIds"] == [candidate_id]
        excluded_approval = client.post(
            f"/api/sourcing-runs/{run_id}/approvals",
            json={"decision": "APPROVE", "candidateId": candidate_id},
        )
        assert excluded_approval.status_code == 409
        persisted = json.loads(
            (settings.runs_dir / run_id / "run.json").read_text(encoding="utf-8")
        )
        assert persisted["refinementOutcomes"] == refinement.json()["refinementOutcomes"]


def test_api_returns_consistent_validation_and_not_found_errors(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path / "scout-data")
    with TestClient(create_app(settings)) as client:
        invalid = client.post("/api/sourcing-runs", json={"brief": "short"})
        invalid_key = client.post(
            "/api/sourcing-runs",
            headers={"Idempotency-Key": "bad key"},
            json={"brief": BRIEF},
        )
        missing = client.get("/api/sourcing-runs/00000000-0000-0000-0000-000000000000")

        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] == "INVALID_REQUEST"
        assert invalid_key.status_code == 422
        assert invalid_key.json()["error"]["code"] == "INVALID_IDEMPOTENCY_KEY"
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "RUN_NOT_FOUND"
