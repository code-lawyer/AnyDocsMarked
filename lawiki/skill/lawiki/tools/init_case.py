#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""确定性建案脚手架（由 SKILL.md 第一步「跑摄入」的引擎幂等自动运行）：把固定的案件结构 + 闭世界锚点
(AGENTS.md/CLAUDE.md) 盖章式写入案件目录，取代手写——手写这一步会被跳过
（问题报告 §10：AGENTS.md/CLAUDE.md 一直没被建）。幂等：只补缺失的，不覆盖
已存在的文件（除非 --force）。lint check 把缺锚点判为硬违规，与本脚本配对：
生成器负责"建"，lint 负责"查存在"。

用法：python init_case.py <案件根目录> [--force]
"""
from __future__ import annotations

import sys
from pathlib import Path

# 闭世界锚点：AGENTS.md 与 CLAUDE.md 内容相同。harness 会自动读工作目录下的
# 这两个文件——是唯一不依赖 skill 是否触发就一定进上下文的护栏。其中的
# 「答前必先检索」是 lint._check_case_files 的 sentinel，生成物必过该检查。
# 与 references/page-formats.md 的模板同源，改一处两处同步。
ANCHOR = """# 本目录是一个 lawiki 法律案件库

由 lawiki skill 构建与维护。任何 agent 或人打开本目录，请按以下约定理解与续作。
**勿手动修改 `原始资料/` 与 `_md/`。**

## 结构（前三层不可变，只写 wiki/）
- `原始资料/` 用户原件，真相之源，永不修改
- `_md/`      makeitdown 转换产物，来源层，永不修改
- `wiki/`     已综合、可溯源的案件 wiki（人读 + agent 读）
- `.rag/`     从 `_md/` 派生的向量库，隐藏、可重建、可删

## 续作方式
1. 加载 lawiki skill（Claude Code/Copilot 按其 SKILL.md 自动识别；Codex 等把 skill 内容作系统指令）。
2. 新增资料：放进 `原始资料/` → 转换出 `_md/` → 索引 `.rag/` → ingest 进 `wiki/` → 跑 lint 校验。
3. 提问（闭世界问答）：**本案事实的唯一来源 = 本目录的 `原始资料/_md/wiki/.rag`**；**答前必先检索（wiki + RAG + outline + grep `_md`），严禁凭记忆/通用法律知识直接回答本案问题**；查不到就明说「未在本案材料中找到」，绝不脑补冒充本案事实；通用分析须标 `> [!note] 分析（非本案证据）`。多路取证 + 四情形分流详见 skill 的 qa.md。

## 铁律（不可违反，全文见 skill）
- 每句事实挂逐字来源锚点 `〔来源: _md/…：「逐字原文」〕`；挂不上锚点的不许当事实写。
- 三类标注：EXTRACTED（原文直取，挂锚点）/ INFERRED（分析，标 `> [!note] 分析`）/ AMBIGUOUS（存疑·冲突·未核验，显式标）。
- 来源不可变；矛盾只暴露不私自调和；要害（日期/金额/姓名/条款）逐字照录。

## 校验
`python <SKILL_DIR>/lint/lint.py check <本目录>` → 修到 0 违规。
"""

INDEX = """# 案件 wiki 索引

> 每次 ingest 后更新：按板块列出页面，每条附一行摘要。

## 案件主体
（暂无）

## 法律关系
（暂无）

## 法律事实
（暂无）

## 时间线
（暂无）
"""

LOG = """# 操作日志

> append-only。每条以 `## [YYYY-MM-DD] <操作> | <对象>` 开头。
"""

WIKI_SUBDIRS = ("案件主体", "法律关系", "法律事实", "时间线")
# case 根下本脚本盖章创建的顶层脚手架（目录 + 锚点文件）。下面的创建逻辑与
# `ingest._RESERVED_CASE_ENTRIES` 都从这里派生——保证"建了什么"与"归入原始资料时
# 绝不移动什么"永远同源、不漂移（新增顶层脚手架只需改这里一处）。
SCAFFOLD_DIRS = ("原始资料", "wiki")
SCAFFOLD_ANCHORS = ("AGENTS.md", "CLAUDE.md")
SCAFFOLD_ENTRIES = frozenset(SCAFFOLD_DIRS) | frozenset(SCAFFOLD_ANCHORS)


def init_case(root: Path, force: bool = False) -> list[str]:
    """建固定结构 + 盖章写闭世界锚点。返回"新建/写入了什么"的说明列表。
    幂等：已存在的文件不覆盖（除非 force）；已存在的目录不动。"""
    created: list[str] = []

    def _ensure_dir(p: Path) -> None:
        if not p.is_dir():
            p.mkdir(parents=True, exist_ok=True)
            created.append(f"建目录 {p.relative_to(root).as_posix()}/")

    def _ensure_file(rel: str, content: str) -> None:
        p = root / rel
        if p.exists() and not force:
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        created.append(f"写文件 {rel}")

    for d in SCAFFOLD_DIRS:
        _ensure_dir(root / d)
    for d in WIKI_SUBDIRS:
        _ensure_dir(root / "wiki" / d)
    _ensure_file("wiki/index.md", INDEX)
    _ensure_file("wiki/log.md", LOG)
    for f in SCAFFOLD_ANCHORS:
        _ensure_file(f, ANCHOR)
    return created


def main(argv: list[str]) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 重定向默认 GBK
    except Exception:
        pass
    positional = [a for a in argv if not a.startswith("-")]
    force = "--force" in argv
    if len(positional) != 1:
        print("用法：python init_case.py <案件根目录> [--force]", file=sys.stderr)
        return 2
    root = Path(positional[0])
    created = init_case(root, force=force)
    if created:
        print(f"案件结构已就绪（{root}）：")
        for c in created:
            print("  + " + c)
    else:
        print(f"案件结构已存在，无需改动（{root}）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
