#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AnyDocsMarked 摄入 GUI 前端（tkinter，仅标准库）。

薄前端：shell out 到 `ingest.py`（与 agent 同一 CLI），读其 stderr 刷进度条，
进程退出后读 ingest-report.json 呈现结果。不重实现任何门/退出码/consent 逻辑。
"""
from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from datetime import date
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import ingest  # 须先于 capabilities：其导入把 skill/lawiki/tools 加进 sys.path
import install  # 复用其环境检测（_verify/_check_answer_gate_ready），不另写一套
from capabilities import answer_persist_map, build_gui_fields, load_capabilities

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


def probe_environment() -> dict:
    """开窗即体检：各组件装了没/就绪没。复用 install.py 的 shell 探活（不跨模块 import
    引擎 Python）。best-effort——探测本身失败按"未就绪"，绝不抛异常阻塞开窗。
    critical=True 的缺失会拦住摄入（makeitdown 缺则根本转不了）；rag 缺只降级仅 wiki。"""
    def _safe(fn, *a):
        try:
            return bool(fn(*a))
        except Exception:  # noqa: BLE001
            return False
    return {
        "makeitdown": {"ok": _safe(install._verify, ["makeitdown", "--help"]),
                       "critical": True,
                       "hint": "转换器未装——请点「安装/检查环境」。"},
        "rag": {"ok": _safe(install._verify, [*install._rag_cmd(), "--help"]),
                "critical": False,
                "hint": "语义检索未装——问答将退化为仅 wiki（可选）。"},
        "stop_hook": {"ok": _safe(install._check_answer_gate_ready),
                      "critical": False,
                      "hint": "问答后闸门未启用——可在「⓪ 环境与闸门」勾选。"},
    }


def has_critical_gap(env: dict) -> bool:
    """是否有 critical 组件缺失（缺则应禁用「开始摄入」）。"""
    return any(v.get("critical") and not v.get("ok") for v in env.values())


def _caps_by_phase(phase: str) -> list[dict]:
    return [c for c in load_capabilities()
            if c["tier"] == "CHOICE" and c.get("phase") == phase]


def choice_controls() -> list[dict]:
    """契约里 phase=ingest 的 CHOICE 能力（GUI「高级」区该渲染的开关；不含 OCR 引擎那种
    inputs-only 常显项）。单一来源于 capabilities.json。"""
    return [c for c in _caps_by_phase("ingest")
            if (c.get("sanctioned") or {}).get("gui_control")]


def _cap_enabled(cap: dict, options: dict) -> bool:
    """有 gui_control 开关的按开关；inputs-only（如 OCR 引擎）视为常启用。"""
    gc = (cap.get("sanctioned") or {}).get("gui_control")
    return bool(options.get(gc, False)) if gc else True


def _flags_for(phase: str, options: dict) -> list[str]:
    """契约驱动地把某一 phase 里启用的 CHOICE 拼成 CLI 标志：sanctioned.ingest_flag +
    各 select/bool 类 input 的 flag。text/secret 类走环境变量，不进 argv。ingest 与 install
    共用同一机制。"""
    argv: list[str] = []
    for cap in _caps_by_phase(phase):
        if not _cap_enabled(cap, options):
            continue
        s = cap.get("sanctioned") or {}
        if s.get("gui_control") and s.get("ingest_flag"):
            argv.append(s["ingest_flag"])
        for inp in cap.get("inputs", []):
            flag = inp.get("flag")
            if not flag:
                continue
            val = options.get(inp["id"])
            if inp["kind"] == "select" and val:
                argv += [flag, str(val)]
            elif inp["kind"] == "bool" and val:
                argv.append(flag)
    return argv


def build_ingest_argv(options: dict) -> list[str]:
    """ingest.py 的标志（契约 phase=ingest）。纯函数，GUI 与契约测试共用。"""
    return _flags_for("ingest", options)


def build_install_argv(options: dict) -> list[str]:
    """install.py 的标志（契约 phase=install，如 --ocr <local|cloud>）。"""
    return _flags_for("install", options)


def build_ingest_env(options: dict) -> dict[str, str]:
    """phase=ingest 的 text/secret 类 input → 子进程环境变量（token/凭证只经 env、不落盘）。
    仅当所属 CHOICE 启用、且用户填了值时注入。"""
    env: dict[str, str] = {}
    for cap in _caps_by_phase("ingest"):
        if not _cap_enabled(cap, options):
            continue
        for inp in cap.get("inputs", []):
            name = inp.get("env")
            val = options.get(inp["id"])
            # 任何声明了 env 的 input（非 flag 型）→ 环境变量；不按 kind 硬编码。
            if name and not inp.get("flag") and val:
                env[name] = str(val)
    return env


def write_case_config(case: Path, options: dict) -> None:
    """把 answer 期非密选择写进 <case>/.anydocsmarked/case.json（rag.py 读它注入 env）。
    没有可持久化项时不建文件。secret 由 build_case_config 排除、绝不落盘。"""
    cfg = build_case_config(options)
    if not cfg:
        return
    d = case / ".anydocsmarked"
    d.mkdir(parents=True, exist_ok=True)
    (d / "case.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


# 只锚定稳定的 原始资料/<路径> 结构（SOURCE_DIR 是产品核心约定），不锚定 reconcile 的
# 中文标签措辞——那不是稳定契约（reconcile 用「未处置源级遗漏」、lint 用「未处置」）。汇总行
# 「原始资料/ 有 N 个…」的 / 后是空格，\S 天然不匹配、自动排除。
# （理想形态是 reconcile/ingest 在报告里结构化暴露未处置文件列表，GUI 直接读，免解析散文；
#  见 spec 后续。此处先去掉最脆的标签耦合。）
_UNRESOLVED_FILE_RE = re.compile(r"(原始资料/\S.*)$")


def unresolved_source_files(report: dict) -> list[str]:
    """从 ingest-report 的源级对账里取出**逐个未处置源文件**的路径（原始资料/…），供 done
    屏让用户裁决。优先读结构化字段 unresolved_files（ingest 新版直接暴露）；旧报告没有该字段
    时回退解析人读字符串（汇总提示自动排除）。"""
    sr = report.get("stages", {}).get("source_reconcile", {}) or {}
    if "unresolved_files" in sr:  # 新版结构化字段，免解析散文
        return list(sr["unresolved_files"])
    out: list[str] = []
    for line in sr.get("unresolved", []):
        m = _UNRESOLVED_FILE_RE.search(line)
        if m:
            out.append(m.group(1))
    return out


def append_skip_log(case: Path, source_rel: str, reason: str) -> None:
    """在 wiki/log.md 追加一条源级跳过登记（reconcile/lint 认的格式），把"未处置"变成
    "已登记跳过（带原因）"——完整性门随即放行该文件。reason 必须非空。"""
    reason = reason.strip() or "（用户在 GUI 登记跳过，未填原因）"
    wiki = case / "wiki"
    wiki.mkdir(parents=True, exist_ok=True)
    entry = f"\n## [{date.today().isoformat()}] skip | {source_rel}\n- 原因：{reason}\n"
    with (wiki / "log.md").open("a", encoding="utf-8") as fh:
        fh.write(entry)


def write_stop_hook(case: Path, skill_dir: Path) -> None:
    """在**案件本地** <case>/.claude/settings.json 挂 Stop hook（问答后闸门）。合并既有
    settings、不覆盖其它键；绝不碰用户全局 settings。"""
    d = case / ".claude"
    d.mkdir(parents=True, exist_ok=True)
    path = d / "settings.json"
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(settings, dict):
            settings = {}
    except (OSError, ValueError):
        settings = {}
    hooks = settings.setdefault("hooks", {})
    cmd = f'python "{(skill_dir / "lint" / "stop_hook.py").as_posix()}"'
    hooks["Stop"] = [{"hooks": [{"type": "command", "command": cmd}]}]
    path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


def _input_empty(inp: dict, options: dict) -> bool:
    """输入是否"未填"。bool 类：未勾（False）即未填；其余：空串即未填。"""
    val = options.get(inp["id"])
    if inp["kind"] == "bool":
        return not bool(val)
    return not str(val or "").strip()


def validate_options(options: dict) -> list[str]:
    """选了却缺必需输入 → 报错项（人话）。**完全契约驱动**：消费每个启用 CHOICE 的 input
    required_when（含 OCR 引擎的 cloud_token/cloud_consent——它们就是 ocr_engine 能力的
    inputs，required_when=engine∈[cloud]）。bool 输入未勾即视为缺。纯函数：GUI 即时红字与
    _on_start 最终拦截共用。"""
    errs: list[str] = []
    for cap in load_capabilities():
        if cap["tier"] != "CHOICE" or not _cap_enabled(cap, options):
            continue
        for inp in cap.get("inputs", []):
            rw = inp.get("required_when")
            if not rw:
                continue
            required = all(options.get(k) in vals for k, vals in rw.items())
            if required and _input_empty(inp, options):
                errs.append(f"「{inp['label']}」为必填（当前选择下需要）")
    return errs


def recheck_unresolved(case: Path) -> int:
    """原地重跑源级对账、返回未处置数（复用 reconcile.reconcile 纯函数）。裁决后无需整轮
    重摄即可确认门是否放行。取不到（缺 report 等）→ 返回 -1 让调用方提示先摄入。"""
    try:
        from reconcile import reconcile
        unresolved, _ = reconcile(case)
    except Exception:  # noqa: BLE001  缺 report/环境问题 → 不可判
        return -1
    return len(unresolved)


def exit_advice(exit_code: int) -> str:
    """把 ingest.py 的退出码翻成"发生了什么 + 建议"（人话，非裸码）。"""
    return {
        0: "✅ 完整性门通过。可回到对话让 agent 建 wiki。",
        1: "转换有硬失败：见摘要/日志的 failures，修好来源后「重新摄入」。",
        2: "前置或环境缺失：常见是未装 makeitdown、或选了云端却没加同意。请先「安装/检查环境」。",
        3: "完整性门未过：有未处置源文件或索引不全。见下方逐个「处置」，或补装 rag 后重摄。",
    }.get(exit_code, f"引擎退出码 {exit_code}——详见日志。")


def build_case_config(options: dict) -> dict:
    """answer 期非密选择 → 写进 <case>/.anydocsmarked/case.json 的内容（rag.py 读它注入 env）。
    持久化键**从契约派生**（answer_persist_map，单一来源）；secret 天然不在其中、绝不落盘。
    空/False 略去。"""
    cfg: dict = {}
    for key in answer_persist_map():
        val = options.get(key)
        if val not in (None, "", False):
            cfg[key] = val
    return cfg


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
    def __init__(self, config_path: Path | None = None, case_dir: Path | None = None):
        super().__init__()
        self.title("AnyDocsMarked · 案卷摄入")
        self.geometry("640x520")
        self._config_path = config_path or (Path.home() / ".anydocsmarked-gui.json")
        self.case_dir: Path | None = Path(case_dir) if case_dir else None
        cfg = load_gui_config(self._config_path)
        self.engine = tk.StringVar(value=cfg["engine"])
        self.consent = tk.BooleanVar(value=False)
        self.token_var = tk.StringVar(value="")
        # 契约驱动的所有 GUI 字段变量（开关 + 各 input），键为字段 id。渲染时按需建。
        self._field_vars: dict[str, tk.Variable] = {}
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._q: queue.Queue = queue.Queue()
        self._container = tk.Frame(self)
        self._container.pack(fill="both", expand=True, padx=16, pady=16)
        self._build_choice_screen()

    def _clear(self) -> None:
        for w in self._container.winfo_children():
            w.destroy()

    def _build_choice_screen(self) -> None:
        self._clear()
        self._render_health_banner()
        tk.Label(self._container, text="① 选择案件目录（含 原始资料/）",
                 font=("", 12, "bold")).pack(anchor="w")
        row = tk.Frame(self._container); row.pack(fill="x", pady=6)
        self._folder_lbl = tk.Label(row, text=str(self.case_dir) if self.case_dir else "（未选择）",
                                    fg="black" if self.case_dir else "gray")
        self._folder_lbl.pack(side="left")
        tk.Button(row, text="选择文件夹…", command=self._on_pick_folder).pack(side="right")
        self._preview_lbl = tk.Label(self._container, text="", fg="#a60", wraplength=580, justify="left")
        self._preview_lbl.pack(anchor="w")
        self._refresh_preview()

        tk.Label(self._container, text="② OCR 方式", font=("", 12, "bold")).pack(anchor="w", pady=(12, 0))
        tk.Message(self._container, text=_TRADEOFF, width=580, fg="#333").pack(anchor="w")
        for val, txt in [("auto", "auto（推荐）"), ("local", "本地版"), ("cloud", "云端版")]:
            tk.Radiobutton(self._container, text=txt, variable=self.engine, value=val,
                           command=self._refresh_cloud_box).pack(anchor="w")

        self._cloud_box = tk.Frame(self._container)
        self._cloud_box.pack(fill="x", pady=8)
        self._refresh_cloud_box()

        self._build_section("⓪ 环境与闸门（可选）", _caps_by_phase("install"))
        tk.Button(self._container, text="安装/检查环境",
                  command=self._on_install).pack(anchor="w")
        self._build_advanced_box()

        self._start_btn = tk.Button(self._container, text="开始摄入 ▶", font=("", 12, "bold"),
                                    command=self._on_start)
        self._start_btn.pack(pady=16)
        if has_critical_gap(self._health):
            self._start_btn.config(state="disabled")
            tk.Label(self._container, text="⛔ 缺关键组件，请先「安装/检查环境」再摄入",
                     fg="#c00").pack()

    def _render_health_banner(self) -> None:
        """开窗即体检横幅：绿=全就绪 / 黄=缺可选（降级）/ 红=缺关键（拦摄入）。"""
        self._health = probe_environment()
        gaps = [v["hint"] for v in self._health.values() if not v["ok"]]
        if not gaps:
            tk.Label(self._container, text="✓ 环境就绪", fg="#080").pack(anchor="w")
            return
        red = has_critical_gap(self._health)
        tk.Label(self._container, text=("⛔ 环境缺件" if red else "⚠ 环境提示"),
                 fg="#c00" if red else "#a60", font=("", 11, "bold")).pack(anchor="w")
        for hint in gaps:
            tk.Label(self._container, text="  • " + hint,
                     fg="#c00" if red else "#a60", wraplength=580, justify="left").pack(anchor="w")

    def _on_install(self) -> None:
        """按 install 期选择直接跑 install.py（GUI 确定性执行，不叫 agent 跑）。"""
        argv = build_install_argv(self._collect_options())
        if not argv:
            messagebox.showinfo("安装环境", "未选择需安装项（在「⓪ 环境与闸门」里选 OCR 安装方式）。")
            return
        if not messagebox.askyesno("安装/检查环境",
                                   f"将运行：install.py {' '.join(argv)}\n（可能下载依赖、耗时）。继续？"):
            return
        install_py = Path(__file__).resolve().parent / "install.py"
        self._run_stream_screen(
            "正在安装环境…（勿关窗）", [sys.executable, str(install_py), *argv],
            "install_done", busy=True)

    def _base_env(self) -> dict:
        """子进程基础环境：强制 UTF-8，避免本机 GBK locale 把进度符号/中文写乱。"""
        env = os.environ.copy(); env.setdefault("PYTHONUTF8", "1")
        return env

    def _stream_screen(self, title: str, with_progress: bool, busy: bool = False) -> None:
        """跑命令屏的共用骨架：清屏 + 标题 + 日志框。with_progress=确定性进度条（摄入，
        靠 [N/M]）；busy=不确定 marquee（安装，子进程无结构化进度、避免看着像死机）。
        无进度条时把 _bar/_stat 置 None（不留上一屏残留引用）。"""
        self._clear()
        tk.Label(self._container, text=title, font=("", 12, "bold")).pack(anchor="w")
        self._bar = None
        self._stat = None
        self._marquee = None
        if with_progress:
            self._bar = ttk.Progressbar(self._container, mode="determinate", maximum=1)
            self._bar.pack(fill="x", pady=8)
            self._stat = tk.Label(self._container, text="启动中…", fg="#333")
            self._stat.pack(anchor="w")
        elif busy:
            self._marquee = ttk.Progressbar(self._container, mode="indeterminate")
            self._marquee.pack(fill="x", pady=8)
            self._marquee.start(12)
        self._logbox = tk.Text(self._container, height=16, wrap="none")
        self._logbox.pack(fill="both", expand=True, pady=8)
        tk.Button(self._container, text="中止", command=self._on_abort).pack(anchor="w")

    def _on_abort(self) -> None:
        """中止正在跑的子进程；线程读完管道后经 _pump 收尾回选择屏。"""
        proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.terminate()

    def _run_stream_screen(self, title: str, argv: list[str], done_kind: str,
                           busy: bool = False) -> None:
        """新屏跑一条命令、输出流到日志框，完成后经 _pump 的 done_kind 分流。"""
        self._stream_screen(title, with_progress=False, busy=busy)
        self._thread = threading.Thread(
            target=self._spawn_and_stream, args=(argv, self._base_env(), done_kind), daemon=True)
        self._thread.start()
        self.after(150, self._pump)

    def _spawn_and_stream(self, argv: list[str], env: dict, done_kind: str) -> None:
        """跑子进程、逐行送进队列、以 done_kind 收尾。摄入与安装两个 worker 共用同一套
        Popen 参数（encoding/bufsize/stderr 合流），一处改处处一致。"""
        try:
            proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding="utf-8", errors="replace", bufsize=1, env=env)
        except Exception as e:  # noqa: BLE001
            self._q.put(("error", str(e))); return
        self._proc = proc
        for line in proc.stdout:  # type: ignore[union-attr]
            self._q.put(("line", line.rstrip("\n")))
        proc.wait()
        self._q.put((done_kind, proc.returncode))

    def _field_var(self, field: dict) -> tk.Variable:
        """按字段 id 复用/新建 tk 变量（bool→Boolean，其余→String，select 带默认值）。"""
        fid = field["id"]
        if fid not in self._field_vars:
            if field["kind"] in ("toggle", "bool"):
                self._field_vars[fid] = tk.BooleanVar(value=False)
            else:
                self._field_vars[fid] = tk.StringVar(value="")
        return self._field_vars[fid]

    def _render_cap_fields(self, parent: tk.Widget, cap: dict) -> None:
        """契约驱动地渲染一个 CHOICE 的开关 + 各 input（build_gui_fields 单一来源）。
        text/secret→输入框（secret 掩码）；select→下拉；bool/toggle→勾选框。"""
        for field in build_gui_fields(cap):
            var = self._field_var(field)
            kind = field["kind"]
            if kind in ("toggle", "bool"):
                tk.Checkbutton(parent, text=field["label"], variable=var).pack(anchor="w")
            else:
                row = tk.Frame(parent); row.pack(fill="x", padx=16)
                tk.Label(row, text=field["label"] + "：").pack(side="left")
                if kind == "select":
                    var.set(var.get() or (field.get("options") or [""])[0])
                    ttk.Combobox(row, textvariable=var, values=field.get("options") or [],
                                 state="readonly", width=16).pack(side="left")
                else:
                    tk.Entry(row, textvariable=var, width=40,
                             show="•" if kind == "secret" else "").pack(side="left")

    def _build_section(self, title: str, caps: list[dict]) -> None:
        if not caps:
            return
        tk.Label(self._container, text=title, font=("", 12, "bold")).pack(anchor="w", pady=(12, 0))
        for cap in caps:
            tk.Message(self._container, text="⚖ " + cap.get("tradeoff", ""), width=580,
                       fg="#666").pack(anchor="w")
            self._render_cap_fields(self._container, cap)

    def _build_advanced_box(self) -> None:
        """③ 高级（可选加强，默认关）——契约中 phase=ingest 的 CHOICE（含各自的 token/凭证
        输入）。把"容易被静默跳过的加强项"连同它的必需输入一起摆到人面前。"""
        self._build_section("③ 高级（可选加强，默认关）", choice_controls())
        self._build_section("④ 问答设置（answer 期，问答时生效）", _caps_by_phase("answer"))

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

    def _movable_count(self) -> int:
        """当前 case_dir 下会被归入 原始资料/ 的散落项数（读盘失败按 0）。预览与确认共用。"""
        try:
            return len(ingest._movable_entries(self.case_dir))
        except OSError:
            return 0

    def _refresh_preview(self) -> None:
        if self.case_dir is None:
            self._preview_lbl.config(text=""); return
        n = self._movable_count()
        if n == 0:
            self._preview_lbl.config(text="✓ 无待归入的散落文件（原始资料/ 已就绪或本就为空）")
        else:
            self._preview_lbl.config(
                text=f"⚠ 将把该文件夹下的 {n} 项归入子目录 原始资料/ 再处理（原件会被移动）。"
                     "若这不是你的案件资料专用文件夹，请重选。")

    def _on_pick_folder(self) -> None:
        d = filedialog.askdirectory()
        if d:
            self.case_dir = Path(d)
            self._folder_lbl.config(text=str(self.case_dir), fg="black")
            self._refresh_preview()

    def _on_start(self) -> None:
        if self.case_dir is None:
            messagebox.showwarning("缺少目录", "请先选择案件目录。"); return
        errs = validate_options(self._collect_options())
        if errs:
            messagebox.showwarning("请先补全", "\n".join("• " + e for e in errs)); return
        # 只要还有散落待归入项就弹确认（不论 原始资料/ 是否已存在——已建库后再扔的
        # 新文件同样会被移动，同样该让用户确认）。
        n = self._movable_count()
        if n and not messagebox.askyesno(
                "确认归入原始资料",
                f"将把\n{self.case_dir}\n下的 {n} 项移动到子目录 原始资料/ 再处理。\n"
                "若这不是你的案件资料专用文件夹，请点「否」重选。\n\n确定继续？"):
            return
        save_gui_config(self._config_path, {"engine": self.engine.get()})
        self._build_run_screen()

    def _build_run_screen(self) -> None:
        self._stream_screen("正在摄入…（可后台运行，勿关窗）", with_progress=True)
        # tk.Variable.get() 走 Tcl，只能在主线程调用；在此（主线程）快照成普通 Python
        # 值再传给 worker 线程，worker 绝不碰 self.*.get()。
        options = self._collect_options()
        self._thread = threading.Thread(target=self._worker, args=(options,), daemon=True)
        self._thread.start()
        self.after(150, self._pump)

    def _collect_options(self) -> dict:
        """把所有 GUI 输入快照成普通 Python dict（供 build_ingest_argv/env/case_config 消费）。
        OCR 引擎/consent/token 走既有控件；其余契约字段走 _field_vars。"""
        opts: dict = {"engine": self.engine.get(), "cloud_consent": bool(self.consent.get()),
                      "cloud_token": self.token_var.get().strip()}
        for fid, var in self._field_vars.items():
            val = var.get()
            opts[fid] = val.strip() if isinstance(val, str) else val
        return opts

    def _worker(self, options: dict) -> None:
        # 与 agent 完全相同的 CLI；token/凭证只经子进程环境变量注入，不落盘。
        ingest_py = Path(__file__).resolve().parent / "ingest.py"
        case = self.case_dir
        # answer 期选择持久化进案件本地 case.json（rag.py 问答时读它注入 env，非委托 agent）；
        # 勾了后闸门则写案件本地 .claude/settings.json。二者确定性执行、绝不碰用户全局。
        try:
            write_case_config(case, options)
            if options.get("enable_stop_hook"):
                write_stop_hook(case, Path(__file__).resolve().parent / "skill" / "lawiki")
        except OSError as e:
            self._q.put(("error", f"写案件配置失败：{e}")); return
        argv = [sys.executable, str(ingest_py), str(case), *build_ingest_argv(options)]
        env = self._base_env()
        env.update(build_ingest_env(options))  # cross-check/OCR/LLM 的 token 与凭证
        # embedding 后端也在建索引时生效（与问答同一后端，否则模型不一致会被拒）——复用
        # rag.py 的同一映射从刚写的 case.json 派生，注入 ingest 子进程。
        try:
            from rag import answer_env_from_case
            env.update(answer_env_from_case(case))
        except Exception:  # noqa: BLE001  取不到就用默认后端，不阻断摄入
            pass
        self._spawn_and_stream(argv, env, "done")

    def _pump(self) -> None:
        try:
            while True:
                kind, payload = self._q.get_nowait()
                if kind == "line":
                    self._logbox.insert("end", payload + "\n")
                    self._logbox.see("end")
                    prog = parse_progress_line(payload)
                    if prog and self._bar is not None:  # install 流没有进度条
                        self._bar.config(maximum=prog["total"], value=prog["done"])
                        self._stat.config(text=f"{prog['done']}/{prog['total']} · {prog['path']}")
                elif kind == "error":
                    messagebox.showerror("启动失败", payload)
                    self._build_choice_screen(); return
                elif kind == "install_done":
                    if self._marquee is not None:
                        self._marquee.stop()
                    ok = payload == 0
                    (messagebox.showinfo if ok else messagebox.showwarning)(
                        "安装环境", "环境安装/检查完成。" if ok else
                        f"install.py 退出码 {payload}——部分组件可能未装好，详见日志。")
                    # 回选择屏会重跑 probe_environment 刷新横幅，用户立刻看到新状态。
                    self._build_choice_screen(); return
                elif kind == "done":
                    self._build_done_screen(payload); return
        except queue.Empty:
            pass
        # 兜底：worker 线程已经退出，但既没排进消息也没到终态（done/error）——
        # 说明线程内部异常静默退出（例如读 proc.stdout 时抛错）。不能继续无限
        # 重排 _pump，否则界面永远卡在"启动中…"。
        if self._thread is not None and not self._thread.is_alive() and self._q.empty():
            messagebox.showerror("摄入异常终止", "摄入线程意外退出，未产生结果，请重试。")
            self._build_choice_screen(); return
        self.after(150, self._pump)

    def _build_done_screen(self, exit_code: int) -> None:
        self._clear()
        report_path = (self.case_dir / "ingest-report.json") if self.case_dir else None
        # 只读+解析报告一次，摘要与"未处置裁决"共用；解析失败或缺文件 → report=None。
        report: dict | None = None
        if report_path and report_path.is_file():
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                report = None
        if report is not None:
            summary = summarize_report(report)
        elif report_path and report_path.is_file():
            summary = f"找到 ingest-report.json 但无法解析（可能写入中断）。引擎退出码 {exit_code}。"
        else:
            summary = f"引擎退出码 {exit_code}，但未找到 ingest-report.json（可能前置失败）。"
        tk.Label(self._container, text="摄入结束", font=("", 13, "bold")).pack(anchor="w")
        summary = summary + "\n\n" + exit_advice(exit_code)  # 裸退出码翻成人话建议
        box = tk.Text(self._container, height=10, wrap="word")
        box.insert("1.0", summary); box.config(state="disabled")
        box.pack(fill="both", expand=True, pady=8)

        self._build_disposition_box(unresolved_source_files(report or {}))

        row = tk.Frame(self._container); row.pack(fill="x")
        if report_path and report_path.is_file():
            tk.Button(row, text="打开报告位置",
                      command=lambda: webbrowser.open(report_path.parent.as_uri())).pack(side="left")
        tk.Button(row, text="重新摄入", command=self._build_choice_screen).pack(side="left", padx=6)
        if exit_code == 0:
            tk.Button(row, text="拷贝下一步", command=self._copy_handoff).pack(side="left", padx=6)
        tk.Button(row, text="关闭", command=self.destroy).pack(side="right")
        if exit_code != 0:
            messagebox.showwarning("完整性提醒",
                                   "有需要处理的项，详见窗口内摘要（未静默放过）。")
        else:
            messagebox.showinfo("完成", "摄入完成、完整性门通过。可让 agent 继续建 wiki。")

    def _build_disposition_box(self, files: list[str]) -> None:
        """未处置源文件逐个裁决：填原因→登记跳过（写 log.md，reconcile 随即放行），或重新
        摄入（补转）。未处置>0 时完整性门本就非 0——这里把裁决权确定性地交给用户。"""
        if not files:
            return
        tk.Label(self._container, text=f"未处置源文件（{len(files)}）——请逐个裁决：",
                 font=("", 11, "bold"), fg="#a60").pack(anchor="w", pady=(8, 0))
        self._disp_reason: dict[str, tk.StringVar] = {}
        for rel in files:
            r = tk.Frame(self._container); r.pack(fill="x", pady=2)
            tk.Label(r, text=rel, width=34, anchor="w").pack(side="left")
            var = tk.StringVar(value="")
            self._disp_reason[rel] = var
            tk.Entry(r, textvariable=var, width=24).pack(side="left")
            tk.Label(r, text="←填跳过原因", fg="#888").pack(side="left", padx=2)
            tk.Button(r, text="登记跳过",
                      command=lambda x=rel: self._on_register_skip(x)).pack(side="left", padx=4)
        tk.Button(self._container, text="复验完整性",
                  command=self._on_recheck).pack(anchor="w", pady=(4, 0))

    def _on_register_skip(self, rel: str) -> None:
        reason = self._disp_reason[rel].get().strip()
        if not reason:
            messagebox.showwarning("需要原因", "登记跳过必须写非空原因（否则 reconcile 仍视为未处置）。")
            return
        try:
            append_skip_log(self.case_dir, rel, reason)
        except OSError as e:
            messagebox.showerror("写入失败", str(e)); return
        messagebox.showinfo("已登记", f"{rel} 已登记跳过。点「复验完整性」确认门是否放行。")

    def _copy_handoff(self) -> None:
        """把"回对话让 agent 建 wiki + 案件路径"拷进剪贴板，减少用户思考下一步。"""
        text = (f"摄入已完成（{self.case_dir}）。请加载 lawiki，对该案件 _md/ 建 wiki，"
                f"再就本案做交叉验证问答。")
        self.clipboard_clear()
        self.clipboard_append(text)
        messagebox.showinfo("已拷贝", "下一步提示已复制到剪贴板，回到对话粘给 agent 即可。")

    def _on_recheck(self) -> None:
        """原地重跑源级对账（不必整轮重摄），把结果告诉用户。"""
        n = recheck_unresolved(self.case_dir)
        if n < 0:
            messagebox.showwarning("无法复验", "未找到转换报告，请先「重新摄入」。")
        elif n == 0:
            messagebox.showinfo("复验通过", "✅ 已无未处置源文件，完整性门放行。可让 agent 建 wiki。")
        else:
            messagebox.showwarning("仍有未处置", f"还有 {n} 个未处置源文件，请继续处置或补转。")


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    argv = sys.argv[1:] if argv is None else argv
    case_dir = Path(argv[0]).resolve() if argv else None
    try:
        app = IngestApp(case_dir=case_dir)
    except tk.TclError as e:
        print("[lawiki-ingest-gui] 无法打开图形界面：本流程需在有桌面的用户电脑上运行；"
              f"当前环境无图形界面（{e}）。请在用户机器上运行，或改用无头 ingest.py（仅限无桌面/CI）。",
              flush=True)
        return 3
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
