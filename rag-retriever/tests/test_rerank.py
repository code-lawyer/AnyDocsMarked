from rag_retriever.config import Config
from rag_retriever.rerank import LocalReranker, get_reranker


def test_rerank_none_returns_no_reranker(monkeypatch):
    monkeypatch.setenv("RAG_RERANK", "none")
    assert get_reranker(Config.load()) is None


def test_default_rerank_is_none(monkeypatch):
    monkeypatch.delenv("RAG_RERANK", raising=False)
    cfg = Config.load()
    assert cfg.rerank == "none"
    assert get_reranker(cfg) is None


def test_default_rerank_model_is_multilingual(monkeypatch):
    monkeypatch.delenv("RAG_RERANK_MODEL", raising=False)
    cfg = Config.load()
    assert cfg.rerank_model == "BAAI/bge-reranker-v2-m3"


class _FakeModel:
    def __init__(self):
        self.seen = []

    def rerank(self, query, passages):
        self.seen = list(passages)
        return [0.2]


def test_local_reranker_scores_cleaned_text_and_returns_original(monkeypatch):
    fake = _FakeModel()
    monkeypatch.setattr(
        "fastembed.rerank.cross_encoder.TextCrossEncoder",
        lambda name: fake,
    )
    reranker = LocalReranker("fake")
    original = "# 标题\n**50,000.00**"
    out = reranker.rerank("金额", [{"text": original, "source": "a", "ord": 0}], k=1)
    assert out[0]["text"] == original
    assert "50,000.00" in fake.seen[0]
    assert "#" not in fake.seen[0]
    assert "**" not in fake.seen[0]
