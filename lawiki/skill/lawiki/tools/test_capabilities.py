#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""能力接线契约的 schema 校验测试（纯，仅标准库）。"""
import unittest

from capabilities import build_gui_fields, load_capabilities, parse_capabilities


def _floor():
    return {"id": "f1", "promise": "p", "owner": "lawiki", "tier": "FLOOR",
            "phase": "ingest", "enforcement": "代码门", "sanctioned": {"default_on": True}}


def _choice_ingest():
    return {"id": "c1", "promise": "p", "owner": "makeitdown", "tier": "CHOICE",
            "phase": "ingest", "tradeoff": "慢换准", "enforcement": "makeitdown --x 代码路径",
            "sanctioned": {"ingest_flag": "--x", "gui_control": "x", "default_on": False}}


def _choice_answer():
    return {"id": "c2", "promise": "p", "owner": "rag", "tier": "CHOICE",
            "phase": "answer", "tradeoff": "慢换准", "enforcement": "rag.py 注入 env",
            "sanctioned": {"env": "RAG_X", "default_on": False}}


def _out():
    return {"id": "o1", "promise": "p", "owner": "makeitdown", "tier": "OUT",
            "rationale": "非承诺相关"}


class ParseTests(unittest.TestCase):
    def test_valid_mix_parses(self):
        entries = parse_capabilities([_floor(), _choice_ingest(), _choice_answer(), _out()])
        self.assertEqual({e["id"] for e in entries}, {"f1", "c1", "c2", "o1"})

    def test_unknown_tier_rejected(self):
        bad = _floor(); bad["tier"] = "MAYBE"
        with self.assertRaises(ValueError):
            parse_capabilities([bad])

    def test_out_without_rationale_rejected(self):
        bad = _out(); bad["rationale"] = ""
        with self.assertRaises(ValueError):
            parse_capabilities([bad])

    def test_choice_without_tradeoff_rejected(self):
        bad = _choice_ingest(); del bad["tradeoff"]
        with self.assertRaises(ValueError):
            parse_capabilities([bad])

    def test_choice_ingest_without_gui_control_rejected(self):
        bad = _choice_ingest(); bad["sanctioned"] = {"ingest_flag": "--x"}
        with self.assertRaises(ValueError):
            parse_capabilities([bad])

    def test_choice_answer_without_env_rejected(self):
        bad = _choice_answer(); bad["sanctioned"] = {"default_on": False}
        with self.assertRaises(ValueError):
            parse_capabilities([bad])

    def test_unknown_owner_rejected(self):
        bad = _floor(); bad["owner"] = "elsewhere"
        with self.assertRaises(ValueError):
            parse_capabilities([bad])

    def test_duplicate_id_rejected(self):
        with self.assertRaises(ValueError):
            parse_capabilities([_floor(), _floor()])

    def test_install_phase_accepted(self):
        c = _choice_ingest(); c["phase"] = "install"
        parse_capabilities([c])  # 不抛

    def test_enforcement_required_for_choice(self):
        bad = _choice_ingest(); del bad["enforcement"]
        with self.assertRaises(ValueError):
            parse_capabilities([bad])

    def test_enforcement_required_for_floor(self):
        bad = _floor(); del bad["enforcement"]
        with self.assertRaises(ValueError):
            parse_capabilities([bad])


class InputsSchemaTests(unittest.TestCase):
    def _with_inputs(self, inputs):
        c = _choice_ingest(); c["inputs"] = inputs
        return c

    def test_valid_inputs_parse(self):
        c = self._with_inputs([
            {"id": "mode", "label": "模式", "kind": "select", "options": ["a", "b"], "flag": "--m"},
            {"id": "tok", "label": "token", "kind": "secret", "env": "TOK",
             "required_when": {"mode": ["a"]}},
        ])
        parse_capabilities([c])  # 不抛

    def test_unknown_input_kind_rejected(self):
        c = self._with_inputs([{"id": "x", "label": "l", "kind": "slider", "env": "X"}])
        with self.assertRaises(ValueError):
            parse_capabilities([c])

    def test_select_without_options_rejected(self):
        c = self._with_inputs([{"id": "x", "label": "l", "kind": "select", "flag": "--x"}])
        with self.assertRaises(ValueError):
            parse_capabilities([c])

    def test_required_when_referencing_unknown_input_rejected(self):
        c = self._with_inputs([{"id": "tok", "label": "l", "kind": "secret", "env": "T",
                                "required_when": {"nope": ["a"]}}])
        with self.assertRaises(ValueError):
            parse_capabilities([c])


class BuildGuiFieldsTests(unittest.TestCase):
    def test_toggle_plus_inputs(self):
        c = _choice_ingest()
        c["inputs"] = [{"id": "tok", "label": "token", "kind": "secret", "env": "T"}]
        fields = build_gui_fields(c)
        ids = [f["id"] for f in fields]
        self.assertEqual(ids, ["x", "tok"])  # gui_control 开关在前，输入随后
        self.assertEqual(fields[0]["kind"], "toggle")
        self.assertEqual(fields[1]["kind"], "secret")


class LoadRealFileTests(unittest.TestCase):
    def test_shipped_contract_is_valid(self):
        # The real capabilities.json must satisfy the schema.
        entries = load_capabilities()
        self.assertTrue(entries)
        ids = {e["id"] for e in entries}
        # first-batch instances the contract must cover
        for required in ("ocr_cross_check", "ocr_rotation", "answer_gate",
                         "structure_headings", "rag_parent_context",
                         "rag_rerank", "rag_min_score"):
            self.assertIn(required, ids)


if __name__ == "__main__":
    unittest.main()
