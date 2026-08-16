# -*- coding: utf-8 -*-
"""ingest.py 回归测试（stdlib unittest，零依赖，镜像 test_install.py）。"""
import json
import subprocess
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


if __name__ == "__main__":
    unittest.main()
