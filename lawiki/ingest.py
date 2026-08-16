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


def _preflight(raw_dir: Path) -> str | None:
    if not raw_dir.is_dir():
        return f"找不到原始资料目录：{raw_dir}（把待转文件放进 <案件目录>/原始资料/）"
    if not any(p.is_file() for p in raw_dir.rglob("*")):
        return f"原始资料目录为空：{raw_dir}"
    if shutil.which("makeitdown") is None:
        return "未找到 makeitdown（先跑 install.py 安装转换器，或检查 PATH）"
    return None


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


def _atomic_write_text(path: Path, text: str) -> None:
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text); fh.flush(); os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _run_init_case(case_dir: Path, *, dry_run: bool) -> None:
    cmd = [sys.executable, (_TOOLS / "init_case.py").as_posix(), case_dir.as_posix()]
    _say("将执行: " + " ".join(cmd))
    if dry_run:
        return
    subprocess.run(cmd, text=True, encoding="utf-8", errors="replace")


def _run_convert(raw_dir: Path, md_dir: Path, *, ocr_engine: str, cloud_consent: bool,
                 workers: int, skip_existing: bool, dry_run: bool) -> tuple[dict | None, int]:
    cmd = ["makeitdown", raw_dir.as_posix(), "-o", md_dir.as_posix(),
           "--ocr-engine", ocr_engine, "--workers", str(workers)]
    if cloud_consent:
        cmd.append("--cloud-consent")
    if skip_existing:
        cmd.append("--skip-existing")
    _say("将执行: " + " ".join(cmd))
    if dry_run:
        return None, 0
    proc = subprocess.run(cmd, text=True, encoding="utf-8", errors="replace")
    report_path = md_dir / "report.json"
    if report_path.is_file():
        try:
            return json.loads(report_path.read_text(encoding="utf-8")), proc.returncode
        except ValueError:
            return None, proc.returncode
    return None, proc.returncode


def _run_index(md_dir: Path, case_dir: Path, rag_dir: Path, *,
               dry_run: bool) -> tuple[dict | None, bool]:
    cmd = [*_rag_cmd(), "--data-dir", rag_dir.as_posix(), "index", md_dir.as_posix(),
           "--source-root", case_dir.as_posix()]
    _say("将执行: " + " ".join(cmd))
    if dry_run:
        return None, True
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
    except FileNotFoundError:
        _say("⏭ 未安装 rag-retriever，跳过建索引（问答将退化仅 wiki）")
        return None, False
    if proc.returncode != 0:
        _say(f"✗ 建索引失败（退出码 {proc.returncode}）：{(proc.stderr or '').strip()[:200]}")
        return {"files_indexed": 0, "files_skipped": 0}, True
    try:
        return json.loads(proc.stdout), True
    except ValueError:
        _say("✗ 建索引退出 0 但输出非 JSON，视为未完成")
        return {"files_indexed": 0, "files_skipped": 0}, True


def _run_reconcile(case_dir: Path, *, dry_run: bool) -> list[str]:
    cmd = [sys.executable, (_TOOLS / "reconcile.py").as_posix(), case_dir.as_posix()]
    _say("将执行: " + " ".join(cmd))
    if dry_run:
        return []
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode == 0:
        return []
    reasons = [ln for ln in proc.stdout.splitlines()
               if ln.strip() and not ln.startswith("源级对账")]
    return reasons or [f"源级对账未通过（退出码 {proc.returncode}）：{(proc.stderr or '').strip()[:200]}"]


def main(argv: list[str]) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    p = argparse.ArgumentParser(prog="ingest.py", description="AnyDocsMarked 摄入引擎")
    p.add_argument("case_dir", help="案件目录（含 原始资料/ 子目录）")
    p.add_argument("--ocr-engine", choices=["auto", "local", "cloud"], default="auto")
    p.add_argument("--cloud-consent", action="store_true",
                   help="同意把文档/文本发往外部 OCR（透传给 makeitdown；不加则云端被阻断）")
    p.add_argument("--workers", type=int, default=os.cpu_count() or 4)
    p.add_argument("--skip-existing", action="store_true")
    p.add_argument("--skip-index", action="store_true", help="只转换、不建 .rag 索引")
    p.add_argument("--dry-run", action="store_true", help="只打印将执行的命令")
    args = p.parse_args(argv)

    case = Path(args.case_dir).resolve()
    raw, md, rag = case / "原始资料", case / "_md", case / ".rag"

    _run_init_case(case, dry_run=args.dry_run)

    err = _preflight(raw)
    if err and not args.dry_run:
        _say("✗ " + err)
        return EXIT_PREFLIGHT

    convert, rc = _run_convert(
        raw, md, ocr_engine=args.ocr_engine, cloud_consent=args.cloud_consent,
        workers=args.workers, skip_existing=args.skip_existing, dry_run=args.dry_run)

    if args.dry_run:
        _run_index(md, case, rag, dry_run=True)
        _run_reconcile(case, dry_run=True)
        _say("（dry-run：未真正执行，未写 ingest-report.json）")
        return EXIT_PASS

    if convert is None:
        _say(f"✗ 转换未产出 report.json（makeitdown 退出码 {rc}）——"
             f"常见：选了云端 OCR 但未加 --cloud-consent，或输入路径非法。")
        return EXIT_PREFLIGHT

    if args.skip_index:
        index, index_ran = {}, False
    else:
        index, index_ran = _run_index(md, case, rag, dry_run=False)
        index = index or {}

    reconcile_reasons = _run_reconcile(case, dry_run=False)

    merged = _gate_and_merge(case, convert, index, _count_md_files(md),
                             reconcile_reasons, index_ran=index_ran)
    _atomic_write_text(case / "ingest-report.json",
                       json.dumps(merged, ensure_ascii=False, indent=2))

    g = merged["gate"]
    _say(f"—— 摄入结果 —— gate={'通过' if g['passed'] else '未通过'}，退出码 {merged['exit_code']}")
    for reason in g["reasons"]:
        _say("  • " + reason)
    _say(f"报告：{case / 'ingest-report.json'}")
    if g["passed"]:
        _say("下一步：让 agent 加载 lawiki，对 _md/ 建 wiki（LLM 环节，不在本引擎内）。")
    return merged["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
