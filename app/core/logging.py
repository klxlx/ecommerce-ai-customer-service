"""Logging configuration."""

from __future__ import annotations

import logging
import re
import sys


_CREDENTIAL_URL = re.compile(
    r"(?P<prefix>://[^:\s/@]+:)(?P<secret>[^@\s]+)(?=@)", re.IGNORECASE
)
_AUTHORIZATION = re.compile(
    r"(?P<prefix>\bauthorization\s*[:=]\s*(?:bearer\s+)?)(?P<secret>[^\s,;]+)",
    re.IGNORECASE,
)
_SENSITIVE_FIELD = re.compile(
    r"(?P<prefix>\b(?:password|passwd|pwd|token|access_token|refresh_token|"
    r"api[_-]?key|secret|message|content|address|phone|mobile)\s*[:=]\s*)"
    r"(?P<secret>[^\s,;]+)",
    re.IGNORECASE,
)
_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_ORDER_NO = re.compile(r"\b[A-Za-z]{2,}-?\d{4,}(?:-\d+)?\b")
_REDACTED = "[REDACTED]"


def redact_sensitive(text: str) -> str:
    """Remove credentials and customer privacy before text reaches a log sink."""
    if not text:
        return text
    cleaned = _CREDENTIAL_URL.sub(
        lambda match: f"{match.group('prefix')}{_REDACTED}", text
    )
    for pattern in (_AUTHORIZATION, _SENSITIVE_FIELD):
        cleaned = pattern.sub(
            lambda match: f"{match.group('prefix')}{_REDACTED}", cleaned
        )
    cleaned = _PHONE.sub(_REDACTED, cleaned)
    cleaned = _ORDER_NO.sub(_REDACTED, cleaned)
    return cleaned


class RedactingFormatter(logging.Formatter):
    """Formatter that applies the shared redaction policy to every record."""

    def format(self, record: logging.LogRecord) -> str:
        return redact_sensitive(super().format(record))


def configure_logging(level: str = "INFO") -> None:
    """Configure a plain, structured-friendly logging setup for the service."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        RedactingFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
    # Quiet noisy third-party loggers.
    for noisy in ("httpx", "httpcore", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
