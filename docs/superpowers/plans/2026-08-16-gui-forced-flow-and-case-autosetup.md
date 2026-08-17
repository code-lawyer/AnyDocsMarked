# 强制 GUI 流程 + 案件自动搭建 实现 Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 让用户只给 agent 一个路径、只面对一个 GUI 窗口即可完成前半段：引擎自动把该路径下的散落资料归入 `原始资料/`（用户不建文件夹）；SKILL 只认可 GUI 入口（删无头选项）；无桌面时明确报错而非静默无头。

**Architecture:** 在既有引擎（`lawiki/ingest.py`）加一个确定性首阶段 `_setup_case`（幂等移动散落资料进 `原始资料/`，保留脚手架不动）；`ingest_gui.py` 接受路径参数、首屏预览+确认移动、`main()` 捕获无桌面硬失败；改写 SKILL 第一步为「后台启动 GUI + 等 `ingest-report.json` 交接」。保留 lawiki 内部 `原始资料/`-在-case-根 的契约不变。

**Tech Stack:** Python 3.11+ 标准库（shutil/pathlib/tkinter/unittest）。

**Spec:** `docs/superpowers/specs/2026-08-16-gui-forced-flow-and-case-autosetup-design.md`

## Global Constraints

- **仅标准库**，无新依赖。
- **`_setup_case` 三条安全约束**：① 只移动**非保留、非隐藏(不以`.`开头)** 的顶层条目；保留名单绝不移动 = `原始资料`/`_md`/`.rag`/`wiki`/`AGENTS.md`/`CLAUDE.md`/`ingest-report.json`/`report.json`。② **幂等**：`原始资料/` 已存在则完全不动（直接返回）。③ 移动清单要可见（`_say`）。
- **保留名单单一来源**在 `ingest.py`（`_RESERVED_CASE_ENTRIES`）；GUI 预览计数**导入** `ingest._movable_entries`，不另抄一份。
- **无桌面硬失败**：`ingest_gui.main()` 捕获 `tkinter.TclError` → 打印明确中文提示 + 返回退出码 `3`；**绝不**静默降级无头。
- **保留 lawiki 路径契约**：`原始资料/`/`_md/`/`.rag/`/`wiki/` 仍在 case 根内；只自动填充 `原始资料/`。
- **Windows 优先**：`shutil.move`（同盘即 rename）；子进程/输出 utf-8。
- 不动引擎门禁/退出码/report 契约、不动 makeitdown/rag/lawiki 本体。

## File Structure

- **Modify `lawiki/ingest.py`**：加 `_RESERVED_CASE_ENTRIES`、`_movable_entries`、`_setup_case`；`main()` 首阶段调用。
- **Modify `lawiki/test_ingest.py`**：`SetupCaseTests` + 一个 main 级测试。
- **Modify `lawiki/ingest_gui.py`**：`IngestApp(case_dir=…)`、首屏移动预览、`main(argv)` 无桌面守卫、`import ingest`。
- **Modify `lawiki/test_ingest_gui.py`**：无桌面守卫测试。
- **Modify `lawiki/skill/lawiki/SKILL.md`**（第一步）+ **`references/setup.md`**。

引擎新阶段顺序：**_setup_case（幂等归入原始资料）→ init_case → preflight → convert → index → reconcile → 门**。

---

### Task 1: `_setup_case` 归入原始资料（确定性 IO + 单测）

**Files:**
- Modify: `lawiki/ingest.py`（加常量 + 两个函数，放在 `_preflight` 之前）
- Test: `lawiki/test_ingest.py`（加 `SetupCaseTests`）

**Interfaces:**
- Produces:
  - `_RESERVED_CASE_ENTRIES: frozenset[str]`
  - `_movable_entries(case_dir: Path) -> list[Path]` — case 根下**可移动**的顶层条目（非保留、非隐藏），按名排序。
  - `_setup_case(case_dir: Path) -> list[str]` — 若 `原始资料/` 不存在则建之并把可移动条目移入，返回移动的名字列表；若已存在则返回 `[]`（幂等）。

- [ ] **Step 1: 写失败测试**

在 `test_ingest.py` 追加：

```python
class SetupCaseTests(unittest.TestCase):
    def test_moves_loose_files_into_raw(self):
        with tempfile.TemporaryDirectory() as td:
            case = Path(td)
            (case / "借条.txt").write_text("x", encoding="utf-8")
            sub = case / "合同"; sub.mkdir()
            (sub / "采购.pdf").write_bytes(b"%PDF")
            moved = ingest._setup_case(case)
            self.assertEqual(sorted(moved), ["借条.txt", "合同"])
            self.assertTrue((case / "原始资料" / "借条.txt").is_file())
            self.assertTrue((case / "原始资料" / "合同" / "采购.pdf").is_file())
            self.assertFalse((case / "借条.txt").exists())

    def test_idempotent_when_raw_exists(self):
        with tempfile.TemporaryDirectory() as td:
            case = Path(td)
            raw = case / "原始资料"; raw.mkdir()
            (raw / "已在里面.txt").write_text("x", encoding="utf-8")
            (case / "新扔的.txt").write_text("y", encoding="utf-8")  # 不该被动
            self.assertEqual(ingest._setup_case(case), [])
            self.assertTrue((case / "新扔的.txt").is_file())        # 原地不动
            self.assertFalse((raw / "新扔的.txt").exists())

    def test_reserved_and_hidden_not_moved(self):
        with tempfile.TemporaryDirectory() as td:
            case = Path(td)
            (case / "料.txt").write_text("x", encoding="utf-8")
            for name in ("_md", "wiki", ".git"):
                (case / name).mkdir()
            (case / "AGENTS.md").write_text("a", encoding="utf-8")
            moved = ingest._setup_case(case)
            self.assertEqual(moved, ["料.txt"])
            for name in ("_md", "wiki", ".git", "AGENTS.md"):
                self.assertTrue((case / name).exists())            # 保留/隐藏原地
            self.assertFalse((case / "原始资料" / "_md").exists())

    def test_empty_case_creates_empty_raw(self):
        with tempfile.TemporaryDirectory() as td:
            case = Path(td)
            self.assertEqual(ingest._setup_case(case), [])
            self.assertTrue((case / "原始资料").is_dir())
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd lawiki && python -m pytest test_ingest.py::SetupCaseTests -q`
Expected: FAIL — `AttributeError: ... '_setup_case'`。

- [ ] **Step 3: 写最小实现**

在 `ingest.py` 的 `_preflight` 之前追加：

```python
# case 根下我们自己搭建的脚手架——_setup_case 归入原始资料时绝不移动这些。
_RESERVED_CASE_ENTRIES = frozenset({
    "原始资料", "_md", ".rag", "wiki",
    "AGENTS.md", "CLAUDE.md", "ingest-report.json", "report.json",
})


def _movable_entries(case_dir: Path) -> list[Path]:
    """case 根下属于用户资料、可移入 原始资料/ 的顶层条目：既非我们建的脚手架，
    也非隐藏项（.git 等）。按名排序，稳定可测。"""
    return sorted(
        (p for p in case_dir.iterdir()
         if p.name not in _RESERVED_CASE_ENTRIES and not p.name.startswith(".")),
        key=lambda p: p.name,
    )


def _setup_case(case_dir: Path) -> list[str]:
    """幂等地保证 <case>/原始资料 装着用户资料。原始资料/ 不存在时：建之，并把每个
    可移动顶层条目（保留原名/子结构）移入；已存在时：什么都不做（视为已搭建）。
    返回被移动的名字列表（已搭建时为空）。移动是破坏性操作，故只碰 _movable_entries。"""
    raw = case_dir / "原始资料"
    if raw.exists():
        return []
    movable = _movable_entries(case_dir)
    raw.mkdir(parents=True)
    moved: list[str] = []
    for p in movable:
        shutil.move(str(p), str(raw / p.name))
        moved.append(p.name)
    return moved
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd lawiki && python -m pytest test_ingest.py -q`
Expected: PASS（含既有测试；`_case()` 帮手已预建 `原始资料/`，不受影响）。

- [ ] **Step 5: 提交**

```bash
git add lawiki/ingest.py lawiki/test_ingest.py
git commit -m "feat(ingest): _setup_case — idempotently move loose material into 原始资料/"
```

---

### Task 2: 把 `_setup_case` 接进 `main()`

**Files:**
- Modify: `lawiki/ingest.py`（`main` 首阶段）
- Test: `lawiki/test_ingest.py`（加 `MainSetupTests`）

**Interfaces:**
- Consumes: `_setup_case`、`_movable_entries`
- Produces: `main` 在 `_run_init_case` 之前跑 `_setup_case`（dry-run 只预览不移动）。

- [ ] **Step 1: 写失败测试**

在 `test_ingest.py` 追加：

```python
class MainSetupTests(unittest.TestCase):
    def test_main_sets_up_loose_folder_then_runs(self):
        with tempfile.TemporaryDirectory() as td:
            case = Path(td) / "案子"; case.mkdir()
            (case / "借条.txt").write_text("甲借乙五万", encoding="utf-8")  # 散落，无 原始资料/
            (case / "_md").mkdir()
            (case / "_md" / "借条.md").write_text("x", encoding="utf-8")
            with mock.patch.object(ingest.shutil, "which", return_value="mk"), \
                 mock.patch.object(ingest, "_run_init_case"), \
                 mock.patch.object(ingest, "_run_convert", return_value=({"succeeded": 1, "failed": 0}, 0)), \
                 mock.patch.object(ingest, "_run_index", return_value=({"files_indexed": 1, "files_skipped": 0}, True)), \
                 mock.patch.object(ingest, "_run_reconcile", return_value=[]):
                rc = ingest.main([str(case), "--ocr-engine", "local"])
            self.assertEqual(rc, ingest.EXIT_PASS)
            self.assertTrue((case / "原始资料" / "借条.txt").is_file())  # 已归入
            self.assertFalse((case / "借条.txt").exists())

    def test_dry_run_does_not_move(self):
        with tempfile.TemporaryDirectory() as td:
            case = Path(td) / "案子"; case.mkdir()
            (case / "借条.txt").write_text("x", encoding="utf-8")
            with mock.patch.object(ingest.subprocess, "run"):
                rc = ingest.main([str(case), "--dry-run"])
            self.assertEqual(rc, ingest.EXIT_PASS)
            self.assertTrue((case / "借条.txt").exists())          # 未移动
            self.assertFalse((case / "原始资料").exists())
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd lawiki && python -m pytest test_ingest.py::MainSetupTests -q`
Expected: FAIL（`借条.txt` 未被移动 —— `_setup_case` 尚未接入 `main`）。

- [ ] **Step 3: 写最小实现**

在 `ingest.py` `main` 中，`case = Path(...).resolve()` 之后、`_run_init_case` 之前插入：

```python
    if args.dry_run:
        if not (case / "原始资料").exists():
            _say(f"将把 {len(_movable_entries(case))} 项归入 原始资料/（dry-run 不移动）")
    else:
        moved = _setup_case(case)
        if moved:
            head = "、".join(moved[:8]) + ("…" if len(moved) > 8 else "")
            _say(f"已把 {len(moved)} 项归入 原始资料/：{head}")
```

（其余 `main` 不变；`raw = case / "原始资料"` 的 preflight 现在在 `_setup_case` 之后跑，`原始资料/` 已就绪。）

- [ ] **Step 4: 跑测试确认通过**

Run: `cd lawiki && python -m pytest test_ingest.py -q`
Expected: PASS（既有 `MainTests` 的 `_case()` 预建了 `原始资料/`，`_setup_case` 幂等无操作，不受影响）。

- [ ] **Step 5: 提交**

```bash
git add lawiki/ingest.py lawiki/test_ingest.py
git commit -m "feat(ingest): main runs _setup_case first (dry-run previews, no move)"
```

---

### Task 3: GUI 接受路径 + 首屏移动预览 + 无桌面硬失败

**Files:**
- Modify: `lawiki/ingest_gui.py`
- Test: `lawiki/test_ingest_gui.py`（加 `MainGuardTests`）

**Interfaces:**
- Consumes: `ingest._movable_entries`（导入，保留名单单一来源）
- Produces:
  - `IngestApp(config_path=None, case_dir: Path | None = None)` — 预置 case 目录。
  - `main(argv: list[str] | None = None) -> int` — 读取可选位置参数为 case 路径；`tk.TclError` → 打印提示 + 返回 `3`。

- [ ] **Step 1: 写失败测试（无桌面守卫，纯逻辑可测）**

在 `test_ingest_gui.py` 追加：

```python
class MainGuardTests(unittest.TestCase):
    def test_no_display_hard_fails_with_code_3(self):
        import tkinter
        with mock.patch.object(g, "IngestApp", side_effect=tkinter.TclError("no display name")):
            rc = g.main([])
        self.assertEqual(rc, 3)

    def test_success_path_returns_0(self):
        fake_app = mock.Mock()
        with mock.patch.object(g, "IngestApp", return_value=fake_app):
            rc = g.main([])
        fake_app.mainloop.assert_called_once()
        self.assertEqual(rc, 0)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd lawiki && python -m pytest test_ingest_gui.py::MainGuardTests -q`
Expected: FAIL（`g.main` 目前不接受 argv / 不返回 3 / 不捕获 TclError）。

- [ ] **Step 3: 写实现**

在 `ingest_gui.py`：

1) 文件顶部 import 区加：`import ingest`（复用 `ingest._movable_entries`；GUI 仍 shell out 到 ingest.py 跑真流程，这里只借纯函数做预览）。

2) `IngestApp.__init__` 签名与预置：

```python
    def __init__(self, config_path: Path | None = None, case_dir: Path | None = None):
        super().__init__()
        ...
        self.case_dir: Path | None = Path(case_dir) if case_dir else None
        ...
        self._build_choice_screen()
```

3) `_build_choice_screen` 的文件夹行：若已有 `case_dir`，显示路径 + 移动预览；否则保留原「选择文件夹」。在该行下方加预览标签：

```python
        row = tk.Frame(self._container); row.pack(fill="x", pady=6)
        self._folder_lbl = tk.Label(row, text=str(self.case_dir) if self.case_dir else "（未选择）",
                                    fg="black" if self.case_dir else "gray")
        self._folder_lbl.pack(side="left")
        tk.Button(row, text="选择文件夹…", command=self._on_pick_folder).pack(side="right")
        self._preview_lbl = tk.Label(self._container, text="", fg="#a60", wraplength=580, justify="left")
        self._preview_lbl.pack(anchor="w")
        self._refresh_preview()
```

4) 新增 `_refresh_preview`（用 `ingest._movable_entries`），并在 `_on_pick_folder` 末尾调用它：

```python
    def _refresh_preview(self) -> None:
        if self.case_dir is None:
            self._preview_lbl.config(text=""); return
        if (self.case_dir / "原始资料").exists():
            self._preview_lbl.config(text="✓ 已就绪（原始资料/ 已存在，不再移动文件）")
        else:
            try:
                n = len(ingest._movable_entries(self.case_dir))
            except OSError:
                n = 0
            self._preview_lbl.config(
                text=f"⚠ 将把该文件夹下的 {n} 项归入子目录 原始资料/ 再处理（原件会被移动）。"
                     "若这不是你的案件资料专用文件夹，请重选。")

    def _on_pick_folder(self) -> None:
        d = filedialog.askdirectory()
        if d:
            self.case_dir = Path(d)
            self._folder_lbl.config(text=str(self.case_dir), fg="black")
            self._refresh_preview()
```

5) `main` 接受 argv + 无桌面守卫：

```python
def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    argv = sys.argv[1:] if argv is None else argv
    case_dir = Path(argv[0]).resolve() if argv else None
    try:
        app = IngestApp(case_dir=case_dir)
    except tk.TclError as e:
        print("[lawiki-ingest-gui] 无法打开图形界面：本流程需在有桌面的用户电脑上运行；"
              f"当前环境无图形界面（{e}）。请在用户机器上运行，或改用无头 ingest.py（仅限无桌面/CI）。",
              flush=True)
        return 3
    app.mainloop()
    return 0
```

（`if __name__ == "__main__": raise SystemExit(main())` 不变。）

- [ ] **Step 4: 跑测试确认通过 + 非阻塞冒烟**

Run: `cd lawiki && python -m pytest test_ingest_gui.py -q`（含既有 9 项 + `MainGuardTests`）
Run: `cd lawiki && python -c "import ingest_gui"`（导入 OK，含 `import ingest`）
**不要真跑 `python ingest_gui.py`（mainloop 阻塞）。**
Expected: PASS；导入 OK。

- [ ] **Step 5: 提交**

```bash
git add lawiki/ingest_gui.py lawiki/test_ingest_gui.py
git commit -m "feat(ingest-gui): accept case path arg + move-preview confirm + no-display hard-fail(exit 3)"
```

---

### Task 4: SKILL 第一步改写（只认 GUI + 交接协议）+ setup.md

**Files:**
- Modify: `lawiki/skill/lawiki/SKILL.md`（第一步）
- Modify: `lawiki/skill/lawiki/references/setup.md`

- [ ] **Step 1: 改 SKILL.md 第一步**

把「## 第一步：跑摄入（一步到位，两种入口）」整节替换为（第〇步、第三步及以后不动）：

```markdown
## 第一步：跑摄入（弹 GUI 交用户，别自己无头跑）

前半段（归入原始资料 + 建案脚手架 + 转换 + 建索引 + 对账 + 完整性门）**只走一条路：
把 GUI 弹给用户操作**。用户只需给你一个「资料所在路径」，其余你来。

1. **拿到用户给的路径**（该路径下有待处理的散落资料；文件夹叫什么、要不要建
   `原始资料/`，用户都不用管——引擎会把散落资料自动归入 `原始资料/`）。
2. **后台启动 GUI**，把该路径传进去（后台，避免被窗口阻塞）：
   - Claude Code：用 Bash 后台模式跑 `python <SKILL_DIR>/../../ingest_gui.py "<路径>"`；
   - 其它 agent：等价的 `nohup … &` / detached 方式。
3. **告诉用户**：「已弹出窗口，请在里面①确认把资料归入原始资料 ②选本地/云端 OCR
   ③等它跑完」。**然后等待**——不要自己去跑 `ingest.py`。
4. **完成信号** = `<路径>/ingest-report.json` 出现 + GUI 进程退出。出现后读它：
   `gate.passed` 为真则进第三步；`gate.reasons` 非空则**如实向用户汇报**需处理项
   （失败/跳过的文件不要凭空补，按缺失处理）。
5. **无桌面兜底**：若 GUI 以退出码 3 + 「无图形界面」报错退出，说明当前环境无桌面
   ——**如实告诉用户本流程需在其有桌面的电脑上运行**，不要偷偷改用无头方式替他跑。

> 引擎（GUI 底层调）幂等跑：把散落资料归入 `原始资料/` → `init_case`（脚手架 +
> `AGENTS.md`/`CLAUDE.md` 闭世界锚点，缺失会被第三步 `lint check` 判硬违规）→
> `makeitdown` → `rag index` → `reconcile`。边界止于 `_md` + `.rag`：把散文变成带
> 锚点的 wiki 是第三步的 LLM 工作，不在引擎内。**无头 `ingest.py` 仅为无桌面/CI
> 兜底，不是给用户的正常路径——正常一律弹 GUI。**
```

同时更新「## 流水线」下那句为：`原始资料`（引擎自动归入）/`_md`/`.rag` 由第一步产出。

- [ ] **Step 2: 改 setup.md 的「GUI 用法」小节**

把 Plan B 加的 GUI 小节改为反映新流程（用户给路径、后台启动、移动确认、无桌面报错）：

```markdown
### GUI 用法（唯一的用户流程）

用户把资料放在任意一个文件夹里，只需把该文件夹路径告诉 agent。agent 后台启动
`python ingest_gui.py "<路径>"`；窗口里：①确认「把该文件夹下的资料归入子目录
原始资料/」②选本地/云端（云端可点「去申请」贴 token）③看进度条④完成弹窗。
底层跑的是和引擎完全一样的门禁；token 仅本次运行经环境变量传入、不写盘；原件被
移动到 `原始资料/` 后即视为不可变来源层。无桌面环境下 GUI 会以退出码 3 明确报错，
不会静默改跑无头。
```

- [ ] **Step 3: 一致性核对 + 提交**

核对命令/路径（`ingest_gui.py "<路径>"`）、退出码 3、交接信号（`ingest-report.json`）在 SKILL 与 setup.md 一致，且与 `ingest_gui.py` `main` 实现一致。

```bash
git add lawiki/skill/lawiki/SKILL.md lawiki/skill/lawiki/references/setup.md
git commit -m "docs(lawiki): first step is GUI-only (background launch + report handoff); user just gives a path"
```

---

## 落地后整体回归

```bash
cd lawiki && python -m pytest skill/lawiki scripts test_install.py test_ingest.py test_ingest_gui.py -q
cd .. && uvx ruff check --select E9,F lawiki/ingest.py lawiki/ingest_gui.py lawiki/test_ingest.py lawiki/test_ingest_gui.py
git diff --check
```

外加手工冒烟（真机）：GUI 传入一个含散落文件的文件夹 → 首屏预览「将把 N 项归入」→ 确认后跑通、原件进 `原始资料/`；再传一个已含 `原始资料/` 的 → 显示「已就绪」不重移。

## 自查（对照 spec）

- 决策一（GUI 唯一入口 + 交接协议）→ Task 4；决策二（无桌面退出码 3）→ Task 3；决策三（自动归入 + 三安全约束）→ Task 1/2。
- 保留名单单一来源在 ingest.py，GUI 导入复用 → Task 3。
- 幂等（原始资料/ 已存在不动）→ Task 1 `test_idempotent_when_raw_exists` + Task 2 既有 MainTests 不受影响。
- 破坏性移动的守护：只移非保留非隐藏 → Task 1 `test_reserved_and_hidden_not_moved`；GUI 移动前预览确认 → Task 3。
- 非目标守卫：不改 lawiki 内部路径契约、不加复制/只读模式、不动引擎门禁。
