# 三阶段决策全量收进 GUI + 决策账本可证明完备 实现 Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development（逐任务）。REQUIRED SUB-SKILL per task: superpowers:test-driven-development。Steps 用 `- [ ]`。

**Goal:** 把 install/ingest/answer 三阶段每个可选决策收进用户必过的 GUI；每个决策由确定性执行点兑现（不委托 agent）；并把"没有决策逃出账本/逃出 GUI/委托 agent"变成 CI 断言。附带删除云端 embedding 后端（关掉第二扇外传门）。

**Architecture:** 承接已提交的能力接线契约（`d16fa79`）。契约数据 `capabilities.json` 扩 `phase(install)`/`inputs`/`enforcement`；GUI 读契约按 phase 渲染三分区；answer 期由 `rag.py`（工具，非 agent）读 `<case>/.anydocsmarked/case.json` 注入 env；完备性靠各模块 `--list-knobs` 自报 + 契约聚合核对。三模块零 import 不变（只共读 JSON / 走 CLI+JSON）。

**Tech Stack:** Python 3.11+ 标准库（json/argparse/subprocess/tkinter/unittest）；makeitdown/rag 侧 uv+pytest。

**Spec:** `docs/superpowers/specs/2026-08-20-gui-all-phase-decision-surfacing-design.md`

## Global Constraints

- **零跨模块 code import**；完备性核对走 `--list-knobs`（CLI+JSON），**不 grep 别人源码**。
- **secret 绝不落盘**：token/key 仅经子进程 env；契约测试断言 secret id 不进任何持久化。
- **enforcement 非 agent**：每个 FLOOR/CHOICE 的 enforcement 指向代码门/工具注入/索引落盘/GUI 直执行；含 "agent" 即测试红。
- **云端仍走 consent 门**（云 OCR / 云 MinerU / 云 LLM 保留 opt-in）；**云端 embedding 删除**、非 loopback ollama 无条件硬拒。
- **stop_hook 只写案件本地** `<case>/.claude/settings.json`；**绝不**碰用户全局。
- **GUI 是载体，不以 CLI/交互式提问替代**；无桌面硬报错（承接 GUI-forced-flow）。
- 不动门禁/退出码/report 契约；不追求中段 agent 推理确定化。
- **Windows 优先**：子进程 utf-8、env 用 `os.environ.copy()`；读 settings/case.json best-effort、失败不致命。

## File Structure

- rag-retriever：`embed.py`/`config.py`（删云端 embedding）、`cli.py`（`--list-knobs`）+ 对应 tests。
- lawiki：`skill/lawiki/tools/rag.py`（读 case.json 注入 env）、`capabilities.json`+`tools/capabilities.py`（schema 扩展）、`ingest_gui.py`（三分区 + inputs）、`install.py`（GUI 复用）、`test_capability_contract.py`（扩展）+ 各 test。
- makeitdown：`cli.py`（`--list-knobs`）+ test。
- 文档：README/setup.md/SKILL/CHANGELOG。

---

## 第一期 · 独立高价值、破坏面小（先落地）

### Task 1: 删除云端 embedding 后端（rag-retriever）

**Files:** Modify `rag-retriever/rag_retriever/embed.py`、`config.py`；Test `tests/test_embed.py`（或对应）
**Steps:**
- [ ] 先改测试（这是删能力，红→绿反向）：断言 `RAG_EMBED_BACKEND=openai` → `ValueError` 未知后端；非 loopback `RAG_OLLAMA_URL` → **无条件**拒（无 consent 逃生口）；`local`/loopback ollama 正常。删除原 openai/consent 用例。
- [ ] `embed.py`：删 `OpenAICompatEmbedder` 分支；`ollama` 分支去掉 `cloud_consent` 逃生口，非 loopback 硬拒。
- [ ] `config.py`：删 openai 合法值、`RAG_OPENAI_API_KEY`/`RAG_OPENAI_BASE_URL`/`RAG_CLOUD_CONSENT`（embedding 侧）。
- [ ] rag 全量 `uv run --group dev pytest tests -q` 绿；CHANGELOG 记破坏性变更（版本 bump 留到发版）。

### Task 2: `rag.py` 读 case.json 注入 env（answer 期硬执行核心）

**Files:** Modify `lawiki/skill/lawiki/tools/rag.py`；Test `lawiki/skill/lawiki/tools/test_rag.py`
**Steps:**
- [ ] 先写测试：给定 `<case>/.anydocsmarked/case.json`（含 `rerank:true`,`min_score:0.3`）→ mock `_run_rag` 的 subprocess，断言子进程 env 含 `RAG_RERANK`/`RAG_MIN_SCORE`；无 case.json → 不注入、不报错。
- [ ] 在 `_run_rag`（或其上游拿到 case 根处）spawn 前读 case.json（best-effort），把非密选择映射成 `RAG_*` 注入子进程 env（`os.environ.copy()`），跑完不污染本进程。
- [ ] 确认 evidence.py / 问答路径经 `rag.py` 均自动生效（同一入口）。

## 第二期 · 契约 schema + 完备性

### Task 3: schema 扩展 + 契约登记

**Files:** Modify `lawiki/skill/lawiki/tools/capabilities.py`、`skill/lawiki/capabilities.json`；Test `tools/test_capabilities.py`
**Steps:**
- [ ] 先写测试：`phase="install"` 合法；CHOICE 的 `inputs` 校验（kind∈{text,secret,select}、select 必有 options、required_when 引用的字段须在同 inputs 内存在）；每个 FLOOR/CHOICE 必有非空 `enforcement`；enforcement 含 "agent" → 拒。
- [ ] `capabilities.py`：加 inputs/enforcement/phase=install 校验 + `build_gui_fields(cap)` 纯函数（产出开关 + 各 input 字段描述）。
- [ ] `capabilities.json`：按 spec 决策三登记（补 `rag_install`/`ocr_install`/`unresolved_disposition`，给 cross_check/structure 补 inputs，全部补 enforcement）。

### Task 4: `--list-knobs` + 完备性断言

**Files:** Modify `makeitdown/src/makeitdown/cli.py`、`rag-retriever/rag_retriever/cli.py`；Test 各自 `test_cli.py` + `lawiki/test_capability_contract.py`
**Steps:**
- [ ] makeitdown：先写测试断言 `--list-knobs` 输出 JSON 覆盖其 argparse 全部 flag + 读的 env；实现之；**加漂移测试**：`--list-knobs` 与真实 parser 一致。
- [ ] rag-retriever：同上（含 config 读的所有 `RAG_*`）。
- [ ] lawiki 契约测试：subprocess 调两个 `--list-knobs` + 枚举 lawiki 自身 env/flag，聚合与 `capabilities.json` 比对——**每个 knob 必登记 FLOOR/CHOICE/OUT，缺一即红**。

## 第三期 · GUI 三分区

### Task 5: `build_gui_fields` 消费 + GUI 三分区渲染

**Files:** Modify `lawiki/ingest_gui.py`；Test `lawiki/test_ingest_gui.py`
**Steps:**
- [ ] 先写纯函数测试：`build_ingest_argv` / answer 期 `build_case_config(options)`（非密→case.json 结构）/ secret 不进 config 的断言。
- [ ] GUI 按 `phase` 渲染三分区（install/ingest/answer），每个 CHOICE 开关下按 `inputs` 渲染字段；`required_when` 动态显隐；沿用主线程快照 + worker 不碰 tk 变量。
- [ ] answer 分区选择 → 写 `<case>/.anydocsmarked/case.json`（非密）；secret 只经 env。

### Task 6: install 期 GUI 直执行 + stop_hook + done 屏裁决

**Files:** Modify `lawiki/ingest_gui.py`、复用 `lawiki/install.py`；Test `lawiki/test_ingest_gui.py`、`test_install.py`
**Steps:**
- [ ] 先写测试：install 分区勾选 → 生成正确的 `install.py --ocr <x>` argv（纯函数）；stop_hook 勾选 → 写出案件本地 `.claude/settings.json` 结构正确（`_check_answer_gate_ready` 随后判就绪）；done 屏未处置项 accept_skip+原因 → 写 `log.md` 使 reconcile 通过。
- [ ] GUI 复用现有 subprocess+进度条跑 `install.py`；stop_hook 写案件本地 settings；done 屏列未处置文件供裁决（retry 重跑 / accept_skip 写 log.md）。

## 第四期 · 契约测试全量 + 文档

### Task 7: 契约测试全量扩展

**Files:** Modify `lawiki/test_capability_contract.py`
**Steps:**
- [ ] 遍历断言：每个 CHOICE 开关 + 每个 input 在 `build_gui_fields` 可达；三阶段分区都有入口；enforcement 非空且非 agent；secret 不落盘（不在 case.json/gui_config 键集合）；required_when 不悬空；完备性（Task 4）通过。
- [ ] 该测试须"接线前红、接线后绿"。

### Task 8: 文档同步

**Files:** `README.md`、`lawiki/skill/lawiki/references/setup.md`、`SKILL.md`、`CHANGELOG.md`
**Steps:**
- [ ] setup.md：删 openai embedding 列；三阶段 GUI 决策图；rag 措辞改"核心问答必需、GUI 确认、仅极端降级缺省"。
- [ ] README：删除云端 embedding 说明；SKILL answer 步注明 rerank/min_score 由 GUI 设、`rag.py` 兑现。
- [ ] CHANGELOG `Unreleased` 记本次三阶段 GUI + 完备性 + 删云端 embedding。

## 非目标 / 验收

见 spec 同名两节（不复述）。核心验收：三模块全量 + 跨模块验收 + ruff `E9,F` + 版本对齐，Ubuntu+Windows 双矩阵全绿；契约测试接线前红、接线后绿；有桌面手动冒烟三分区。

## 落地顺序

1. 本 plan 定案（待用户批准）。
2. 按 Task 1→8 SDD/TDD 执行；每任务红→绿→重构 + 局部回归。第一期(1,2)独立可先并入。
3. 全量双 OS 回归 + 文档同步后并入。均待用户批准再动代码。
