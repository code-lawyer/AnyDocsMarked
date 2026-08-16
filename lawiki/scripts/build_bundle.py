#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""组装 anydocsmarked 发布包（仅标准库）。

把三个协作模块的**源码**当场从本地权威仓库复制进一个自包含 zip（每次发版重新
vendor 最新源码，避免副本陈旧）：

  anydocsmarked-v<版本>.zip         （plain，仅源码，不含模型）
  anydocsmarked-v<版本>-offline.zip （--offline，额外含 vendored embedding ONNX）

包结构：
  ├── skill/lawiki/          # agent 加载的 skill
  ├── vendor/rag-retriever/  # 源码 + LICENSE（plain 包不含 _models/_tiktoken）
  ├── vendor/makeitdown/     # 源码 + LICENSE
  ├── install.py             # 安装器
  ├── MANIFEST.txt           # 各模块的 commit 哈希
  └── README.txt

Plain 包（默认）不含本地 embedding ONNX；模型在安装/首次运行时下载。
--offline 包含 rag_retriever/_models/（约 90 MB），让国内用户"解压即离线可用"。

生成 --offline 包前，先在能联网的机器上取一次模型：
  rag-retriever/.venv/Scripts/python rag-retriever/scripts/fetch_bundled_model.py

用法：
  python scripts/build_bundle.py [--version 1.0.0] [--offline]
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from pathlib import Path

LAWIKI = Path(__file__).resolve().parent.parent          # lawiki/ 子项目目录
REPO = LAWIKI.parent                                      # 单仓库根（AnyDocsMarked）
RAG_SRC = REPO / "rag-retriever"                          # 同仓库的姊妹子项目
MD_SRC = REPO / "makeitdown"
VERSION_FILE = REPO / "VERSION"

# 复制时跳过的目录/文件名（按名匹配，任意层级）
_EXCLUDE = {
    ".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".ruff_cache",
    "dist", "build", "node_modules", ".rag", ".rag-retriever",
    "原始资料", "_md", ".mypy_cache",
}
_EXCLUDE_SUFFIX = {".pyc", ".pyo", ".zip"}

# Vendored offline assets (embedding ONNX + tiktoken BPE). Excluded from the
# plain bundle; included only when building the --offline bundle.
_VENDORED = {"_models", "_tiktoken"}


def _make_ignore(offline: bool):
    # Always skip _EXCLUDE; the plain bundle additionally skips the vendored assets.
    exclude = _EXCLUDE | (set() if offline else _VENDORED)

    def _ignore(_dir: str, names: list[str]) -> set:
        out = set()
        for n in names:
            if n in exclude or any(n.endswith(s) for s in _EXCLUDE_SUFFIX) or n.endswith(".egg-info"):
                out.add(n)
        return out

    return _ignore


def _copy_tree(src: Path, dst: Path, offline: bool) -> None:
    if not src.is_dir():
        sys.exit(f"找不到源目录：{src}")
    shutil.copytree(src, dst, ignore=_make_ignore(offline))


def _zip_name(version: str, offline: bool) -> str:
    return f"anydocsmarked-v{version}{'-offline' if offline else ''}.zip"


def _component_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    components = (
        ("makeitdown", MD_SRC, MD_SRC / "src" / "makeitdown" / "__init__.py"),
        ("rag-retriever", RAG_SRC, RAG_SRC / "rag_retriever" / "__init__.py"),
    )
    for name, directory, runtime_init in components:
        with (directory / "pyproject.toml").open("rb") as fh:
            versions[name] = tomllib.load(fh)["project"]["version"]
        runtime_tree = ast.parse(runtime_init.read_text(encoding="utf-8"), filename=str(runtime_init))
        runtime_version = next(
            (
                node.value.value
                for node in runtime_tree.body
                if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ),
            None,
        )
        if runtime_version is None:
            raise ValueError(f"找不到运行时版本常量：{runtime_init}")
        versions[f"{name}.__version__"] = runtime_version
    return versions


def _validate_component_versions(version: str, components: dict[str, str]) -> None:
    mismatched = {name: value for name, value in components.items() if value != version}
    if mismatched:
        detail = ", ".join(f"{name}={value}" for name, value in mismatched.items())
        raise ValueError(f"发布版本不一致：bundle={version}; {detail}")


def _write_checksums(dist: Path, version: str) -> Path:
    manifest = dist / "SHA256SUMS.txt"
    lines = []
    for bundle in sorted(dist.glob(f"anydocsmarked-v{version}*.zip")):
        digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
        lines.append(f"{digest}  {bundle.name}")
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return manifest


def _has_vendored_models(rag_src: Path) -> bool:
    # An offline bundle is only real if a model .onnx was vendored (rglob on a
    # missing dir yields nothing, so no separate is_dir() check is needed).
    d = rag_src / "rag_retriever" / "_models"
    return next(d.rglob("*.onnx"), None) is not None


def _git_head(repo: Path) -> str:
    try:
        out = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                             capture_output=True, text=True)
        return out.stdout.strip() or "(无 git)"
    except Exception:
        return "(无 git)"


def _assemble_bundle_root(root: Path, version: str, offline: bool) -> None:
    """把发布包内容组装进 root（main() 打包前调用；也供测试直接断言目录内容）。"""
    # 1) skill
    _copy_tree(LAWIKI / "skill" / "lawiki", root / "skill" / "lawiki", offline)
    # 2) vendor 两个可安装包（skill 单独放在上面）
    _copy_tree(RAG_SRC, root / "vendor" / "rag-retriever", offline)
    _copy_tree(MD_SRC, root / "vendor" / "makeitdown", offline)

    # 3) 安装器 + 一步摄入脚本（CLI + GUI 共用同一引擎，agent 与桌面用户都要能跑）
    shutil.copy2(LAWIKI / "install.py", root / "install.py")
    shutil.copy2(LAWIKI / "ingest.py", root / "ingest.py")
    shutil.copy2(LAWIKI / "ingest_gui.py", root / "ingest_gui.py")

    # 4) MANIFEST（溯源各模块的 commit）
    (root / "MANIFEST.txt").write_text(
        f"anydocsmarked v{version}\n"
        f"lawiki        {_git_head(LAWIKI)}\n"
        f"rag-retriever {_git_head(RAG_SRC)}\n"
        f"makeitdown    {_git_head(MD_SRC)}\n",
        encoding="utf-8")

    # 5) README
    (root / "README.txt").write_text(
        "AnyDocsMarked —— 法律案件资料整理 + 交叉验证问答\n\n"
        "用法：\n"
        "1. 解压本包。\n"
        "2. 让你的 AI agent 加载 skill/lawiki（Claude Code/Copilot 自动识别 SKILL.md；\n"
        "   Codex 等把 skill/lawiki 内容作系统指令或置入案件目录作 AGENTS.md）。\n"
        "3. 首次使用：运行 `python install.py`（agent 会按 setup.md 自动跑）安装\n"
        "   makeitdown 与 rag-retriever；按提示选 OCR 方式。\n"
        "4. 把法律文件放进案件目录的 原始资料/，对 agent 说「整理案件资料」。\n"
        "5. 之后可就案件提问，agent 会用 wiki 与 RAG 原文交叉验证作答。\n\n"
        "vendor/ 下为 rag-retriever、makeitdown 源码（含各自 LICENSE）。\n",
        encoding="utf-8")


def main(argv: list[str]) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台默认 GBK
    except Exception:
        pass
    ap = argparse.ArgumentParser(prog="build_bundle.py")
    ap.add_argument(
        "--version",
        default=VERSION_FILE.read_text(encoding="utf-8").strip(),
        help="release version; must match VERSION and both component packages",
    )
    ap.add_argument("--offline", action="store_true",
                    help="include vendored embedding/tiktoken assets; names the zip -offline")
    args = ap.parse_args(argv[1:])

    declared = VERSION_FILE.read_text(encoding="utf-8").strip()
    if args.version != declared:
        sys.exit(f"--version {args.version} 与 VERSION {declared} 不一致")
    try:
        _validate_component_versions(args.version, _component_versions())
    except ValueError as exc:
        sys.exit(str(exc))

    if args.offline and not _has_vendored_models(RAG_SRC):
        sys.exit("--offline 需要先运行 rag-retriever/scripts/fetch_bundled_model.py 生成 _models/")

    out_zip = LAWIKI / "dist" / _zip_name(args.version, args.offline)
    out_zip.parent.mkdir(exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / f"anydocsmarked-v{args.version}"
        root.mkdir()

        _assemble_bundle_root(root, args.version, args.offline)

        # 打包
        if out_zip.exists():
            out_zip.unlink()
        with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(root.rglob("*")):
                if f.is_file():
                    zf.write(f, f.relative_to(root.parent).as_posix())

    size_mb = out_zip.stat().st_size / 1e6
    _write_checksums(out_zip.parent, args.version)
    print(f"✓ 已生成 {out_zip}  ({size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
