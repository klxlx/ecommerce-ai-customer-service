"""OpenAI-compatible chat provider."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    OpenAIError,
    PermissionDeniedError,
    RateLimitError,
    UnprocessableEntityError,
)

from app.core.config import Settings
from app.core.errors import (
    ProviderAuthenticationError,
    ProviderConnectionError,
    ProviderError,
    ProviderInvalidRequestError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from app.core.metrics import MetricsRegistry
from app.core.resilience import (
    ResiliencePolicy,
    execute_with_resilience,
)


class ChatProvider(Protocol):
    async def complete(
        self,
        messages: list[dict],
        model: str | None = None,
    ) -> str: ...

    def stream(
        self,
        messages: list[dict],
        model: str | None = None,
    ) -> AsyncIterator[str]: ...


class OpenAICompatChat:
    """Chat completion against any OpenAI-compatible endpoint."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: AsyncOpenAI | None = None,
        metrics: MetricsRegistry | None = None,
        policy: ResiliencePolicy | None = None,
    ) -> None:
        self._settings = settings
        self._model = settings.chat_model
        self._metrics = metrics
        self._policy = policy or ResiliencePolicy(
            attempt_timeout_seconds=settings.chat_attempt_timeout_seconds,
            total_budget_seconds=settings.chat_total_budget_seconds,
            max_retries=settings.provider_max_retries,
            backoff_seconds=settings.provider_retry_backoff_seconds,
        )
        self._client = (
            client
            if client is not None
            else AsyncOpenAI(
                base_url=settings.chat_base_url,
                api_key=settings.chat_api_key or "not-configured",
                max_retries=0,
            )
        )
        client_key = str(getattr(client, "api_key", "") or "")
        self._configured = bool(settings.chat_api_key) or (
            client is not None and bool(client_key)
        )

    async def complete(
        self,
        messages: list[dict],
        model: str | None = None,
    ) -> str:
        async def operation() -> str:
            if not self._configured:
                raise ProviderAuthenticationError(
                    "chat", "CHAT_API_KEY is not configured"
                )
            try:
                resp = await self._client.chat.completions.create(
                    model=model or self._model,
                    messages=messages,
                    temperature=0.2,
                )
            except OpenAIError as exc:
                raise _map_openai_error(exc) from exc
            if not resp.choices:
                raise ProviderInvalidRequestError(
                    "chat", "provider returned no completion choices"
                )
            return (resp.choices[0].message.content or "").strip()

        return await execute_with_resilience(
            provider="chat",
            operation=operation,
            policy=self._policy,
            metrics=self._metrics,
        )

    async def stream(
        self,
        messages: list[dict],
        model: str | None = None,
    ) -> AsyncIterator[str]:
        """Yield content deltas from the provider's streaming endpoint."""
        if not self._configured:
            raise ProviderAuthenticationError(
                "chat", "CHAT_API_KEY is not configured"
            )
        try:
            stream = await self._client.chat.completions.create(
                model=model or self._model,
                messages=messages,
                temperature=0.2,
                stream=True,
            )
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta.content
                if delta:
                    yield delta
        except OpenAIError as exc:
            raise _map_openai_error(exc) from exc


def _map_openai_error(error: OpenAIError) -> ProviderError:
    """Map SDK errors without exposing provider response bodies."""
    if isinstance(error, APITimeoutError):
        return ProviderTimeoutError("chat", str(error))
    if isinstance(error, (AuthenticationError, PermissionDeniedError)):
        return ProviderAuthenticationError("chat", str(error))
    if isinstance(error, RateLimitError):
        return ProviderRateLimitError("chat", str(error))
    if isinstance(error, (BadRequestError, UnprocessableEntityError)):
        return ProviderInvalidRequestError("chat", str(error))
    if isinstance(error, (InternalServerError, APIConnectionError)):
        return ProviderConnectionError("chat", str(error))
    if isinstance(error, APIStatusError):
        if error.status_code in {401, 403}:
            return ProviderAuthenticationError("chat", str(error))
        if error.status_code == 429:
            return ProviderRateLimitError("chat", str(error))
        if error.status_code >= 500:
            return ProviderConnectionError("chat", str(error))
        return ProviderInvalidRequestError("chat", str(error))
    return ProviderConnectionError("chat", str(error))
