"""Shared test fixtures.

The embedder is the one heavy/external dependency; a tiny deterministic fake
lets tests exercise the real extract -> chunk -> store -> search path without
loading a model.
"""

from __future__ import annotations

from pathlib import Path

from rag_retriever.config import Config
from rag_retriever.pipeline import Retriever


class FakeEmbedder:
    def embed_documents(self, chunks: list[str]) -> list[list[float]]:
        return [[float(len(c)), 1.0, 0.0] for c in chunks]

    def embed_query(self, query: str) -> list[float]:
        return [float(len(query)), 1.0, 0.0]


def make_cfg(tmp_path: Path, **overrides) -> Config:
    base = dict(
        embed_backend="local",
        embed_model="fake",
        ollama_url="",
        data_dir=tmp_path / ".rag",
        chunk_tokens=800,
        chunk_overlap=100,
    )
    base.update(overrides)
    return Config(**base)


def make_retriever(tmp_path: Path, **cfg_overrides) -> Retriever:
    r = Retriever(make_cfg(tmp_path, **cfg_overrides))
    r._embedder = FakeEmbedder()
    return r
