import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import reconcile as R  # noqa: E402


def _case(tmp_path, report, log_md=None):
    (tmp_path / "_md").mkdir()
    (tmp_path / "_md" / "report.json").write_text(
        json.dumps(report, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "wiki").mkdir()
    if log_md is not None:
        (tmp_path / "wiki" / "log.md").write_text(log_md, encoding="utf-8")
    (tmp_path / "原始资料").mkdir()
    return tmp_path


def _report(**over):
    base = {"succeeded": 0, "warned": 0, "failed": 0, "skipped_existing": 0,
            "skipped_unsupported": 0, "failures": [], "warnings": [], "skipped": []}
    base.update(over)
    return base


def test_unregistered_failure_is_unresolved(tmp_path):
    root = _case(tmp_path, _report(
        failed=1, failures=[{"file": "章程.doc", "error": "no LibreOffice"}]))
    unresolved, stats = R.reconcile(root)
    assert stats["unresolved"] == 1
    assert any("原始资料/章程.doc" in w for w in unresolved)


def test_unresolved_files_structured(tmp_path):
    # 结构化暴露逐个未处置源文件路径（不含"源多于已处理"汇总行）——GUI 直接读，免解析散文。
    root = _case(tmp_path, _report(
        failed=1, failures=[{"file": "章程.doc", "error": "no LibreOffice"}]))
    _unresolved, stats = R.reconcile(root)
    assert stats["unresolved_files"] == ["原始资料/章程.doc"]


def test_registered_skip_resolves(tmp_path):
    log = ("# log\n\n"
           "## [2026-07-13] skip | 原始资料/章程.doc\n"
           "- 原因：环境无 LibreOffice，待装后补转\n")
    root = _case(tmp_path, _report(
        skipped_unsupported=1, skipped=[{"file": "章程.doc", "reason": "needs LibreOffice"}]),
        log_md=log)
    unresolved, stats = R.reconcile(root)
    assert stats["registered"] == 1 and stats["unresolved"] == 0
    assert unresolved == []


def test_registered_skip_without_reason_remains_unresolved(tmp_path):
    log = "# log\n\n## [2026-07-13] skip | 原始资料/章程.doc\n"
    root = _case(
        tmp_path,
        _report(
            skipped_unsupported=1,
            skipped=[{"file": "章程.doc", "reason": "needs LibreOffice"}],
        ),
        log_md=log,
    )

    unresolved, stats = R.reconcile(root)

    assert stats["registered"] == 0 and stats["unresolved"] == 1
    assert unresolved == ["[跳过无原因] 原始资料/章程.doc"]


def test_all_produced_passes(tmp_path):
    root = _case(tmp_path, _report(succeeded=3))
    unresolved, stats = R.reconcile(root)
    assert stats["unresolved"] == 0 and stats["produced"] == 3


def test_missing_report_errors(tmp_path):
    (tmp_path / "wiki").mkdir()
    import pytest
    with pytest.raises(FileNotFoundError):
        R.reconcile(tmp_path)
    assert R.main([str(tmp_path)]) == 2


def test_source_more_than_accounted_flags(tmp_path):
    root = _case(tmp_path, _report(succeeded=1))
    # 原始资料/ 放 2 个文件，report 只记 1 个 → 源多于已处理
    (root / "原始资料" / "a.pdf").write_text("x", encoding="utf-8")
    (root / "原始资料" / "b.pdf").write_text("y", encoding="utf-8")
    unresolved, stats = R.reconcile(root)
    assert stats["source_total"] == 2 and stats["accounted"] == 1
    assert stats["unresolved"] >= 1
    assert any("源多于已处理" in w for w in unresolved)


def test_os_junk_files_excluded_from_source_count(tmp_path):
    # Thumbs.db/desktop.ini/.DS_Store are created by the OS *after* conversion
    # (e.g. opening 原始资料/ in Explorer/Finder) and were never seen by
    # makeitdown. Counting them would false-flag every such case as
    # [源多于已处理] on a file makeitdown could never have processed.
    root = _case(tmp_path, _report(succeeded=1))
    (root / "原始资料" / "a.pdf").write_text("x", encoding="utf-8")
    for junk in ("Thumbs.db", "desktop.ini", ".DS_Store"):
        (root / "原始资料" / junk).write_text("", encoding="utf-8")
    unresolved, stats = R.reconcile(root)
    assert stats["source_total"] == 1           # junk files not counted
    assert stats["unresolved"] == 0
    assert unresolved == []


def test_main_exit_codes(tmp_path):
    root = _case(tmp_path, _report(
        failed=1, failures=[{"file": "x.doc", "error": "boom"}]))
    assert R.main([str(root)]) == 1          # 未处置 → 非 0
    assert R.main([]) == 2                    # 缺参数
