"""Integration fixtures: shared PostgreSQL database and table cleanup."""

from __future__ import annotations

# Re-export the shared DB fixtures so integration tests can use them.
from tests.db import (  # noqa: F401
    _database,
    _truncate_tables,
    db_session,
)
