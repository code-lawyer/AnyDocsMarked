# anydoc 老式二进制 Office 转换 —— 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 makeitdown 用 firecrawl-anydoc 进程内转换老式二进制 Office：`.doc/.wps` 在 COM/LibreOffice 之后加 anydoc 兜底,`.ppt/.xls/.xlsb` 从 unsupported 改为 anydoc 直转。

**Architecture:** 新增薄壳 `convert_anydoc.py` 调 `anydoc.to_markdown` 并把 anydoc 类型化异常映射为 `ConversionUnavailable`;router 增 `anydoc` 路由;`convert_legacy` 的 ole2 分支追加 anydoc 兜底;pipeline 增一路分派并把跳过异常泛化。native/ocr/frontmatter/quality 零改动。

**Tech Stack:** Python ≥3.11、firecrawl-anydoc(Rust,`import anydoc`)、pytest、既有 markitdown/pymupdf 依赖。

## Global Constraints

- Python 要求 `>=3.11`(makeitdown 现状);firecrawl-anydoc 要求 `>=3.10`,兼容。
- `firecrawl-anydoc` 加入 `[project].dependencies`(**基础硬依赖**,非可选)。
- **禁止改动** `convert_native.py`(markitdown 分支)、OCR 分支、`frontmatter.py`、`quality.py` 的行为。
- 引擎标签精确:`.ppt/.xls/.xlsb` 直转 → `anydoc`;`.doc/.wps` 经兜底 → `legacy:anydoc`。
- 失败即优雅降级:anydoc 已知异常 → `ConversionUnavailable`(pipeline 记 `skipped_unsupported` + 提示,落 `report["skipped"]`);未知异常照常上抛记 `failed`。绝不中断整批。
- 离线包零改动(anydoc 与 markitdown/pymupdf 同为 install 时由 uv 从镜像拉取)。
- 每个转换器统一返回 `ConversionResult(text, engine, pages=None, assets={}, confidences=None, cross_check_reasons=None)`。
- 测试用相对导入包名 `makeitdown.*`;`pytest` 在 `makeitdown/` 目录下跑(`pythonpath=["src"]`)。

---

### Task 0: SPIKE —— 验证 anydoc 对真 OLE2 二进制的转换质量(决策门)

**目的:** 本功能全部价值系于 anydoc 能否可用地转真二进制 `.doc/.ppt/.xls`。此前只验证过 OOXML(`.docx/.xlsx`)。此为 go/no-go 门,**不写产品代码**。

**Files:**
- Create(临时,不提交):scratchpad 下 `spike_anydoc.py`

- [ ] **Step 1: 在隔离 venv 装 anydoc + xlwt**

```bash
python -m venv /tmp/spikeenv && /tmp/spikeenv/Scripts/python -m pip install \
  firecrawl-anydoc xlwt -i https://mirrors.aliyun.com/pypi/simple
```

- [ ] **Step 2: 造一个真 OLE2 `.xls`(xlwt 写 BIFF/OLE2)并喂 anydoc**

```python
import xlwt, anydoc
wb = xlwt.Workbook(); ws = wb.add_sheet("流水")
rows = [["日期","摘要","金额"],["2024-03-15","货款","1,234,567.89"],["2024-06-30","退款","493,827.16"]]
for r,row in enumerate(rows):
    for c,v in enumerate(row): ws.write(r,c,v)
wb.save("/tmp/ledger.xls")
print(anydoc.to_markdown("/tmp/ledger.xls"))
# 断言金额/日期逐字出现;确认输出是合理的 GFM 表格
```

- [ ] **Step 3: 确认 anydoc 异常类可实例化**(测试要 `raise` 它们)

```python
for name in ["ConvertError","EncryptedError","MalformedError","MissingPartError","ResourceLimitError","UnsupportedError"]:
    cls = getattr(anydoc, name)
    try: raise cls("x")
    except Exception as e: print(name, "OK", type(e).__name__)
```

- [ ] **Step 4:(可取到样本时)验证真 `.doc` / `.ppt`**

若机器有 LibreOffice:`soffice --headless --convert-to doc /tmp/any.docx --outdir /tmp` 造真 `.doc`,再 `anydoc.to_markdown`。取不到样本则记录"「.doc/.ppt 真机验证」留待 Task 6 用committed fixture 补",不阻塞。

- [ ] **Step 5: 决策门(记录结论到本计划或 spec 末尾)**

- `.xls`(及能验的 `.doc/.ppt`)金额/日期可用 → 全量执行 Task 1–6。
- `.doc` 保真差、`.ppt/.xls` 可用 → 仍执行,但 Task 4 的 `.doc` 兜底标注"验证受限",在 README 中说明 `.doc` 走 anydoc 为尽力而为。
- 全部不可用(极小概率)→ 停止,回到 spec 重议(不应发生:anydoc 明确宣称支持这些格式)。

> 无 commit。这是投研门。若某异常类无法 `raise cls("x")`,在 Task 2 测试里改用 `cls()` 无参构造。

---

### Task 1: 新增 `ConversionUnavailable` 基类 + 加入 anydoc 依赖

**Files:**
- Modify: `makeitdown/src/makeitdown/models.py`
- Modify: `makeitdown/pyproject.toml`(`dependencies` 加 `firecrawl-anydoc`)
- Test: `makeitdown/tests/test_models.py`

**Interfaces:**
- Produces: `ConversionUnavailable(RuntimeError)`;`LegacyConversionUnavailable(ConversionUnavailable)`(保留原名,改父类)。

- [ ] **Step 1: 写失败测试**

`makeitdown/tests/test_models.py` 追加:

```python
from makeitdown.models import ConversionResult, ConversionUnavailable, LegacyConversionUnavailable


def test_legacy_unavailable_is_conversion_unavailable():
    assert issubclass(LegacyConversionUnavailable, ConversionUnavailable)
    assert issubclass(ConversionUnavailable, RuntimeError)


def test_conversion_result_defaults():
    r = ConversionResult(text="x", engine="anydoc")
    assert r.pages is None and r.assets == {} and r.confidences is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd makeitdown && python -m pytest tests/test_models.py -q`
Expected: FAIL — `ImportError: cannot import name 'ConversionUnavailable'`

- [ ] **Step 3: 改 `models.py`**

把现有 `LegacyConversionUnavailable` 定义替换为:

```python
class ConversionUnavailable(RuntimeError):
    """文件可识别但当前后端无法转换(无可用转换器 / 加密 / 损坏)。
    携带可执行提示;pipeline 将其转为"知情跳过"而非失败。"""


class LegacyConversionUnavailable(ConversionUnavailable):
    """老式二进制(.doc/.wps)无 COM/LibreOffice/anydoc 后端可用。带可执行提示。"""
```

- [ ] **Step 4: `pyproject.toml` 加依赖**

`[project].dependencies` 列表加一行 `"firecrawl-anydoc",`(置于 `"markitdown[all]",` 之后)。

- [ ] **Step 5: 装依赖并确认可导入**

Run: `cd makeitdown && python -m pip install -e . -i https://mirrors.aliyun.com/pypi/simple && python -c "import anydoc; print(anydoc.to_markdown.__name__)"`
Expected: 打印 `to_markdown`

- [ ] **Step 6: 跑测试确认通过**

Run: `cd makeitdown && python -m pytest tests/test_models.py -q`
Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add makeitdown/src/makeitdown/models.py makeitdown/pyproject.toml makeitdown/tests/test_models.py
git commit -m "feat(makeitdown): add ConversionUnavailable base + firecrawl-anydoc dep"
```

---

### Task 2: `convert_anydoc.py` 转换薄壳

**Files:**
- Create: `makeitdown/src/makeitdown/convert_anydoc.py`
- Test: `makeitdown/tests/test_convert_anydoc.py`

**Interfaces:**
- Consumes: `anydoc.to_markdown(str) -> str`;`anydoc.{Encrypted,Unsupported,Malformed,MissingPart,ResourceLimit,Convert}Error`;`ConversionResult`、`ConversionUnavailable`(Task 1)。
- Produces: `convert(path: Path) -> ConversionResult`(`engine="anydoc"`, `pages=None`),失败抛 `ConversionUnavailable`。

- [ ] **Step 1: 写失败测试**

`makeitdown/tests/test_convert_anydoc.py`:

```python
import anydoc
import pytest

from makeitdown import convert_anydoc
from makeitdown.models import ConversionResult, ConversionUnavailable


def test_success_returns_anydoc_result(monkeypatch):
    monkeypatch.setattr(anydoc, "to_markdown", lambda p: "# 判决书\n金额 1,234,567.89")
    r = convert_anydoc.convert("case.xls")
    assert isinstance(r, ConversionResult)
    assert r.engine == "anydoc"
    assert r.pages is None
    assert "1,234,567.89" in r.text


@pytest.mark.parametrize("exc_name", [
    "UnsupportedError", "MalformedError", "MissingPartError",
    "ResourceLimitError", "ConvertError",
])
def test_typed_errors_become_unavailable(monkeypatch, exc_name):
    exc = getattr(anydoc, exc_name)

    def boom(p):
        raise exc("boom")

    monkeypatch.setattr(anydoc, "to_markdown", boom)
    with pytest.raises(ConversionUnavailable):
        convert_anydoc.convert("case.ppt")


def test_encrypted_gives_password_hint(monkeypatch):
    def boom(p):
        raise anydoc.EncryptedError("enc")

    monkeypatch.setattr(anydoc, "to_markdown", boom)
    with pytest.raises(ConversionUnavailable, match="加密"):
        convert_anydoc.convert("case.doc")
```

> 若 Task 0 Step 3 发现某异常类不能 `raise cls("boom")`,把 `boom` 改为 `raise exc()`。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd makeitdown && python -m pytest tests/test_convert_anydoc.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'makeitdown.convert_anydoc'`

- [ ] **Step 3: 写实现**

`makeitdown/src/makeitdown/convert_anydoc.py`:

```python
from pathlib import Path

import anydoc

from .models import ConversionResult, ConversionUnavailable

_ENGINE = "anydoc"


def convert(path: Path) -> ConversionResult:
    """用 anydoc 的进程内 Rust 解析器把老式二进制 Office(.doc/.ppt/.xls/.xlsb)
    转 Markdown,无需任何外部程序。

    anydoc 的类型化失败 → ConversionUnavailable(可识别但此处不可转,带提示,
    由 pipeline 记为知情跳过)。未知异常照常上抛,由 pipeline 记 failed。"""
    path = Path(path)
    try:
        text = anydoc.to_markdown(str(path))
    except anydoc.EncryptedError as e:
        raise ConversionUnavailable(
            f"{path.suffix or '文件'} 已加密,需先用密码解密再转换 —— 跳过。"
        ) from e
    except (
        anydoc.UnsupportedError,
        anydoc.MalformedError,
        anydoc.MissingPartError,
        anydoc.ResourceLimitError,
        anydoc.ConvertError,
    ) as e:
        raise ConversionUnavailable(
            f"anydoc 无法解析 {path.suffix or '该文件'}({type(e).__name__})—— 跳过。"
        ) from e
    return ConversionResult(text=text, engine=_ENGINE, pages=None)
```

> `EncryptedError` 单列在前以给专属提示(即便它继承自 `ConvertError`,先匹配也正确)。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd makeitdown && python -m pytest tests/test_convert_anydoc.py -q`
Expected: PASS(全部参数化用例)

- [ ] **Step 5: 提交**

```bash
git add makeitdown/src/makeitdown/convert_anydoc.py makeitdown/tests/test_convert_anydoc.py
git commit -m "feat(makeitdown): convert_anydoc wrapper mapping typed errors to ConversionUnavailable"
```

---

### Task 3: router 增加 `anydoc` 路由

**Files:**
- Modify: `makeitdown/src/makeitdown/router.py`
- Test: `makeitdown/tests/test_router.py`

**Interfaces:**
- Produces: `classify()` 对 `.ppt/.xls/.xlsb` 返回 `"anydoc"`;`.doc/.wps` 仍 `"legacy"`。

- [ ] **Step 1: 写失败测试**

`makeitdown/tests/test_router.py` 追加:

```python
import pytest

from makeitdown.router import classify


@pytest.mark.parametrize("name,expected", [
    ("a.ppt", "anydoc"), ("a.xls", "anydoc"), ("a.xlsb", "anydoc"),
    ("a.doc", "legacy"), ("a.wps", "legacy"),
    ("a.PPT", "anydoc"), ("a.Xls", "anydoc"),  # 扩展名大小写不敏感
])
def test_anydoc_and_legacy_routing(tmp_path, name, expected):
    p = tmp_path / name
    p.write_bytes(b"")  # 非 PDF 路由仅看扩展名,内容无关
    assert classify(p) == expected
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd makeitdown && python -m pytest tests/test_router.py -q`
Expected: FAIL — `.ppt/.xls/.xlsb` 目前返回 `"unsupported"`

- [ ] **Step 3: 写实现**

`router.py`:在 `LEGACY_BINARY_EXTS` 定义后加:

```python
# 老式二进制 Office,markitdown 读不了、也无既有转换路径 —— 交给 anydoc 直转。
ANYDOC_EXTS = {".ppt", ".xls", ".xlsb"}
```

在 `classify()` 里 `if ext in LEGACY_BINARY_EXTS: return "legacy"` 之后、`return "unsupported"` 之前加:

```python
    if ext in ANYDOC_EXTS:
        return "anydoc"
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd makeitdown && python -m pytest tests/test_router.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add makeitdown/src/makeitdown/router.py makeitdown/tests/test_router.py
git commit -m "feat(makeitdown): route .ppt/.xls/.xlsb to anydoc"
```

---

### Task 4: `convert_legacy` 的 ole2 分支追加 anydoc 兜底

**Files:**
- Modify: `makeitdown/src/makeitdown/convert_legacy.py`(imports + `convert()` ole2 分支)
- Test: `makeitdown/tests/test_convert_legacy.py`

**Interfaces:**
- Consumes: `convert_anydoc.convert`(Task 2)、`ConversionUnavailable`(Task 1)。
- Produces: ole2 且 COM/LO 失败但 anydoc 成功 → `ConversionResult(engine="legacy:anydoc")`;三者皆败 → `LegacyConversionUnavailable`。

- [ ] **Step 1: 写失败测试**

`makeitdown/tests/test_convert_legacy.py` 追加:

```python
import pytest

from makeitdown import convert_legacy
from makeitdown.models import (
    ConversionResult, ConversionUnavailable, LegacyConversionUnavailable,
)

_OLE2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1rest"


def _make_ole_doc(tmp_path):
    src = tmp_path / "old.doc"
    src.write_bytes(_OLE2)
    return src


def test_ole2_falls_back_to_anydoc(monkeypatch, tmp_path):
    src = _make_ole_doc(tmp_path)
    monkeypatch.setattr(convert_legacy, "_convert_via_com", lambda s, o: False)
    monkeypatch.setattr(convert_legacy, "_convert_via_libreoffice", lambda s, d: None)
    monkeypatch.setattr(convert_legacy, "convert_anydoc",
                        lambda p: ConversionResult(text="# 判决书", engine="anydoc"))
    r = convert_legacy.convert(src)
    assert r.engine == "legacy:anydoc"
    assert "判决书" in r.text


def test_ole2_all_backends_fail_raises_legacy(monkeypatch, tmp_path):
    src = _make_ole_doc(tmp_path)
    monkeypatch.setattr(convert_legacy, "_convert_via_com", lambda s, o: False)
    monkeypatch.setattr(convert_legacy, "_convert_via_libreoffice", lambda s, d: None)

    def boom(p):
        raise ConversionUnavailable("anydoc 读不了")

    monkeypatch.setattr(convert_legacy, "convert_anydoc", boom)
    with pytest.raises(LegacyConversionUnavailable):
        convert_legacy.convert(src)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd makeitdown && python -m pytest tests/test_convert_legacy.py -q -k anydoc or all_backends`
Expected: FAIL — `convert_legacy` 无 `convert_anydoc` 属性 / 无兜底逻辑

- [ ] **Step 3: 写实现**

`convert_legacy.py` 顶部 import 区加:

```python
from .convert_anydoc import convert as convert_anydoc
from .models import ConversionResult, ConversionUnavailable, LegacyConversionUnavailable
```

(合并到现有 `from .models import ...` 一行亦可;确保 `ConversionUnavailable` 在列。)

`convert()` 的 `if kind == "ole2":` 块,在 LibreOffice 分支之后、`raise` 之前插入:

```python
            # 最后兜底:anydoc 直接读 OLE 二进制,无需任何外部程序。
            try:
                return _relabel(convert_anydoc(src), "legacy:anydoc")
            except ConversionUnavailable:
                pass
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd makeitdown && python -m pytest tests/test_convert_legacy.py -q`
Expected: PASS(新增两例 + 原有用例不回归)

- [ ] **Step 5: 提交**

```bash
git add makeitdown/src/makeitdown/convert_legacy.py makeitdown/tests/test_convert_legacy.py
git commit -m "feat(makeitdown): anydoc last-resort for ole2 .doc/.wps in convert_legacy"
```

---

### Task 5: pipeline 分派 `anydoc` 路由 + 泛化跳过异常

**Files:**
- Modify: `makeitdown/src/makeitdown/pipeline.py`(imports、`handle()` 分派、except 子句)
- Test: `makeitdown/tests/test_pipeline.py`

**Interfaces:**
- Consumes: `convert_anydoc.convert`(Task 2)、`ConversionUnavailable`(Task 1)、`classify` 的 `"anydoc"`(Task 3)。
- Produces: `route == "anydoc"` 走 `convert_anydoc`;`ConversionUnavailable` → `skipped_unsupported` + 提示落 `report["skipped"]`。

- [ ] **Step 1: 写失败测试**

`makeitdown/tests/test_pipeline.py` 追加(用 monkeypatch 隔离 anydoc,聚焦分派与跳过语义;真二进制往返在 Task 6):

```python
from makeitdown import convert_anydoc, pipeline
from makeitdown.models import ConversionResult, ConversionUnavailable


def test_pipeline_routes_xls_to_anydoc(monkeypatch, tmp_path):
    (tmp_path / "in").mkdir()
    (tmp_path / "in" / "ledger.xls").write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1x")
    monkeypatch.setattr(convert_anydoc, "convert",
                        lambda p: ConversionResult(text="# 流水\n100000.00", engine="anydoc"))
    report = pipeline.convert_tree(
        tmp_path / "in", tmp_path / "out",
        ocr_engine="local", ocr_model="", cloud_token=None, workers=1,
        skip_existing=False, text_threshold=50, report_path=tmp_path / "report.json",
        progress=False,
    )
    assert report["succeeded"] + report["warned"] == 1
    md = (tmp_path / "out" / "ledger.md").read_text(encoding="utf-8")
    assert "engine: anydoc" in md
    assert "流水" in md


def test_pipeline_anydoc_unconvertible_is_skipped(monkeypatch, tmp_path):
    (tmp_path / "in").mkdir()
    (tmp_path / "in" / "deck.ppt").write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1x")

    def boom(p):
        raise ConversionUnavailable("anydoc 无法解析 .ppt —— 跳过。")

    monkeypatch.setattr(convert_anydoc, "convert", boom)
    report = pipeline.convert_tree(
        tmp_path / "in", tmp_path / "out",
        ocr_engine="local", ocr_model="", cloud_token=None, workers=1,
        skip_existing=False, text_threshold=50, report_path=tmp_path / "report.json",
        progress=False,
    )
    assert report["skipped_unsupported"] == 1
    assert report["skipped"] and "无法解析" in report["skipped"][0]["reason"]
```

> `convert_tree` 里 `handle()` 用的是模块级导入的 `convert_anydoc`(见 Step 3),故 `monkeypatch.setattr(convert_anydoc, "convert", ...)` 生效。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd makeitdown && python -m pytest tests/test_pipeline.py -q -k anydoc`
Expected: FAIL — 无 `anydoc` 分派;`.ppt/.xls` 目前会走 `skipped_unsupported`(unsupported 类型),`.xls` 不会产出 md

- [ ] **Step 3: 写实现**

`pipeline.py` import 区:
- 加 `from .convert_anydoc import convert as convert_anydoc`
- 把 `from .models import LegacyConversionUnavailable` 改为 `from .models import ConversionUnavailable`

`handle()` 分派链,在 `elif route == "legacy":` 之后、`else:`(OCR)之前插:

```python
            elif route == "anydoc":
                result = convert_anydoc(src)
```

`handle()` 的异常捕获,把:

```python
        except LegacyConversionUnavailable as e:
```

改为:

```python
        except ConversionUnavailable as e:
```

(`LegacyConversionUnavailable` 是其子类,`.doc/.wps` 路径仍被正确捕获。)

- [ ] **Step 4: 跑测试确认通过**

Run: `cd makeitdown && python -m pytest tests/test_pipeline.py -q`
Expected: PASS(新增两例 + 原有不回归)

- [ ] **Step 5: 全套回归**

Run: `cd makeitdown && python -m pytest -q`
Expected: 全绿

- [ ] **Step 6: 提交**

```bash
git add makeitdown/src/makeitdown/pipeline.py makeitdown/tests/test_pipeline.py
git commit -m "feat(makeitdown): dispatch anydoc route and generalize skip to ConversionUnavailable"
```

---

### Task 6: 真二进制 fixture 端到端 + README

**Files:**
- Create: `makeitdown/tests/fixtures/ledger.xls`(真 OLE2/BIFF,一次性用 xlwt 生成后**提交二进制**)
- Create: `makeitdown/tests/gen_xls_fixture.py`(生成脚本,便于复现)
- Test: `makeitdown/tests/test_pipeline.py`(追加真往返用例)
- Modify: `makeitdown/README.md`

**Interfaces:**
- Consumes: 真实 `anydoc.to_markdown`(不 mock),经 `convert_tree` 全链。

- [ ] **Step 1: 写生成脚本并生成 fixture**

`makeitdown/tests/gen_xls_fixture.py`:

```python
"""一次性生成真 OLE2 .xls 测试样本(dev 用:pip install xlwt)。"""
from pathlib import Path

import xlwt

wb = xlwt.Workbook()
ws = wb.add_sheet("流水")
rows = [
    ["日期", "摘要", "金额"],
    ["2024-03-15", "货款", "1,234,567.89"],
    ["2024-06-30", "退款", "493,827.16"],
]
for r, row in enumerate(rows):
    for c, val in enumerate(row):
        ws.write(r, c, val)
Path(__file__).parent.joinpath("fixtures").mkdir(exist_ok=True)
wb.save(str(Path(__file__).parent / "fixtures" / "ledger.xls"))
print("wrote fixtures/ledger.xls")
```

Run: `cd makeitdown && python -m pip install xlwt -i https://mirrors.aliyun.com/pypi/simple && python tests/gen_xls_fixture.py`
Expected: 写出 `tests/fixtures/ledger.xls`

- [ ] **Step 2: 写真往返测试(不 mock anydoc)**

`makeitdown/tests/test_pipeline.py` 追加:

```python
import shutil
from pathlib import Path


def test_real_xls_roundtrip_through_anydoc(tmp_path):
    fixture = Path(__file__).parent / "fixtures" / "ledger.xls"
    (tmp_path / "in").mkdir()
    shutil.copy(fixture, tmp_path / "in" / "ledger.xls")
    report = pipeline.convert_tree(
        tmp_path / "in", tmp_path / "out",
        ocr_engine="local", ocr_model="", cloud_token=None, workers=1,
        skip_existing=False, text_threshold=50, report_path=tmp_path / "report.json",
        progress=False,
    )
    assert report["failed"] == 0
    md = (tmp_path / "out" / "ledger.md").read_text(encoding="utf-8")
    assert "engine: anydoc" in md
    assert "1,234,567.89" in md  # 金额逐字保真(真 anydoc 转换)
```

- [ ] **Step 3: 跑真往返测试确认通过**

Run: `cd makeitdown && python -m pytest tests/test_pipeline.py::test_real_xls_roundtrip_through_anydoc -q`
Expected: PASS。若失败(金额未逐字出现),记录实际输出并回到 Task 0 决策门重估 `.xls` 可用性。

- [ ] **Step 4: 更新 README**

`makeitdown/README.md` 的"它做什么"路由清单中,把老式格式一条更新为:

```markdown
  - **老式二进制 Office**:`.doc/.wps` 先嗅探内核(实为 .docx 的直转),真二进制优先用已装的 Word/WPS 或 LibreOffice,**都没有则用内置 anydoc 进程内直转**;`.ppt/.xls/.xlsb` 直接由 anydoc 转换。加密件跳过并提示。
```

- [ ] **Step 5: 全套回归 + 提交**

Run: `cd makeitdown && python -m pytest -q && python -m ruff check .`
Expected: 全绿 + 无 lint 违规

```bash
git add makeitdown/tests/fixtures/ledger.xls makeitdown/tests/gen_xls_fixture.py \
        makeitdown/tests/test_pipeline.py makeitdown/README.md
git commit -m "test(makeitdown): real .xls anydoc roundtrip fixture + README update"
```

---

## Self-Review

**Spec 覆盖:**
- `.doc/.wps` 兜底 → Task 4 ✓;`.ppt/.xls/.xlsb` 直转 → Task 3(路由)+ Task 5(分派)✓
- 不替换 native → 全程未碰 `convert_native.py` ✓
- 异常映射 + 加密提示 → Task 2 ✓;跳过语义落 report → Task 5 ✓
- `ConversionUnavailable` 泛化 → Task 1(定义)+ Task 5(捕获)✓
- 硬依赖 + 无离线改动 → Task 1 Step 4/5 ✓(离线包不涉及)
- 引擎标签 `anydoc` / `legacy:anydoc` → Task 2 / Task 4 ✓
- 质检复用 → 未改 `quality.py`;真往返测试经 `convert_tree` 覆盖质检路径 ✓
- SPIKE 优先 → Task 0 ✓
- 真 fixture 往返 → Task 6 ✓

**占位扫描:** 无 TBD/TODO;每个 code step 含完整代码。

**类型一致性:** `convert(path)->ConversionResult`、`engine` 值、`ConversionUnavailable`/`LegacyConversionUnavailable` 名称在 Task 1/2/4/5 间一致;`convert_anydoc` 在 convert_legacy 与 pipeline 均以模块级名导入,monkeypatch 目标一致。

**已知裁量:** `.doc/.ppt` 真机 fixture 未强制committ(取样依赖 LibreOffice);Task 0/6 以 `.xls`(xlwt 可造)保证至少一条真往返,`.doc/.ppt` 真机验证在 Task 0 尽力而为并记录。
