# -*- coding: utf-8 -*-
"""能力接线契约的 CI 门（本方案核心）。

遍历 capabilities.json，断言"承诺相关能力确实接进了 sanctioned 路径"——把过去
不可见的编排/GUI/安装接缝变成机器可判：
  FLOOR  → 对应保证点在真实代码里成立（每个 FLOOR 必须有一条具体断言，否则失败，
           逼新增 FLOOR 时补断言）。
  CHOICE(ingest) → GUI argv 含其标志 且 ingest.py 定义了对应 CLI 标志（够得着）。
  CHOICE(answer) → 其 RAG_* 环境变量在 setup.md / SKILL.md 有文档（答案侧 surfaced）。
  OUT    → 带非空 rationale。
"""
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE / "skill" / "lawiki" / "tools"))
import ingest  # noqa: E402
import ingest_gui  # noqa: E402
import install  # noqa: E402
from capabilities import load_capabilities  # noqa: E402

_INGEST_SRC = (_HERE / "ingest.py").read_text(encoding="utf-8")
_SETUP_MD = (_HERE / "skill" / "lawiki" / "references" / "setup.md").read_text(encoding="utf-8")
_SKILL_MD = (_HERE / "skill" / "lawiki" / "SKILL.md").read_text(encoding="utf-8")
_DOCS = _SETUP_MD + _SKILL_MD


def _default_convert_argv():
    return ingest.build_convert_argv(
        Path("raw"), Path("md"), ocr_engine="auto", workers=1,
        cloud_consent=False, skip_existing=False)


# lawiki 侧可执行的接线断言：对活代码做真实断言（不是读 JSON 自证）。
# 缺失即 KeyError → 测试失败（逼补断言，防新 FLOOR 掉缝）。
_LAWIKI_FLOOR_CHECKS = {
    # sanctioned 路径绝不产出关闭质检的标志。
    "ocr_quality_check": lambda e: e["sanctioned"]["forbid_flag"] not in _default_convert_argv(),
    # ingest.main 真的调用了源级对账。
    "source_reconcile": lambda e: "_run_reconcile(" in _INGEST_SRC,
    # 安装器带答案后闸门就绪检测。
    "answer_gate": lambda e: hasattr(install, "_check_answer_gate_ready"),
}
# 跨模块 FLOOR（owner=rag/makeitdown）：lawiki 不越边界 import 它们的 Python，
# **无法在此验证运行时默认**；这里只锁契约声明（default_on=true），真实默认由该模块
# 自身测试套件强制。显式列名而非伪装成"接线检查"，不让绿行虚报覆盖。
_DELEGATED_FLOORS = {"rag_hybrid", "provenance_sha", "ocr_rotation"}


class CapabilityContractTests(unittest.TestCase):
    def setUp(self):
        self.caps = load_capabilities()

    def test_every_floor_wired(self):
        for cap in self.caps:
            if cap["tier"] != "FLOOR":
                continue
            cid = cap["id"]
            with self.subTest(capability=cid):
                if cid in _LAWIKI_FLOOR_CHECKS:
                    self.assertTrue(_LAWIKI_FLOOR_CHECKS[cid](cap), f"FLOOR {cid} 接线未成立")
                elif cid in _DELEGATED_FLOORS:
                    # 声明锁定（非接线验证）：真实默认由 owner 模块自身测试套件强制。
                    self.assertIs(cap["sanctioned"].get("default_on"), True)
                else:
                    self.fail(f"FLOOR {cid} 未登记：既无 lawiki 侧接线断言，也未标为跨模块委托")

    def test_choice_ingest_reachable(self):
        parser = ingest._build_parser()
        for cap in self.caps:
            if cap["tier"] != "CHOICE" or cap.get("phase") != "ingest":
                continue
            s = cap["sanctioned"]
            with self.subTest(capability=cap["id"]):
                # GUI 勾选该 CHOICE → build_ingest_argv 产出其 ingest 标志。
                argv = ingest_gui.build_ingest_argv({"engine": "auto", s["gui_control"]: True})
                self.assertIn(s["ingest_flag"], argv)
                # ingest.py 确实定义了该 CLI 标志（够得着，不是死设置）。
                parsed = parser.parse_args(["/case", s["ingest_flag"]])
                dest = s["ingest_flag"].lstrip("-").replace("-", "_")
                self.assertTrue(getattr(parsed, dest), f"{s['ingest_flag']} 未被 parser 接受")

    def test_choice_answer_documented(self):
        for cap in self.caps:
            if cap["tier"] != "CHOICE" or cap.get("phase") != "answer":
                continue
            env = cap["sanctioned"]["env"]
            with self.subTest(capability=cap["id"]):
                self.assertIn(env, _DOCS, f"答案侧 CHOICE {cap['id']} 的 {env} 未在 setup.md/SKILL.md 文档化")

    def test_out_has_rationale(self):
        for cap in self.caps:
            if cap["tier"] == "OUT":
                with self.subTest(capability=cap["id"]):
                    self.assertTrue(cap.get("rationale", "").strip())

    def test_first_batch_instances_present_and_typed(self):
        by_id = {c["id"]: c for c in self.caps}
        expected = {
            "ocr_cross_check": ("CHOICE", "ingest"),
            "structure_headings": ("CHOICE", "ingest"),
            "rag_parent_context": ("CHOICE", "ingest"),
            "rag_rerank": ("CHOICE", "answer"),
            "rag_min_score": ("CHOICE", "answer"),
            "ocr_rotation": ("FLOOR", "ingest"),
            "answer_gate": ("FLOOR", "answer"),
        }
        for cid, (tier, phase) in expected.items():
            self.assertIn(cid, by_id)
            self.assertEqual(by_id[cid]["tier"], tier)
            self.assertEqual(by_id[cid].get("phase"), phase)


if __name__ == "__main__":
    unittest.main()
