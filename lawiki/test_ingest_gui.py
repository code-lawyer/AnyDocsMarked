# -*- coding: utf-8 -*-
"""ingest_gui.py 纯逻辑测试（stdlib unittest；不实例化 tkinter）。"""
import json
import sys
import tempfile
import tkinter
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
import ingest_gui as g  # noqa: E402


class ParseProgressTests(unittest.TestCase):
    def test_success_line(self):
        d = g.parse_progress_line("[12/87] ✓ 合同/采购框架.pdf (8.2s)")
        self.assertEqual(d, {"done": 12, "total": 87, "status": "succeeded",
                             "path": "合同/采购框架.pdf (8.2s)"})

    def test_failed_line(self):
        d = g.parse_progress_line("[14/87] ✗ 损坏.pdf — PDFSyntaxError: x")
        self.assertEqual(d["status"], "failed")
        self.assertEqual((d["done"], d["total"]), (14, 87))

    def test_non_progress_line_returns_none(self):
        self.assertIsNone(g.parse_progress_line("[lawiki-ingest] 将执行: makeitdown ..."))
        self.assertIsNone(g.parse_progress_line(""))


class SummarizeTests(unittest.TestCase):
    def test_pass(self):
        merged = {"gate": {"passed": True, "reasons": []}, "exit_code": 0,
                  "stages": {"convert": {"succeeded": 3, "warned": 0, "failed": 0,
                                         "skipped_existing": 0, "skipped_unsupported": 0},
                             "index": {"ran": True, "files_indexed": 3},
                             "source_reconcile": {"unresolved": []}}}
        s = g.summarize_report(merged)
        self.assertIn("通过", s)

    def test_fail_lists_reasons(self):
        merged = {"gate": {"passed": False, "reasons": ["[未处置源级遗漏] 原始资料/x.doc"]},
                  "exit_code": 3, "stages": {"convert": {}, "index": {"ran": False},
                  "source_reconcile": {"unresolved": ["[未处置源级遗漏] 原始资料/x.doc"]}}}
        s = g.summarize_report(merged)
        self.assertIn("未通过", s)
        self.assertIn("x.doc", s)


class EngineArgvTests(unittest.TestCase):
    def test_cloud_with_consent(self):
        self.assertEqual(g.build_ingest_argv({"engine": "cloud", "cloud_consent": True}),
                         ["--ocr-engine", "cloud", "--cloud-consent"])

    def test_local_no_consent(self):
        self.assertEqual(g.build_ingest_argv({"engine": "local"}), ["--ocr-engine", "local"])


class ConfigTests(unittest.TestCase):
    def test_load_missing_defaults_auto(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(g.load_gui_config(Path(td) / "cfg.json"), {"engine": "auto"})

    def test_roundtrip_without_token(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "cfg.json"
            g.save_gui_config(p, {"engine": "cloud", "token": "SECRET"})
            loaded = g.load_gui_config(p)
            self.assertEqual(loaded["engine"], "cloud")
            self.assertNotIn("token", json.loads(p.read_text(encoding="utf-8")))


class MainGuardTests(unittest.TestCase):
    def test_no_display_hard_fails_with_code_3(self):
        with mock.patch.object(g, "IngestApp", side_effect=tkinter.TclError("no display name")):
            rc = g.main([])
        self.assertEqual(rc, 3)

    def test_success_path_returns_0(self):
        fake_app = mock.Mock()
        with mock.patch.object(g, "IngestApp", return_value=fake_app):
            rc = g.main([])
        fake_app.mainloop.assert_called_once()
        self.assertEqual(rc, 0)


class BuildIngestArgvTests(unittest.TestCase):
    def test_engine_and_consent(self):
        argv = g.build_ingest_argv({"engine": "auto", "cloud_consent": True})
        self.assertIn("--ocr-engine", argv)
        self.assertIn("auto", argv)
        self.assertIn("--cloud-consent", argv)

    def test_choice_flags_only_when_enabled(self):
        argv = g.build_ingest_argv({"engine": "local", "cross_check": True})
        self.assertIn("--ocr-cross-check", argv)
        self.assertNotIn("--structure-headings", argv)
        self.assertNotIn("--rag-parent-context", argv)

    def test_parent_context_flag(self):
        argv = g.build_ingest_argv({"engine": "local", "parent_context": True})
        self.assertIn("--rag-parent-context", argv)

    def test_no_choice_by_default(self):
        argv = g.build_ingest_argv({"engine": "local"})
        for flag in ("--ocr-cross-check", "--structure-headings", "--rag-parent-context"):
            self.assertNotIn(flag, argv)


if __name__ == "__main__":
    unittest.main()
