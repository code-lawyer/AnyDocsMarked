"""Optional cross-encoder reranking. OFF by default — the only place a model may
be loaded. `none` (default) keeps the pipeline zero-model and offline.

- local:  fastembed cross-encoder reranker (in-process, ONNX)
"""

from __future__ import annotations

from typing import Protocol

from .config import Config
from .passage_clean import clean_passage


class Reranker(Protocol):
    def rerank(self, query: str, hits: list[dict], k: int) -> list[dict]: ...


class LocalReranker:
    def __init__(self, model_name: str):
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        # Positional: the model name is the first argument, and the unit test
        # replaces this class with a one-argument callable.
        self._model = TextCrossEncoder(model_name)

    def rerank(self, query: str, hits: list[dict], k: int) -> list[dict]:
        if not hits:
            return []
        scores = list(self._model.rerank(query, [clean_passage(h["text"]) for h in hits]))
        order = sorted(range(len(hits)), key=lambda i: scores[i], reverse=True)
        out = []
        for i in order[:k]:
            hit = dict(hits[i])
            hit["score"] = round(float(scores[i]), 6)
            out.append(hit)
        return out


def get_reranker(cfg: Config) -> Reranker | None:
    """Return a reranker, or None when reranking is disabled (the default)."""
    if cfg.rerank == "none":
        return None
    if cfg.rerank == "local":
        return LocalReranker(cfg.rerank_model)
    raise ValueError(f"unknown RAG_RERANK: {cfg.rerank!r} (expected none|local)")
