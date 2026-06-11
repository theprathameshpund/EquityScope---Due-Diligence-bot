"""Shared test configuration: dummy env so src.config imports cleanly."""

from __future__ import annotations

import os

# Set before any src import — config fails fast on missing required vars.
os.environ.setdefault("GROQ_API_KEY", "test-key-not-real")
os.environ.setdefault("EDGAR_USER_AGENT", "EquityScope CI ci@example.com")
