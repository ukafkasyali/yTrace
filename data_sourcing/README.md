# Evidence-complete dataset scout

This service turns a natural-language dataset brief into an auditable shortlist. It uses
LangGraph for durable orchestration, Tavily for bounded discovery, and source-native APIs for
verification. Deterministic gates and scoring remain separate from model-generated planning.

The service never starts ingestion. An approved sourcing run produces a manifest for the
ingestion team.

## Development

```bash
cd data_sourcing
uv sync --all-groups
uv run pytest
uv run uvicorn data_sourcing.api:create_app --factory --reload
```

Copy `.env.example` to `.env` if live Tavily or LLM planning is required. Without those keys,
the bundled robot-collision fixture is used only when the brief explicitly contains
`github.com/zhang-zengjie/robot-raw-collision-signals`. Other unconfigured searches abstain with
`NEEDS_INPUT` instead of returning sample data as live evidence.

## Demo request

```bash
curl -i http://127.0.0.1:8000/api/sourcing-runs \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: robot-demo-1' \
  -d '{"brief":"Find 1 kHz robot collision and intentional contact time-series torque data from https://github.com/zhang-zengjie/robot-raw-collision-signals"}'
```

Poll the returned `statusUrl`. An evidence-complete run pauses at `AWAITING_APPROVAL`; approve the
recommendation or another assessed candidate that passes every mandatory gate:

```bash
curl -X POST http://127.0.0.1:8000/api/sourcing-runs/RUN_ID/approvals \
  -H 'Content-Type: application/json' \
  -d '{"decision":"APPROVE","candidateId":"CANDIDATE_ID"}'
```

Reject with specific feedback to run another bounded search in the same durable thread:

```bash
curl -X POST http://127.0.0.1:8000/api/sourcing-runs/RUN_ID/approvals \
  -H 'Content-Type: application/json' \
  -d '{"decision":"REJECT","candidateId":"CANDIDATE_ID","note":"Find an alternative"}'
```

The reviewer can request at most two refinements, and all cycles share the original Tavily-credit
and active-research-time budgets. Each resumed run returns a structured `refinementOutcomes`
entry showing the executed query, evidence delta and whether the deterministic recommendation
changed. The rejected candidate is retained in the evidence and ranking audit, but is excluded
from subsequent recommendation and approval choices. If no eligible replacement is found, the
agent withholds its recommendation instead of selecting the rejected candidate again. Feedback
guides discovery but does not rewrite hard gates or score weights.

Runtime artifacts are written under `var/runs/<run-id>/`. SQLite checkpoints and idempotency keys
remain under `var/`. Set `SOURCING_DATA_DIR` to relocate all runtime state.

The complete wire contract and state semantics are in
[`docs/DATA_SOURCING_API.md`](../docs/DATA_SOURCING_API.md). This module deliberately ends at the
approved manifest; downloading the dataset and integrating TimeNet are separate concerns.

Framework patterns follow the official LangGraph documentation for
[stateful orchestration](https://docs.langchain.com/oss/python/langgraph/overview),
[persistence](https://docs.langchain.com/oss/python/langgraph/persistence), and
[human approval interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts).
Adapter request shapes follow the official
[Tavily Search API](https://docs.tavily.com/documentation/api-reference/endpoint/search),
[GitHub repositories API](https://docs.github.com/en/rest/repos/repos),
[Zenodo REST API](https://developers.zenodo.org/#records), and
[Hugging Face Hub API](https://huggingface.co/docs/hub/api) documentation. Optional model-assisted
planning uses OpenAI
[Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs) through the
direct client; no prebuilt agent is used.
