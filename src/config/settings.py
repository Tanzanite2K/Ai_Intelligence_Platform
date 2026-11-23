"""Centralised, environment-driven configuration.

Every tunable lives here. Secrets are read from the environment (or a local
``.env``) and are typed as ``SecretStr`` so they never appear in logs or reprs.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- runtime -------------------------------------------------------------
    environment: str = "development"
    log_level: str = "INFO"
    log_json: bool = True
    schema_version: str = "1.0"

    # --- storage -------------------------------------------------------------
    database_url: str = "sqlite+aiosqlite:///./data/platform.db"
    db_pool_size: int = 10
    db_max_overflow: int = 20
    bulk_insert_batch_size: int = 500
    # Phase 7 history layer (observations, versions, snapshots). Nothing reads or writes it yet; it stays off until
    # Phase 7B is approved and verified on a copy.
    history_enabled: bool = False
    redis_url: str | None = None  # None -> in-process dedup (single node only)
    qdrant_url: str | None = None
    neo4j_url: str | None = None
    neo4j_user: str = "neo4j"
    neo4j_password: SecretStr | None = None

    # --- crawling ------------------------------------------------------------
    user_agent: str = "FrontierAtlasBot/1.0 (+https://github.com/; research ingestion; contact: ops@example.com)"
    http_timeout_s: float = 30.0
    http_max_connections: int = 100
    http_max_per_host: int = 8
    default_domain_rps: float = 2.0
    http_max_retries: int = 4
    # A 429/503 asking us to wait longer than this is not retried in-run: the source is put on a
    # cross-run cooldown instead (see BaseCrawler.note_rate_limit), so a run never sleeps for minutes.
    http_max_retry_after_s: float = 60.0
    # In-run retries after an HTTP 429. A source that says "too many requests" twice in a row is not asked again in
    # the same run; the cross-run cooldown takes over (5 attempts per run were observed against VentureBeat).
    http_max_429_retries: int = 1
    respect_robots_txt: bool = True
    circuit_breaker_threshold: int = 5
    circuit_breaker_cooldown_s: float = 60.0
    crawl_target_per_vertical: int = 1000
    freshness_window_hours: int = 24

    # --- source credentials (all optional) ------------------------------------
    github_token: SecretStr | None = None
    producthunt_token: SecretStr | None = None
    hf_token: SecretStr | None = None  # raises Hugging Face Hub API rate limits
    hf_org_cache_path: str | None = "data/hf_org_cache.json"

    # --- LLM -----------------------------------------------------------------
    gemini_api_key: SecretStr | None = None
    # gemini-2.0-flash was shut down on 2026-06-01 (Gemini API deprecations page). gemini-3.1-flash-lite: stable,
    # announced shutdown 2027-05-07. Use scripts/select_gemini_model.py to re-check availability.
    gemini_model: str = "gemini-3.1-flash-lite"
    # 0 = ask "thinking" Flash models not to spend output tokens on hidden reasoning (extraction needs none).
    # If a model rejects the field, the provider drops it automatically. None = never send it.
    gemini_thinking_budget: int | None = 0
    groq_api_key: SecretStr | None = None
    groq_model: str = "llama-3.3-70b-versatile"
    deepseek_api_key: SecretStr | None = None
    deepseek_model: str = "deepseek-chat"
    llm_timeout_s: float = 60.0
    llm_max_retries_per_provider: int = 3
    llm_backoff_base_s: float = 1.0
    llm_backoff_max_s: float = 30.0
    llm_max_concurrency: int = 16
    # Conservative per-request input budgets (tokens). Real windows are larger;
    # the budget also caps request *bytes* so gateways never return 413.
    gemini_input_token_budget: int = 24_000
    groq_input_token_budget: int = 6_000
    deepseek_input_token_budget: int = 24_000
    groq_rpm: int = 30
    gemini_rpm: int = 15
    deepseek_rpm: int = 60

    # --- entity resolution ---------------------------------------------------
    er_fuzzy_accept: float = 92.0
    er_fuzzy_review: float = 80.0
    er_semantic_accept: float = 0.90

    # --- export --------------------------------------------------------------
    google_service_account_file: str | None = None
    google_sheet_id: str | None = None
    google_sheet_title: str = "FrontierAtlas AI Intelligence Export"
    sheets_batch_rows: int = 2000
    export_dir: str = "./data/exports"

    # --- api -----------------------------------------------------------------
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    # Admin operations (crawl trigger, future review decisions) require this key in the X-Admin-Key header.
    # Unset -> admin endpoints are DISABLED (HTTP 503), never open.
    admin_api_key: SecretStr | None = None
    # Comma-separated browser origins allowed by CORS (the Next.js frontend). No wildcard.
    api_cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    # Public read API: requests per minute per client IP (0 disables). In-process limiter (single API process).
    api_rate_limit_per_minute: int = 240
    # Open SQLite read-only (mode=ro) for the API process, so the web tier can never write to the database.
    api_read_only: bool = True

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip().rstrip("/") for o in self.api_cors_origins.split(",") if o.strip() and o.strip() != "*"]

    providers_order: list[str] = Field(default_factory=lambda: ["gemini", "groq", "deepseek"])

    @field_validator(
        "github_token", "producthunt_token", "hf_token", "gemini_api_key", "groq_api_key", "deepseek_api_key", "neo4j_password",
        "redis_url", "qdrant_url", "neo4j_url", "google_service_account_file", "google_sheet_id", "admin_api_key",
        mode="before",
    )
    @classmethod
    def _blank_is_unset(cls, v: object) -> object:
        """``KEY=`` in .env means "not configured", never an empty credential.

        The same holds for placeholder values with no letter or digit (``KEY=-``, ``KEY=#``): no real token, key, URL or
        ID consists only of punctuation, and treating it as configured would send a junk credential to the provider.
        """
        return None if isinstance(v, str) and not any(ch.isalnum() for ch in v) else v

    @field_validator("google_sheet_id", mode="after")
    @classmethod
    def _sheet_id_from_url(cls, v: str | None) -> str | None:
        """Accept either the bare spreadsheet ID or the full docs.google.com URL."""
        if v and "/spreadsheets/d/" in v:
            import re

            m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", v)
            return m.group(1) if m else v
        return v.strip() if v else v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
