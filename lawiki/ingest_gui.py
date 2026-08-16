#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AnyDocsMarked 摄入 GUI 前端（tkinter，仅标准库）。

薄前端：shell out 到 `ingest.py`（与 agent 同一 CLI），读其 stderr 刷进度条，
进程退出后读 ingest-report.json 呈现结果。不重实现任何门/退出码/consent 逻辑。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_PROGRESS_RE = re.compile(r"^\[(\d+)/(\d+)\]\s+(.)\s+(.*)$")
_GLYPH_STATUS = {"✓": "succeeded", "⚠": "warned", "✗": "failed",
                 "=": "skipped_existing", "→": "skipped_unsupported"}


def parse_progress_line(line: str) -> dict | None:
    m = _PROGRESS_RE.match(line.rstrip("\n"))
    if not m:
        return None
    status = _GLYPH_STATUS.get(m.group(3))
    if status is None:
        return None
    return {"done": int(m.group(1)), "total": int(m.group(2)),
            "status": status, "path": m.group(4)}


def summarize_report(merged: dict) -> str:
    gate = merged.get("gate", {})
    conv = merged.get("stages", {}).get("convert", {})
    idx = merged.get("stages", {}).get("index", {})
    head = "✅ 摄入完成，完整性门 **通过**" if gate.get("passed") else \
           f"⚠ 摄入完成，完整性门 **未通过**（退出码 {merged.get('exit_code')}）"
    lines = [head,
             f"转换：成功 {conv.get('succeeded', 0)} / 警告 {conv.get('warned', 0)} / "
             f"失败 {conv.get('failed', 0)} / 跳过 {conv.get('skipped_unsupported', 0)}",
             ("索引：已建 " + str(idx.get("files_indexed", 0)) + " 个") if idx.get("ran")
             else "索引：未建（未装 rag 或 --skip-index，问答退化仅 wiki）"]
    reasons = gate.get("reasons", [])
    if reasons:
        lines.append("需处理：")
        lines.extend("  • " + r for r in reasons)
    else:
        lines.append("下一步：让 agent 加载 lawiki，对 _md/ 建 wiki。")
    return "\n".join(lines)


def resolve_engine_argv(engine: str, cloud_consent: bool) -> list[str]:
    argv = ["--ocr-engine", engine]
    if cloud_consent:
        argv.append("--cloud-consent")
    return argv


def load_gui_config(path: Path) -> dict:
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"engine": "auto"}
    return {"engine": cfg.get("engine", "auto")}


def save_gui_config(path: Path, cfg: dict) -> None:
    # 只持久化 engine；token 绝不落盘。
    path.write_text(json.dumps({"engine": cfg.get("engine", "auto")},
                               ensure_ascii=False, indent=2), encoding="utf-8")
