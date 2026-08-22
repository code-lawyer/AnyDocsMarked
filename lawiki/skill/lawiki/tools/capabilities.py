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
_PHASES = {"install", "ingest", "answer"}
_INPUT_KINDS = {"text", "secret", "select", "bool"}


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

        # FLOOR / CHOICE：须有承诺、合法 phase、确定性执行点。
        _nonempty(entry, "promise")
        _nonempty(entry, "enforcement")  # 每个决策必须点名非 agent 的确定性执行点
        sanctioned = entry.get("sanctioned") or {}
        if not isinstance(sanctioned, dict):
            raise ValueError(f"能力 {cid}: sanctioned 须为对象")
        phase = entry.get("phase", "ingest")
        if phase not in _PHASES:
            raise ValueError(f"能力 {cid}: phase 须为 {_PHASES}，得到 {phase!r}")
        _validate_inputs(entry)

        if tier == "CHOICE":
            _nonempty(entry, "tradeoff")
            gui_control = (sanctioned.get("gui_control") or "").strip()
            has_inputs = bool(entry.get("inputs"))
            if phase in ("ingest", "install") and not gui_control and not has_inputs:
                raise ValueError(
                    f"能力 {cid}: CHOICE(phase={phase}) 须有 gui_control 开关或 inputs 之一"
                    "（否则 GUI 无从呈现）")
            if phase == "answer" and not (sanctioned.get("env") or "").strip():
                raise ValueError(f"能力 {cid}: CHOICE(phase=answer) 须有 sanctioned.env")
    return list(raw)


def _validate_inputs(entry: dict) -> None:
    """校验可选的 inputs（GUI 需向用户收集的字段）。"""
    inputs = entry.get("inputs")
    if inputs is None:
        return
    cid = entry.get("id", "?")
    if not isinstance(inputs, list):
        raise ValueError(f"能力 {cid}: inputs 须为列表")
    ids = {inp.get("id") for inp in inputs if isinstance(inp, dict)}
    for inp in inputs:
        if not isinstance(inp, dict):
            raise ValueError(f"能力 {cid}: 每个 input 须为对象")
        _nonempty(inp, "id")
        _nonempty(inp, "label")
        kind = inp.get("kind")
        if kind not in _INPUT_KINDS:
            raise ValueError(f"能力 {cid}: input {inp.get('id')} kind 须为 {_INPUT_KINDS}")
        if kind == "select" and not (isinstance(inp.get("options"), list) and inp["options"]):
            raise ValueError(f"能力 {cid}: select 输入 {inp['id']} 须带非空 options")
        if not (inp.get("env") or inp.get("flag")):
            raise ValueError(f"能力 {cid}: input {inp['id']} 须声明 env 或 flag（值落到哪）")
        rw = inp.get("required_when")
        if rw is not None:
            if not isinstance(rw, dict):
                raise ValueError(f"能力 {cid}: input {inp['id']} required_when 须为对象")
            for ref in rw:
                if ref not in ids:
                    raise ValueError(
                        f"能力 {cid}: input {inp['id']} required_when 引用了不存在的输入 {ref!r}")


def contract_knobs(caps: list[dict]) -> set[str]:
    """契约账本里被交代过的所有旋钮名（env / CLI 标志）。完备性核对用：三模块
    --list-knobs 报出的每个旋钮都必须落在这个集合里，否则说明有决策逃出了账本。
    来源：sanctioned 的 ingest_flag/forbid_flag/env、每个 input 的 flag/env、OUT 的 knobs。"""
    knobs: set[str] = set()
    for cap in caps:
        s = cap.get("sanctioned") or {}
        for key in ("ingest_flag", "forbid_flag", "env"):
            if s.get(key):
                knobs.add(s[key])
        for inp in cap.get("inputs", []):
            for key in ("flag", "env"):
                if inp.get(key):
                    knobs.add(inp[key])
        for kn in cap.get("knobs", []):
            knobs.add(kn)
    return knobs


def answer_persist_map() -> dict[str, str]:
    """答案期需持久化到 <case>/.anydocsmarked/case.json 的选择 → rag.py 注入的 RAG_* 环境
    变量。**从契约派生**（`answer_persist:true` 的 gui_control/input），单一来源——写入端
    (build_case_config)、注入端 (rag.answer_env_from_case)、契约测试都读它，不各抄一份键表。
    secret 输入不纳入（绝不落盘）。"""
    m: dict[str, str] = {}
    for cap in load_capabilities():
        s = cap.get("sanctioned") or {}
        if s.get("answer_persist") and s.get("gui_control") and s.get("env"):
            m[s["gui_control"]] = s["env"]
        for inp in cap.get("inputs", []):
            if inp.get("answer_persist") and inp.get("env") and inp.get("kind") != "secret":
                m[inp["id"]] = inp["env"]
    return m


def build_gui_fields(cap: dict) -> list[dict]:
    """把一个 CHOICE 展开成 GUI 该渲染的字段：开关（gui_control）+ 各 input。
    纯函数，GUI 与契约测试共用（契约测试据此断言每个开关/输入都在 GUI 可达）。"""
    fields: list[dict] = []
    gc = (cap.get("sanctioned") or {}).get("gui_control")
    if gc:
        # 开关标签用 promise（说明这个开关做什么）；tradeoff 由 GUI 另起 ⚖ 一行呈现，不重复。
        fields.append({"kind": "toggle", "id": gc, "label": cap.get("promise", "")})
    for inp in cap.get("inputs", []):
        fields.append({"kind": inp["kind"], "id": inp["id"], "label": inp["label"],
                       "options": inp.get("options"), "default": inp.get("default"),
                       "required_when": inp.get("required_when")})
    return fields


@lru_cache(maxsize=None)
def load_capabilities(path: Path | None = None) -> list[dict]:
    """从 capabilities.json 读取并校验。契约文件进程内静态——缓存，避免 GUI 每次
    建屏/每次点击都重读重解析重校验。调用方只读，不改返回列表。"""
    p = path or _DEFAULT_PATH
    data = json.loads(p.read_text(encoding="utf-8"))
    return parse_capabilities(data)
