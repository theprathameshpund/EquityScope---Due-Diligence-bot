"""Local sentence-transformers embeddings, batched, lazily loaded."""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING, cast

from app.config import settings
from app.logging_setup import get_logger

if TYPE_CHECKING:
    from sentence_transformers import CrossEncoder, SentenceTransformer

log = get_logger(__name__)

# BGE models are trained with this query-side instruction prefix.
_BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


@lru_cache(maxsize=1)
def get_embedder() -> SentenceTransformer:
    from sentence_transformers import SentenceTransformer

    log.info("loading_embedding_model", model=settings.embedding_model)
    return cast(
        "SentenceTransformer",
        SentenceTransformer(settings.embedding_model, device=settings.embedding_device),
    )


@lru_cache(maxsize=2)
def get_cross_encoder(model_name: str) -> CrossEncoder:
    from sentence_transformers import CrossEncoder

    log.info("loading_cross_encoder", model=model_name)
    return cast("CrossEncoder", CrossEncoder(model_name, device=settings.embedding_device))


def embedding_dim() -> int:
    dim = get_embedder().get_sentence_embedding_dimension()
    if dim is None:  # pragma: no cover - depends on model metadata
        raise RuntimeError(f"Model {settings.embedding_model} does not report a dimension")
    return int(dim)


def embed_passages(texts: list[str], batch_size: int = 16) -> list[list[float]]:
    """Embed document chunks (no instruction prefix), L2-normalized."""
    if not texts:
        return []
    vectors = get_embedder().encode(
        texts,
        batch_size=batch_size,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return cast("list[list[float]]", vectors.tolist())


def embed_query(query: str) -> list[float]:
    """Embed a search query with the BGE instruction prefix when applicable."""
    text = query
    if "bge" in settings.embedding_model.lower():
        text = _BGE_QUERY_PREFIX + query
    vector = get_embedder().encode([text], normalize_embeddings=True, show_progress_bar=False)
    return cast("list[float]", vector[0].tolist())
