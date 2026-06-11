"""LangGraph Postgres checkpointer — makes crashed runs resumable."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from langgraph.checkpoint.postgres import PostgresSaver

from src.config import settings
from src.logging_setup import get_logger

log = get_logger(__name__)


@contextmanager
def get_checkpointer() -> Iterator[PostgresSaver]:
    """Yield a ready PostgresSaver (tables created on first use)."""
    with PostgresSaver.from_conn_string(settings.postgres_dsn) as saver:
        saver.setup()
        yield saver
