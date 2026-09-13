"""Run the bounded semantic agent, then evaluate after generation."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from dataset_profiler.evidence import DocumentationSource, EvidenceSession
from dataset_profiler.semantic_agent import OpenAIChatClient, OpenAIResponsesClient, SemanticAgentError, generate_dataset_spec
from dataset_profiler.semantic_agent.repair import run_repairs, trace_evidence_ids
from dataset_profiler.semantic_agent.evaluation import compare_specs
from dataset_profiler.semantic_agent.profile_io import read_dataset_profile
from dataset_profiler.semantic_spec import load_kuka_collision_part1_spec, validate_dataset_spec

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--documentation", type=Path, action="append", default=[])
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    parser.add_argument("--model", default="gpt-4.1-mini")
    parser.add_argument("--reasoning-effort")
    parser.add_argument("--responses-api", action="store_true", help="Use official OpenAI Responses API")
    parser.add_argument("--allow-reasoning-fallback", action="store_true")
    parser.add_argument("--request-timeout", type=float, default=120)
    parser.add_argument("--max-output-tokens", type=int, default=8192)
    parser.add_argument("--repair", action="store_true")
    parser.add_argument("--evaluate-kuka-part1", action="store_true")
    args = parser.parse_args()
    profile = read_dataset_profile(args.profile)
    docs = [DocumentationSource(f"doc-{index + 1}", path, path.name) for index, path in enumerate(args.documentation)]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    client = (OpenAIResponsesClient(args.model, reasoning_effort=args.reasoning_effort, allow_reasoning_fallback=args.allow_reasoning_fallback,
                                    timeout=args.request_timeout, max_output_tokens=args.max_output_tokens,
                                    diagnostic_logger=lambda event, fields: print(json.dumps({"event": event, **fields}), flush=True))
              if args.responses_api else OpenAIChatClient(args.model, reasoning_effort=args.reasoning_effort))
    try:
        run = generate_dataset_spec(profile, EvidenceSession(profile, documentation_sources=docs), client)
    except SemanticAgentError as exc:
        if exc.trace:
            (args.output_dir / "kuka_part1_agent_trace.json").write_text(json.dumps(exc.trace, indent=2) + "\n", encoding="utf-8")
        raise
    run.spec.write_json(args.output_dir / "kuka_part1_agent_spec.json")
    run.write_trace(args.output_dir / "kuka_part1_agent_trace.json")
    evidence_ids = trace_evidence_ids(run.trace)
    validation = validate_dataset_spec(profile, run.spec, evidence_ids=evidence_ids)
    (args.output_dir / "kuka_part1_agent_validation.json").write_text(validation.to_json(), encoding="utf-8")
    if args.repair:
        run.spec.write_json(args.output_dir / "initial_spec.json")
        (args.output_dir / "initial_validation.json").write_text(validation.to_json(), encoding="utf-8")
        rounds, final_spec, final_validation, selection = run_repairs(
            profile, docs, run.spec, client, evidence_ids=evidence_ids
        )
        summary = []
        for item in rounds:
            prefix = args.output_dir / f"repair_round_{item['round']}"
            item['spec'].write_json(str(prefix) + "_spec.json")
            Path(str(prefix) + "_trace.json").write_text(json.dumps(item['trace'], indent=2) + "\n", encoding="utf-8")
            Path(str(prefix) + "_validation.json").write_text(item['validation'].to_json(), encoding="utf-8")
            summary.append({"round": item['round'], "input_issue_count": item['input_issue_count'], "output_issue_count": len(item['validation'].errors), "grouped_issues": item['grouped_issues'], "candidate_score": item['candidate_score'], "accepted_as_best": item['accepted_as_best'], "selection_reason": item['selection_reason'], "best_round_after": item['best_round_after'], "best_score_after": item['best_score_after']})
        final_spec.write_json(args.output_dir / "final_spec.json")
        (args.output_dir / "final_validation.json").write_text(final_validation.to_json(), encoding="utf-8")
        (args.output_dir / "repair_summary.json").write_text(
            json.dumps({"selection": selection, "rounds": summary}, indent=2) + "\n",
            encoding="utf-8",
        )
        if args.evaluate_kuka_part1:
            report = compare_specs(final_spec, load_kuka_collision_part1_spec())
            (args.output_dir / "final_evaluation.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.evaluate_kuka_part1:  # intentionally only after generation has returned
        report = compare_specs(run.spec, load_kuka_collision_part1_spec())
        (args.output_dir / "kuka_part1_agent_evaluation.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
