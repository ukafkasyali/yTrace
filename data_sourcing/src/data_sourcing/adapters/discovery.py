from __future__ import annotations

import json
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, Field

from data_sourcing.config import Settings
from data_sourcing.models import ExecutionMode, SearchResult


class SourceUnavailable(RuntimeError):
    pass


class SearchBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    results: list[SearchResult]
    credits_used: int = Field(ge=0)
    execution_mode: ExecutionMode


class _TavilyResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str
    url: str
    content: str = ""
    score: float = Field(default=0, ge=0, le=1)


class _TavilyResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    results: list[_TavilyResult]
    usage: dict[str, int] = Field(default_factory=dict)


class TavilySearchAdapter:
    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self.settings = settings
        self.client = client or httpx.Client(
            timeout=settings.request_timeout_seconds,
            follow_redirects=False,
        )
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def search(self, query: str, *, allow_cached_demo: bool = False) -> SearchBatch:
        if not self.settings.tavily_api_key:
            if (
                allow_cached_demo
                and "robot" in query.casefold()
                and "collision" in query.casefold()
            ):
                return self._cached_batch(query)
            raise SourceUnavailable("Tavily is not configured and no exact demo cache applies")
        if self.settings.tavily_base_url.rstrip("/") != "https://api.tavily.com":
            raise SourceUnavailable("Tavily base URL must be https://api.tavily.com")
        try:
            response = self.client.post(
                "https://api.tavily.com/search",
                headers={
                    "Authorization": f"Bearer {self.settings.tavily_api_key.get_secret_value()}"
                },
                json={
                    "query": query,
                    "search_depth": "advanced",
                    "max_results": 8,
                    "include_usage": True,
                    "include_domains": ["github.com", "zenodo.org", "huggingface.co"],
                    "safe_search": True,
                },
            )
            response.raise_for_status()
        except httpx.HTTPError:
            if allow_cached_demo:
                return self._cached_batch(query)
            raise
        if len(response.content) > self.settings.max_source_response_bytes:
            raise SourceUnavailable("Tavily response exceeded the configured size limit")
        payload = _TavilyResponse.model_validate(response.json())
        credits = payload.usage.get("credits", 2)
        return SearchBatch(
            results=[
                SearchResult(
                    title=item.title,
                    url=item.url,
                    content=item.content,
                    score=item.score,
                    query=query,
                )
                for item in payload.results
            ],
            credits_used=credits,
            execution_mode=ExecutionMode.LIVE,
        )

    @staticmethod
    def _cached_batch(query: str) -> SearchBatch:
        fixture_path = Path(__file__).parent.parent / "fixtures" / "robot_collision_search.json"
        records = json.loads(fixture_path.read_text(encoding="utf-8"))
        return SearchBatch(
            results=[SearchResult.model_validate(record | {"query": query}) for record in records],
            credits_used=0,
            execution_mode=ExecutionMode.CACHED,
        )
