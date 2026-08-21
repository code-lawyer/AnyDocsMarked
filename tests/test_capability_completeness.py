# -*- coding: utf-8 -*-
"""跨模块：能力接线契约的**账本完备性**门。

三模块 (makeitdown / rag-retriever / lawiki) 各自 --list-knobs 自报它读的每个 env / CLI
标志；本用例聚合后断言：**每一个旋钮都已在 capabilities.json 登记为 FLOOR/CHOICE/OUT
之一**。新增一个 env/flag 却忘了归类，此门即红——杜绝"又一个决策悄悄逃出账本"（正是
embedding 外传门那类复发）。走 CLI/JSON 的跨模块契约，不从 lawiki grep 别人源码。
"""
import json
import sys
from pathlib import Path

from makeitdown.cli import _list_knobs_json as mk_knobs
from rag_retriever.cli import _list_knobs_json as rag_knobs

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "lawiki" / "skill" / "lawiki" / "tools"))
from capabilities import contract_knobs, load_capabilities  # noqa: E402

def test_every_engine_knob_is_registered_in_contract():
    # --list-knobs 与 argparse 内建 -h/--help 由各 reporter 在源头滤掉（"什么算旋钮"单处
    # 定义），这里不再各维护一份忽略集。
    covered = contract_knobs(load_capabilities())
    reported: set[str] = set()
    for blob in (mk_knobs(), rag_knobs()):
        d = json.loads(blob)
        reported |= set(d["flags"]) | set(d["env"])
    missing = sorted(reported - covered)
    assert not missing, (
        "以下引擎旋钮未在 capabilities.json 登记（须归类 FLOOR/CHOICE/OUT）：" + ", ".join(missing))
