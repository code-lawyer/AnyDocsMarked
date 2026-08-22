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


class BuildInstallEnvTests(unittest.TestCase):
    def test_ollama_url_injected_when_ollama_backend(self):
        env = g.build_install_env({"embed_backend": "ollama",
                                   "ollama_url": "http://127.0.0.1:9999"})
        self.assertEqual(env.get("RAG_OLLAMA_URL"), "http://127.0.0.1:9999")

    def test_embed_backend_injected(self):
        env = g.build_install_env({"embed_backend": "ollama", "ollama_url": "http://x"})
        self.assertEqual(env.get("RAG_EMBED_BACKEND"), "ollama")

    def test_empty_when_default_local(self):
        # local 后端不需要 ollama_url；未填则不注入。
        env = g.build_install_env({"embed_backend": "local"})
        self.assertNotIn("RAG_OLLAMA_URL", env)


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

    def test_prefers_structured_field_over_prose(self):
        # 新版报告带结构化 unresolved_files → 直接用，不解析人读字符串。
        report = {"stages": {"source_reconcile": {
            "unresolved": ["[随便改的措辞] 原始资料/x.pdf"],
            "unresolved_files": ["原始资料/合同.pdf", "原始资料/笔录.doc"]}}}
        self.assertEqual(g.unresolved_source_files(report),
                         ["原始资料/合同.pdf", "原始资料/笔录.doc"])

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


class ValidateOptionsTests(unittest.TestCase):
    def test_cloud_without_token_flagged(self):
        errs = g.validate_options({"engine": "cloud", "cloud_consent": True, "cloud_token": ""})
        self.assertTrue(any("token" in e.lower() for e in errs))

    def test_cloud_without_consent_flagged(self):
        errs = g.validate_options({"engine": "cloud", "cloud_consent": False, "cloud_token": "T"})
        self.assertTrue(any("同意" in e for e in errs))

    def test_cloud_fully_configured_ok(self):
        errs = g.validate_options({"engine": "cloud", "cloud_consent": True, "cloud_token": "T"})
        self.assertEqual(errs, [])

    def test_local_engine_needs_nothing(self):
        self.assertEqual(g.validate_options({"engine": "local"}), [])

    def test_required_when_missing_input_flagged(self):
        # 勾双 OCR 互校 + 云端模式，但没填 MinerU token → required_when 触发
        errs = g.validate_options({"engine": "local", "cross_check": True,
                                   "cross_check_mode": "cloud", "mineru_token": ""})
        self.assertTrue(any("MinerU" in e or "mineru" in e.lower() for e in errs))

    def test_required_when_satisfied_ok(self):
        errs = g.validate_options({"engine": "local", "cross_check": True,
                                   "cross_check_mode": "local"})  # local 校验器不需 token
        self.assertEqual(errs, [])


class RecheckUnresolvedTests(unittest.TestCase):
    def _case_with_skip(self, td, registered_reason=None):
        case = Path(td)
        (case / "_md").mkdir()
        # report.json: one failed conversion → one unresolved source file
        (case / "_md" / "report.json").write_text(json.dumps({
            "succeeded": 0, "warned": 0, "failed": 1, "skipped_existing": 0,
            "skipped_unsupported": 0,
            "failures": [{"file": "合同.pdf", "error": "boom"}], "skipped": [],
        }, ensure_ascii=False), encoding="utf-8")
        (case / "原始资料").mkdir()
        (case / "原始资料" / "合同.pdf").write_bytes(b"x")
        (case / "wiki").mkdir()
        if registered_reason:
            g.append_skip_log(case, "原始资料/合同.pdf", registered_reason)
        return case

    def test_unresolved_before_registration(self):
        with tempfile.TemporaryDirectory() as td:
            case = self._case_with_skip(td)
            self.assertEqual(g.recheck_unresolved(case), 1)

    def test_zero_after_registration(self):
        with tempfile.TemporaryDirectory() as td:
            case = self._case_with_skip(td, registered_reason="重复扫描件")
            self.assertEqual(g.recheck_unresolved(case), 0)


class ExitAdviceTests(unittest.TestCase):
    def test_code_2_mentions_install_or_consent(self):
        adv = g.exit_advice(2)
        self.assertTrue("安装" in adv or "同意" in adv)

    def test_code_3_mentions_integrity(self):
        self.assertIn("完整性", g.exit_advice(3))

    def test_code_0_is_pass(self):
        self.assertTrue(g.exit_advice(0))


class ProbeEnvironmentTests(unittest.TestCase):
    def _probe(self, mk, rg, gate=False):
        # mk/rg: whether makeitdown / rag-retriever verify as available
        def fake_verify(cmd):
            head = cmd[0] if cmd else ""
            if "makeitdown" in head:
                return mk
            return rg  # rag-retriever (via _rag_cmd) --help

        with mock.patch.object(g.install, "_verify", fake_verify), \
             mock.patch.object(g.install, "_check_answer_gate_ready", lambda: gate):
            return g.probe_environment()

    def test_makeitdown_missing_is_critical(self):
        env = self._probe(mk=False, rg=True)
        self.assertFalse(env["makeitdown"]["ok"])
        self.assertTrue(env["makeitdown"]["critical"])

    def test_rag_missing_is_not_critical(self):
        env = self._probe(mk=True, rg=False)
        self.assertFalse(env["rag"]["ok"])
        self.assertFalse(env["rag"]["critical"])

    def test_all_present(self):
        env = self._probe(mk=True, rg=True, gate=True)
        self.assertTrue(env["makeitdown"]["ok"])
        self.assertTrue(env["rag"]["ok"])
        self.assertTrue(env["stop_hook"]["ok"])

    def test_has_critical_blocker_helper(self):
        self.assertTrue(g.has_critical_gap(self._probe(mk=False, rg=True)))
        self.assertFalse(g.has_critical_gap(self._probe(mk=True, rg=False)))


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

    def test_preserves_existing_stop_hooks(self):
        # 用户已有的 Stop 钩子组不能被覆盖掉（只追加 lawiki 的）。
        with tempfile.TemporaryDirectory() as td:
            case = Path(td)
            d = case / ".claude"; d.mkdir()
            (d / "settings.json").write_text(json.dumps({"hooks": {"Stop": [
                {"hooks": [{"type": "command", "command": "my-own-hook"}]}]}}), encoding="utf-8")
            g.write_stop_hook(case, Path("/skills/lawiki"))
            cmds = [h["command"] for grp in
                    json.loads((d / "settings.json").read_text(encoding="utf-8"))["hooks"]["Stop"]
                    for h in grp["hooks"]]
            self.assertIn("my-own-hook", cmds)              # 既有保留
            self.assertTrue(any("stop_hook.py" in c for c in cmds))  # lawiki 追加

    def test_idempotent_no_duplicate_lawiki_hook(self):
        with tempfile.TemporaryDirectory() as td:
            case = Path(td)
            g.write_stop_hook(case, Path("/skills/lawiki"))
            g.write_stop_hook(case, Path("/skills/lawiki"))  # 跑两次
            cmds = [h["command"] for grp in
                    json.loads((case / ".claude" / "settings.json").read_text(encoding="utf-8"))
                    ["hooks"]["Stop"] for h in grp["hooks"]]
            self.assertEqual(sum("stop_hook.py" in c for c in cmds), 1)  # 不重复


if __name__ == "__main__":
    unittest.main()
