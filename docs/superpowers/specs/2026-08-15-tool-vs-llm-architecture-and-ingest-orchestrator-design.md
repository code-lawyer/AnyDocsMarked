# 工具 / LLM 职责划分架构 + 摄入引擎与双前端（GUI/CLI）设计（2026-08-15）

## 背景

产品要"绝不编造"，就必须回答一个贯穿全局的问题：**哪些操作能做成确定性工具、哪些不可约地依赖 LLM、二者如何衔接。** 此前这条边界散落在各模块与 SKILL prose 里，导致两类"过度依赖 agent"的顾虑反复出现：

- **信任类**：输出对不对会不会取决于 agent 老不老实；
- **活性/编排类**：系统往前走（按序、轮询、等待、重试、把进度/申请链接/完成提示转述给人）会不会取决于 agent 在对的时刻做对的事。

本 spec 把职责划分**升为一条显式的架构准则**（durable 参考），并据此把前半段（markitdown+OCR+RAG）收敛成**一个共用确定性引擎 + 两个薄前端**：CLI 给 agent 无头调用、tkinter GUI 给人直接掌控。GUI 是关键一步——它**给人一条直连通道**（进度条、申请链接、完成提示人直接看见），从构造上绕过"靠 agent 转述"这条无法强制的链路。

血缘背景（据实记录，非本 spec 决策）：lawiki 源自 karpathy 的 llm-wiki 模式，rag-retriever 源自 open-notebook 的检索层但**故意拆掉了 LLM**，makeitdown 是本项目自加的上游摄入层。"拆掉 LLM"正是 rag-retriever 能充当独立校验路径的前提。

## 核心原则（durable）

### 原则一 · 分类看"能否校验"，不看"能否生成"
- 能用规则**生成**正确结果 → **纯工具**（Tier 1）。
- 不能生成，但能用规则**校验** → **LLM 生成 + 工具门禁**（Tier 3）。信任活在这一层。
- 既不能生成也不能规则校验（只能人判） → **纯 LLM 判断**（Tier 2）。必须最小化，且**显式上交人**。

推论——"绝不编造"不是靠让 LLM 别乱写（做不到），而是靠让它写的每条都带机器可校验锚点：编造的过不了门，**生成不可信，但编造可被检测**。

### 原则二 · LLM 提议，工具裁决
每个 LLM 输出，出门前必撞一道确定性门（exit 非零可阻断）。LLM 从不被直接信任。已由既有 lint / 交付门体现（`lawiki/skill/lawiki/lint`、makeitdown `quality.py`、覆盖账本、闭世界）。

### 原则三 · 有确定正确行为的控制流，焊进工具，不交 agent
等待、重试、按序、轮询、超时——都有标准算法、无判断空间，必须埋进工具内部。**人机交互中的"可见性/申请指引/完成提示"同理**：与其赌 agent 转述，不如给人一个直连前端（GUI）。轮询已在 `CloudOCR._poll` 内（agent 不轮询）；进度可见性由 GUI 进度条兜（人直接看），CLI 侧沿用 2026-07-10 定案（逐文件 stderr 行 + 退出码 + report，不做 heartbeat）。

## 操作分类（据现状归位，durable 参考）

| 操作 | 层 | 接口形态 |
|---|---|---|
| 文件路由 / markitdown 转换 / OCR 调用 / 质检 / frontmatter+双SHA256 / 覆盖账本 / 源级对账 / 双OCR比对 / consent 门 | Tier 1 | makeitdown CLI（已有） |
| 分块 / BM25 / 向量嵌入 / RRF 排序 / 返回段落 | Tier 1 | rag-retriever CLI + MCP（已有） |
| 全部 lint / 交付门（锚点存在、死链、日期序、对账、覆盖、闭世界） | Tier 1 | 各模块内 lint（已有） |
| 抽取事实 / EXTRACTED·INFERRED·AMBIGUOUS 分类 / 理解问题 / 综合答案 / 语义"两路是否一致"终判 | Tier 2 | lawiki（唯一 LLM 席位） |
| LLM 写锚点→工具逐字校验；LLM 建索引→工具查死链；LLM 答→交付门校验；蕴含校验（换实例判官） | Tier 3 | lawiki 产出 + Tier 1 门 |

**残余不确定性（诚实边界）**：锚点 lint 只校验"引文逐字存在"，**不校验"引文是否支撑该主张"**。故 LLM 可配真引文却推错、或错标注释类别——这类**过得了门**，属 Tier 2。防御非消灭而是三重：① 最小化 Tier 2；② 双路径（lawiki × rag-retriever）交叉验证；③ 分歧与 INFERRED 一律显式上交人。蕴含校验（换实例判官）部分缓解此残余，但其本身仍是模型判断。

## 决策：摄入引擎 + 双前端（GUI 给人 / CLI 给 agent）

前半段（原始资料 → `_md` → `.rag` → 确定性完整性门）整段有确定正确行为，收敛成**一个共用引擎 + 两个薄前端**。

### 结构

```
                 ┌──────────────── 共用确定性引擎（stdlib，只 shell out）─────────────┐
   人 ─▶ GUI ────┤ preflight · run_convert(makeitdown) · run_index(rag) ·           │──▶ _md/ + .rag/
                 │ reconcile(原始资料↔_md) · gate_and_merge → 退出码 + ingest-report │──▶ ingest-report.json
 agent ─▶ CLI ───┤ 门 / 退出码 / report / consent 全在此，单一来源                    │
                 └──────────────────────────────────────────────────────────────────┘
```

- **CLI 前端 `ingest.py`**：agent 无头调用，一次调用 → 单退出码 + `ingest-report.json`。
- **GUI 前端 `ingest_gui.py`（tkinter）**：人操作，或 agent 启动后交给人。首屏收编 **本地/云端选择（图形化优劣对比）+ token 申请链接与粘贴校验 + 本地模型下载（带进度）**；运行显示 **OCR 进度条 + 逐文件状态**；完成 **弹窗提示**。

### 约束（守住信任，别被 GUI 破坏）

1. **单一来源**：门、退出码、`ingest-report.json`、consent 逻辑**全在引擎**；两前端只做"输入采集 + 结果呈现"，**绝不各写一份门逻辑**。
2. **不 import 三模块内部**：引擎只经 CLI/JSON/退出码 shell out 到 makeitdown、rag-retriever。允许 `from rag import _rag_base`（`skill/lawiki/tools/rag.py`，与 install.py 同款 within-bundle 复用）。
3. **GUI 用 tkinter（stdlib，零新依赖）**：长任务走工作线程、UI 用 `after()` 轮询队列刷进度条；进度事件源自引擎转发的 makeitdown 逐文件 stderr 行。**不引入 Electron/PyQt/web 框架**。
4. **consent 仍由引擎守**：GUI"我同意上传"勾选框 → 翻译成 `--cloud-consent` 标志 → 引擎既有 consent 门仍是真拦截。**隐私门绝不下放到 UI 层**；缺同意 fail-closed、绝不静默上传。
5. **GUI 不设绕门口子**：无"强制完成"按钮。可让用户选"带登记跳过继续"（合法的覆盖账本"已登记跳过"），但**必记入报告，绝不伪装转全**。
6. **默认引擎 `auto`**：有本地用本地、仅在已配 token+consent 时用云、否则**绝不静默上传**（非 `cloud` 默认，以免强迫零判断路径去弄 token）。GUI 首屏选择持久化为 case 级配置，CLI 读之或用显式 `--ocr-engine`。
7. **完整性单一裁决**：`reconcile`（原始资料↔`_md`，逼出没装 LibreOffice 的 `.doc` 这类盲点）并入引擎完整性门，与 `_md`↔`.rag` 一起给**一个** gate 结论，不再劈成两处脚本。
8. **交接靠终态产物**：GUI/CLI 完成都产出 `<case>/ingest-report.json` + 单退出码；agent 开完 GUI 不干等窗口，以**"report 出现 + 用户『好了』"** 为恢复信号（沿用确定性完成契约）。

### 退出码语义（引擎自有表）

`0` 全通过；`1` 转换有硬失败（makeitdown report `failed>0`）；`2` 前置/环境缺失（无原始资料 / makeitdown 不可用 / 转换未产出 report，通常云端未同意或输入非法）；`3` 完整性门未过（源级对账未处置>0，或有 rag 却索引不全）。转换失败优先于完整性。

### GUI 收编"两版下载"

首次运行时由 GUI 图形化呈现本地/云端权衡并让人选，选本地即下模型（带进度条）、选云端即贴 token——**把选择放到信息最全、教学最充分的时刻**，优于静态下载页或运行中打断。纯离线/涉密用户仍可用 `-offline` 预打包，但多数人一个包 + GUI 首屏即可，无需强制维护两个 release 制品。

## 部署形态（收束全部讨论）

用户面对**两种入口皆可**：① 直接开 GUI 自助跑完前半段；② 面对 agent，agent 开 GUI 后交人操作、完成再回到 agent。无论哪种，**LLM 只坐 lawiki 一个席位**（建 wiki + 双路径问答），前半段的人机交互与门禁全在 GUI/引擎侧，agent 地盘缩到不可约的 Tier 2，且其输出被门卡、被双路径验、有疑上交人。

## 非目标（本次不做）

- 不合并模块 / 不建共享 gates 包；门禁保持各模块内。
- 不动 rag-retriever、lawiki、makeitdown 本体行为；引擎只 shell out。
- **GUI 不含 LLM、不建 wiki、不做问答**；不把门/consent 逻辑放前端；无绕门按钮。
- 不用 Electron/PyQt/本地 web 框架（tkinter stdlib 足够）；不追求像素级美观（功能优先，需更美再评估本地 web UI）。
- 不强制拆两个 release（GUI 收编选择，`-offline` 仍为离线/涉密保留）。
- 不做 heartbeat 文件 / report 增量写入（GUI 进度条 + 退出码 + report 已够；CLI 侧沿用 2026-07-10 定案）。
- 引擎/GUI **不跑 `lint check`**（那需 wiki 已存在，属建 wiki 之后的 Tier-3 校验，归 lawiki/agent）。

## 验收

- **引擎纯逻辑单测**（stdlib unittest，镜像 `test_install.py`）：门禁 + 退出码四情形 + 合并 report + reconcile 并入；缺云端同意时阻断且无上传。
- **CLI 前端**：合成目录端到端跑通（含一个坏文件 + 一个需跳过文件），产出合并 `ingest-report.json`，退出码按语义区分。
- **GUI 前端**：逻辑尽量下沉到可测引擎；GUI 本体以最小可运行 + 手工/E2E 验证为准（tkinter 难自动化，参照 install.py 无单测先例）；跨 Windows/macOS 手工冒烟（首屏选择、token 校验、进度条、完成弹窗、消费同一 report）。
- 引擎/GUI/CLI 不 import 任何模块（静态核对）；三模块全量测试与仓库 Ruff `E9,F` 门禁不受影响。

## 落地顺序

1. **引擎**（`ingest.py` 现有 plan 的纯核心 + runners + main，但：默认 `auto`、并入 `reconcile`、门逻辑抽成可被两前端调用的函数）。
2. **CLI 前端**（即 `ingest.py` 的 `main`，agent 无头用）。
3. **GUI 前端**（`ingest_gui.py`，tkinter 薄壳，调引擎函数；首屏选择 + token + 模型下载 + 进度条 + 完成弹窗 + 工作线程）。
4. **SKILL 改写**：build 第二步令 agent「**启动 GUI 交用户操作，或无头跑 CLI**」，并以 `ingest-report.json` 为恢复信号；删去分步 makeitdown/reconcile/rag 的 prose。
5. **文档**：setup.md / README 记两前端用法与边界。

> 实现分两 plan：**Plan A（引擎 + CLI）** 已存在（`docs/superpowers/plans/2026-08-15-ingest-orchestrator-cli.md`，需按本 spec 调默认引擎与并入 reconcile）；**Plan B（GUI + SKILL 改写）** 另写。均待用户批准后再动代码。
