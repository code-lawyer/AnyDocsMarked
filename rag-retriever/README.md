# rag-retriever

A lightweight, **local-first document retrieval engine** that mounts to an agent
as an **MCP tool**. Drop files in; the agent searches them and answers with **its
own LLM**. There is no LLM in here — this is only the "front half" of RAG
(extract → chunk → embed → store + similarity search).

```
your agent (owns the LLM)
   │  calls MCP tool: search("question")
   ▼
rag-retriever ──► extract ─► chunk ─► embed ─► LanceDB
   ▲                                              │
   └────────── returns relevant passages ◄────────┘
```

- **One LLM, not two.** The retriever never answers; your agent does. You keep full control of reasoning, prompts, and cost.
- **Local-first.** Default backend (`fastembed`) runs entirely offline, no server.
- **Pluggable embeddings.** Switch between fully local and a China-friendly cloud API with one env var — no code change.

## Install

```bash
cd rag-retriever
uv sync
cp .env.example .env   # then pick your embedding backend
```

## Configure (`.env`)

**Embedding backend** — index-time and query-time must use the **same backend + model**; changing the model means re-indexing.

| `RAG_EMBED_BACKEND` | Uses | Notes |
|---|---|---|
| `local` (default) | fastembed (ONNX, in-process) | 100% offline, no server |
| `ollama` | Ollama daemon | loopback needs no consent; a remote `RAG_OLLAMA_URL` needs `RAG_CLOUD_CONSENT=1` |
| `openai` | OpenAI-compatible API (e.g. SiliconFlow) | needs `RAG_OPENAI_API_KEY` + `RAG_CLOUD_CONSENT=1`; text leaves the machine |

`RAG_CLOUD_CONSENT` governs the data boundary, not a vendor. `localhost`, `127.0.0.0/8`, and `::1` stay local by default.

**Retrieval & chunking** — sensible defaults; override only if needed.

| Setting | Default | Notes |
|---|---|---|
| `RAG_CHUNK_STRATEGY` | `structure` | heading/table/legal-marker aware; `token` for plain packing |
| `RAG_HYBRID` | `1` | BM25 + vector RRF; `0` for pure vector |
| `RAG_MIN_SCORE` | `0` | cosine floor on the vector channel (`0` = off); must be within `[0,1]`, else falls back to `0` with a warning |
| `RAG_RERANK` | `none` | `local` loads a cross-encoder (`BAAI/bge-reranker-v2-m3`, CN-capable); the only setting that loads a model. Before scoring, Markdown markers are stripped; the text returned is still the original chunk |
| `RAG_QUERY_EXPAND` | off | when on, appends a content-word BM25 query only when the first full-text hit count is below `k`; `local` counts as on |
| `RAG_PARENT_CONTEXT` | `false` | small-to-big retrieval; every hit has a stable `parent_text` (`None` when off); enabling needs a re-index |

Other knobs (`RAG_RRF_K`, `RAG_HYBRID_CANDIDATES`, `RAG_PARENT_TOKENS`) are documented in `.env.example`.

Search is **hybrid by default**: a BM25 keyword channel (jieba-segmented, fully
offline) runs alongside vector similarity, merged with RRF — this sharpens recall
for exact legal terms (表见代理 vs 无权代理) that pure vectors blur. Chunking is
**structure-aware by default**: split along markdown headings (each chunk carries
its section breadcrumb), tables kept intact, legal markers (第X条, 本院认为, …)
preferred as split points.

## Mount as an MCP server (the real entry point)

Run `uv run rag-retriever-mcp` (stdio) and register it with your MCP client. For Claude Code:

```json
{
  "mcpServers": {
    "rag-retriever": {
      "command": "uv",
      "args": ["run", "--directory", "D:\\path\\to\\rag-retriever", "rag-retriever-mcp"]
    }
  }
}
```

Tools exposed: `index_path`, `search`, `list_sources`, `stats`.

## CLI (for testing)

```bash
uv run rag-retriever index "C:\path\to\docs"     # a file or a whole folder
uv run rag-retriever search "什么是表见代理" -k 5
uv run rag-retriever search "什么是表见代理" --show-parent   # needs RAG_PARENT_CONTEXT=1
uv run rag-retriever list | stats
uv run rag-retriever doctor                       # check manifest vs table; --fix to repair
```

## Supported files

pdf, docx, pptx, xlsx, html, md, txt, csv, json, epub (via markitdown).
**Scanned / image-only PDFs** need an OCR engine (tesseract) installed separately;
without it they extract empty and are reported as skipped.

## Layout

```
rag_retriever/
  config.py     # env-driven config; picks the embedding backend
  extract.py    # file -> text (markitdown)
  chunk.py      # structure-aware / token chunking
  embed.py      # local | ollama | openai-compatible backends
  store.py      # LanceDB vector store (embedded, no server)
  pipeline.py   # ingest + search orchestration (no LLM)
  server.py     # MCP server (agent-facing)
  cli.py        # manual CLI
```
