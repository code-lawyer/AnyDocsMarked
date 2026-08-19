# -*- coding: utf-8 -*-
"""install.py 回归测试（stdlib unittest，零依赖）。

锁住 LAWIKI-RAG-001 复盘出的两个坑：
① _check_offline 的旧版本只查 bundle 内某份源码副本"看起来"带没带模型，不是
  lawiki 实际会调用的那个运行实例——git 装的实例永远拿不到 vendored 模型（见
  RAG索引构建失败-问题报告.md 4.3 节）。现在改成对实际实例跑真实探针，这里锁
  探针的纯逻辑分支（未装/超时/非零退出/成功），不依赖真跑 fastembed。
② 旧版 rag_available 检查硬编码字面量 "rag-retriever"，不读 LAWIKI_RAG_CMD，
  于是设了覆盖也测不到——同一类"测错实例"的坑，_rag_cmd() 统一了两处。
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
import install  # noqa: E402


class RagCmdTests(unittest.TestCase):
    def setUp(self):
        self._saved = install.os.environ.pop("LAWIKI_RAG_CMD", None)

    def tearDown(self):
        if self._saved is not None:
            install.os.environ["LAWIKI_RAG_CMD"] = self._saved

    def test_default_is_bare_command(self):
        self.assertEqual(install._rag_cmd(), ["rag-retriever"])

    def test_override_is_shlex_split(self):
        install.os.environ["LAWIKI_RAG_CMD"] = 'uv run --project "C:/a b/rag" rag-retriever'
        self.assertEqual(
            install._rag_cmd(),
            ["uv", "run", "--project", "C:/a b/rag", "rag-retriever"],
        )


class ProbeEmbedOfflineTests(unittest.TestCase):
    """monkeypatch subprocess.run：验证 _probe_embed_offline 的分支逻辑，不真跑 fastembed。"""

    def _patch_run(self, fn):
        orig = install.subprocess.run
        install.subprocess.run = fn
        self.addCleanup(lambda: setattr(install.subprocess, "run", orig))

    def test_missing_binary_reports_not_installed(self):
        def fake_run(cmd, **kw):
            raise FileNotFoundError()
        self._patch_run(fake_run)
        ok, detail = install._probe_embed_offline(["rag-retriever"])
        self.assertFalse(ok)
        self.assertIn("未安装", detail)

    def test_timeout_reports_diagnosable_message(self):
        def fake_run(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 60))
        self._patch_run(fake_run)
        ok, detail = install._probe_embed_offline(["rag-retriever"])
        self.assertFalse(ok)
        self.assertIn("60 秒", detail)

    def test_nonzero_exit_surfaces_stderr(self):
        def fake_run(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="ConnectTimeout: boom")
        self._patch_run(fake_run)
        ok, detail = install._probe_embed_offline(["rag-retriever"])
        self.assertFalse(ok)
        self.assertEqual(detail, "ConnectTimeout: boom")

    def test_nonzero_exit_both_streams_empty_has_fallback(self):
        def fake_run(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
        self._patch_run(fake_run)
        ok, detail = install._probe_embed_offline(["rag-retriever"])
        self.assertFalse(ok)
        self.assertIn("1", detail)  # 退出码可见，不是空字符串

    def test_success(self):
        def fake_run(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 0, stdout='{"files_indexed": 1}', stderr="")
        self._patch_run(fake_run)
        ok, detail = install._probe_embed_offline(["rag-retriever"])
        self.assertTrue(ok)
        self.assertEqual(detail, "")

    def test_zero_indexed_with_exit_zero_is_not_a_success(self):
        def fake_run(cmd, **kw):
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout='{"files_seen": 1, "files_indexed": 0, "files_skipped": 1, '
                       '"skipped": [{"reason": "embedding failed"}]}',
                stderr="1 file(s) skipped",
            )
        self._patch_run(fake_run)

        ok, detail = install._probe_embed_offline(["rag-retriever"])

        self.assertFalse(ok)
        self.assertIn("0", detail)

    def test_forces_hf_hub_offline_env_and_uses_given_rag_cmd(self):
        # 真断网逼真：没有 vendored 模型时立刻报错而非挂起等超时。同时验证
        # 探针跑的是调用方传入的 rag_cmd（尊重 LAWIKI_RAG_CMD），不是硬编码。
        captured = {}

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            captured["env"] = kw.get("env")
            return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

        self._patch_run(fake_run)
        install._probe_embed_offline(["uv", "run", "rag-retriever"])
        self.assertEqual(captured["env"].get("HF_HUB_OFFLINE"), "1")
        self.assertEqual(captured["cmd"][:2], ["uv", "run"])
        self.assertIn("index", captured["cmd"])


class MainExitCodeTests(unittest.TestCase):
    def test_missing_uv_is_environment_error(self):
        with mock.patch.object(install, "_have", return_value=False):
            self.assertEqual(install.main(["install.py"]), 2)

    def test_component_install_failure_returns_nonzero(self):
        with tempfile.TemporaryDirectory() as td:
            vendor = Path(td)
            (vendor / "makeitdown").mkdir()
            (vendor / "rag-retriever").mkdir()
            with (mock.patch.object(install, "VENDOR", vendor),
                  mock.patch.object(install, "_have", return_value=True),
                  mock.patch.object(install, "_uv_install", return_value=False),
                  mock.patch.object(install, "_verify", return_value=False)):
                self.assertEqual(install.main(["install.py"]), 1)


    def test_successful_dry_run_returns_zero(self):
        with tempfile.TemporaryDirectory() as td:
            vendor = Path(td)
            (vendor / "makeitdown").mkdir()
            (vendor / "rag-retriever").mkdir()
            with (mock.patch.object(install, "VENDOR", vendor),
                  mock.patch.object(install, "_have", return_value=True),
                  mock.patch.object(install, "_uv_install", return_value=True)):
                self.assertEqual(install.main(["install.py", "--dry-run"]), 0)

    def test_successful_install_with_broken_entry_points_returns_nonzero(self):
        with tempfile.TemporaryDirectory() as td:
            vendor = Path(td)
            (vendor / "makeitdown").mkdir()
            (vendor / "rag-retriever").mkdir()
            with (mock.patch.object(install, "VENDOR", vendor),
                  mock.patch.object(install, "_have", return_value=True),
                  mock.patch.object(install, "_uv_install", return_value=True),
                  mock.patch.object(install, "_verify", return_value=False)):
                self.assertEqual(install.main(["install.py"]), 1)


class InstalledCommandTests(unittest.TestCase):
    def test_explicit_uv_tool_bin_is_used_when_path_is_sanitized(self):
        with tempfile.TemporaryDirectory() as td:
            command = Path(td) / "makeitdown.exe"
            command.write_bytes(b"")
            with (mock.patch.dict(install.os.environ, {"UV_TOOL_BIN_DIR": td}),
                  mock.patch.object(install.shutil, "which", return_value=None)):
                self.assertEqual(install._installed_command("makeitdown"), [str(command)])


class AnswerGateReadyTests(unittest.TestCase):
    def _settings(self, td, obj):
        p = Path(td) / "settings.json"
        p.write_text(json.dumps(obj), encoding="utf-8")
        return p

    def test_ready_when_stop_hook_configured(self):
        with tempfile.TemporaryDirectory() as td:
            p = self._settings(td, {"hooks": {"Stop": [{"hooks": [
                {"type": "command", "command": 'python "X/lint/stop_hook.py"'}]}]}})
            self.assertTrue(install._check_answer_gate_ready([p]))

    def test_not_ready_when_hook_absent(self):
        with tempfile.TemporaryDirectory() as td:
            p = self._settings(td, {"hooks": {}})
            self.assertFalse(install._check_answer_gate_ready([p]))

    def test_not_ready_when_no_file(self):
        # best-effort：读不到文件不抛异常，按未就绪处理。
        self.assertFalse(install._check_answer_gate_ready([Path("/nope/settings.json")]))

    def test_stop_hook_string_outside_stop_hook_is_not_ready(self):
        # 结构化匹配：'stop_hook' 出现在非 Stop 钩子里不算就绪（旧的整串 grep 会误报）。
        with tempfile.TemporaryDirectory() as td:
            p = self._settings(td, {"hooks": {"PreToolUse": [{"hooks": [
                {"type": "command", "command": 'python "X/lint/stop_hook.py"'}]}]}})
            self.assertFalse(install._check_answer_gate_ready([p]))

    def test_unreadable_json_is_not_ready_not_raise(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "settings.json"
            p.write_text("{not json", encoding="utf-8")
            self.assertFalse(install._check_answer_gate_ready([p]))


if __name__ == "__main__":
    unittest.main()
