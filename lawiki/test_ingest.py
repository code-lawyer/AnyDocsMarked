# -*- coding: utf-8 -*-
"""ingest.py 回归测试（stdlib unittest，零依赖，镜像 test_install.py）。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
import ingest  # noqa: E402


def _conv(succeeded=0, warned=0, failed=0, skipped_existing=0, skipped_unsupported=0):
    return {"succeeded": succeeded, "warned": warned, "failed": failed,
            "skipped_existing": skipped_existing, "skipped_unsupported": skipped_unsupported}


class GateAndMergeTests(unittest.TestCase):
    def test_all_pass(self):
        merged = ingest._gate_and_merge(
            Path("/case"), _conv(succeeded=3), {"files_indexed": 3, "files_skipped": 0},
            3, [], index_ran=True)
        self.assertTrue(merged["gate"]["passed"])
        self.assertEqual(merged["gate"]["reasons"], [])
        self.assertEqual(merged["exit_code"], ingest.EXIT_PASS)

    def test_conversion_failure_exits_1_and_takes_precedence(self):
        merged = ingest._gate_and_merge(
            Path("/case"), _conv(succeeded=2, failed=1), {"files_indexed": 0, "files_skipped": 0},
            2, ["[未处置源级遗漏] 原始资料/x.doc"], index_ran=True)
        self.assertEqual(merged["exit_code"], ingest.EXIT_CONVERT_FAILED)  # 1 覆盖 3
        self.assertTrue(any("硬失败" in r for r in merged["gate"]["reasons"]))

    def test_index_incomplete_exits_3(self):
        merged = ingest._gate_and_merge(
            Path("/case"), _conv(succeeded=3), {"files_indexed": 2, "files_skipped": 1},
            3, [], index_ran=True)
        self.assertEqual(merged["exit_code"], ingest.EXIT_INCOMPLETE)
        self.assertTrue(any("未进入 .rag" in r for r in merged["gate"]["reasons"]))

    def test_reconcile_unresolved_exits_3(self):
        merged = ingest._gate_and_merge(
            Path("/case"), _conv(succeeded=1), {"files_indexed": 1, "files_skipped": 0},
            1, ["[未处置源级遗漏] 原始资料/老合同.doc"], index_ran=True)
        self.assertEqual(merged["exit_code"], ingest.EXIT_INCOMPLETE)
        self.assertIn("[未处置源级遗漏] 原始资料/老合同.doc", merged["gate"]["reasons"])
        self.assertEqual(merged["stages"]["source_reconcile"]["unresolved"],
                         ["[未处置源级遗漏] 原始资料/老合同.doc"])

    def test_index_not_run_still_passes(self):
        merged = ingest._gate_and_merge(
            Path("/case"), _conv(succeeded=3), {}, 3, [], index_ran=False)
        self.assertTrue(merged["gate"]["passed"])
        self.assertEqual(merged["stages"]["index"], {"ran": False, "md_files": 3})

    def test_skipped_unsupported_alone_does_not_fail(self):
        # skipped_unsupported 若已在 reconcile 登记则 reconcile_reasons 为空 → 不失败
        merged = ingest._gate_and_merge(
            Path("/case"), _conv(succeeded=2, skipped_unsupported=1),
            {"files_indexed": 2, "files_skipped": 0}, 2, [], index_ran=True)
        self.assertTrue(merged["gate"]["passed"])


class PreflightTests(unittest.TestCase):
    def test_missing_raw(self):
        with tempfile.TemporaryDirectory() as td:
            err = ingest._preflight(Path(td) / "原始资料")
            self.assertIn("原始资料", err or "")

    def test_empty_raw(self):
        with tempfile.TemporaryDirectory() as td:
            raw = Path(td) / "原始资料"; raw.mkdir()
            self.assertIn("空", ingest._preflight(raw) or "")

    def test_makeitdown_missing(self):
        with tempfile.TemporaryDirectory() as td:
            raw = Path(td) / "原始资料"; raw.mkdir()
            (raw / "a.txt").write_text("x", encoding="utf-8")
            with mock.patch.object(ingest.shutil, "which", return_value=None):
                self.assertIn("makeitdown", ingest._preflight(raw) or "")

    def test_ok(self):
        with tempfile.TemporaryDirectory() as td:
            raw = Path(td) / "原始资料"; raw.mkdir()
            (raw / "a.txt").write_text("x", encoding="utf-8")
            with mock.patch.object(ingest.shutil, "which", return_value="/bin/makeitdown"):
                self.assertIsNone(ingest._preflight(raw))


class _FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode; self.stdout = stdout; self.stderr = stderr


class RunnerTests(unittest.TestCase):
    def test_init_case_argv(self):
        with mock.patch.object(ingest.subprocess, "run", return_value=_FakeProc(0)) as m:
            ingest._run_init_case(Path("/x"), dry_run=False)
        argv = m.call_args.args[0]
        self.assertEqual(argv[0], sys.executable)
        self.assertTrue(argv[1].endswith("init_case.py"))
        self.assertEqual(argv[2], "/x")

    def test_convert_reads_report_and_flags(self):
        with tempfile.TemporaryDirectory() as td:
            md = Path(td) / "_md"; md.mkdir()
            (md / "report.json").write_text(json.dumps({"succeeded": 2}), encoding="utf-8")
            with mock.patch.object(ingest.subprocess, "run", return_value=_FakeProc(0)) as m:
                report, rc = ingest._run_convert(
                    Path(td) / "原始资料", md, ocr_engine="cloud", cloud_consent=True,
                    workers=1, skip_existing=True, dry_run=False)
            argv = m.call_args.args[0]
            self.assertEqual(argv[0], "makeitdown")
            self.assertIn("--cloud-consent", argv)
            self.assertIn("--skip-existing", argv)
            self.assertIn("--ocr-engine", argv)
            self.assertEqual(report["succeeded"], 2)
            self.assertEqual(rc, 0)

    def test_convert_no_report_returns_none(self):
        with tempfile.TemporaryDirectory() as td:
            md = Path(td) / "_md"; md.mkdir()
            with mock.patch.object(ingest.subprocess, "run", return_value=_FakeProc(2)):
                report, rc = ingest._run_convert(
                    Path(td) / "原始资料", md, ocr_engine="cloud", cloud_consent=False,
                    workers=1, skip_existing=False, dry_run=False)
            self.assertIsNone(report)
            self.assertEqual(rc, 2)

    # _run_index 现在委托 rag.index_case（单一来源）；这里只锁「index_case 结果 → 阶段元组」
    # 的映射。--exclude report.json / --metadata-fields 的命令构造由 rag 自己的测试守。
    def test_index_delegates_and_maps_ok(self):
        with mock.patch.object(ingest, "_index_case",
                               return_value={"ok": True, "files_indexed": 3, "files_skipped": 0}) as m:
            result, ran = ingest._run_index(Path("/x"), dry_run=False)
        m.assert_called_once_with(Path("/x"))
        self.assertTrue(ran)
        self.assertEqual(result, {"files_indexed": 3, "files_skipped": 0})

    def test_index_not_installed_degrades(self):
        with mock.patch.object(ingest, "_index_case",
                               return_value={"ok": False, "reason": "未安装 rag-retriever（或不在 PATH）"}):
            result, ran = ingest._run_index(Path("/x"), dry_run=False)
        self.assertFalse(ran); self.assertIsNone(result)

    def test_index_other_failure_marks_empty(self):
        with mock.patch.object(ingest, "_index_case",
                               return_value={"ok": False, "reason": "退出码 1：boom"}):
            result, ran = ingest._run_index(Path("/x"), dry_run=False)
        self.assertTrue(ran); self.assertEqual(result, {"files_indexed": 0, "files_skipped": 0})

    def test_index_dry_run_skips_call(self):
        with mock.patch.object(ingest, "_index_case") as m:
            result, ran = ingest._run_index(Path("/x"), dry_run=True)
        m.assert_not_called()
        self.assertEqual((result, ran), (None, True))

    # _run_reconcile 现在直接调 reconcile 纯函数（reconcile.py 自带纯函数测试）；
    # 这里锁映射 + 无 report.json 时的兜底。
    def test_reconcile_clean(self):
        with mock.patch.object(ingest, "_reconcile", return_value=([], {})):
            self.assertEqual(ingest._run_reconcile(Path("/x"), dry_run=False), [])

    def test_reconcile_unresolved(self):
        with mock.patch.object(ingest, "_reconcile",
                               return_value=(["[未处置源级遗漏] 原始资料/老合同.doc"], {})):
            reasons = ingest._run_reconcile(Path("/x"), dry_run=False)
        self.assertEqual(reasons, ["[未处置源级遗漏] 原始资料/老合同.doc"])

    def test_reconcile_missing_report_returns_reason(self):
        with mock.patch.object(ingest, "_reconcile",
                               side_effect=FileNotFoundError("找不到 _md/report.json")):
            reasons = ingest._run_reconcile(Path("/x"), dry_run=False)
        self.assertEqual(len(reasons), 1)
        self.assertIn("report.json", reasons[0])


class MainTests(unittest.TestCase):
    def _case(self, td):
        case = Path(td) / "case"; raw = case / "原始资料"; raw.mkdir(parents=True)
        (raw / "a.txt").write_text("甲方向乙方借款五万元。", encoding="utf-8")
        return case

    def test_default_engine_is_auto(self):
        with tempfile.TemporaryDirectory() as td:
            case = self._case(td); (case / "_md").mkdir()
            (case / "_md" / "a.md").write_text("x", encoding="utf-8")
            captured = {}
            def fake_convert(raw, md, *, ocr_engine, **kw):
                captured["engine"] = ocr_engine
                return {"succeeded": 1, "failed": 0}, 0
            with mock.patch.object(ingest.shutil, "which", return_value="mk"), \
                 mock.patch.object(ingest, "_run_init_case"), \
                 mock.patch.object(ingest, "_run_convert", side_effect=fake_convert), \
                 mock.patch.object(ingest, "_run_index", return_value=({"files_indexed": 1, "files_skipped": 0}, True)), \
                 mock.patch.object(ingest, "_run_reconcile", return_value=[]):
                ingest.main([str(case)])
            self.assertEqual(captured["engine"], "auto")

    def test_preflight_failure_returns_2(self):
        with tempfile.TemporaryDirectory() as td:
            case = Path(td) / "case"
            with mock.patch.object(ingest, "_run_init_case"):
                rc = ingest.main([str(case)])
            self.assertEqual(rc, ingest.EXIT_PREFLIGHT)
            self.assertFalse((case / "ingest-report.json").exists())

    def test_convert_no_report_returns_2(self):
        with tempfile.TemporaryDirectory() as td:
            case = self._case(td)
            with mock.patch.object(ingest.shutil, "which", return_value="mk"), \
                 mock.patch.object(ingest, "_run_init_case"), \
                 mock.patch.object(ingest, "_run_convert", return_value=(None, 2)):
                rc = ingest.main([str(case), "--ocr-engine", "cloud"])
            self.assertEqual(rc, ingest.EXIT_PREFLIGHT)

    def test_happy_path_returns_0_and_writes_report(self):
        with tempfile.TemporaryDirectory() as td:
            case = self._case(td); (case / "_md").mkdir()
            (case / "_md" / "借条.md").write_text("x", encoding="utf-8")
            with mock.patch.object(ingest.shutil, "which", return_value="mk"), \
                 mock.patch.object(ingest, "_run_init_case"), \
                 mock.patch.object(ingest, "_run_convert", return_value=({"succeeded": 1, "failed": 0}, 0)), \
                 mock.patch.object(ingest, "_run_index", return_value=({"files_indexed": 1, "files_skipped": 0}, True)), \
                 mock.patch.object(ingest, "_run_reconcile", return_value=[]):
                rc = ingest.main([str(case), "--ocr-engine", "local"])
            self.assertEqual(rc, ingest.EXIT_PASS)
            merged = json.loads((case / "ingest-report.json").read_text(encoding="utf-8"))
            self.assertTrue(merged["gate"]["passed"])

    def test_reconcile_unresolved_returns_3(self):
        with tempfile.TemporaryDirectory() as td:
            case = self._case(td); (case / "_md").mkdir()
            (case / "_md" / "a.md").write_text("x", encoding="utf-8")
            with mock.patch.object(ingest.shutil, "which", return_value="mk"), \
                 mock.patch.object(ingest, "_run_init_case"), \
                 mock.patch.object(ingest, "_run_convert", return_value=({"succeeded": 1, "failed": 0}, 0)), \
                 mock.patch.object(ingest, "_run_index", return_value=({"files_indexed": 1, "files_skipped": 0}, True)), \
                 mock.patch.object(ingest, "_run_reconcile", return_value=["[未处置源级遗漏] 原始资料/x.doc"]):
                rc = ingest.main([str(case), "--ocr-engine", "local"])
            self.assertEqual(rc, ingest.EXIT_INCOMPLETE)

    def test_skip_index_passes(self):
        with tempfile.TemporaryDirectory() as td:
            case = self._case(td); (case / "_md").mkdir()
            (case / "_md" / "a.md").write_text("x", encoding="utf-8")
            with mock.patch.object(ingest.shutil, "which", return_value="mk"), \
                 mock.patch.object(ingest, "_run_init_case"), \
                 mock.patch.object(ingest, "_run_convert", return_value=({"succeeded": 1, "failed": 0}, 0)), \
                 mock.patch.object(ingest, "_run_reconcile", return_value=[]), \
                 mock.patch.object(ingest, "_run_index") as mi:
                rc = ingest.main([str(case), "--ocr-engine", "local", "--skip-index"])
            mi.assert_not_called()
            self.assertEqual(rc, ingest.EXIT_PASS)
            merged = json.loads((case / "ingest-report.json").read_text(encoding="utf-8"))
            self.assertEqual(merged["stages"]["index"], {"ran": False, "md_files": 1})

    def test_dry_run_returns_0_no_report(self):
        with tempfile.TemporaryDirectory() as td:
            case = self._case(td)
            with mock.patch.object(ingest.shutil, "which", return_value="mk"), \
                 mock.patch.object(ingest.subprocess, "run") as m:
                rc = ingest.main([str(case), "--dry-run"])
            m.assert_not_called()
            self.assertEqual(rc, ingest.EXIT_PASS)
            self.assertFalse((case / "ingest-report.json").exists())


class MainSetupTests(unittest.TestCase):
    def test_main_sets_up_loose_folder_then_runs(self):
        with tempfile.TemporaryDirectory() as td:
            case = Path(td) / "案子"; case.mkdir()
            (case / "借条.txt").write_text("甲借乙五万", encoding="utf-8")  # 散落，无 原始资料/
            (case / "_md").mkdir()
            (case / "_md" / "借条.md").write_text("x", encoding="utf-8")
            with mock.patch.object(ingest.shutil, "which", return_value="mk"), \
                 mock.patch.object(ingest, "_run_init_case"), \
                 mock.patch.object(ingest, "_run_convert", return_value=({"succeeded": 1, "failed": 0}, 0)), \
                 mock.patch.object(ingest, "_run_index", return_value=({"files_indexed": 1, "files_skipped": 0}, True)), \
                 mock.patch.object(ingest, "_run_reconcile", return_value=[]):
                rc = ingest.main([str(case), "--ocr-engine", "local"])
            self.assertEqual(rc, ingest.EXIT_PASS)
            self.assertTrue((case / "原始资料" / "借条.txt").is_file())  # 已归入
            self.assertFalse((case / "借条.txt").exists())

    def test_dry_run_does_not_move(self):
        with tempfile.TemporaryDirectory() as td:
            case = Path(td) / "案子"; case.mkdir()
            (case / "借条.txt").write_text("x", encoding="utf-8")
            with mock.patch.object(ingest.subprocess, "run"):
                rc = ingest.main([str(case), "--dry-run"])
            self.assertEqual(rc, ingest.EXIT_PASS)
            self.assertTrue((case / "借条.txt").exists())          # 未移动
            self.assertFalse((case / "原始资料").exists())

    def test_nonexistent_case_rejected_no_dir_created(self):
        with tempfile.TemporaryDirectory() as td:
            case = Path(td) / "不存在的案子"  # never created
            rc = ingest.main([str(case)])
            self.assertEqual(rc, ingest.EXIT_PREFLIGHT)
            self.assertFalse(case.exists())  # 没有在打错的路径上建目录


class ReconcileIntegrationTests(unittest.TestCase):
    """真实调用 _run_reconcile（不 mock）——实打实跑 skill/lawiki/tools/reconcile.py，
    验证 stdout 解析这条缝真的被走到（其余 MainTests 全 mock 掉了这条路径）。"""

    def _build_case(self, td):
        case = Path(td) / "case"
        raw = case / "原始资料"; raw.mkdir(parents=True)
        (raw / "good.txt").write_text("甲方向乙方借款五万元。", encoding="utf-8")
        (raw / "老合同.doc").write_text("legacy placeholder", encoding="utf-8")
        md = case / "_md"; md.mkdir()
        report = {
            "succeeded": 1, "warned": 0, "failed": 0,
            "skipped_existing": 0, "skipped_unsupported": 1,
            "failures": [],
            "skipped": [{"file": "老合同.doc", "reason": "needs LibreOffice"}],
        }
        (md / "report.json").write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        (case / "wiki").mkdir()
        return case

    def test_unresolved_skip_surfaces_in_reasons(self):
        with tempfile.TemporaryDirectory() as td:
            case = self._build_case(td)
            (case / "wiki" / "log.md").write_text("# 操作日志\n", encoding="utf-8")
            reasons = ingest._run_reconcile(case, dry_run=False)
        self.assertTrue(reasons)
        self.assertTrue(any("老合同.doc" in r for r in reasons))
        self.assertFalse(any(r.startswith("源级对账") for r in reasons))

    def test_registered_skip_resolves_to_empty(self):
        with tempfile.TemporaryDirectory() as td:
            case = self._build_case(td)
            log = ("# 操作日志\n\n"
                   "## [2026-08-16] skip | 原始资料/老合同.doc\n"
                   "- 原因：needs LibreOffice\n")
            (case / "wiki" / "log.md").write_text(log, encoding="utf-8")
            reasons = ingest._run_reconcile(case, dry_run=False)
        self.assertEqual(reasons, [])


class SetupCaseTests(unittest.TestCase):
    def test_moves_loose_files_into_raw(self):
        with tempfile.TemporaryDirectory() as td:
            case = Path(td)
            (case / "借条.txt").write_text("x", encoding="utf-8")
            sub = case / "合同"; sub.mkdir()
            (sub / "采购.pdf").write_bytes(b"%PDF")
            moved = ingest._setup_case(case)
            self.assertEqual(sorted(moved), ["借条.txt", "合同"])
            self.assertTrue((case / "原始资料" / "借条.txt").is_file())
            self.assertTrue((case / "原始资料" / "合同" / "采购.pdf").is_file())
            self.assertFalse((case / "借条.txt").exists())

    def test_idempotent_when_raw_exists(self):
        with tempfile.TemporaryDirectory() as td:
            case = Path(td)
            raw = case / "原始资料"; raw.mkdir()
            (raw / "已在里面.txt").write_text("x", encoding="utf-8")
            (case / "新扔的.txt").write_text("y", encoding="utf-8")  # 不该被动
            self.assertEqual(ingest._setup_case(case), [])
            self.assertTrue((case / "新扔的.txt").is_file())        # 原地不动
            self.assertFalse((raw / "新扔的.txt").exists())

    def test_reserved_and_hidden_not_moved(self):
        with tempfile.TemporaryDirectory() as td:
            case = Path(td)
            (case / "料.txt").write_text("x", encoding="utf-8")
            for name in ("_md", "wiki", ".git"):
                (case / name).mkdir()
            (case / "AGENTS.md").write_text("a", encoding="utf-8")
            moved = ingest._setup_case(case)
            self.assertEqual(moved, ["料.txt"])
            for name in ("_md", "wiki", ".git", "AGENTS.md"):
                self.assertTrue((case / name).exists())            # 保留/隐藏原地
            self.assertFalse((case / "原始资料" / "_md").exists())

    def test_empty_case_creates_empty_raw(self):
        with tempfile.TemporaryDirectory() as td:
            case = Path(td)
            self.assertEqual(ingest._setup_case(case), [])
            self.assertTrue((case / "原始资料").is_dir())


if __name__ == "__main__":
    unittest.main()
