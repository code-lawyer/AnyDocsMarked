# -*- coding: utf-8 -*-
"""能力接线契约的 CI 门（本方案核心）。

遍历 capabilities.json，断言"承诺相关能力确实接进了 sanctioned 路径、且执行是确定性的
（不委托 agent）"——把过去不可见的编排/GUI/安装接缝变成机器可判：
  FLOOR  → 对应保证点在真实代码里成立（每个 FLOOR 必须有一条具体断言）。
  enforcement → 非空且非 agent（含 "agent" 即失败）。
  CHOICE → 在 GUI 可达（build_gui_fields 非空）；ingest 标志既产得出又被 ingest.py 接受。
  answer CHOICE → 其 RAG_* 环境变量在 setup.md / SKILL.md 有文档。
  OUT    → 带非空 rationale。
  secret → 绝不落盘（不进 build_case_config）。
（三模块 env/flag 完备性核对在 repo 根 tests/ 的跨模块用例里，那里两模块都装了。）
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
from capabilities import answer_persist_map, build_gui_fields, load_capabilities  # noqa: E402

_INGEST_SRC = (_HERE / "ingest.py").read_text(encoding="utf-8")
_SETUP_MD = (_HERE / "skill" / "lawiki" / "references" / "setup.md").read_text(encoding="utf-8")
_SKILL_MD = (_HERE / "skill" / "lawiki" / "SKILL.md").read_text(encoding="utf-8")
_DOCS = _SETUP_MD + _SKILL_MD


def _default_convert_argv():
    return ingest.build_convert_argv(
        Path("raw"), Path("md"), ocr_engine="auto", workers=1,
        cloud_consent=False, skip_existing=False)


# lawiki 侧可执行的接线断言（对活代码，不是读 JSON 自证）。缺失即 KeyError → 失败。
_LAWIKI_FLOOR_CHECKS = {
    "ocr_quality_check": lambda e: e["sanctioned"]["forbid_flag"] not in _default_convert_argv(),
    "source_reconcile": lambda e: "_run_reconcile(" in _INGEST_SRC,
    "answer_gate": lambda e: hasattr(install, "_check_answer_gate_ready"),
}
# 跨模块 FLOOR（owner=rag/makeitdown）：lawiki 不越边界 import，只锁契约声明，真实默认由
# 该模块自身测试套件强制。显式列名而非伪装成"接线检查"。
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
                    self.assertIs(cap["sanctioned"].get("default_on"), True)
                else:
                    self.fail(f"FLOOR {cid} 未登记：既无 lawiki 侧接线断言，也未标为跨模块委托")

    def test_enforcement_present_and_non_agent(self):
        # 每个 FLOOR/CHOICE 必须点名确定性执行点，且不得委托 agent。刻意用最简的亮线规则：
        # enforcement **不得出现 "agent" 一词**——想说"非委托"就直接写具体机制（代码门/
        # 工具注入/文件写入），别提 agent。宁可粗一点也要不可被绕过。
        for cap in self.caps:
            if cap["tier"] == "OUT":
                continue
            with self.subTest(capability=cap["id"]):
                enf = cap.get("enforcement", "")
                self.assertTrue(enf.strip(), f"{cap['id']} 缺 enforcement")
                self.assertNotIn("agent", enf.lower(),
                                 f"{cap['id']} 的 enforcement 委托了 agent：{enf!r}")

    def test_choice_reachable_in_gui(self):
        # 每个 install/ingest/answer 的 CHOICE 都能被 GUI 呈现（有开关或输入字段）。
        for cap in self.caps:
            if cap["tier"] != "CHOICE":
                continue
            with self.subTest(capability=cap["id"]):
                self.assertTrue(build_gui_fields(cap),
                                f"CHOICE {cap['id']} 在 GUI 无从呈现（无 gui_control 也无 inputs）")

    def test_ingest_flag_producible_and_parsed(self):
        parser = ingest._build_parser()
        for cap in self.caps:
            if cap["tier"] != "CHOICE" or cap.get("phase") != "ingest":
                continue
            flag = (cap.get("sanctioned") or {}).get("ingest_flag")
            if not flag:
                continue  # inputs-only（如 OCR 引擎）无 ingest_flag，跳过
            gc = cap["sanctioned"]["gui_control"]
            with self.subTest(capability=cap["id"]):
                self.assertIn(flag, ingest_gui.build_ingest_argv({gc: True}))
                parsed = parser.parse_args(["/case", flag])
                dest = flag.lstrip("-").replace("-", "_")
                self.assertTrue(getattr(parsed, dest), f"{flag} 未被 ingest.py parser 接受")

    def test_answer_choice_documented(self):
        for cap in self.caps:
            if cap["tier"] != "CHOICE" or cap.get("phase") != "answer":
                continue
            env = cap["sanctioned"]["env"]
            with self.subTest(capability=cap["id"]):
                self.assertIn(env, _DOCS, f"答案侧 CHOICE {cap['id']} 的 {env} 未在文档出现")

    def test_secret_inputs_never_persisted(self):
        # 持久化键从契约派生（与 build_case_config / rag.py 同一来源）；secret 绝不在其中。
        persisted_keys = set(answer_persist_map())
        secret_ids = {inp["id"] for cap in self.caps for inp in cap.get("inputs", [])
                      if inp.get("kind") == "secret"}
        self.assertTrue(secret_ids)  # 契约确实有 secret（否则断言空转）
        self.assertTrue(persisted_keys)  # 确实有持久化键（否则断言空转）
        self.assertEqual(secret_ids & persisted_keys, set(),
                         "有 secret 输入落进了持久化键集合")

    def test_out_has_rationale(self):
        for cap in self.caps:
            if cap["tier"] == "OUT":
                with self.subTest(capability=cap["id"]):
                    self.assertTrue(cap.get("rationale", "").strip())

    def test_first_batch_instances_present_and_typed(self):
        by_id = {c["id"]: c for c in self.caps}
        expected = {
            "ocr_engine": ("CHOICE", "ingest"),
            "ocr_cross_check": ("CHOICE", "ingest"),
            "structure_headings": ("CHOICE", "ingest"),
            "rag_parent_context": ("CHOICE", "ingest"),
            "rag_install": ("CHOICE", "install"),
            "ocr_install": ("CHOICE", "install"),
            "answer_gate_install": ("CHOICE", "install"),
            "rag_rerank": ("CHOICE", "answer"),
            "rag_min_score": ("CHOICE", "answer"),
            "unresolved_disposition": ("CHOICE", "ingest"),
            "ocr_rotation": ("FLOOR", "ingest"),
            "answer_gate": ("FLOOR", "answer"),
        }
        for cid, (tier, phase) in expected.items():
            self.assertIn(cid, by_id)
            self.assertEqual(by_id[cid]["tier"], tier)
            self.assertEqual(by_id[cid].get("phase"), phase)


if __name__ == "__main__":
    unittest.main()
