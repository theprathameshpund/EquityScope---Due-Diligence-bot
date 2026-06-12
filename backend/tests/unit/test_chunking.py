"""Chunker behavior: token budget, overlap, deterministic IDs."""

from __future__ import annotations

from app.rag.chunking import chunk_section, chunk_text, count_tokens, make_chunk_id
from app.rag.parsing import FilingSection


def test_chunks_respect_token_budget() -> None:
    text = "\n\n".join(f"Paragraph {i}: " + "word " * 120 for i in range(20))
    chunks = chunk_text(text, max_tokens=200, overlap_tokens=20)
    assert len(chunks) > 1
    # Budget + one overlap seed worth of slack.
    assert all(count_tokens(c) <= 200 + 130 for c in chunks)


def test_long_paragraph_is_hard_split() -> None:
    text = "token " * 5000  # single huge paragraph
    chunks = chunk_text(text, max_tokens=300, overlap_tokens=0)
    assert len(chunks) >= 2


def test_overlap_carries_tail_text() -> None:
    text = "\n\n".join(f"Sentence number {i} about revenue." for i in range(200))
    chunks = chunk_text(text, max_tokens=100, overlap_tokens=30)
    assert len(chunks) >= 2
    # The start of chunk 2 must repeat the tail of chunk 1.
    assert chunks[1][:20] in chunks[0]


def test_chunk_id_deterministic() -> None:
    a = make_chunk_id("0000320193-24-000123", "1A", 0)
    b = make_chunk_id("0000320193-24-000123", "1A", 0)
    c = make_chunk_id("0000320193-24-000123", "1A", 1)
    assert a == b
    assert a != c
    assert a.startswith("chk_")


def test_chunk_section_metadata() -> None:
    section = FilingSection(item="1A", title="Risk Factors", text="Risk. " * 500)
    records = chunk_section(
        section,
        ticker="aapl",
        cik="320193",
        form_type="10-K",
        fiscal_period="2024-09-28",
        source_url="https://example.com/doc.htm",
        accession="0000320193-24-000123",
    )
    assert records
    assert all(r.ticker == "AAPL" for r in records)
    assert all(r.section == "Risk Factors" for r in records)
    assert all(r.text.startswith("[10-K 2024-09-28 — Risk Factors]") for r in records)
    assert [r.chunk_index for r in records] == list(range(len(records)))
