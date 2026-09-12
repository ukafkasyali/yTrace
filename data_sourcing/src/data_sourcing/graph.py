from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any, TypedDict

import httpx
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from data_sourcing.adapters import NativeVerifier, TavilySearchAdapter, canonicalize_results
from data_sourcing.adapters.discovery import SourceUnavailable
from data_sourcing.config import Settings
from data_sourcing.models import (
    ApprovalDecision,
    ApprovalRequest,
    CandidateAssessment,
    CandidateTier,
    CreateSourcingRun,
    DatasetCandidate,
    DatasetProfile,
    EvidenceRecord,
    ExecutionMode,
    HypothesisStatus,
    ResearchRequirement,
    RunStatus,
    SearchHypothesis,
    SearchResult,
    SourcingConstraints,
    SourcingManifest,
    VerificationStatus,
)
from data_sourcing.planning import RequirementPlanner, make_gap_hypothesis
from data_sourcing.scoring import (
    apply_recommendation_confidence,
    assess_candidate,
    requirement_is_evidenced,
)


class SourcingState(TypedDict, total=False):
    run_id: str
    brief: str
    constraints: dict[str, Any]
    status: str
    requirements: list[dict[str, Any]]
    hypotheses: list[dict[str, Any]]
    search_results: list[dict[str, Any]]
    candidates: list[dict[str, Any]]
    profiles: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    assessments: list[dict[str, Any]]
    recommended_candidate_id: str | None
    gap_queries_used: int
    tavily_credits_used: int
    execution_mode: str
    errors: list[str]
    report_markdown: str
    manifest: dict[str, Any] | None
    started_at: float
    allow_cached_demo: bool
    approval_decision: str | None


def initial_state(
    run_id: str,
    request: CreateSourcingRun,
    *,
    allow_cached_demo: bool,
) -> SourcingState:
    return {
        "run_id": run_id,
        "brief": request.brief,
        "constraints": request.constraints.model_dump(mode="json"),
        "status": RunStatus.QUEUED.value,
        "requirements": [],
        "hypotheses": [],
        "search_results": [],
        "candidates": [],
        "profiles": [],
        "evidence": [],
        "assessments": [],
        "recommended_candidate_id": None,
        "gap_queries_used": 0,
        "tavily_credits_used": 0,
        "execution_mode": ExecutionMode.LIVE.value,
        "errors": [],
        "report_markdown": "",
        "manifest": None,
        "started_at": time.time(),
        "allow_cached_demo": allow_cached_demo,
        "approval_decision": None,
    }


def _json_list(items: list[Any]) -> list[dict[str, Any]]:
    return [item.model_dump(mode="json") for item in items]


def _request(state: SourcingState) -> CreateSourcingRun:
    return CreateSourcingRun(
        brief=state["brief"],
        constraints=SourcingConstraints.model_validate(state["constraints"]),
    )


class DatasetScoutGraph:
    def __init__(
        self,
        settings: Settings,
        checkpointer: Any,
        *,
        search: TavilySearchAdapter | None = None,
        verifier: NativeVerifier | None = None,
    ):
        self.settings = settings
        self.planner = RequirementPlanner(settings)
        self.search = search or TavilySearchAdapter(settings)
        self.verifier = verifier or NativeVerifier(settings)
        self.graph = self._build().compile(checkpointer=checkpointer)

    def close(self) -> None:
        self.search.close()
        self.verifier.close()

    def _build(self) -> StateGraph:
        builder = StateGraph(SourcingState)
        builder.add_node("requirements", self.requirements)
        builder.add_node("hypotheses", self.hypotheses)
        builder.add_node("discovery", self.discovery)
        builder.add_node("canonicalization", self.canonicalization)
        builder.add_node("verification", self.verification)
        builder.add_node("coverage_assessment", self.coverage_assessment)
        builder.add_node("gap_search", self.gap_search)
        builder.add_node("scoring", self.scoring)
        builder.add_node("approval", self.approval)
        builder.add_node("manifest_generation", self.manifest_generation)
        builder.add_edge(START, "requirements")
        builder.add_edge("requirements", "hypotheses")
        builder.add_edge("hypotheses", "discovery")
        builder.add_edge("discovery", "canonicalization")
        builder.add_edge("canonicalization", "verification")
        builder.add_edge("verification", "coverage_assessment")
        builder.add_conditional_edges(
            "coverage_assessment",
            self.route_after_coverage,
            {"gap": "gap_search", "score": "scoring"},
        )
        builder.add_edge("gap_search", "canonicalization")
        builder.add_conditional_edges(
            "scoring",
            self.route_after_scoring,
            {"approval": "approval", "end": END},
        )
        builder.add_conditional_edges(
            "approval",
            self.route_after_approval,
            {"manifest": "manifest_generation", "end": END},
        )
        builder.add_edge("manifest_generation", END)
        return builder

    def _expired(self, state: SourcingState) -> bool:
        return time.time() - state["started_at"] >= self.settings.run_timeout_seconds

    def requirements(self, state: SourcingState) -> dict[str, Any]:
        requirements = self.planner.requirements(_request(state))
        return {
            "status": RunStatus.PLANNING.value,
            "requirements": _json_list(requirements),
        }

    def hypotheses(self, state: SourcingState) -> dict[str, Any]:
        hypotheses = self.planner.hypotheses(_request(state))[: self.settings.initial_query_limit]
        return {"hypotheses": _json_list(hypotheses)}

    def _perform_searches(
        self,
        state: SourcingState,
        hypotheses: list[SearchHypothesis],
    ) -> dict[str, Any]:
        results = [SearchResult.model_validate(item) for item in state["search_results"]]
        credits = state["tavily_credits_used"]
        errors = list(state["errors"])
        mode = ExecutionMode(state["execution_mode"])
        for hypothesis in hypotheses:
            if self._expired(state) or credits + 2 > self.settings.tavily_credit_limit:
                errors.append("Search stopped at the configured time or credit budget")
                break
            try:
                batch = self.search.search(
                    hypothesis.query,
                    allow_cached_demo=state["allow_cached_demo"],
                )
            except (SourceUnavailable, ValueError, OSError, httpx.HTTPError) as exc:
                errors.append(f"Search hypothesis {hypothesis.id} failed: {exc}")
                hypothesis.status = HypothesisStatus.EXHAUSTED
                continue
            credits += batch.credits_used
            results.extend(batch.results)
            hypothesis.status = HypothesisStatus.SEARCHED
            if batch.execution_mode is ExecutionMode.CACHED:
                mode = ExecutionMode.CACHED
            elif mode is ExecutionMode.CACHED:
                mode = ExecutionMode.PARTIAL
        all_hypotheses = [SearchHypothesis.model_validate(item) for item in state["hypotheses"]]
        by_id = {item.id: item for item in all_hypotheses}
        by_id.update({item.id: item for item in hypotheses})
        return {
            "status": RunStatus.DISCOVERING.value,
            "hypotheses": _json_list(list(by_id.values())),
            "search_results": _json_list(results),
            "tavily_credits_used": credits,
            "execution_mode": mode.value,
            "errors": errors,
        }

    def discovery(self, state: SourcingState) -> dict[str, Any]:
        hypotheses = [
            SearchHypothesis.model_validate(item)
            for item in state["hypotheses"]
            if not item.get("is_gap_query") and item.get("status") == HypothesisStatus.PLANNED.value
        ][: self.settings.initial_query_limit]
        return self._perform_searches(state, hypotheses)

    def canonicalization(self, state: SourcingState) -> dict[str, Any]:
        results = [SearchResult.model_validate(item) for item in state["search_results"]]
        candidates = canonicalize_results(results, self.settings.candidate_limit)
        return {"candidates": _json_list(candidates)}

    def verification(self, state: SourcingState) -> dict[str, Any]:
        candidates = [DatasetCandidate.model_validate(item) for item in state["candidates"]]
        known_profiles = {
            item.candidate_id: item
            for item in (DatasetProfile.model_validate(raw) for raw in state["profiles"])
        }
        evidence = [EvidenceRecord.model_validate(item) for item in state["evidence"]]
        errors = list(state["errors"])
        constraints = SourcingConstraints.model_validate(state["constraints"])
        for candidate in candidates:
            if candidate.id in known_profiles or self._expired(state):
                continue
            try:
                verified = self.verifier.verify(
                    candidate,
                    cached=state["execution_mode"] == ExecutionMode.CACHED.value,
                    max_download_bytes=constraints.max_download_bytes,
                )
            except (SourceUnavailable, ValueError, OSError, httpx.HTTPError) as exc:
                errors.append(f"Candidate {candidate.id} verification failed: {exc}")
                continue
            known_profiles[candidate.id] = verified.profile
            evidence.extend(verified.evidence)
        deduplicated_evidence = {item.id: item for item in evidence}
        return {
            "status": RunStatus.VERIFYING.value,
            "profiles": _json_list(list(known_profiles.values())),
            "evidence": _json_list(list(deduplicated_evidence.values())),
            "errors": errors,
        }

    def coverage_assessment(self, state: SourcingState) -> dict[str, Any]:
        profiles = [DatasetProfile.model_validate(item) for item in state["profiles"]]
        requirements = [ResearchRequirement.model_validate(item) for item in state["requirements"]]
        evidence = [EvidenceRecord.model_validate(item) for item in state["evidence"]]
        for requirement in requirements:
            supporting_profiles = [
                profile
                for profile in profiles
                if requirement_is_evidenced(requirement, profile, evidence)
            ]
            requirement.status = (
                VerificationStatus.VERIFIED if supporting_profiles else VerificationStatus.MISSING
            )
            candidate_ids = {profile.candidate_id for profile in supporting_profiles}
            requirement.evidence_ids = [
                item.id for item in evidence if item.candidate_id in candidate_ids
            ]
        return {
            "status": RunStatus.ASSESSING.value,
            "requirements": _json_list(requirements),
        }

    def route_after_coverage(self, state: SourcingState) -> str:
        missing = [
            ResearchRequirement.model_validate(item)
            for item in state["requirements"]
            if item.get("priority") == "MUST"
            and item.get("status") != VerificationStatus.VERIFIED.value
        ]
        can_search = (
            bool(missing)
            and state["gap_queries_used"] < self.settings.gap_query_limit
            and state["tavily_credits_used"] + 2 <= self.settings.tavily_credit_limit
            and not self._expired(state)
        )
        return "gap" if can_search else "score"

    def gap_search(self, state: SourcingState) -> dict[str, Any]:
        requirements = [ResearchRequirement.model_validate(item) for item in state["requirements"]]
        missing = [item for item in requirements if item.status is not VerificationStatus.VERIFIED]
        iteration = state["gap_queries_used"] + 1
        hypothesis = make_gap_hypothesis(iteration, missing, state["brief"])
        hypotheses = [SearchHypothesis.model_validate(item) for item in state["hypotheses"]]
        hypotheses.append(hypothesis)
        temporary_state = dict(state)
        temporary_state["hypotheses"] = _json_list(hypotheses)
        update = self._perform_searches(temporary_state, [hypothesis])
        update["gap_queries_used"] = iteration
        return update

    def scoring(self, state: SourcingState) -> dict[str, Any]:
        profiles = [DatasetProfile.model_validate(item) for item in state["profiles"]]
        requirements = [ResearchRequirement.model_validate(item) for item in state["requirements"]]
        evidence = [EvidenceRecord.model_validate(item) for item in state["evidence"]]
        assessments = apply_recommendation_confidence(
            [assess_candidate(profile, requirements, evidence) for profile in profiles]
        )
        ranked = sorted(assessments, key=lambda item: (-item.total_score, item.candidate_id))
        missing_mandatory = [
            item.id
            for item in requirements
            if item.priority.value == "MUST" and item.status is not VerificationStatus.VERIFIED
        ]
        recommended = next(
            (item for item in ranked if item.tier is CandidateTier.RECOMMEND),
            None,
        )
        eligible = recommended is not None and not missing_mandatory
        status = RunStatus.AWAITING_APPROVAL if eligible else RunStatus.NEEDS_INPUT
        report = self._report(state, ranked, missing_mandatory)
        return {
            "status": status.value,
            "assessments": _json_list(ranked),
            "recommended_candidate_id": recommended.candidate_id if eligible else None,
            "report_markdown": report,
        }

    def _report(
        self,
        state: SourcingState,
        ranked: list[CandidateAssessment],
        missing: list[str],
    ) -> str:
        lines = [
            f"# Dataset sourcing report — {state['run_id']}",
            "",
            f"Execution mode: **{state['execution_mode']}**",
            (
                f"Tavily credits used: **{state['tavily_credits_used']} / "
                f"{self.settings.tavily_credit_limit}**"
            ),
            "",
        ]
        if missing:
            lines.extend(
                ["## Mandatory evidence gaps", "", *[f"- `{item}`" for item in missing], ""]
            )
        lines.extend(["## Deterministic ranking", ""])
        evidence = [EvidenceRecord.model_validate(item) for item in state["evidence"]]
        for assessment in ranked:
            conflict = ", ".join(assessment.conflicts) or "none"
            lines.append(
                f"- `{assessment.candidate_id}` — {assessment.total_score}/100, "
                f"{assessment.tier.value}; conflicts: {conflict}"
            )
            for claim in assessment.conflicts:
                lines.append(
                    f"  - {self._conflict_resolution(evidence, assessment.candidate_id, claim)}"
                )
        if state["errors"]:
            lines.extend(["", "## Retrieval notes", "", *[f"- {item}" for item in state["errors"]]])
        return "\n".join(lines) + "\n"

    @staticmethod
    def _conflict_resolution(
        evidence: list[EvidenceRecord],
        candidate_id: str,
        claim: str,
    ) -> str:
        records = sorted(
            (
                item
                for item in evidence
                if item.candidate_id == candidate_id and item.claim_key == claim
            ),
            key=lambda item: (-item.precedence, item.source_kind.value, item.observed_value),
        )
        observations = ", ".join(
            f"{item.source_kind.value}={item.observed_value} (precedence {item.precedence})"
            for item in records
        )
        if not records:
            return f"{claim}: no evidence records found"
        return (
            f"{claim}: {observations}. Ranking uses {records[0].observed_value} from "
            f"{records[0].source_kind.value}; all observations remain in evidence.jsonl."
        )

    @staticmethod
    def route_after_scoring(state: SourcingState) -> str:
        return "approval" if state["status"] == RunStatus.AWAITING_APPROVAL.value else "end"

    def approval(self, state: SourcingState) -> dict[str, Any]:
        payload = interrupt(
            {
                "runId": state["run_id"],
                "recommendedCandidateId": state["recommended_candidate_id"],
                "message": "Approve the evidence-backed dataset manifest?",
            }
        )
        approval = ApprovalRequest.model_validate(payload)
        if (
            approval.decision is ApprovalDecision.APPROVE
            and approval.candidate_id != state["recommended_candidate_id"]
        ):
            return {
                "status": RunStatus.NEEDS_INPUT.value,
                "approval_decision": ApprovalDecision.REJECT.value,
                "errors": [
                    *state["errors"],
                    "Approval candidate did not match the recommendation",
                ],
            }
        status = (
            RunStatus.APPROVED
            if approval.decision is ApprovalDecision.APPROVE
            else RunStatus.REJECTED
        )
        return {"status": status.value, "approval_decision": approval.decision.value}

    @staticmethod
    def route_after_approval(state: SourcingState) -> str:
        return (
            "manifest"
            if state.get("approval_decision") == ApprovalDecision.APPROVE.value
            else "end"
        )

    def manifest_generation(self, state: SourcingState) -> dict[str, Any]:
        candidate_id = state["recommended_candidate_id"]
        profile = next(
            DatasetProfile.model_validate(item)
            for item in state["profiles"]
            if item.get("candidate_id") == candidate_id
        )
        assessment = next(
            CandidateAssessment.model_validate(item)
            for item in state["assessments"]
            if item.get("candidate_id") == candidate_id
        )
        evidence = [
            EvidenceRecord.model_validate(item)
            for item in state["evidence"]
            if item.get("candidate_id") == candidate_id
        ]
        limitations = [
            self._conflict_resolution(evidence, profile.candidate_id, claim)
            for claim in assessment.conflicts
        ]
        limitations.append(
            "Collision/contact observations are not evidence of internal mechanical faults."
        )
        manifest = SourcingManifest(
            run_id=state["run_id"],
            candidate_id=profile.candidate_id,
            name=profile.name,
            canonical_url=profile.canonical_url,
            revision=profile.revision,
            license_id=profile.license_id or "",
            labels=profile.labels,
            sample_rate_hz=profile.sample_rate_hz,
            file_extensions=profile.file_extensions,
            total_size_bytes=profile.total_size_bytes,
            evidence_ids=[item.id for item in evidence],
            limitations=limitations,
            approved_at=datetime.now(UTC),
        )
        return {
            "status": RunStatus.APPROVED.value,
            "manifest": manifest.model_dump(mode="json"),
        }
