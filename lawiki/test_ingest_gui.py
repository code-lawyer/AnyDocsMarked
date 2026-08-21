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

    def test_select_input_emits_flag_value(self):
        argv = g.build_ingest_argv({"engine": "auto", "cross_check": True,
                                    "cross_check_mode": "local"})
        self.assertIn("--cross-check-mode", argv)
        self.assertIn("local", argv)

    def test_no_bogus_flag_from_gui_only_capability(self):
        # unresolved_disposition 是 done 屏动作，无 CLI 标志——绝不进 ingest argv。
        argv = g.build_ingest_argv({"engine": "auto", "unresolved_disposition": True})
        self.assertNotIn("--disposition", argv)


class BuildIngestEnvTests(unittest.TestCase):
    def test_cross_check_token_env_when_enabled(self):
        env = g.build_ingest_env({"engine": "auto", "cross_check": True,
                                  "cross_check_mode": "cloud", "mineru_token": "TK"})
        self.assertEqual(env.get("MINERU_API_TOKEN"), "TK")

    def test_ocr_cloud_token_env(self):
        env = g.build_ingest_env({"engine": "cloud", "cloud_token": "PK"})
        self.assertEqual(env.get("PADDLEOCR_AISTUDIO_TOKEN"), "PK")

    def test_disabled_capability_secret_not_injected(self):
        env = g.build_ingest_env({"engine": "auto", "mineru_token": "TK"})  # cross_check off
        self.assertNotIn("MINERU_API_TOKEN", env)


class BuildCaseConfigTests(unittest.TestCase):
    def test_answer_selections_persisted(self):
        cfg = g.build_case_config({"rerank": True, "min_score": "0.3", "embed_backend": "ollama"})
        self.assertEqual(cfg, {"rerank": True, "min_score": "0.3", "embed_backend": "ollama"})

    def test_secret_never_persisted(self):
        cfg = g.build_case_config({"rerank": True, "mineru_token": "TK", "llm_api_key": "K"})
        self.assertNotIn("mineru_token", cfg)
        self.assertNotIn("llm_api_key", cfg)

    def test_empty_and_false_omitted(self):
        cfg = g.build_case_config({"rerank": False, "min_score": ""})
        self.assertEqual(cfg, {})


class WriteCaseConfigTests(unittest.TestCase):
    def test_writes_answer_selections(self):
        with tempfile.TemporaryDirectory() as td:
            case = Path(td)
            g.write_case_config(case, {"rerank": True, "min_score": "0.3", "mineru_token": "S"})
            data = json.loads((case / ".anydocsmarked" / "case.json").read_text(encoding="utf-8"))
            self.assertEqual(data, {"rerank": True, "min_score": "0.3"})  # secret 不落盘

    def test_no_file_when_empty(self):
        with tempfile.TemporaryDirectory() as td:
            case = Path(td)
            g.write_case_config(case, {"rerank": False})
            self.assertFalse((case / ".anydocsmarked" / "case.json").exists())


class BuildInstallArgvTests(unittest.TestCase):
    def test_ocr_pkg_becomes_ocr_flag(self):
        self.assertEqual(g.build_install_argv({"ocr_pkg": "local"}), ["--ocr", "local"])

    def test_empty_when_no_install_choice(self):
        self.assertEqual(g.build_install_argv({}), [])


class UnresolvedDispositionTests(unittest.TestCase):
    def test_extracts_per_file_paths_not_summary(self):
        report = {"stages": {"source_reconcile": {"unresolved": [
            "[未处置源级遗漏] 原始资料/合同.pdf",
            "[跳过无原因] 原始资料/笔录.doc",
            "[源多于已处理] 原始资料/ 有 5 个文件，report.json 仅记录 3 个——请重跑。",
        ]}}}
        self.assertEqual(g.unresolved_source_files(report),
                         ["原始资料/合同.pdf", "原始资料/笔录.doc"])

    def test_empty_report_yields_none(self):
        self.assertEqual(g.unresolved_source_files({}), [])

    def test_append_skip_log_writes_reconcile_parseable_entry(self):
        with tempfile.TemporaryDirectory() as td:
            case = Path(td)
            (case / "wiki").mkdir()
            g.append_skip_log(case, "原始资料/合同.pdf", "同一扫描件的重复件")
            text = (case / "wiki" / "log.md").read_text(encoding="utf-8")
            self.assertIn("skip | 原始资料/合同.pdf", text)
            self.assertIn("同一扫描件的重复件", text)
            # 真被 reconcile 的解析器视为"已登记跳过（带原因）"
            sys.path.insert(0, str(Path(g.__file__).parent / "skill" / "lawiki" / "lint"))
            from lint import _load_skips
            skips = _load_skips(case)
            self.assertTrue(skips.get("原始资料/合同.pdf"))


class WriteStopHookTests(unittest.TestCase):
    def test_writes_case_local_stop_hook(self):
        with tempfile.TemporaryDirectory() as td:
            case = Path(td)
            g.write_stop_hook(case, Path("/skills/lawiki"))
            settings = json.loads((case / ".claude" / "settings.json").read_text(encoding="utf-8"))
            cmds = [h["command"] for grp in settings["hooks"]["Stop"] for h in grp["hooks"]]
            self.assertTrue(any("stop_hook.py" in c for c in cmds))

    def test_merges_into_existing_settings(self):
        with tempfile.TemporaryDirectory() as td:
            case = Path(td)
            d = case / ".claude"
            d.mkdir()
            (d / "settings.json").write_text(json.dumps({"other": 1}), encoding="utf-8")
            g.write_stop_hook(case, Path("/skills/lawiki"))
            settings = json.loads((d / "settings.json").read_text(encoding="utf-8"))
            self.assertEqual(settings["other"], 1)  # 不覆盖既有键
            self.assertIn("Stop", settings["hooks"])


if __name__ == "__main__":
    unittest.main()
