"""Application configuration loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for the customer service backend."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    database_url: str = Field(
        default="postgresql+psycopg://cs_user:cs_password@localhost:5432/cs_db",
        validation_alias="DATABASE_URL",
    )

    # Chat provider (OpenAI-compatible)
    chat_model: str = Field(default="qwen-plus", validation_alias="CHAT_MODEL")
    router_model: str = Field(
        default="qwen-plus", validation_alias="ROUTER_MODEL"
    )
    knowledge_model: str = Field(
        default="qwen-max", validation_alias="KNOWLEDGE_MODEL"
    )
    chitchat_model: str = Field(
        default="qwen-flash", validation_alias="CHITCHAT_MODEL"
    )
    chat_base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        validation_alias="CHAT_BASE_URL",
    )
    chat_api_key: str = Field(default="", validation_alias="CHAT_API_KEY")

    # Read-only weather MCP for the chitchat agent.
    weather_mcp_enabled: bool = Field(
        default=True, validation_alias="WEATHER_MCP_ENABLED"
    )
    weather_mcp_url: str = Field(
        default="http://127.0.0.1:8000/mcp/weather",
        validation_alias="WEATHER_MCP_URL",
    )
    weather_mcp_tool: str = Field(
        default="get_weather", validation_alias="WEATHER_MCP_TOOL"
    )
    weather_mcp_timeout_seconds: float = Field(
        default=8.0, validation_alias="WEATHER_MCP_TIMEOUT_SECONDS"
    )
    weather_mcp_auth_token: str = Field(
        default="", validation_alias="WEATHER_MCP_AUTH_TOKEN"
    )

    # DashScope native embedding / rerank
    dashscope_api_key: str = Field(default="", validation_alias="DASHSCOPE_API_KEY")
    embedding_model: str = Field(
        default="text-embedding-v3", validation_alias="EMBEDDING_MODEL"
    )
    embedding_dimension: int = Field(
        default=1024, validation_alias="EMBEDDING_DIMENSION"
    )
    rerank_model: str = Field(default="text-rerank-v1", validation_alias="RERANK_MODEL")

    # Handoff ticket policy
    handoff_dedupe_similarity: float = Field(
        default=0.93, validation_alias="HANDOFF_DEDUPE_SIMILARITY"
    )
    handoff_max_unresolved: int = Field(
        default=2, validation_alias="HANDOFF_MAX_UNRESOLVED"
    )

    # Auth
    session_ttl_hours: int = Field(default=24, validation_alias="SESSION_TTL_HOURS")
    login_failure_limit: int = Field(
        default=5, validation_alias="LOGIN_FAILURE_LIMIT"
    )
    login_failure_window_seconds: int = Field(
        default=300, validation_alias="LOGIN_FAILURE_WINDOW_SECONDS"
    )
    login_lock_seconds: int = Field(
        default=900, validation_alias="LOGIN_LOCK_SECONDS"
    )

    # API rate limits
    chat_rate_limit_requests: int = Field(
        default=20, validation_alias="CHAT_RATE_LIMIT_REQUESTS"
    )
    chat_rate_limit_window_seconds: int = Field(
        default=60, validation_alias="CHAT_RATE_LIMIT_WINDOW_SECONDS"
    )
    admin_rate_limit_requests: int = Field(
        default=120, validation_alias="ADMIN_RATE_LIMIT_REQUESTS"
    )
    admin_rate_limit_window_seconds: int = Field(
        default=60, validation_alias="ADMIN_RATE_LIMIT_WINDOW_SECONDS"
    )

    # Provider resilience
    provider_max_retries: int = Field(
        default=2, validation_alias="PROVIDER_MAX_RETRIES"
    )
    provider_retry_backoff_seconds: float = Field(
        default=0.5, validation_alias="PROVIDER_RETRY_BACKOFF_SECONDS"
    )
    chat_attempt_timeout_seconds: float = Field(
        default=30.0, validation_alias="CHAT_ATTEMPT_TIMEOUT_SECONDS"
    )
    chat_total_budget_seconds: float = Field(
        default=60.0, validation_alias="CHAT_TOTAL_BUDGET_SECONDS"
    )
    embedding_attempt_timeout_seconds: float = Field(
        default=20.0, validation_alias="EMBEDDING_ATTEMPT_TIMEOUT_SECONDS"
    )
    embedding_total_budget_seconds: float = Field(
        default=60.0, validation_alias="EMBEDDING_TOTAL_BUDGET_SECONDS"
    )
    rerank_attempt_timeout_seconds: float = Field(
        default=15.0, validation_alias="RERANK_ATTEMPT_TIMEOUT_SECONDS"
    )
    rerank_total_budget_seconds: float = Field(
        default=30.0, validation_alias="RERANK_TOTAL_BUDGET_SECONDS"
    )

    # App
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    mock_providers: bool = Field(default=False, validation_alias="MOCK_PROVIDERS")

    @property
    def chat_configured(self) -> bool:
        """Whether a chat API key has been supplied."""
        return bool(self.chat_api_key)

    @property
    def embedding_configured(self) -> bool:
        """Whether an embedding API key has been supplied."""
        return bool(self.dashscope_api_key)


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
