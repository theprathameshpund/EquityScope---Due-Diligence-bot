"""Local sentence-transformers embeddings, batched, lazily loaded."""

from __future__ import annotations

import os
from functools import lru_cache
from time import perf_counter
from threading import Lock
from typing import TYPE_CHECKING, cast

from app.config import settings
from app.logging_setup import get_logger

# Propagate HF_HUB_OFFLINE setting early so sentence-transformers respects it
if getattr(settings, "hf_hub_offline", False) or os.getenv("HF_HUB_OFFLINE", "0") == "1":
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"

if TYPE_CHECKING:
    from sentence_transformers import CrossEncoder, SentenceTransformer

log = get_logger(__name__)

# BGE models are trained with this query-side instruction prefix.
_BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

_embedder_lock = Lock()
_embedder_instance: SentenceTransformer | None = None


@lru_cache(maxsize=1)
def get_embedder() -> SentenceTransformer:
    global _embedder_instance
    if _embedder_instance is not None:
        return _embedder_instance

    with _embedder_lock:
        if _embedder_instance is not None:
            return _embedder_instance

        from sentence_transformers import SentenceTransformer
        import torch

        # Use all available CPU cores — PyTorch defaults to 1 thread on Windows
        cpu_cores = os.cpu_count() or 4
        try:
            torch.set_num_threads(cpu_cores)
        except RuntimeError as exc:
            log.warning("torch_num_threads_unchanged", error=str(exc))
        try:
            torch.set_num_interop_threads(max(1, cpu_cores // 2))
        except RuntimeError as exc:
            log.warning("torch_interop_threads_unchanged", error=str(exc))

        log.info("loading_embedding_model", model=settings.embedding_model, threads=cpu_cores)
        _embedder_instance = cast(
            "SentenceTransformer",
            SentenceTransformer(settings.embedding_model, device=settings.embedding_device),
        )
        return _embedder_instance


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


def embed_passages(texts: list[str], batch_size: int = 32) -> list[list[float]]:
    """Embed document chunks (no instruction prefix), L2-normalized."""
    if not texts:
        log.info("embedding_passages_skip", reason="no_texts")
        return []

    total = len(texts)
    total_batches = (total + batch_size - 1) // batch_size
    log.info(
        "embedding_passages_start",
        total_chunks=total,
        batch_size=batch_size,
        total_batches=total_batches,
        model=settings.embedding_model,
        device=settings.embedding_device,
    )

    embedder = get_embedder()
    vectors_out: list[list[float]] = []
    started = perf_counter()
    for batch_index, start in enumerate(range(0, total, batch_size), 1):
        batch = texts[start : start + batch_size]
        batch_started = perf_counter()
        log.info(
            "embedding_batch_start",
            batch=batch_index,
            total_batches=total_batches,
            chunk_start=start + 1,
            chunk_end=start + len(batch),
            batch_size=len(batch),
        )
        batch_vectors = embedder.encode(
            batch,
            batch_size=len(batch),
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        rows = batch_vectors.tolist() if hasattr(batch_vectors, "tolist") else batch_vectors
        vectors_out.extend(cast("list[list[float]]", rows))
        log.info(
            "embedding_batch_end",
            batch=batch_index,
            total_batches=total_batches,
            embedded=len(vectors_out),
            total_chunks=total,
            seconds=round(perf_counter() - batch_started, 2),
        )

    log.info(
        "embedding_passages_complete",
        total_chunks=total,
        total_batches=total_batches,
        seconds=round(perf_counter() - started, 2),
    )
    return vectors_out


def embed_query(query: str) -> list[float]:
    """Embed a search query with the BGE instruction prefix when applicable."""
    text = query
    if "bge" in settings.embedding_model.lower():
        text = _BGE_QUERY_PREFIX + query
    vector = get_embedder().encode([text], normalize_embeddings=True, show_progress_bar=False)
    return cast("list[float]", vector[0].tolist())
