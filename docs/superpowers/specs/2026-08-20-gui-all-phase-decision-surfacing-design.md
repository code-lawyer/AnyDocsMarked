# 三阶段决策全量收进 GUI + 决策账本可证明完备（能力接线契约扩展）设计（2026-08-20）

## 背景

承接 `2026-08-19-capability-engagement-contract-design.md`。第一版把"承诺相关能力 →
sanctioned 路径接线"做成单一契约（`capabilities.json`），把 **ingest 期的 CHOICE** 渲染成
GUI 开关，并加了契约测试盯住已知接线。但它只覆盖**有人记得登记的实例**——是一张会过期的
快照。本项目反复栽的恰恰是"名单之外"那一类。

### 产品原则（用户明确）

**GUI 存在的意义，就是把每一个"不需要 agent 判断、却容易被本地 agent 静默忽略"的可选
决策，逐一摆到用户面前强制确认。** 任何决策若只活在 `install.py` 命令行参数、环境变量、
或 agent 的 prose 里，就是一个可被静默忽略的缺口。而且——**选定之后，必须有一个确定性
（代码/配置）执行点兑现它，绝不委托 agent 的 prose 去"记得执行"。**

### 这个项目的签名式失败：缺失的东西正是没人盯的东西

一次次栽在同一形状上——能力/决策悄悄活在 env/flag/prose 里，没有任何机器化的东西点名它，
于是它沉默、全绿、靠人工审计几年后才翻出来：

- 漏检（无评测集，系统不知自己漏了）；双 OCR 互校（能力在、没测试看它接没接进路径）；
  `ocr_rotate.py`（死代码、单测全绿）；**本轮现挖**：`RAG_EMBED_BACKEND=openai` / 远程
  ollama 的 **embedding 外传门**（代码有硬门却从没在账本/GUI 露面，纯靠 agent 设环境变量）。

### 一次全项目决策点审计（枚举三模块所有 env/flag）后确认的缺口

1. **只摆开关、没摆必需输入**（确认了却执行不了）：双 OCR 互校有勾选框但**无 MinerU
   token / verifier 模式选择**（本地/云端/双云端）；结构重建有开关但**无 LLM 凭证**。
2. **install 期决策完全不在 GUI**：装不装 rag-retriever、embedding 后端、**装不装本地
   OCR 包**（`makeitdown[local]` 重装）、stop_hook 后闸门——全在 `install.py` CLI + env。
3. **answer 期决策没有任何 GUI**：`RAG_RERANK` / `RAG_MIN_SCORE` 只是环境变量。
4. **第二条静默外传路径**（embedding 云上传）——本 spec 选择**删除**而非 GUI 确认（见决策六）。
5. **未处置/跳过的源文件**：由 agent 登记 `log.md`；reconcile 门确定性拦截，但**跳过哪些、
   怎么处置从没摆给用户看**。
6. **新旋钮会再逃出账本**：契约只覆盖已登记项，明天新增一个 env/flag 就是同一个洞复发。

## 目标

把 **install / ingest / answer 三阶段每一个契约声明的可选决策**收进用户必过的 GUI 确认；
**每个决策选定后由一个确定性执行点兑现，不委托 agent**；并把"没有决策点逃出账本 / 逃出
GUI / 委托给 agent"三件事都变成**机器可判的 CI 断言**。仍是一个机制、一份契约。

## 决策一 · 契约 schema 扩展：`phase` 增 install、新增 `inputs` 与 `enforcement`

CHOICE/FLOOR 每条扩展（OUT 只需 `rationale`）：

```jsonc
{
  "id": "ocr_cross_check",
  "phase": "ingest",            // install | ingest | answer（新增 install）
  "tier": "CHOICE",
  "gui_control": "cross_check", // GUI 开关 key
  "tradeoff": "……",            // GUI 展示的权衡文案
  "inputs": [                   // 勾选后 GUI 需收集的用户输入
    { "id": "cross_check_mode", "label": "校验器位置", "kind": "select",
      "options": ["cloud", "local", "auto"], "flag": "--cross-check-mode", "default": "cloud" },
    { "id": "mineru_token", "label": "MinerU 云端 token", "kind": "secret",
      "env": "MINERU_API_TOKEN", "required_when": {"cross_check_mode": ["cloud", "auto"]} }
  ],
  "sanctioned": {"ingest_flag": "--ocr-cross-check", "default_on": false},
  "enforcement": "makeitdown --ocr-cross-check 代码路径（convert_ocr verifier）；token 经子进程 env"
}
```

- `inputs[].kind`：`text` / `secret`（掩码、session-only、**绝不落盘**）/ `select`（配 `options`）。
- `inputs[].env` 或 `.flag` 声明它落到哪；`required_when` 表条件必填。
- **`enforcement`（必填）**：一句话点名兑现该决策的**确定性机制**，见决策二。

契约仍是唯一真值来源：GUI 按 `inputs` 渲染、契约测试按 `inputs` 断言 GUI 有对应字段。

## 决策二 · 每个决策必须有确定性执行点，不得委托 agent

`enforcement` 必须指向**非 agent** 的执行点。契约测试用一份"已知确定性执行形态"白名单校验，
**enforcement 含 "agent" 字样即断言失败**。四种被认可的形态（本项目已有或本 spec 建立）：

1. **代码门**：makeitdown `require_cloud_consent`、rag `get_embedder` 的门、ingest 完整性门/
   退出码、reconcile 门——选择经标志/env 进入，代码强制。
2. **工具读配置注入**（本 spec 关键新增）：answer 期旋钮**不靠 agent export**，而由 lawiki
   `rag.py` 包装器（所有工具消费 RAG 的单一入口）在 `_run_rag` spawn 子进程前，读
   `<case>/.anydocsmarked/case.json` 把 `RAG_RERANK`/`RAG_MIN_SCORE`/`RAG_EMBED_BACKEND` 注入
   **每一个** rag-retriever 子进程 env。任何工具碰 RAG，选择就被代码兑现，**与 agent 无关**。
3. **索引期落盘**：embed 后端/模型建索引时写进 `.rag/`，rag-retriever 查询前比对不匹配即拒
   （已有机制）；parent-context 于索引期生效。
4. **GUI 直接执行**：install 动作由 GUI 自己 subprocess 跑 `install.py`（不叫 agent 跑）；
   stop_hook 由 GUI 写**案件本地** `.claude/settings.json`（不叫用户手配、不碰全局）。

## 决策三 · 首批三阶段能力登记

| id | phase | tier | inputs | enforcement（确定性执行点） |
|---|---|---|---|---|
| `rag_install` | install | CHOICE | `embed_backend`(select **local / ollama**，均本地) + `ollama_url`(text, **仅 loopback**, required_when ollama) | GUI 直跑 `install.py`；默认开/强推，取消弹「无交叉验证、信任降级」警示；后端记入 `.rag/` |
| `ocr_install` | install | CHOICE | `ocr_pkg`(select **local / cloud**) | **新增（补缺口）**：GUI 直跑 `install.py --ocr <local\|cloud>`，决定装不装 `makeitdown[local]` 重包 |
| `answer_gate` | install | FLOOR→GUI 确认 | — | GUI 往案件本地 `.claude/settings.json` 写 Stop hook；harness 每次回复确定性校验锚点 |
| `ocr_cross_check` | ingest | CHOICE | `cross_check_mode`(select 本地/云端/**双云端**) + `mineru_token`(secret, required_when 云端/auto) | makeitdown `--ocr-cross-check` + verifier 代码路径；token 经子进程 env |
| `structure_headings` | ingest | CHOICE | `llm_base_url`(text)+`llm_model`(text)+`llm_api_key`(secret) | makeitdown `--structure-headings` 代码路径；凭证经子进程 env |
| `rag_parent_context` | ingest | CHOICE | — | ingest 建索引期设 `RAG_PARENT_CONTEXT`，rag 子进程消费 |
| `unresolved_disposition` | ingest(收尾) | CHOICE | 每个未处置源文件：`retry` / `accept_skip`+原因(text) | done 屏列出让用户裁决；写 `log.md`；reconcile 门确定性拦截（未处置>0 即非 0） |
| `rag_rerank` | answer | CHOICE | — | `rag.py` 读 `case.json` → 注入 `RAG_RERANK` 到 rag 子进程（不靠 agent） |
| `rag_min_score` | answer | CHOICE | `threshold`(text 0~1) | 同上，注入 `RAG_MIN_SCORE` |

**OUT（不塞 GUI，但仍登记入账本以防逃逸）**：`RAG_CHUNK_TOKENS/OVERLAP`、`RAG_RRF_K`、
`RAG_HYBRID_CANDIDATES`、`RAG_PARENT_TOKENS`、`RAG_EMBED_BATCH_SIZE`、`RAG_DATA_DIR`、
`RAG_METADATA_FIELDS`、makeitdown `--text-threshold`/`--ocr-model`/`--keep-images`/`--workers`/
`--skip-existing`/`--warn-*` 阈值、`LAWIKI_RAG_CMD`、`HF_ENDPOINT`/`ANYDOCS_PYPI_INDEX`/
`UV_TOOL_BIN_DIR`。rationale：调优或基础设施，非"错一位就出事"的信任/隐私决策；暴露反而
诱导放松安全阈值。

## 决策四 · GUI 三阶段呈现（一个窗口，按 phase 驱动分区）

`ingest_gui.py` 在**现有窗口**（已能选文件夹/OCR/token/进度条/完成屏）上扩为按 `phase`
渲染的分区，能力清单一律读契约（零硬编码某能力），字段随 `required_when` 动态显隐：

- **① 环境（install）**：探测环境；缺件/首次呈现 RAG 安装+后端、OCR 安装(local/cloud)、
  stop_hook 启用。勾选触发 GUI 自跑 `install.py`。
- **② 摄入（ingest）**：OCR 卡片 + 「高级」区；每个 CHOICE 开关下**按 `inputs` 渲染字段**
  （cross-check → 模式下拉 + MinerU token；structure → LLM 三项）。
- **③ 问答设置（answer）**：rerank 开关 + min_score 数值；**不立即生效**，持久化进案件配置。

## 决策五 · 持久化与信任边界

- **secret 绝不落盘**：所有 `kind:"secret"` 仅本次运行经子进程 env 注入，不写任何文件。
- **answer 期非密选择持久化 + 工具确定性兑现**：rerank(bool)/min_score(数值)/embed_backend
  写进案件本地 `<case>/.anydocsmarked/case.json`；**兑现方是 `rag.py` 工具不是 agent**（决策二·2）。
- **stop_hook 只写案件本地** `<case>/.claude/settings.json`，仅用户勾选确认后写；绝不碰全局。
- **云端仍走 consent 门**：保留的云端项（云 OCR、云 MinerU、structure 的云 LLM）勾选后仍需
  显式同意；**双云端互校**=主云+校验云、两条都上传，必须 consent。契约不放宽 consent。
- **install 期动作可失败不阻塞**：RAG/OCR 装不上 → 如实告知并降级，与既有哲学一致（只是把
  "要不要装/装哪个"从 agent 决定改成用户 GUI 决定）。

## 决策六 · 删除云端 embedding 后端（只保留本地）

审计确认云端 embedding 的独有价值**只是"免下载/免本地算力"，不是质量**——最强模型 `bge-m3`
本地 ollama 即可离线跑，`local` 后端更内置于 offline 发布包。故**删掉选项**而非加同意（"删掉
选项"是"防 agent 静默误用"的最强形态）。改动限于 rag-retriever 模块内：

- **`embed.py`**：删 `openai` 分支（`OpenAICompatEmbedder`）；`ollama` 改为**只允许 loopback**，
  非 loopback 无条件硬拒（**去掉 consent 逃生口**）。合法后端收敛为 `{local, ollama(loopback)}`。
- **`config.py`**：删 `RAG_EMBED_BACKEND=openai`、`RAG_OPENAI_API_KEY`/`RAG_OPENAI_BASE_URL`/
  `RAG_CLOUD_CONSENT`（embedding 侧已无外传，consent 变死码）。
- **测试/文档/版本**：删相关测试与 setup.md 的 openai 列；README/CHANGELOG 记"移除云端
  embedding，只保留本地(内置)+本地 ollama"；rag 公开 config 破坏性变更，随 bundle tag bump。
- **保留**：`local`（内置 `bge-small-zh`，零下载零外传）+ `ollama`（本地 `bge-m3`，离线最强）。
- **边界**：只删云端 **embedding**；云端 OCR、structure 的云 LLM 不动（各自仍 consent-gated opt-in）。

## 决策七 · 决策账本可证明完备：`--list-knobs` 自报 + 契约聚合核对

**为什么要它**（防复发，见背景）：契约只盯已登记项就是快照，新增一个 env/flag 又会逃出账本，
本项目签名式失败复发。要把"没有决策逃出账本"从人的自觉变成 CI 门。

**为什么不能 grep 别人源码**：契约测试在 lawiki，env/flag 在 makeitdown/rag，三模块互不 import。
从 lawiki grep 别人源码 → 耦合其文件布局、装机布局下拿不到源码、正则抓不全动态读——不干净。

**机制（走既有 CLI+JSON 跨模块契约，干净）**：

- makeitdown / rag-retriever 各新增 `--list-knobs`（输出 JSON：本模块读的所有 env/flag + 简述）。
- **各模块自身测试**断言其 `--list-knobs` 与真实 argparse/config 一致（本地防漂移）。
- 契约测试 subprocess 调两个 `--list-knobs` + 读 lawiki 自身 env/flag，聚合与 `capabilities.json`
  比对：**每个 knob 必须被登记为 FLOOR/CHOICE/OUT 之一；有未登记的即红。** 装机布局下也能跑。

## 非目标

- **不以 CLI / 交互式提问替代 GUI**：CLI 带 flag 是"agent 静默跳过"的现状本身；交互式提问在
  agent 驱动下 stdin 归 agent、同样不安全。GUI 是"人必须确认、agent 不能跳"这条保证的**载体**，
  不是可替换的实现选择（承接 GUI-forced-flow 决策二：无桌面硬报错，绝不静默降级）。
- **不追求"中段"agent 推理步骤的确定化**：建 wiki 的组织、问答中取证/四情形分流/`lint answer`
  本质是模型的活（"锁两端放中段"），GUI 收不进、也不该收。确定性只守两端（取证前门 + 交付后
  闸门 stop_hook）；stop_hook 只查锚点、不强制 agent 真去取证——这是产品架构的固有边界，如实标注。
- 不把 RAG 改成硬性必选（以 GUI 强确认 + 默认开 + 取消警示落地"事实必需"，保留极端降级）。
- 不做通用设置管理器；GUI 只呈现契约声明的决策，不暴露任意 env。不持久化任何 secret。不碰用户
  全局 settings。不新建独立 answer GUI 进程（answer 决策并入同一窗口、持久化后由 `rag.py` 读取）。

## 验收

- schema 扩展（phase=install、inputs/enforcement/required_when）校验通过；`capabilities.json`
  按决策三登记。
- 纯函数单测：`build_gui_fields`/`build_ingest_argv` 各 CHOICE 的开关+inputs 正确产出；secret 不
  进持久化。
- 契约测试（**接线前红、接线后绿**）：每个 CHOICE 的开关+每个 input 在 GUI 可达；三阶段分区都有
  入口；每个 `enforcement` 非空且**非 agent**（含 "agent" 即红）；secret 不落盘；`required_when`
  不悬空；**`--list-knobs` 聚合后三模块每个 env/flag 都在契约登记**（决策七）。
- embedding 无外传（删除后）：`RAG_EMBED_BACKEND=openai` 报未知后端；非 loopback ollama 无条件
  硬拒；`RAG_OPENAI_*`/`RAG_CLOUD_CONSENT`（embedding 侧）已删。
- answer 期硬执行：GUI 设 rerank/min_score → 写 `case.json`；直接调 `rag.py search`（不经 agent
  设任何 env）即命中（mock 子进程捕获 env 含 `RAG_RERANK`/`RAG_MIN_SCORE`）。
- 未处置裁决：done 屏列出未处置文件；accept_skip+原因 → 写 `log.md` 使 reconcile 通过；不处置 →
  门保持非 0。
- 端到端冒烟（有桌面手动）：install 屏取消 RAG 弹警示 / OCR 选 local 触发装重包 / 勾 stop_hook 写
  案件本地 settings；ingest 屏勾双 OCR 出现模式下拉+MinerU token，选双云端+consent → 命令带
  `--ocr-cross-check --cross-check-mode cloud` 且子进程有 `MINERU_API_TOKEN`。
- 三模块全量 + 跨模块验收 + ruff `E9,F` + 版本对齐，Ubuntu+Windows 双矩阵全绿。
- README/setup.md/SKILL 同步：三阶段 GUI 决策图；rag 措辞改"核心问答必需、GUI 确认、仅极端降级
  缺省"；删除云端 embedding 说明。

## 落地顺序（建议分期，降"一次太大"风险）

1. 本 spec 定案（待用户确认）。
2. 写实现 plan（`docs/superpowers/plans/2026-08-20-*`）。建议排序：
   **① 决策六删云端 embedding + ② 决策二·2 的 `rag.py` 读 case.json 注入**（独立、破坏面小、
   高价值，先落地）→ ③ schema 扩展 + 契约登记（含 ocr_install / unresolved_disposition / OUT）→
   ④ 决策七 `--list-knobs` + 完备性断言 → ⑤ `build_gui_fields` 纯函数 → ⑥ GUI 三分区渲染 →
   ⑦ install 期 GUI 直跑 + stop_hook 案件本地写入 + done 屏裁决 → ⑧ 契约测试全量扩展 → ⑨ 文档同步。
3. 按 SDD/TDD 执行 + 双 OS 回归。均待用户批准再动代码。
