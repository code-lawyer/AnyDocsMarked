"""Configuration, loaded from environment variables with sane defaults.

Everything is overridable via env so the same code serves a fully-local
(fastembed / Ollama) setup or a domestic cloud API (SiliconFlow) setup.
The embedding backend is chosen at runtime via RAG_EMBED_BACKEND.
"""

from __future__ import annotations

import os
import math
import warnings
from dataclasses import dataclass
from pathlib import Path


def _env(name: str, default: str) -> str:
    value = os.getenv(name)
    return value if value is not None and value.strip() else default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def split_csv(s: str) -> tuple[str, ...]:
    """Parse a comma-separated list into a tuple of non-empty trimmed fields."""
    return tuple(f.strip() for f in s.split(",") if f.strip())


# Default embedding model per backend (bge-m3 is strong on Chinese + long text).
# Both backends run **on-device** — no cloud embedding (case text never leaves the
# machine). local (fastembed) has no bge-m3; bge-small-zh-v1.5 is the safe Chinese
# option (release-bundled, offline). For true bge-m3 quality run a **local** ollama.
_DEFAULT_MODEL = {
    "local": "BAAI/bge-small-zh-v1.5",
    "ollama": "bge-m3",
}

# Default chunk size per backend, sized to the model's input window so chunks
# aren't silently truncated at embed time. The local fastembed default
# (bge-small-zh-v1.5) caps at 512 tokens; tiktoken (o200k) over-counts CJK vs
# the model's tokenizer, so 384 leaves headroom. bge-m3 (local ollama) handles
# 8192, so the larger 800 keeps more context per chunk. Override via
# RAG_CHUNK_TOKENS regardless of backend.
_DEFAULT_CHUNK_TOKENS = {"local": 384, "ollama": 800}


@dataclass(frozen=True)
class Config:
    # Which embedding backend: "local" (fastembed) | "ollama" (local server). Both
    # on-device — there is no cloud embedding backend (case text never leaves the box).
    embed_backend: str
    embed_model: str

    # ollama (local server only; remote endpoints are rejected — no cloud embedding)
    ollama_url: str

    # storage
    data_dir: Path

    # chunking (token-based; bge-m3 handles up to 8192 tokens)
    chunk_tokens: int
    chunk_overlap: int

    # Optional path to a locally vendored copy of the local model's ONNX files.
    # Empty = auto-detect the release-bundled copy (or download if absent).
    embed_model_path: str = ""

    # frontmatter fields to carry through as per-hit metadata (domain-agnostic).
    # Empty = none. The retriever does not interpret these; callers do.
    metadata_fields: tuple[str, ...] = ()

    # Max texts per request to the HTTP embedder (local ollama). A big document
    # can yield hundreds of chunks; batching keeps each request under provider
    # payload/timeout limits. Ignored by the local (fastembed) backend, which
    # batches internally.
    embed_batch_size: int = 64

    # chunking strategy: "structure" (heading/table/legal-marker aware) | "token"
    chunk_strategy: str = "structure"

    # hybrid retrieval (BM25 + vector via RRF)
    hybrid: bool = True
    rrf_k: int = 60
    hybrid_candidates: int = 50

    # optional cross-encoder rerank: "none" (default) | "local"
    rerank: str = "none"
    # cross-encoder model used when rerank == "local" (only loaded then).
    # Multilingual (same family as bge-m3 embeddings) so Chinese legal terms
    # rerank meaningfully; the English ms-marco default did not.
    rerank_model: str = "BAAI/bge-reranker-v2-m3"

    # Vector-channel relevance floor (cosine similarity). Hits with score below
    # this are dropped before fusion/rerank; BM25/keyword hits are never
    # filtered by this. 0.0 (default) = off, byte-identical to before this
    # feature. Only meaningful in (0, 1] — cosine similarity's range.
    min_score: float = 0.0

    # parent-context (small-to-big) retrieval: index fine child chunks, return the
    # enclosing parent block for context. Off by default (backward compatible).
    parent_context: bool = False
    parent_tokens: int = 1600

    @classmethod
    def load(cls) -> "Config":
        backend = _env("RAG_EMBED_BACKEND", "local").lower()
        if backend not in _DEFAULT_MODEL:
            raise ValueError(
                f"RAG_EMBED_BACKEND must be one of {list(_DEFAULT_MODEL)}, got '{backend}'"
            )
        model = _env("RAG_EMBED_MODEL", _DEFAULT_MODEL[backend])
        data_dir = Path(_env("RAG_DATA_DIR", str(Path.home() / ".rag-retriever" / "data")))
        chunk_tokens = _env_int("RAG_CHUNK_TOKENS", _DEFAULT_CHUNK_TOKENS[backend])
        parent_context = _env_bool("RAG_PARENT_CONTEXT", False)
        # A parent must be materially larger than a child, else small-to-big
        # degenerates into single-level chunking. Do not rewrite a dormant setting
        # while the feature is off; that made diagnostics disagree with the env.
        parent_tokens = _env_int("RAG_PARENT_TOKENS", 1600)
        if parent_context and parent_tokens < chunk_tokens * 2:
            adjusted = chunk_tokens * 2
            warnings.warn(
                f"RAG_PARENT_TOKENS={parent_tokens} is too small; using {adjusted} "
                f"(2 × RAG_CHUNK_TOKENS)", RuntimeWarning, stacklevel=2,
            )
            parent_tokens = adjusted
        min_score = _env_float("RAG_MIN_SCORE", 0.0)
        if not math.isfinite(min_score) or not 0.0 <= min_score <= 1.0:
            warnings.warn(
                f"RAG_MIN_SCORE must be finite and within [0, 1]; got {min_score!r}. "
                "Falling back to 0 (disabled).",
                RuntimeWarning, stacklevel=2,
            )
            min_score = 0.0
        rerank = _env("RAG_RERANK", "none").lower()
        if rerank not in {"none", "local"}:
            raise ValueError(
                f"RAG_RERANK must be one of ['none', 'local'], got '{rerank}'"
            )
        return cls(
            embed_backend=backend,
            embed_model=model,
            embed_model_path=_env("RAG_EMBED_MODEL_PATH", ""),
            ollama_url=_env("RAG_OLLAMA_URL", "http://localhost:11434"),
            data_dir=data_dir,
            chunk_tokens=chunk_tokens,
            chunk_overlap=_env_int("RAG_CHUNK_OVERLAP", 100),
            metadata_fields=split_csv(_env("RAG_METADATA_FIELDS", "")),
            embed_batch_size=_env_int("RAG_EMBED_BATCH_SIZE", 64),
            chunk_strategy=_env("RAG_CHUNK_STRATEGY", "structure").lower(),
            hybrid=_env_bool("RAG_HYBRID", True),
            rrf_k=_env_int("RAG_RRF_K", 60),
            hybrid_candidates=_env_int("RAG_HYBRID_CANDIDATES", 50),
            rerank=rerank,
            rerank_model=_env("RAG_RERANK_MODEL", "BAAI/bge-reranker-v2-m3"),
            min_score=min_score,
            parent_context=parent_context,
            parent_tokens=parent_tokens,
        )
