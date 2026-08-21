"""Release-level acceptance path across the three product modules."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import makeitdown.pipeline as conversion
import rag_retriever.pipeline as retrieval
from rag_retriever.config import Config


REPO_ROOT = Path(__file__).resolve().parents[1]
LINT = REPO_ROOT / "lawiki" / "skill" / "lawiki" / "lint" / "lint.py"
CASE_ANCHOR = "答前必先检索"


class _DeterministicEmbedder:
    """Small offline boundary replacement; storage and retrieval remain real."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(text)), 1.0, 0.0] for text in texts]

    def embed_query(self, query: str) -> list[float]:
        return [float(len(query)), 1.0, 0.0]


def _run_lint(*args: Path | str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(LINT), *(str(arg) for arg in args)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_raw_source_reaches_retrieval_and_cited_answer_gate(tmp_path, monkeypatch):
    case = tmp_path / "case"
    raw = case / "原始资料"
    converted = case / "_md"
    raw.mkdir(parents=True)
    source_text = "甲方向乙方借款人民币50,000元。"
    (raw / "借条.txt").write_text(source_text, encoding="utf-8")

    report = conversion.convert_tree(
        raw,
        converted,
        ocr_engine="local",
        ocr_model="PP-StructureV3",
        cloud_token=None,
        workers=1,
        skip_existing=False,
        text_threshold=50,
        report_path=case / "report.json",
        quality_check=False,
        progress=False,
    )
    evidence = converted / "借条.md"
    assert report["succeeded"] == 1
    assert "provenance_version: 1" in evidence.read_text(encoding="utf-8")

    wiki = case / "wiki"
    wiki.mkdir()
    (wiki / "借款事实.md").write_text(
        "# 借款事实\n\n"
        "- 甲方向乙方借款人民币50,000元。"
        "〔来源: _md/借条.md：「甲方向乙方借款人民币50,000元」〕\n",
        encoding="utf-8",
    )
    (wiki / "index.md").write_text("# 案件索引\n\n[[借款事实]]\n", encoding="utf-8")
    (wiki / "log.md").write_text("# 操作日志\n", encoding="utf-8")
    for name in ("AGENTS.md", "CLAUDE.md"):
        (case / name).write_text(f"# 案件库\n\n{CASE_ANCHOR}\n", encoding="utf-8")

    checked = _run_lint("check", case)
    assert checked.returncode == 0, checked.stdout + checked.stderr

    monkeypatch.setattr(retrieval, "get_embedder", lambda _cfg: _DeterministicEmbedder())
    cfg = Config(
        embed_backend="local",
        embed_model="acceptance-fake",
        ollama_url="",
        data_dir=case / ".rag",
        chunk_tokens=384,
        chunk_overlap=50,
    )
    retriever = retrieval.Retriever(cfg)
    indexed = retriever.index_path(converted, source_root=case)
    assert indexed["files_indexed"] == 1
    hits = retriever.search("借款金额", k=1, source_prefix="_md/")
    assert hits and hits[0]["source"] == "_md/借条.md"
    assert "50,000" in hits[0]["text"]

    draft = case / "answer.md"
    draft.write_text(
        "## 结论\n\n"
        "甲方向乙方借款人民币50,000元。"
        "〔来源: _md/借条.md：「甲方向乙方借款人民币50,000元」〕\n",
        encoding="utf-8",
    )
    answered = _run_lint("answer", case, draft)
    assert answered.returncode == 0, answered.stdout + answered.stderr
