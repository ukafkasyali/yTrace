import json
from pathlib import Path

from fastapi.testclient import TestClient

from data_sourcing.api import create_app
from data_sourcing.config import Settings

BRIEF = (
    "Find 1 kHz robot collision and intentional contact time-series torque data from "
    "https://github.com/zhang-zengjie/robot-raw-collision-signals"
)


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
        candidate_id = run.json()["recommendedCandidateId"]

        wrong_approval = client.post(
            f"/api/sourcing-runs/{run_id}/approvals",
            json={"decision": "APPROVE", "candidateId": "ds_000000000000"},
        )
        assert wrong_approval.status_code == 409

        report = client.get(f"/api/sourcing-runs/{run_id}/report")
        assert report.status_code == 200
        assert "batch_count" in report.text
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
        assert "internal mechanical faults" in " ".join(manifest.json()["limitations"])

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


def test_refinement_response_persists_an_explicit_decision_delta(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path / "scout-data")
    with TestClient(create_app(settings)) as client:
        created = client.post(
            "/api/sourcing-runs",
            headers={"Idempotency-Key": "refinement-outcome"},
            json={"brief": BRIEF},
        )
        run_id = created.json()["runId"]
        candidate_id = client.get(
            f"/api/sourcing-runs/{run_id}"
        ).json()["recommendedCandidateId"]

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
        assert outcome["outcome"] == "RECOMMENDATION_WITHHELD"
        assert outcome["rejectedCandidateId"] == candidate_id
        assert refinement.json()["recommendedCandidateId"] is None
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
