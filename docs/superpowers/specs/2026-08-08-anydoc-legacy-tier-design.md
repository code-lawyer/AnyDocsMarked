# 设计:在 makeitdown 引入 anydoc(补老式二进制 Office 转换)

日期:2026-08-08
状态:已获设计批准,待写实现计划

## 背景与目标

makeitdown 当前的转换分工:

- `native`(markitdown):`.docx/.xlsx/.pptx/.html/.csv/.json/.xml/.txt/.md/.epub` + 有文字层 PDF
- `ocr`(Paddle/MinerU):扫描件、图片、无文字层 PDF
- `legacy`(`convert_legacy.py`):`.doc/.wps` —— sniff 内核,OOXML 直转;真 OLE2 靠 **COM(Word/WPS)→ LibreOffice**,都没有则**干净跳过**
- `unsupported`:其余(含 `.ppt/.xls/.xlsb`)→ 计入 `skipped_unsupported`

两个真实缺口:

1. **老式二进制 `.doc`**:律师电脑若没装 Word/WPS/LibreOffice 就转不出来,只能跳过。
2. **`.ppt/.xls/.xlsb`**:老律所档案常见,目前直接判 `unsupported`,无人处理。

[firecrawl-anydoc](https://pypi.org/project/firecrawl-anydoc/)(Rust,`import anydoc`,零运行时依赖,7 个 abi3 wheel 覆盖 Win/mac/Linux×x86_64/arm64,py≥3.10)能**进程内**原生读 OLE 二进制 Office,无需任何外部程序。本设计用它补上这两个缺口。

**非目标(明确排除):**

- 不替换 `native` 引擎 markitdown。前期实测已定论:anydoc 对文字层 PDF(CID 字体/无 ToUnicode)会整类拒转(`UnsupportedError`),且与我们基于 PyMuPDF 的文字层 router 硬冲突。见 [记忆:anydoc-vs-markitdown-eval]。
- 不为 `.ppt/.xls` 另建 COM/LibreOffice 兜底路径(YAGNI)。anydoc 直转失败即跳过+提示。
- 不碰 OCR、质检、frontmatter、SHA-256 逻辑——全部原样复用。

## 设计决策(已与用户敲定)

| 岔口 | 决定 |
|---|---|
| `.doc/.wps`(ole2)中 anydoc 的位置 | **兜底**:排在 COM、LibreOffice **之后**、跳过**之前**。只救本来会被跳过的文件,对已装 Word 的用户零行为变化、零回归。 |
| 引入格式范围 | **也救 `.ppt/.xls`(含 `.xlsb`)**:从 `unsupported` 改路由到 anydoc **直转**(它们无既有转换路径,anydoc 即其主转换器)。 |
| `.ppt/.xls` 失败 | 不加 LibreOffice 兜底;`ConversionUnavailable` → 跳过 + 提示。 |
| anydoc 依赖形态 | **基础硬依赖**(非可选)。零依赖、全平台 wheel,与 pymupdf/pillow 同级;避免"未装则跳过"的额外分支。 |

## 架构与组件

### 1. 新增 `src/makeitdown/convert_anydoc.py`(薄壳)

```
convert(path: Path) -> ConversionResult
    text = anydoc.to_markdown(str(path))
    return ConversionResult(text=text, engine="anydoc", pages=None)
  异常映射(anydoc 类型化异常 → ConversionUnavailable + 可执行提示):
    EncryptedError                         → "已加密,需密码后重转"
    UnsupportedError / MalformedError /
    ResourceLimitError / ConvertError      → "anydoc 无法解析该文件"
```

- `pages` 恒为 `None`(anydoc 不提供 Office 页数;与现有非 PDF 一致)。
- 只捕获 anydoc 已知异常类型;其余异常照常上抛,由 pipeline 记为 `failed`(不吞未知错误)。

### 2. `router.py`

```python
ANYDOC_EXTS = {".ppt", ".xls", ".xlsb"}
# classify(): if ext in ANYDOC_EXTS: return "anydoc"
```

`LEGACY_BINARY_EXTS = {.doc, .wps}` 不变(仍归 `"legacy"`)。

### 3. `convert_legacy.py`(ole2 分支加兜底)

```
kind == "ole2":
    COM(Word/WPS) 成功        → legacy:com->markitdown      (不变)
    LibreOffice 成功          → legacy:libreoffice->markitdown (不变)
    convert_anydoc 成功        → legacy:anydoc               (新增兜底)
    (anydoc 抛 ConversionUnavailable → 继续)
raise LegacyConversionUnavailable(_HINT)
```

引擎标签 `legacy:anydoc`,溯源可辨其经由 anydoc。

### 4. `models.py`

新增基类 `ConversionUnavailable(RuntimeError)`;`LegacyConversionUnavailable` 改为其子类(保留原名,pipeline 现有 catch 不破)。`convert_anydoc` 失败抛 `ConversionUnavailable`。

### 5. `pipeline.py`(分派 + 捕获)

```python
if route == "native":   result = convert_native(src)
elif route == "legacy": result = convert_legacy(src)
elif route == "anydoc": result = convert_anydoc(src)   # 新增
else:                   result = dispatcher.convert(src)  # ocr
...
except ConversionUnavailable as e:   # 由 LegacyConversionUnavailable 泛化而来
    return ("skipped_unsupported", rel, str(e), False, 0)
```

`.ppt/.xls/.xlsb` 直转失败与 `.doc` 兜底失败,统一落 `report["skipped"]` + 提示(供 lawiki `reconcile.py` 源级审计),绝不中断整批。成功产出照跑质检(空白/乱码标 `quality: suspect`)。

### 6. `pyproject.toml` + 离线包

- `dependencies` 加 `firecrawl-anydoc`。
- Release 离线包 vendor 对应平台 wheel(与现有 vendor ONNX 同机制);release 工作流机器校验 wheel 存在。

## 数据流

```
.ppt/.xls/.xlsb ─ router "anydoc" ─ convert_anydoc ─┐
.doc/.wps ─ router "legacy" ─ sniff ─ ooxml/COM/LO ─┤─成功─→ md + frontmatter(SHA/质检不变)
                                    └ anydoc 兜底 ───┘
任何 anydoc 读不了 / 加密 → ConversionUnavailable → report["skipped"] + 提示
```

## 错误处理

| 情形 | 行为 |
|---|---|
| 加密件 | 跳过,提示"已加密,需密码"(优于现在 COM 静默挂起) |
| anydoc 不支持/损坏 | 跳过,提示"anydoc 无法解析" |
| 未知异常 | `failed`(不吞) |
| 成功但可疑(空白/乱码) | 照过质检 → `quality: suspect` 随文件下游 |
| 整批 | 单文件失败不中断,汇总 `report.json` |

## 测试

- **`test_router.py`**:`.ppt/.xls/.xlsb` → `"anydoc"`;`.doc/.wps` 仍 `"legacy"`。
- **`test_convert_anydoc.py`**:monkeypatch `anydoc.to_markdown` 分别抛 5 类异常 → 断言 `ConversionUnavailable` + 提示文案;stub 成功 → 断言 `engine=="anydoc"`、正文透传、`pages is None`。
- **`test_convert_legacy.py`**:ole2 且 COM/LO 均不可用 → 命中 anydoc 兜底(monkeypatch)→ `legacy:anydoc`;anydoc 也抛 `ConversionUnavailable` → 最终 `LegacyConversionUnavailable`。
- **真 fixture 往返**:至少一个真二进制 `.xls`(可用 `xlwt` 现造 OLE2/BIFF)跑一次真实 anydoc 转换,断言金额/日期出现在输出。`.doc/.ppt` 若能取得小样本则一并committ 到 `tests/fixtures/`。
- **`test_pipeline.py`**:一例 `.xls` 走全链 → `succeeded`/`warned`,`_md` 带 frontmatter(`engine: anydoc`、双 SHA)。

## 风险与验证优先

**最大未知**:anydoc 对真 OLE2 `.doc/.ppt/.xls` 的实际转换质量尚未验证(前期只测过 OOXML `.docx/.xlsx`)。

**实现计划第一步必须是 SPIKE**:拿真二进制 `.doc/.ppt/.xls` 喂 `anydoc.to_markdown`,人工核金额/日期/表格是否可用。

- 若 `.ppt/.xls` 可用但 `.doc` 保真差 → 保留 `.ppt/.xls` 范围,`.doc` 兜底降级为可选/暂缓。
- 若全部可用 → 按本设计全量落地。

其余风险低:装包已实测(Win+阿里云镜像一条命令、零依赖 abi3 wheel);改动只碰 legacy/unsupported 两条冷路径,native/ocr/质检/frontmatter 零改;失败均优雅降级为跳过。
