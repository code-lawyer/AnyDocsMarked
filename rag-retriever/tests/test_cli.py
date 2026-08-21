"""CLI wiring: flags reach the Retriever / produce the right output.

A recording fake stands in for Retriever so we test argument plumbing and
output formatting without loading an embedding model.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import rag_retriever.cli as cli


class FakeRetriever:
    """Records construction + calls; returns trivial canned data."""

    last_cfg = None
    last_index: dict = {}
    last_search: dict = {}

    def __init__(self, cfg=None):
        FakeRetriever.last_cfg = cfg

    def index_path(self, path, recursive=True, source_root=None, exclude=()):
        FakeRetriever.last_index = {
            "path": path, "recursive": recursive, "source_root": source_root,
            "exclude": exclude,
        }
        return {
            "path": path, "files_seen": 0, "files_indexed": 0,
            "files_skipped": 0, "total_chunks": 0, "skipped": [],
        }

    def search(self, query, k=5, source_prefix=None):
        FakeRetriever.last_search = {"query": query, "k": k, "source_prefix": source_prefix}
        return [{"source": "_md/a.md", "ord": 0, "text": "命中原文", "score": 0.5}]

    def list_sources(self):
        return [{"source": "_md/a.md", "chunks": 1}]

    def stats(self):
        return {"documents": 1, "chunks": 1}

    def doctor(self, fix=False):
        FakeRetriever.last_index = {"doctor_fix": fix}
        return {"in_sync": True, "manifest_chunks": 1, "table_chunks": 1}


def _run(monkeypatch, argv):
    monkeypatch.setattr(cli, "Retriever", FakeRetriever)
    monkeypatch.setattr(sys, "argv", argv)
    cli.main()


def test_index_passes_source_root(monkeypatch):
    _run(monkeypatch, ["rag-retriever", "index", "/case/_md", "--source-root", "/case"])
    assert FakeRetriever.last_index["path"] == "/case/_md"
    assert FakeRetriever.last_index["source_root"] == "/case"


def test_data_dir_overrides_config(monkeypatch):
    _run(monkeypatch, ["rag-retriever", "--data-dir", "/tmp/x/.rag", "list"])
    assert FakeRetriever.last_cfg is not None
    assert FakeRetriever.last_cfg.data_dir == Path("/tmp/x/.rag")


def test_exclude_flag_reaches_index(monkeypatch):
    _run(monkeypatch, ["rag-retriever", "index", "/case/_md", "--exclude", "report.json"])
    assert FakeRetriever.last_index["exclude"] == ("report.json",)


def test_index_warns_on_skipped_files(monkeypatch, capsys):
    class SkippingRetriever(FakeRetriever):
        def index_path(self, path, recursive=True, source_root=None, exclude=()):
            return {"path": path, "files_seen": 3, "files_indexed": 1,
                    "files_skipped": 2, "total_chunks": 5,
                    "skipped": [{"source": "a", "reason": "x"},
                                {"source": "b", "reason": "y"}]}

    monkeypatch.setattr(cli, "Retriever", SkippingRetriever)
    monkeypatch.setattr(sys, "argv", ["rag-retriever", "index", "/case/_md"])
    cli.main()
    assert "2 file(s) skipped" in capsys.readouterr().err   # partial batch is loud


def test_index_no_warning_when_nothing_skipped(monkeypatch, capsys):
    _run(monkeypatch, ["rag-retriever", "index", "/case/_md"])  # FakeRetriever: skipped 0
    assert "skipped" not in capsys.readouterr().err


def test_metadata_fields_flag_overrides_config(monkeypatch):
    _run(monkeypatch, [
        "rag-retriever", "index", "/case/_md",
        "--metadata-fields", "quality,source_type",
    ])
    assert FakeRetriever.last_cfg.metadata_fields == ("quality", "source_type")


def test_search_json_outputs_machine_readable(monkeypatch, capsys):
    _run(monkeypatch, ["rag-retriever", "search", "欠款", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data[0]["source"] == "_md/a.md"
    assert data[0]["text"] == "命中原文"


def test_doctor_fix_flag_reaches_retriever(monkeypatch, capsys):
    _run(monkeypatch, ["rag-retriever", "doctor", "--fix"])
    assert FakeRetriever.last_index == {"doctor_fix": True}
    assert json.loads(capsys.readouterr().out)["in_sync"] is True


def test_doctor_without_fix_defaults_false(monkeypatch, capsys):
    _run(monkeypatch, ["rag-retriever", "doctor"])
    assert FakeRetriever.last_index == {"doctor_fix": False}
    capsys.readouterr()


def test_search_filter_flag_reaches_retriever(monkeypatch, capsys):
    _run(monkeypatch, ["rag-retriever", "search", "表见代理", "--filter", "caseA/", "--json"])
    capsys.readouterr()
    assert FakeRetriever.last_search["source_prefix"] == "caseA/"


def test_search_without_filter_defaults_none(monkeypatch, capsys):
    _run(monkeypatch, ["rag-retriever", "search", "表见代理", "--json"])
    capsys.readouterr()
    assert FakeRetriever.last_search["source_prefix"] is None


def test_search_show_parent_prints_parent_block(monkeypatch, capsys):
    class ParentRetriever(FakeRetriever):
        def search(self, query, k=5, source_prefix=None):
            return [{"source": "doc.md", "ord": 0, "text": "child", "score": 0.9,
                     "metadata": {}, "parent_text": "THE PARENT BLOCK"}]

    monkeypatch.setattr(cli, "Retriever", ParentRetriever)
    monkeypatch.setattr(sys, "argv", ["rag-retriever", "search", "q", "--show-parent"])
    cli.main()
    out = capsys.readouterr().out
    assert "child" in out
    assert "THE PARENT BLOCK" in out


def test_list_knobs_env_present_and_cloud_removed():
    import json

    from rag_retriever.cli import _list_knobs_json

    d = json.loads(_list_knobs_json())
    assert d["module"] == "rag-retriever"
    assert "RAG_EMBED_BACKEND" in d["env"]
    assert "RAG_MIN_SCORE" in d["env"]
    # cloud embedding removed — these must be gone
    assert "RAG_CLOUD_CONSENT" not in d["env"]
    assert not any(e.startswith("RAG_OPENAI") for e in d["env"])
