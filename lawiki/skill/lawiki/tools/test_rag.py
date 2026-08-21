# -*- coding: utf-8 -*-
"""rag wrapper 回归测试（stdlib unittest，零依赖，任何 python 可跑）。

锁住纯逻辑：锚点拼装、quality→未核验、模型一致性判定、当前模型解析。
关键一条：wrapper 拼出的锚点喂给**真实 lint** 必须 0 违规——证明问答引用机器可校验。
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent / "lint"))

import rag  # noqa: E402
from lint import scan_case  # noqa: E402


class BuildAnchorTests(unittest.TestCase):
    def test_plain_anchor(self):
        self.assertEqual(
            rag.build_anchor("_md/a.md", "双方于 2021 年签约"),
            "〔来源: _md/a.md：「双方于 2021 年签约」〕",
        )

    def test_suspect_appends_unverified(self):
        a = rag.build_anchor("_md/a.md", "金额 5 万元", quality="suspect")
        self.assertTrue(a.endswith("」〕（未核验）"))

    def test_non_suspect_quality_no_suffix(self):
        a = rag.build_anchor("_md/a.md", "x", quality="clean")
        self.assertFalse(a.endswith("（未核验）"))


class EnrichHitTests(unittest.TestCase):
    def test_enriched_anchor_passes_real_lint(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            src = "本案欠款金额为人民币 50000 元，借款人为张三。"
            (root / "_md").mkdir(parents=True)
            (root / "_md" / "借条.md").write_text(src, encoding="utf-8")

            hit = {"source": "_md/借条.md",
                   "text": "欠款金额为人民币 50000 元", "metadata": {}}
            enriched = rag.enrich_hit(hit)

            (root / "wiki").mkdir()
            (root / "wiki" / "p.md").write_text(
                f"- 事实 {enriched['anchor']}\n", encoding="utf-8")

            total, violations, warnings, _ = scan_case(root)
            self.assertEqual(violations, [], msg=str(violations))
            self.assertEqual(total, 1)

    def test_anchor_from_multiline_chunk_is_single_line_and_lint_recognized(self):
        # rag chunks carry the whole file (frontmatter + newlines). lint anchors
        # are single-line (ANCHOR_RE's . does not cross \n), so a multi-line
        # snippet would be silently UN-recognized. The default anchor must be a
        # single line and still locate verbatim in the source.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            src = "---\nquality: suspect\n---\n本案欠款金额为人民币 50000 元，借款人为张三。"
            (root / "_md").mkdir(parents=True)
            (root / "_md" / "借条.md").write_text(src, encoding="utf-8")

            hit = {"source": "_md/借条.md", "text": src,
                   "metadata": {"quality": "suspect"}}
            enriched = rag.enrich_hit(hit)
            self.assertNotIn("\n", enriched["anchor"])

            (root / "wiki").mkdir()
            (root / "wiki" / "p.md").write_text(
                f"- 事实 {enriched['anchor']}\n", encoding="utf-8")
            total, violations, *_ = scan_case(root)
            self.assertEqual(total, 1, "锚点未被 lint 识别（多行？）")
            self.assertEqual(violations, [], msg=str(violations))

    def test_suspect_hit_flagged_unverified(self):
        hit = {"source": "_md/a.md", "text": "x", "metadata": {"quality": "suspect"}}
        enriched = rag.enrich_hit(hit)
        self.assertTrue(enriched["unverified"])
        self.assertTrue(enriched["anchor"].endswith("（未核验）"))

    def test_breadcrumb_prefix_stripped_anchor_passes_real_lint(self):
        # 结构分块的命中 text 带标题面包屑前缀（rag-retriever pipeline._compose），
        # 而源文件里两个标题之间隔着正文——面包屑不是连续文本，直接进锚点必挂 lint。
        # enrich_hit 须按 metadata.heading_path 剥前缀后再取默认片段。
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            src = ("---\nsource: 判决书.pdf\n---\n# 民事判决书\n\n"
                   "（2023）京0105民初12345号\n\n## 本院认为\n\n"
                   "本院认为，被告应向原告偿还借款本金人民币 50000 元。\n")
            (root / "_md").mkdir(parents=True)
            (root / "_md" / "判决书.md").write_text(src, encoding="utf-8")

            hit = {"source": "_md/判决书.md",
                   "text": ("民事判决书 > 本院认为\n\n"
                            "本院认为，被告应向原告偿还借款本金人民币 50000 元。"),
                   "metadata": {"heading_path": "民事判决书 > 本院认为"}}
            enriched = rag.enrich_hit(hit)

            (root / "wiki").mkdir()
            (root / "wiki" / "p.md").write_text(
                f"- 事实 {enriched['anchor']}\n", encoding="utf-8")
            total, violations, *_ = scan_case(root)
            self.assertEqual(total, 1)
            self.assertEqual(violations, [], msg=str(violations))

    def test_heading_path_absent_snippet_unchanged(self):
        hit = {"source": "_md/a.md", "text": "正文片段", "metadata": {}}
        self.assertIn("「正文片段」", rag.enrich_hit(hit)["anchor"])

    def test_heading_path_prefix_mismatch_not_stripped(self):
        # heading_path 存在但 text 不以它开头（防御边界）——不剥、不崩。
        hit = {"source": "_md/a.md", "text": "正文片段",
               "metadata": {"heading_path": "别的标题"}}
        self.assertIn("「正文片段」", rag.enrich_hit(hit)["anchor"])


class ModelStatusTests(unittest.TestCase):
    # stats supplies BOTH index-time and live query model; wrapper only compares.
    def test_ok_when_models_match(self):
        ok, _ = rag.model_status({
            "index_backend": "local", "index_model": "m",
            "query_backend": "local", "query_model": "m"})
        self.assertTrue(ok)

    def test_not_indexed_when_index_model_none(self):
        ok, reason = rag.model_status({
            "index_backend": None, "index_model": None,
            "query_backend": "local", "query_model": "m"})
        self.assertFalse(ok)
        self.assertIn("索引", reason)

    def test_mismatch_detected(self):
        ok, reason = rag.model_status({
            "index_backend": "local", "index_model": "old",
            "query_backend": "ollama", "query_model": "new"})
        self.assertFalse(ok)
        self.assertIn("不一致", reason)


class ProcErrorTests(unittest.TestCase):
    # 真实事故（LAWIKI-RAG-001）：embedding 下载超时时子进程非零退出，但 stderr/
    # stdout 都捕不到内容——旧版 (stderr or stdout).strip() 直接返回 ""，排障时
    # 看不到任何有效信息。锁住"恒非空、带退出码"的兜底。
    def _proc(self, returncode, stdout="", stderr=""):
        return subprocess.CompletedProcess(
            args=["rag-retriever"], returncode=returncode, stdout=stdout, stderr=stderr)

    def test_stderr_used_when_present(self):
        detail = rag._proc_error(self._proc(1, stderr="ConnectTimeout: ..."))
        self.assertEqual(detail, "ConnectTimeout: ...")

    def test_stdout_used_when_stderr_empty(self):
        detail = rag._proc_error(self._proc(1, stdout="traceback on stdout"))
        self.assertEqual(detail, "traceback on stdout")

    def test_both_empty_yields_diagnosable_fallback_not_blank(self):
        detail = rag._proc_error(self._proc(1))
        self.assertNotEqual(detail.strip(), "")
        self.assertIn("1", detail)  # 退出码可见


class _PatchRagRunMixin:
    """Swap rag.subprocess.run for a fake, auto-restored via addCleanup — same
    shape as test_install.py's _patch_run, reused here instead of each test
    hand-rolling its own try/finally save-and-restore."""

    def _patch_run(self, fn):
        orig = rag.subprocess.run
        rag.subprocess.run = fn
        self.addCleanup(lambda: setattr(rag.subprocess, "run", orig))


class RunRagDecodeSafetyTests(_PatchRagRunMixin, unittest.TestCase):
    # real subprocess.run(..., encoding="utf-8") without errors="replace" raises
    # UnicodeDecodeError on non-UTF-8 bytes (e.g. a Windows OS error message in
    # the system codepage), which would abort index_case before it can even
    # build a reason. errors="replace" is what keeps that path from crashing.
    def test_run_rag_passes_replace_errors_to_subprocess(self):
        captured = {}

        def fake_run(cmd, **kw):
            captured.update(kw)
            return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

        self._patch_run(fake_run)
        rag._run_rag(Path("/tmp/x"), ["stats"])
        self.assertEqual(captured.get("errors"), "replace")


class AnswerEnvFromCaseTests(unittest.TestCase):
    """answer 期旋钮由工具确定性兑现：rag.py 读 <case>/.anydocsmarked/case.json，
    把用户在 GUI 选的 rerank/min_score 映射成 RAG_* —— 不靠 agent 记得 export。"""

    def _write(self, case: Path, obj: dict) -> None:
        d = case / ".anydocsmarked"
        d.mkdir(parents=True, exist_ok=True)
        (d / "case.json").write_text(json.dumps(obj), encoding="utf-8")

    def test_maps_known_keys(self):
        with tempfile.TemporaryDirectory() as td:
            case = Path(td)
            self._write(case, {"rerank": True, "min_score": 0.3, "embed_backend": "ollama"})
            self.assertEqual(rag.answer_env_from_case(case), {
                "RAG_RERANK": "local", "RAG_MIN_SCORE": "0.3", "RAG_EMBED_BACKEND": "ollama"})

    def test_absent_config_is_empty(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(rag.answer_env_from_case(Path(td)), {})

    def test_rerank_false_omitted(self):
        with tempfile.TemporaryDirectory() as td:
            case = Path(td)
            self._write(case, {"rerank": False})
            self.assertEqual(rag.answer_env_from_case(case), {})

    def test_unreadable_json_is_empty_not_raise(self):
        with tempfile.TemporaryDirectory() as td:
            case = Path(td)
            (case / ".anydocsmarked").mkdir()
            (case / ".anydocsmarked" / "case.json").write_text("{bad", encoding="utf-8")
            self.assertEqual(rag.answer_env_from_case(case), {})


class RunRagInjectsAnswerEnvTests(_PatchRagRunMixin, unittest.TestCase):
    def test_run_rag_injects_case_json_env_into_subprocess(self):
        with tempfile.TemporaryDirectory() as td:
            case = Path(td)
            (case / ".anydocsmarked").mkdir()
            (case / ".anydocsmarked" / "case.json").write_text(
                json.dumps({"rerank": True, "min_score": 0.3}), encoding="utf-8")
            captured = {}

            def fake_run(cmd, **kw):
                captured["env"] = kw.get("env")
                return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

            self._patch_run(fake_run)
            # data_dir = <case>/.rag → wrapper derives case root as data_dir.parent
            rag._run_rag(case / ".rag", ["stats"])
            self.assertEqual(captured["env"]["RAG_RERANK"], "local")
            self.assertEqual(captured["env"]["RAG_MIN_SCORE"], "0.3")

    def test_run_rag_no_case_json_does_not_inject(self):
        with tempfile.TemporaryDirectory() as td:
            case = Path(td)
            captured = {}

            def fake_run(cmd, **kw):
                captured["env"] = kw.get("env")
                return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

            self._patch_run(fake_run)
            rag._run_rag(case / ".rag", ["stats"])
            env = captured["env"] or {}
            self.assertNotIn("RAG_RERANK", env)


class NoticeSurfacingTests(_PatchRagRunMixin, unittest.TestCase):
    # Real incident (LAWIKI-RAG-001): rag-retriever prints a non-fatal heads-up
    # to stderr ("no vendored model, downloading over network") before a slow/
    # failing embedding download. capture_output=True means that text is
    # silently discarded unless the success path explicitly surfaces it.
    def test_with_notice_adds_key_when_stderr_present(self):
        proc = subprocess.CompletedProcess([], 0, stdout="", stderr="heads up\n")
        result = rag._with_notice({"ok": True}, proc)
        self.assertEqual(result["notice"], "heads up")

    def test_with_notice_omits_key_when_stderr_empty(self):
        proc = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        result = rag._with_notice({"ok": True}, proc)
        self.assertNotIn("notice", result)

    def test_index_case_surfaces_notice_on_success(self):
        case = Path(tempfile.mkdtemp())
        (case / "_md").mkdir()

        def fake_run(cmd, **kw):
            return subprocess.CompletedProcess(
                cmd, 0, stdout='{"files_indexed": 1}',
                stderr="[rag-retriever] 未检测到内置 embedding 模型，将尝试联网下载……")

        self._patch_run(fake_run)
        result = rag.index_case(case)
        self.assertTrue(result["ok"])
        self.assertIn("未检测到内置", result["notice"])

    def test_index_case_command_excludes_report_json_and_sets_metadata(self):
        # makeitdown 的台账 report.json 非源文，绝不能进 RAG；quality 元数据须透传。
        # 这是「完整性门不被 report.json 混入而误判」的守护，ingest.py 依赖它。
        case = Path(tempfile.mkdtemp())
        (case / "_md").mkdir()
        captured = {}

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, stdout='{"files_indexed": 1}', stderr="")

        self._patch_run(fake_run)
        rag.index_case(case)
        self.assertIn("--exclude", captured["cmd"])
        self.assertIn("report.json", captured["cmd"])
        self.assertIn("--metadata-fields", captured["cmd"])
        self.assertIn(rag._METADATA_FIELDS, captured["cmd"])

    def test_search_case_surfaces_notice_on_success(self):
        case = Path(tempfile.mkdtemp())
        (case / ".rag").mkdir()

        def fake_run(cmd, **kw):
            if "stats" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({
                    "index_backend": "local", "index_model": "m",
                    "query_backend": "local", "query_model": "m"}), stderr="")
            return subprocess.CompletedProcess(
                cmd, 0, stdout="[]",
                stderr="[rag-retriever] 未检测到内置 embedding 模型，将尝试联网下载……")

        self._patch_run(fake_run)
        result = rag.search_case(case, "问题")
        self.assertTrue(result["rag_available"])
        self.assertIn("未检测到内置", result["notice"])


if __name__ == "__main__":
    unittest.main()
