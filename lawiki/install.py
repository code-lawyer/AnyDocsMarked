#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AnyDocsMarked bundle 安装器（仅标准库，跨平台）。

随 `anydocsmarked` 发布包发布，位于 bundle 根目录。agent 在 setup 阶段跑这一条即可
把三个协作模块装好：lawiki skill（无需安装，加载即用）、makeitdown（转换器）、
rag-retriever（RAG 检索）。后两者从 bundle 内 `vendor/` 的源码本地安装。

用法：
  python install.py [--ocr local|cloud] [--dry-run]
                    [--skip-makeitdown] [--skip-rag]

- `--ocr cloud`（默认）：装云端版（轻、需百度 AI Studio token，见 setup.md）。
  `--ocr local`：装本地 PaddleOCR（离线、不需 token、体积大）。
  两种都应先由 agent 向用户说明并让其选择，绝不静默替用户决定。
- `--dry-run`：只打印将执行的命令，不真正安装。
- embedding 默认 local（fastembed，离线、无需 key）；换 ollama/openai 见 setup.md。
- PyPI 镜像可用 `ANYDOCS_PYPI_INDEX` 环境变量覆盖（语义见 `_pypi_index`）。

设计：每步独立、失败不致命（降级哲学）——makeitdown 装不上仍可用预转的 _md/；
rag-retriever 装不上则问答退化「仅 wiki」。退出码 0 表示请求的能力安装成功或被显式跳过，
1 表示部分安装失败、工作流将降级，2 表示环境或调用错误。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BUNDLE = Path(__file__).resolve().parent
VENDOR = BUNDLE / "vendor"
PYPI_MIRROR = "https://mirrors.aliyun.com/pypi/simple"

# 复用 skill/lawiki/tools/rag.py 的 LAWIKI_RAG_CMD 解析（_rag_base）与错误兜底
# 文案（_proc_error），而不是重新发明一遍——早先两处各写一份，其中 install.py
# 那份的兜底文案更差（"退出码 N，无输出" vs. rag.py 解释了为什么可能没输出、
# 该怎么办）。单向依赖是安全的：build_bundle.py 打包时 install.py（bundle 根）
# 与 skill/lawiki（bundle 内 skill/lawiki/）永远同处一个发布包、从未单独分发
# 过一方；反过来 skill/lawiki 绝不能依赖 install.py——它必须能被 agent 直接
# 加载使用，无需 install.py 在场。
sys.path.insert(0, str(BUNDLE / "skill" / "lawiki" / "tools"))
from rag import _proc_error as _rag_proc_error  # noqa: E402
from rag import _rag_base as _rag_cmd  # noqa: E402


def _say(msg: str) -> None:
    print(f"[lawiki-install] {msg}", flush=True)


def _have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def _pypi_index() -> str | None:
    """镜像源：默认阿里云，可用 ANYDOCS_PYPI_INDEX 覆盖（设为空字符串则不传
    --index，走 uv 自带的默认 pypi.org——某国内镜像抽风/从境外环境安装时都用得上）。
    """
    return os.environ.get("ANYDOCS_PYPI_INDEX", PYPI_MIRROR).strip() or None


def _mirror_hint() -> str:
    idx = _pypi_index()
    return f" -i {idx}" if idx else ""


def _uv_subprocess_env() -> dict[str, str]:
    """去掉 AI 客户端注入的 session-id 环境变量再传给 uv 子进程。

    部分客户端（实测 WorkBuddy 的 CODEBUDDY_SESSION_ID，同类还有 CLAUDE_SESSION_ID）
    的运行时垫片一看到自家 session-id 存在，就会接管 shutil.rmtree 把删除改成"移入
    回收站"；uv 构建 wheel 时清理的临时目录常不在系统 tmp 下，绕开了垫片的豁免判断，
    回收站又常不可用，于是 fail-closed 抛异常、中断安装（需本地编译的包如 jieba/paddle
    首当其冲）。按 `*_SESSION_ID` 模式剥离而非枚举厂商名——下一家同款垫片不必有人再
    踩一次坑；uv/pip 构建对任何客户端会话号都无合法用途。只裁剪这一条子进程的环境，
    不碰全局变量，无副作用。
    """
    return {k: v for k, v in os.environ.items() if not k.endswith("_SESSION_ID")}


def _uv_install(spec: str, dry: bool) -> bool:
    """uv tool install <spec>（走国内镜像加速）。返回是否成功。

    不再钉死某个 Python 版本：makeitdown(>=3.11)、rag-retriever(>=3.10)、
    lawiki lint(>=3.11) 都已放开上限（paddleocr/paddlepaddle 早已支持 3.13+），
    交给 uv 按各包自身的 requires-python 去选/装解释器，客户已有的新版 Python
    不必再为了满足过时的上限被迫降级。
    """
    cmd = ["uv", "tool", "install"]
    idx = _pypi_index()
    if idx:
        cmd += ["--index", idx]
    cmd.append(spec)
    _say("将执行: " + " ".join(cmd))
    if dry:
        return True
    try:
        proc = subprocess.run(cmd, text=True, env=_uv_subprocess_env())
        return proc.returncode == 0
    except FileNotFoundError:
        return False


def _verify(cmd: list[str]) -> bool:
    try:
        return subprocess.run(cmd, capture_output=True).returncode == 0
    except FileNotFoundError:
        return False


def _installed_command(command: str) -> list[str] | None:
    """Resolve a tool entry point, including an explicit uv tool bin directory.

    Some launchers intentionally sanitize PATH for child Python processes. When
    the caller set UV_TOOL_BIN_DIR, the installer can still verify the exact
    executable uv just installed instead of reporting a false failure.
    """
    if found := shutil.which(command):
        return [found]
    if tool_bin := os.environ.get("UV_TOOL_BIN_DIR", "").strip():
        for name in (command, f"{command}.exe"):
            candidate = Path(tool_bin) / name
            if candidate.is_file():
                return [str(candidate)]
    return None


def _probe_embed_offline(rag_cmd: list[str]) -> tuple[bool, str]:
    """跑一次真实的最小 index，验证 lawiki 实际会调用的这个 rag-retriever 运行
    实例能否离线跑通 embedding + 分词——而不是像旧版那样只查 bundle 内某份
    源码副本"看起来"带没带模型。那份副本未必是运行时真正用的那份：`_models/`
    被 `.gitignore` 排除、只在 CI 打 `-offline` 包时才塞进 wheel，`uv tool
    install "rag-retriever @ git+..."` 装的实例永远拿不到（LAWIKI-RAG-001 的
    根因——用户 `--check-offline` 显示 ✓，实际运行时联网超时失败）。

    设 HF_HUB_OFFLINE=1 逼真断网：没有 vendored 模型时会立刻报错而非挂起等
    超时，与用户实测复现所用的方法一致。60 秒超时兜底（正常应几秒内出结果）。
    """
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        md_dir = td_path / "_md"
        md_dir.mkdir()
        (md_dir / "probe.md").write_text(
            "断网就绪自检探针文本，用于验证 embedding 模型与分词表可离线加载。",
            encoding="utf-8",
        )
        cmd = [*rag_cmd, "--data-dir", str(td_path / ".rag"),
               "index", str(md_dir), "--source-root", str(td_path)]
        env = {**os.environ, "HF_HUB_OFFLINE": "1"}
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace",
                                  timeout=60, env=env)
        except FileNotFoundError:
            return False, "未安装 rag-retriever（或不在 PATH / LAWIKI_RAG_CMD）"
        except subprocess.TimeoutExpired:
            return False, "60 秒内未完成——HF_HUB_OFFLINE=1 下不应发生，请人工排查"
        if proc.returncode != 0:
            return False, _rag_proc_error(proc)
        try:
            result = json.loads(proc.stdout)
        except (TypeError, ValueError):
            return False, "index 命令退出 0 但未返回可解析的 JSON，无法确认探针已入索引"
        if result.get("files_indexed") != 1 or result.get("files_skipped", 0) != 0:
            return False, (
                "index 命令退出 0，但探针未完整入索引："
                f"files_indexed={result.get('files_indexed', 0)}, "
                f"files_skipped={result.get('files_skipped', 0)}；"
                f"{_rag_proc_error(proc)}"
            )
        return True, ""


def _live_embed_backend(rag_cmd: list[str]) -> str:
    """问 rag-retriever 自己（`stats` 的 query_backend 字段）当前实际解析出的
    embed 后端，而不是在 install.py 里重新猜一遍 RAG_EMBED_BACKEND 的默认值——
    那样猜的默认值只会在 rag-retriever 的 config.py 改默认值时悄悄和真实配置
    分叉，是"检查的不是运行时真配置"同一类坑（LAWIKI-RAG-001），只是挪到了
    这一层。`stats` 不需要已有索引也能跑；查不到就当 local 处理，真出错交给
    调用方的下一步（探针本身）去报告，这里只是分流依据。
    """
    with tempfile.TemporaryDirectory() as td:
        try:
            proc = subprocess.run([*rag_cmd, "--data-dir", td, "stats"],
                                  capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=15)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return "local"
    try:
        return json.loads(proc.stdout).get("query_backend") or "local"
    except ValueError:
        return "local"


def _default_settings_paths() -> list[Path]:
    """Claude Code settings 候选位置（用户级 + 项目级），best-effort。"""
    home = Path.home()
    cwd = Path.cwd()
    return [home / ".claude" / "settings.json",
            cwd / ".claude" / "settings.json",
            cwd / ".claude" / "settings.local.json"]


def settings_has_stop_hook(data: dict) -> bool:
    """内存中的 settings dict 是否已挂 lawiki stop_hook（hooks.Stop[].hooks[].command 引用
    stop_hook）——按结构匹配而非整串 grep，免得别处偶然出现 'stop_hook' 就误报。检测端
    （_check_answer_gate_ready）与写入端（GUI write_stop_hook 去重）共用此一处，钩子结构
    若变只改这里。"""
    hooks = data.get("hooks") if isinstance(data, dict) else None
    stop_groups = hooks.get("Stop") if isinstance(hooks, dict) else None
    for group in stop_groups or []:
        for hook in (group.get("hooks") or []) if isinstance(group, dict) else []:
            if isinstance(hook, dict) and "stop_hook" in (hook.get("command") or ""):
                return True
    return False


def _check_answer_gate_ready(settings_paths: list[Path] | None = None) -> bool:
    """探测问答后闸门（stop_hook）是否已挂进 Claude Code settings 的 Stop 钩子。best-effort：
    读不到/解析失败按未就绪，绝不抛异常、绝不自动写入用户配置（尊重不侵入）。"""
    for p in (settings_paths if settings_paths is not None else _default_settings_paths()):
        try:
            data = json.loads(Path(p).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if settings_has_stop_hook(data):
            return True
    return False


def _say_answer_gate_status() -> None:
    if _check_answer_gate_ready():
        _say("✓ 问答后闸门（stop_hook）已就绪：回答含未锚定事实会被自动拦下。")
    else:
        _say("⚠ 问答后闸门（stop_hook）未启用——'不编造'的自动强制当前不生效。")
        _say("  按 references/setup.md 把 stop_hook.py 挂进 Claude Code settings.json 的 Stop 钩子后即生效。")


def _check_offline() -> None:
    """断网就绪自检：只查不装，逐项报告 ✓/✗ 与国内替代路径。退出码恒 0。
    embedding/分词一项对 lawiki 实际会调用的运行实例做真实探针（见
    `_probe_embed_offline`），不是查 bundle 内 vendor 资产是否"看起来"带模型。"""
    _say("—— 离线就绪自检（--check-offline）——")
    ok_py = sys.version_info >= (3, 11)
    _say(f"  {'✓' if ok_py else '✗'} Python {sys.version.split()[0]}（需 3.11+）")
    if _have("uv"):
        _say("  ✓ uv 在 PATH")
    else:
        _say("  ✗ 未找到 uv —— pip install uv" + _mirror_hint())
    _say(f"  {'✓' if _verify(['makeitdown', '--help']) else '✗'} makeitdown 可用")

    # 走 _rag_cmd() 而非硬编码 "rag-retriever"：必须测的是 LAWIKI_RAG_CMD 覆盖下
    # lawiki 实际会调用的那个实例，否则设了覆盖也测不到（同一类"测错实例"的坑）。
    rag_available = _verify([*_rag_cmd(), "--help"])
    _say(f"  {'✓' if rag_available else '✗'} rag-retriever 可用")

    if not rag_available:
        _say("  ⏭ embedding/分词离线探针跳过（rag-retriever 不可用）")
    else:
        backend = _live_embed_backend(_rag_cmd())
        if backend != "local":
            _say(f"  ⏭ embedding 离线探针跳过（RAG_EMBED_BACKEND={backend}，非 local 后端"
                 f"不依赖内置 .onnx 模型；ollama 需本地服务，openai 本就要联网）")
        else:
            ok, detail = _probe_embed_offline(_rag_cmd())
            if ok:
                _say("  ✓ embedding 模型 + 分词表离线可用（对实际运行实例真实探针验证）")
            else:
                _say("  ✗ embedding/分词离线探针失败——lawiki 实际调用的这个 rag-retriever "
                     "无法离线跑通，首次索引会联网下载。常见原因：用 `uv tool install "
                     "\"rag-retriever @ git+...\"` 从 GitHub 装的实例永远不带内置模型；"
                     "改用本脚本从 -offline 发布包安装（不要用 setup.md 里的手动 git 命令），"
                     "或设 RAG_EMBED_MODEL_PATH 指向已手动搬运的模型目录。")
                _say(f"     探针详情：{detail}")

    _say("  提示：reranker（RAG_RERANK=local）默认关闭，开启需联网下载；")
    _say("        ollama 后端拉模型走境外 registry，国内建议 local（内置）或 openai（硅基流动）；")
    _say("        MinerU 互校默认已从 ModelScope（魔搭）拉权重，国内首用无需 HuggingFace。")
    _say_answer_gate_status()


def main(argv: list[str]) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 重定向默认 GBK
    except Exception:
        pass

    p = argparse.ArgumentParser(prog="install.py", description="lawiki bundle installer")
    p.add_argument("--ocr", choices=["local", "cloud"], default="cloud")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--skip-makeitdown", action="store_true")
    p.add_argument("--skip-rag", action="store_true")
    p.add_argument("--check-offline", action="store_true",
                   help="只查不装：报告离线就绪状态（Python/uv/命令可用性 + 对实际运行实例"
                        "做真实 embedding/分词离线探针）")
    args = p.parse_args(argv[1:])

    if args.check_offline:
        _check_offline()
        return 0

    results: list[tuple[str, str]] = []  # (部件, 状态)

    # 0) 前置：Python 3.11+ / uv
    if sys.version_info < (3, 11):
        _say(f"⚠ 需要 Python 3.11+，当前 {sys.version.split()[0]}。请升级后重试。")
        return 2
    if not _have("uv"):
        _say("⚠ 未找到 uv（安装 makeitdown/rag-retriever 需要它）。")
        _say("  Windows: winget install astral-sh.uv ；macOS/Linux: curl -LsSf https://astral.sh/uv/install.sh | sh")
        _say("  或（国内推荐）: pip install uv" + _mirror_hint())
        _say("  装好 uv 后重跑本脚本；在此之前 RAG/转换不可用，但 lawiki 核心仍可用预转的 _md/。")
        return 2

    # 1) makeitdown（转换器）
    md_dir = VENDOR / "makeitdown"
    if args.skip_makeitdown:
        results.append(("makeitdown", "跳过"))
    elif not md_dir.is_dir():
        results.append(("makeitdown", "✗ bundle 内缺 vendor/makeitdown"))
    else:
        extra = "[local]" if args.ocr == "local" else ""
        spec = f"makeitdown{extra} @ {md_dir.as_uri()}"
        _say(f"正在安装 makeitdown（OCR={args.ocr}）……")
        ok = _uv_install(spec, args.dry_run)
        results.append(("makeitdown", "✓" if ok else "✗ 安装失败（可改用预转 _md/）"))

    # 2) rag-retriever（RAG 检索；embedding 默认 local）
    rag_dir = VENDOR / "rag-retriever"
    if args.skip_rag:
        results.append(("rag-retriever", "跳过"))
    elif not rag_dir.is_dir():
        results.append(("rag-retriever", "✗ bundle 内缺 vendor/rag-retriever"))
    else:
        _say("正在安装 rag-retriever（embedding 默认 local，离线）……")
        ok = _uv_install(f"rag-retriever @ {rag_dir.as_uri()}", args.dry_run)
        results.append(("rag-retriever", "✓" if ok else "✗ 安装失败（问答将退化仅 wiki）"))

    # 3) 验证
    if not args.dry_run:
        commands = {"makeitdown": "makeitdown", "rag-retriever": "rag-retriever"}
        for index, (part, status) in enumerate(results):
            if status != "✓":
                continue
            command = commands[part]
            resolved = _installed_command(command)
            if resolved is not None and _verify([*resolved, "--help"]):
                _say(f"✓ {part} 可用")
            else:
                results[index] = (part, "✗ 安装完成但命令不可用（检查 PATH 或安装日志）")

    # 汇总
    _say("—— 安装结果 ——")
    for part, status in results:
        _say(f"  {part}: {status}")
    _say("lawiki skill 无需安装：让 agent 加载 bundle 内 skill/lawiki 即可。")
    _say('就绪后把文件放进案件目录的 原始资料/，对 agent 说「整理案件资料」。')
    _say_answer_gate_status()
    if args.ocr == "cloud":
        _say("云端 OCR 需设 PADDLEOCR_AISTUDIO_TOKEN，见 skill/lawiki/references/setup.md。")
    # Mirrors rag_retriever.embed._BUNDLED_MODELS_DIR (stdlib-only installer can't
    # import it); keep the "_models" layout in sync if that package is renamed.
    offline_models = VENDOR / "rag-retriever" / "rag_retriever" / "_models"
    if offline_models.is_dir():
        _say("✓ 离线包：已内置 embedding 模型，首次建索引无需联网下载。")
    else:
        _say("提示：本包首次建索引会联网下载 embedding 模型（bge-small-zh-v1.5，境外 HuggingFace）。")
        _say("  国内如慢：设 HF_ENDPOINT=https://hf-mirror.com，或改用 -offline 版发布包。")
    # 安装器面向 agent/脚本，不能只把失败写在中文输出里却返回成功。
    # 显式 --skip-* 属于用户选择的降级，不算失败；真正的缺包/安装失败返回 1。
    return 1 if any(status.startswith("✗") for _, status in results) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
