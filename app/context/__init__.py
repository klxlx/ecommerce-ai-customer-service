"""Context assembly primitives.

The package is intentionally small: it normalizes the data that reaches a
prompt without introducing long-term memory or graph checkpointing. All
formatters are deterministic so prompt changes remain reviewable.
"""

from app.context.builder import build_context_pack, context_engine_enabled
from app.context.models import ContextManifest, ContextPack

__all__ = [
    "ContextManifest",
    "ContextPack",
    "build_context_pack",
    "context_engine_enabled",
]
