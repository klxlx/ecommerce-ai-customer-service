"""Provider failures mapped to stable, retry-aware domain errors."""

from __future__ import annotations

import httpx


class ProviderError(Exception):
    """Base class for provider failures safe to classify across the app."""

    category = "provider"
    retryable = False

    def __init__(self, provider: str, reason: str = "") -> None:
        self.provider = provider
        self.reason = reason
        # A single final failure may cross provider and chat-service boundaries.
        # The first metrics owner marks this flag to avoid double counting.
        self.degraded_recorded = False
        super().__init__(f"{provider}:{self.category}")

    def as_evidence(self) -> dict:
        return {
            "provider": self.provider,
            "category": self.category,
            "retryable": self.retryable,
        }


class ProviderAuthenticationError(ProviderError):
    category = "authentication"
    retryable = False


class ProviderConnectionError(ProviderError):
    category = "connection"
    retryable = True


class ProviderInvalidRequestError(ProviderError):
    category = "invalid_request"
    retryable = False


class ProviderRateLimitError(ProviderError):
    category = "rate_limit"
    retryable = True


class ProviderQuotaExceededError(ProviderError):
    category = "quota_exceeded"
    retryable = False


class ProviderTimeoutError(ProviderError):
    category = "timeout"
    retryable = True


class ProviderBudgetExceededError(ProviderError):
    category = "budget_exceeded"
    retryable = False


class ProviderCircuitOpenError(ProviderError):
    """Short-circuit a provider after repeated final failures."""

    category = "circuit_open"
    retryable = False


def _is_quota_failure(response: httpx.Response) -> bool:
    """DashScope reports depleted free quota as HTTP 403, not a bad API key."""
    try:
        payload = response.json()
    except ValueError:
        return "quota" in (response.text or "").lower()
    code = str(payload.get("code") or "").lower()
    message = str(payload.get("message") or "").lower()
    return "quota" in code or "quota" in message or "free tier" in message


def map_httpx_error(provider: str, error: httpx.HTTPError) -> ProviderError:
    """Translate an HTTP transport/status failure into a domain error."""
    if isinstance(error, httpx.HTTPStatusError):
        status_code = error.response.status_code
        if status_code in {401, 403}:
            if status_code == 403 and _is_quota_failure(error.response):
                return ProviderQuotaExceededError(provider, str(error))
            return ProviderAuthenticationError(provider, str(error))
        if status_code == 429:
            return ProviderRateLimitError(provider, str(error))
        if status_code >= 500:
            return ProviderConnectionError(provider, str(error))
        return ProviderInvalidRequestError(provider, str(error))
    if isinstance(error, httpx.TimeoutException):
        return ProviderTimeoutError(provider, str(error))
    return ProviderConnectionError(provider, str(error))
