"""Retrieval: payload-filtered dense search + cross-encoder reranking."""

from __future__ import annotations

from typing import cast

from qdrant_client.models import FieldCondition, Filter, MatchValue

from src.config import settings
from src.logging_setup import get_logger
from src.rag.embeddings import embed_query, get_cross_encoder
from src.rag.ingestion import get_qdrant_client
from src.state import RetrievedEvidence

log = get_logger(__name__)


def search(
    query: str,
    *,
    ticker: str,
    form_type: str | None = None,
    section_item: str | None = None,
    top_k: int | None = None,
) -> list[RetrievedEvidence]:
    """Dense search over the filings index, filtered by company payload."""
    k = top_k if top_k is not None else settings.retrieval_top_k
    must: list[FieldCondition] = [
        FieldCondition(key="ticker", match=MatchValue(value=ticker.upper()))
    ]
    if form_type:
        must.append(FieldCondition(key="form_type", match=MatchValue(value=form_type)))
    if section_item:
        must.append(FieldCondition(key="section_item", match=MatchValue(value=section_item)))

    client = get_qdrant_client()
    result = client.query_points(
        collection_name=settings.qdrant_collection,
        query=embed_query(query),
        query_filter=Filter(must=cast("list[object]", must)),
        limit=k,
        with_payload=True,
    )
    evidence: list[RetrievedEvidence] = []
    for point in result.points:
        payload = point.payload or {}
        evidence.append(
            RetrievedEvidence(
                chunk_id=str(payload.get("chunk_id", point.id)),
                text=str(payload.get("text", "")),
                source_url=str(payload.get("source_url", "")),
                form_type=str(payload.get("form_type", "")),
                fiscal_period=str(payload.get("fiscal_period", "")),
                section=str(payload.get("section", "")),
                score=float(point.score),
            )
        )
    return evidence


def rerank(
    query: str, candidates: list[RetrievedEvidence], top_n: int | None = None
) -> list[RetrievedEvidence]:
    """Cross-encoder rerank; returns the top N with updated scores."""
    if not candidates:
        return []
    n = top_n if top_n is not None else settings.rerank_top_n
    encoder = get_cross_encoder(settings.reranker_model)
    scores = encoder.predict([(query, c.text) for c in candidates], show_progress_bar=False)
    rescored = [
        c.model_copy(update={"score": float(s)})
        for c, s in zip(candidates, scores.tolist(), strict=True)
    ]
    rescored.sort(key=lambda c: c.score, reverse=True)
    return rescored[:n]


def retrieve(
    query: str,
    *,
    ticker: str,
    form_type: str | None = None,
    top_n: int | None = None,
) -> list[RetrievedEvidence]:
    """Search then rerank — the standard retrieval path for agents."""
    candidates = search(query, ticker=ticker, form_type=form_type)
    reranked = rerank(query, candidates, top_n=top_n)
    log.info("retrieved", query=query, candidates=len(candidates), kept=len(reranked))
    return reranked


def fetch_chunks_by_ids(chunk_ids: list[str]) -> dict[str, RetrievedEvidence]:
    """Look up chunks by their chunk_id payload (used by critic & evals)."""
    if not chunk_ids:
        return {}
    client = get_qdrant_client()
    found: dict[str, RetrievedEvidence] = {}
    for chunk_id in set(chunk_ids):
        points, _ = client.scroll(
            collection_name=settings.qdrant_collection,
            scroll_filter=Filter(
                must=cast(
                    "list[object]",
                    [FieldCondition(key="chunk_id", match=MatchValue(value=chunk_id))],
                )
            ),
            limit=1,
            with_payload=True,
        )
        for point in points:
            payload = point.payload or {}
            found[chunk_id] = RetrievedEvidence(
                chunk_id=chunk_id,
                text=str(payload.get("text", "")),
                source_url=str(payload.get("source_url", "")),
                form_type=str(payload.get("form_type", "")),
                fiscal_period=str(payload.get("fiscal_period", "")),
                section=str(payload.get("section", "")),
            )
    return found
