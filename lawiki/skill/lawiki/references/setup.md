# 首次环境配置（setup）

agent 第一次在某台机器上用 lawiki、或检测到环境缺失时，照此走一遍。**每一步都让用户看到你在做什么**——尤其安装时明确说「正在安装环境……」。

lawiki 需要三样：① Python 3.11+（跑本 skill 自带的校验，零第三方依赖）② makeitdown（上游转换器，把各式文件转成 md）③ Obsidian（人看 wiki 用，可选、不自动装 GUI）。**可选第四样**：rag-retriever（语义检索，支撑交叉验证问答；不装则问答退化为「仅 wiki」，核心不受影响）。

## 第 0 步 · 一键安装（bundle 发布包，推荐）

若本 skill 来自 `anydocsmarked` 发布包（解压后 `skill/lawiki` 旁有 `vendor/` 与 `install.py`），首选一键安装——它从 bundle 内 `vendor/` 本地装好 makeitdown 与 rag-retriever：

1. 先问用户选 **OCR 方式**（本地/云端，见第 2 步的对比表）。
2. 在 bundle 根目录跑（明确告诉用户"正在安装环境……"）：
   ```
   python install.py --ocr <local|cloud>
   ```
   （想先看将执行什么，可加 `--dry-run`。embedding 默认 local、离线、无需 key。）
3. 装完它会打印各部件结果与激活语。任一部件失败不阻塞核心（降级，见第 4 步）。

非 bundle 场景（只拿到 skill、或要手动控制）走下面第 1–5 步。

## 两种发布包（Release 二选一）

- **`anydocsmarked-v<ver>-offline.zip`（离线包，推荐国内/内网）**：已内置 embedding
  模型（`bge-small-zh-v1.5`）与 tiktoken，`install.py` 装完首次建索引**无需联网**。
- **`anydocsmarked-v<ver>.zip`（源码小包）**：不含模型，首次建索引会从境外
  HuggingFace 下 embedding；国内可设 `HF_ENDPOINT=https://hf-mirror.com` 加速。
- 两者其余一致。注意：可选的**重排模型**（`RAG_RERANK=local`）两种包都不内置，
  开启时仍需联网下载。

> ⚠️ **"离线包=零下载"这个承诺，只在用 `python install.py` 安装时成立**。模型
> 文件本身没进 git 仓库（`.gitignore` 排除，只在打 `-offline` 包那次 CI 里塞进
> 发布物），`install.py` 从 bundle 内 `vendor/` 装才带得到它；**第 3 步补**里那条
> 手动 `uv tool install "rag-retriever @ git+..."` 命令是从 GitHub 现拉源码，
> **不论你手上是不是 `-offline` 包，这条命令装出来的实例永远没有内置模型、永远
> 会联网下载**。装了离线包就用第 0 步的 `install.py`，别绕去手动命令。

## 断网 / 内网部署（国内）

大陆断外网使用，按此保证不触国际互联网（offline 发布包已内置 embedding 模型与
tiktoken，云端选项全是国内服务）：

- **提前自备 Python 3.11+**：别依赖 uv 自动下载（那走 GitHub 的
  python-build-standalone，国内常断）。uv 本体用
  `pip install uv -i https://mirrors.aliyun.com/pypi/simple` 装；若必须让 uv 管
  Python，设环境变量 `UV_PYTHON_INSTALL_MIRROR` 指向国内镜像。
- **内网/涉密三件套**（联网机备好带入内网）：Python 官方安装包 + uv wheel
  （`pip download uv`）+ `-offline` 发布包。进内网后 `python install.py --ocr local`，
  装完 `python install.py --check-offline` 逐项核验离线就绪。
- **如实提示**：`RAG_EMBED_BACKEND=ollama` 拉模型走境外 registry，国内建议 `local`
  （内置）或 `openai`（硅基流动）；reranker（`RAG_RERANK=local`）开启需联网下载。
  MinerU 互校默认已从 ModelScope（魔搭）取权重，国内首用无需 HuggingFace。
- **边界**：agent 本身（如 Claude）的联网需求超出本项目范围；skill 跨 agent 可用，
  配国产 agent 可做到全链路国内。

## 第 1 步 · 检测（并把结果告诉用户）

依次在 shell 跑，报告每项有无：
- `python --version` —— 需 3.11+。
- `uv --version` —— 装 makeitdown 用（没有也可后面装）。
- `makeitdown --help` —— 上游转换器。
- `rag-retriever --help` —— 可选，RAG 检索（没有则问答仅 wiki）。

## 第 2 步 · 让用户选 OCR 方式（装 makeitdown 前必问）

扫描件 / 图片需要 OCR。**makeitdown 默认用云端**（开箱即用、无需重型安装），但**绝不静默上传**。两种模式对比，**看用户是否接受云端 + 显式同意、或坚持本地**：

| | 本地 PaddleOCR | 云端 PaddleOCR（AI Studio，默认） |
|---|---|---|
| 转换时联网 | **不需要** | 需要 |
| 账号 / token | **不需要**，装完即用 | 需去百度 AI Studio 申请 |
| 隐私 | 文件**不出本机** | 文件上传到百度服务器（**需显式同意**） |
| 费用 | 免费 | 可能按量计费 |
| 体积 / 速度 | 大（几百 MB）、转换较慢 | 小、装得快、转换较快 |

- **接受云端**（推荐多数用户）→ 设好 token 并在转换时加 `--cloud-consent`，享受轻快无需本地安装。
- **坚持本地**（涉密案件 / 离线 / 磁盘够）→ 装"本地版"，加 `--ocr-engine local`，文件永不上传。

### 选云端：申请 token（大陆可直连）

请用户点开 **百度 AI Studio 申请 API：https://aistudio.baidu.com/paddleocr** ，拿到 token 后设环境变量：
```
PADDLEOCR_AISTUDIO_TOKEN=<你的token>
```
（或转换时传 `--cloud-token <token>`。）**转换时必须加 `--cloud-consent`，否则不会上传、自动跳过**（绝不静默上云、绝不硬编码 token）。

## 第 3 步 · 安装（明确告诉用户「正在安装环境……」）

按所选 OCR 模式装 makeitdown（**大陆走阿里云 PyPI 镜像**，避开卡顿；清华源在部分云环境/境外网络下会连接超时，阿里云镜像商用 CDN 更稳）：

- **本地版**：
  ```
  uv tool install --index https://mirrors.aliyun.com/pypi/simple "makeitdown[local] @ git+https://github.com/Tsinglaw/AnyDocsMarked.git#subdirectory=makeitdown"
  ```
- **云端版**：
  ```
  uv tool install --index https://mirrors.aliyun.com/pypi/simple "makeitdown @ git+https://github.com/Tsinglaw/AnyDocsMarked.git#subdirectory=makeitdown"
  ```
- **缺 Python / uv**：先装 uv（Windows：`winget install astral-sh.uv`；macOS/Linux：`curl -LsSf https://astral.sh/uv/install.sh | sh`），uv 能顺带备好 Python。装系统软件可能要权限——装不动就把命令交给用户自己跑，别硬来。
- **本 skill 的校验（lint）**：零依赖，只要有 Python 即可，**无需安装**。
- 不再需要钉 `--python`：makeitdown 要求 `>=3.11`（无上限），uv 会直接用已有的、满足要求的解释器。
- `--index 阿里云镜像` 仅为大陆加速依赖下载；海外可去掉。若这条也连不上（罕见），去掉 `--index` 让 uv 落回默认 pypi.org。git 源已用 GitHub。

装完用 `makeitdown --help` 验证；提示用户命令找不到时跑 `uv tool update-shell` 后开新终端。

## 第 3 步补 · RAG 检索（可选，可降级）

支撑交叉验证问答。装与不装都行——不装则问答退化「仅 wiki」，核心（wiki + lint）零依赖不受影响。细节见 `rag.md`。

**手上有 `-offline` 发布包？直接用第 0 步的 `python install.py`，跳过本节。** 下面这条
手动命令从 GitHub 拉源码，**不带内置模型、首次索引必联网下载**——只适合没有离线包、
或明确接受联网下载的场景。装完想确认到底离没离线，跑
`python install.py --check-offline`（对实际运行实例做真实探针，不是猜）。

**装 rag-retriever（会联网下载 embedding 模型，非离线）**（让 `rag-retriever` 进 PATH；大陆走阿里云镜像加速依赖）：
```
uv tool install --index https://mirrors.aliyun.com/pypi/simple "rag-retriever @ git+https://github.com/Tsinglaw/AnyDocsMarked.git#subdirectory=rag-retriever"
```
（开发期也可不装、用环境变量 `LAWIKI_RAG_CMD='uv run --project "<本地路径>" rag-retriever'` 指向本地仓库，见 `rag.md`。海外可去掉 `--index`。）

**选 embedding 后端**（两个都在**本机**跑——**无云端 embedding，案件正文永不离机**）：

| | `local`（fastembed） | `ollama`（bge-m3，本地服务） |
|---|---|---|
| 联网 | **不需要**，离线（offline 包内置模型） | 不需要（本地 ollama 服务） |
| 账号 / key | **不需要** | 不需要 |
| 中文质量 | 可用（bge-small-zh） | **最佳** |
| 适合 | 涉密 / 离线 / 开箱即用 | 已跑本地 ollama、要 bge-m3 质量 |

用环境变量选：`RAG_EMBED_BACKEND=local|ollama`（默认 `local`）。**云端 embedding（openai/远程 ollama）已移除**——想要 bge-m3 质量走**本地** ollama，无需上云；`RAG_OLLAMA_URL` 必须是 loopback，远程端点会被硬拒。

**答案侧可选加强（查询期能力，能力接线契约里标 `phase=answer`）**：这两项在**问答**阶段生效，不由摄入 GUI 控制，按需在答案环境设环境变量——
- `RAG_RERANK=local`：交叉编码器重排，提升精排质量；需联网下载重排模型、更慢。
- `RAG_MIN_SCORE=<0~1>`：向量通道相关度下限，滤掉弱命中；设太高会漏检、太低无效，默认 `0`（关）。

**一致性铁规**：索引与查询**必须同一 embedding 模型**，否则相似度失真。机制：rag-retriever 索引时把模型记进 `.rag/`，wrapper 查询前自动比对、不一致即降级并提示 rebuild（删 `.rag/` 重建索引）。**换模型 = 必须重建索引。**

## 第 3 步再补 · 问答交付闸门加硬（可选，仅 Claude Code）

问答协议已要求 agent 交付前自跑 `lint.py answer`（见 `qa.md` 第四步）。用 Claude Code 时可再加一道 harness 级保险：**Stop hook** 在每次回复结束时自动校验回复中的锚点（逐字存在 + 指向本案 `_md/`），违规自动打回重答。在**案件目录**建 `.claude/settings.json`（`<SKILL_DIR>` 换成本 skill 的绝对路径）：

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python \"<SKILL_DIR>/lint/stop_hook.py\""
          }
        ]
      }
    ]
  }
}
```

边界（如实告诉用户）：hook 只做零误报的两检；**无锚点的回复不拦**（它分不清案件问答与日常闲聊），「裸答必须明示未找到」的兜底仍靠协议里的 answer 闸门。其他 agent（Codex / Copilot 等）无此机制，靠协议约束；闸门工具本身零依赖、随 skill 走。

## 第 4 步 · 优雅降级

makeitdown 实在装不上时**不阻塞核心**：若用户已有别处转好的 `_md/`，lawiki 仍能直接 ingest + 跑校验，只是不能在本机做转换。把这点告诉用户。

RAG 同理可降级：没装 rag-retriever / 没建索引 / 模型不一致时，问答自动退化「仅 wiki」，并对用户挑明「当前无 RAG 交叉验证」。

## 第 5 步 · 告诉用户怎么激活

环境就绪后，明确告诉用户**以后怎么启动**：

> 把法律文件放进案件目录的 `原始资料/`，然后对我说下面任一句即可启动：
> 「**整理案件资料**」「**把案件资料建成 wiki**」「**建案件库 / 建案件 wiki**」「**处理这个案子**」「**ingest case files**」「**build a case wiki**」。

## 一步摄入

GUI 是正常/唯一的用户流程（见下节「GUI 用法」）；本节的无头 `ingest.py` **仅供无桌面 /
CI 兜底**，不是给用户的正常路径。

把待转文件放进 `<案件目录>/原始资料/`，跑一条：

    python ingest.py <案件目录> [--ocr-engine auto|local|cloud] [--cloud-consent] [--skip-index]

它幂等建案脚手架 → makeitdown 转换 → rag 建索引 → 源级对账 → 完整性核对，产出
`<案件目录>/ingest-report.json`，并以**单退出码**给出机器可读结论：

- `0` 全通过；`1` 转换有硬失败；`2` 前置/环境缺失（无原始资料 / 未装 makeitdown /
  选云端但未加 `--cloud-consent`）；`3` 完整性门未过（源级未处置>0，或装了 rag 却索引不全）。

默认 `--ocr-engine auto`（有本地用本地、仅在已配 token+consent 时用云、否则绝不静默上传）。
未装 rag-retriever 时自动跳过建索引、不算失败（问答退化仅 wiki）。

边界：本步**止于 `_md` + `.rag`**。把散文变成带锚点的 wiki 是 LLM 环节，由 agent 加载
lawiki 后驱动，**不在引擎内**；wiki 的 `lint check` 也在建 wiki 之后才跑。

### GUI 用法（唯一的用户流程）

用户把资料放在任意一个文件夹里，只需把该文件夹路径告诉 agent。agent 后台启动
`python ingest_gui.py "<路径>"`；窗口里：①确认「把该文件夹下的资料归入子目录
原始资料/」②选本地/云端（云端可点「去申请」贴 token）③看进度条④完成弹窗。
底层跑的是和引擎完全一样的门禁；token 仅本次运行经环境变量传入、不写盘；原件被
移动到 `原始资料/` 后即视为不可变来源层。无桌面环境下 GUI 会以退出码 3 明确报错，
不会静默改跑无头。
