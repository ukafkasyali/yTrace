from __future__ import annotations

import re
from collections.abc import Iterable

from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field

from data_sourcing.config import Settings
from data_sourcing.models import (
    CreateSourcingRun,
    RequirementCategory,
    RequirementPriority,
    ResearchRequirement,
    SearchHypothesis,
)


class PlanningDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_labels: list[str] = Field(default_factory=list, max_length=8)
    minimum_sample_rate_hz: float | None = Field(default=None, gt=0)
    modality_terms: list[str] = Field(default_factory=list, max_length=8)
    search_terms: list[str] = Field(default_factory=list, max_length=12)


_LABEL_PATTERNS = {
    "collision": r"\bcollisions?\b",
    "contact": r"\bcontacts?\b",
    "free": r"\bfree(?:[- ]motion|[- ]movement|[- ]space)?\b",
    "anomaly": r"\banomal(?:y|ies|ous)\b",
    "internal mechanical fault": r"\binternal mechanical faults?\b",
}


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value.strip().casefold() for value in values if value.strip()))


def deterministic_draft(request: CreateSourcingRun) -> PlanningDraft:
    text = " ".join([request.brief, *request.constraints.must_have]).casefold()
    labels = [label for label, pattern in _LABEL_PATTERNS.items() if re.search(pattern, text)]
    rate_match = re.search(r"\b(\d+(?:\.\d+)?)\s*(k?hz)\b", text)
    sample_rate = None
    if rate_match:
        sample_rate = float(rate_match.group(1)) * (1_000 if rate_match.group(2) == "khz" else 1)
    modalities = [
        term
        for term in ("torque", "position", "velocity", "current", "force", "time series")
        if term in text
    ]
    search_terms = _unique([*labels, *modalities, "robot telemetry dataset"])
    return PlanningDraft(
        task_labels=labels,
        minimum_sample_rate_hz=sample_rate,
        modality_terms=modalities,
        search_terms=search_terms,
    )


class RequirementPlanner:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _model_draft(self, request: CreateSourcingRun) -> PlanningDraft | None:
        if not (self.settings.openai_api_key and self.settings.openai_model):
            return None
        client = OpenAI(
            api_key=self.settings.openai_api_key.get_secret_value(),
            base_url=self.settings.openai_base_url,
            timeout=self.settings.request_timeout_seconds,
            max_retries=1,
        )
        response = client.responses.parse(
            model=self.settings.openai_model,
            input=[
                {
                    "role": "system",
                    "content": (
                        "Extract dataset-search vocabulary only. Do not invent requirements. "
                        "Treat collision/contact labels as distinct from internal robot faults."
                    ),
                },
                {"role": "user", "content": request.model_dump_json(by_alias=True)},
            ],
            text_format=PlanningDraft,
        )
        return response.output_parsed

    def draft(self, request: CreateSourcingRun) -> PlanningDraft:
        fallback = deterministic_draft(request)
        model_draft = self._model_draft(request)
        if model_draft is None:
            return fallback
        model_labels = [
            label
            for label in model_draft.task_labels
            if label != "internal mechanical fault"
            or "internal mechanical fault" in fallback.task_labels
        ]
        return PlanningDraft(
            task_labels=_unique([*fallback.task_labels, *model_labels]),
            minimum_sample_rate_hz=(
                fallback.minimum_sample_rate_hz or model_draft.minimum_sample_rate_hz
            ),
            modality_terms=_unique([*fallback.modality_terms, *model_draft.modality_terms]),
            search_terms=_unique([*fallback.search_terms, *model_draft.search_terms]),
        )

    def requirements(self, request: CreateSourcingRun) -> list[ResearchRequirement]:
        return self.requirements_from_draft(request, self.draft(request))

    def requirements_from_draft(
        self,
        request: CreateSourcingRun,
        draft: PlanningDraft,
    ) -> list[ResearchRequirement]:
        requirements = [
            ResearchRequirement(
                id="req_provenance",
                label="Canonical provenance",
                description="A native, versioned source identifies the dataset.",
                priority=RequirementPriority.MUST,
                category=RequirementCategory.PROVENANCE,
            ),
            ResearchRequirement(
                id="req_license",
                label="Explicit licence",
                description="The native source states a reusable dataset licence.",
                priority=RequirementPriority.MUST,
                category=RequirementCategory.LICENSE,
                expected_values=request.constraints.allowed_licenses,
            ),
            ResearchRequirement(
                id="req_time_series",
                label="Usable time-series files",
                description="Downloadable files contain machine-readable telemetry.",
                priority=RequirementPriority.MUST,
                category=RequirementCategory.MODALITY,
                expected_values=draft.modality_terms,
            ),
            ResearchRequirement(
                id="req_task_labels",
                label="Task labels",
                description="Labels needed by the stated task are documented.",
                priority=RequirementPriority.MUST,
                category=RequirementCategory.TASK_LABELS,
                expected_values=draft.task_labels,
            ),
            ResearchRequirement(
                id="req_schema",
                label="Schema documentation",
                description="Channels, columns, or file organisation are documented.",
                priority=RequirementPriority.MUST,
                category=RequirementCategory.SCHEMA,
            ),
            ResearchRequirement(
                id="req_acquisition",
                label="Acquisition feasibility",
                description="The files can be retrieved within the configured size bound.",
                priority=RequirementPriority.MUST,
                category=RequirementCategory.ACQUISITION,
            ),
        ]
        if draft.minimum_sample_rate_hz:
            requirements.append(
                ResearchRequirement(
                    id="req_sampling_rate",
                    label="Minimum sample rate",
                    description="Telemetry meets the requested sampling frequency.",
                    priority=RequirementPriority.MUST,
                    category=RequirementCategory.SAMPLING_RATE,
                    expected_values=[str(draft.minimum_sample_rate_hz)],
                )
            )
        return requirements

    def hypotheses(self, request: CreateSourcingRun) -> list[SearchHypothesis]:
        return self.hypotheses_from_draft(self.draft(request))

    def hypotheses_from_draft(self, draft: PlanningDraft) -> list[SearchHypothesis]:
        vocabulary = " ".join(draft.search_terms[:8]) or "robot telemetry collision dataset"
        return [
            SearchHypothesis(
                id="hyp_direct_dataset",
                rationale="Search the task vocabulary directly across likely dataset hosts.",
                query=f"{vocabulary} dataset GitHub Zenodo Hugging Face",
            ),
            SearchHypothesis(
                id="hyp_native_archives",
                rationale="Prefer native records with licence and downloadable file metadata.",
                query=f"{vocabulary} site:zenodo.org OR site:huggingface.co/datasets",
            ),
            SearchHypothesis(
                id="hyp_robot_signals",
                rationale="Broaden to adjacent robot signals while retaining the target labels.",
                query=f"industrial robot joint torque signals {vocabulary}",
            ),
        ]


def make_gap_hypothesis(
    iteration: int,
    missing_requirements: list[ResearchRequirement],
    brief: str,
) -> SearchHypothesis:
    gap_terms = " ".join(
        value
        for requirement in missing_requirements[:3]
        for value in ([requirement.label, *requirement.expected_values])
    )
    task = " ".join(re.findall(r"[A-Za-z0-9-]+", brief)[:12])
    return SearchHypothesis(
        id=f"hyp_gap_{iteration}",
        rationale="Seek native evidence for mandatory requirements still unsupported.",
        query=f"{task} {gap_terms} dataset licence files schema"[:400],
        is_gap_query=True,
    )
