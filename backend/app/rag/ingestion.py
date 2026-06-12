"""Ingestion pipeline: fetch filings → parse → chunk → embed → upsert to Qdrant.

CLI:  python -m app.rag.ingestion AAPL
"""

from __future__ import annotations

import sys
import uuid
from functools import lru_cache

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    PointStruct,
    VectorParams,
)

from app.config import settings
from app.logging_setup import get_logger
from app.rag.chunking import ChunkRecord, chunk_section
from app.rag.embeddings import embed_passages, embedding_dim
from app.rag.parsing import parse_filing
from app.tools.edgar import CompanyIdentity, EdgarClient

log = get_logger(__name__)


@lru_cache(maxsize=1)
def get_qdrant_client() -> QdrantClient:
    return QdrantClient(url=settings.qdrant_url)


def ensure_collection(client: QdrantClient) -> None:
    if not client.collection_exists(settings.qdrant_collection):
        client.create_collection(
            collection_name=settings.qdrant_collection,
            vectors_config=VectorParams(size=embedding_dim(), distance=Distance.COSINE),
        )
        log.info("qdrant_collection_created", collection=settings.qdrant_collection)


def _point_id(chunk_id: str) -> str:
    """Deterministic UUID5 so re-ingestion overwrites instead of duplicating."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id))


def upsert_chunks(client: QdrantClient, chunks: list[ChunkRecord], batch_size: int = 64) -> None:
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i : i + batch_size]
        vectors = embed_passages([c.text for c in batch])
        points = [
            PointStruct(
                id=_point_id(chunk.chunk_id),
                vector=vector,
                payload=chunk.model_dump(),
            )
            for chunk, vector in zip(batch, vectors, strict=True)
        ]
        client.upsert(collection_name=settings.qdrant_collection, points=points)
        log.info("chunks_upserted", count=len(points), progress=f"{i + len(batch)}/{len(chunks)}")


_MIN_INDEXED_CHUNKS = 10  # below this threshold → treat as un-indexed and re-ingest


def is_company_indexed(ticker: str) -> bool:
    """True if the vector store holds a meaningful number of chunks for this ticker.

    A minimum threshold avoids treating a company as 'done' after a failed
    first run that produced 0 or very few chunks (e.g. when the 20-F form
    type was not yet supported).
    """
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    client = get_qdrant_client()
    if not client.collection_exists(settings.qdrant_collection):
        return False
    count = client.count(
        collection_name=settings.qdrant_collection,
        count_filter=Filter(
            must=[FieldCondition(key="ticker", match=MatchValue(value=ticker.upper()))]
        ),
        exact=True,
    )
    return count.count >= _MIN_INDEXED_CHUNKS


def ingest_company(query: str, force: bool = False) -> tuple[CompanyIdentity, int]:
    """Resolve, download, parse, chunk, embed and index a company's filings.

    Returns the resolved identity and the number of chunks indexed.
    """
    edgar = EdgarClient()
    identity = edgar.resolve_company(query)

    if not force and is_company_indexed(identity.ticker):
        log.info("company_already_indexed", ticker=identity.ticker)
        return identity, 0

    filings = edgar.list_target_filings(identity.cik)
    all_chunks: list[ChunkRecord] = []
    for filing in filings:
        try:
            path = edgar.download_filing(identity.ticker, filing)
            html = path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            log.warning("filing_download_failed", accession=filing.accession, error=str(exc))
            continue
        sections = parse_filing(html, filing.form)
        period = (filing.report_date or filing.filing_date).isoformat()
        for section in sections:
            all_chunks.extend(
                chunk_section(
                    section,
                    ticker=identity.ticker,
                    cik=identity.cik,
                    form_type=filing.form,
                    fiscal_period=period,
                    source_url=filing.source_url,
                    accession=filing.accession,
                )
            )
        log.info(
            "filing_parsed",
            accession=filing.accession,
            form=filing.form,
            sections=len(sections),
        )

    client = get_qdrant_client()
    ensure_collection(client)
    upsert_chunks(client, all_chunks)
    log.info("ingestion_complete", ticker=identity.ticker, chunks=len(all_chunks))
    edgar.close()
    return identity, len(all_chunks)


def main(argv: list[str]) -> int:
    if len(argv) < 1:
        print("usage: python -m src.rag.ingestion <TICKER-or-company> [--force]")
        return 2
    query = argv[0]
    force = "--force" in argv[1:]
    identity, n_chunks = ingest_company(query, force=force)
    print(f"Indexed {n_chunks} chunks for {identity.name} ({identity.ticker}, CIK {identity.cik})")

    # Sample retrieval as a smoke check.
    from app.rag.retriever import search

    hits = search("What are the main risk factors?", ticker=identity.ticker, top_k=3)
    print(f"\nSample retrieval — 'What are the main risk factors?' ({len(hits)} hits):")
    for hit in hits:
        preview = hit.text[:180].replace("\n", " ")
        print(f"  [{hit.score:.3f}] {hit.form_type} {hit.section}: {preview}…")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
