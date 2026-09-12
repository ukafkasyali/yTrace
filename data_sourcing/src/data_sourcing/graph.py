from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any, TypedDict

import httpx
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from data_sourcing.adapters import (
    NativeVerifier,
    TavilySearchAdapter,
    candidate_from_source,
    canonicalize_results,
)
from data_sourcing.adapters.discovery import SearchAdapter, SourceUnavailable
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
    RefinementOutcome,
    RefinementOutcomeStatus,
    RequirementCategory,
    ResearchRequirement,
    RunStatus,
    SearchHypothesis,
    SearchResult,
    SourcingConstraints,
    SourcingManifest,
    VerificationStatus,
)
from data_sourcing.planning import (
    PlanningDraft,
    RequirementPlanner,
    make_gap_hypothesis,
    make_review_hypothesis,
)
from data_sourcing.relevance import EvidenceRelevanceJudge
from data_sourcing.scoring import (
    apply_recommendation_confidence,
    assess_candidate,
    candidate_is_approvable,
    candidate_rank_key,
    requirement_claim_key,
    requirement_is_evidenced,
)

_REVIEW_ITERATION_LIMIT = 2


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
    approved_candidate_id: str | None
    excluded_candidate_ids: list[str]
    review_rejected_candidate_id: str | None
    gap_queries_used: int
    tavily_credits_used: int
    execution_mode: str
    errors: list[str]
    report_markdown: str
    manifest: dict[str, Any] | None
    started_at: float
    allow_cached_demo: bool
    approval_decision: str | None
    review_feedback: list[str]
    review_iterations_used: int
    refinement_outcomes: list[dict[str, Any]]
    pending_refinement: dict[str, Any] | None
    active_research_seconds: float
    planning_draft: dict[str, Any]


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
        "approved_candidate_id": None,
        "excluded_candidate_ids": [],
        "review_rejected_candidate_id": None,
        "gap_queries_used": 0,
        "tavily_credits_used": 0,
        "execution_mode": ExecutionMode.LIVE.value,
        "errors": [],
        "report_markdown": "",
        "manifest": None,
        "started_at": time.time(),
        "allow_cached_demo": allow_cached_demo,
        "approval_decision": None,
        "review_feedback": [],
        "review_iterations_used": 0,
        "refinement_outcomes": [],
        "pending_refinement": None,
        "active_research_seconds": 0.0,
        "planning_draft": {},
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
        search: SearchAdapter | None = None,
        verifier: NativeVerifier | None = None,
    ):
        self.settings = settings
        self.planner = RequirementPlanner(settings)
        self.relevance_judge = EvidenceRelevanceJudge(settings)
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
        builder.add_node("review_refinement", self.review_refinement)
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
            {
                "manifest": "manifest_generation",
                "refinement": "review_refinement",
                "end": END,
            },
        )
        builder.add_conditional_edges(
            "review_refinement",
            self.route_after_review_refinement,
            {"continue": "verification", "end": END},
        )
        builder.add_edge("manifest_generation", END)
        return builder

    def _expired(self, state: SourcingState) -> bool:
        active_seconds = state.get("active_research_seconds", 0.0)
        return (
            active_seconds + time.time() - state["started_at"]
            >= self.settings.run_timeout_seconds
        )

    def requirements(self, state: SourcingState) -> dict[str, Any]:
        request = _request(state)
        draft = self.planner.draft(request)
        requirements = self.planner.requirements_from_draft(request, draft)
        return {
            "status": RunStatus.PLANNING.value,
            "requirements": _json_list(requirements),
            "planning_draft": draft.model_dump(mode="json"),
        }

    def hypotheses(self, state: SourcingState) -> dict[str, Any]:
        draft = PlanningDraft.model_validate(state["planning_draft"])
        hypotheses = self.planner.hypotheses_from_draft(draft)[: self.settings.initial_query_limit]
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
            had_results = bool(results)
            results.extend(batch.results)
            hypothesis.status = HypothesisStatus.SEARCHED
            if batch.execution_mode is ExecutionMode.CACHED:
                mode = (
                    ExecutionMode.PARTIAL
                    if had_results and mode is ExecutionMode.LIVE
                    else ExecutionMode.CACHED
                )
            elif mode is ExecutionMode.CACHED:
                mode = ExecutionMode.PARTIAL
            errors.extend(batch.warnings)
        all_hypotheses = [SearchHypothesis.model_validate(item) for item in state["hypotheses"]]
        by_id = {item.id: item for item in all_hypotheses}
        by_id.update({item.id: item for item in hypotheses})
        return {
            "status": RunStatus.DISCOVERING.value,
            "hypotheses": _json_list(list(by_id.values())),
            "search_results": _json_list(results),
            "tavily_credits_used": credits,
            "execution_mode": mode.value,
            "errors": list(dict.fromkeys(errors)),
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
        discovered = canonicalize_results(results, self.settings.candidate_limit)
        existing = [DatasetCandidate.model_validate(item) for item in state["candidates"]]
        candidates: list[DatasetCandidate] = []
        seen: set[str] = set()
        for candidate in [*discovered, *existing]:
            if candidate.id in seen:
                continue
            seen.add(candidate.id)
            candidates.append(candidate)
            if len(candidates) == self.settings.source_inspection_limit:
                break
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
        requirements = [ResearchRequirement.model_validate(item) for item in state["requirements"]]
        required_domain_terms = [
            value
            for requirement in requirements
            if requirement.category is RequirementCategory.DOMAIN
            for value in requirement.expected_values
        ]
        seen_candidate_ids = {candidate.id for candidate in candidates}
        candidate_index = 0
        inspections = 0
        while (
            candidate_index < len(candidates)
            and inspections < self.settings.source_inspection_limit
        ):
            candidate = candidates[candidate_index]
            candidate_index += 1
            if candidate.id in known_profiles or self._expired(state):
                continue
            inspections += 1
            try:
                verified = self.verifier.verify(
                    candidate,
                    cached=state["execution_mode"] == ExecutionMode.CACHED.value,
                    max_download_bytes=constraints.max_download_bytes,
                )
            except (SourceUnavailable, ValueError, OSError, httpx.HTTPError) as exc:
                errors.append(f"Candidate {candidate.id} verification failed: {exc}")
                continue
            relevance = self.relevance_judge.evaluate(
                candidate.id,
                verified.documents,
                required_domain_terms,
            )
            known_profiles[candidate.id] = verified.profile.model_copy(
                update={"domains": relevance.matched_terms}
            )
            evidence.extend([*verified.evidence, *relevance.evidence])
            errors.extend(relevance.warnings)
            if (
                verified.documents
                and candidate.discovery_depth < self.settings.traversal_depth_limit
            ):
                primary_document = verified.documents[0]
                for related_url in primary_document.related_urls:
                    child = candidate_from_source(
                        related_url,
                        name=f"Source linked from {primary_document.name}",
                        discovery_depth=candidate.discovery_depth + 1,
                        discovered_from_candidate_id=candidate.id,
                    )
                    if child is None or child.id in seen_candidate_ids:
                        continue
                    seen_candidate_ids.add(child.id)
                    candidates.append(child)
                    if len(candidates) == self.settings.source_inspection_limit:
                        break
        deduplicated_evidence = {item.id: item for item in evidence}
        return {
            "status": RunStatus.VERIFYING.value,
            "profiles": _json_list(list(known_profiles.values())),
            "evidence": _json_list(list(deduplicated_evidence.values())),
            "candidates": _json_list(candidates),
            "errors": list(dict.fromkeys(errors)),
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
            claim = requirement_claim_key(requirement)
            requirement.evidence_ids = [
                item.id
                for item in evidence
                if item.candidate_id in candidate_ids and item.claim_key == claim
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
        active_candidate_ids = {item["id"] for item in state["candidates"]}
        profiles = [
            DatasetProfile.model_validate(item)
            for item in state["profiles"]
            if item["candidate_id"] in active_candidate_ids
        ]
        requirements = [ResearchRequirement.model_validate(item) for item in state["requirements"]]
        evidence = [EvidenceRecord.model_validate(item) for item in state["evidence"]]
        excluded_candidate_ids = set(state.get("excluded_candidate_ids", []))
        raw_assessments = [
            assess_candidate(profile, requirements, evidence) for profile in profiles
        ]
        active_assessments = apply_recommendation_confidence(
            [
                item
                for item in raw_assessments
                if item.candidate_id not in excluded_candidate_ids
            ]
        )
        assessments = [
            *active_assessments,
            *[
                item
                for item in raw_assessments
                if item.candidate_id in excluded_candidate_ids
            ],
        ]
        ranked = sorted(assessments, key=candidate_rank_key)
        missing_mandatory = [
            item.id
            for item in requirements
            if item.priority.value == "MUST" and item.status is not VerificationStatus.VERIFIED
        ]
        recommended = next(
            (
                item
                for item in ranked
                if item.candidate_id not in excluded_candidate_ids
                and item.tier is CandidateTier.RECOMMEND
            ),
            None,
        )
        approvable = [
            item
            for item in ranked
            if item.candidate_id not in excluded_candidate_ids
            and candidate_is_approvable(item)
        ]
        recommended_candidate_id = (
            recommended.candidate_id if recommended and not missing_mandatory else None
        )
        refinement_outcomes = list(state.get("refinement_outcomes", []))
        pending = state.get("pending_refinement")
        if pending:
            new_evidence_ids = sorted(
                {item.id for item in evidence} - set(pending["previous_evidence_ids"])
            )
            new_candidate_ids = pending["new_candidate_ids"]
            if (
                pending["rejected_candidate_id"]
                == pending["previous_recommended_candidate_id"]
                and recommended_candidate_id is None
            ):
                outcome_status = RefinementOutcomeStatus.RECOMMENDATION_WITHHELD
            elif pending["previous_recommended_candidate_id"] != recommended_candidate_id:
                outcome_status = RefinementOutcomeStatus.RECOMMENDATION_CHANGED
            elif new_evidence_ids:
                outcome_status = RefinementOutcomeStatus.EVIDENCE_EXPANDED
            elif new_candidate_ids:
                outcome_status = RefinementOutcomeStatus.CANDIDATES_ADDED
            else:
                outcome_status = RefinementOutcomeStatus.NO_CHANGE
            outcome = RefinementOutcome(
                iteration=pending["iteration"],
                feedback=pending["feedback"],
                query=pending["query"],
                outcome=outcome_status,
                rejected_candidate_id=pending["rejected_candidate_id"],
                previous_recommended_candidate_id=pending[
                    "previous_recommended_candidate_id"
                ],
                recommended_candidate_id=recommended_candidate_id,
                new_candidate_ids=new_candidate_ids,
                new_evidence_ids=new_evidence_ids,
            )
            refinement_outcomes.append(outcome.model_dump(mode="json"))
        can_refine_again = bool(pending) and (
            state.get("review_iterations_used", 0) < _REVIEW_ITERATION_LIMIT
            and state["tavily_credits_used"] + 2 <= self.settings.tavily_credit_limit
            and not self._expired(state)
        )
        can_review = not missing_mandatory and (bool(approvable) or can_refine_again)
        status = RunStatus.AWAITING_APPROVAL if can_review else RunStatus.NEEDS_INPUT
        report = self._report(state, ranked, missing_mandatory)
        return {
            "status": status.value,
            "assessments": _json_list(ranked),
            "recommended_candidate_id": recommended_candidate_id,
            "refinement_outcomes": refinement_outcomes,
            "pending_refinement": None,
            "review_rejected_candidate_id": None,
            "report_markdown": report,
            "active_research_seconds": min(
                self.settings.run_timeout_seconds,
                state.get("active_research_seconds", 0.0)
                + time.time()
                - state["started_at"],
            ),
            "started_at": time.time(),
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
        excluded_candidate_ids = set(state.get("excluded_candidate_ids", []))
        for assessment in ranked:
            conflict = ", ".join(assessment.conflicts) or "none"
            review_status = (
                "; excluded by reviewer"
                if assessment.candidate_id in excluded_candidate_ids
                else ""
            )
            lines.append(
                f"- `{assessment.candidate_id}` — {assessment.total_score}/100, "
                f"{assessment.tier.value}; conflicts: {conflict}{review_status}"
            )
            for claim in assessment.conflicts:
                lines.append(
                    f"  - {self._conflict_resolution(evidence, assessment.candidate_id, claim)}"
                )
        if state["errors"]:
            lines.extend(["", "## Retrieval notes", "", *[f"- {item}" for item in state["errors"]]])
        if state.get("review_feedback"):
            lines.extend(
                [
                    "",
                    "## Reviewer refinements",
                    "",
                    *[f"- {item}" for item in state["review_feedback"]],
                ]
            )
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
            f"{claim}: {observations}. Resolution uses {records[0].observed_value} from "
            f"{records[0].source_kind.value}; all observations remain in evidence.jsonl."
        )

    @staticmethod
    def route_after_scoring(state: SourcingState) -> str:
        return "approval" if state["status"] == RunStatus.AWAITING_APPROVAL.value else "end"

    def approval(self, state: SourcingState) -> dict[str, Any]:
        excluded_candidate_ids = set(state.get("excluded_candidate_ids", []))
        payload = interrupt(
            {
                "runId": state["run_id"],
                "recommendedCandidateId": state["recommended_candidate_id"],
                "eligibleCandidateIds": [
                    assessment.candidate_id
                    for assessment in (
                        CandidateAssessment.model_validate(item)
                        for item in state["assessments"]
                    )
                    if candidate_is_approvable(assessment)
                    and assessment.candidate_id not in excluded_candidate_ids
                ],
                "message": "Approve the evidence-backed dataset manifest?",
            }
        )
        approval = ApprovalRequest.model_validate(payload)
        assessments = [
            CandidateAssessment.model_validate(item) for item in state["assessments"]
        ]
        selected = next(
            (item for item in assessments if item.candidate_id == approval.candidate_id),
            None,
        )
        if approval.decision is ApprovalDecision.APPROVE and (
            selected is None
            or not candidate_is_approvable(selected)
            or selected.candidate_id in excluded_candidate_ids
        ):
            return {
                "status": RunStatus.NEEDS_INPUT.value,
                "approval_decision": ApprovalDecision.REJECT.value,
                "errors": [
                    *state["errors"],
                    "Approval candidate did not pass every mandatory gate",
                ],
            }
        if approval.decision is ApprovalDecision.REJECT:
            if state.get("review_iterations_used", 0) >= _REVIEW_ITERATION_LIMIT:
                return {
                    "status": RunStatus.NEEDS_INPUT.value,
                    "approval_decision": approval.decision.value,
                    "errors": [
                        *state["errors"],
                        "Reviewer refinement limit reached; start a new run to continue",
                    ],
                }
            rejected_candidate_id = (
                approval.candidate_id or state.get("recommended_candidate_id")
            )
            if rejected_candidate_id:
                excluded_candidate_ids.add(rejected_candidate_id)
            return {
                "status": RunStatus.DISCOVERING.value,
                "approval_decision": approval.decision.value,
                "review_feedback": [*state.get("review_feedback", []), approval.note],
                "excluded_candidate_ids": sorted(excluded_candidate_ids),
                "review_rejected_candidate_id": rejected_candidate_id,
                "started_at": time.time(),
            }
        return {
            "status": RunStatus.APPROVED.value,
            "approval_decision": approval.decision.value,
            "approved_candidate_id": approval.candidate_id,
        }

    @staticmethod
    def route_after_approval(state: SourcingState) -> str:
        if state["status"] == RunStatus.NEEDS_INPUT.value:
            return "end"
        return (
            "manifest"
            if state.get("approval_decision") == ApprovalDecision.APPROVE.value
            else "refinement"
        )

    def review_refinement(self, state: SourcingState) -> dict[str, Any]:
        iteration = state.get("review_iterations_used", 0) + 1
        errors = list(state["errors"])
        if iteration > _REVIEW_ITERATION_LIMIT:
            errors.append("Reviewer refinement limit reached; start a new run to continue")
            return {"status": RunStatus.NEEDS_INPUT.value, "errors": errors}
        if (
            self._expired(state)
            or state["tavily_credits_used"] + 2 > self.settings.tavily_credit_limit
        ):
            errors.append(
                "Reviewer refinement could not run within the remaining time or credit budget"
            )
            return {"status": RunStatus.NEEDS_INPUT.value, "errors": errors}
        hypothesis = make_review_hypothesis(
            iteration,
            state["review_feedback"][-1],
            state["brief"],
        )
        hypotheses = [SearchHypothesis.model_validate(item) for item in state["hypotheses"]]
        hypotheses.append(hypothesis)
        temporary_state = dict(state)
        temporary_state["hypotheses"] = _json_list(hypotheses)
        previous_result_count = len(state["search_results"])
        update = self._perform_searches(temporary_state, [hypothesis])
        review_results = [
            SearchResult.model_validate(item)
            for item in update["search_results"][previous_result_count:]
        ]
        discovered = canonicalize_results(review_results, self.settings.candidate_limit)
        existing = [DatasetCandidate.model_validate(item) for item in state["candidates"]]
        verified_ids = {item["candidate_id"] for item in state["profiles"]}
        merged: list[DatasetCandidate] = []
        seen: set[str] = set()
        for candidate in [
            *[item for item in existing if item.id in verified_ids],
            *discovered,
            *existing,
        ]:
            if candidate.id not in seen:
                seen.add(candidate.id)
                merged.append(candidate)
            if len(merged) == self.settings.candidate_limit:
                break
        previous_candidate_ids = {item.id for item in existing}
        new_candidate_ids = [
            item.id for item in discovered if item.id not in previous_candidate_ids
        ]
        update["candidates"] = _json_list(merged)
        update["review_iterations_used"] = iteration
        update["pending_refinement"] = {
            "iteration": iteration,
            "feedback": state["review_feedback"][-1],
            "query": hypothesis.query,
            "rejected_candidate_id": state.get("review_rejected_candidate_id"),
            "previous_recommended_candidate_id": state.get("recommended_candidate_id"),
            "previous_evidence_ids": [item["id"] for item in state["evidence"]],
            "new_candidate_ids": new_candidate_ids,
        }
        return update

    @staticmethod
    def route_after_review_refinement(state: SourcingState) -> str:
        return "end" if state["status"] == RunStatus.NEEDS_INPUT.value else "continue"

    def manifest_generation(self, state: SourcingState) -> dict[str, Any]:
        candidate_id = state["approved_candidate_id"]
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
