"""Orchestration: ingest (extract -> chunk -> embed -> store) and search.

This is the whole "front half of RAG". There is deliberately no LLM here —
search() returns passages; the calling agent does the reasoning.
"""

from __future__ import annotations

from pathlib import Path

from .chunk import Chunk, chunk_document, chunk_document_hierarchical
from .config import Config
from .embed import get_embedder
from .extract import extract_text, iter_files
from .frontmatter import read_frontmatter, select_fields
from .query_expand import keyword_variants
from .rerank import get_reranker
from .store import VectorStore

# Sentinel for the lazily-resolved reranker: get_reranker() legitimately returns
# None (reranking off), so None can't double as "not yet resolved".
_UNSET = object()


def _rrf_fuse(vector_hits: list[dict], text_hits: list[dict], rrf_k: int, k: int) -> list[dict]:
    """Reciprocal Rank Fusion over two ranked lists, keyed by (source, ord)."""
    scores: dict[tuple, float] = {}
    rep: dict[tuple, dict] = {}
    for ranked in (vector_hits, text_hits):
        for rank, hit in enumerate(ranked):
            key = (hit["source"], hit["ord"])
            scores[key] = scores.get(key, 0.0) + 1.0 / (rrf_k + rank)
            rep.setdefault(key, hit)
    fused = []
    for key, score in sorted(scores.items(), key=lambda kv: kv[1], reverse=True):
        hit = dict(rep[key])
        hit["score"] = round(score, 6)
        fused.append(hit)
    return fused[:k]


def _above_floor(hits: list[dict], floor: float) -> list[dict]:
    """Drop hits whose (cosine) score is below floor. No-op when floor <= 0.

    Applied to the vector channel only — BM25/keyword hits are never filtered,
    so a legal exact-term match (amount, statute number) still surfaces even
    when its vector similarity is weak."""
    if floor <= 0.0:
        return hits
    return [h for h in hits if h["score"] >= floor]


def _compose(chunk: Chunk) -> str:
    """Stored/embedded text: breadcrumb-prefixed so the vector carries section context."""
    if chunk.heading_path:
        return f"{chunk.heading_path}\n\n{chunk.text}"
    return chunk.text


def _chunk_meta(c: Chunk) -> dict:
    """Per-chunk metadata: heading breadcrumb (when present) and parent_ord (when
    the chunk came from the hierarchical path). Empty dict when neither applies —
    keeps existing metadata tests (which expect no key when absent) green."""
    m: dict = {}
    if c.heading_path:
        m["heading_path"] = c.heading_path
    if c.parent_ord is not None:
        m["parent_ord"] = c.parent_ord
    return m


def _relative_source(path: Path, source_root: str | Path | None) -> str:
    """Stored source id for a file: POSIX-relative to source_root, else absolute."""
    if source_root is None:
        return str(path)
    root = Path(source_root).resolve()
    return path.relative_to(root).as_posix()


class Retriever:
    def __init__(self, cfg: Config | None = None):
        self.cfg = cfg or Config.load()
        self.store = VectorStore(self.cfg.data_dir)
        self._embedder = None  # lazy: don't load the model until needed
        self._reranker = _UNSET  # lazy; None is a valid resolved value (rerank off)

    @property
    def embedder(self):
        if self._embedder is None:
            self._embedder = get_embedder(self.cfg)
        return self._embedder

    @property
    def reranker(self):
        if self._reranker is _UNSET:
            self._reranker = get_reranker(self.cfg)
        return self._reranker

    def index_file(self, path: str | Path, source_root: str | Path | None = None) -> dict:
        """Extract, chunk, embed, and store one file. Re-indexes cleanly.

        If source_root is given, the stored `source` is the file path relative to
        that root, with POSIX separators (e.g. `_md/合同/采购.md`) so downstream
        anchors stay stable across machines and OSes. Otherwise it's the absolute
        path (backward-compatible default).
        """
        path = Path(path).resolve()
        source = _relative_source(path, source_root)
        # Isolate EVERY per-file failure (extract / chunk / embed / store), not
        # just extraction: one bad file in a folder must not abort the whole batch
        # (mirrors makeitdown). A downstream crash — e.g. the embedder choking on
        # one file — would otherwise propagate out of index_path, leaving a partial
        # index with a nonzero exit (the "伪失败" seen in the field). Reported as
        # skipped-with-reason instead.
        try:
            text = extract_text(path)
            if not text:
                return {"source": source, "indexed": False, "chunks": 0,
                        "reason": "no extractable text (scanned image without OCR?)"}
            if self.cfg.parent_context:
                doc_chunks, parents = chunk_document_hierarchical(
                    text, self.cfg.chunk_tokens, self.cfg.chunk_overlap,
                    self.cfg.parent_tokens, self.cfg.chunk_strategy,
                )
            else:
                doc_chunks = chunk_document(
                    text, self.cfg.chunk_tokens, self.cfg.chunk_overlap, self.cfg.chunk_strategy
                )
                parents = None
            texts = [_compose(c) for c in doc_chunks]
            metas = [_chunk_meta(c) for c in doc_chunks]
            vectors = self.embedder.embed_documents(texts)
            meta = select_fields(read_frontmatter(path), self.cfg.metadata_fields)
            self.store.delete_source(source)
            n = self.store.add(source, texts, vectors, meta=meta, metas=metas)
            if parents is not None:
                self.store.set_parents(source, parents)
        except Exception as e:
            return {"source": source, "indexed": False, "chunks": 0,
                    "reason": f"{type(e).__name__}: {e}"}
        return {"source": source, "indexed": True, "chunks": n}

    def index_path(
        self, path: str | Path, recursive: bool = True,
        source_root: str | Path | None = None, exclude: tuple[str, ...] = (),
    ) -> dict:
        """Index a file or every supported file under a folder."""
        files = iter_files(path, recursive=recursive, exclude=exclude)
        if files and self._embedder is None:
            # Resolve the embedder once, up front, OUTSIDE index_file's per-file
            # try: construction failure (e.g. offline with no vendored model) is
            # an environment failure — every file would fail identically — and
            # must propagate/abort the batch, not be swallowed as one file's
            # "skipped" reason. install.py's --check-offline probe relies on this
            # nonzero exit (LAWIKI-RAG-001); per-file isolation below is only for
            # failures specific to one file (bad extraction, a single bad chunk).
            self._embedder = get_embedder(self.cfg)
        results = [self.index_file(f, source_root=source_root) for f in files]
        indexed = [r for r in results if r["indexed"]]
        skipped = [r for r in results if not r["indexed"]]
        # Record the index-time model once per run (not per file), and build the
        # full-text index once for the whole batch rather than once per file.
        if indexed:
            self.store.record_model(self.cfg.embed_backend, self.cfg.embed_model)
            self.store.rebuild_fts()
        return {
            "path": str(Path(path).resolve()),
            "files_seen": len(files),
            "files_indexed": len(indexed),
            "files_skipped": len(skipped),
            "total_chunks": sum(r["chunks"] for r in indexed),
            "skipped": skipped,
        }

    def search(self, query: str, k: int = 5,
               source_prefix: str | None = None) -> list[dict]:
        """Top-k relevant chunks. Hybrid (BM25+vector RRF) when enabled and FTS
        is available; otherwise pure vector. Optional source_prefix scopes the
        search to sources under that path prefix. No answer generation."""
        if not query.strip():
            return []
        sp = (source_prefix or "").strip() or None
        qvec = self.embedder.embed_query(query)
        if not self.cfg.hybrid:
            hits = self._vector_search(qvec, k, sp)
        else:
            cand = max(k, self.cfg.hybrid_candidates)
            vector_hits = self._vector_search(qvec, cand, sp)
            text_hits = self.store.search_text(query, k=cand, source_prefix=sp)
            if self.cfg.query_expand and len(text_hits) < k:
                seen = {(h["source"], h["ord"]) for h in text_hits}
                for variant in keyword_variants(query):
                    for hit in self.store.search_text(variant, k=cand, source_prefix=sp):
                        key = (hit["source"], hit["ord"])
                        if key not in seen:
                            text_hits.append(hit)
                            seen.add(key)
            if text_hits:
                fused = _rrf_fuse(vector_hits, text_hits, self.cfg.rrf_k, cand)
            else:
                fused = vector_hits[:cand]
            hits = self.reranker.rerank(query, fused, k) if self.reranker is not None else fused[:k]
        return self._attach_parents(hits)

    def _vector_search(self, qvec: list[float], k: int, source_prefix: str | None) -> list[dict]:
        """Vector-channel search with the relevance floor applied — the single
        choke point so every store.search() call in this class stays filtered
        (a future third call site can't forget to wrap it)."""
        return _above_floor(self.store.search(qvec, k=k, source_prefix=source_prefix), self.cfg.min_score)

    def _attach_parents(self, hits: list[dict]) -> list[dict]:
        """Attach each hit's enclosing parent block (small-to-big) as `parent_text`.

        The result contract is stable: every hit has ``parent_text``. It is ``None``
        when parent context is disabled or the index predates the parent sidecar.
        """
        for h in hits:
            parent = None
            if self.cfg.parent_context:
                ord_ = (h.get("metadata") or {}).get("parent_ord")
                parent = self.store.get_parent(h["source"], ord_)
            h["parent_text"] = parent
        return hits

    def list_sources(self) -> list[dict]:
        return self.store.list_sources()

    def doctor(self, fix: bool = False) -> dict:
        """Check the cached manifest against the table's real contents.

        The manifest is a sidecar that can desync from the table (crash mid-write,
        manual deletion). Report any drift; with fix=True, rebuild the manifest
        from the table so list/count are correct again.
        """
        manifest = {row["source"]: row["chunks"] for row in self.store.list_sources()}
        truth = self.store.table_manifest()
        in_sync = manifest == truth
        result = {
            "in_sync": in_sync,
            "manifest_documents": len(manifest),
            "manifest_chunks": sum(manifest.values()),
            "table_documents": len(truth),
            "table_chunks": sum(truth.values()),
        }
        if not in_sync and fix:
            # Reuse the scan we already did rather than re-scanning the table.
            result["repaired"] = self.store.reconcile(truth=truth)
        return result

    def stats(self) -> dict:
        # Report BOTH the index-time model (persisted, None until first index)
        # and the live query model, so a consumer can detect a mismatch that
        # would silently break similarity — without re-deriving either itself.
        info = self.store.model_info() or {}
        return {
            "index_backend": info.get("backend"),
            "index_model": info.get("model"),
            "query_backend": self.cfg.embed_backend,
            "query_model": self.cfg.embed_model,
            "data_dir": str(self.cfg.data_dir),
            "documents": len(self.store.list_sources()),
            "chunks": self.store.count(),
            # BM25 health — search_text degrades to [] silently, so this is the
            # visible signal that "hybrid" has quietly become vector-only.
            "fts": self.store.fts_status(),
        }
