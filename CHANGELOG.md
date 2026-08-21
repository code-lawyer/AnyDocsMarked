# Changelog

本项目遵循语义化版本。尚未发布的变化记录在 `Unreleased`。

## Unreleased

### Changed / Removed

- **移除云端 embedding 后端(隐私收束,破坏性变更)**:rag-retriever 删除 `openai` 后端与远程 ollama 支持——embedding 只可能在本机算,案件正文永不因建索引而外传。合法 `RAG_EMBED_BACKEND` 收敛为 `local`(内置 `bge-small-zh`,离线) / `ollama`(本地服务 `bge-m3`);非 loopback `RAG_OLLAMA_URL` 无条件硬拒(去掉 `RAG_CLOUD_CONSENT` 逃生口)。删除 `RAG_OPENAI_API_KEY`/`RAG_OPENAI_BASE_URL`/`RAG_CLOUD_CONSENT`。想要 bge-m3 质量走本地 ollama,无需上云。
- **answer 期检索旋钮由工具确定性兑现,不委托 agent**:lawiki `rag.py` 包装器(消费 RAG 的单一入口)在 spawn rag-retriever 子进程前,读 `<case>/.anydocsmarked/case.json` 把用户选的 `rerank`/`min_score`/`embed_backend` 注入子进程环境变量。任何工具碰 RAG 都自动带上用户选择,与 agent 记不记得无关(为三阶段 GUI 决策落地做准备)。

### Added

- **能力接线契约**（`lawiki/skill/lawiki/capabilities.json` + `tools/capabilities.py`):把"承诺相关能力 → sanctioned 路径接线要求"做成单一真值来源(FLOOR 必开 / CHOICE 让人选 / OUT 明确排除),ingest/GUI/install 各自读契约接线,并新增契约测试 `test_capability_contract.py` 断言接线成立——把过去不可见的编排/GUI/安装接缝变成 CI 看得见的门。首批修复:① 双 OCR 互校(`--ocr-cross-check`)接入 ingest 透传 + GUI「高级」开关;② `ocr_rotate.py` 旋转纠偏从死代码接入 OCR 路径(仅对低置信扫描件重探,限成本);③ `install.py` 新增问答后闸门(stop_hook)就绪检测与告警(不侵入用户 settings);④ `--structure-headings` 与 rag `parent_context`(索引期)经 GUI 高级区可达;⑤ rag `rerank`/`min_score`(查询期)在 setup.md/SKILL 文档化为答案侧可选加强。
- 摄入引擎 `lawiki/ingest.py`（仅标准库,跨平台):单入口编排 `_setup_case → init_case → makeitdown 转换 → rag 建索引 → 源级对账 → 确定性完整性门`,产出合并 `ingest-report.json` + 单退出码(0 全通过 / 1 转换硬失败 / 2 前置或环境缺失 / 3 完整性门未过)。门/退出码/consent 逻辑为单一来源,GUI 直接复用本模块函数,不另写门逻辑。
- 图形摄入前端 `lawiki/ingest_gui.py`(tkinter,仅标准库):文件夹选择 + 本地/云端 OCR 权衡说明 + token/consent + 实时进度条 + 完成摘要与下一步。薄前端 shell out 到 `ingest.py`,不重实现任何门/退出码/consent;token 仅经子进程环境变量注入,**绝不落盘**。无桌面环境明确中文报错并以专用退出码(3)退出,绝不静默降级无头。
- 案件自动搭建 `_setup_case`:用户只给一个 case 路径,系统幂等地把根下散落的非脚手架条目移动进 `原始资料/`(可续传、同名不覆盖、跳过隐藏项与脚手架)。移动清单记入 `ingest-report.json` 的 `stages.setup.moved`,事后可查;GUI 移动前预览并要用户确认(防"指错文件夹把整盘搬走")。

### Changed

- lawiki SKILL 第一步收敛为 **GUI-only 摄入入口**:agent 后台启动 GUI、以 `ingest-report.json` 出现 + GUI 退出为完成信号,再进建 wiki/问答;无头 `ingest.py` 仍为引擎底座,但降为 CI/无桌面兜底,不再作为给 agent 的 sanctioned 用户路径。
- 摄入引擎的建索引与源级对账改为直接复用 `rag.index_case` / `reconcile.reconcile` 纯函数(不再 shell out 解析 stdout);`_RESERVED_CASE_ENTRIES` 从 `init_case.SCAFFOLD_ENTRIES` 单一来源派生,防脚手架清单漂移。

## 1.8.0 - 2026-08-08

### Added

- makeitdown 引入 `firecrawl-anydoc`(进程内 Rust 解析器,零依赖),补齐老式二进制 Office 转换:`.doc/.wps` 在 COM(Word/WPS)/LibreOffice 之后新增 anydoc 兜底(引擎标 `legacy:anydoc`),免掉"未装 Office/LibreOffice 就跳过老 .doc"的外部依赖;`.ppt/.xls/.xlsb` 由过去的"不支持"改为 anydoc 直转。加密件跳过并提示。markitdown 仍为原生格式引擎,不受影响。

### Changed

- 转换失败语义统一:新增 `ConversionUnavailable` 基类(`LegacyConversionUnavailable` 为其子类),anydoc 的类型化异常经其转为"知情跳过"(进 `report["skipped"]` 带提示),绝不误报为 failure、绝不中断整批。

## 1.7.1 - 2026-07-22

### Fixed

- makeitdown 的 `requires-python` 去掉过时的 `<3.13` 上限（paddleocr/paddlepaddle 早已支持 3.13+）；安装器与文档不再把客户已装的更高版本 Python 强行降级到 3.12。

## 1.7.0 - 2026-07-21

### Security / trust boundaries

- `lint answer` 改为逐行拒绝未锚定事实；普通 blockquote 不再被当作分析豁免。
- Wiki 页面同样逐行拒绝未锚定事实；导航 wikilink 与确定性勾稽行保留结构豁免。
- 来源 frontmatter 引入 `provenance_version: 1`；缺版本的旧转换可读但不能通过完成闸门。
- wiki 与回答锚点统一限制在案件 `_md/` 内，拒绝 `..` 与符号链接逃逸。
- makeitdown 为原件与转换正文写入 SHA-256；lawiki 在 ingest 闸门复核两者。
- OCR 与标题重建 LLM 共用显式外部处理同意。
- 兼容 OpenAI 的远端 embedding 需要 `RAG_CLOUD_CONSENT=1`，且报错明确说明外传范围。
- JSON sidecar 与 Markdown/report 输出改为原子替换，降低崩溃截断风险。
- 有效锚点不再豁免其后同一行夹带的无来源事实；Wiki 首标题只豁免页面身份或固定结构标题。
- 源级对账不再把缺少非空理由的 skip 视为已处置。

### Changed

- 覆盖率未处置或 skip 无理由时，`lint check` 返回非零。
- 安装器在环境或组件安装失败时返回非零。
- `RAG_MIN_SCORE` 拒绝非有限值和 `[0,1]` 外数值。
- 搜索结果稳定包含 `parent_text`，关闭或旧索引时为 `null`。
- 删除未实现却被公开配置的 `cloud` rerank；端到端验收覆盖原始资料到带锚点回答。
- bundle 构建强制校验顶层版本与两个 Python 包版本一致。
- 依赖锁将 Pillow 提升到 12.3.0+、MCP 提升到 1.28.1+，修复发布前漏洞审计发现的已知安全问题。

## 1.6.0 - 2026-07-17

- 增加 parent-context（small-to-big）检索。
- 增加向量通道相关度阈值 `RAG_MIN_SCORE`。
