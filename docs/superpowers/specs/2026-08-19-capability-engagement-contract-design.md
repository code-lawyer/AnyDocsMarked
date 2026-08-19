# 能力接线契约（Capability Engagement Contract）设计（2026-08-19）

## 背景

一次审查暴露出一类**系统性缺陷**：承诺相关的能力活在模块内部，但 sanctioned 路径
（GUI → `ingest.py` → makeitdown/rag；以及 `install.py`）**临时地、逐个地**决定要不要
调用它——没有任何契约规定"每一个承诺相关能力，sanctioned 路径必须**开**（floor）/ 必须
**让人选**（choice）/ **明确排除**（out）"。而每个模块只在自己内部单测该能力，于是这条
"接线缝"对 CI **完全不可见**：模块测试全绿，能力却从未在真实流程里被启用。

### 已确认的实例（证据）

第一档 · 安全下限被静默关掉：

1. **双 OCR 互校**（`--ocr-cross-check`）：makeitdown 默认关；`ingest.py:_run_convert`
   不透传；GUI 无入口。README 把它摆成"金额/日期错一位就是事故"的答案，实际 shipped
   默认是单 OCR。
2. **OCR 旋转纠偏**（`ocr_rotate.py`）：**死代码**，任何 src 代码调用零次（grep 全 src
   仅命中 pycache/egg-info/自身测试）。其 docstring 表明它本是 cross-check 的前置步骤，
   cross-check 未接线 → 它双重死亡。旋转的扫描件无纠偏 → OCR 乱码，且无 cross-check 兜底。
3. **答案后闸门**（`stop_hook.py`）：`install.py` 只装 makeitdown + rag，**从不安装
   stop_hook**；"回答含未锚定事实即拦"的自动强制只靠 `references/setup.md:145` 让用户
   手工粘 hook。产品头号承诺"不编造"在问答时的**自动**强制默认是关的。

第二档 · 增强能力默认休眠且不暴露（off-by-default 本身合理，但同一条缝——够不着、
不作为选择呈现）：

4. **`--structure-headings`**（LLM 标题重建）：off，GUI 无入口。
5. **rag `rerank="none"` / `parent_context=False` / `min_score=0.0`**（`config.py:113,123,127`）：
   休眠；ingest 用默认、MCP 读环境变量，无一层把取舍摆给用户。

对照组（作者在别处做对了，说明缺的只是"统一保证"）：makeitdown 质检 warn 阈值、
frontmatter+双 SHA-256、覆盖账本/源级对账、rag `hybrid=True`/`chunk_strategy="structure"`
——这些下限**默认就是开的**。

### 根因（一句话）

**不存在一份"能力 → 接线要求"的单一契约，也不存在一条断言"sanctioned 路径确实按契约
接线"的 CI 测试。** 能力逐个掉进编排器/GUI/安装器的缝里，而 CI 看不见缝。

## 目标

用**一个机制**（而非逐个打补丁）根治这一类：让"哪些能力必须开 / 必须让人选 / 明确排除"
成为**单一可读契约**，让 sanctioned 路径**接到该契约**，并用**一条契约测试**把接缝**变成
CI 看得见的门**。修复上面 5 个实例只是这个机制的首批消费者。

## 决策一 · 单一登记表（machine-readable manifest）

新增 `lawiki/skill/lawiki/capabilities.json`（仅 lawiki 侧，不新增跨模块 import——它是
**声明数据**，各模块通过它对齐，不通过它耦合代码）。每条能力一个对象：

```jsonc
{
  "id": "ocr_cross_check",
  "promise": "金额/日期错一位就是事故 —— 双 OCR 互校 + 数字位专项比对",  // 指向 README 承诺
  "owner": "makeitdown",              // 能力实现在哪个模块
  "tier": "CHOICE",                   // FLOOR | CHOICE | OUT
  "sanctioned": {                     // FLOOR/CHOICE 必填；断言依据
    "ingest_flag": "--ocr-cross-check",       // ingest.py 必须能透传的标志（null=非 CLI 型）
    "gui_control": "cross_check",             // GUI 必须暴露的控件 key（CHOICE 才有）
    "default_on": false                       // sanctioned 默认是否开
  },
  "tradeoff": "更慢、需第二引擎(MinerU)或云端 token；换来抓出金额/日期识别分歧",  // CHOICE 的 GUI 说明
  "rationale": "……"                  // OUT 必填：为何排除
}
```

契约测试与 GUI/ingest 都从这一份读，`capabilities.json` 是唯一真值来源。

### 首批登记内容

| id | owner | tier | sanctioned 接线 | 说明 |
|---|---|---|---|---|
| `ocr_quality_check` | makeitdown | FLOOR | ingest 不得传 `--no-quality-check`；makeitdown 默认 `quality_check=True` | 已满足，登记以防回归 |
| `source_reconcile` | lawiki | FLOOR | `ingest.main` 必调 `_run_reconcile` | 已满足 |
| `rag_hybrid` | rag | FLOOR | `Config.hybrid` 默认 True | 已满足 |
| `provenance_sha` | makeitdown | FLOOR | frontmatter 双 SHA-256 默认写 | 已满足 |
| `ocr_rotation` | makeitdown | FLOOR（低置信触发） | 接入 OCR 路径，默认开；仅对首轮低置信页做 4 角重探（限成本） | **修复实例②** |
| `answer_gate` | lawiki | FLOOR-advisory | `install.py` 装后检测 stop_hook 是否就绪，未就绪则明确告警 | **修复实例③** |
| `ocr_cross_check` | makeitdown | CHOICE(phase=ingest) | ingest `--ocr-cross-check` 透传；GUI 控件 `cross_check` + verifier token | **修复实例①** |
| `structure_headings` | makeitdown | CHOICE(phase=ingest) | ingest `--structure-headings`（+ LLM 凭证透传）；GUI「高级」区 | **修复实例④** |
| `rag_parent_context` | rag | CHOICE(phase=ingest) | **索引期**能力：GUI「高级」区 → ingest 在 `_run_index` 前设 `RAG_PARENT_CONTEXT`，子进程继承 | 实例⑤ |
| `rag_rerank` | rag | CHOICE(phase=answer) | **查询期**能力：ingest 无法控制；surfaced 在答案侧（`RAG_RERANK` 记入 setup.md + SKILL answer 步骤读取） | 实例⑤ |
| `rag_min_score` | rag | CHOICE(phase=answer) | 同上 `RAG_MIN_SCORE`（查询期，答案侧 surfaced） | 实例⑤ |
| `keep_images` | makeitdown | OUT | rationale：法律文本以文字为准，图片留存是排版偏好非承诺 | 示范 OUT 分类 |

## 决策二 · 把 sanctioned 路径接到契约

### 引擎 `ingest.py`

- 为每个 `owner=="makeitdown"` 且有 `ingest_flag` 的 FLOOR/CHOICE 能力**新增对应 CLI 标志**，
  在 `_run_convert` 里按启用与否透传给 makeitdown（cross-check 一并透传 verifier mode）。
- 抽出**纯函数** `build_convert_argv(options) -> list[str]`（单一来源，供 GUI 与契约测试复用），
  取代当前 `_run_convert` 内联拼命令。
- rag 型 CHOICE 按 **phase** 区分（`rag.index_case` 经 `subprocess.run` 无显式 `env`、
  子进程**继承 `os.environ`**，故 ingest 侧设环境变量可达 rag 子进程）：
  - `phase=ingest`（`parent_context` 影响**建索引**）：ingest 在 `_run_index` 前设
    `RAG_PARENT_CONTEXT`，随子进程生效。
  - `phase=answer`（`rerank`/`min_score` 是**查询期**旋钮，ingest 建索引阶段设了也不影响
    答案）：**不由 ingest/GUI 拥有**；surfaced 在答案侧——写入 `setup.md` 的环境变量清单
    与 SKILL 的 answer 步骤，让答案阶段的 rag 进程读取。契约据 `phase` 分别断言两条路径。

### GUI `ingest_gui.py`

- `resolve_engine_argv` 泛化为 `build_ingest_argv(options: dict) -> list[str]`：从契约里
  `tier in {FLOOR,CHOICE}` 且 `gui_control` 非空的项生成标志。**纯函数，可单测**。
- 首屏在现有 OCR 卡片下，为每个 CHOICE 能力渲染一个开关（复用 `_TRADEOFF` 范式展示
  `tradeoff` 文案）；LLM/token 类隐藏在可折叠的「高级」区，默认收起、默认关。
- 隐私铁律不变：cross-check 的云端 verifier、structure-headings 的 LLM 均**继续走既有
  consent 门**（无 consent 不上传/不启用），契约不放宽 consent。

### 安装器 `install.py`

- 新增 `_check_answer_gate_ready()`：探测当前 Claude Code settings 是否已挂 `stop_hook`
  （读 settings.json 的 hooks，best-effort），未就绪则在安装汇总里打**明确告警**并给出
  `setup.md` 的一行修复指引。**不自动改用户全局 settings.json**（尊重"不侵入用户配置"）。
- `--check-offline` 的 stop_hook 就绪同样纳入自检输出。

### makeitdown 本体（实例②的真实集成）

- `ocr_rotation` 从死代码接入 `convert_ocr` / OCR 路径：对**首轮 OCR 置信度低**的页面，
  做 4 角快速重探、按 `best_rotation_angle` 选正向再正式识别；置信度正常的页**不重探**
  （限住 4× 成本）。默认开（FLOOR），可用既有质检口径观测。

## 决策三 · 契约测试（本方案的核心、专治复发）

新增 `lawiki/test_capability_contract.py`，遍历 `capabilities.json` 断言：

- **FLOOR**：其 `sanctioned` 接线在真实代码里成立——
  - `ingest_flag` 型：`build_convert_argv({...启用...})` 的输出**含**该标志；且**默认**
    构造（无覆盖）对 FLOOR 项也含（对 `default_on:false` 的 CHOICE 不含）。
  - 非 CLI 型（reconcile/hybrid/sha/answer_gate）：断言对应保证点存在（如 `ingest.main`
    调 `_run_reconcile`、`Config().hybrid is True`、`install.py` 有 `_check_answer_gate_ready`）。
- **CHOICE**：`build_ingest_argv({gui_control: True})` **含**其标志；`ingest.py` argparse
  **定义**了对应 CLI 标志（够得着）；rag 型断言启用时对应环境变量被设置。
- **OUT**：必须带非空 `rationale`（防止"随手标 OUT"绕过）。
- **完备性**：README「它解决的真问题」表里每条承诺，在契约里至少有一条能力覆盖
  （防止新增承诺却漏登记）。

这条测试把"能力有没有被真正接进 sanctioned 路径"**变成机器可判**——正是产出上述所有
实例的那条不可见缝，从此对 CI 可见。

## 信任边界与隐私

- 契约**不放宽任何 consent**：cross-check 云端 verifier、structure-headings LLM 仍受
  `--cloud-consent` / `has_consent` 约束；无同意即跳过并记原因，绝不静默外传。
- `answer_gate` 采用"检测+告警"而非自动改配置，避免安装器越权写用户全局 settings。
- 新增能力**默认开与否**严格按 tier：FLOOR 默认开、CHOICE 默认关（可见可选）。GUI 让
  用户对 CHOICE **有意识地拍板**，而不是替他默认开高成本/外传项。

## 非目标

- 不引入跨模块 code import；`capabilities.json` 是声明数据，不是共享包。
- 不改 makeitdown/rag 的门禁语义、退出码、report/frontmatter 契约（只接线 + rotation 集成）。
- 不把 rag 的 CHOICE 旋钮做成 GUI 精细数值调参——只给"开/关（用推荐值）"，数值仍留环境变量。
- 不追求 GUI 强制的硬保证（prose 边界，承接 GUI-forced-flow spec）。
- 不在本 spec 内建"漏检"召回评测集（另议）。

## 验收

- `capabilities.json` schema 合法；契约测试遍历通过（FLOOR 接线成立、CHOICE 够得着、
  OUT 带 rationale、承诺完备性）。
- `build_convert_argv` / `build_ingest_argv` 纯函数单测：各能力启用/关闭 → 标志正确增删。
- 实例①：GUI 勾选 cross-check → sanctioned 命令含 `--ocr-cross-check`；无 verifier/无
  consent 时记跳过原因不报错。
- 实例②：低置信页触发旋转纠偏、正常页不重探（mock 每角置信度可测）；死代码被真实调用。
- 实例③：`install.py` 无 stop_hook 时输出明确告警；有则报就绪。
- 实例④⑤：GUI 高级区开关 → ingest 透传标志 / 设置 rag 环境变量。
- 三模块全量 + 跨模块验收 + ruff `E9,F` + 版本对齐不回归；Ubuntu+Windows 双矩阵。
- README 承诺表与契约同步（cross-check 措辞从"常开保护"改为"可选加强，GUI 一键开"）。

## 落地顺序

1. 本 spec 定案（**本轮已获用户原则同意**：第二档纳入作 CHOICE；stop_hook 走检测+告警）。
2. 写实现 plan（`docs/superpowers/plans/2026-08-19-*`）：契约 schema → 纯函数抽取 →
   ingest/GUI/install 接线 → rotation 集成 → 契约测试 → README 同步。
3. 按 SDD/TDD 执行 + 双 OS 回归。均待用户批准再动代码。
