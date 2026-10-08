# PDF 逐页读取与检索卫生 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用显式开关吸收 pdf-inspector 的逐页 PDF 读取，并补上 OLE 扩展名纠偏、重排前清洗、无模型词面扩展。默认转换和默认检索保持今天的字节行为。

**Architecture:** makeitdown 新增可选 extra `pdf` 和 `--pdf-reader-inspector`。打开且导入成功时走 `extract_pages_markdown`；全部不可信则退回现有整份 OCR；混合页只把不可信页交给现有 OCR。rag-retriever 在本地 rerank 打分前清洗字符串，并在 `RAG_QUERY_EXPAND` 打开且 FTS 命中数小于 `k` 时追加内容词查询。两模块互不 import。

**Tech Stack:** Python ≥3.11、pdf-inspector==1.24.0（可选）、现有 PyMuPDF / MarkItDown / Paddle、jieba、pytest。

**Spec:** `docs/superpowers/specs/2026-09-24-pdf-and-retrieval-hygiene-design.md`

## Global Constraints

- 本方案若与 `docs/product-requirements.md`、`CONTEXT.md`、ADR-0001、2026-08-15 工具/LLM 职责划分冲突，改方案，不改原则。
- `pdf-inspector==1.24.0` 只放在 `makeitdown[pdf]`，不进默认 `dependencies`。不调用 `process_pdf_with_ocr`，不下载 PP-OCRv6，不读 `PDFIUM_LIB_PATH` / `ORT_DYLIB_PATH`。
- 不传 `--pdf-reader-inspector` 时，PDF 仍走今天的 `classify` + MarkItDown / 整份 OCR，`align_page_markers` 行为不变。
- 页标记只由 `join_pages` 写成 `<!-- page: N -->`，N 从 1 起。不用 `<!-- Page N -->`。
- 云端 OCR 仍要 `--cloud-consent`。未同意且无本地 OCR 时，混合页正文为空，原因进 report warning，不写进正文。
- `RAG_QUERY_EXPAND` 默认关。打开时只追加 BM25 命中，不改写向量查询。`1` / `true` / `yes` / `on` / `local` 都算打开。
- 本地 rerank 的清洗不改变返回的 `hit["text"]`。`rerank=none` 时不清洗。
- 三模块互不 import。rag 的 `extract.py` 继续用 MarkItDown，本方案不改它。
- 测试只用合成数据。makeitdown 测试在 `makeitdown/` 下跑；rag 测试在 `rag-retriever/` 下跑。
- 第 6 节真实夹具通过之前，不把新开关的默认值改成开。
- Task 1、2、3 不依赖 Task 0。Task 0 失败则不做 Task 4–6，Task 1–3 仍可落地。

---

### Task 0: 确认 pdf-inspector 1.24.0 能在 Windows 和 Ubuntu 导入

**目的:** 旁路的版本钉死在已读过的 1.24.0。wheel 导入失败就停，不改钉别的版本。

**Files:**
- 不提交产品代码。

- [ ] **Step 1: 在 Windows 上导入**

在仓库根目录：

```powershell
uv run --python 3.12 --with "pdf-inspector==1.24.0" python -c "import pdf_inspector; print(pdf_inspector.__doc__[:80] if pdf_inspector.__doc__ else 'ok')"
```

Expected: 进程退出码 0，且没有下载 PDFium 或 OCR 模型。

- [ ] **Step 2: 确认公开符号**

```powershell
uv run --python 3.12 --with "pdf-inspector==1.24.0" python -c "import pdf_inspector; assert hasattr(pdf_inspector, 'extract_pages_markdown')"
```

Expected: 退出码 0。

- [ ] **Step 3: 失败时停止 Task 4–6**

若 PyPI 没有 `1.24.0`，或 Windows 导入失败：把完整报错写进本任务备注，不要把 extra 改成别的版本。Task 1–3 继续。

---

### Task 1: 名为 Office Open XML、实为 OLE 的文件走 legacy

**Files:**
- Create: `makeitdown/src/makeitdown/magic.py`
- Modify: `makeitdown/src/makeitdown/convert_legacy.py`（改为调用 `magic.sniff_container`）
- Modify: `makeitdown/src/makeitdown/router.py`
- Test: `makeitdown/tests/test_router.py`

**Interfaces:**
- Consumes: 无
- Produces: `sniff_container(path: Path) -> str`，返回 `"ooxml"`、`"ole2"` 或 `"unknown"`。`classify` 对 `.docx` / `.xlsx` / `.pptx` 且结果为 `"ole2"` 时返回 `"legacy"`。

- [ ] **Step 1: 写失败测试**

在 `makeitdown/tests/test_router.py` 追加：

```python
OLE2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
ZIP = b"PK\x03\x04"


def test_docx_with_ole_header_is_legacy(tmp_path):
    path = tmp_path / "contract.docx"
    path.write_bytes(OLE2 + b"not a zip")
    assert router.classify(path) == "legacy"


def test_docx_with_zip_header_stays_native(tmp_path):
    path = tmp_path / "contract.docx"
    path.write_bytes(ZIP + b"not a real docx")
    assert router.classify(path) == "native"
```

- [ ] **Step 2: 跑测试，确认失败**

```powershell
cd makeitdown
uv run --extra dev python -m pytest tests/test_router.py::test_docx_with_ole_header_is_legacy -q
```

Expected: FAIL，`classify` 返回 `"native"`。

- [ ] **Step 3: 实现**

新建 `makeitdown/src/makeitdown/magic.py`：

```python
from pathlib import Path

_OOXML_MAGIC = b"PK\x03\x04"
_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def sniff_container(path: Path) -> str:
    """Return 'ooxml', 'ole2', or 'unknown' from the first 8 bytes."""
    with open(path, "rb") as fh:
        head = fh.read(8)
    if head.startswith(_OOXML_MAGIC):
        return "ooxml"
    if head.startswith(_OLE2_MAGIC):
        return "ole2"
    return "unknown"
```

`convert_legacy.py` 删掉本文件里的 `_OOXML_MAGIC`、`_OLE2_MAGIC` 和 `_sniff`，改为：

```python
from .magic import sniff_container
```

`convert()` 里的 `kind = _sniff(src)` 改为 `kind = sniff_container(src)`。

`router.py` 在 `classify` 开头、按扩展名返回 `"native"` 之前：

```python
from .magic import sniff_container

_OFFICE_ZIP_EXTS = {".docx", ".xlsx", ".pptx"}
```

```python
def classify(path: Path, text_threshold: int = 50) -> str:
    ext = path.suffix.lower()
    if ext in _OFFICE_ZIP_EXTS and sniff_container(path) == "ole2":
        return "legacy"
    # 其余保持现有分支
```

读失败时 `sniff_container` 会抛异常。`classify` 捕获 `OSError`，当作 `"unknown"`，继续按扩展名走 native，避免一个打不开的文件变成 legacy。

- [ ] **Step 4: 跑测试**

```powershell
uv run --extra dev python -m pytest tests/test_router.py tests/test_convert_legacy.py -q
```

Expected: PASS。

- [ ] **Step 5: Commit**

```powershell
git add makeitdown/src/makeitdown/magic.py makeitdown/src/makeitdown/convert_legacy.py makeitdown/src/makeitdown/router.py makeitdown/tests/test_router.py
git commit -m "fix: route OLE bytes misnamed as docx to the legacy converter"
```

---

### Task 2: 本地重排只对清洗后的字符串打分

**Files:**
- Create: `rag-retriever/rag_retriever/passage_clean.py`
- Modify: `rag-retriever/rag_retriever/rerank.py`
- Test: `rag-retriever/tests/test_passage_clean.py`
- Modify: `rag-retriever/tests/test_rerank.py`（若已有 LocalReranker 测试则追加；没有则新建该文件里的假模型测试）

**Interfaces:**
- Consumes: 无
- Produces: `clean_passage(text: str) -> str`。`LocalReranker.rerank` 把 `clean_passage(h["text"])` 送给模型，返回的 `hit["text"]` 仍是原文字。

- [ ] **Step 1: 写失败测试**

`rag-retriever/tests/test_passage_clean.py`：

```python
from rag_retriever.passage_clean import clean_passage


def test_clean_passage_keeps_amount_and_heading_words():
    raw = "\n".join([
        "# 标题",
        "**50,000.00**",
        "![scan](images/p1.png)",
        "<b>被告</b>",
        "| --- | --- |",
        "https://example.invalid/a",
        "第3条 应付款",
    ])
    cleaned = clean_passage(raw)
    assert "50,000.00" in cleaned
    assert "标题" in cleaned
    assert "第3条" in cleaned
    assert "被告" in cleaned
    assert "#" not in cleaned
    assert "**" not in cleaned
    assert "https://" not in cleaned
    assert "<b>" not in cleaned
    assert "---" not in cleaned
    assert "images/p1.png" not in cleaned
```

`rag-retriever/tests/test_rerank.py` 追加（文件不存在就新建，并保留已有测试）：

```python
from rag_retriever.rerank import LocalReranker


class _FakeModel:
    def __init__(self):
        self.seen = []

    def rerank(self, query, passages):
        self.seen = list(passages)
        return [0.2]


def test_local_reranker_scores_cleaned_text_and_returns_original(monkeypatch):
    fake = _FakeModel()
    monkeypatch.setattr(
        "fastembed.rerank.cross_encoder.TextCrossEncoder",
        lambda name: fake,
    )
    reranker = LocalReranker("fake")
    original = "# 标题\n**50,000.00**"
    out = reranker.rerank("金额", [{"text": original, "source": "a", "ord": 0}], k=1)
    assert out[0]["text"] == original
    assert "50,000.00" in fake.seen[0]
    assert "#" not in fake.seen[0]
    assert "**" not in fake.seen[0]
```

- [ ] **Step 2: 跑测试，确认失败**

```powershell
cd rag-retriever
uv run --group dev python -m pytest tests/test_passage_clean.py tests/test_rerank.py -q
```

Expected: FAIL，`passage_clean` 不存在，或 `LocalReranker` 把 `#` 送进了模型。

- [ ] **Step 3: 实现**

`passage_clean.py` 用正则去掉：HTML 标签、`![alt](url)` 整段、`[文字](url)` 的链接壳但留下文字、裸 `http(s)://`、行首 1–6 个 `#` 及其后的一个空格、成对或单独的 `**` / `__`、只由 `|`、`-`、`:` 和空白组成的表格分隔行、围栏行 `` ``` ``。不要删除数字、逗号、小数点、中文和 `第3条` 这类词。连续空行压成一个空行。

`LocalReranker.rerank`：

```python
from .passage_clean import clean_passage

scores = list(self._model.rerank(query, [clean_passage(h["text"]) for h in hits]))
```

返回的 `hit` 仍从原始 `hits[i]` 拷贝。不要把清洗结果写回 `hit["text"]`。

- [ ] **Step 4: 跑测试**

```powershell
uv run --group dev python -m pytest tests/test_passage_clean.py tests/test_rerank.py -q
```

Expected: PASS。

- [ ] **Step 5: Commit**

```powershell
git add rag-retriever/rag_retriever/passage_clean.py rag-retriever/rag_retriever/rerank.py rag-retriever/tests/test_passage_clean.py rag-retriever/tests/test_rerank.py
git commit -m "fix: score rerank passages without markdown syntax"
```

---

### Task 3: 关键词命中太少时追加无模型变体

**Files:**
- Create: `rag-retriever/rag_retriever/query_expand.py`
- Modify: `rag-retriever/rag_retriever/config.py`
- Modify: `rag-retriever/rag_retriever/pipeline.py`（`search` 里 FTS 那一段）
- Test: `rag-retriever/tests/test_query_expand.py`
- Test: `rag-retriever/tests/test_config.py`
- Modify: `lawiki/skill/lawiki/capabilities.json`
- Modify: `lawiki/skill/lawiki/references/rag.md` 或 `setup.md` 里已经记载 `RAG_RERANK` 的那一节，加上 `RAG_QUERY_EXPAND`
- Test: `lawiki/test_ingest_gui.py` 或现有 answer env 测试，断言 `{"query_expand": true}` 注入 `RAG_QUERY_EXPAND=local`

**Interfaces:**
- Consumes: `tokenize_for_fts`
- Produces: `keyword_variants(query: str) -> list[str]`，最多 2 条，不含原查询。`Config.query_expand: bool`，环境变量 `RAG_QUERY_EXPAND`，默认 False。`Retriever.search` 在 `hybrid and query_expand and len(text_hits) < k` 时对每条变体再调用 `store.search_text`，按 `(source, ord)` 去重后追加到 `text_hits` 末尾。

- [ ] **Step 1: 写失败测试**

`rag-retriever/tests/test_query_expand.py`：

```python
from rag_retriever.query_expand import keyword_variants


def test_variants_drop_function_words_and_keep_content():
    variants = keyword_variants("合同 的 违约金 是 多少")
    assert variants
    assert all("的" not in v.split() for v in variants)
    assert any("违约金" in v for v in variants)


def test_variants_empty_for_blank():
    assert keyword_variants("   ") == []
```

`rag-retriever/tests/test_config.py` 追加：

```python
def test_query_expand_defaults_off(monkeypatch):
    monkeypatch.delenv("RAG_QUERY_EXPAND", raising=False)
    assert Config.load().query_expand is False


def test_query_expand_accepts_local_token(monkeypatch):
    monkeypatch.setenv("RAG_QUERY_EXPAND", "local")
    assert Config.load().query_expand is True
```

在现有 pipeline 搜索测试旁追加一个用假 store 的测试：第一次 `search_text("原问", ...)` 返回 `[]`，第一次变体返回一条 `{source, ord, text, score, metadata}`。`query_expand=True` 时结果含该条；`False` 时变体函数不被调用。假 embedder 返回固定向量，`hybrid=True`，`rerank="none"`。

lawiki 侧：构造只含 `{"query_expand": true}` 的 case.json，调用 `answer_env_from_case`，断言 `"RAG_QUERY_EXPAND" in env` 且值为 `"local"`。

- [ ] **Step 2: 跑测试，确认失败**

```powershell
cd rag-retriever
uv run --group dev python -m pytest tests/test_query_expand.py tests/test_config.py -q -k query_expand
```

Expected: FAIL，`query_expand` 模块或字段不存在。

- [ ] **Step 3: 实现**

`keyword_variants`：

- 用 `tokenize_for_fts` 得到词。
- 去掉空白、单字中文，以及功能字：`的了是在与及或和或其被把将对从为以而也都就`。
- 第一条变体是剩下的词用空格连接。与原词序列相同则不要这条。
- 若内容词不少于 3 个，第二条是按字数最长的两个词，长的在前，空格连接。
- 最多返回 2 条。不调用网络，不调用嵌入模型。

`Config` 增加 `query_expand: bool = False`。`load()` 读取 `RAG_QUERY_EXPAND`，小写后属于 `{1, true, yes, on, local}` 则为 True，缺省 False。

`pipeline.search` 在 `text_hits = self.store.search_text(...)` 之后、RRF 之前：

```python
if self.cfg.query_expand and len(text_hits) < k:
    seen = {(h["source"], h["ord"]) for h in text_hits}
    for variant in keyword_variants(query):
        for hit in self.store.search_text(variant, k=cand, source_prefix=sp):
            key = (hit["source"], hit["ord"])
            if key not in seen:
                text_hits.append(hit)
                seen.add(key)
```

`capabilities.json` 在 `rag_rerank` 之后增加：

```json
{
  "id": "rag_query_expand",
  "promise": "关键词命中少于请求条数时，用去掉功能词后的内容词再查一次 BM25，不调用模型",
  "owner": "rag",
  "tier": "CHOICE",
  "phase": "answer",
  "tradeoff": "只在第一次全文命中不足时追加候选；默认关，避免在没有查找金标准时改变召回",
  "enforcement": "rag.py 读 case.json 注入 RAG_QUERY_EXPAND；rag Config 把 local/true 视为打开",
  "sanctioned": {
    "env": "RAG_QUERY_EXPAND",
    "gui_control": "query_expand",
    "default_on": false,
    "answer_persist": true
  }
}
```

在记载 `RAG_*` 环境变量名单的那个列表里补上 `RAG_QUERY_EXPAND`（`capabilities.json` 末尾若有枚举）。文档里用一句话说明默认关、布尔开关经 case.json 注入为 `local`。

- [ ] **Step 4: 跑测试**

```powershell
cd rag-retriever
uv run --group dev python -m pytest tests/test_query_expand.py tests/test_config.py tests/test_pipeline.py -q
cd ..\lawiki
python -m pytest test_ingest_gui.py test_capability_contract.py -q -k "query_expand or capability or answer_env"
```

Expected: PASS。契约测试会要求 GUI 能呈现 `query_expand`；`build_gui_fields` 从 `gui_control` 派生，不必手写控件。

- [ ] **Step 5: Commit**

```powershell
git add rag-retriever/rag_retriever/query_expand.py rag-retriever/rag_retriever/config.py rag-retriever/rag_retriever/pipeline.py rag-retriever/tests/test_query_expand.py rag-retriever/tests/test_config.py lawiki/skill/lawiki/capabilities.json lawiki/skill/lawiki/references
git commit -m "feat: expand thin keyword queries without a model"
```

若 pipeline 测试文件也改了，一并加入。

---

### Task 4: PDF 旁路的纯函数与 mock 测试

**Files:**
- Create: `makeitdown/src/makeitdown/pdf_reader.py`
- Modify: `makeitdown/src/makeitdown/models.py`（`ConversionResult.notices`）
- Modify: `makeitdown/src/makeitdown/pipeline.py`（把 `notices` 并进 warning）
- Test: `makeitdown/tests/test_pdf_reader.py`

**Interfaces:**
- Consumes: `join_pages`，现有 OCR `dispatcher.convert(image_path) -> ConversionResult`，PyMuPDF
- Produces:

```python
def read_pdf(path: Path, *, dispatcher, cloud_consent: bool) -> ConversionResult | None
```

返回 `None` 表示「调用方请走今天的整份路径」（未安装、抛错、或全部页需要 OCR）。返回结果时 `page_map=="native"`，正文已含 `<!-- page: N -->`。

- [ ] **Step 1: 写失败测试**

`makeitdown/tests/test_pdf_reader.py`。不要在这个文件里要求真的 pdf-inspector。用 monkeypatch 替换 `pdf_reader._extract_pages`。

夹具页对象用简单命名空间：

```python
from types import SimpleNamespace

def _page(index, markdown, needs_ocr):
    return SimpleNamespace(page=index, markdown=markdown, needs_ocr=needs_ocr)
```

用例：

1. `extract` 抛 `RuntimeError` → `read_pdf` 返回 `None`。
2. 两页都 `needs_ocr=True` → 返回 `None`，且不调用 dispatcher。
3. 第 0 页 markdown 为 `"Article 3 50,000.00"`、`needs_ocr=False`；第 1 页 markdown `""`、`needs_ocr=True`。dispatcher 对图片路径返回 `ConversionResult(text="扫描正文", engine="local:pp-structurev3", pages=1, page_map="native")`。结果正文里 `<!-- page: 1 -->` 后有 `50,000.00`，`<!-- page: 2 -->` 后有 `扫描正文`，引擎为 `pdf-inspector+local:pp-structurev3`，`page_map=="native"`。
4. 同上，但 dispatcher 抛 `OCRUnavailableError`。第 2 页标记存在，该页在标记之后没有「需要 OCR」这类解释句，`notices` 含 `"2"`。
5. 两页都可信 → 引擎恰好是 `pdf-inspector`，dispatcher 不被调用。

渲染函数 `_render_page(pdf, page_index, dest)` 在测试里替换成写一个空 png 的假函数，避免测试依赖中文字体。

- [ ] **Step 2: 跑测试，确认失败**

```powershell
cd makeitdown
uv run --extra dev python -m pytest tests/test_pdf_reader.py -q
```

Expected: FAIL，`pdf_reader` 不存在。

- [ ] **Step 3: 实现**

`ConversionResult` 增加 `notices: list[str] | None = None`。`pipeline.handle` 里：

```python
notice_reasons = result.notices or []
reasons = notice_reasons + struct_reasons + cc_reasons + _quality_reasons(result, source_type)
```

`pdf_reader.py`：

- `_extract_pages(path)`：函数内部 `import pdf_inspector`，调用 `pdf_inspector.extract_pages_markdown(str(path))`。`ImportError` 或其他异常由 `read_pdf` 捕获并返回 `None`。
- `_render_page`：PyMuPDF 打开 PDF，`load_page(page_index)`（0 起），`get_pixmap(dpi=150)` 写到 `dest`。调用方用临时文件。
- 全部 `needs_ocr` 或 markdown 去空白后为空 → 返回 `None`。
- 可信页用 `page.markdown.strip()`。不可信页调用 dispatcher。dispatcher 抛 `OCRUnavailableError` 或 `ConversionUnavailable` 时该页用 `""`，`notices` 追加 `pdf page {n} needs OCR; local engine unavailable and cloud consent is off`，其中 `{n}` 是 1 起页码。有云端同意时仍由现有 dispatcher 决定能否上传；本函数不新增上传。
- `join_pages(parts)` 得到正文。`pages=len(parts)`。
- 没有任何页走了 OCR：`engine="pdf-inspector"`。有 OCR：`engine="pdf-inspector+" + ocr_result.engine`。多页 OCR 引擎不同时用第一页成功的引擎标签，并在 notices 里记下其余标签。

不要把 notice 句子放进 `parts`。

- [ ] **Step 4: 跑测试**

```powershell
uv run --extra dev python -m pytest tests/test_pdf_reader.py tests/test_pipeline.py -q
```

Expected: PASS。现有 pipeline 测试不因新字段失败。

- [ ] **Step 5: Commit**

```powershell
git add makeitdown/src/makeitdown/pdf_reader.py makeitdown/src/makeitdown/models.py makeitdown/src/makeitdown/pipeline.py makeitdown/tests/test_pdf_reader.py
git commit -m "feat: add an optional per-page PDF reader with OCR fallback"
```

---

### Task 5: 把旁路接到 CLI、ingest 和能力契约

**Files:**
- Modify: `makeitdown/pyproject.toml`（`[project.optional-dependencies] pdf = ["pdf-inspector==1.24.0"]`）
- Modify: `makeitdown/src/makeitdown/cli.py`
- Modify: `makeitdown/src/makeitdown/pipeline.py` 的 `convert_tree` 签名与 `handle`
- Modify: `lawiki/ingest.py` 的 `build_convert_argv`、`_run_convert`、argparse
- Modify: `lawiki/skill/lawiki/capabilities.json`
- Test: `makeitdown/tests/test_cli.py` 或 pipeline 调用测试
- Test: `lawiki/test_ingest.py`、`lawiki/test_capability_contract.py`

**Interfaces:**
- Consumes: `read_pdf`，Task 4
- Produces: `convert_tree(..., pdf_reader_inspector: bool = False)`。CLI `--pdf-reader-inspector`。ingest 同名标志，为真时向 makeitdown 追加该标志。能力 id `pdf_reader_inspector`。

- [ ] **Step 1: 写失败测试**

makeitdown：`convert_tree` 在 `pdf_reader_inspector=True` 且 `read_pdf` 返回一份带 `page_map="native"` 的结果时，不调用 `convert_native`。`read_pdf` 返回 `None` 时仍调用今天的 native/ocr 分支。PDF 以外的文件即使开关打开也不调用 `read_pdf`。

lawiki：

```python
argv = build_convert_argv(..., pdf_reader_inspector=True)
assert "--pdf-reader-inspector" in argv
argv = build_convert_argv(...)  # 默认
assert "--pdf-reader-inspector" not in argv
```

`ingest._build_parser().parse_args(["/case", "--pdf-reader-inspector"])` 的对应属性为 True。

`capabilities.json` 增加 CHOICE 后，`test_capability_contract.py` 会要求 `build_ingest_argv({"pdf_reader_inspector": True})` 含 `--pdf-reader-inspector`。

- [ ] **Step 2: 跑测试，确认失败**

```powershell
cd makeitdown
uv run --extra dev python -m pytest tests/test_pipeline.py -q -k pdf_reader
cd ..\lawiki
python -m pytest test_ingest.py test_capability_contract.py -q -k "pdf_reader or capability"
```

Expected: FAIL，参数不存在。

- [ ] **Step 3: 实现**

`convert_tree` 增加关键字参数 `pdf_reader_inspector: bool = False`，默认 False。在 `handle` 里，`route = classify(...)` 之后、按 route 分派之前：

```python
if pdf_reader_inspector and src.suffix.lower() == ".pdf":
    inspected = read_pdf(src, dispatcher=dispatcher, cloud_consent=cloud_consent)
    if inspected is not None:
        result = inspected
        # 跳过后面的 native/ocr 分派，继续走写盘、质检、layout_tree
```

`read_pdf` 返回 `None` 时什么都不做，落到现有 `if route == "native"`。

CLI 增加 `--pdf-reader-inspector`，`action="store_true"`，传给 `convert_tree`。

`capabilities.json` 的 `page_map` enforcement 补一句：默认路径不变；显式 `--pdf-reader-inspector` 且读取成功时，正文来自逐页提取，页标记由 `join_pages` 写出。新增：

```json
{
  "id": "pdf_reader_inspector",
  "promise": "显式打开时，PDF 按页读取文字层；不可信页走现有 OCR；未安装或失败则退回 MarkItDown",
  "owner": "makeitdown",
  "tier": "CHOICE",
  "phase": "ingest",
  "tradeoff": "阅读顺序和页边界更好，但正文与 MarkItDown 不再逐字节相同。默认关。",
  "enforcement": "ingest 仅在勾选时向 makeitdown 传递 --pdf-reader-inspector；makeitdown 未安装 extra 或抛错时退回原路径",
  "sanctioned": {
    "ingest_flag": "--pdf-reader-inspector",
    "gui_control": "pdf_reader_inspector",
    "default_on": false
  }
}
```

`build_convert_argv` 增加 `pdf_reader_inspector: bool = False`，为真时 `argv.append("--pdf-reader-inspector")`。`_run_convert` 和 `main` 把 argparse 的值传下去。GUI 不用新控件代码：`build_ingest_argv` 已从契约拼标志。ingest 的 main 若目前只从 argparse 取 `layout_tree`，要同样读取 `pdf_reader_inspector` 并传入 `_run_convert`。不要只让 GUI 拼出标志、却在 `ingest.main` 里丢掉。

- [ ] **Step 4: 跑测试**

```powershell
cd makeitdown
uv run --extra dev python -m pytest tests -q
cd ..\lawiki
python -m pytest skill/lawiki scripts test_install.py test_ingest.py test_ingest_gui.py test_capability_contract.py -q
```

Expected: PASS。makeitdown 全量这时还没装 `[pdf]`，mock 测试不导入 pdf-inspector。

- [ ] **Step 5: Commit**

```powershell
git add makeitdown/pyproject.toml makeitdown/src/makeitdown/cli.py makeitdown/src/makeitdown/pipeline.py lawiki/ingest.py lawiki/skill/lawiki/capabilities.json makeitdown/tests lawiki/test_ingest.py
git commit -m "feat: gate per-page PDF reading behind an explicit ingest flag"
```

---

### Task 6: 用真实 wheel 钉住金额和页标记

**前置:** Task 0 已通过。

**Files:**
- Test: `makeitdown/tests/test_pdf_reader_wheel.py`
- Modify: `.github/workflows/test.yml` 的 makeitdown 步骤，在 `uv run` 上增加 `--extra pdf`

**Interfaces:**
- Consumes: 已发布的 `pdf_inspector.extract_pages_markdown`，`read_pdf`

- [ ] **Step 1: 写测试**

用 PyMuPDF 写两页，只使用 Helvetica 能编码的字符：

```python
import fitz

def _two_page_pdf(path):
    doc = fitz.open()
    for text in ("Article 3 Amount 50,000.00", "Schedule B dated 2024-03-15"):
        page = doc.new_page()
        page.insert_text((72, 72), text, fontsize=12, fontname="helv")
    doc.save(path)
    doc.close()
```

`pytest.importorskip("pdf_inspector")`。调用 `read_pdf(path, dispatcher=不允许被调用的假对象, cloud_consent=False)`。

断言：

- 返回值不是 `None`
- `"50,000.00" in result.text`
- `"2024-03-15" in result.text`
- `result.text.index("<!-- page: 1 -->") < result.text.index("50,000.00")`
- `result.text.index("<!-- page: 2 -->") < result.text.index("2024-03-15")`
- `result.page_map == "native"`
- `result.engine == "pdf-inspector"`
- 正文不含 `<!-- Page`

若真实包把其中一页标成 `needs_ocr` 并清空，测试失败。不要在测试里改断言去迁就空页。那说明这个版本不能当文字层阅读器，回到 Task 0 的停止条件。

- [ ] **Step 2: 本地安装 extra 后跑**

```powershell
cd makeitdown
uv run --extra dev --extra pdf python -m pytest tests/test_pdf_reader_wheel.py -q
```

Expected: PASS。

- [ ] **Step 3: CI**

`.github/workflows/test.yml` 的 makeitdown 命令从：

```text
uv run --python 3.12 --extra dev --with pytest-cov python -m pytest tests -q
```

改为同时带 `--extra pdf`。Ubuntu 与 Windows 都要跑到这个测试。

- [ ] **Step 4: Commit**

```powershell
git add makeitdown/tests/test_pdf_reader_wheel.py .github/workflows/test.yml
git commit -m "test: pin per-page PDF reading against the pdf-inspector wheel"
```

---

### Task 7: 文档与旧规格指针

**Files:**
- Modify: `makeitdown/README.md`
- Modify: `makeitdown/skill/makeitdown/SKILL.md`
- Modify: `rag-retriever/README.md`（重排清洗、`RAG_QUERY_EXPAND`）
- Modify: `CHANGELOG.md` 未发布节
- Modify: `docs/superpowers/specs/2026-09-20-structure-navigation-design.md` 文首

- [ ] **Step 1: 写明三件事**

makeitdown 文档：

- 默认 PDF 仍是 MarkItDown；平均字数路由和 `align_page_markers` 不变。
- `--pdf-reader-inspector` 需要 `pip install "makeitdown[pdf]"`。成功时按页写 `<!-- page: N -->`。未安装或抛错则退回原路径，转换不失败。
- 名叫 `.docx` 但文件头是 OLE 的文件走 legacy。

rag 文档：

- 本地 rerank 打分前去掉 Markdown 标记，返回文本仍是分块原文。
- `RAG_QUERY_EXPAND` 默认关。打开后只在全文命中少于 `k` 时追加内容词查询。

`CHANGELOG.md` 用与现有条目相同的语气各写一条，不写版本号。

`2026-09-20` 文首、标题下追加一段：

```markdown
旁路（2026-09-24 草案落地后）：显式 `--pdf-reader-inspector` 且读取成功时，文字层正文改为逐页提取，页标记由 `join_pages` 写出。默认路径仍是本段下面的 MarkItDown 规则。见 `docs/superpowers/specs/2026-09-24-pdf-and-retrieval-hygiene-design.md`。
```

若本方案尚未获准，不要先改 2026-09-20。这一步只在 Task 4–6 已经落地时做。

- [ ] **Step 2: 校对**

```powershell
git diff --check
```

Expected: 无空白错误。通读新规格第 6 节，确认每一条都有对应测试名。

- [ ] **Step 3: Commit**

```powershell
git add makeitdown/README.md makeitdown/skill/makeitdown/SKILL.md rag-retriever/README.md CHANGELOG.md docs/superpowers/specs/2026-09-20-structure-navigation-design.md
git commit -m "docs: describe the optional per-page PDF reader and retrieval hygiene"
```

---

## Self-review

| 规格条目 | 任务 |
|---|---|
| 逐页读取、空掉不可信页、混合 OCR、整份扫描退回 | Task 4 |
| 未安装或抛错退回 MarkItDown | Task 4、Task 5 |
| 页标记用 `join_pages` | Task 4 |
| 默认字节不变 | Task 5 的默认 False 与现有测试 |
| 真实金额与页序 | Task 6 |
| OLE 改道 | Task 1 |
| 重排清洗且不改返回原文 | Task 2 |
| 无模型词面扩展，默认关，`local` 算打开 | Task 3 |
| 能力契约与 ingest 透传 | Task 3、Task 5 |
| 不使用对方 OCR / 托管回退 | Task 4 的实现禁止调用 `process_pdf_with_ocr` |
| 不改 rag `extract.py` | 无任务触碰该文件 |
| 旧结构导航规格只加指针 | Task 7 |
