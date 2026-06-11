"""Section-aware, token-budgeted chunker for filing text."""

from __future__ import annotations

import hashlib
from functools import lru_cache

import tiktoken
from pydantic import BaseModel

from src.config import settings
from src.rag.parsing import FilingSection


class ChunkRecord(BaseModel):
    """One indexed chunk; chunk_id is deterministic for idempotent upserts."""

    chunk_id: str
    text: str
    ticker: str
    cik: str
    form_type: str
    fiscal_period: str
    section: str
    section_item: str
    chunk_index: int
    source_url: str
    accession: str
    token_count: int


@lru_cache(maxsize=1)
def _encoding() -> tiktoken.Encoding:
    return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    return len(_encoding().encode(text, disallowed_special=()))


def _split_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in text.split("\n\n") if p.strip()]


def chunk_text(
    text: str,
    max_tokens: int | None = None,
    overlap_tokens: int | None = None,
) -> list[str]:
    """Greedy paragraph packing into ~max_tokens chunks with token overlap.

    Paragraphs longer than max_tokens are hard-split on token boundaries.
    """
    max_tok = max_tokens if max_tokens is not None else settings.chunk_max_tokens
    overlap = overlap_tokens if overlap_tokens is not None else settings.chunk_overlap_tokens
    enc = _encoding()

    # Flatten to paragraph units, hard-splitting oversized paragraphs.
    units: list[tuple[str, int]] = []
    for para in _split_paragraphs(text):
        tokens = enc.encode(para, disallowed_special=())
        if len(tokens) <= max_tok:
            units.append((para, len(tokens)))
        else:
            for i in range(0, len(tokens), max_tok):
                piece = enc.decode(tokens[i : i + max_tok])
                units.append((piece, min(max_tok, len(tokens) - i)))

    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for para, n_tok in units:
        if current and current_tokens + n_tok > max_tok:
            chunks.append("\n\n".join(current))
            # Seed the next chunk with overlap from the tail of this one.
            if overlap > 0:
                tail_tokens = enc.encode(chunks[-1], disallowed_special=())[-overlap:]
                current = [enc.decode(tail_tokens)]
                current_tokens = len(tail_tokens)
            else:
                current = []
                current_tokens = 0
        current.append(para)
        current_tokens += n_tok
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def make_chunk_id(accession: str, section_item: str, index: int) -> str:
    digest = hashlib.sha1(f"{accession}|{section_item}|{index}".encode()).hexdigest()
    return f"chk_{digest[:16]}"


def chunk_section(
    section: FilingSection,
    *,
    ticker: str,
    cik: str,
    form_type: str,
    fiscal_period: str,
    source_url: str,
    accession: str,
) -> list[ChunkRecord]:
    """Chunk one filing section, prefixing each chunk with its section context."""
    header = f"[{form_type} {fiscal_period} — {section.title}]"
    records: list[ChunkRecord] = []
    for idx, body in enumerate(chunk_text(section.text)):
        text = f"{header}\n{body}"
        records.append(
            ChunkRecord(
                chunk_id=make_chunk_id(accession, section.item, idx),
                text=text,
                ticker=ticker.upper(),
                cik=cik,
                form_type=form_type,
                fiscal_period=fiscal_period,
                section=section.title,
                section_item=section.item,
                chunk_index=idx,
                source_url=source_url,
                accession=accession,
                token_count=count_tokens(text),
            )
        )
    return records
