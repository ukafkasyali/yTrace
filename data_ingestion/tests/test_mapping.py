import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from dataset_profiler.ingestion import (
    AssetReceipt,
    CreateIngestion,
    IngestionJobConflict,
    IngestionService,
    IngestionState,
    MappingLayout,
    MappingService,
    MappingValidationError,
    ResourceFormat,
    ResourceProfile,
)
from dataset_profiler.ingestion.api import create_app


class UnusedResolver:
    def resolve(self, approved_source_id: str):
        raise AssertionError(f"unexpected resolution: {approved_source_id}")

    def close(self) -> None:
        pass


class MappingServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.service = IngestionService(Path(self.temporary.name), UnusedResolver())
        self.job, _ = self.service.jobs.get_or_create(
            approved_source_id="src_0123456789abcdef01234567",
            manifest_sha256="a" * 64,
            source_url="https://zenodo.org/records/123",
            source_kind="ZENODO",
            source_revision="123.r1",
            asset_ids=["asset_0123456789abcdef"],
            state=IngestionState.QUEUED,
            message="queued",
        )
        self.service.jobs.record_receipt(
            AssetReceipt(
                ingestion_id=self.job.ingestion_id,
                asset_id="asset_0123456789abcdef",
                provider_locator="zenodo:123:signals.csv",
                expected_size_bytes=100,
                observed_size_bytes=100,
                content_sha256="b" * 64,
                content_key=f"sha256/bb/{'b' * 64}",
                acquired_at=datetime.now(UTC),
            )
        )
        self.profile = ResourceProfile(
            resource_id="res_0123456789abcdef01234567",
            ingestion_id=self.job.ingestion_id,
            asset_id="asset_0123456789abcdef",
            logical_path="signals.csv",
            size_bytes=100,
            content_sha256="c" * 64,
            format=ResourceFormat.CSV,
            details={
                "columns": [
                    {"name": "run", "dtype": "string", "nullable": False},
                    {"name": "time", "dtype": "float64", "nullable": False},
                    {"name": "joint_1", "dtype": "float64", "nullable": False},
                    {"name": "joint_2", "dtype": "float64", "nullable": True},
                ]
            },
            inspected_at=datetime.now(UTC),
        )
        self.service.jobs.record_resource(self.profile)
        self.job = self.service.jobs.set_state(
            self.job.ingestion_id,
            IngestionState.MAPPING,
            "mapping required",
        )
        self.mappings = MappingService(self.service.jobs)

    def tearDown(self) -> None:
        self.service.close()
        self.temporary.cleanup()

    def confirmed_candidate(self):
        candidate = self.mappings.proposals(self.job.ingestion_id)[0].candidates[0]
        return candidate.model_copy(
            update={
                "channels": [
                    channel.model_copy(update={"unit": "newton * meter"})
                    for channel in candidate.channels
                ]
            }
        )

    def test_wide_proposal_requires_explicit_units(self) -> None:
        proposal = self.mappings.proposals(self.job.ingestion_id)[0]

        self.assertEqual(len(proposal.candidates), 1)
        self.assertEqual(proposal.candidates[0].layout, MappingLayout.WIDE_TABLE)
        self.assertEqual(proposal.candidates[0].record_selector, "run")
        self.assertEqual(
            [channel.selector for channel in proposal.candidates[0].channels],
            ["joint_1", "joint_2"],
        )
        self.assertIn("CHANNEL_UNITS_REQUIRE_CONFIRMATION", proposal.issues)
        with self.assertRaises(MappingValidationError):
            self.mappings.confirm(self.job.ingestion_id, proposal.candidates[0])
        confirmed = self.confirmed_candidate()
        with self.assertRaisesRegex(MappingValidationError, "absent"):
            self.mappings.confirm(
                self.job.ingestion_id,
                confirmed.model_copy(update={"time_selector": "not_a_column"}),
            )

    def test_confirm_is_idempotent_and_rejects_stale_or_conflicting_mapping(self) -> None:
        confirmed = self.confirmed_candidate()
        with self.assertRaisesRegex(IngestionJobConflict, "stale"):
            self.mappings.confirm(
                self.job.ingestion_id,
                confirmed.model_copy(update={"job_revision": confirmed.job_revision - 1}),
            )
        with self.assertRaisesRegex(IngestionJobConflict, "current resource"):
            self.mappings.confirm(
                self.job.ingestion_id,
                confirmed.model_copy(update={"resource_sha256": "d" * 64}),
            )

        first = self.mappings.confirm(self.job.ingestion_id, confirmed)
        repeated = self.mappings.confirm(self.job.ingestion_id, confirmed)

        self.assertTrue(first.created)
        self.assertFalse(repeated.created)
        self.assertEqual(first.mapping_sha256, repeated.mapping_sha256)
        self.assertEqual(
            self.service.jobs.get(self.job.ingestion_id).state,
            IngestionState.VALIDATING,
        )
        changed = confirmed.model_copy(
            update={
                "channels": [
                    confirmed.channels[0].model_copy(update={"unit": "radian"}),
                    confirmed.channels[1],
                ]
            }
        )
        with self.assertRaisesRegex(IngestionJobConflict, "another confirmed mapping"):
            self.mappings.confirm(self.job.ingestion_id, changed)

    def test_failed_mapped_job_retries_the_import_without_reacquisition(self) -> None:
        self.mappings.confirm(self.job.ingestion_id, self.confirmed_candidate())
        self.service.jobs.set_state(self.job.ingestion_id, IngestionState.FAILED, "failed")

        retried, created = self.service.create(
            CreateIngestion(
                approved_source_id=self.job.approved_source_id,
                asset_ids=self.job.asset_ids,
            )
        )

        self.assertFalse(created)
        self.assertEqual(retried.ingestion_id, self.job.ingestion_id)
        self.assertEqual(retried.state, IngestionState.VALIDATING)
        self.assertEqual(retried.message, "Queued for deterministic import retry")

    def test_ambiguous_time_and_array_axes_do_not_auto_resolve(self) -> None:
        no_time = self.profile.model_copy(
            update={
                "resource_id": "res_111111111111111111111111",
                "logical_path": "no-time.csv",
                "content_sha256": "d" * 64,
                "details": {
                    "columns": [
                        {"name": "sample", "dtype": "int64", "nullable": False},
                        {"name": "joint", "dtype": "float64", "nullable": False},
                    ]
                },
            }
        )
        square = self.profile.model_copy(
            update={
                "resource_id": "res_222222222222222222222222",
                "logical_path": "square.npz",
                "content_sha256": "e" * 64,
                "format": ResourceFormat.NPZ,
                "details": {
                    "arrays": [
                        {"name": "time", "shape": [2], "dtype": "float64", "sizeBytes": 16},
                        {
                            "name": "signals",
                            "shape": [2, 2],
                            "dtype": "float64",
                            "sizeBytes": 32,
                        },
                    ]
                },
            }
        )
        self.service.jobs.record_resource(no_time)
        self.service.jobs.record_resource(square)

        proposals = {
            proposal.resource_id: proposal
            for proposal in self.mappings.proposals(self.job.ingestion_id)
        }

        self.assertEqual(proposals[no_time.resource_id].issues, ["TIME_SELECTOR_AMBIGUOUS"])
        self.assertEqual(proposals[square.resource_id].issues, ["ARRAY_AXES_AMBIGUOUS"])

    def test_api_exposes_proposals_and_confirms_mapping(self) -> None:
        with TestClient(create_app(self.service)) as client:
            proposals = client.get(
                f"/api/ingestions/{self.job.ingestion_id}/mapping-proposals"
            )
            mapping = self.confirmed_candidate()
            confirmed = client.put(
                f"/api/ingestions/{self.job.ingestion_id}/mapping",
                json=mapping.model_dump(mode="json", by_alias=True),
            )
            fetched = client.get(f"/api/ingestions/{self.job.ingestion_id}/mapping")

        self.assertEqual(proposals.status_code, 200)
        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(fetched.json()["mappingSha256"], confirmed.json()["mappingSha256"])


if __name__ == "__main__":
    unittest.main()
