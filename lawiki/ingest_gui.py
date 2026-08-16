#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AnyDocsMarked 摄入 GUI 前端（tkinter，仅标准库）。

薄前端：shell out 到 `ingest.py`（与 agent 同一 CLI），读其 stderr 刷进度条，
进程退出后读 ingest-report.json 呈现结果。不重实现任何门/退出码/consent 逻辑。
"""
from __future__ import annotations

import json
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

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


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    IngestApp().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
