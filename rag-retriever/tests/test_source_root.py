"""source_root: store paths relative to a root, with POSIX separators.

Downstream consumers (e.g. lawiki anchors) need stable relative paths like
`_md/合同/采购.md`, not absolute machine paths with OS-specific separators.

The embedder is the one heavy/external dependency; we inject a tiny fake so the
tests exercise the real extract -> chunk -> store path without loading a model.
"""

from __future__ import annotations

from pathlib import Path

from rag_retriever.config import Config
from rag_retriever.pipeline import Retriever


class FakeEmbedder:
    """Deterministic fixed-dim vectors; no model load."""

    def embed_documents(self, chunks: list[str]) -> list[list[float]]:
        return [[float(len(c)), 1.0, 0.0] for c in chunks]

    def embed_query(self, query: str) -> list[float]:
        return [float(len(query)), 1.0, 0.0]


def _cfg(tmp_path: Path) -> Config:
    return Config(
        embed_backend="local",
        embed_model="fake",
        ollama_url="",
        data_dir=tmp_path / ".rag",
        chunk_tokens=800,
        chunk_overlap=100,
    )


def _retriever(tmp_path: Path) -> Retriever:
    r = Retriever(_cfg(tmp_path))
    r._embedder = FakeEmbedder()
    return r


def test_index_file_stores_path_relative_to_source_root(tmp_path):
    case = tmp_path / "case"
    md = case / "_md" / "合同"
    md.mkdir(parents=True)
    f = md / "采购.md"
    f.write_text("双方于 2021 年 3 月 5 日签订本框架协议。", encoding="utf-8")

    r = _retriever(tmp_path)
    r.index_file(f, source_root=case)

    sources = [row["source"] for row in r.list_sources()]
    assert sources == ["_md/合同/采购.md"]


def test_index_file_without_source_root_keeps_absolute_path(tmp_path):
    f = tmp_path / "note.md"
    f.write_text("欠款金额为 50000 元。", encoding="utf-8")

    r = _retriever(tmp_path)
    r.index_file(f)

    assert [row["source"] for row in r.list_sources()] == [str(f.resolve())]


def test_search_returns_relative_source(tmp_path):
    case = tmp_path / "case"
    md = case / "_md"
    md.mkdir(parents=True)
    f = md / "note.md"
    f.write_text("欠款金额为 50000 元。", encoding="utf-8")

    r = _retriever(tmp_path)
    r.index_file(f, source_root=case)

    hits = r.search("欠款")
    assert hits
    assert hits[0]["source"] == "_md/note.md"
