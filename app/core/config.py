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
        default="text-embedding-v4", validation_alias="EMBEDDING_MODEL"
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

    # SSE and gateway keep-alive
    sse_heartbeat_seconds: float = Field(
        default=15.0, validation_alias="SSE_HEARTBEAT_SECONDS"
    )

    # Asynchronous answer sentinel. An empty model falls back to ROUTER_MODEL,
    # so the guard uses the same model tier and API key as intent routing.
    sentinel_enabled: bool = Field(
        default=True, validation_alias="SENTINEL_ENABLED"
    )
    sentinel_model: str = Field(default="", validation_alias="SENTINEL_MODEL")
    sentinel_timeout_seconds: float = Field(
        default=8.0, validation_alias="SENTINEL_TIMEOUT_SECONDS"
    )
    sentinel_max_context_chars: int = Field(
        default=6000, validation_alias="SENTINEL_MAX_CONTEXT_CHARS"
    )

    # Photo return intake. ``vision_provider`` is intentionally pluggable.
    vision_provider: str = Field(
        default="yolo", validation_alias="VISION_PROVIDER"
    )
    yolo_model_path: str = Field(
        default="models/yolov8n.onnx", validation_alias="YOLO_MODEL_PATH"
    )
    yolo_confidence_threshold: float = Field(
        default=0.35, validation_alias="YOLO_CONFIDENCE_THRESHOLD"
    )
    yolo_iou_threshold: float = Field(
        default=0.45, validation_alias="YOLO_IOU_THRESHOLD"
    )
    yolo_input_size: int = Field(
        default=640, validation_alias="YOLO_INPUT_SIZE"
    )
    return_image_dir: str = Field(
        default="data/return_images", validation_alias="RETURN_IMAGE_DIR"
    )
    return_image_max_mb: int = Field(
        default=10, validation_alias="RETURN_IMAGE_MAX_MB"
    )

    # Semantic FAQ cache (Postgres + pgvector; seed data only).
    semantic_cache_enabled: bool = Field(
        default=True, validation_alias="SEMANTIC_CACHE_ENABLED"
    )
    semantic_cache_similarity: float = Field(
        default=0.95, validation_alias="SEMANTIC_CACHE_SIMILARITY"
    )
    semantic_cache_max_results: int = Field(
        default=3, validation_alias="SEMANTIC_CACHE_MAX_RESULTS"
    )
    semantic_cache_embedding_model: str = Field(
        default="", validation_alias="SEMANTIC_CACHE_EMBEDDING_MODEL"
    )

    @property
    def semantic_cache_embedding_model_name(self) -> str:
        return self.semantic_cache_embedding_model or self.embedding_model

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
    # P0 stream resilience. Both flags default on; disable per environment if a
    # provider's stream semantics turn out to be incompatible with retries.
    stream_retry_enabled: bool = Field(
        default=True, validation_alias="STREAM_RETRY_ENABLED"
    )
    stream_partial_fallback_enabled: bool = Field(
        default=True, validation_alias="STREAM_PARTIAL_FALLBACK_ENABLED"
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

    @property
    def sentinel_model_name(self) -> str:
        """The sentinel uses the router tier unless explicitly overridden."""
        return self.sentinel_model or self.router_model


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
