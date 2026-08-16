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

    def test_index_parses_json(self):
        with mock.patch.object(ingest.subprocess, "run",
                               return_value=_FakeProc(0, stdout='{"files_indexed":3,"files_skipped":0}')):
            result, ran = ingest._run_index(Path("/x/_md"), Path("/x"), Path("/x/.rag"), dry_run=False)
        self.assertTrue(ran); self.assertEqual(result["files_indexed"], 3)

    def test_index_not_installed_degrades(self):
        with mock.patch.object(ingest.subprocess, "run", side_effect=FileNotFoundError()):
            result, ran = ingest._run_index(Path("/x/_md"), Path("/x"), Path("/x/.rag"), dry_run=False)
        self.assertFalse(ran); self.assertIsNone(result)

    def test_index_nonzero_marks_empty(self):
        with mock.patch.object(ingest.subprocess, "run", return_value=_FakeProc(1, stdout="")):
            result, ran = ingest._run_index(Path("/x/_md"), Path("/x"), Path("/x/.rag"), dry_run=False)
        self.assertTrue(ran); self.assertEqual(result, {"files_indexed": 0, "files_skipped": 0})

    def test_reconcile_clean(self):
        with mock.patch.object(ingest.subprocess, "run",
                               return_value=_FakeProc(0, stdout="源级对账：2 源文件 | 已产出 2 | 已登记跳过 0 | 未处置 0")):
            self.assertEqual(ingest._run_reconcile(Path("/x"), dry_run=False), [])

    def test_reconcile_unresolved(self):
        out = ("源级对账：3 源文件 | 已产出 2 | 已登记跳过 0 | 未处置 1\n"
               "[未处置源级遗漏] 原始资料/老合同.doc")
        with mock.patch.object(ingest.subprocess, "run", return_value=_FakeProc(1, stdout=out)):
            reasons = ingest._run_reconcile(Path("/x"), dry_run=False)
        self.assertEqual(reasons, ["[未处置源级遗漏] 原始资料/老合同.doc"])


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


if __name__ == "__main__":
    unittest.main()
