# makeitdown

把一整个文件夹的文档**批量转换为高保真 Markdown**,作为 LLM 知识库的原材料。中国大陆可用,无需海外服务。

> 🤖 **完全不懂技术?** 去 [Releases](https://github.com/Tsinglaw/AnyDocsMarked/releases) 下载发布包,把整个文件夹交给你的 AI 助手,说"帮我用 makeitdown 把这个文件夹转成 markdown"即可——助手会自动安装并运行。详见包内 `给你的AI助手.md`。

基于 [markitdown](https://github.com/microsoft/markitdown)(原生格式)与 [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR)(扫描件/图片)。

## 它做什么

- 递归扫描输入目录,按文件类型自动路由:
  - **原生文档**(Word/Excel/PPT、HTML、csv/json/xml、txt/md、epub、**有文字层的 PDF**)→ markitdown
  - **扫描件 / 图片型 PDF / 图片** → PaddleOCR(PDF 用 PyMuPDF 检测文字层,每页平均字符低于阈值即判扫描件)
  - **老式二进制 Office**:`.doc/.wps` 先嗅探内核(实为 .docx 的直转),真二进制优先用已装的 Word/WPS 或 LibreOffice,**都没有则用内置 anydoc 进程内直转**;`.ppt/.xls/.xlsb` 直接由 anydoc 转换。加密件跳过并提示。
- 输出**镜像输入目录结构**的 `.md`,每个文件带 YAML frontmatter(来源、引擎、原件与正文 SHA-256 等),便于溯源与篡改检测。
- 单文件出错不中断整批,结果汇总到 `report.json`。

## 安装

需要 **Python 3.11 及以上**。先装 uv,再**二选一**:

```bash
pip install uv -i https://mirrors.aliyun.com/pypi/simple

# 本地版(离线、免费、私密;体积大、较慢)
uv tool install --index https://mirrors.aliyun.com/pypi/simple \
  "makeitdown[local] @ git+https://github.com/Tsinglaw/AnyDocsMarked.git#subdirectory=makeitdown"

# 云端版(轻快;需联网 + token,文档上传百度服务器)
uv tool install --index https://mirrors.aliyun.com/pypi/simple \
  "makeitdown @ git+https://github.com/Tsinglaw/AnyDocsMarked.git#subdirectory=makeitdown"
```

| | 本地版 | 云端版 |
|---|---|---|
| 联网 / token | 都不需要 | 需联网 + [百度 AI Studio](https://aistudio.baidu.com/paddleocr) token |
| 隐私 | 文档不出本机 | 文档上传百度 |
| 体积 / 速度 | 大、较慢 | 小、快 |

装完执行 `makeitdown --help` 验证(命令找不到时 `uv tool update-shell` 后开新终端)。**海外用户**去掉 `--index`/`-i` 即可;uv 若有问题可用纯 pip 后备。

## 使用

```bash
makeitdown <输入目录> -o <输出目录>
```

输出目录默认 `<输入>_md`,不得位于输入目录内部(避免重跑递归转换旧输出)。进度逐文件打到 stderr;批量大或走云端时建议后台运行,**完成以 `report.json` 为准**(`succeeded` 干净产出、`warned` 可疑产出、`failures` 硬失败、`skipped` 干净跳过)。中断后 `--skip-existing` 断点续传。

### OCR 后端:云端默认 + 显式同意

默认走**云端 OCR**(开箱即用),但**绝不静默上传**:

- 同意上云:设 `PADDLEOCR_AISTUDIO_TOKEN` 并加 `--cloud-consent`(或 `MAKEITDOWN_CLOUD_CONSENT=1`)。
- 不上传:加 `--ocr-engine local`(需本地版),文档不出本机。

**双 OCR 互校**(`--ocr-cross-check`,法律高危件):Paddle + MinerU 两独立引擎比对同一页,分歧(尤其金额/日期)标 `quality: suspect` 随文件进入下游,失败也绝不丢结果。校验方用 `--cross-check-mode {cloud,local,auto}` 选。

### 常用选项

| 选项 | 说明 |
|---|---|
| `-o, --output DIR` | 输出目录(默认 `<输入>_md`) |
| `--ocr-engine {local,cloud,auto}` | OCR 后端(默认 `cloud`) |
| `--cloud-consent` | 显式同意把文档/文本发往外部 OCR 或标题 LLM |
| `--ocr-cross-check` / `--cross-check-mode` | 双 OCR 互校及其校验引擎 |
| `--structure-headings` | 用 LLM 为 OCR 产物重建标题层级(见下,默认关) |
| `--workers N` | 并发数(默认按 CPU 核数) |
| `--skip-existing` | 输出比源新则跳过(轻量增量) |
| `--no-quality-check` | 关闭输出质检 |

> 质检阈值(`--warn-min-chars`、`--warn-garbled-ratio`、`--warn-min-confidence`…)、云端 token、模型选择等全部选项见 `makeitdown --help`。

**质检只警告、不改内容**:成功但可疑的文件(整页空白、乱码、异常重复、多页却几乎没字、OCR 置信度过低)会被标记进 `report.json` 的 `warnings` 与该 `.md` 的 frontmatter(`quality: suspect`),警告随文件进入下游。置信度检测专治"OCR 局部数字损坏"这类法律高危盲点(仅本地 PP-StructureV3 暴露逐区域置信度时生效)。

### LLM 标题层级重建(可选,默认关)

扫描件 OCR 出来是扁平文本。`--structure-headings` 用 LLM **只为 OCR 产物**重建标题层级。**安全第一**:LLM 只返回"行号 → 标题级别"的数字,绝不经手正文——加 `#` 由本地完成,正文逐字节原样保留,因此**在原理上不可能改动正文**(金额/日期/当事人零风险),任何失败都回退原文。该功能需显式 `--cloud-consent`,端点/模型/key 从环境变量读:

```bash
$env:MAKEITDOWN_LLM_BASE_URL = "https://api.deepseek.com/v1"
$env:MAKEITDOWN_LLM_MODEL    = "deepseek-chat"
$env:MAKEITDOWN_LLM_API_KEY  = "你的key"   # 绝不硬编码
makeitdown docs --ocr-engine local --structure-headings --cloud-consent
```

## 输出示例

```markdown
---
source: 合同/2024采购框架.pdf
source_type: pdf
engine: cloud:paddleocr-vl-1.6
pages: 12
provenance_version: 1
source_sha256: <原始 PDF 的 SHA-256>
content_sha256: <下方 Markdown 正文的 SHA-256>
---

# 采购框架协议
...
```

转换完成后,把构建 wiki 的工作流指向 `<输出目录>`——干净的 Markdown + frontmatter 正适合 LLM 增量消化、交叉引用。

## 开发(从源码)

```bash
git clone https://github.com/Tsinglaw/AnyDocsMarked.git
cd AnyDocsMarked/makeitdown
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]" -i https://mirrors.aliyun.com/pypi/simple
.venv/Scripts/python -m pytest -q
```

> Windows 用 `.venv/Scripts/python`;macOS/Linux 用 `.venv/bin/python`。老式 `.doc/.wps` 的真二进制转换需 `makeitdown[com]`(仅装 COM 桥)或 PATH 中的 `soffice`;makeitdown 从不自动安装任何外部程序。
