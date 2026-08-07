# lawiki

把一个法律案件的资料整合成**可控、可溯源**的案件 wiki。成果是一个**可被不同 agent 使用的 skill**(`skill/lawiki/SKILL.md`),不是 CLI 工具。

## 流水线

```
原始资料/ ──makeitdown──▶ _md/ ──lawiki skill──▶ wiki/
```

- `原始资料/`:你丢入的法律文件(不可变)。
- `_md/`:[makeitdown](https://github.com/Tsinglaw/AnyDocsMarked/tree/main/makeitdown) 转出的 markdown(按工作流只读;lawiki 通过其 frontmatter 契约 `source` / `quality: suspect` 对接,原件与正文 SHA-256 由 lint 校验)。
- `wiki/`:LLM 按 skill 构建的案件 wiki:案件主体 / 法律关系 / 法律事实 / 时间线 + index/log。产出**以 Obsidian 为基准**(wikilink `[[]]` 驱动图谱反链、callout 标注分析/冲突、frontmatter 即 properties),在非 Obsidian 查看器中亦为合法 markdown。

## 挂载与使用

- **Claude Code / Copilot**:把整个 `skill/lawiki/`(含 `references/`、`lint/`)放入 skills 目录,agent 按 `description` 自动触发。
- **Codex 等**:把 `SKILL.md` 内容作系统指令喂给 agent 并带上 `references/`、`lint/`;或放入案件目录作 `AGENTS.md`。

把文件放进某案件的 `原始资料/`,让 agent "处理 / 整理 / 建库",agent 会调 makeitdown 转换、再按 skill 归档进 `wiki/`,无需手动跑中间步骤。首次使用时 agent 会照 `references/setup.md` 引导配环境(检测 Python/makeitdown → 选 OCR 方式 → 安装 → 告知激活语);lawiki 自带的 `lint/` 零第三方依赖,只要有 Python 即可跑校验。

## 可控性(为何可信)

skill 内嵌不可违反的铁律:写进 wiki 的每句话必须归入 **EXTRACTED(原文直取)/ INFERRED(推断)/ AMBIGUOUS(存疑)** 三类之一并各自打标,无法归类者不写。硬底线:凡作为事实陈述的(EXTRACTED)必须挂固定格式逐字锚点 `〔来源: _md/…：「逐字原文」〕`,可回溯、可机检;推断须标为分析,存疑须显式标注。

## 校验(lint,确定性闸门)

`python <SKILL_DIR>/lint/lint.py check <案件根目录>` 把铁律从"自觉"变成"可机检":

- **违规(退出码非 0,必修)**:锚点逐字存在且解析后仍位于本案 `_md/`、原件/正文 SHA-256 未变、无死链、时间线顺序、勾稽闭合。
- **完整性警告(同样非 0,未清零不得宣称完成)**:`_md/` 下未引用且未登记跳过的文件,或跳过却没理由的文件。

`lint answer` 还逐行拒绝未锚定事实(只有明确的 `> [!note] 分析` callout 可豁免)。校验只消除格式噪声,数字与文字保持精确。

**蕴含校验(每次 ingest 收尾自动跑)**:`lint extract` 把每条 `(断言, 引文, 来源, 上下文)` 拆出,交一个**换实例的 LLM 判官**三分判"引文是否支持断言",抓"引文真但断言被脑补/拔高/歪曲"。判官只判不改;ingest agent 据判官**有界修复**(只许把断言改忠实,绝不编造),三轮仍不过则显著上报用户。协议见 `references/verification.md`,法律定性正确性归人。

## 设计

`docs/superpowers/specs/2026-06-16-lawiki-design.md`
