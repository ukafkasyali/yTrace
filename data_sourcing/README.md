# Evidence-complete dataset scout

This service turns a natural-language dataset brief into an auditable shortlist. It uses
LangGraph for durable orchestration, Tavily for bounded discovery, and source-native APIs for
verification. Deterministic gates and scoring remain separate from model-generated planning.

The service never starts ingestion. An approved sourcing run produces a manifest for the
ingestion team.

## Development

```bash
uv sync --all-groups
uv run pytest
uv run uvicorn data_sourcing.api:app --reload
```

Copy `.env.example` to `.env` if live Tavily or LLM planning is required. Without those keys,
the exact bundled robot-collision demo brief uses cached discovery and deterministic planning.

Framework patterns follow the official LangGraph documentation for
[stateful orchestration](https://docs.langchain.com/oss/python/langgraph/overview),
[persistence](https://docs.langchain.com/oss/python/langgraph/persistence), and
[human approval interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts).
