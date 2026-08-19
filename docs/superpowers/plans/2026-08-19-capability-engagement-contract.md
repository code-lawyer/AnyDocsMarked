# 能力接线契约 实现 Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. REQUIRED SUB-SKILL per task: superpowers:test-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 用一份单一契约 + 一条 CI 断言，根治"承诺相关能力活在模块内、却没接进 sanctioned 路径（GUI→`ingest.py`→makeitdown/rag；`install.py`）"这一类缺陷。首批消费者：接通双 OCR 互校、旋转纠偏、structure-headings、rag parent_context（索引期）、答案后闸门就绪告警；把 rerank/min_score 明确归入答案侧 surfaced。

**Architecture:** 新增 `lawiki/skill/lawiki/capabilities.json`（声明数据，非共享代码）为唯一真值来源；`ingest.py`/`ingest_gui.py`/`install.py` 各自**读契约**决定接线；新增契约测试遍历契约断言接线成立。makeitdown 内部把 `ocr_rotate.py` 从死代码接入 OCR 路径。跨模块零 import 不变——各模块只读同一份 JSON。

**Tech Stack:** Python 3.11+ 标准库（json/argparse/subprocess/tkinter/unittest）；makeitdown 侧 uv+pytest；rag 不改本体。

**Spec:** `docs/superpowers/specs/2026-08-19-capability-engagement-contract-design.md`

## Global Constraints

- **零跨模块 code import**：`capabilities.json` 是声明数据；makeitdown/rag 不 import lawiki，lawiki 不 import 它们的 Python（沿用 subprocess/JSON 契约）。
- **隐私铁律不放宽**：cross-check 云端 verifier、structure-headings 的 LLM 继续走既有 `--cloud-consent`/`has_consent` 门；无同意即跳过并记原因，绝不静默外传。契约不新增放宽路径。
- **tier 决定默认**：FLOOR 默认开；CHOICE 默认关但 GUI 可见可选；OUT 必带非空 `rationale`。
- **phase 决定归属**：`phase=ingest` 的能力由 ingest/GUI 拥有；`phase=answer`（rerank/min_score）由答案侧 surfaced，ingest/GUI **不得**假装控制它。
- **单一真值来源**：GUI 与契约测试都经纯函数 `build_convert_argv`/`build_ingest_argv` 生成标志，不各抄一份；能力清单只在 `capabilities.json`。
- **不改门禁/退出码/report/frontmatter 契约**；不动 rag/makeitdown 的对外 JSON 契约（rotation 是 makeitdown 内部集成）。
- **Windows 优先**：子进程 utf-8、env 注入用 `os.environ.copy()`；`install.py` 读 settings.json 用 best-effort、失败不致命。
- **成本约束**：旋转纠偏只对**首轮 OCR 低置信页**做 4 角重探，正常页不重探。

## File Structure

- **New `lawiki/skill/lawiki/capabilities.json`**：契约数据。
- **New `lawiki/skill/lawiki/tools/capabilities.py`**：`load_capabilities()` + schema 校验（纯，stdlib）。
- **New `lawiki/test_capability_contract.py`**：契约遍历断言（本方案核心）。
- **Modify `lawiki/ingest.py`**：新增 CHOICE/FLOOR 对应 CLI 标志；抽 `build_convert_argv`；`_run_index` 前按 phase=ingest 注入 rag 环境变量。
- **Modify `lawiki/test_ingest.py`**：`build_convert_argv` 单测。
- **Modify `lawiki/ingest_gui.py`**：`build_ingest_argv(options)`（读契约）；首屏「高级」折叠区按 CHOICE 渲染开关 + tradeoff。
- **Modify `lawiki/test_ingest_gui.py`**：`build_ingest_argv` 纯函数单测。
- **Modify `lawiki/install.py`**：`_check_answer_gate_ready()` + 汇总/`--check-offline` 接入。
- **Modify `lawiki/test_install.py`**：answer-gate 就绪检测测试。
- **Modify makeitdown**：`convert_ocr.py`/`ocr_local.py` 集成 `ocr_rotate.best_rotation_angle`（低置信触发）。
- **Modify `makeitdown/tests/`**：rotation 集成测试（mock 每角置信度）。
- **Modify `README.md`** + **`lawiki/skill/lawiki/references/setup.md`** + **`SKILL.md`**：cross-check 措辞、rag 答案侧环境变量、stop_hook 就绪指引。

---

### Task 1: 契约数据 + 加载器（纯，含 schema 校验）

**Files:**
- New: `lawiki/skill/lawiki/capabilities.json`（首批登记见 spec 表，含 `id/promise/owner/tier/phase/sanctioned/tradeoff/rationale`）
- New: `lawiki/skill/lawiki/tools/capabilities.py`（`load_capabilities()`：读 JSON、校验必填字段、tier∈{FLOOR,CHOICE,OUT}、OUT 必有 rationale、CHOICE 必有 tradeoff+gui_control、返回 list[dict]）
- Test: 在 `capabilities.py` 旁 `test_capabilities.py`（schema 合法性、非法条目抛错）

**Steps:**
- [ ] 先写测试：合法契约加载成功；缺 rationale 的 OUT / 缺 tradeoff 的 CHOICE / 未知 tier → 抛 `ValueError`。
- [ ] 写 `capabilities.json` 首批 11 条（按 spec 表；rerank/min_score 标 `phase:"answer"`，parent_context 标 `phase:"ingest"`）。
- [ ] 写 `load_capabilities()` 通过测试。

### Task 2: `ingest.py` — CLI 标志 + `build_convert_argv` + rag 索引期 env 注入

**Files:**
- Modify: `lawiki/ingest.py`
- Test: `lawiki/test_ingest.py`

**Steps:**
- [ ] 先写测试：`build_convert_argv({"ocr_engine":"local","cross_check":True,"structure_headings":False,...})` → 含 `--ocr-cross-check`、不含 `--structure-headings`；FLOOR 项（quality-check）**永不**产出 `--no-quality-check`。
- [ ] 抽 `build_convert_argv(options) -> list[str]` 纯函数，`_run_convert` 改为调用它。
- [ ] argparse 新增 `--ocr-cross-check`（+ `--cross-check-mode`）、`--structure-headings`（+ LLM 凭证透传参数），默认关；透传给 makeitdown。
- [ ] `_run_index` 前：若启用 `parent_context`（phase=ingest），`env = os.environ.copy(); env["RAG_PARENT_CONTEXT"]="1"` 并确保 `_index_case` 子进程继承（`rag._run_rag` 用继承的 environ；在调用前设 `os.environ` 或改 `index_case` 支持 env——**优先不改 rag/tools**，在 ingest 进程内设 `os.environ["RAG_PARENT_CONTEXT"]` 后再调，跑完清理）。
- [ ] 测试：启用 parent_context 时 `_run_index` 路径下 `RAG_PARENT_CONTEXT` 生效（mock `_index_case` 捕获 env）。

### Task 3: `ingest_gui.py` — 读契约渲染 CHOICE 开关 + `build_ingest_argv`

**Files:**
- Modify: `lawiki/ingest_gui.py`
- Test: `lawiki/test_ingest_gui.py`

**Steps:**
- [ ] 先写测试：`build_ingest_argv({"engine":"auto","cross_check":True,"cloud_consent":True})` → 含 `--ocr-engine auto`、`--ocr-cross-check`、`--cloud-consent`；未勾选项不出现。
- [ ] `resolve_engine_argv` 泛化为 `build_ingest_argv(options)`：读 `load_capabilities()`，对 `tier∈{FLOOR,CHOICE}` 且 `sanctioned.ingest_flag` 非空且 options 启用者产出标志。纯函数。
- [ ] 首屏 OCR 卡片下加**可折叠「高级」区**：对每个 `phase=ingest` 的 CHOICE 渲染 Checkbutton + `tradeoff` 说明（复用 `_TRADEOFF` 范式），默认收起、默认关；LLM/token 输入隐藏其中。
- [ ] `_worker` 用 `build_ingest_argv` 拼 argv；token 仍仅经子进程 env、不落盘。
- [ ] 冒烟项记入验收（tkinter 手工）。

### Task 4: `install.py` — 答案后闸门就绪检测 + 告警（不侵入用户配置）

**Files:**
- Modify: `lawiki/install.py`
- Test: `lawiki/test_install.py`

**Steps:**
- [ ] 先写测试：给定含/不含 stop_hook 的 settings.json（临时目录 mock），`_check_answer_gate_ready()` 分别返回就绪/未就绪。
- [ ] 实现 `_check_answer_gate_ready()`：best-effort 读 Claude Code settings（用户/项目级），检测 hooks 里是否挂 `stop_hook.py`；读不到/无则返回未就绪，**绝不**自动写入。
- [ ] 安装汇总与 `--check-offline` 输出接入：未就绪打明确告警 + 一行 `setup.md` 修复指引。
- [ ] 退出码语义不变（就绪与否只告警、不改 0/1/2）。

### Task 5: makeitdown — 旋转纠偏从死代码接入 OCR 路径（低置信触发）

**Files:**
- Modify: `makeitdown/src/makeitdown/convert_ocr.py`（或 `ocr_local.py` 的识别入口）
- Test: `makeitdown/tests/test_ocr_rotation_integration.py`

**Steps:**
- [ ] 先写测试：mock 一页首轮 OCR 低置信 → 触发 4 角重探、按 `best_rotation_angle` 选向、以选中角正式识别；首轮高置信页 → 不重探（断言未多调 OCR）。
- [ ] 在 OCR 路径接入 `ocr_rotate.best_rotation_angle`：仅当首轮置信度低于阈值（复用 `QualityThresholds.min_confidence` 口径）才做 4 角快探。
- [ ] 确认 `ocr_rotate.py` 不再是死代码（被 src 真实 import）。
- [ ] makeitdown 全量 `uv run --extra dev pytest tests -q` 通过。

### Task 6: 契约测试（核心 CI 门）

**Files:**
- New: `lawiki/test_capability_contract.py`

**Steps:**
- [ ] 遍历 `load_capabilities()`：
  - FLOOR + `ingest_flag`：`build_convert_argv(默认)` 含该标志；且不产出其反向禁用标志。
  - FLOOR 非 CLI 型：断言保证点存在（`ingest.main` 调 `_run_reconcile`；`Config().hybrid is True`；`install.py` 有 `_check_answer_gate_ready`；frontmatter 写双 SHA——按能力查对应代码符号）。
  - CHOICE + `phase=ingest`：`build_ingest_argv({gui_control:True})` 含标志；`ingest.py` argparse 定义该 CLI 标志（够得着）。
  - CHOICE + `phase=answer`（rerank/min_score）：断言 `setup.md`/`SKILL.md` 文档含对应 `RAG_*` 环境变量名（答案侧 surfaced）。
  - OUT：`rationale` 非空。
  - **完备性**：README 承诺表每条至少被一条能力 `promise` 覆盖。
- [ ] 该测试须在**接线未做时红、做完后绿**——它就是把接缝变 CI 可见的那道门。

### Task 7: 文档同步（README / setup.md / SKILL）

**Files:** `README.md`、`lawiki/skill/lawiki/references/setup.md`、`lawiki/skill/lawiki/SKILL.md`

**Steps:**
- [ ] README「它解决的真问题」：双 OCR 互校措辞从"常开保护"→"可选加强，GUI 一键开"（与 CHOICE 一致，不再暗示默认常开）。
- [ ] setup.md：新增答案侧 `RAG_RERANK`/`RAG_MIN_SCORE` 环境变量说明；stop_hook 就绪指引（承接 install 告警）。
- [ ] SKILL.md answer 步骤：注明可经 `RAG_RERANK`/`RAG_MIN_SCORE` 增强查询（phase=answer）。
- [ ] CHANGELOG `Unreleased` 记本次能力接线契约。

## 非目标

- 不建"漏检"召回评测集（另议）。
- 不把 rag CHOICE 做成 GUI 数值精调——只"开/关（用推荐值）"，数值留环境变量。
- 不自动改用户全局 settings.json（answer_gate 只检测+告警）。
- 不改 makeitdown/rag 对外 JSON/退出码契约；rotation 是 makeitdown 内部集成。
- 不追求 GUI 强制硬保证（prose 边界）。

## 验收

- 三模块全量 + 跨模块产品验收 + ruff `E9,F` + `git diff --check` + 版本对齐，Ubuntu+Windows 双矩阵全绿。
- 契约测试：接线前红、接线后绿；FLOOR 接线成立、CHOICE 够得着、OUT 带 rationale、承诺完备。
- 实例①冒烟：GUI 勾 cross-check → 命令含 `--ocr-cross-check`；无 verifier/consent 记跳过不报错。
- 实例②：低置信页触发旋转、高置信页不重探；`ocr_rotate` 被真实调用。
- 实例③：无 stop_hook 时 `install.py` 明确告警、有则报就绪。
- 实例④：GUI 高级区开 structure-headings → ingest 透传（无 LLM 凭证时按既有语义报错/跳过）。
- 实例⑤：GUI 开 parent_context → 索引期 `RAG_PARENT_CONTEXT` 生效；rerank/min_score 在 setup.md/SKILL 文档可见（答案侧）。

## 落地顺序

1. 本 plan 定案（待用户批准）。
2. 按 Task 1→7 顺序 SDD/TDD 执行；每任务红→绿→重构 + 局部回归。
3. 全量双 OS 回归 + 文档同步后再考虑并入。均待用户批准再动代码。
