"""Config defaults — chunk size must match each backend's model window.

bge-small-zh-v1.5 (local fastembed default) caps at 512 tokens, so the default
chunk budget must stay under it; bge-m3 (ollama/openai) handles 8192, so it can
be larger. An explicit RAG_CHUNK_TOKENS always wins.
"""

from __future__ import annotations

import pytest

from rag_retriever.config import Config


def _clear(monkeypatch):
    for var in ("RAG_EMBED_BACKEND", "RAG_CHUNK_TOKENS", "RAG_EMBED_MODEL"):
        monkeypatch.delenv(var, raising=False)


def test_local_default_chunk_fits_512_window(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("RAG_EMBED_BACKEND", "local")
    assert Config.load().chunk_tokens == 384


def test_remote_default_chunk_is_larger(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("RAG_EMBED_BACKEND", "ollama")
    assert Config.load().chunk_tokens == 800


def test_explicit_chunk_tokens_overrides_backend_default(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("RAG_EMBED_BACKEND", "local")
    monkeypatch.setenv("RAG_CHUNK_TOKENS", "1234")
    assert Config.load().chunk_tokens == 1234


def test_chunk_strategy_defaults_to_structure(monkeypatch):
    monkeypatch.delenv("RAG_CHUNK_STRATEGY", raising=False)
    assert Config.load().chunk_strategy == "structure"


def test_chunk_strategy_env_override(monkeypatch):
    monkeypatch.setenv("RAG_CHUNK_STRATEGY", "token")
    assert Config.load().chunk_strategy == "token"


def test_hybrid_defaults(monkeypatch):
    for var in ("RAG_HYBRID", "RAG_RRF_K", "RAG_HYBRID_CANDIDATES"):
        monkeypatch.delenv(var, raising=False)
    cfg = Config.load()
    assert cfg.hybrid is True
    assert cfg.rrf_k == 60
    assert cfg.hybrid_candidates == 50


def test_hybrid_can_be_disabled(monkeypatch):
    monkeypatch.setenv("RAG_HYBRID", "0")
    assert Config.load().hybrid is False


def test_parent_context_defaults_off(monkeypatch):
    monkeypatch.delenv("RAG_PARENT_CONTEXT", raising=False)
    monkeypatch.delenv("RAG_PARENT_TOKENS", raising=False)
    from rag_retriever.config import Config
    cfg = Config.load()
    assert cfg.parent_context is False
    assert cfg.parent_tokens == 1600


def test_parent_context_env_on(monkeypatch):
    monkeypatch.setenv("RAG_PARENT_CONTEXT", "1")
    from rag_retriever.config import Config
    assert Config.load().parent_context is True


def test_parent_tokens_floored_to_twice_child(monkeypatch):
    # A parent must be materially larger than a child; a too-small value is floored.
    monkeypatch.setenv("RAG_CHUNK_TOKENS", "800")
    monkeypatch.setenv("RAG_PARENT_TOKENS", "500")
    monkeypatch.setenv("RAG_PARENT_CONTEXT", "1")
    from rag_retriever.config import Config
    with pytest.warns(RuntimeWarning, match="RAG_PARENT_TOKENS"):
        assert Config.load().parent_tokens == 1600  # max(500, 800*2)


def test_parent_tokens_not_rewritten_when_feature_off(monkeypatch):
    monkeypatch.setenv("RAG_CHUNK_TOKENS", "800")
    monkeypatch.setenv("RAG_PARENT_TOKENS", "500")
    monkeypatch.setenv("RAG_PARENT_CONTEXT", "0")
    assert Config.load().parent_tokens == 500


def test_min_score_defaults_to_zero(monkeypatch):
    monkeypatch.delenv("RAG_MIN_SCORE", raising=False)
    from rag_retriever.config import Config
    assert Config.load().min_score == 0.0


def test_min_score_env_parses_float(monkeypatch):
    monkeypatch.setenv("RAG_MIN_SCORE", "0.35")
    from rag_retriever.config import Config
    assert Config.load().min_score == 0.35


def test_min_score_invalid_value_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("RAG_MIN_SCORE", "not-a-number")
    from rag_retriever.config import Config
    assert Config.load().min_score == 0.0


@pytest.mark.parametrize("value", ["nan", "inf", "-0.1", "1.1"])
def test_min_score_nonfinite_or_out_of_range_falls_back(monkeypatch, value):
    monkeypatch.setenv("RAG_MIN_SCORE", value)
    with pytest.warns(RuntimeWarning, match="RAG_MIN_SCORE"):
        assert Config.load().min_score == 0.0


def test_openai_backend_removed(monkeypatch):
    # 云端 embedding 已删除：openai 后端不再是合法值，加载即 ValueError。
    monkeypatch.setenv("RAG_EMBED_BACKEND", "openai")
    with pytest.raises(ValueError):
        Config.load()


def test_remote_ollama_hard_rejected(monkeypatch):
    # 非 loopback ollama 会外传正文——无条件硬拒（无 consent 逃生口）。
    from rag_retriever.embed import get_embedder

    monkeypatch.setenv("RAG_EMBED_BACKEND", "ollama")
    monkeypatch.setenv("RAG_OLLAMA_URL", "https://ollama.example.com")
    get_embedder.cache_clear()

    with pytest.raises(ValueError):
        get_embedder(Config.load())


@pytest.mark.parametrize(
    "url",
    ["http://localhost:11434", "http://127.0.0.2:11434", "http://[::1]:11434"],
)
def test_loopback_ollama_ok(monkeypatch, url):
    from rag_retriever.embed import OllamaEmbedder, get_embedder

    monkeypatch.setenv("RAG_EMBED_BACKEND", "ollama")
    monkeypatch.setenv("RAG_OLLAMA_URL", url)
    get_embedder.cache_clear()

    assert isinstance(get_embedder(Config.load()), OllamaEmbedder)


def test_unimplemented_cloud_rerank_is_rejected_by_config(monkeypatch):
    monkeypatch.setenv("RAG_RERANK", "cloud")

    with pytest.raises(ValueError, match=r"RAG_RERANK.*none.*local"):
        Config.load()
