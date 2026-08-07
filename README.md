# AnyDocsMarked

**把成百上千页杂乱案件材料,变成可控、可溯源、经得起交叉验证的案件知识库,再就案情做可回溯的问答。**

为**法律工作**而生:面对判决书、合同、笔录、票据、聊天记录,**每一个金额、日期、当事人、法条都不能错,任何一句结论都要能指回原文**。核心承诺只有一句——**宁可查不出,也不编造**。

> 产品目标、边界与验收标准见 [`docs/product-requirements.md`](docs/product-requirements.md);文档全图见 [`docs/README.md`](docs/README.md)。

---

## 它解决的真问题

通用 AI 和普通 RAG 用在法律场景,会在最不能出错的地方出错:

| 法律工作的硬要求 | 通用方案的问题 | AnyDocsMarked 的做法 |
|---|---|---|
| 结论必须能指回原文 | 生成式回答无法逐字溯源 | 每句事实挂**逐字来源锚点**,**确定性 lint 机器校验** |
| 金额/日期错一位就是事故 | OCR 静默出错、模型幻觉数字 | **双 OCR 互校** + 质检标记 + 数字/日期位专项比对 |
| 近义法律术语必须分清 | 纯向量把"表见/无权代理"混为一谈 | **BM25 + 向量混合检索**(RRF),术语精确召回 |
| 推断不能冒充事实 | 模型把分析当事实陈述 | **三类标注铁律**:原文/推断/存疑物理隔离,越界即拦 |
| 只答本案,不答"一般来说" | 模型用通用知识填补本案空白 | **闭世界问答**:答前必检索,锚点须指向本案,裸答打回 |
| 敏感案卷不能随便外传 | 云服务默认上传 | **可完全本地离线、默认不外发**;外部处理需**显式同意** |
| 漏检等于没做 | 漏 ingest 无人知晓 | **三层互补** + **wiki×RAG 交叉验证** + **三态覆盖账本** |

---

## 设计哲学

理解这六条,就理解整个仓库的每一处取舍:

1. **可信 > 聪明;宁可查不出,也不编造。** 下限决定一切——查不到就明说"未在本案材料中找到",绝不用通用知识冒充本案事实。
2. **确定性闸门优先于模型判断。** 能用规则校验的一律用零依赖 lint 卡住(锚点、死链、日期顺序、勾稽、覆盖率、闭世界越界),退出码非 0 不放行;模型只做规则做不了的归纳与蕴含判断,且仍受闸门约束。
3. **锁两端,放中段。** 取证有前闸门、交付有后闸门,两端确定性锁死;中间的推理、导航、组织答案完全交给 agent。
4. **倾向 ≠ 裁决。** 证据矛盾且查不出原因时,agent 必须并列两套答案 + 各自锚点交人裁决(安全阀),但应附标注为分析的倾向(立场 + 理由 + 排除不掉的反证)。机器永不静默取舍,人永握终裁权。
5. **松耦合 + 稳定契约。** 三模块**互不 import**,仅通过 CLI / JSON / frontmatter 契约衔接,各自可单用、可替换、可单测。
6. **可降级 + 默认隐私。** lawiki 核心零依赖,只要有 Python 就能跑校验;RAG 与 OCR 可插拔,没装则问答自动降级并告知;默认不外发,上云需 `--cloud-consent`,非交互也绝不静默上传。

---

## 仓库构成

三个**各自独立、通过稳定契约协作、互不 import** 的模块:

| 子项目 | 职责 | 形态 |
|---|---|---|
| [`makeitdown/`](makeitdown/README.md) | 各式文件(PDF/Word/扫描件/图片/老式 .doc.wps…)→ **带质量标记**的 markdown | CLI,双 OCR 互校可选 |
| [`rag-retriever/`](rag-retriever/README.md) | 本地优先语义检索:**结构感知分块 + BM25/向量混合检索**,不含 LLM | CLI / MCP 工具 |
| [`lawiki/`](lawiki/README.md) | 案件 wiki skill:`_md` → 可溯源 wiki + **wiki×RAG 交叉验证问答** | agent skill,确定性 lint,核心零依赖 |

---

## 架构与数据流

```
原始资料/                     不可变的原始案卷
   │  makeitdown（转换 + 质检 + 可选双 OCR 互校）
   ▼
_md/                          带 frontmatter 的高保真 markdown（双 SHA-256 封存来源层）
   ├─ lawiki ingest ───────▶  wiki/   案件主体·法律关系·法律事实·时间线（每句挂逐字锚点）
   └─ rag-retriever index ─▶  .rag/   结构感知分块 + 向量 + BM25 索引
                                 │
      问答：lawiki（综合结论） ◄── wiki×RAG 交叉验证 ──► rag-retriever（原文召回）
                                 一致则答；不一致 → 暴露给人裁决
                                 回答草稿过 lint answer，0 违规才交付
```

**三层互补,各司其职、互为校验:** `_md`(原文层,SHA-256 复核)· `wiki`(综合层,逐字锚点可机检)· `.rag`(召回层,防漏检安全网,可重建可选)。三层独立产生;问答时 wiki 主检索主作答("人类式思考"),RAG 独立检索用于验证("机器式")——两条独立路径得同一答案才可信,不一致即报警。

---

## 技术要点

| 要点 | 一句话 |
|---|---|
| 机器可校验的逐字溯源 | 每句事实挂 `〔来源: _md/…：「逐字原文」〕`,lint 确定性核验锚点逐字存在;只消除格式噪声,数字文字精确比对 |
| 三类标注 + 换实例蕴含判官 | 每句归入 EXTRACTED/INFERRED/AMBIGUOUS;ingest 收尾用换实例 LLM 判官抓"引文真但断言被拔高/脑补" |
| 问答双闸门 | 前闸门 `evidence.py` 跑齐 RAG + 精确 grep + outline 三路取证;后闸门 `lint answer` 逐行拒绝未锚定事实 |
| 三态覆盖账本 | 每份源文件判为 已引用/已登记跳过/未处置;未处置 > 0 不许宣称 ingest 完成 |
| 双 OCR 互校 | Paddle × MinerU 独立识别同一页,盯金额/日期位分歧标 `quality: suspect`,失败也绝不丢结果 |
| 结构感知 + 混合检索 | 沿标题切块(带面包屑)、表格不切、法律标记软边界;BM25(jieba 离线) + 向量 RRF 融合 |
| LLM 标题重建 | 只返回"行号→标题级"数字,绝不经手正文,金额/日期原理上零篡改风险,失败回退原文 |
| 确定性导航 | `graph.py` 走已过 lint 的 wikilink 建图答多跳;`outline.py` 全库标题树对抗措辞差异漏检 |
| 国内可用 + 真离线 | Release `-offline.zip` 内置 embedding 模型,`install.py --check-offline` 自检运行期离线出向量 |

各点的详细设计见对应子模块 README 与 [`docs/superpowers/`](docs/superpowers)。

---

## 使用

1. **建案件目录**,原始文件放进 `原始资料/`。
2. **转换**:`makeitdown 原始资料 -o _md`(递归转换 + 自动质检;高危件加 `--ocr-cross-check`)。
3. **建库**:lawiki skill 读 `_md` 按铁律归档进 `wiki/`(收尾自动跑 lint + 蕴含判官 + 覆盖率清账);rag-retriever 对 `_md` 建索引(可选,没装不阻塞)。
4. **问答**:对 agent 说"问本案…"。走闭世界协议:多路取证 → wiki×RAG 交叉验证 → 带逐字来源作答;冲突时两版并列 + 附倾向分析交人裁决;过 `lint answer` 才交付。

**非技术用户**:把整个仓库(或 Release bundle)交给 AI 助手(Claude Code / Copilot 等),说一句"整理案件资料",助手会自动装环境、跑完转换与建库。

---

## 安装

**推荐下载 Release bundle**([Releases](https://github.com/Tsinglaw/AnyDocsMarked/releases)):

- `anydocsmarked-v*-offline.zip` — 内置 embedding 模型,解压即离线可用,**国内推荐**;
- `anydocsmarked-v*.zip` — 源码包,首次建索引需联网下模型。

解压后让 agent 加载 `skill/lawiki`,跑 `python install.py`(自动装 makeitdown + rag-retriever,`--check-offline` 可自检)。

**从源码**(两个可安装包,lawiki 是 skill 无需安装):

```bash
uv tool install "makeitdown[local] @ git+https://github.com/Tsinglaw/AnyDocsMarked.git#subdirectory=makeitdown"
uv tool install "rag-retriever @ git+https://github.com/Tsinglaw/AnyDocsMarked.git#subdirectory=rag-retriever"
```

> 注意:git 源码路径装不出离线版(vendor 模型不进 git),要真离线请用 `-offline` bundle。镜像加速见 [`lawiki/skill/lawiki/references/setup.md`](lawiki/skill/lawiki/references/setup.md)。

---

## 工程质量

- **CI 双矩阵**:三子项目测试套件在 Ubuntu + Windows 上跑(Windows 是律师主平台);仓库级 ruff 门禁 + 生产代码分支覆盖率 80% 硬门槛(当前各模块 84%–86%)。
- **lint 自带边界测试**:锚点归一化"抓真错、不误报"由 `test_lint.py`(嵌套引号、千分位、换行、逐行裸答、路径穿越、来源哈希)钉死。
- **发布即校验**:release 工作流机器验证 offline 包内确含 ONNX 模型,缺了直接 fail。
- **规格驱动**:较大特性先 spec 再 plan 再实现,留档 [`docs/superpowers/`](docs/superpowers)。
- 版本变化见 [`CHANGELOG.md`](CHANGELOG.md),安全基线见 [`SECURITY.md`](SECURITY.md)。

---

## License

[MIT](LICENSE) © Tsinglaw。各子项目另附各自的 LICENSE。
