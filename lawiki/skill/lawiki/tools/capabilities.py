#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""能力接线契约的加载与 schema 校验（纯，仅标准库）。

`capabilities.json` 是"承诺相关能力 → sanctioned 路径接线要求"的单一真值来源。
ingest / GUI / install / 契约测试都经此读取，不各抄一份能力清单。

字段：id / promise / owner(makeitdown|rag|lawiki) / tier(FLOOR|CHOICE|OUT)
     / phase(ingest|answer) / sanctioned{...} / tradeoff(CHOICE) / rationale(OUT)。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_DEFAULT_PATH = Path(__file__).resolve().parent.parent / "capabilities.json"

_OWNERS = {"makeitdown", "rag", "lawiki"}
_TIERS = {"FLOOR", "CHOICE", "OUT"}
_PHASES = {"ingest", "answer"}


def _nonempty(entry: dict, key: str) -> str:
    val = entry.get(key)
    if not isinstance(val, str) or not val.strip():
        raise ValueError(f"能力 {entry.get('id', '?')}: 字段 {key} 必须为非空字符串")
    return val


def parse_capabilities(raw: list) -> list[dict]:
    """校验并返回能力条目列表。任何违规抛 ValueError（契约不允许"随手绕过"）。"""
    if not isinstance(raw, list):
        raise ValueError("capabilities 必须是列表")
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError("每条能力必须是对象")
        cid = _nonempty(entry, "id")
        if cid in seen:
            raise ValueError(f"能力 id 重复：{cid}")
        seen.add(cid)
        owner = _nonempty(entry, "owner")
        if owner not in _OWNERS:
            raise ValueError(f"能力 {cid}: owner 须为 {_OWNERS}，得到 {owner!r}")
        tier = _nonempty(entry, "tier")
        if tier not in _TIERS:
            raise ValueError(f"能力 {cid}: tier 须为 {_TIERS}，得到 {tier!r}")

        if tier == "OUT":
            _nonempty(entry, "rationale")
            continue

        # FLOOR / CHOICE：须有承诺、sanctioned 与合法 phase。
        _nonempty(entry, "promise")
        sanctioned = entry.get("sanctioned")
        if not isinstance(sanctioned, dict):
            raise ValueError(f"能力 {cid}: {tier} 须带 sanctioned 对象")
        phase = entry.get("phase", "ingest")
        if phase not in _PHASES:
            raise ValueError(f"能力 {cid}: phase 须为 {_PHASES}，得到 {phase!r}")

        if tier == "CHOICE":
            _nonempty(entry, "tradeoff")
            if phase == "ingest" and not (sanctioned.get("gui_control") or "").strip():
                raise ValueError(f"能力 {cid}: CHOICE(phase=ingest) 须有 sanctioned.gui_control")
            if phase == "answer" and not (sanctioned.get("env") or "").strip():
                raise ValueError(f"能力 {cid}: CHOICE(phase=answer) 须有 sanctioned.env")
    return list(raw)


@lru_cache(maxsize=None)
def load_capabilities(path: Path | None = None) -> list[dict]:
    """从 capabilities.json 读取并校验。契约文件进程内静态——缓存，避免 GUI 每次
    建屏/每次点击都重读重解析重校验。调用方只读，不改返回列表。"""
    p = path or _DEFAULT_PATH
    data = json.loads(p.read_text(encoding="utf-8"))
    return parse_capabilities(data)
