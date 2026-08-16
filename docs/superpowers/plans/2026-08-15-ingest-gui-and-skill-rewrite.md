# 摄入 GUI 前端 + SKILL 改写 实现 Plan（Plan B）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **前置：Plan A（引擎 + CLI）已落地**——GUI 通过 shell out 到 `ingest.py` 运行，本 plan 不重实现引擎。

**Goal:** 给前半段（摄入引擎）加一个 **tkinter GUI 前端**：人可直接掌控（选本地/云端、贴 token、看 OCR 进度条、完成提示），也可由 agent 启动后交给人。并改写 lawiki `SKILL.md` 的 build 流水线，令 agent「**启动 GUI 交用户，或无头跑 CLI**」，以 `ingest-report.json` 为恢复信号。

**Architecture:** 新增 `lawiki/ingest_gui.py`（tkinter，stdlib）。**GUI 不重实现任何门/退出码逻辑**——它 shell out 到 `python ingest.py <case> ...`（与 agent 完全相同的 CLI），管道读其 stderr 逐行喂进度条，进程退出后读 `<case>/ingest-report.json` + 退出码呈现结果。纯展示逻辑（进度行解析、报告摘要、配置读写）抽成无 tk 依赖的函数单测；tkinter 窗口本体按 `install.py` 先例（无单测）以手工/E2E 冒烟验证。

**Tech Stack:** Python 3.11+ 标准库（tkinter / threading / queue / subprocess / json / pathlib / webbrowser / os）；测试用 stdlib `unittest`。

**Spec:** `docs/superpowers/specs/2026-08-15-tool-vs-llm-architecture-and-ingest-orchestrator-design.md`

## Global Constraints

- **仅标准库**：tkinter 随官方 Python（Windows/macOS）自带；不引入 Electron/PyQt/web 框架。
- **GUI 是薄前端**：门/退出码/report/consent 单一来源在引擎（`ingest.py`）；GUI **只采集输入 + 呈现结果 + shell out**，绝不另写门逻辑、**无绕门按钮**。
- **consent 仍由引擎守**：GUI「我同意上传」勾选 → 传 `--cloud-consent` 给 `ingest.py`；真正拦截静默上传的是 makeitdown 的 consent 门。GUI 不下放隐私门。
- **token 不落盘明文**：云端 token 优先从环境变量 `PADDLEOCR_AISTUDIO_TOKEN` 读；GUI 输入的 token 通过**子进程环境变量**传给 `ingest.py`，不写进任何配置文件（可选「记住」→ 提示用户自行 `setx`，不代写）。
- **UI 不冻结**：长任务 OCR 在 worker 线程跑；线程经 `queue.Queue` 回传进度/日志，主线程 `root.after()` 轮询刷新（tkinter 单线程 UI 铁律，禁止在子线程碰 widget）。
- **进度为 best-effort**：进度条解析 makeitdown 的 `[k/N] ✓ 路径 (t)` stderr 行；解析失败只是进度条不前进，**不影响正确性**（完成仍以退出码 + report 为准）。
- **Windows 优先**：`sys.stdout.reconfigure(encoding="utf-8")`；子进程 `encoding="utf-8", errors="replace"`。
- **测试运行**：`cd lawiki && python -m pytest test_ingest_gui.py -q`；GUI 手工冒烟另见各任务。

---

## File Structure

- **Create `lawiki/ingest_gui.py`** — tkinter GUI。纯函数：`parse_progress_line`、`summarize_report`、`load_gui_config`/`save_gui_config`、`resolve_engine_argv`；tk 类：`IngestApp`（选择屏 / 运行屏 / 完成屏）。
- **Create `lawiki/test_ingest_gui.py`** — stdlib unittest，只测纯函数（不实例化 tk）。
- **Modify `lawiki/skill/lawiki/SKILL.md`** — build 流水线第一/二/二半步收敛为「跑摄入（GUI 或 CLI）」，以 `ingest-report.json` 为恢复信号；第三步（LLM ingest）不变。
- **Modify `lawiki/skill/lawiki/references/setup.md`** — 增补「GUI 用法」小节。
- 不改引擎（`ingest.py`）与任何被 shell out 的工具。

GUI 三屏流转：**选择屏**（folder picker + 本地/云端权衡 + 云端 token/consent）→ **运行屏**（进度条 + 逐文件日志，子进程跑 `ingest.py`）→ **完成屏**（读 report 显示 gate 结果 + 未处置项 + 下一步）。

---

### Task 1: 纯展示逻辑（进度解析 / 报告摘要 / 引擎参数 / 配置）

**Files:**
- Create: `lawiki/ingest_gui.py`（骨架：imports、四个纯函数）
- Test: `lawiki/test_ingest_gui.py`

**Interfaces:**
- Produces:
  - `parse_progress_line(line: str) -> dict | None` — 解析 makeitdown 逐文件行 `[k/N] <glyph> <path> ...`；返回 `{"done": int, "total": int, "status": str, "path": str}` 或 `None`（非进度行）。`glyph→status`：`✓`→succeeded、`⚠`→warned、`✗`→failed、`=`→skipped_existing、`→`→skipped_unsupported。
  - `summarize_report(merged: dict) -> str` — 把 `ingest-report.json` 转成中文人读摘要（gate 通过与否、退出码、各阶段计数、未处置项）。
  - `resolve_engine_argv(engine: str, cloud_consent: bool) -> list[str]` — 返回追加给 `ingest.py` 的参数片段（如 `["--ocr-engine","cloud","--cloud-consent"]`）。
  - `load_gui_config(path: Path) -> dict` / `save_gui_config(path: Path, cfg: dict) -> None` — 读写 `{"engine": "auto|local|cloud"}`（**不含 token**）；文件缺失时 `load` 返回 `{"engine": "auto"}`。

- [ ] **Step 1: 写失败测试**

创建 `lawiki/test_ingest_gui.py`：

```python
# -*- coding: utf-8 -*-
"""ingest_gui.py 纯逻辑测试（stdlib unittest；不实例化 tkinter）。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import ingest_gui as g  # noqa: E402


class ParseProgressTests(unittest.TestCase):
    def test_success_line(self):
        d = g.parse_progress_line("[12/87] ✓ 合同/采购框架.pdf (8.2s)")
        self.assertEqual(d, {"done": 12, "total": 87, "status": "succeeded",
                             "path": "合同/采购框架.pdf (8.2s)"})

    def test_failed_line(self):
        d = g.parse_progress_line("[14/87] ✗ 损坏.pdf — PDFSyntaxError: x")
        self.assertEqual(d["status"], "failed")
        self.assertEqual((d["done"], d["total"]), (14, 87))

    def test_non_progress_line_returns_none(self):
        self.assertIsNone(g.parse_progress_line("[lawiki-ingest] 将执行: makeitdown ..."))
        self.assertIsNone(g.parse_progress_line(""))


class SummarizeTests(unittest.TestCase):
    def test_pass(self):
        merged = {"gate": {"passed": True, "reasons": []}, "exit_code": 0,
                  "stages": {"convert": {"succeeded": 3, "warned": 0, "failed": 0,
                                         "skipped_existing": 0, "skipped_unsupported": 0},
                             "index": {"ran": True, "files_indexed": 3},
                             "source_reconcile": {"unresolved": []}}}
        s = g.summarize_report(merged)
        self.assertIn("通过", s)

    def test_fail_lists_reasons(self):
        merged = {"gate": {"passed": False, "reasons": ["[未处置源级遗漏] 原始资料/x.doc"]},
                  "exit_code": 3, "stages": {"convert": {}, "index": {"ran": False},
                  "source_reconcile": {"unresolved": ["[未处置源级遗漏] 原始资料/x.doc"]}}}
        s = g.summarize_report(merged)
        self.assertIn("未通过", s)
        self.assertIn("x.doc", s)


class EngineArgvTests(unittest.TestCase):
    def test_cloud_with_consent(self):
        self.assertEqual(g.resolve_engine_argv("cloud", True),
                         ["--ocr-engine", "cloud", "--cloud-consent"])

    def test_local_no_consent(self):
        self.assertEqual(g.resolve_engine_argv("local", False), ["--ocr-engine", "local"])


class ConfigTests(unittest.TestCase):
    def test_load_missing_defaults_auto(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(g.load_gui_config(Path(td) / "cfg.json"), {"engine": "auto"})

    def test_roundtrip_without_token(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "cfg.json"
            g.save_gui_config(p, {"engine": "cloud", "token": "SECRET"})
            loaded = g.load_gui_config(p)
            self.assertEqual(loaded["engine"], "cloud")
            self.assertNotIn("token", json.loads(p.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd lawiki && python -m pytest test_ingest_gui.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ingest_gui'`。

- [ ] **Step 3: 写最小实现**

创建 `lawiki/ingest_gui.py`（本任务只写 imports + 四个纯函数；tk 类下一任务）：

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AnyDocsMarked 摄入 GUI 前端（tkinter，仅标准库）。

薄前端：shell out 到 `ingest.py`（与 agent 同一 CLI），读其 stderr 刷进度条，
进程退出后读 ingest-report.json 呈现结果。不重实现任何门/退出码/consent 逻辑。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_PROGRESS_RE = re.compile(r"^\[(\d+)/(\d+)\]\s+(.)\s+(.*)$")
_GLYPH_STATUS = {"✓": "succeeded", "⚠": "warned", "✗": "failed",
                 "=": "skipped_existing", "→": "skipped_unsupported"}


def parse_progress_line(line: str) -> dict | None:
    m = _PROGRESS_RE.match(line.rstrip("\n"))
    if not m:
        return None
    status = _GLYPH_STATUS.get(m.group(3))
    if status is None:
        return None
    return {"done": int(m.group(1)), "total": int(m.group(2)),
            "status": status, "path": m.group(4)}


def summarize_report(merged: dict) -> str:
    gate = merged.get("gate", {})
    conv = merged.get("stages", {}).get("convert", {})
    idx = merged.get("stages", {}).get("index", {})
    head = "✅ 摄入完成，完整性门 **通过**" if gate.get("passed") else \
           f"⚠ 摄入完成，完整性门 **未通过**（退出码 {merged.get('exit_code')}）"
    lines = [head,
             f"转换：成功 {conv.get('succeeded', 0)} / 警告 {conv.get('warned', 0)} / "
             f"失败 {conv.get('failed', 0)} / 跳过 {conv.get('skipped_unsupported', 0)}",
             ("索引：已建 " + str(idx.get("files_indexed", 0)) + " 个") if idx.get("ran")
             else "索引：未建（未装 rag 或 --skip-index，问答退化仅 wiki）"]
    reasons = gate.get("reasons", [])
    if reasons:
        lines.append("需处理：")
        lines.extend("  • " + r for r in reasons)
    else:
        lines.append("下一步：让 agent 加载 lawiki，对 _md/ 建 wiki。")
    return "\n".join(lines)


def resolve_engine_argv(engine: str, cloud_consent: bool) -> list[str]:
    argv = ["--ocr-engine", engine]
    if cloud_consent:
        argv.append("--cloud-consent")
    return argv


def load_gui_config(path: Path) -> dict:
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"engine": "auto"}
    return {"engine": cfg.get("engine", "auto")}


def save_gui_config(path: Path, cfg: dict) -> None:
    # 只持久化 engine；token 绝不落盘。
    path.write_text(json.dumps({"engine": cfg.get("engine", "auto")},
                               ensure_ascii=False, indent=2), encoding="utf-8")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd lawiki && python -m pytest test_ingest_gui.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add lawiki/ingest_gui.py lawiki/test_ingest_gui.py
git commit -m "feat(ingest-gui): pure helpers — progress parse, report summary, engine argv, config"
```

---

### Task 2: GUI 选择屏（folder picker + 本地/云端权衡 + 云端 token/consent）

**Files:**
- Modify: `lawiki/ingest_gui.py`（追加 `IngestApp` 类的选择屏 + `main()`）

**Interfaces:**
- Consumes: `load_gui_config`/`save_gui_config`/`resolve_engine_argv`
- Produces: `IngestApp(tk.Tk)`：属性 `case_dir: Path|None`、`engine: tk.StringVar`、`consent: tk.BooleanVar`、`token_var: tk.StringVar`；方法 `_build_choice_screen()`、`_on_pick_folder()`、`_on_start()`（本任务先只切到运行屏占位）。`main()` 实例化并 `mainloop()`。

- [ ] **Step 1: 写实现（GUI 手工验证为主，无单元测试——tk 窗口本体不实例化于 CI）**

在 `ingest_gui.py` 追加（`import tkinter as tk` 等放文件顶部 import 区）：

```python
import queue
import subprocess
import threading
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk

AISTUDIO_URL = "https://aistudio.baidu.com/paddleocr"
_TRADEOFF = (
    "本地版：文档不出本机、离线、免费；但要下载几百 MB 模型、吃电脑性能、较慢。\n"
    "云端版：装得小、快；但要去百度 AI Studio 申请 token、需联网、文档会上传。\n"
    "auto：装了本地就用本地，否则在你已配 token+同意时才用云端，绝不静默上传。\n\n"
    "⚖ 案卷含机密材料时优先本地；机器弱又非机密可选云端。"
)


class IngestApp(tk.Tk):
    def __init__(self, config_path: Path | None = None):
        super().__init__()
        self.title("AnyDocsMarked · 案卷摄入")
        self.geometry("640x520")
        self._config_path = config_path or (Path.home() / ".anydocsmarked-gui.json")
        self.case_dir: Path | None = None
        cfg = load_gui_config(self._config_path)
        self.engine = tk.StringVar(value=cfg["engine"])
        self.consent = tk.BooleanVar(value=False)
        self.token_var = tk.StringVar(value="")
        self._proc: subprocess.Popen | None = None
        self._q: queue.Queue = queue.Queue()
        self._container = tk.Frame(self)
        self._container.pack(fill="both", expand=True, padx=16, pady=16)
        self._build_choice_screen()

    def _clear(self) -> None:
        for w in self._container.winfo_children():
            w.destroy()

    def _build_choice_screen(self) -> None:
        self._clear()
        tk.Label(self._container, text="① 选择案件目录（含 原始资料/）",
                 font=("", 12, "bold")).pack(anchor="w")
        row = tk.Frame(self._container); row.pack(fill="x", pady=6)
        self._folder_lbl = tk.Label(row, text="（未选择）", fg="gray")
        self._folder_lbl.pack(side="left")
        tk.Button(row, text="选择文件夹…", command=self._on_pick_folder).pack(side="right")

        tk.Label(self._container, text="② OCR 方式", font=("", 12, "bold")).pack(anchor="w", pady=(12, 0))
        tk.Message(self._container, text=_TRADEOFF, width=580, fg="#333").pack(anchor="w")
        for val, txt in [("auto", "auto（推荐）"), ("local", "本地版"), ("cloud", "云端版")]:
            tk.Radiobutton(self._container, text=txt, variable=self.engine, value=val,
                           command=self._refresh_cloud_box).pack(anchor="w")

        self._cloud_box = tk.Frame(self._container)
        self._cloud_box.pack(fill="x", pady=8)
        self._refresh_cloud_box()

        tk.Button(self._container, text="开始摄入 ▶", font=("", 12, "bold"),
                  command=self._on_start).pack(pady=16)

    def _refresh_cloud_box(self) -> None:
        for w in self._cloud_box.winfo_children():
            w.destroy()
        if self.engine.get() not in ("cloud", "auto"):
            return
        tk.Label(self._cloud_box, text="云端 token（仅云端时需要；留空则本地/auto 不受影响）"
                 ).pack(anchor="w")
        r = tk.Frame(self._cloud_box); r.pack(fill="x")
        tk.Entry(r, textvariable=self.token_var, show="•", width=48).pack(side="left")
        tk.Button(r, text="去申请", command=lambda: webbrowser.open(AISTUDIO_URL)).pack(side="left", padx=6)
        tk.Checkbutton(self._cloud_box, text="我已知晓并同意：云端会把文档上传至百度 AI Studio",
                       variable=self.consent).pack(anchor="w", pady=4)

    def _on_pick_folder(self) -> None:
        d = filedialog.askdirectory()
        if d:
            self.case_dir = Path(d)
            self._folder_lbl.config(text=str(self.case_dir), fg="black")

    def _on_start(self) -> None:
        if self.case_dir is None:
            messagebox.showwarning("缺少目录", "请先选择案件目录。"); return
        if self.engine.get() == "cloud" and not self.consent.get():
            messagebox.showwarning("需要同意", "云端会上传文档，请勾选同意，或改用 本地/auto。"); return
        save_gui_config(self._config_path, {"engine": self.engine.get()})
        self._build_run_screen()  # 下一任务实现

    # 占位，Task 3 实现
    def _build_run_screen(self) -> None:
        self._clear()
        tk.Label(self._container, text="（运行屏占位——Task 3 实现）").pack()


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    IngestApp().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: 手工冒烟**

Run: `cd lawiki && python ingest_gui.py`
Expected: 弹出窗口；能选文件夹；切换 auto/local/cloud 时云端 token 框显隐正确；「去申请」打开浏览器到 AI Studio；cloud 未勾同意点开始弹告警。（关窗结束。）

- [ ] **Step 3: 跑纯逻辑回归确认未破坏**

Run: `cd lawiki && python -m pytest test_ingest_gui.py -q`
Expected: PASS（纯函数不受 tk 追加影响）。

- [ ] **Step 4: 提交**

```bash
git add lawiki/ingest_gui.py
git commit -m "feat(ingest-gui): choice screen — folder picker, engine tradeoff, cloud token/consent"
```

---

### Task 3: GUI 运行屏（worker 线程 shell out `ingest.py` + 进度条）

**Files:**
- Modify: `lawiki/ingest_gui.py`（实现 `_build_run_screen` + 线程/队列/`after` 泵）

**Interfaces:**
- Consumes: `parse_progress_line`、`resolve_engine_argv`、`self.case_dir`、`self.engine`、`self.consent`、`self.token_var`
- Produces: `_build_run_screen()`、`_worker()`（子线程）、`_pump()`（`after` 泵）、切到完成屏调 `_build_done_screen(exit_code)`（Task 4）。

- [ ] **Step 1: 写实现**

替换占位 `_build_run_screen`，并追加 `_worker`/`_pump`：

```python
    def _build_run_screen(self) -> None:
        self._clear()
        tk.Label(self._container, text="正在摄入…（可后台运行，勿关窗）",
                 font=("", 12, "bold")).pack(anchor="w")
        self._bar = ttk.Progressbar(self._container, mode="determinate", maximum=1)
        self._bar.pack(fill="x", pady=8)
        self._stat = tk.Label(self._container, text="启动中…", fg="#333")
        self._stat.pack(anchor="w")
        self._logbox = tk.Text(self._container, height=16, wrap="none")
        self._logbox.pack(fill="both", expand=True, pady=8)
        t = threading.Thread(target=self._worker, daemon=True)
        t.start()
        self.after(150, self._pump)

    def _worker(self) -> None:
        # 与 agent 完全相同的 CLI；token 只经子进程环境变量注入，不落盘。
        ingest_py = Path(__file__).resolve().parent / "ingest.py"
        argv = [sys.executable, str(ingest_py), str(self.case_dir),
                *resolve_engine_argv(self.engine.get(), self.consent.get())]
        env = dict(**__import__("os").environ)
        if self.token_var.get().strip():
            env["PADDLEOCR_AISTUDIO_TOKEN"] = self.token_var.get().strip()
        try:
            proc = subprocess.Popen(
                argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1, env=env)
        except Exception as e:  # noqa: BLE001
            self._q.put(("error", str(e))); return
        self._proc = proc
        for line in proc.stdout:  # type: ignore[union-attr]
            self._q.put(("line", line.rstrip("\n")))
        proc.wait()
        self._q.put(("done", proc.returncode))

    def _pump(self) -> None:
        try:
            while True:
                kind, payload = self._q.get_nowait()
                if kind == "line":
                    self._logbox.insert("end", payload + "\n")
                    self._logbox.see("end")
                    prog = parse_progress_line(payload)
                    if prog:
                        self._bar.config(maximum=prog["total"], value=prog["done"])
                        self._stat.config(text=f"{prog['done']}/{prog['total']} · {prog['path']}")
                elif kind == "error":
                    messagebox.showerror("启动失败", payload)
                    self._build_choice_screen(); return
                elif kind == "done":
                    self._build_done_screen(payload); return
        except queue.Empty:
            pass
        self.after(150, self._pump)

    # 占位，Task 4 实现
    def _build_done_screen(self, exit_code: int) -> None:
        self._clear()
        tk.Label(self._container, text=f"（完成屏占位 exit={exit_code}——Task 4 实现）").pack()
```

- [ ] **Step 2: 手工冒烟（需已装 makeitdown；否则看 preflight 报错路径）**

Run: `cd lawiki && python ingest_gui.py` → 选一个含 `原始资料/`（放几个 .txt/.pdf）的目录 → local → 开始。
Expected: 进度条随 `[k/N]` 前进、日志滚动；无 makeitdown 时子进程走 preflight，日志出现「✗ 未找到 makeitdown …」并最终进入完成屏占位。窗口全程不冻结。

- [ ] **Step 3: 纯逻辑回归**

Run: `cd lawiki && python -m pytest test_ingest_gui.py -q`
Expected: PASS。

- [ ] **Step 4: 提交**

```bash
git add lawiki/ingest_gui.py
git commit -m "feat(ingest-gui): run screen — threaded ingest.py subprocess + live progress bar"
```

---

### Task 4: GUI 完成屏（读 report + gate 结果 + 下一步）

**Files:**
- Modify: `lawiki/ingest_gui.py`（实现 `_build_done_screen`）

**Interfaces:**
- Consumes: `summarize_report`、`self.case_dir`
- Produces: `_build_done_screen(exit_code: int)` — 读 `<case>/ingest-report.json`，用 `summarize_report` 展示；按钮：打开报告文件所在目录、重新摄入（回选择屏）、复制「下一步」提示。**无绕门按钮**；未通过时如实列出未处置项与建议（装转换器 / 建 wiki 时登记跳过），不代写 log.md。

- [ ] **Step 1: 写实现**

替换占位 `_build_done_screen`：

```python
    def _build_done_screen(self, exit_code: int) -> None:
        self._clear()
        report_path = (self.case_dir / "ingest-report.json") if self.case_dir else None
        summary = f"引擎退出码 {exit_code}，但未找到 ingest-report.json（可能前置失败）。"
        if report_path and report_path.is_file():
            try:
                summary = summarize_report(json.loads(report_path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass
        tk.Label(self._container, text="摄入结束", font=("", 13, "bold")).pack(anchor="w")
        box = tk.Text(self._container, height=16, wrap="word")
        box.insert("1.0", summary); box.config(state="disabled")
        box.pack(fill="both", expand=True, pady=8)
        row = tk.Frame(self._container); row.pack(fill="x")
        if report_path and report_path.is_file():
            tk.Button(row, text="打开报告位置",
                      command=lambda: webbrowser.open(report_path.parent.as_uri())).pack(side="left")
        tk.Button(row, text="重新摄入", command=self._build_choice_screen).pack(side="left", padx=6)
        tk.Button(row, text="关闭", command=self.destroy).pack(side="right")
        if exit_code != 0:
            messagebox.showwarning("完整性提醒",
                                   "有需要处理的项，详见窗口内摘要（未静默放过）。")
        else:
            messagebox.showinfo("完成", "摄入完成、完整性门通过。可让 agent 继续建 wiki。")
```

- [ ] **Step 2: 手工冒烟**

Run: 用一个只含可转文件的目录跑通 → 完成屏显示「通过」摘要 + 完成弹窗；再用一个含无法转换的 `.doc`（无 LibreOffice）的目录 → 完成屏显示「未通过」+ 列出该 `.doc` 未处置 + 提醒弹窗。「打开报告位置」能打开目录。
Expected: 两种路径都如实呈现，无绕门按钮。

- [ ] **Step 3: 纯逻辑回归**

Run: `cd lawiki && python -m pytest test_ingest_gui.py -q`
Expected: PASS。

- [ ] **Step 4: 提交**

```bash
git add lawiki/ingest_gui.py
git commit -m "feat(ingest-gui): done screen — report summary, honest gate result, next-step"
```

---

### Task 5: 改写 SKILL.md build 流水线 + setup.md GUI 用法

**Files:**
- Modify: `lawiki/skill/lawiki/SKILL.md`（「流水线」图注 + 第一/二/二半步 → 收敛为「跑摄入」；第三步不变）
- Modify: `lawiki/skill/lawiki/references/setup.md`（增补「GUI 用法」）

**Interfaces:** 无代码接口；文字须与 `ingest.py`/`ingest_gui.py` 的命令、参数、退出码一致。

- [ ] **Step 1: 改 SKILL.md**

把「第一步 / 第二步 / 第二步半」三节替换为下面一节（第〇步 setup、第三步 ingest 及其后全部保留不变）：

```markdown
## 第一步：跑摄入（一步到位，两种入口）

前半段（建案脚手架 + 闭世界锚点 → makeitdown 转换 → RAG 建索引 → 源级对账 →
确定性完整性门）已收敛为**一个引擎、两个前端**，你按场景二选一：

- **交用户自助（推荐给非技术用户）**：启动 GUI 让用户自己选本地/云端、（云端）贴
  token、看进度条、拿完成提示——
  `python <SKILL_DIR>/../../ingest_gui.py`（即 bundle 根的 `ingest_gui.py`）。
  告诉用户「窗口里跟着做，完成后回来叫我继续」。
- **无头自动（headless / 你直接驱动）**：
  `python <SKILL_DIR>/../../ingest.py <案件根目录> [--ocr-engine auto|local|cloud] [--cloud-consent]`

**恢复信号（两种入口相同）**：以 `<案件根目录>/ingest-report.json` 出现 + 进程退出码
为完成信号。退出码：`0` 全通过；`1` 转换有硬失败；`2` 前置/环境缺失（无原始资料 /
未装 makeitdown / 选云端未同意）；`3` 完整性门未过（源级未处置>0 或索引不全）。
**非 0 时读 report 的 `gate.reasons` 向用户如实汇报**，别跳过：失败/跳过的文件不要
凭空补内容，按缺失处理——补装转换器重跑，或在 `wiki/log.md` 登记 skip（`原始资料/<相对
路径>` + 非空原因，格式见 `page-formats.md`）并显式告知用户。索引未建（未装 rag）不阻塞，
问答退化仅 wiki。

> 引擎已幂等跑 `init_case`（脚手架 + `AGENTS.md`/`CLAUDE.md` 闭世界锚点）、`makeitdown`、
> `rag index`、`reconcile`，你无需再逐个手调这些脚本。边界止于 `_md` + `.rag`：把散文
> 变成带锚点的 wiki 是下面第三步的 LLM 工作，**不在引擎内**。
```

同时把「流水线」代码块图注保持不变（它已画出 `原始资料→_md→wiki/.rag`），并在其下一行补一句：`_md`/`.rag` 由第一步的摄入引擎产出。

- [ ] **Step 2: 改 setup.md**

在 Plan A 增补的「一步摄入（CLI）」小节后追加：

```markdown
### GUI 用法（给非技术用户）

不想用命令行时，双击/运行 `ingest_gui.py` 打开图形界面：选案件目录 → 图形化选本地/云端
（云端可直接点「去申请」打开 token 页并粘贴）→ 看 OCR 进度条 → 完成弹窗。它底层跑的是
和上面 CLI 完全一样的引擎与完整性门，只是把选择、进度、结果做成了可视界面；token 仅在
本次运行经环境变量传入、不写盘。
```

- [ ] **Step 3: 一致性核对**

核对：GUI/CLI 路径（`ingest_gui.py` / `ingest.py` 相对 `<SKILL_DIR>` 的位置）、参数、退出码 `0/1/2/3`、恢复信号（`ingest-report.json`）三处描述互相一致，且与两个 py 的实现一致。

- [ ] **Step 4: 提交**

```bash
git add lawiki/skill/lawiki/SKILL.md lawiki/skill/lawiki/references/setup.md
git commit -m "docs(lawiki): rewrite build pipeline to one-step ingest (GUI/CLI) + report-signal handoff"
```

---

## 落地后的整体回归

```bash
cd lawiki && python -m pytest skill/lawiki scripts test_install.py test_ingest.py test_ingest_gui.py -q
cd .. && uvx ruff check --select E9,F lawiki/ingest_gui.py lawiki/test_ingest_gui.py
git diff --check
```

外加**跨平台手工冒烟**（Windows 必做，macOS 尽量）：GUI 三屏走通一遍（通过路径 + 未通过路径），确认进度条、完成弹窗、报告摘要、无绕门按钮。

## 自查（对照 spec）

- **spec 覆盖**：GUI 给人直连通道（进度条/申请链接/完成提示）→ Task 2–4；GUI 薄前端、shell out 同一 CLI、不另写门 → Task 3 `_worker`；consent 由引擎守、GUI 勾选仅传 flag → `resolve_engine_argv` + Task 2 校验；token 不落盘、经子进程 env → Task 1 `save_gui_config` 剥 token + Task 3 env 注入；无绕门按钮 → Task 4；tkinter stdlib、worker 线程 + `after` 泵 → Task 3；GUI 收编「本地/云端选择 + token」→ Task 2；SKILL 改写令 build 必走摄入、以 report 为恢复信号 → Task 5。
- **占位符扫描**：Task 2/3 的「占位」方法在 Task 3/4 被真实实现替换；无遗留 TODO。
- **类型一致性**：`parse_progress_line` 返回键（done/total/status/path）在 Task 3 `_pump` 一致消费；`summarize_report` 读的 stages 键（convert/index/source_reconcile）与 Plan A `_gate_and_merge` 产出一致；`resolve_engine_argv` 顺序（`--ocr-engine <e> [--cloud-consent]`）与 `ingest.py` 参数一致。
- **非目标守卫**：GUI 不含 LLM、不建 wiki、不做问答；不代写 log.md（未通过时只提示，不自动登记跳过）；不引入非 stdlib 依赖。
```
