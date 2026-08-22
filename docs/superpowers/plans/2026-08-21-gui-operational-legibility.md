# 摄入 GUI 操作可读性整改 实现 Plan

> REQUIRED SUB-SKILL per task: superpowers:test-driven-development（纯逻辑先写测试→红→绿）。tkinter 渲染层实机冒烟。Steps 用 `- [ ]`。

**Goal:** 把摄入 GUI 从"决策完整、操作不可读"抬到"对非技术用户可读"：开窗体检、缺件拦住、长任务有忙碌指示、错误给人话、裁决后可复验、可中止、有交接提示。不改契约渲染与确定性执行。

**Spec:** `docs/superpowers/specs/2026-08-21-gui-operational-legibility-design.md`

**Architecture:** 纯逻辑（probe_environment / validate_options / 退出码映射 / 复验取数 / 结构化 unresolved）单测；tkinter 装配薄层实机冒烟。复用 install.py 的 `_have`/`_verify`/`_check_answer_gate_ready`（同 lawiki 模块内，允许）。

## Global Constraints

- 纯 tkinter 标准库，无新依赖。
- 不跨模块 import 引擎 Python；体检走 install.py 的 shell 探活 / 契约 JSON。
- 不改契约渲染、确定性执行、consent、secret 不落盘、ingest 门/退出码语义。
- 体检/校验 best-effort，绝不抛异常阻塞开窗。
- Windows 优先：子进程 utf-8、terminate 跨平台。

## 批 1 · 及格线

### Task 1: `probe_environment` + 体检横幅 + 缺关键件禁用「开始」
- Files: `lawiki/ingest_gui.py`（import install 的 `_have`/`_verify`/`_check_answer_gate_ready`；新增 `probe_environment`）；Test: `lawiki/test_ingest_gui.py`
- [ ] 先写测试：mock `_verify` 使 makeitdown 缺 → probe 报 makeitdown.ok False & critical True；rag 缺 → ok False & critical False；全在 → 全绿。
- [ ] 实现 `probe_environment()` 纯函数。
- [ ] 选择屏顶部渲染横幅（绿/黄/红）；红（critical 缺）时 `开始摄入` 按钮 `state=disabled` 并提示点「安装/检查环境」。
- [ ] 实机冒烟：缺 makeitdown 时红+禁用。

### Task 2: 安装屏 marquee + 装完复验刷新
- Files: `lawiki/ingest_gui.py`（`_stream_screen` 加 `busy`；`_pump` 的 install_done 分支）
- [ ] `_stream_screen(title, with_progress, busy=False)`：busy 时用 `ttk.Progressbar(mode="indeterminate").start()`；安装流传 busy=True。
- [ ] `install_done`：`.stop()` marquee → 重跑 `probe_environment` → 回选择屏（横幅已刷新）。
- [ ] 实机冒烟：安装屏 marquee 转动；装完横幅态更新。

## 批 2 · 顺滑度

### Task 3: `validate_options` 字段级即时校验
- Files: `lawiki/ingest_gui.py`；Test: `test_ingest_gui.py`
- [ ] 先写测试：cloud+无 token → 报错项含 token；cloud+未同意 → 含 consent；某 CHOICE 勾了但 required_when 输入空 → 含该输入；全填好 → []。
- [ ] 实现 `validate_options(options)->list[str]`（读契约 required_when + OCR consent 规则）。
- [ ] 选择屏变量变化时刷新红字提示；`_on_start` 用它做最终拦截（替换零散 if）。

### Task 4: 退出码→人话建议
- Files: `lawiki/ingest_gui.py`；Test: `test_ingest_gui.py`
- [ ] 先写测试：`exit_advice(2)` 含"未装/同意"；`exit_advice(3)` 含"完整性/处置"；0 → 通过语。
- [ ] 完成屏摘要用 `exit_advice(exit_code)` 补一句建议。

### Task 5: 裁决后原地复验
- Files: `lawiki/ingest_gui.py`；Test: `test_ingest_gui.py`
- [ ] 先写测试：给定 case（含 _md/report + log.md skip），`recheck_unresolved(case)` 返回未处置数（复用 reconcile.reconcile）。
- [ ] 完成屏加「复验完整性」按钮 → 调 recheck → 更新门状态/未处置列表/横幅。

## 批 3 · 完备性

### Task 6: 运行屏可中止
- [ ] 运行/安装屏加「中止」→ `self._proc.terminate()`；线程退出兜底收尾回选择屏。实机冒烟。

### Task 7: 完成屏交接一键拷贝
- [ ] 门通过时加「拷贝下一步」→ clipboard 写入建 wiki 提示 + 案件路径。实机冒烟。

### Task 8: 结构化 unresolved_files（承接旧 TODO）
- Files: `lawiki/skill/lawiki/tools/reconcile.py`、`lawiki/ingest.py`、`lawiki/ingest_gui.py`；Tests 各自
- [ ] 先写测试：reconcile 暴露结构化未处置文件列表；ingest 报告写 `stages.source_reconcile.unresolved_files`；GUI `unresolved_source_files` 优先读结构字段（回退旧正则以兼容旧报告）。
- [ ] 实现；删/降级正则耦合。

## 验收

- 全部纯函数单测绿；lawiki + makeitdown + rag 全量 + 跨模块验收 + ruff `E9,F` 不回归；双 OS。
- 有桌面手动冒烟：横幅红/禁用、装完转绿、marquee、即时红字、退出码人话、裁决后复验、中止、交接拷贝。

## 落地顺序

按批 1→2→3、Task 顺序 TDD；每批做完回归 + 提交。均已获用户批准（"三批全做"）。
