#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""源级对账（ingest 收尾单独一步，独立于 lint check）。

把 原始资料/ 全量 与 _md/report.json 对齐，暴露"转换失败 / 跳过、从未进入
_md/"的源文件——这些正是 lint 覆盖率账本（只看 _md/）结构上看不见的盲点。
三态账本，镜像覆盖率：已产出 / 已登记跳过 / 未处置源级遗漏。

- 复用 lint._load_skips 解析 wiki/log.md 的 skip 条目（DRY，同一个账本文件）。
- 源级遗漏用 `原始资料/<rel>` 路径登记，与 _md 级用 `_md/<rel>` 对称、互不冲突：
  覆盖率只匹配 _md/*，reconcile 只匹配 原始资料/*，同一 log.md 各取所需。
- 未处置源级遗漏 > 0 → 退出码非 0；覆盖率告警同样会阻断完成。源级盲点在别处
  完全不可见，必须显式处置——补转，或在 log.md 登记 skip 并告知用户。

用法：python reconcile.py <案件根目录>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# 复用 lint 的 log.md 解析与路径归一（DRY；两者同属本 skill、同一个覆盖率账本）。
_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE.parent / "lint"))
from lint import _load_skips, _posix  # noqa: E402

SOURCE_DIR = "原始资料"

# OS-generated artifacts that appear in a folder AFTER makeitdown has already
# run (opening it in Explorer/Finder) — never seen by makeitdown, so counting
# them would false-flag every such case as "源多于已处理" on files makeitdown
# could never have processed.
_OS_JUNK_NAMES = {"Thumbs.db", "desktop.ini", ".DS_Store"}


def reconcile(root: Path) -> tuple[list[str], dict[str, int]]:
    """返回 (未处置告警列表, 统计)。纯函数，便于测试。
    统计键：produced / registered / unresolved / source_total / accounted。"""
    stats: dict = {"produced": 0, "registered": 0, "unresolved": 0,
                   "source_total": 0, "accounted": 0}
    unresolved: list[str] = []
    # 结构化的逐个未处置源文件路径（原始资料/rel），供 GUI 直接读、免解析人读散文。
    # 只含单文件遗漏，不含"源多于已处理"这类汇总。
    unresolved_files: list[str] = []

    report_path = root / "_md" / "report.json"
    if not report_path.is_file():
        raise FileNotFoundError(
            f"找不到 {report_path}（先在案件目录跑：makeitdown 原始资料 -o _md）")
    report = json.loads(report_path.read_text(encoding="utf-8"))

    # 源级非产出 = 转换失败 + 跳过（skipped_unsupported）：从未变成 _md 的源文件。
    non_produced = ([e["file"] for e in report.get("failures", [])] +
                    [e["file"] for e in report.get("skipped", [])])
    skips = _load_skips(root)  # {posix 路径: 是否带非空原因}
    for rel in non_produced:
        key = _posix(f"{SOURCE_DIR}/{rel}")
        if skips.get(key):
            stats["registered"] += 1
        else:
            stats["unresolved"] += 1
            label = "跳过无原因" if key in skips else "未处置源级遗漏"
            unresolved.append(f"[{label}] {SOURCE_DIR}/{rel}")
            unresolved_files.append(_posix(f"{SOURCE_DIR}/{rel}"))

    # 已产出 = 成功 + 质检警告 + 增量跳过（上轮已产出 _md，本轮 skip_existing）。
    stats["produced"] = (report.get("succeeded", 0) + report.get("warned", 0)
                         + report.get("skipped_existing", 0))
    stats["accounted"] = (stats["produced"] + report.get("failed", 0)
                          + report.get("skipped_unsupported", 0))

    # 计数兜底：原始资料/ 文件数应 == report 已处理总数；源多于已处理 → 有文件在
    # 上次转换后新增、makeitdown 从未见过（report 与覆盖率都看不见它）。
    src_dir = root / SOURCE_DIR
    if src_dir.is_dir():
        stats["source_total"] = sum(1 for p in src_dir.rglob("*")
                                    if p.is_file() and p.name not in _OS_JUNK_NAMES)
        if stats["source_total"] > stats["accounted"]:
            stats["unresolved"] += 1
            unresolved.append(
                f"[源多于已处理] 原始资料/ 有 {stats['source_total']} 个文件，"
                f"report.json 仅记录 {stats['accounted']} 个——请重跑 makeitdown 原始资料 -o _md。")
    stats["unresolved_files"] = unresolved_files
    return unresolved, stats


def main(argv: list[str]) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 重定向默认 GBK
    except Exception:
        pass
    if not argv:
        print("用法：python reconcile.py <案件根目录>", file=sys.stderr)
        return 2
    root = Path(argv[0])
    try:
        unresolved, stats = reconcile(root)
    except FileNotFoundError as e:
        print(f"错误：{e}", file=sys.stderr)
        return 2
    print(f"源级对账：{stats['source_total']} 源文件 | 已产出 {stats['produced']} | "
          f"已登记跳过 {stats['registered']} | 未处置 {stats['unresolved']}")
    for w in unresolved:
        print(w)
    return 1 if stats["unresolved"] else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
