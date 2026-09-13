import json
from pathlib import Path

from data_sourcing.models import SourcingManifest


def test_shared_manifest_v1_1_fixture_matches_producer_contract() -> None:
    fixture = (
        Path(__file__).resolve().parents[2]
        / "docs"
        / "contracts"
        / "approved-source-manifest-v1.1.json"
    )
    manifest = SourcingManifest.model_validate(json.loads(fixture.read_text(encoding="utf-8")))

    assert manifest.schema_version == "1.1"
    assert manifest.dataset_license_id == "cc-by-4.0"
    assert manifest.assets[0].name == "signals.csv"
