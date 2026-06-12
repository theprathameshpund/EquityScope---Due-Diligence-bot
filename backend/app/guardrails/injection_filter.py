"""Sanitize retrieved web/news text before it reaches any LLM prompt.

Retrieved content is data, never instructions. This filter strips HTML,
neutralizes common prompt-injection patterns, and caps length.
"""

from __future__ import annotations

import html
import re

from app.logging_setup import get_logger

log = get_logger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_MAX_LEN = 2000

# Patterns that try to address the model directly. Case-insensitive.
_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"ignore\s+(all\s+|any\s+)?(previous|prior|above)\s+instructions?", re.I),
    re.compile(r"disregard\s+(all\s+|any\s+)?(previous|prior|above)", re.I),
    re.compile(r"you\s+are\s+now\s+", re.I),
    re.compile(r"system\s*prompt", re.I),
    re.compile(r"\b(assistant|system|user)\s*:", re.I),
    re.compile(r"<\s*/?\s*(system|assistant|instructions?)\b[^>]*>", re.I),
    re.compile(r"\bdo\s+not\s+(follow|obey)\b.{0,40}\binstructions?\b", re.I),
    re.compile(r"\bnew\s+instructions?\s*:", re.I),
    re.compile(r"```", re.M),
]

_REDACTION = "[filtered]"


def sanitize(text: str, source: str = "unknown") -> str:
    """Return text safe to embed in a prompt as quoted data."""
    cleaned = html.unescape(text)
    cleaned = _TAG_RE.sub(" ", cleaned)
    hits = 0
    for pattern in _INJECTION_PATTERNS:
        cleaned, n = pattern.subn(_REDACTION, cleaned)
        hits += n
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if hits:
        log.warning("injection_patterns_filtered", source=source, hits=hits)
    return cleaned[:_MAX_LEN]
