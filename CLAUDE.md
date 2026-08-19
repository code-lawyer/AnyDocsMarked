# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

AnyDocsMarked turns messy legal case materials into a controlled, source-traceable case knowledge base and answers case questions with verbatim provenance. The overriding promise is **宁可查不出，也不编造** (never fabricate — say "not found in this case's materials" rather than answer from general knowledge). Read `README.md` for the six design principles and `CONTEXT.md` for the domain glossary; both encode invariants that constrain almost every change.

## Repository shape: three loosely-coupled modules

The repo is three independent subprojects that **must never import each other**. They collaborate only through CLI invocations, JSON, and markdown frontmatter contracts. Keep them independently installable, replaceable, and unit-testable — do not add a shared package or cross-module import to "reduce duplication."

| Module | Role | Form |
|---|---|---|
| `makeitdown/` | raw files (PDF/Word/scans/images/legacy .doc/.wps) → quality-marked markdown | installable CLI (`makeitdown`), optional dual-OCR cross-check |
| `rag-retriever/` | local-first hybrid retrieval (structure-aware chunking + BM25/vector RRF), **no LLM** | installable CLI (`rag-retriever`) + MCP server (`rag-retriever-mcp`) |
| `lawiki/` | agent skill: `_md` → source-anchored wiki + wiki×RAG cross-verified Q&A | **not installed** — a skill under `lawiki/skill/lawiki/`, zero-dependency stdlib core |

Data flow: `原始资料/` (immutable originals) → makeitdown → `_md/` (immutable source layer, dual SHA-256) → in parallel `wiki/` (lawiki ingest, every fact carries a verbatim anchor) and `.rag/` (rag-retriever index, optional/rebuildable). Q&A: lawiki is the primary path; RAG is an independent second path used to cross-check. Two independent paths reaching the same answer is the trust condition — disagreement is surfaced to a human, never silently resolved.

## Non-negotiable invariants (violating these breaks the product's core promise)

- **Deterministic gates before model judgment.** Rule-checkable things (anchors, dead links, date order, reconciliation, coverage, closed-world violations) are enforced by zero-dependency lint that exits non-zero to block. The model only does what rules cannot, and is still bound by the gates. Do not weaken a lint check to make a workflow pass.
- **Three annotation classes stay physically separated:** EXTRACTED (原文) / INFERRED (推断) / AMBIGUOUS (存疑). Inference must never be presented as fact.
- **Verbatim source anchors** `〔来源: _md/…：「逐字原文」〕` are machine-verified to exist character-for-character (only format noise normalized; numbers/text compared exactly). The anchor-normalization "catch real errors, don't false-positive" behavior is pinned by `lawiki/skill/lawiki/lint/test_lint.py` — treat that test as a spec, not a nuisance.
- **Privacy by default.** No content leaves the machine unless the user passes `--cloud-consent`; non-interactive runs never silently upload.
- **Coverage ledger:** every source file is 已引用 / 已登记跳过 / 未处置. If 未处置 > 0, ingest is not complete.
- **Tests use synthetic or fully desensitized data only** — never commit real case materials or credentials.

## Common commands

Each module uses different tooling — do not assume a single test runner.

```powershell
# lawiki — stdlib core, only pytest needed
cd lawiki
python -m pytest skill/lawiki scripts test_install.py -q

# makeitdown — uv, `dev` extra pulls pytest (`local`/paddle OCR is mocked, not needed)
cd makeitdown
uv run --extra dev python -m pytest tests -q

# rag-retriever — uv, `dev` dependency-group; a FakeEmbedder avoids model downloads
cd rag-retriever
uv run --group dev python -m pytest tests -q

# repo-wide gates (from repo root)
uvx ruff check --select E9,F .          # correctness only (undefined names/unused imports/syntax), NOT full style
git diff --check
```

Run a single test: append `-k <name>` or a path/`::` selector to the pytest command, e.g. `python -m pytest skill/lawiki/lint/test_lint.py -k anchor -q`.

Cross-module product-acceptance test (real raw→markdown→LanceDB/BM25→cited wiki→answer-gate path, only the embedding boundary is faked) lives at repo root:

```powershell
uv run --with-editable ./makeitdown --with-editable ./rag-retriever --with pytest python -m pytest tests -q
```

## CI and release gates

- `test.yml` runs all four suites (three modules + product-acceptance) on **Ubuntu + Windows**. Windows is in every matrix because it is the primary user platform (lawyers' desktops) — COM conversion, GBK console encoding, and path-separator bugs only surface there. Any change touching paths, encoding, or Office/COM must be considered on both OSes.
- Coverage: 80% branch-aware floor on the ubuntu leg (`.coveragerc` sets `branch=True`, omits tests); current production coverage is 84–86%.
- **Version alignment:** all three modules track the same bundle tag `vX.Y.Z`. Bump `VERSION`, both `pyproject.toml` `version` fields, and the CHANGELOG together when tagging. `release.yml` fires on `v*` tags and hard-fails on test/version/audit/offline-index/checksum mismatch — a tag is only a release *request*.
- **Before tagging, run the full release gate locally**, not just the module you touched: the full lawiki suite + `pip-audit` + version alignment. Hardcoded-version tests and transitive-dependency CVEs are the usual blockers (see recent CVE/build_bundle fix commits).

## Working conventions

- For changes to trust boundaries, data contracts, or larger behavior: write a spec in `docs/superpowers/specs/` (and a plan in `docs/superpowers/plans/`) before implementing. Existing specs there are the reference for module design decisions.
- When behavior changes, sync the affected README, `lawiki/skill/lawiki/references/*`, example outputs, and machine-readable contract tests in the same change.
- lawiki has two modes — **build** vs **answer** — and choosing wrong is the most common failure. `answer` mode requires re-reading `references/qa.md` in full and re-running evidence gathering (`tools/evidence.py`) + the `lint answer` delivery gate every time, even immediately after a build. See `lawiki/skill/lawiki/SKILL.md`.
