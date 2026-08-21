"""Pluggable embedding backends: local (fastembed) | ollama (local server only).

Both run on-device — there is no cloud embedding backend, so case text never
leaves the machine. Both expose the same interface so the rest of the pipeline
never cares where vectors come from. Index-time and query-time MUST use the same
backend + model, or similarity is meaningless — switching models requires re-indexing.
"""

from __future__ import annotations

import ipaddress
import logging
from functools import lru_cache
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

import httpx

from .config import Config


def _is_loopback_url(url: str) -> bool:
    """Return true only when an HTTP endpoint resolves syntactically to loopback."""
    host = urlsplit(url).hostname
    if host is None:
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False

_log = logging.getLogger(__name__)

# Directory where a vendored ONNX copy of the local model is shipped in the
# release bundle so the first index works offline (no HuggingFace download).
# Layout: _models/<model_name with '/' -> '--'>/ containing the files fastembed
# expects (model_optimized.onnx + tokenizer.json/config.json/vocab.txt/...).
_BUNDLED_MODELS_DIR = Path(__file__).resolve().parent / "_models"


def _bundled_model_dir(model_name: str) -> Path:
    return _BUNDLED_MODELS_DIR / model_name.replace("/", "--")


class Embedder(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


class LocalEmbedder:
    """In-process embeddings via fastembed (ONNX, no torch, no server)."""

    def __init__(self, model_name: str, model_path: str | None = None):
        from fastembed import TextEmbedding

        supported = {m["model"] for m in TextEmbedding.list_supported_models()}
        if model_name not in supported:
            raise ValueError(
                f"fastembed does not support '{model_name}'.\n"
                f"Pick one of (Chinese-capable first): "
                f"{sorted(s for s in supported if 'bge' in s.lower() or 'e5' in s.lower() or 'm3' in s.lower())}\n"
                f"Set RAG_EMBED_MODEL to a supported id, or switch RAG_EMBED_BACKEND."
            )
        # Prefer a locally vendored copy (release bundle, or RAG_EMBED_MODEL_PATH)
        # so the first index needs no network. specific_model_path short-circuits
        # fastembed's HuggingFace/GCS download entirely; fall back to the normal
        # download only when no local copy is present.
        local_dir = Path(model_path) if model_path else _bundled_model_dir(model_name)
        if local_dir.is_dir():
            self._model = TextEmbedding(
                model_name=model_name,
                specific_model_path=str(local_dir),
                local_files_only=True,
            )
        else:
            # No vendored copy: fastembed is about to reach out to HuggingFace.
            # Log the heads-up *before* attempting — a slow/stalled connection
            # should be understood immediately, not diagnosed after minutes of
            # silent waiting followed by a timeout. Real incident (LAWIKI-RAG-001):
            # a user hit this branch unknowingly (installed the non-offline bundle)
            # and only found out why indexing was hanging/failing after the fact.
            # logging (not print to stderr directly): LocalEmbedder is a library
            # class used from both cli.py and the MCP server (server.py) — a
            # caller must be able to suppress/redirect this, not just inherit an
            # unconditional stderr write. With no handler configured (the default
            # for a library), the stdlib's "handler of last resort" still prints
            # WARNING+ to stderr, so behavior is unchanged out of the box.
            _log.warning(
                "[rag-retriever] 未检测到内置 embedding 模型 '%s'，"
                "将尝试联网从 HuggingFace 下载……如需完全离线，请改用 "
                "anydocsmarked-*-offline.zip 发布包，或设 RAG_EMBED_MODEL_PATH "
                "指向已手动搬运到本机的模型目录。",
                model_name,
            )
            # In a network-restricted environment this fails deep inside fastembed/
            # huggingface_hub as a bare connection-timeout traceback with no
            # actionable next step — wrap it so the real fix (offline bundle /
            # mirror / different backend) is visible without reading a stack trace.
            try:
                self._model = TextEmbedding(model_name=model_name)
            except Exception as e:
                raise RuntimeError(
                    f"无法下载 embedding 模型 '{model_name}'（{type(e).__name__}: {e}）。"
                    f"该环境到 HuggingFace（含 HF_ENDPOINT 镜像）疑似不可达。解决：① 换用 "
                    f"anydocsmarked-*-offline.zip 发布包（内置模型，索引零下载）；② 设环境变量 "
                    f"HF_ENDPOINT=https://hf-mirror.com（或其他可达镜像）后重试；③ 设 "
                    f"RAG_EMBED_MODEL_PATH 指向手动搬运到本机的模型目录；④ 换后端 "
                    f"RAG_EMBED_BACKEND=ollama（本地服务，见 setup.md）。"
                ) from e

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self._model.embed(texts)]

    def embed_query(self, text: str) -> list[float]:
        return next(iter(self._model.query_embed(text))).tolist()


def _batched(texts: list[str], size: int):
    """Yield successive slices of `texts` of at most `size` (>=1) items."""
    step = max(1, size)
    for i in range(0, len(texts), step):
        yield texts[i:i + step]


class _HttpEmbedder:
    """Shared batching + interface for HTTP-backed embedders. Subclasses set
    ``self._batch_size`` and implement ``_embed_batch`` (one request); the loop,
    the document/query split, and the order contract live here once."""

    _batch_size: int

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    def _embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for batch in _batched(texts, self._batch_size):
            out.extend(self._embed_batch(batch))
        return out

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text])[0]


class OllamaEmbedder(_HttpEmbedder):
    """Local Ollama server (http). Lighter Python deps, but a daemon must run."""

    def __init__(self, model_name: str, base_url: str, batch_size: int = 64):
        self._model = model_name
        self._url = base_url.rstrip("/") + "/api/embed"
        self._batch_size = batch_size

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        resp = httpx.post(
            self._url, json={"model": self._model, "input": texts}, timeout=120
        )
        resp.raise_for_status()
        return resp.json()["embeddings"]


@lru_cache(maxsize=1)
def get_embedder(cfg: Config) -> Embedder:
    if cfg.embed_backend == "local":
        return LocalEmbedder(cfg.embed_model, cfg.embed_model_path or None)
    if cfg.embed_backend == "ollama":
        # Remote ollama would send case text off-device. No cloud embedding: only a
        # loopback server is allowed, and there is no consent escape hatch.
        if not _is_loopback_url(cfg.ollama_url):
            raise ValueError(
                "RAG_OLLAMA_URL must be a loopback endpoint (localhost / 127.0.0.1 / [::1]); "
                "a remote ollama would send case text off-device and is not supported. "
                "Run ollama locally, or use the bundled local backend."
            )
        return OllamaEmbedder(cfg.embed_model, cfg.ollama_url, cfg.embed_batch_size)
    raise ValueError(f"Unknown embed backend: {cfg.embed_backend}")
