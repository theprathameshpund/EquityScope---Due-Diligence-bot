"""LangGraph checkpointer selection."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from langgraph.checkpoint.memory import MemorySaver

from app.config import settings
from app.logging_setup import get_logger

log = get_logger(__name__)


@contextmanager
def get_checkpointer() -> Iterator[Any]:
    """Yield a ready checkpointer for local or external runtime storage."""
    if settings.runtime_storage == "local":
        yield MemorySaver()
        return

    from langgraph.checkpoint.postgres import PostgresSaver

    with PostgresSaver.from_conn_string(settings.postgres_dsn) as saver:
        saver.setup()
        yield saver
