# 摄入引擎 + CLI 前端 实现 Plan（Plan A）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把前半段（脚手架 → 原始资料 → `_md` → `.rag` → 源级对账 → 完整性门）收敛成一个 stdlib 确定性**摄入引擎**，并配一个 **CLI 前端**（agent 无头调用，一次调用 → 单退出码 + `ingest-report.json`）。引擎的门/退出码/report/consent 逻辑抽成**可被后续 GUI 前端（Plan B）直接调用**的函数。

**Architecture:** 新增 `lawiki/ingest.py`（与 `lawiki/install.py` 同级的 bundle 根脚本），仅标准库；**不 import makeitdown/rag-retriever/lawiki-lint 的模块内部**，只 `subprocess` shell out 到它们的 CLI 及 bundle 内 `skill/lawiki/tools/{init_case,reconcile}.py`。纯判断逻辑（门禁 + 合并报告 + 退出码）无副作用、可穷举单测；subprocess 壳用 mock 单测。边界**止于 `_md` + `.rag` + 完整性门**；lawiki 建 wiki（Tier-2 LLM）不进本引擎，wiki 的 `lint check` 亦不在此。

**Tech Stack:** Python 3.11+ 标准库（argparse / subprocess / json / pathlib / shutil / os / tempfile）；测试用 stdlib `unittest` + `unittest.mock`（零第三方依赖，镜像 `lawiki/test_install.py`）。

**Spec:** `docs/superpowers/specs/2026-08-15-tool-vs-llm-architecture-and-ingest-orchestrator-design.md`

## Global Constraints

- **仅标准库**：`ingest.py` 与 `test_ingest.py` 零第三方依赖。
- **不 import 三模块内部**：只经 CLI/JSON/退出码 shell out 到 makeitdown、rag-retriever，及 bundle 内 `skill/lawiki/tools/{init_case,reconcile}.py`。允许 `from rag import _rag_base`（同 install.py 的 within-bundle 复用）。
- **门逻辑可被两前端复用**：`_gate_and_merge` / `_run_*` / `_preflight` 均为模块级函数（无 tk、无全局态），Plan B 的 GUI 直接 import 调用；**门/退出码/report/consent 是单一来源，前端不得另写**。
- **退出码语义**：`0` 全通过；`1` 转换有硬失败（makeitdown report `failed>0`）；`2` 前置/环境缺失（无原始资料 / makeitdown 不可用 / 转换未产出 report，常见云端未同意或输入非法）；`3` 完整性门未过（源级对账未处置>0，或有 rag 却索引不全）。**转换失败(1)优先于完整性(3)**。
- **默认引擎 `auto`**（**不是 cloud**）：有本地用本地、仅在已配 token+consent 时用云、否则由 makeitdown fail-closed，绝不静默上传。云端为显式覆盖。
- **隐私绝不放宽**：引擎不做 consent 判断，只透传 `--cloud-consent`；缺同意→makeitdown 无 report + 非零退出→引擎映射退出码 `2`。
- **降级哲学**：rag-retriever **未安装**（`FileNotFoundError`）→ 跳过建索引、`index.ran=false`、**不失败**；rag **已装但索引出错/空** → `index.ran=true` 且 `files_indexed=0`，走完整性门(3)。
- **Windows 优先**：`sys.stdout.reconfigure(encoding="utf-8")`；所有 `subprocess.run` 传 `encoding="utf-8", errors="replace"`；`ingest-report.json` 原子替换。
- **固定目录约定**：`<case>/原始资料` → `<case>/_md`（含 `report.json`）→ `<case>/.rag`；报告写 `<case>/ingest-report.json`。
- **测试运行**：`cd lawiki && python -m pytest test_ingest.py -q`。

---

## File Structure

- **Create `lawiki/ingest.py`** — 引擎 + CLI。含：退出码常量、`_say`、`_atomic_write_text`、`_count_md_files`、`_gate_and_merge`（纯）、`_preflight`、`_run_init_case`、`_run_convert`、`_run_index`、`_run_reconcile`、`main(argv)`。
- **Create `lawiki/test_ingest.py`** — stdlib unittest；覆盖 `_gate_and_merge` 全分支、`_preflight`、四个 runner（mock subprocess）、`main`（mock runner）。
- **Modify `lawiki/skill/lawiki/references/setup.md`** — 增补「一步摄入：`python ingest.py <案件目录>`」小节（Plan B 会再补 GUI 用法）。
- 不改 makeitdown / rag-retriever / lawiki lint / init_case / reconcile 任何行为。

引擎阶段顺序：**init_case（幂等脚手架+闭世界锚点+原始资料/+log.md）→ preflight（原始资料非空+makeitdown可用）→ convert（makeitdown）→ index（rag，可降级）→ source reconcile（reconcile.py）→ gate+merge+落盘**。

---

### Task 1: 纯核心——退出码 + 完整性门 + 合并报告（`_gate_and_merge`）

**Files:**
- Create: `lawiki/ingest.py`（骨架：docstring、imports、常量、`_say`、`_count_md_files`、`_gate_and_merge`）
- Test: `lawiki/test_ingest.py`（骨架 + `GateAndMergeTests`）

**Interfaces:**
- Produces:
  - 常量 `EXIT_PASS=0`, `EXIT_CONVERT_FAILED=1`, `EXIT_PREFLIGHT=2`, `EXIT_INCOMPLETE=3`
  - `_count_md_files(md_dir: Path) -> int`（`*.md` 递归计数）
  - `_gate_and_merge(case_dir: Path, convert: dict, index: dict, md_file_count: int, reconcile_reasons: list[str], *, index_ran: bool) -> dict` — 返回含 `stages`/`gate`/`exit_code` 的合并报告。`convert`=makeitdown report；`index`=rag JSON（`index_ran=False` 时传 `{}`）；`reconcile_reasons`=reconcile 未处置行（空=干净）。

- [ ] **Step 1: 写失败测试**

创建 `lawiki/test_ingest.py`：

```python
# -*- coding: utf-8 -*-
"""ingest.py 回归测试（stdlib unittest，零依赖，镜像 test_install.py）。"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
import ingest  # noqa: E402


def _conv(succeeded=0, warned=0, failed=0, skipped_existing=0, skipped_unsupported=0):
    return {"succeeded": succeeded, "warned": warned, "failed": failed,
            "skipped_existing": skipped_existing, "skipped_unsupported": skipped_unsupported}


class GateAndMergeTests(unittest.TestCase):
    def test_all_pass(self):
        merged = ingest._gate_and_merge(
            Path("/case"), _conv(succeeded=3), {"files_indexed": 3, "files_skipped": 0},
            3, [], index_ran=True)
        self.assertTrue(merged["gate"]["passed"])
        self.assertEqual(merged["gate"]["reasons"], [])
        self.assertEqual(merged["exit_code"], ingest.EXIT_PASS)

    def test_conversion_failure_exits_1_and_takes_precedence(self):
        merged = ingest._gate_and_merge(
            Path("/case"), _conv(succeeded=2, failed=1), {"files_indexed": 0, "files_skipped": 0},
            2, ["[未处置源级遗漏] 原始资料/x.doc"], index_ran=True)
        self.assertEqual(merged["exit_code"], ingest.EXIT_CONVERT_FAILED)  # 1 覆盖 3
        self.assertTrue(any("硬失败" in r for r in merged["gate"]["reasons"]))

    def test_index_incomplete_exits_3(self):
        merged = ingest._gate_and_merge(
            Path("/case"), _conv(succeeded=3), {"files_indexed": 2, "files_skipped": 1},
            3, [], index_ran=True)
        self.assertEqual(merged["exit_code"], ingest.EXIT_INCOMPLETE)
        self.assertTrue(any("未进入 .rag" in r for r in merged["gate"]["reasons"]))

    def test_reconcile_unresolved_exits_3(self):
        merged = ingest._gate_and_merge(
            Path("/case"), _conv(succeeded=1), {"files_indexed": 1, "files_skipped": 0},
            1, ["[未处置源级遗漏] 原始资料/老合同.doc"], index_ran=True)
        self.assertEqual(merged["exit_code"], ingest.EXIT_INCOMPLETE)
        self.assertIn("[未处置源级遗漏] 原始资料/老合同.doc", merged["gate"]["reasons"])
        self.assertEqual(merged["stages"]["source_reconcile"]["unresolved"],
                         ["[未处置源级遗漏] 原始资料/老合同.doc"])

    def test_index_not_run_still_passes(self):
        merged = ingest._gate_and_merge(
            Path("/case"), _conv(succeeded=3), {}, 3, [], index_ran=False)
        self.assertTrue(merged["gate"]["passed"])
        self.assertEqual(merged["stages"]["index"], {"ran": False, "md_files": 3})

    def test_skipped_unsupported_alone_does_not_fail(self):
        # skipped_unsupported 若已在 reconcile 登记则 reconcile_reasons 为空 → 不失败
        merged = ingest._gate_and_merge(
            Path("/case"), _conv(succeeded=2, skipped_unsupported=1),
            {"files_indexed": 2, "files_skipped": 0}, 2, [], index_ran=True)
        self.assertTrue(merged["gate"]["passed"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd lawiki && python -m pytest test_ingest.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ingest'`。

- [ ] **Step 3: 写最小实现**

创建 `lawiki/ingest.py`：

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AnyDocsMarked 摄入引擎 + CLI 前端（仅标准库，跨平台）。

前半段单入口：脚手架 → makeitdown 转换 → rag 建索引 → 源级对账 → 确定性完整性门，
产出合并 ingest-report.json + 单退出码。门/退出码/report/consent 逻辑为单一来源，
Plan B 的 GUI 直接 import 本模块函数复用（不另写门逻辑）。

用法：
  python ingest.py <案件目录> [--ocr-engine auto|local|cloud] [--cloud-consent]
                   [--workers N] [--skip-existing] [--skip-index] [--dry-run]

约定目录：<案件目录>/原始资料 → /_md（含 report.json）→ /.rag；报告写 /ingest-report.json。

退出码：0 全通过；1 转换有硬失败；2 前置/环境缺失；3 完整性门未过（源级对账未处置>0，
或有 rag 却索引不全）。转换失败优先。边界止于 _md+.rag+门；建 wiki（LLM）与 lint check 不在此。
隐私：不放宽 consent，只透传 --cloud-consent。
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

_TOOLS = Path(__file__).resolve().parent / "skill" / "lawiki" / "tools"
sys.path.insert(0, str(_TOOLS))
from rag import _rag_base as _rag_cmd  # noqa: E402

EXIT_PASS = 0
EXIT_CONVERT_FAILED = 1
EXIT_PREFLIGHT = 2
EXIT_INCOMPLETE = 3


def _say(msg: str) -> None:
    print(f"[lawiki-ingest] {msg}", flush=True)


def _count_md_files(md_dir: Path) -> int:
    return sum(1 for _ in md_dir.rglob("*.md"))


def _gate_and_merge(case_dir: Path, convert: dict, index: dict, md_file_count: int,
                    reconcile_reasons: list[str], *, index_ran: bool) -> dict:
    reasons: list[str] = []
    exit_code = EXIT_PASS

    failed = convert.get("failed", 0)
    if failed > 0:
        reasons.append(f"转换硬失败 {failed} 个（见 _md/report.json 的 failures）")
        exit_code = EXIT_CONVERT_FAILED

    if index_ran:
        indexed = index.get("files_indexed", 0)
        skipped = index.get("files_skipped", 0)
        index_stage: dict = {"ran": True, "files_indexed": indexed,
                             "files_skipped": skipped, "md_files": md_file_count}
        if skipped > 0:
            reasons.append(f"{skipped} 个文件被检索器跳过、未入索引")
        if indexed < md_file_count:
            reasons.append(f"{md_file_count - indexed} 个 _md 未进入 .rag 索引")
        if (skipped > 0 or indexed < md_file_count) and exit_code == EXIT_PASS:
            exit_code = EXIT_INCOMPLETE
    else:
        index_stage = {"ran": False, "md_files": md_file_count}

    if reconcile_reasons:
        reasons.extend(reconcile_reasons)
        if exit_code == EXIT_PASS:
            exit_code = EXIT_INCOMPLETE

    return {
        "case_dir": str(case_dir),
        "stages": {
            "convert": {k: convert.get(k, 0) for k in (
                "succeeded", "warned", "failed",
                "skipped_existing", "skipped_unsupported")},
            "index": index_stage,
            "source_reconcile": {"unresolved": reconcile_reasons},
        },
        "gate": {"passed": exit_code == EXIT_PASS, "reasons": reasons},
        "exit_code": exit_code,
    }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd lawiki && python -m pytest test_ingest.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add lawiki/ingest.py lawiki/test_ingest.py
git commit -m "feat(ingest): pure gate+merge core (with source-reconcile) for ingest engine"
```

---

### Task 2: 前置检查（`_preflight`）

**Files:**
- Modify: `lawiki/ingest.py`（追加 `_preflight`）
- Test: `lawiki/test_ingest.py`（追加 `PreflightTests`）

**Interfaces:**
- Produces: `_preflight(raw_dir: Path) -> str | None` — 错误串（应退 `EXIT_PREFLIGHT`）或 `None`。顺序：原始资料存在 → 非空 → `makeitdown` 可解析。

- [ ] **Step 1: 写失败测试**

追加：

```python
class PreflightTests(unittest.TestCase):
    def test_missing_raw(self):
        with tempfile.TemporaryDirectory() as td:
            err = ingest._preflight(Path(td) / "原始资料")
            self.assertIn("原始资料", err or "")

    def test_empty_raw(self):
        with tempfile.TemporaryDirectory() as td:
            raw = Path(td) / "原始资料"; raw.mkdir()
            self.assertIn("空", ingest._preflight(raw) or "")

    def test_makeitdown_missing(self):
        with tempfile.TemporaryDirectory() as td:
            raw = Path(td) / "原始资料"; raw.mkdir()
            (raw / "a.txt").write_text("x", encoding="utf-8")
            with mock.patch.object(ingest.shutil, "which", return_value=None):
                self.assertIn("makeitdown", ingest._preflight(raw) or "")

    def test_ok(self):
        with tempfile.TemporaryDirectory() as td:
            raw = Path(td) / "原始资料"; raw.mkdir()
            (raw / "a.txt").write_text("x", encoding="utf-8")
            with mock.patch.object(ingest.shutil, "which", return_value="/bin/makeitdown"):
                self.assertIsNone(ingest._preflight(raw))
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd lawiki && python -m pytest test_ingest.py::PreflightTests -q`
Expected: FAIL — `AttributeError: ... '_preflight'`。

- [ ] **Step 3: 写最小实现**

追加：

```python
def _preflight(raw_dir: Path) -> str | None:
    if not raw_dir.is_dir():
        return f"找不到原始资料目录：{raw_dir}（把待转文件放进 <案件目录>/原始资料/）"
    if not any(p.is_file() for p in raw_dir.rglob("*")):
        return f"原始资料目录为空：{raw_dir}"
    if shutil.which("makeitdown") is None:
        return "未找到 makeitdown（先跑 install.py 安装转换器，或检查 PATH）"
    return None
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd lawiki && python -m pytest test_ingest.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add lawiki/ingest.py lawiki/test_ingest.py
git commit -m "feat(ingest): preflight checks"
```

---

### Task 3: 四个 subprocess 壳（init_case / convert / index / reconcile）

**Files:**
- Modify: `lawiki/ingest.py`（追加 `_atomic_write_text` + 四个 runner）
- Test: `lawiki/test_ingest.py`（追加 `RunnerTests`）

**Interfaces:**
- Consumes: `_rag_cmd()`、`_TOOLS`
- Produces:
  - `_atomic_write_text(path: Path, text: str) -> None`
  - `_run_init_case(case_dir: Path, *, dry_run: bool) -> None` — 跑 `[sys.executable, _TOOLS/../init_case.py, case_dir]`（幂等脚手架）。
  - `_run_convert(raw_dir, md_dir, *, ocr_engine, cloud_consent, workers, skip_existing, dry_run) -> tuple[dict|None, int]` — 跑 makeitdown；返回 `(report_or_None, returncode)`。
  - `_run_index(md_dir, case_dir, rag_dir, *, dry_run) -> tuple[dict|None, bool]` — 跑 rag index；`FileNotFoundError`→`(None,False)`；非零/非 JSON→`({"files_indexed":0,"files_skipped":0},True)`。
  - `_run_reconcile(case_dir, *, dry_run) -> list[str]` — 跑 `reconcile.py`；退出 0→`[]`；非零→stdout 里非汇总行组成的未处置列表。

  注：`init_case.py` 与 `reconcile.py` 均在 `_TOOLS`（`skill/lawiki/tools/`），`_TOOLS.parent` 无关；直接 `_TOOLS / "init_case.py"`、`_TOOLS / "reconcile.py"`。

- [ ] **Step 1: 写失败测试**

追加：

```python
class _FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode; self.stdout = stdout; self.stderr = stderr


class RunnerTests(unittest.TestCase):
    def test_init_case_argv(self):
        with mock.patch.object(ingest.subprocess, "run", return_value=_FakeProc(0)) as m:
            ingest._run_init_case(Path("/x"), dry_run=False)
        argv = m.call_args.args[0]
        self.assertEqual(argv[0], sys.executable)
        self.assertTrue(argv[1].endswith("init_case.py"))
        self.assertEqual(argv[2], "/x")

    def test_convert_reads_report_and_flags(self):
        with tempfile.TemporaryDirectory() as td:
            md = Path(td) / "_md"; md.mkdir()
            (md / "report.json").write_text(json.dumps({"succeeded": 2}), encoding="utf-8")
            with mock.patch.object(ingest.subprocess, "run", return_value=_FakeProc(0)) as m:
                report, rc = ingest._run_convert(
                    Path(td) / "原始资料", md, ocr_engine="cloud", cloud_consent=True,
                    workers=1, skip_existing=True, dry_run=False)
            argv = m.call_args.args[0]
            self.assertEqual(argv[0], "makeitdown")
            self.assertIn("--cloud-consent", argv)
            self.assertIn("--skip-existing", argv)
            self.assertIn("--ocr-engine", argv)
            self.assertEqual(report["succeeded"], 2)
            self.assertEqual(rc, 0)

    def test_convert_no_report_returns_none(self):
        with tempfile.TemporaryDirectory() as td:
            md = Path(td) / "_md"; md.mkdir()
            with mock.patch.object(ingest.subprocess, "run", return_value=_FakeProc(2)):
                report, rc = ingest._run_convert(
                    Path(td) / "原始资料", md, ocr_engine="cloud", cloud_consent=False,
                    workers=1, skip_existing=False, dry_run=False)
            self.assertIsNone(report)
            self.assertEqual(rc, 2)

    def test_index_parses_json(self):
        with mock.patch.object(ingest.subprocess, "run",
                               return_value=_FakeProc(0, stdout='{"files_indexed":3,"files_skipped":0}')):
            result, ran = ingest._run_index(Path("/x/_md"), Path("/x"), Path("/x/.rag"), dry_run=False)
        self.assertTrue(ran); self.assertEqual(result["files_indexed"], 3)

    def test_index_not_installed_degrades(self):
        with mock.patch.object(ingest.subprocess, "run", side_effect=FileNotFoundError()):
            result, ran = ingest._run_index(Path("/x/_md"), Path("/x"), Path("/x/.rag"), dry_run=False)
        self.assertFalse(ran); self.assertIsNone(result)

    def test_index_nonzero_marks_empty(self):
        with mock.patch.object(ingest.subprocess, "run", return_value=_FakeProc(1, stdout="")):
            result, ran = ingest._run_index(Path("/x/_md"), Path("/x"), Path("/x/.rag"), dry_run=False)
        self.assertTrue(ran); self.assertEqual(result, {"files_indexed": 0, "files_skipped": 0})

    def test_reconcile_clean(self):
        with mock.patch.object(ingest.subprocess, "run",
                               return_value=_FakeProc(0, stdout="源级对账：2 源文件 | 已产出 2 | 已登记跳过 0 | 未处置 0")):
            self.assertEqual(ingest._run_reconcile(Path("/x"), dry_run=False), [])

    def test_reconcile_unresolved(self):
        out = ("源级对账：3 源文件 | 已产出 2 | 已登记跳过 0 | 未处置 1\n"
               "[未处置源级遗漏] 原始资料/老合同.doc")
        with mock.patch.object(ingest.subprocess, "run", return_value=_FakeProc(1, stdout=out)):
            reasons = ingest._run_reconcile(Path("/x"), dry_run=False)
        self.assertEqual(reasons, ["[未处置源级遗漏] 原始资料/老合同.doc"])
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd lawiki && python -m pytest test_ingest.py::RunnerTests -q`
Expected: FAIL — `AttributeError: ... '_run_init_case'`。

- [ ] **Step 3: 写最小实现**

追加：

```python
def _atomic_write_text(path: Path, text: str) -> None:
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text); fh.flush(); os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _run_init_case(case_dir: Path, *, dry_run: bool) -> None:
    cmd = [sys.executable, str(_TOOLS / "init_case.py"), str(case_dir)]
    _say("将执行: " + " ".join(cmd))
    if dry_run:
        return
    subprocess.run(cmd, text=True, encoding="utf-8", errors="replace")


def _run_convert(raw_dir: Path, md_dir: Path, *, ocr_engine: str, cloud_consent: bool,
                 workers: int, skip_existing: bool, dry_run: bool) -> tuple[dict | None, int]:
    cmd = ["makeitdown", str(raw_dir), "-o", str(md_dir),
           "--ocr-engine", ocr_engine, "--workers", str(workers)]
    if cloud_consent:
        cmd.append("--cloud-consent")
    if skip_existing:
        cmd.append("--skip-existing")
    _say("将执行: " + " ".join(cmd))
    if dry_run:
        return None, 0
    proc = subprocess.run(cmd, text=True, encoding="utf-8", errors="replace")
    report_path = md_dir / "report.json"
    if report_path.is_file():
        try:
            return json.loads(report_path.read_text(encoding="utf-8")), proc.returncode
        except ValueError:
            return None, proc.returncode
    return None, proc.returncode


def _run_index(md_dir: Path, case_dir: Path, rag_dir: Path, *,
               dry_run: bool) -> tuple[dict | None, bool]:
    cmd = [*_rag_cmd(), "--data-dir", str(rag_dir), "index", str(md_dir),
           "--source-root", str(case_dir)]
    _say("将执行: " + " ".join(cmd))
    if dry_run:
        return None, True
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
    except FileNotFoundError:
        _say("⏭ 未安装 rag-retriever，跳过建索引（问答将退化仅 wiki）")
        return None, False
    if proc.returncode != 0:
        _say(f"✗ 建索引失败（退出码 {proc.returncode}）：{(proc.stderr or '').strip()[:200]}")
        return {"files_indexed": 0, "files_skipped": 0}, True
    try:
        return json.loads(proc.stdout), True
    except ValueError:
        _say("✗ 建索引退出 0 但输出非 JSON，视为未完成")
        return {"files_indexed": 0, "files_skipped": 0}, True


def _run_reconcile(case_dir: Path, *, dry_run: bool) -> list[str]:
    cmd = [sys.executable, str(_TOOLS / "reconcile.py"), str(case_dir)]
    _say("将执行: " + " ".join(cmd))
    if dry_run:
        return []
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode == 0:
        return []
    reasons = [ln for ln in proc.stdout.splitlines()
               if ln.strip() and not ln.startswith("源级对账")]
    return reasons or [f"源级对账未通过（退出码 {proc.returncode}）：{(proc.stderr or '').strip()[:200]}"]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd lawiki && python -m pytest test_ingest.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add lawiki/ingest.py lawiki/test_ingest.py
git commit -m "feat(ingest): init_case/convert/index/reconcile subprocess runners"
```

---

### Task 4: 主流程 `main(argv)` 接线

**Files:**
- Modify: `lawiki/ingest.py`（追加 `main` + `if __name__`）
- Test: `lawiki/test_ingest.py`（追加 `MainTests`）

**Interfaces:**
- Consumes: 全部上述函数
- Produces: `main(argv: list[str]) -> int` — 参数：位置 `case_dir`；`--ocr-engine {auto,local,cloud}`（**默认 `auto`**）；`--cloud-consent`；`--workers`（默认 `os.cpu_count() or 4`）；`--skip-existing`；`--skip-index`；`--dry-run`。顺序：init_case → preflight → convert →（可选 index）→ reconcile → gate → 写 `ingest-report.json` → 返回退出码。

- [ ] **Step 1: 写失败测试**

追加：

```python
class MainTests(unittest.TestCase):
    def _case(self, td):
        case = Path(td) / "case"; raw = case / "原始资料"; raw.mkdir(parents=True)
        (raw / "a.txt").write_text("甲方向乙方借款五万元。", encoding="utf-8")
        return case

    def test_default_engine_is_auto(self):
        with tempfile.TemporaryDirectory() as td:
            case = self._case(td); (case / "_md").mkdir()
            (case / "_md" / "a.md").write_text("x", encoding="utf-8")
            captured = {}
            def fake_convert(raw, md, *, ocr_engine, **kw):
                captured["engine"] = ocr_engine
                return {"succeeded": 1, "failed": 0}, 0
            with mock.patch.object(ingest.shutil, "which", return_value="mk"), \
                 mock.patch.object(ingest, "_run_init_case"), \
                 mock.patch.object(ingest, "_run_convert", side_effect=fake_convert), \
                 mock.patch.object(ingest, "_run_index", return_value=({"files_indexed": 1, "files_skipped": 0}, True)), \
                 mock.patch.object(ingest, "_run_reconcile", return_value=[]):
                ingest.main([str(case)])
            self.assertEqual(captured["engine"], "auto")

    def test_preflight_failure_returns_2(self):
        with tempfile.TemporaryDirectory() as td:
            case = Path(td) / "case"
            with mock.patch.object(ingest, "_run_init_case"):
                rc = ingest.main([str(case)])
            self.assertEqual(rc, ingest.EXIT_PREFLIGHT)
            self.assertFalse((case / "ingest-report.json").exists())

    def test_convert_no_report_returns_2(self):
        with tempfile.TemporaryDirectory() as td:
            case = self._case(td)
            with mock.patch.object(ingest.shutil, "which", return_value="mk"), \
                 mock.patch.object(ingest, "_run_init_case"), \
                 mock.patch.object(ingest, "_run_convert", return_value=(None, 2)):
                rc = ingest.main([str(case), "--ocr-engine", "cloud"])
            self.assertEqual(rc, ingest.EXIT_PREFLIGHT)

    def test_happy_path_returns_0_and_writes_report(self):
        with tempfile.TemporaryDirectory() as td:
            case = self._case(td); (case / "_md").mkdir()
            (case / "_md" / "借条.md").write_text("x", encoding="utf-8")
            with mock.patch.object(ingest.shutil, "which", return_value="mk"), \
                 mock.patch.object(ingest, "_run_init_case"), \
                 mock.patch.object(ingest, "_run_convert", return_value=({"succeeded": 1, "failed": 0}, 0)), \
                 mock.patch.object(ingest, "_run_index", return_value=({"files_indexed": 1, "files_skipped": 0}, True)), \
                 mock.patch.object(ingest, "_run_reconcile", return_value=[]):
                rc = ingest.main([str(case), "--ocr-engine", "local"])
            self.assertEqual(rc, ingest.EXIT_PASS)
            merged = json.loads((case / "ingest-report.json").read_text(encoding="utf-8"))
            self.assertTrue(merged["gate"]["passed"])

    def test_reconcile_unresolved_returns_3(self):
        with tempfile.TemporaryDirectory() as td:
            case = self._case(td); (case / "_md").mkdir()
            (case / "_md" / "a.md").write_text("x", encoding="utf-8")
            with mock.patch.object(ingest.shutil, "which", return_value="mk"), \
                 mock.patch.object(ingest, "_run_init_case"), \
                 mock.patch.object(ingest, "_run_convert", return_value=({"succeeded": 1, "failed": 0}, 0)), \
                 mock.patch.object(ingest, "_run_index", return_value=({"files_indexed": 1, "files_skipped": 0}, True)), \
                 mock.patch.object(ingest, "_run_reconcile", return_value=["[未处置源级遗漏] 原始资料/x.doc"]):
                rc = ingest.main([str(case), "--ocr-engine", "local"])
            self.assertEqual(rc, ingest.EXIT_INCOMPLETE)

    def test_skip_index_passes(self):
        with tempfile.TemporaryDirectory() as td:
            case = self._case(td); (case / "_md").mkdir()
            (case / "_md" / "a.md").write_text("x", encoding="utf-8")
            with mock.patch.object(ingest.shutil, "which", return_value="mk"), \
                 mock.patch.object(ingest, "_run_init_case"), \
                 mock.patch.object(ingest, "_run_convert", return_value=({"succeeded": 1, "failed": 0}, 0)), \
                 mock.patch.object(ingest, "_run_reconcile", return_value=[]), \
                 mock.patch.object(ingest, "_run_index") as mi:
                rc = ingest.main([str(case), "--ocr-engine", "local", "--skip-index"])
            mi.assert_not_called()
            self.assertEqual(rc, ingest.EXIT_PASS)
            merged = json.loads((case / "ingest-report.json").read_text(encoding="utf-8"))
            self.assertEqual(merged["stages"]["index"], {"ran": False, "md_files": 1})

    def test_dry_run_returns_0_no_report(self):
        with tempfile.TemporaryDirectory() as td:
            case = self._case(td)
            with mock.patch.object(ingest.shutil, "which", return_value="mk"), \
                 mock.patch.object(ingest.subprocess, "run") as m:
                rc = ingest.main([str(case), "--dry-run"])
            m.assert_not_called()
            self.assertEqual(rc, ingest.EXIT_PASS)
            self.assertFalse((case / "ingest-report.json").exists())
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd lawiki && python -m pytest test_ingest.py::MainTests -q`
Expected: FAIL — `AttributeError: ... 'main'`。

- [ ] **Step 3: 写最小实现**

追加：

```python
def main(argv: list[str]) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    p = argparse.ArgumentParser(prog="ingest.py", description="AnyDocsMarked 摄入引擎")
    p.add_argument("case_dir", help="案件目录（含 原始资料/ 子目录）")
    p.add_argument("--ocr-engine", choices=["auto", "local", "cloud"], default="auto")
    p.add_argument("--cloud-consent", action="store_true",
                   help="同意把文档/文本发往外部 OCR（透传给 makeitdown；不加则云端被阻断）")
    p.add_argument("--workers", type=int, default=os.cpu_count() or 4)
    p.add_argument("--skip-existing", action="store_true")
    p.add_argument("--skip-index", action="store_true", help="只转换、不建 .rag 索引")
    p.add_argument("--dry-run", action="store_true", help="只打印将执行的命令")
    args = p.parse_args(argv)

    case = Path(args.case_dir).resolve()
    raw, md, rag = case / "原始资料", case / "_md", case / ".rag"

    _run_init_case(case, dry_run=args.dry_run)

    err = _preflight(raw)
    if err and not args.dry_run:
        _say("✗ " + err)
        return EXIT_PREFLIGHT

    convert, rc = _run_convert(
        raw, md, ocr_engine=args.ocr_engine, cloud_consent=args.cloud_consent,
        workers=args.workers, skip_existing=args.skip_existing, dry_run=args.dry_run)

    if args.dry_run:
        _run_index(md, case, rag, dry_run=True)
        _run_reconcile(case, dry_run=True)
        _say("（dry-run：未真正执行，未写 ingest-report.json）")
        return EXIT_PASS

    if convert is None:
        _say(f"✗ 转换未产出 report.json（makeitdown 退出码 {rc}）——"
             f"常见：选了云端 OCR 但未加 --cloud-consent，或输入路径非法。")
        return EXIT_PREFLIGHT

    if args.skip_index:
        index, index_ran = {}, False
    else:
        index, index_ran = _run_index(md, case, rag, dry_run=False)
        index = index or {}

    reconcile_reasons = _run_reconcile(case, dry_run=False)

    merged = _gate_and_merge(case, convert, index, _count_md_files(md),
                             reconcile_reasons, index_ran=index_ran)
    _atomic_write_text(case / "ingest-report.json",
                       json.dumps(merged, ensure_ascii=False, indent=2))

    g = merged["gate"]
    _say(f"—— 摄入结果 —— gate={'通过' if g['passed'] else '未通过'}，退出码 {merged['exit_code']}")
    for reason in g["reasons"]:
        _say("  • " + reason)
    _say(f"报告：{case / 'ingest-report.json'}")
    if g["passed"]:
        _say("下一步：让 agent 加载 lawiki，对 _md/ 建 wiki（LLM 环节，不在本引擎内）。")
    return merged["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd lawiki && python -m pytest test_ingest.py -q`
Expected: PASS（全套）。

- [ ] **Step 5: 提交**

```bash
git add lawiki/ingest.py lawiki/test_ingest.py
git commit -m "feat(ingest): main() wiring — init_case→preflight→convert→index→reconcile→gate"
```

---

### Task 5: 文档接线（setup.md 一节）

**Files:**
- Modify: `lawiki/skill/lawiki/references/setup.md`

- [ ] **Step 1: 增补小节**

在安装小节之后，按该文件 `##`/`###` 风格加入：

```markdown
## 一步摄入（CLI；GUI 见下节，Plan B 落地后补）

把待转文件放进 `<案件目录>/原始资料/`，跑一条：

    python ingest.py <案件目录> [--ocr-engine auto|local|cloud] [--cloud-consent] [--skip-index]

它幂等建案脚手架 → makeitdown 转换 → rag 建索引 → 源级对账 → 完整性核对，产出
`<案件目录>/ingest-report.json`，并以**单退出码**给出机器可读结论：

- `0` 全通过；`1` 转换有硬失败；`2` 前置/环境缺失（无原始资料 / 未装 makeitdown /
  选云端但未加 `--cloud-consent`）；`3` 完整性门未过（源级未处置>0，或装了 rag 却索引不全）。

默认 `--ocr-engine auto`（有本地用本地、仅在已配 token+consent 时用云、否则绝不静默上传）。
未装 rag-retriever 时自动跳过建索引、不算失败（问答退化仅 wiki）。

边界：本步**止于 `_md` + `.rag`**。把散文变成带锚点的 wiki 是 LLM 环节，由 agent 加载
lawiki 后驱动，**不在引擎内**；wiki 的 `lint check` 也在建 wiki 之后才跑。
```

- [ ] **Step 2: 一致性核对**

核对命令名/参数（`--ocr-engine auto|local|cloud`、`--cloud-consent`、`--skip-index`）与退出码 `0/1/2/3` 语义，与 `ingest.py` 的 `main`/docstring 一致。

- [ ] **Step 3: 提交**

```bash
git add lawiki/skill/lawiki/references/setup.md
git commit -m "docs(ingest): document one-step ingest CLI in setup.md"
```

---

## 落地后的整体回归

```bash
cd lawiki && python -m pytest skill/lawiki scripts test_install.py test_ingest.py -q
cd .. && uvx ruff check --select E9,F lawiki/ingest.py lawiki/test_ingest.py
git diff --check
```

Expected: 全绿；无 E9/F；无行尾空白冲突。

## 自查（对照 spec）

- **spec 决策 8 条覆盖**：① bundle 级、只 shell out、不 import 模块 → imports 注释 + Global Constraints；② 门/退出码/report/consent 单一来源、函数可被 GUI 复用 → 全部模块级函数、无 tk；③ 默认 `auto` → Task 4 `test_default_engine_is_auto`；④ reconcile 并入 → Task 1/3/4；⑤ 退出码四情形 → 各 MainTests；⑥ 隐私不放宽 → `_run_convert` 透传 + `convert is None→EXIT_PREFLIGHT`；⑦ 完整性单一裁决（index + source_reconcile 同进一个 gate）→ `_gate_and_merge`；⑧ 边界止于 _md+.rag（不跑 lint check，不建 wiki）→ main 收尾文案。
- **占位符扫描**：无 TBD/TODO；每步含真实代码。
- **类型一致性**：`_gate_and_merge` 新增 `reconcile_reasons: list[str]` 位置参数，Task 1 测试、Task 4 `main` 一致传入；`index` 在 `index_ran=False` 时传 `{}`；`_run_index` 返回 `(dict|None,bool)`，`main` 用 `index = index or {}` 归一；退出码常量四处同名。
- **非目标守卫**：未跑 `lint check`；未加 heartbeat；未改被 shell out 的五个工具本体。
