"""HTTP embedders must split large inputs into bounded batches.

A single document can produce hundreds of chunks; posting them all in one
request risks provider payload limits and timeouts. These tests stub the
transport so no network/model is needed (and conftest, which pulls tiktoken at
import, is intentionally not used here).
"""

from __future__ import annotations

from rag_retriever import embed as embed_mod


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_ollama_splits_into_batches(monkeypatch):
    calls = []

    def fake_post(url, json, timeout):
        texts = json["input"]
        calls.append(len(texts))
        return _FakeResp({"embeddings": [[1.0, 2.0] for _ in texts]})

    monkeypatch.setattr(embed_mod.httpx, "post", fake_post)
    e = embed_mod.OllamaEmbedder("m", "http://x", batch_size=2)

    out = e.embed_documents(["a", "b", "c", "d", "e"])
    assert len(out) == 5
    assert calls == [2, 2, 1]  # 5 texts, batch size 2


def test_empty_input_makes_no_request(monkeypatch):
    def boom(*a, **k):  # must not be called
        raise AssertionError("no request expected for empty input")

    monkeypatch.setattr(embed_mod.httpx, "post", boom)
    assert embed_mod.OllamaEmbedder("m", "http://x").embed_documents([]) == []
