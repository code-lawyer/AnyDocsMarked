# 摄入 GUI 操作可读性整改设计（2026-08-21）

## 背景

前序几版把摄入 GUI 做成了"能力接线契约的渲染器 + 确定性执行器"——三阶段每个决策都
surface、选了都硬执行、secret 不落盘。**决策完整性已达标。** 但一次以真实用户（律师）
视角的走查暴露出另一整维度的系统性缺失：**操作可读性（operational legibility）**——
用户坐在窗口前，能否知道「现在缺什么、正在干什么、卡住没、出错怎么办」。

根因诚实记录：过去把 GUI 目标定为"渲染契约"，只想"把安装**选项**摆出来"，从没想"先告诉
用户**当前缺什么**"；`install.py` 已有 `_have`/`_verify`/`_check_answer_gate_ready` 检测却
一个都没被 GUI 调用。两轮 `/simplify` 按定义只查代码质量、不猎 UX 缺口，也照不到。

## 目标

把 GUI 从"决策完整、操作不可读"抬到"操作对非技术用户可读"：开窗即体检、缺件即提示且
拦住、长任务有忙碌指示、错误给人话建议、裁决后可原地复验、可中止、有交接提示。**不改**
既有的契约驱动渲染与确定性执行，只补操作反馈闭环。

## 用户旅程与责任（对照现状）

| 阶段 | 应尽责任 | 现状 | 本 spec |
|---|---|---|---|
| 0 打开窗口 | 环境体检 + 缺件提示 + 缺关键件禁用「开始」 | ❌ 无 | 批1 |
| 1 选案件+归集 | 预览+移动确认 | ✅ | 不动 |
| 2 配置决策 | 三阶段契约渲染 + 字段级校验 | ✅ 渲染；❌ 即时校验 | 批2 |
| 3 安装环境 | 忙碌指示 + 装完复验刷新 | ⚠️ 静态日志、只弹完成 | 批1 |
| 4 摄入执行 | 进度条 + 可中止 + 退出码人话 | ✅ 进度条；❌ 中止/人话 | 批2/批3 |
| 5 结果处置 | 逐文件裁决 + 原地复验 | ✅ 裁决；❌ 复验 | 批2 |
| 6 交接 | 明确下一步 + 一键拷贝 | ⚠️ 一句话 | 批3 |

## 决策一 · 环境体检（批1，最高性价比）

新增纯函数 `probe_environment() -> dict`（GUI 侧，复用 `install.py` 的 `_have`/`_verify`/
`_check_answer_gate_ready`，不跨模块 import 引擎 Python）：

```python
{
  "python": {"ok": True, "detail": "3.12"},
  "makeitdown": {"ok": bool, "critical": True},     # 缺→无法转换
  "rag": {"ok": bool, "critical": False},            # 缺→问答仅 wiki（可选降级）
  "ocr_local": {"ok": bool, ...},                    # 供"选了 local 却没装"提示
  "stop_hook": {"ok": bool, ...},
}
```

- 纯逻辑可单测（monkeypatch `_have`/`_verify`）。
- 选择屏顶部渲染**体检横幅**：全就绪=绿；缺可选=黄（如"未装 RAG，问答将仅 wiki"）；
  缺 `critical`=红。
- **红（makeitdown 缺）时禁用「开始摄入」按钮**，文案指向「安装/检查环境」——把"跑一半
  才失败"提前成"进门即告知"。
- 检测是 best-effort、快速（`--help` 探活）；失败按"未就绪"，绝不抛异常阻塞开窗。

## 决策二 · 安装屏忙碌指示 + 装完复验（批1）

- `_stream_screen` 增加 `busy: bool`：安装流用 `ttk.Progressbar(mode="indeterminate")`
  `.start()`（marquee），子进程无结构化进度时也不显死机；`_pump` 收 `install_done` 时
  `.stop()`。摄入流仍用确定性进度条（`[N/M]`）。
- `install_done` 分支：完成后**自动重跑 `probe_environment()` 并刷新体检横幅**，而不是
  只弹一句"完成"——用户立刻看到"现在 makeitdown ✓ 了"。

## 决策三 · 字段级即时校验（批2）

选择屏对"选了却缺必需输入"的组合做**即时红字**（不等点「开始」再弹窗）：
- OCR=cloud 但 token 空 / 未勾同意；
- 任一 CHOICE 勾了但其 `required_when` 必填输入空（契约已有 `required_when`，此处消费它）。

抽纯函数 `validate_options(options) -> list[str]`（读契约的 `required_when`），GUI 在变量
变化时调用刷新提示；`_on_start` 也用它做最终拦截（替代当前零散的 if 判断）。**可单测。**

## 决策四 · 退出码→人话建议（批2）

`ingest.py` 的退出码语义（0/1/2/3）已固定。GUI 加一张映射，在完成屏把裸退出码翻成
"发生了什么 + 建议怎么办"（如 `2 → 未装 makeitdown 或云端未加同意；请先『安装/检查
环境』`）。纯数据，可单测。

## 决策五 · 裁决后原地复验（批2）

完成屏登记跳过后，加「复验完整性」按钮：**原地重跑源级对账**（复用 `reconcile.reconcile`
纯函数，经 ingest 已在 sys.path 的 tools），更新门状态与未处置列表，而不必"重新摄入"整轮。
纯函数部分（拿 case → 调 reconcile → 得未处置数）可单测。

## 决策六 · 可中止（批3）

运行屏（摄入 / 安装）加「中止」按钮 → `self._proc.terminate()`；`_pump` 的线程退出兜底
已能收尾回到选择屏。避免用户对着一个跑飞的长任务只能关窗口。

## 决策七 · 交接提示（批3）

完成屏（门通过时）给一键「拷贝下一步」：把"让 agent 加载 lawiki 建 wiki + 案件路径"写入
剪贴板（`self.clipboard_clear/append`），并显著提示回到对话。

## 决策八 · 结构化未处置列表（批3，承接旧 TODO）

让 `reconcile.reconcile` / `ingest.py` 在 `ingest-report.json` 里**结构化**暴露未处置源
文件列表（`stages.source_reconcile.unresolved_files: [路径…]`，与现有人读字符串并存）。
GUI 的 `unresolved_source_files` 改读结构字段，删除正则解析散文的脆耦合（上轮已在代码
注释标记为理想后续）。这动 ingest/reconcile，属跨阶段小改，随批3。

## 非目标

- 不改契约驱动的三阶段渲染、不改确定性执行/consent/secret-不落盘。
- 不做通用设置管理器、不引入 GUI 框架依赖（仍纯 tkinter 标准库）。
- 不追求像素级美观；只做"可读、不误导、不显死机"。
- 不改 ingest 门/退出码语义（决策四只翻译，不新增码）。
- 不跨模块 import 引擎 Python（体检走 `install.py` 的 `_verify` shell 探活 / 契约 JSON）。

## 验收

- 纯函数单测：`probe_environment`（mock 检测→红/黄/绿分类）、`validate_options`
  （缺 token/同意/required_when→报错项）、退出码→建议映射、复验取未处置数、结构化
  unresolved_files。
- 契约测试与既有 GUI/ingest/rag/makeitdown 全量 + 跨模块验收 + ruff `E9,F` 不回归。
- 有桌面手动冒烟：缺 makeitdown 时横幅红 + 「开始」禁用；装完横幅转绿；安装屏 marquee
  转动；cloud 未填 token 即时红字；完成屏退出码给人话；裁决后复验更新门；中止能停子进程；
  交接一键拷贝。

## 落地顺序

1. 本 spec 定案（用户已批"三批全做"）。
2. plan（`docs/superpowers/plans/2026-08-21-*`）：批1（体检+横幅+禁用+marquee+装后复验）
   → 批2（字段校验+退出码人话+裁决后复验）→ 批3（中止+交接拷贝+结构化 unresolved）。
3. 每批 TDD（纯逻辑）→ 实机冒烟（tkinter）→ 回归 → 提交。
