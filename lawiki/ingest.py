#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AnyDocsMarked 摄入引擎 + CLI 前端（仅标准库，跨平台）。

前半段单入口：脚手架 → makeitdown 转换 → rag 建索引 → 源级对账 → 确定性完整性门，
产出合并 ingest-report.json + 单退出码。门/退出码/report/consent 逻辑为单一来源，
Plan B 的 GUI 直接 import 本模块函数复用（不另写门逻辑）。

用法：
  python ingest.py <案件目录> [--ocr-engine auto|local|cloud] [--cloud-consent]
                   [--workers N] [--skip-existing] [--skip-index] [--dry-run]

约定目录：<案件目录>/原始资料 → /_md（含 report.json）→ /.rag；报告写 /ingest-report.json。

退出码：0 全通过；1 转换有硬失败；2 前置/环境缺失；3 完整性门未过（源级对账未处置>0，
或有 rag 却索引不全）。转换失败优先。边界止于 _md+.rag+门；建 wiki（LLM）与 lint check 不在此。
隐私：不放宽 consent，只透传 --cloud-consent。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_TOOLS = Path(__file__).resolve().parent / "skill" / "lawiki" / "tools"
sys.path.insert(0, str(_TOOLS))
from rag import _rag_base as _rag_cmd  # noqa: E402

EXIT_PASS = 0
EXIT_CONVERT_FAILED = 1
EXIT_PREFLIGHT = 2
EXIT_INCOMPLETE = 3


def _say(msg: str) -> None:
    print(f"[lawiki-ingest] {msg}", flush=True)


def _count_md_files(md_dir: Path) -> int:
    return sum(1 for _ in md_dir.rglob("*.md"))


def _gate_and_merge(case_dir: Path, convert: dict, index: dict, md_file_count: int,
                    reconcile_reasons: list[str], *, index_ran: bool) -> dict:
    reasons: list[str] = []
    exit_code = EXIT_PASS

    failed = convert.get("failed", 0)
    if failed > 0:
        reasons.append(f"转换硬失败 {failed} 个（见 _md/report.json 的 failures）")
        exit_code = EXIT_CONVERT_FAILED

    if index_ran:
        indexed = index.get("files_indexed", 0)
        skipped = index.get("files_skipped", 0)
        index_stage: dict = {"ran": True, "files_indexed": indexed,
                             "files_skipped": skipped, "md_files": md_file_count}
        if skipped > 0:
            reasons.append(f"{skipped} 个文件被检索器跳过、未入索引")
        if indexed < md_file_count:
            reasons.append(f"{md_file_count - indexed} 个 _md 未进入 .rag 索引")
        if (skipped > 0 or indexed < md_file_count) and exit_code == EXIT_PASS:
            exit_code = EXIT_INCOMPLETE
    else:
        index_stage = {"ran": False, "md_files": md_file_count}

    if reconcile_reasons:
        reasons.extend(reconcile_reasons)
        if exit_code == EXIT_PASS:
            exit_code = EXIT_INCOMPLETE

    return {
        "case_dir": str(case_dir),
        "stages": {
            "convert": {k: convert.get(k, 0) for k in (
                "succeeded", "warned", "failed",
                "skipped_existing", "skipped_unsupported")},
            "index": index_stage,
            "source_reconcile": {"unresolved": reconcile_reasons},
        },
        "gate": {"passed": exit_code == EXIT_PASS, "reasons": reasons},
        "exit_code": exit_code,
    }
