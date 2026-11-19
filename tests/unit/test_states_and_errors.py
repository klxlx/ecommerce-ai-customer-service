"""Central state values and provider error classification."""

from __future__ import annotations

import httpx
import pytest

from app.core import states
from app.core.errors import (
    ProviderAuthenticationError,
    ProviderConnectionError,
    ProviderInvalidRequestError,
    ProviderRateLimitError,
    map_httpx_error,
)


def test_central_state_values_remain_compatible():
    assert states.Intent.ORDER.value == "order"
    assert states.Intent.AFTER_SALE.value == "after_sale"
    assert states.HandoffStatus.OPEN.value == "open"
    assert states.HandoffStatus.IN_PROGRESS.value == "in_progress"
    assert states.HandoffStatus.RESOLVED.value == "resolved"
    assert states.AfterSaleContextStatus.HAS_RECORDS.value == "has_records"
    assert states.AfterSaleContextStatus.NO_RECORDS.value == "no_records"
    assert states.AfterSaleContextStatus.NOT_AVAILABLE.value == "not_available"
    assert states.AfterSaleContextStatus.QUERY_ERROR.value == "query_error"
    assert states.ToolName.AFTER_SALE.value == "after_sale"


@pytest.mark.parametrize(
    ("status_code", "expected_type", "retryable"),
    [
        (429, ProviderRateLimitError, True),
        (500, ProviderConnectionError, True),
        (503, ProviderConnectionError, True),
        (401, ProviderAuthenticationError, False),
        (403, ProviderAuthenticationError, False),
        (400, ProviderInvalidRequestError, False),
    ],
)
def test_http_status_errors_map_to_retry_policy(
    status_code, expected_type, retryable
):
    request = httpx.Request("POST", "https://provider.example/api")
    response = httpx.Response(status_code, request=request)
    error = httpx.HTTPStatusError(
        "provider failed", request=request, response=response
    )

    mapped = map_httpx_error("embedding", error)

    assert isinstance(mapped, expected_type)
    assert mapped.retryable is retryable


def test_network_errors_are_retryable_provider_errors():
    request = httpx.Request("POST", "https://provider.example/api")
    mapped = map_httpx_error("rerank", httpx.ConnectError("down", request=request))
    assert isinstance(mapped, ProviderConnectionError)
    assert mapped.retryable is True

