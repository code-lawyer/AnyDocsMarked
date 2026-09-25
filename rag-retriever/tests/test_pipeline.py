import re

from rag_retriever import pipeline as pipeline_mod
from rag_retriever.pipeline import _rrf_fuse
from rag_retriever.config import Config


class _FakeEmbedder:
    def embed_documents(self, texts):
        return [[float(len(t)), 0.0, 0.0] for t in texts]

    def embed_query(self, text):
        return [float(len(text)), 0.0, 0.0]


class _FakeStore:
    def __init__(self):
        self.added = None

    def delete_source(self, source):
        pass

    def add(self, source, chunks, vectors, meta=None, metas=None):
        self.added = {"source": source, "chunks": chunks, "meta": metas}
        return len(chunks)

    def record_model(self, *a, **k):
        pass


def _retriever(monkeypatch, tmp_path, text, strategy="structure"):
    cfg = Config.load()
    cfg = type(cfg)(**{**cfg.__dict__, "data_dir": tmp_path, "chunk_strategy": strategy})
    r = pipeline_mod.Retriever(cfg)
    r.store = _FakeStore()
    r._embedder = _FakeEmbedder()
    monkeypatch.setattr(pipeline_mod, "extract_text", lambda p: text)
    monkeypatch.setattr(pipeline_mod, "read_frontmatter", lambda p: {})
    monkeypatch.setattr(pipeline_mod, "select_fields", lambda fm, fields: {})
    return r


def test_rrf_fuse_rewards_agreement():
    # B is ranked highly by both channels → should win after fusion.
    vector = [
        {"source": "d", "ord": 1, "text": "A", "score": 0.9, "metadata": {}},
        {"source": "d", "ord": 2, "text": "B", "score": 0.8, "metadata": {}},
    ]
    text = [
        {"source": "d", "ord": 2, "text": "B", "score": 5.0, "rank": 0, "metadata": {}},
        {"source": "d", "ord": 3, "text": "C", "score": 3.0, "rank": 1, "metadata": {}},
    ]
    fused = _rrf_fuse(vector, text, rrf_k=60, k=3)
    assert fused[0]["ord"] == 2  # B appears in both → highest fused score
    ids = [(h["source"], h["ord"]) for h in fused]
    assert ids[0] == ("d", 2)


def test_search_falls_back_to_vector_when_no_fts(monkeypatch, tmp_path):
    cfg = Config.load()
    cfg = type(cfg)(**{**cfg.__dict__, "data_dir": tmp_path, "hybrid": True})
    r = pipeline_mod.Retriever(cfg)
    r._embedder = _FakeEmbedder()

    class _S:
        def search(self, vec, k, source_prefix=None):
            return [{"source": "d", "ord": 0, "text": "hit", "score": 0.5, "metadata": {}}]
        def search_text(self, q, k, source_prefix=None):
            return []  # no FTS

    r.store = _S()
    hits = r.search("query", k=3)
    assert hits and hits[0]["text"] == "hit"


def test_search_passes_source_prefix_to_store(tmp_path):
    cfg = Config.load()
    cfg = type(cfg)(**{**cfg.__dict__, "data_dir": tmp_path, "hybrid": True})
    r = pipeline_mod.Retriever(cfg)
    r._embedder = _FakeEmbedder()
    seen = {}

    class _S:
        def search(self, vec, k, source_prefix=None):
            seen["vec"] = source_prefix
            return [{"source": "caseA/x", "ord": 0, "text": "hit", "score": 0.5, "metadata": {}}]
        def search_text(self, q, k, source_prefix=None):
            seen["fts"] = source_prefix
            return []

    r.store = _S()
    r.search("query", k=3, source_prefix="caseA/")
    assert seen["vec"] == "caseA/"
    assert seen["fts"] == "caseA/"
    # empty prefix is normalized to None (full-index search)
    seen.clear()
    r.search("query", k=3, source_prefix="   ")
    assert seen["vec"] is None
    assert seen["fts"] is None


def test_index_file_stores_heading_path_in_meta(monkeypatch, tmp_path):
    md = tmp_path / "case.md"
    md.write_text("# 判决书\n\n## 本院认为\n\n认定事实如下。\n", encoding="utf-8")
    r = _retriever(monkeypatch, tmp_path, md.read_text("utf-8"), strategy="structure")
    out = r.index_file(md)
    assert out["indexed"] is True
    stored = r.store.added
    # breadcrumb is prepended into the stored text
    assert any("判决书 > 本院认为" in c for c in stored["chunks"])
    # and recorded in per-chunk meta
    assert any(m.get("heading_path") == "判决书 > 本院认为" for m in stored["meta"])


def _real_store_retriever(monkeypatch, tmp_path, text, **overrides):
    """A Retriever with a REAL VectorStore (parents sidecar) but a fake embedder."""
    cfg = Config.load()
    cfg = type(cfg)(**{**cfg.__dict__, "data_dir": tmp_path / ".rag", **overrides})
    r = pipeline_mod.Retriever(cfg)
    r._embedder = _FakeEmbedder()
    monkeypatch.setattr(pipeline_mod, "extract_text", lambda p: text)
    monkeypatch.setattr(pipeline_mod, "read_frontmatter", lambda p: {})
    monkeypatch.setattr(pipeline_mod, "select_fields", lambda fm, fields: {})
    return r


def test_search_attaches_parent_text_when_enabled(monkeypatch, tmp_path):
    text = "# 合同\n\n" + "\n\n".join(f"第{i}条 关于货款与违约金的约定条款。" for i in range(30))
    r = _real_store_retriever(
        monkeypatch, tmp_path, text,
        parent_context=True, parent_tokens=120, chunk_tokens=30,
        chunk_overlap=0, hybrid=False, rerank="none",
    )
    r.index_file(tmp_path / "doc.md", source_root=tmp_path)
    hits = r.search("货款 违约金", k=3)
    assert hits
    assert hits[0]["parent_text"]
    # child body (breadcrumb stripped) is contained in its parent block.
    nows = lambda s: re.sub(r"\s+", "", s)
    child_body = hits[0]["text"]
    if child_body.startswith("合同"):
        child_body = child_body[len("合同"):]
    assert nows(child_body) in nows(hits[0]["parent_text"])


def test_search_parent_text_is_none_when_disabled(monkeypatch, tmp_path):
    text = "# 合同\n\n" + "\n\n".join(f"第{i}条 关于货款与违约金的约定条款。" for i in range(30))
    r = _real_store_retriever(
        monkeypatch, tmp_path, text,
        parent_context=False, chunk_tokens=30, chunk_overlap=0, hybrid=False, rerank="none",
    )
    r.index_file(tmp_path / "doc.md", source_root=tmp_path)
    hits = r.search("货款 违约金", k=3)
    assert hits
    assert hits[0]["parent_text"] is None


def test_index_file_parent_context_writes_ords_and_parents(monkeypatch, tmp_path):
    text = "# 合同\n\n" + "\n\n".join(f"第{i}条 关于货款与违约金的约定条款。" for i in range(30))
    r = _real_store_retriever(
        monkeypatch, tmp_path, text,
        parent_context=True, parent_tokens=120, chunk_tokens=30, chunk_overlap=0,
    )
    r.index_file(tmp_path / "doc.md", source_root=tmp_path)
    # Parents were stored, and every child carries a valid parent_ord.
    assert r.store.get_parent("doc.md", 0) is not None
    assert r.store.list_sources()[0]["source"] == "doc.md"


def test_search_min_score_default_zero_is_noop(tmp_path):
    cfg = Config.load()
    cfg = type(cfg)(**{**cfg.__dict__, "data_dir": tmp_path, "hybrid": False})
    assert cfg.min_score == 0.0

    r = pipeline_mod.Retriever(cfg)
    r._embedder = _FakeEmbedder()

    class _S:
        def search(self, vec, k, source_prefix=None):
            return [{"source": "d", "ord": 0, "text": "low", "score": 0.01, "metadata": {}}]
        def search_text(self, q, k, source_prefix=None):
            return []

    r.store = _S()
    hits = r.search("query", k=5)
    assert hits and hits[0]["text"] == "low"  # floor is off, nothing is dropped


def test_search_min_score_filters_pure_vector_path(tmp_path):
    cfg = Config.load()
    cfg = type(cfg)(**{**cfg.__dict__, "data_dir": tmp_path, "hybrid": False, "min_score": 0.6})
    r = pipeline_mod.Retriever(cfg)
    r._embedder = _FakeEmbedder()

    class _S:
        def search(self, vec, k, source_prefix=None):
            return [
                {"source": "d", "ord": 0, "text": "strong", "score": 0.9, "metadata": {}},
                {"source": "d", "ord": 1, "text": "weak", "score": 0.4, "metadata": {}},
            ]
        def search_text(self, q, k, source_prefix=None):
            return []

    r.store = _S()
    hits = r.search("query", k=5)
    assert [h["text"] for h in hits] == ["strong"]


def test_search_min_score_hybrid_preserves_keyword_only_hits(tmp_path):
    # Core contract: BM25/keyword hits must survive even when their vector
    # similarity is below the floor; a pure-vector hit below the floor with
    # no keyword match must be dropped.
    cfg = Config.load()
    cfg = type(cfg)(**{
        **cfg.__dict__, "data_dir": tmp_path, "hybrid": True, "min_score": 0.6, "rerank": "none",
    })
    r = pipeline_mod.Retriever(cfg)
    r._embedder = _FakeEmbedder()

    class _S:
        def search(self, vec, k, source_prefix=None):
            return [
                {"source": "d", "ord": 0, "text": "strong_vec", "score": 0.9, "metadata": {}},
                {"source": "d", "ord": 1, "text": "weak_vec_no_kw", "score": 0.2, "metadata": {}},
                {"source": "d", "ord": 2, "text": "weak_vec_with_kw", "score": 0.2, "metadata": {}},
            ]
        def search_text(self, q, k, source_prefix=None):
            return [{"source": "d", "ord": 2, "text": "weak_vec_with_kw", "score": 5.0, "metadata": {}}]

    r.store = _S()
    hits = r.search("query", k=5)
    ids = {(h["source"], h["ord"]) for h in hits}
    assert ("d", 0) in ids       # strong vector match: survives
    assert ("d", 2) in ids       # weak vector but BM25-matched: survives via keyword channel
    assert ("d", 1) not in ids   # weak vector, no keyword match: dropped


def test_search_min_score_all_filtered_returns_empty(tmp_path):
    cfg = Config.load()
    cfg = type(cfg)(**{**cfg.__dict__, "data_dir": tmp_path, "hybrid": False, "min_score": 0.99})
    r = pipeline_mod.Retriever(cfg)
    r._embedder = _FakeEmbedder()

    class _S:
        def search(self, vec, k, source_prefix=None):
            return [{"source": "d", "ord": 0, "text": "low", "score": 0.5, "metadata": {}}]
        def search_text(self, q, k, source_prefix=None):
            return []

    r.store = _S()
    assert r.search("query", k=5) == []


def test_search_min_score_with_parent_context_still_attaches(monkeypatch, tmp_path):
    # Filtering happens before _attach_parents; a surviving hit still gets
    # parent_text when parent_context is on.
    text = "# 合同\n\n" + "\n\n".join(f"第{i}条 关于货款与违约金的约定条款。" for i in range(30))
    r = _real_store_retriever(
        monkeypatch, tmp_path, text,
        parent_context=True, parent_tokens=120, chunk_tokens=30,
        chunk_overlap=0, hybrid=False, rerank="none", min_score=0.5,
    )
    r.index_file(tmp_path / "doc.md", source_root=tmp_path)
    hits = r.search("货款 违约金", k=3)
    assert hits
    assert hits[0]["parent_text"]


def test_search_query_expand_appends_variant_only_when_thin(monkeypatch, tmp_path):
    # First full-text search of the original query is empty. The first keyword
    # variant returns one hit. Expansion is BM25-only: the vector query is not
    # rewritten, and the variant helper is not called when the flag is off.
    from rag_retriever.query_expand import keyword_variants

    query = "合同 的 违约金 是 多少"
    variants = keyword_variants(query)
    assert variants
    first_variant = variants[0]
    variant_hit = {
        "source": "d.md",
        "ord": 3,
        "text": "违约金二十万",
        "score": 4.2,
        "metadata": {"quality": "clean"},
    }
    fixed = [0.25, 0.5, 0.75]

    class _FixedEmbedder:
        def embed_query(self, text):
            return list(fixed)

    class _S:
        def __init__(self):
            self.vectors = []
            self.texts = []

        def search(self, vec, k, source_prefix=None):
            self.vectors.append(vec)
            return []

        def search_text(self, q, k, source_prefix=None):
            self.texts.append((q, source_prefix))
            if q == query:
                return []
            if q == first_variant:
                return [dict(variant_hit)]
            return []

    def _search(expand):
        cfg = Config.load()
        cfg = type(cfg)(**{
            **cfg.__dict__,
            "data_dir": tmp_path,
            "hybrid": True,
            "rerank": "none",
            "parent_context": False,
            "query_expand": expand,
        })
        r = pipeline_mod.Retriever(cfg)
        r._embedder = _FixedEmbedder()
        store = _S()
        r.store = store
        return r.search(query, k=5, source_prefix="case/"), store

    hits, store = _search(True)
    assert store.vectors == [fixed]
    assert store.texts[0] == (query, "case/")
    assert (first_variant, "case/") in store.texts
    assert any(
        h["source"] == variant_hit["source"]
        and h["ord"] == variant_hit["ord"]
        and h["text"] == variant_hit["text"]
        and h["metadata"] == variant_hit["metadata"]
        for h in hits
    )

    called = []

    def _variants(q):
        called.append(q)
        return ["should-not-be-searched"]

    monkeypatch.setattr(pipeline_mod, "keyword_variants", _variants)
    monkeypatch.setattr("rag_retriever.query_expand.keyword_variants", _variants)
    hits_off, store_off = _search(False)
    assert called == []
    assert store_off.texts == [(query, "case/")]
    assert hits_off == []


def test_search_query_expand_skips_when_not_hybrid_or_already_full(monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(pipeline_mod, "keyword_variants", lambda q: called.append(q) or ["v"])

    class _E:
        def embed_query(self, text):
            return [1.0, 0.0, 0.0]

    class _S:
        def __init__(self, n):
            self.n = n

        def search(self, vec, k, source_prefix=None):
            return [{"source": "d", "ord": 0, "text": "vec", "score": 0.9, "metadata": {}}]

        def search_text(self, q, k, source_prefix=None):
            return [
                {"source": "d", "ord": i, "text": f"h{i}", "score": 1.0, "metadata": {}}
                for i in range(self.n)
            ]

    def _run(hybrid, n, k):
        called.clear()
        cfg = Config.load()
        cfg = type(cfg)(**{
            **cfg.__dict__,
            "data_dir": tmp_path,
            "hybrid": hybrid,
            "rerank": "none",
            "parent_context": False,
            "query_expand": True,
        })
        r = pipeline_mod.Retriever(cfg)
        r._embedder = _E()
        r.store = _S(n)
        return r.search("合同 的 违约金", k=k)

    hits = _run(hybrid=False, n=0, k=3)
    assert [h["text"] for h in hits] == ["vec"]
    assert called == []

    hits = _run(hybrid=True, n=3, k=3)
    assert len(hits) == 3
    assert called == []
