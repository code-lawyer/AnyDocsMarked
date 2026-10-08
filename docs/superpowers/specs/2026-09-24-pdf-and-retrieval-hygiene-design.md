# PDF 逐页读取与检索卫生（2026-09-24）

状态：**草案，待批准。** 批准前不改运行代码，也不改 `2026-09-20` 结构导航规格的现行句子。批准并落地后，只有显式打开 `--pdf-reader-inspector` 的 PDF 才改走新正文；默认转换仍是 MarkItDown。

参照实现（只取已读过的代码和文档，不取 README 宣传分数当作验收）：

- Firecrawl `pdf-inspector` `1.24.0`（MIT），<https://github.com/firecrawl/pdf-inspector>
- 腾讯 `WeKnora` `v0.8.2` 文档与源码中的三处手法：OLE 魔数改道、重排前清洗、无模型词面扩展

## 1. 不可动摇

下列条目与 `docs/product-requirements.md`、`CONTEXT.md`、ADR-0001、2026-08-15 工具/LLM 职责划分一致。本方案若与之冲突，以它们为准，改方案，不改原则。

1. **证据忠实优先。** 金额、日期、案号不得被转换或检索改写成另一种写法。查不到就说明未找到。
2. **`_md` 仍是来源层。** 锚点继续对着 `_md` 逐字核对。页标记仍是 `<!-- page: N -->`，lint 仍把它当格式噪声。页码不能代替锚点。
3. **三模块互不 import。** `pdf-inspector` 只出现在 makeitdown。rag-retriever 不读 PDF 结构，不调用 pdf-inspector。lawiki 只传 CLI 标志和 `RAG_*` 环境变量。
4. **默认不外发。** 本方案不调用 `process_pdf_with_ocr`，不下载 PP-OCRv6，不读取 `PDFIUM_LIB_PATH` / `ORT_DYLIB_PATH`，不理会 `pages_recommending_hosted`。混合页若要 OCR，只走现有本地引擎；云端 OCR 仍要 `--cloud-consent`。
5. **可选失败只降级。** 未安装 `makeitdown[pdf]`、或 pdf-inspector 抛错，该文件退回今天的 MarkItDown / 整份 OCR 路径，转换不因此失败。
6. **默认字节不变。** 不传新开关时，PDF 正文、`page_map`、检索结果与今天一致。重排清洗只在已经打开的本地 rerank 里改变送进模型的字符串，不改返回的 `text`。词面扩展默认关闭。
7. **覆盖账本、三类标注、lint answer 不放松。** 本方案不写 wiki，不增加第二套问答。

`docs/superpowers/specs/2026-09-20-structure-navigation-design.md` 里「文字层 PDF 的正文仍是 MarkItDown，对不上就不写 `page_map`」在默认路径上继续有效。本方案只给一条显式旁路。落地时在该文开头加一段指向本文的说明，不改写其余条款。

## 2. 要解决的运行问题

1. **整份 PDF 一刀切。** `router.classify` 用平均每页字符数：够 50 字整份走 MarkItDown，不够整份走 OCR。夹在文字合同里的扫描页会被当成文字；字很多但是乱码的页会因为凑够字数跳过 OCR。
2. **页标记靠事后对齐。** `align_page_markers` 要求每一页最长的一行按顺序出现在 MarkItDown 正文里，对不上就整份不写 `page_map`。
3. **乱码会进 `_md`。** 一旦判成文字层，不可信的文本层成为来源正文。
4. **扩展名说谎。** 名叫 `.docx` / `.xlsx` / `.pptx`、文件头却是 OLE（`D0 CF 11 E0`）的文件走原生 MarkItDown。嗅探今天只发生在 `.doc` / `.wps`。
5. **重排把 Markdown 语法一起打分。** `LocalReranker` 把分块原文整段送进 cross-encoder。
6. **关键词召回偏瘦时没有第二次本地查询。** FTS 已经用 jieba。第一次词面没命中时，不再用内容词重查。

阅读顺序、表格和标题的差距，用 pdf-inspector 已生成的 Markdown 吸收，不在 Python 里重写它的表格检测。

## 3. 采纳与拒绝

### 采纳

| 来源 | 落到本仓库 | 理由 |
|---|---|---|
| `extract_pages_markdown`：逐页 Markdown，`needs_ocr`，公开接口在需要 OCR 时把该页 Markdown 置空 | makeitdown 可选旁路 | 页边界来自解析，乱码不进正文 |
| 逐页原因：`scanned`、`invisible_text_layer`、`suspected_garbled_text`、`vector_text` | 同一旁路的路由信号 | 补上「有字但不可信」 |
| 全部页面都 `needs_ocr` | 退回现有整份 OCR | 扫描件继续用 Paddle / MinerU、旋转纠偏和双 OCR |
| 部分页 `needs_ocr` | 可信页用 inspector 正文；不可信页用现有 OCR 只跑那一页 | 混合卷宗 |
| WeKnora：OLE 魔数改道 | `router.classify` 对 `.docx/.xlsx/.pptx` | 与 `.doc/.wps` 已有嗅探对齐 |
| WeKnora `cleanPassageForRerank` | 只清洗送进本地 rerank 的字符串 | 返回给 agent 的分块原文不变 |
| WeKnora 无模型查询扩展 | BM25 通道、召回少于 `k` 时最多再查两次 | 不改写向量查询，不调用模型 |

### 拒绝

- `detect_pdf` 的默认 `ScanStrategy::Sample(8)`。抽样漏掉中间扫描页时，整份会被当成文字件。路由只用逐页的 `needs_ocr`。
- `process_pdf_with_ocr`、PP-OCRv6、PDFium 渲染 OCR、`pages_recommending_hosted`。Windows 上他们的 OCR 路径仍是预览；融合两种识别会合成出第三种金额。
- 他们的页标记 `<!-- Page N -->`。我们继续用 `join_pages` 写 `<!-- page: N -->`。
- 把 pdf-inspector 放进默认安装，或在夹具通过前把默认阅读器从 MarkItDown 换掉。
- 在 rag-retriever 的 `extract.py` 里换 PDF 引擎。案件索引读的是 `_md`。直接对原始 PDF 跑 rag 不在本方案内。
- 模型改写问题、Wiki 加权、检索为空时自由作答、可编辑分块、Neo4j。这些不在本次采纳范围里。

## 4. 目标数据流

```
原始资料/*.pdf
    │  未传 --pdf-reader-inspector，或未安装 makeitdown[pdf]，或 inspector 抛错
    ├─ 今天的路径：平均字数 → MarkItDown 或整份 OCR
    │             align_page_markers 对上才写 page_map
    │
    └─ 显式 --pdf-reader-inspector 且 import 成功
          extract_pages_markdown（每一页）
          ├─ 全部 needs_ocr → 现有整份 OCR（引擎标签不变）
          ├─ 全部可信 → join_pages，engine=pdf-inspector，page_map=native
          └─ 混合 → 可信页用 inspector 正文
                    不可信页渲染成图，交给现有 OCR dispatcher
                    该页 OCR 不可用（无本地引擎且未同意云端）→ 该页正文为空
                    原因写入 report 的 warning，不写进正文
                    engine=pdf-inspector+<ocr引擎>
                    page_map=native

.doc/.wps                         不变
.docx/.xlsx/.pptx 且文件头为 OLE  → legacy（COM / LibreOffice / anydoc）
.docx/.xlsx/.pptx 且文件头为 ZIP  → 仍是 native

检索（与 PDF 旁路无关，默认同今天）
    BM25 + 向量 + RRF
    RAG_QUERY_EXPAND 打开且 FTS 命中数 < k → 内容词变体再查，只追加
    本地 rerank 打开 → 用清洗后的字符串打分，hit["text"] 仍是原分块
```

`PageMarkdown.page` 从 0 起，`pages_needing_ocr` 从 1 起。写 `<!-- page: N -->` 时 N 从 1 起，用页在结果列表中的顺序，不使用 `pages_needing_ocr` 的下标做页码。

## 5. 开关与依赖

| 项 | 值 |
|---|---|
| 可选依赖 | `makeitdown[pdf]` = `pdf-inspector==1.24.0`。不进默认 `dependencies` |
| makeitdown 标志 | `--pdf-reader-inspector`（store_true，默认关） |
| ingest / GUI | 能力 id `pdf_reader_inspector`，tier `CHOICE`，phase `ingest`，`gui_control` 同名，`ingest_flag` 为 `--pdf-reader-inspector`，`default_on` false |
| 词面扩展 | `RAG_QUERY_EXPAND`，默认关。能力 id `rag_query_expand`，phase `answer`，`answer_persist` true |
| 重排清洗 | 无新开关。仅 `RAG_RERANK=local` 时生效 |
| Python | `>=3.11`（makeitdown 现状） |
| 版本号 | 本方案不单独改 bundle 版本。行为变更写入 `CHANGELOG.md` 的未发布节 |

`answer_env_from_case` 把布尔 True 写成环境值 `local`（为 `RAG_RERANK`）。因此 `RAG_QUERY_EXPAND` 把 `1`、`true`、`yes`、`on`、`local` 都视为打开。不要改 rerank 的注入语义。

## 6. 验收

合成材料，不使用真实案卷。

1. **默认路径。** 不传新标志时，现有 makeitdown 与 rag 测试保持通过。一份两页文字 PDF 的引擎仍是 `markitdown`。
2. **逐页正文。** 打开旁路后，PyMuPDF 写出的两页文字 PDF（Helvetica，含 `Article 3` 与 `50,000.00`）两页字符串都在，`<!-- page: 1 -->` 在第一页前，`<!-- page: 2 -->` 在第二页前，`page_map` 为 `native`，引擎为 `pdf-inspector`。
3. **混合页。** mock 的 inspector 返回第 0 页可信正文、第 1 页 `needs_ocr=True` 且 Markdown 为空。第 1 页走 mock OCR，`join_pages` 顺序正确，OCR 正文不覆盖第 0 页的 `50,000.00`。
4. **整份扫描。** mock 的两页都 `needs_ocr` 时，调用现有整份 OCR，不写 inspector 引擎名。
5. **不可用即降级。** 未安装或 `extract_pages_markdown` 抛错时，退回 MarkItDown，文件记 warning，不记 failure。
6. **OCR 不可用的混合页。** 无本地 OCR、无云端同意时，该页正文为空，warning 含 1 起页码，正文里不出现解释句。
7. **OLE。** 文件名 `.docx`、头 8 字节为 OLE 魔数时 `classify` 为 `legacy`，不调用 MarkItDown。ZIP 头的 `.docx` 仍是 `native`。
8. **重排清洗。** 输入含 `# 标题`、`**50,000.00**`、图片标记、表格分隔行、HTML。清洗结果仍含 `标题` 与 `50,000.00`，不含 `#`、`**`、`---|`、`<b>`。`rerank` 返回的 `hit["text"]` 与输入原文相同。
9. **词面扩展。** 默认关闭时搜索结果与今天相同。打开且第一次 FTS 为空、变体能命中时，命中出现在结果里。变体函数不发网络请求。
10. **CI。** makeitdown 的 Ubuntu 与 Windows 任务安装 `--extra pdf`，第 2 条真实夹具两边都跑。若 `1.24.0` 的 wheel 在任一平台 import 失败，停止该旁路，不换一个未读过的版本凑合。

第 2 条通过之前，不把 `--pdf-reader-inspector` 的默认值改成开。那是另一次批准。

## 7. 变更准入对照

| 问题 | 答案 |
|---|---|
| 哪个用户步骤 | build 的材料转换；answer 的召回卫生 |
| 增强哪项可信目标 | 证据忠实（乱码不进 `_md`、页能指回正文）；检索效率（瘦召回与重排噪声） |
| 为何不深化现有模块即可 | MarkItDown 不逐页分类，也不把不可信文本层置空。平均字数阈值修不好「字多但是乱码」 |
| 谁负责 | 确定性代码。宿主 agent 不选择引擎、不改写查询 |
| 失败如何降级 | inspector 缺失或抛错 → 今天的路径。混合页 OCR 缺失 → 该页空正文 + warning。扩展关闭 → 今天的检索 |
| 如何验收 | 第 6 节的合成夹具。不用 BLEU / ROUGE，不用 OpenDataLoader 总分代替金额与页码 |
