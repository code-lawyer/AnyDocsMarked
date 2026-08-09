import json
import hashlib
import os
import makeitdown.pipeline as pl
import makeitdown.pipeline as pipeline_mod
from makeitdown import convert_anydoc
from makeitdown.models import ConversionResult, ConversionUnavailable, LegacyConversionUnavailable


def _setup_tree(tmp_path):
    src = tmp_path / "in"
    (src / "sub").mkdir(parents=True)
    (src / "a.docx").write_text("x", encoding="utf-8")
    (src / "sub" / "b.png").write_bytes(b"\x00")
    (src / "note.unknownext").write_bytes(b"\x00")
    return src


def test_convert_tree_writes_mirrored_md_and_report(tmp_path, monkeypatch):
    src = _setup_tree(tmp_path)
    out = tmp_path / "out"

    monkeypatch.setattr(pl, "classify",
                        lambda p, text_threshold=50: {"docx": "native", "png": "ocr"}.get(
                            p.suffix.lstrip("."), "unsupported"))
    monkeypatch.setattr(pl, "convert_native",
                        lambda p: ConversionResult(text="# native\n\n" + "正常的文档内容" * 5,
                                                   engine="markitdown"))

    class _Disp:
        def __init__(self, **k): pass
        def convert(self, p): return ConversionResult(text="# ocr\n\n" + "正常的文档内容" * 5,
                                                      engine="local:pp-structurev3")

    monkeypatch.setattr(pl, "OCRDispatcher", _Disp)

    report = pl.convert_tree(src, out, ocr_engine="auto", ocr_model="PP-StructureV3",
                             cloud_token=None, workers=2, skip_existing=False,
                             text_threshold=50, report_path=out / "report.json")

    a_md = (out / "a.md").read_text(encoding="utf-8")
    b_md = (out / "sub" / "b.md").read_text(encoding="utf-8")
    assert a_md.startswith("---\n") and "# native" in a_md
    assert "engine: markitdown" in a_md
    assert f"source_sha256: {hashlib.sha256(b'x').hexdigest()}" in a_md
    body = "# native\n\n" + "正常的文档内容" * 5
    assert f"content_sha256: {hashlib.sha256(body.encode('utf-8')).hexdigest()}" in a_md
    assert "# ocr" in b_md
    assert report["succeeded"] == 2
    assert report["skipped_unsupported"] == 1
    saved = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert saved["succeeded"] == 2


def test_hash_aware_skip_detects_changed_source_even_with_old_mtime(tmp_path):
    src = tmp_path / "source.txt"
    src.write_text("old", encoding="utf-8")
    source_hash = hashlib.sha256(src.read_bytes()).hexdigest()
    md = tmp_path / "source.md"
    md.write_text(f"---\nsource_sha256: {source_hash}\n---\n\nold", encoding="utf-8")
    newer = md.stat().st_mtime + 10
    os.utime(md, (newer, newer))
    src.write_text("changed", encoding="utf-8")
    older = newer - 5
    os.utime(src, (older, older))
    assert pl._is_up_to_date(src, md) is False


def test_convert_tree_rejects_output_inside_input(tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    try:
        pl.convert_tree(
            src, src / "out", ocr_engine="local", ocr_model=None,
            cloud_token=None, workers=1, skip_existing=False,
            text_threshold=50, report_path=src / "out" / "report.json",
        )
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "output_dir" in str(exc)


def test_source_change_during_conversion_fails_instead_of_binding_wrong_hash(tmp_path, monkeypatch):
    src_dir = tmp_path / "in"; src_dir.mkdir()
    source = src_dir / "a.txt"; source.write_text("before", encoding="utf-8")
    out = tmp_path / "out"
    monkeypatch.setattr(pl, "classify", lambda *a, **k: "native")

    def convert_and_replace(path):
        path.write_text("after", encoding="utf-8")
        return ConversionResult(text="converted before replacement", engine="markitdown")

    monkeypatch.setattr(pl, "convert_native", convert_and_replace)
    report = pl.convert_tree(
        src_dir, out, ocr_engine="local", ocr_model=None, cloud_token=None,
        workers=1, skip_existing=False, text_threshold=50,
        report_path=out / "report.json",
    )
    assert report["failed"] == 1
    assert "source changed during conversion" in report["failures"][0]["error"]
    assert not (out / "a.md").exists()


def test_unsupported_extension_lands_in_skipped_list_not_just_count(tmp_path):
    # classify() itself (not mocked) routes unknown extensions to "unsupported"
    # with detail=None — this must still surface a reason and land in the
    # `skipped` list, not just bump the skipped_unsupported count. Otherwise a
    # downstream account (e.g. lawiki's reconcile.py) that reads the `skipped`
    # list to detect truly-unprocessed source files stays blind to it.
    src = tmp_path / "in"
    src.mkdir()
    (src / "note.xyz123").write_bytes(b"\x00")  # not in NATIVE/IMAGE/LEGACY/pdf
    out = tmp_path / "out"

    report = pl.convert_tree(src, out, ocr_engine="auto", ocr_model="PP-StructureV3",
                             cloud_token=None, workers=1, skip_existing=False,
                             text_threshold=50, report_path=out / "report.json")

    assert report["skipped_unsupported"] == 1
    assert len(report["skipped"]) == 1
    assert report["skipped"][0]["file"] == "note.xyz123"
    assert report["skipped"][0]["reason"]  # non-empty, actionable


def test_os_junk_files_are_not_reported_at_all(tmp_path):
    # Thumbs.db/desktop.ini/.DS_Store are OS-generated, not case content — they
    # must not even enter the report (not succeeded/failed/skipped/counted).
    # Reporting them as "skipped_unsupported" would make a downstream source-level
    # audit (lawiki's reconcile.py) flag them as an unresolved gap on a file
    # nobody could ever "convert".
    src = tmp_path / "in"
    src.mkdir()
    (src / "a.md").write_text("正常内容" * 5, encoding="utf-8")
    (src / "Thumbs.db").write_bytes(b"\x00")
    (src / "desktop.ini").write_text("[.ShellClassInfo]", encoding="utf-8")
    (src / ".DS_Store").write_bytes(b"\x00")
    out = tmp_path / "out"

    report = pl.convert_tree(src, out, ocr_engine="auto", ocr_model="PP-StructureV3",
                             cloud_token=None, workers=1, skip_existing=False,
                             text_threshold=50, report_path=out / "report.json")

    assert report["succeeded"] == 1
    assert report["skipped_unsupported"] == 0
    assert report["skipped"] == []
    assert report["failed"] == 0


def test_convert_tree_records_failures_without_aborting(tmp_path, monkeypatch):
    src = tmp_path / "in"
    src.mkdir()
    (src / "a.docx").write_text("x", encoding="utf-8")
    (src / "c.docx").write_text("x", encoding="utf-8")
    out = tmp_path / "out"

    monkeypatch.setattr(pl, "classify", lambda p, text_threshold=50: "native")

    def flaky(p):
        if p.name == "a.docx":
            raise ValueError("broken file")
        return ConversionResult(text="# ok\n\n" + "正常的文档内容" * 5, engine="markitdown")

    monkeypatch.setattr(pl, "convert_native", flaky)

    report = pl.convert_tree(src, out, ocr_engine="auto", ocr_model="PP-StructureV3",
                             cloud_token=None, workers=1, skip_existing=False,
                             text_threshold=50, report_path=out / "report.json")
    assert report["succeeded"] == 1
    assert report["failed"] == 1
    assert (out / "c.md").exists()
    assert not (out / "a.md").exists()
    assert any("broken file" in f["error"] for f in report["failures"])


def _single_docx(tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    (src / "a.docx").write_text("x", encoding="utf-8")
    return src


def test_garbage_output_flagged_as_warned(tmp_path, monkeypatch):
    src = _single_docx(tmp_path)
    out = tmp_path / "out"
    monkeypatch.setattr(pl, "classify", lambda p, text_threshold=50: "native")
    monkeypatch.setattr(pl, "convert_native",
                        lambda p: ConversionResult(text="x", engine="markitdown"))

    report = pl.convert_tree(src, out, ocr_engine="auto", ocr_model="PP-StructureV3",
                             cloud_token=None, workers=1, skip_existing=False,
                             text_threshold=50, report_path=out / "report.json")

    assert report["succeeded"] == 0
    assert report["warned"] == 1
    assert (out / "a.md").exists()  # output is kept, not lost
    md = (out / "a.md").read_text(encoding="utf-8")
    assert "quality: suspect" in md
    assert report["warnings"][0]["file"] == "a.docx"
    assert any("near-empty" in r for r in report["warnings"][0]["reasons"])


def test_clean_output_not_warned(tmp_path, monkeypatch):
    src = _single_docx(tmp_path)
    out = tmp_path / "out"
    monkeypatch.setattr(pl, "classify", lambda p, text_threshold=50: "native")
    monkeypatch.setattr(pl, "convert_native",
                        lambda p: ConversionResult(text="正常的中文合同内容" * 5,
                                                   engine="markitdown"))

    report = pl.convert_tree(src, out, ocr_engine="auto", ocr_model="PP-StructureV3",
                             cloud_token=None, workers=1, skip_existing=False,
                             text_threshold=50, report_path=out / "report.json")
    assert report["succeeded"] == 1
    assert report["warned"] == 0
    assert "quality:" not in (out / "a.md").read_text(encoding="utf-8")


def test_quality_check_failure_is_non_fatal(tmp_path, monkeypatch):
    src = _single_docx(tmp_path)
    out = tmp_path / "out"
    monkeypatch.setattr(pl, "classify", lambda p, text_threshold=50: "native")
    monkeypatch.setattr(pl, "convert_native",
                        lambda p: ConversionResult(text="正常内容" * 10, engine="markitdown"))

    def boom(*a, **k):
        raise RuntimeError("quality checker bug")
    monkeypatch.setattr(pl, "assess", boom)

    report = pl.convert_tree(src, out, ocr_engine="auto", ocr_model="PP-StructureV3",
                             cloud_token=None, workers=1, skip_existing=False,
                             text_threshold=50, report_path=out / "report.json")
    # A buggy checker must never lose a successful conversion.
    assert report["succeeded"] == 1
    assert report["failed"] == 0
    assert (out / "a.md").exists()


def test_no_quality_check_disables_warnings(tmp_path, monkeypatch):
    src = _single_docx(tmp_path)
    out = tmp_path / "out"
    monkeypatch.setattr(pl, "classify", lambda p, text_threshold=50: "native")
    monkeypatch.setattr(pl, "convert_native",
                        lambda p: ConversionResult(text="x", engine="markitdown"))

    report = pl.convert_tree(src, out, ocr_engine="auto", ocr_model="PP-StructureV3",
                             cloud_token=None, workers=1, skip_existing=False,
                             text_threshold=50, report_path=out / "report.json",
                             quality_check=False)
    assert report["succeeded"] == 1
    assert report["warned"] == 0
    assert "quality:" not in (out / "a.md").read_text(encoding="utf-8")


def test_legacy_route_converts_like_native(tmp_path, monkeypatch):
    src = tmp_path / "in"
    src.mkdir()
    (src / "a.doc").write_bytes(b"\xd0\xcf\x11\xe0")
    out = tmp_path / "out"
    monkeypatch.setattr(pl, "classify", lambda p, text_threshold=50: "legacy")
    monkeypatch.setattr(pl, "convert_legacy",
                        lambda p: ConversionResult(text="正常的合同正文内容" * 5,
                                                   engine="legacy:com->markitdown"))

    report = pl.convert_tree(src, out, ocr_engine="auto", ocr_model="PP-StructureV3",
                             cloud_token=None, workers=1, skip_existing=False,
                             text_threshold=50, report_path=out / "report.json")
    assert report["succeeded"] == 1
    md = (out / "a.md").read_text(encoding="utf-8")
    assert "engine: legacy:com->markitdown" in md


def test_legacy_unavailable_is_skipped_with_hint(tmp_path, monkeypatch):
    src = tmp_path / "in"
    src.mkdir()
    (src / "a.doc").write_bytes(b"\xd0\xcf\x11\xe0")
    out = tmp_path / "out"
    monkeypatch.setattr(pl, "classify", lambda p, text_threshold=50: "legacy")

    def unavailable(p):
        raise LegacyConversionUnavailable("install WPS/Office or LibreOffice")
    monkeypatch.setattr(pl, "convert_legacy", unavailable)

    report = pl.convert_tree(src, out, ocr_engine="auto", ocr_model="PP-StructureV3",
                             cloud_token=None, workers=1, skip_existing=False,
                             text_threshold=50, report_path=out / "report.json")
    assert report["skipped_unsupported"] == 1
    assert report["failed"] == 0
    assert not (out / "a.md").exists()
    assert report["skipped"][0]["file"] == "a.doc"
    assert "LibreOffice" in report["skipped"][0]["reason"]


def test_same_stem_different_ext_do_not_collide(tmp_path, monkeypatch):
    src = tmp_path / "in"
    src.mkdir()
    (src / "report.docx").write_text("x", encoding="utf-8")
    (src / "report.txt").write_text("x", encoding="utf-8")
    out = tmp_path / "out"
    monkeypatch.setattr(pl, "classify", lambda p, text_threshold=50: "native")
    monkeypatch.setattr(pl, "convert_native",
                        lambda p: ConversionResult(text=f"内容来自 {p.name} " * 5,
                                                   engine="markitdown"))

    report = pl.convert_tree(src, out, ocr_engine="auto", ocr_model="PP-StructureV3",
                             cloud_token=None, workers=2, skip_existing=False,
                             text_threshold=50, report_path=out / "report.json")
    assert report["succeeded"] == 2
    # Colliding stems are disambiguated by keeping the original extension.
    assert (out / "report.docx.md").exists()
    assert (out / "report.txt.md").exists()
    assert "report.docx" in (out / "report.docx.md").read_text(encoding="utf-8")
    assert "report.txt" in (out / "report.txt.md").read_text(encoding="utf-8")


def test_unique_stem_keeps_clean_name(tmp_path, monkeypatch):
    src = tmp_path / "in"
    src.mkdir()
    (src / "report.docx").write_text("x", encoding="utf-8")
    out = tmp_path / "out"
    monkeypatch.setattr(pl, "classify", lambda p, text_threshold=50: "native")
    monkeypatch.setattr(pl, "convert_native",
                        lambda p: ConversionResult(text="正常的文档内容" * 5, engine="markitdown"))

    pl.convert_tree(src, out, ocr_engine="auto", ocr_model="PP-StructureV3",
                    cloud_token=None, workers=1, skip_existing=False,
                    text_threshold=50, report_path=out / "report.json")
    assert (out / "report.md").exists()
    assert not (out / "report.docx.md").exists()


def test_unsafe_asset_paths_are_skipped(tmp_path, monkeypatch):
    src = tmp_path / "in"
    src.mkdir()
    (src / "a.docx").write_text("x", encoding="utf-8")
    out = tmp_path / "out"
    monkeypatch.setattr(pl, "classify", lambda p, text_threshold=50: "native")
    monkeypatch.setattr(pl, "convert_native",
                        lambda p: ConversionResult(
                            text="正常的文档内容" * 5, engine="markitdown",
                            assets={"../evil.png": b"e", "sub/ok.png": b"o",
                                    "a/../../evil2.png": b"e2"}))

    pl.convert_tree(src, out, ocr_engine="auto", ocr_model="PP-StructureV3",
                    cloud_token=None, workers=1, skip_existing=False,
                    text_threshold=50, report_path=out / "report.json", keep_images=True)
    assert (out / "sub" / "ok.png").read_bytes() == b"o"          # safe asset written
    assert not (tmp_path / "evil.png").exists()                   # ../ escape blocked
    assert not (tmp_path / "evil2.png").exists()                  # a/../../ escape blocked


def test_mark_images_helper():
    from makeitdown.pipeline import _mark_images
    t = ('正文 <img src="imgs/seal.jpg" alt="Image"> 中间 ![cap](pic.png) 末尾 '
         '<div style="text-align: center;"><table>keep</table></div>')
    out, n = _mark_images(t)
    assert "<img" not in out
    assert "![" not in out
    assert "imgs/seal.jpg" not in out            # full path gone
    assert "〔图像：seal.jpg" in out               # html <img> -> marker by basename
    assert "〔图像：pic.png" in out                # md ![]() -> marker by basename
    assert "<table>keep</table>" in out           # table content preserved
    assert n == 2


def test_mark_images_html_alt_fallback_when_no_src():
    from makeitdown.pipeline import _mark_images
    out, n = _mark_images('<img alt="现场照片">')   # HTML img, alt but no src
    assert "〔图像：现场照片" in out                  # falls back to alt, not 未命名
    assert n == 1


def test_mark_images_falls_back_to_alt_then_placeholder():
    from makeitdown.pipeline import _mark_images
    out1, n1 = _mark_images("![说明]()")           # alt present, no path
    assert "〔图像：说明" in out1 and n1 == 1
    out2, n2 = _mark_images("<img>")               # no src attribute
    assert "〔图像：未命名" in out2 and n2 == 1


def test_mark_images_collapses_genuinely_empty_div():
    from makeitdown.pipeline import _mark_images
    out, n = _mark_images('<div style="x"></div>正文')   # empty for non-image reasons
    assert "<div" not in out and n == 0


def test_images_marked_by_default(tmp_path, monkeypatch):
    src = tmp_path / "in"
    src.mkdir()
    (src / "a.docx").write_text("x", encoding="utf-8")
    out = tmp_path / "out"
    monkeypatch.setattr(pl, "classify", lambda p, text_threshold=50: "native")
    text = "正文内容很长很长很长" * 5 + '\n\n<div style="text-align: center;"><img src="imgs/seal.jpg"></div>'
    monkeypatch.setattr(pl, "convert_native",
                        lambda p: ConversionResult(text=text, engine="markitdown",
                                                   assets={"imgs/seal.jpg": b"JPG"}))

    report = pl.convert_tree(src, out, ocr_engine="auto", ocr_model="PP-StructureV3",
                             cloud_token=None, workers=1, skip_existing=False,
                             text_threshold=50, report_path=out / "report.json")
    md = (out / "a.md").read_text(encoding="utf-8")
    assert "<img" not in md and "imgs/seal.jpg" not in md   # ref + full path gone
    assert "〔图像：seal.jpg" in md                          # placeholder marker left
    assert not (out / "imgs" / "seal.jpg").exists()          # bytes still not written
    assert report["images_omitted"] == 1
    saved = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert saved["images_omitted"] == 1


def test_keep_images_preserves_assets(tmp_path, monkeypatch):
    src = tmp_path / "in"
    src.mkdir()
    (src / "a.docx").write_text("x", encoding="utf-8")
    out = tmp_path / "out"
    monkeypatch.setattr(pl, "classify", lambda p, text_threshold=50: "native")
    text = "正文内容很长很长很长" * 5 + '\n\n<div style="text-align: center;"><img src="imgs/seal.jpg"></div>'
    monkeypatch.setattr(pl, "convert_native",
                        lambda p: ConversionResult(text=text, engine="markitdown",
                                                   assets={"imgs/seal.jpg": b"JPG"}))

    report = pl.convert_tree(src, out, ocr_engine="auto", ocr_model="PP-StructureV3",
                             cloud_token=None, workers=1, skip_existing=False,
                             text_threshold=50, report_path=out / "report.json", keep_images=True)
    md = (out / "a.md").read_text(encoding="utf-8")
    assert "<img" in md
    assert (out / "imgs" / "seal.jpg").read_bytes() == b"JPG"
    assert report["images_omitted"] == 0          # keep-images path does not mark/omit


class _ApplyStruct:
    """Fake structurer that pretends to add a heading and label the engine."""

    def restructure(self, text):
        return ("# 标题\n" + text, "llm-heads:deepseek-chat", None)


class _SpyStruct:
    def __init__(self):
        self.calls = 0

    def restructure(self, text):
        self.calls += 1
        return text, None, None


class _WarnStruct:
    def restructure(self, text):
        return text, None, "heading structuring skipped: Timeout"


def _ocr_tree(tmp_path, monkeypatch):
    src = tmp_path / "in"
    src.mkdir()
    (src / "b.png").write_bytes(b"\x00")
    monkeypatch.setattr(pl, "classify", lambda p, text_threshold=50: "ocr")

    class _Disp:
        def __init__(self, **k):
            pass

        def convert(self, p):
            return ConversionResult(text="标题\n" + "正常的文档内容" * 5,
                                    engine="local:pp-structurev3")

    monkeypatch.setattr(pl, "OCRDispatcher", _Disp)
    return src


def test_structurer_applied_on_ocr_route(tmp_path, monkeypatch):
    src = _ocr_tree(tmp_path, monkeypatch)
    out = tmp_path / "out"
    report = pl.convert_tree(src, out, ocr_engine="auto", ocr_model="PP-StructureV3",
                             cloud_token=None, workers=1, skip_existing=False,
                             text_threshold=50, report_path=out / "report.json",
                             structurer=_ApplyStruct())
    md = (out / "b.md").read_text(encoding="utf-8")
    assert "engine: local:pp-structurev3+llm-heads:deepseek-chat" in md
    assert "# 标题" in md
    assert report["structured"] == 1
    assert report["succeeded"] == 1


def test_structurer_not_called_on_native_route(tmp_path, monkeypatch):
    src = _single_docx(tmp_path)
    out = tmp_path / "out"
    monkeypatch.setattr(pl, "classify", lambda p, text_threshold=50: "native")
    monkeypatch.setattr(pl, "convert_native",
                        lambda p: ConversionResult(text="正常的文档内容" * 5,
                                                   engine="markitdown"))
    spy = _SpyStruct()
    report = pl.convert_tree(src, out, ocr_engine="auto", ocr_model="PP-StructureV3",
                             cloud_token=None, workers=1, skip_existing=False,
                             text_threshold=50, report_path=out / "report.json",
                             structurer=spy)
    assert spy.calls == 0
    assert report["structured"] == 0
    assert report["succeeded"] == 1


def test_structurer_warning_marks_warned_but_keeps_output(tmp_path, monkeypatch):
    src = _ocr_tree(tmp_path, monkeypatch)
    out = tmp_path / "out"
    report = pl.convert_tree(src, out, ocr_engine="auto", ocr_model="PP-StructureV3",
                             cloud_token=None, workers=1, skip_existing=False,
                             text_threshold=50, report_path=out / "report.json",
                             structurer=_WarnStruct())
    assert report["warned"] == 1
    assert (out / "b.md").exists()
    md = (out / "b.md").read_text(encoding="utf-8")
    assert "quality: suspect" in md
    assert any("heading structuring skipped" in r
               for r in report["warnings"][0]["reasons"])


def test_low_confidence_ocr_flagged_as_warned(tmp_path, monkeypatch):
    src = tmp_path / "in"
    src.mkdir()
    (src / "b.png").write_bytes(b"\x00")
    out = tmp_path / "out"
    monkeypatch.setattr(pl, "classify", lambda p, text_threshold=50: "ocr")

    class _Disp:
        def __init__(self, **k):
            pass

        def convert(self, p):
            return ConversionResult(text="正常的文档内容" * 5,
                                    engine="local:pp-structurev3",
                                    confidences=[0.99, 0.30, 0.95])

    monkeypatch.setattr(pl, "OCRDispatcher", _Disp)
    report = pl.convert_tree(src, out, ocr_engine="auto", ocr_model="PP-StructureV3",
                             cloud_token=None, workers=1, skip_existing=False,
                             text_threshold=50, report_path=out / "report.json")
    assert report["warned"] == 1
    md = (out / "b.md").read_text(encoding="utf-8")
    assert "quality: suspect" in md
    assert any("low-confidence" in r for r in report["warnings"][0]["reasons"])


def test_cross_check_reasons_reach_report(monkeypatch, tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    (src / "scan.pdf").write_bytes(b"%PDF fake")
    out = tmp_path / "out"

    monkeypatch.setattr(pipeline_mod, "classify", lambda p, text_threshold: "ocr")

    class _Disp:
        def __init__(self, *a, **k):
            assert k.get("cross_check") is True  # plumbing reached the dispatcher
        def convert(self, path):
            return ConversionResult(
                text="金额为500000元", engine="local:pp-structurev3 × mineru",
                pages=1, cross_check_reasons=["双OCR分歧 20.0%，含 1 处数字/日期位不一致（Paddle×MinerU）"],
            )

    monkeypatch.setattr(pipeline_mod, "OCRDispatcher", _Disp)

    report = pipeline_mod.convert_tree(
        src, out, ocr_engine="local", ocr_model=None, cloud_token=None,
        workers=1, skip_existing=False, text_threshold=50,
        report_path=out / "report.json", cross_check=True,
    )
    assert report["warned"] == 1
    assert report["warnings"][0]["reasons"][0].startswith("双OCR分歧")
    md = (out / "scan.pdf.md") if (out / "scan.pdf.md").exists() else (out / "scan.md")
    assert "quality: suspect" in md.read_text("utf-8")


def test_convert_tree_threads_consent_and_mode(monkeypatch, tmp_path):
    src = tmp_path / "in"; src.mkdir()
    (src / "scan.pdf").write_bytes(b"%PDF fake")
    out = tmp_path / "out"
    monkeypatch.setattr(pipeline_mod, "classify", lambda p, text_threshold: "ocr")

    captured = {}

    class _Disp:
        def __init__(self, *a, **k):
            captured.update(k)
        def convert(self, path):
            from makeitdown.models import ConversionResult
            return ConversionResult(text="结果", engine="cloud:paddleocr-vl-1.6", pages=1)

    monkeypatch.setattr(pipeline_mod, "OCRDispatcher", _Disp)
    pipeline_mod.convert_tree(
        src, out, ocr_engine="cloud", ocr_model=None, cloud_token="tok",
        workers=1, skip_existing=False, text_threshold=50,
        report_path=out / "report.json",
        cross_check=True, cross_check_mode="local", cloud_consent=True, mineru_token="mt",
    )
    assert captured["cross_check_mode"] == "local"
    assert captured["cloud_consent"] is True
    assert captured["mineru_token"] == "mt"


def test_skip_existing_skips_up_to_date_output(tmp_path, monkeypatch):
    src = tmp_path / "in"
    src.mkdir()
    f = src / "a.docx"
    f.write_text("x", encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    md = out / "a.md"
    md.write_text("old", encoding="utf-8")
    import os, time
    future = time.time() + 100
    os.utime(md, (future, future))  # output newer than source

    monkeypatch.setattr(pl, "classify", lambda p, text_threshold=50: "native")
    called = {"n": 0}
    def conv(p):
        called["n"] += 1
        return ConversionResult(text="# new", engine="markitdown")
    monkeypatch.setattr(pl, "convert_native", conv)

    report = pl.convert_tree(src, out, ocr_engine="auto", ocr_model="PP-StructureV3",
                             cloud_token=None, workers=1, skip_existing=True,
                             text_threshold=50, report_path=out / "report.json")
    assert called["n"] == 0
    assert report["skipped_existing"] == 1
    assert md.read_text(encoding="utf-8") == "old"


def _one_native_file(tmp_path, monkeypatch, content="正常的文档内容" * 5):
    src = tmp_path / "in"
    src.mkdir()
    (src / "a.docx").write_text("x", encoding="utf-8")
    monkeypatch.setattr(pl, "classify", lambda p, text_threshold=50: "native")
    monkeypatch.setattr(pl, "convert_native",
                        lambda p: ConversionResult(text="# ok\n\n" + content, engine="markitdown"))
    return src


def test_progress_lines_printed_to_stderr_by_default(tmp_path, monkeypatch, capsys):
    src = _one_native_file(tmp_path, monkeypatch)
    pl.convert_tree(src, tmp_path / "out", ocr_engine="auto", ocr_model="x",
                    cloud_token=None, workers=1, skip_existing=False,
                    text_threshold=50, report_path=tmp_path / "out" / "report.json")
    err = capsys.readouterr().err
    assert "[1/1] ✓ a.docx" in err


def test_progress_can_be_silenced(tmp_path, monkeypatch, capsys):
    src = _one_native_file(tmp_path, monkeypatch)
    pl.convert_tree(src, tmp_path / "out", ocr_engine="auto", ocr_model="x",
                    cloud_token=None, workers=1, skip_existing=False,
                    text_threshold=50, report_path=tmp_path / "out" / "report.json",
                    progress=False)
    err = capsys.readouterr().err
    assert "[1/" not in err


def test_progress_marks_failure_with_error(tmp_path, monkeypatch, capsys):
    src = tmp_path / "in"
    src.mkdir()
    (src / "bad.docx").write_text("x", encoding="utf-8")
    monkeypatch.setattr(pl, "classify", lambda p, text_threshold=50: "native")
    def boom(p): raise ValueError("broken file")
    monkeypatch.setattr(pl, "convert_native", boom)
    pl.convert_tree(src, tmp_path / "out", ocr_engine="auto", ocr_model="x",
                    cloud_token=None, workers=1, skip_existing=False,
                    text_threshold=50, report_path=tmp_path / "out" / "report.json")
    err = capsys.readouterr().err
    assert "[1/1] ✗ bad.docx" in err and "broken file" in err


def test_pipeline_routes_xls_to_anydoc(monkeypatch, tmp_path):
    (tmp_path / "in").mkdir()
    (tmp_path / "in" / "ledger.xls").write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1x")
    monkeypatch.setattr(convert_anydoc, "convert",
                        lambda p: ConversionResult(text="# 流水\n100000.00", engine="anydoc"))
    report = pl.convert_tree(
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
    report = pl.convert_tree(
        tmp_path / "in", tmp_path / "out",
        ocr_engine="local", ocr_model="", cloud_token=None, workers=1,
        skip_existing=False, text_threshold=50, report_path=tmp_path / "report.json",
        progress=False,
    )
    assert report["skipped_unsupported"] == 1
    assert report["skipped"] and "无法解析" in report["skipped"][0]["reason"]
