from __future__ import annotations

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SOURCING_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    data_dir: Path = Path("var")
    tavily_api_key: SecretStr | None = None
    tavily_base_url: str = "https://api.tavily.com"
    github_token: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    openai_base_url: str | None = None
    openai_model: str | None = None

    initial_query_limit: int = Field(default=3, ge=1, le=3)
    gap_query_limit: int = Field(default=2, ge=0, le=2)
    candidate_limit: int = Field(default=8, ge=1, le=8)
    source_inspection_limit: int = Field(default=16, ge=1, le=24)
    traversal_depth_limit: int = Field(default=2, ge=0, le=2)
    tavily_credit_limit: int = Field(default=12, ge=1, le=12)
    run_timeout_seconds: int = Field(default=90, ge=10, le=90)
    max_source_response_bytes: int = Field(default=2_000_000, ge=10_000, le=10_000_000)
    request_timeout_seconds: float = Field(default=15, gt=0, le=30)

    @property
    def runs_dir(self) -> Path:
        return self.data_dir / "runs"

    @property
    def checkpoint_path(self) -> Path:
        return self.data_dir / "checkpoints.sqlite3"

    @property
    def idempotency_path(self) -> Path:
        return self.data_dir / "idempotency.sqlite3"
