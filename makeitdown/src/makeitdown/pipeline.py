import json
import hashlib
import os
import tempfile
import re
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from . import convert_anydoc as _convert_anydoc_mod
from .convert_legacy import convert as convert_legacy
from .convert_native import convert as convert_native
from .convert_ocr import OCRDispatcher
from .frontmatter import build_frontmatter, prepend_frontmatter
from .models import ConversionUnavailable
from .quality import QualityThresholds, assess
from .router import IGNORED_FILENAMES, classify


def convert_anydoc(src: Path):
    # Thin wrapper, not `from .convert_anydoc import convert as convert_anydoc`:
    # a direct function import binds the object at module-import time, so
    # `monkeypatch.setattr(convert_anydoc, "convert", ...)` in tests (which
    # patches the makeitdown.convert_anydoc submodule) would silently miss —
    # this indirection does the attribute lookup at call time instead.
    return _convert_anydoc_mod.convert(src)


# 进度行状态字形（打到 stderr，供长任务时人/agent 感知进度；见 SKILL.md 长任务模式）。
_STATUS_GLYPH = {
    "succeeded": "✓", "warned": "⚠", "failed": "✗",
    "skipped_existing": "=", "skipped_unsupported": "→",
}


def _progress_line(k: int, total: int, status: str, rel: Path,
                   detail, elapsed: float) -> str:
    line = f"[{k}/{total}] {_STATUS_GLYPH.get(status, '?')} {rel.as_posix()}"
    if status == "failed":
        return line + f" — {detail}"
    if status == "skipped_existing":
        return line + "（已最新，跳过）"
    if status == "skipped_unsupported":
        return line + "（需外部转换器，见 report）"
    return line + f" ({elapsed:.1f}s)"  # succeeded / warned


def _iter_files(input_dir: Path) -> list[Path]:
    return sorted(p for p in input_dir.rglob("*")
                 if p.is_file() and p.name not in IGNORED_FILENAMES)


def _is_up_to_date(src: Path, md: Path) -> bool:
    if not md.exists() or md.stat().st_mtime < src.stat().st_mtime:
        return False
    # New outputs carry a source hash, so copied/restored mtimes cannot make a
    # changed legal document look current. Legacy outputs fall back to mtime.
    try:
        head = md.read_text(encoding="utf-8", errors="replace")[:4096]
        match = re.search(r"(?m)^source_sha256:\s*([0-9a-f]{64})\s*$", head)
        return match.group(1) == _sha256_file(src) if match else True
    except OSError:
        return False


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_write_text(path: Path, text: str) -> None:
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


_IMG_HTML_RE = re.compile(r"<img\b[^>]*?>", re.IGNORECASE)
_IMG_MD_RE = re.compile(r"!\[([^\]]*)\]\(([^)]*)\)")
_IMG_SRC_RE = re.compile(r"""src\s*=\s*["']([^"']*)["']""", re.IGNORECASE)
_IMG_ALT_RE = re.compile(r"""alt\s*=\s*["']([^"']*)["']""", re.IGNORECASE)
_EMPTY_DIV_RE = re.compile(r"<div\b[^>]*>\s*</div>", re.IGNORECASE)


def _image_marker(name: str) -> str:
    return f"〔图像：{name} —— 已省略未保留，请查原件〕"


def _basename_or(path: str, alt: str) -> str:
    """Filename handle for the marker: basename of path, else alt, else 未命名."""
    if path:
        base = path.replace("\\", "/").rsplit("/", 1)[-1].strip()
        if base:
            return base
    alt = (alt or "").strip()
    return alt or "未命名"


def _mark_images(text: str) -> tuple[str, int]:
    """Replace image references with a traceable placeholder marker instead of
    deleting them, so _md records that an image existed (and its filename) even
    when the bytes are not kept. Returns (marked_text, n_marked)."""
    count = 0

    def _md_sub(m: "re.Match[str]") -> str:
        nonlocal count
        count += 1
        return _image_marker(_basename_or(m.group(2), m.group(1)))

    def _html_sub(m: "re.Match[str]") -> str:
        nonlocal count
        count += 1
        src_m = _IMG_SRC_RE.search(m.group(0))
        alt_m = _IMG_ALT_RE.search(m.group(0))
        return _image_marker(_basename_or(
            src_m.group(1) if src_m else "",
            alt_m.group(1) if alt_m else ""))

    text = _IMG_MD_RE.sub(_md_sub, text)
    text = _IMG_HTML_RE.sub(_html_sub, text)
    prev = None
    while prev != text:
        prev = text
        text = _EMPTY_DIV_RE.sub("", text)
    return text, count


def _is_safe_asset_rel(rel: str) -> bool:
    """Reject asset paths that would escape the output directory.

    Asset keys come from the OCR engine (cloud API / paddle); an absolute path
    or one containing '..' could write outside the per-document folder.
    """
    p = Path(rel)
    return not p.is_absolute() and ".." not in p.parts


def _write_output(out_md: Path, result, source_sha256: str, source_rel: str, source_type: str,
                  warnings: list[str] | None = None):
    out_md.parent.mkdir(parents=True, exist_ok=True)
    fm = build_frontmatter(
        source=source_rel,
        source_type=source_type,
        engine=result.engine,
        pages=result.pages,
        converted_at=datetime.now().isoformat(timespec="seconds"),
        warnings=warnings,
        source_sha256=source_sha256,
        content_sha256=hashlib.sha256(result.text.encode("utf-8")).hexdigest(),
    )
    _atomic_write_text(out_md, prepend_frontmatter(result.text, fm))
    for rel, data in result.assets.items():
        if not _is_safe_asset_rel(rel):
            continue  # skip path-escaping assets rather than write outside output
        asset_path = out_md.parent / rel
        asset_path.parent.mkdir(parents=True, exist_ok=True)
        asset_path.write_bytes(data)


def convert_tree(
    input_dir: Path,
    output_dir: Path,
    *,
    ocr_engine: str,
    ocr_model: str,
    cloud_token: str | None,
    workers: int,
    skip_existing: bool,
    text_threshold: int,
    report_path: Path,
    quality_check: bool = True,
    quality_thresholds: QualityThresholds | None = None,
    keep_images: bool = False,
    structurer=None,
    cross_check: bool = False,
    cross_check_ratio: float = 0.1,
    cross_check_mode: str = "cloud",
    cloud_consent: bool = False,
    mineru_token: str | None = None,
    progress: bool = True,
) -> dict:
    input_dir = Path(input_dir)
    output_dir = Path(output_dir)
    input_resolved = input_dir.resolve()
    output_resolved = output_dir.resolve()
    if output_resolved == input_resolved or output_resolved.is_relative_to(input_resolved):
        raise ValueError("output_dir must not be inside input_dir")
    dispatcher = OCRDispatcher(
        engine=ocr_engine, model=ocr_model, token=cloud_token,
        cross_check=cross_check, cross_check_ratio=cross_check_ratio,
        cross_check_mode=cross_check_mode, cloud_consent=cloud_consent,
        mineru_token=mineru_token,
    )

    report = {
        "succeeded": 0,
        "warned": 0,
        "structured": 0,
        "failed": 0,
        "skipped_existing": 0,
        "skipped_unsupported": 0,
        "images_omitted": 0,
        "failures": [],
        "warnings": [],
        "skipped": [],
    }

    files = _iter_files(input_dir)
    if not files:
        print(f"warning: {input_dir} 下没有找到任何文件——将产出空 report（0 个转换目标）。",
              file=sys.stderr, flush=True)
    # Two sources with the same stem but different extensions (e.g. report.pdf
    # and report.docx) would both map to report.md and overwrite each other.
    # Detect those collisions up front and disambiguate by keeping the original
    # extension (report.pdf.md); unique stems keep the clean name.
    md_counts = Counter(
        f.relative_to(input_dir).with_suffix(".md").as_posix() for f in files
    )

    def _out_md_for(rel: Path) -> Path:
        base = rel.with_suffix(".md")
        if md_counts[base.as_posix()] > 1:
            return output_dir / rel.parent / (rel.name + ".md")
        return output_dir / base

    def _quality_reasons(result, source_type: str) -> list[str]:
        # A buggy quality checker must never lose a successful conversion.
        if not quality_check:
            return []
        try:
            return assess(result.text, source_type=source_type,
                          pages=result.pages, thresholds=quality_thresholds,
                          confidences=result.confidences)
        except Exception:
            return []

    def handle(src: Path):
        rel = src.relative_to(input_dir)
        out_md = _out_md_for(rel)
        # Cheap stat check first so re-runs don't open (and decode) files just to skip them.
        if skip_existing and _is_up_to_date(src, out_md):
            return ("skipped_existing", rel, None, False, 0)
        route = classify(src, text_threshold=text_threshold)
        if route == "unsupported":
            # Non-empty detail so this lands in report["skipped"] (see the
            # `and detail` guard below), not just the skipped_unsupported count —
            # a downstream source-level audit (e.g. lawiki's reconcile.py) reads
            # that list to catch source files that never entered the output dir.
            return ("skipped_unsupported", rel,
                    f"unsupported file type ({src.suffix or 'no extension'})", False, 0)
        try:
            # Hash both sides of conversion. If a synced folder/scanner replaces
            # the source mid-run, never bind the output to an ambiguous snapshot.
            source_hash_before = _sha256_file(src)
            source_type = src.suffix.lstrip(".")
            struct_reasons: list[str] = []
            structured_ok = False
            if route == "native":
                result = convert_native(src)
            elif route == "legacy":
                result = convert_legacy(src)
            elif route == "anydoc":
                result = convert_anydoc(src)
            else:
                result = dispatcher.convert(src)
                # OCR output is flat; optionally rebuild heading levels via LLM.
                # A structurer bug must never lose a successful conversion.
                if structurer is not None:
                    try:
                        new_text, suffix, warn = structurer.restructure(result.text)
                        result.text = new_text
                        if suffix:
                            result.engine = f"{result.engine}+{suffix}"
                            structured_ok = True
                        if warn:
                            struct_reasons.append(warn)
                    except Exception:
                        pass
            n_omitted = 0
            if not keep_images:
                result.text, n_omitted = _mark_images(result.text)
                result.assets = {}
            cc_reasons = result.cross_check_reasons or []
            reasons = struct_reasons + cc_reasons + _quality_reasons(result, source_type)
            if _sha256_file(src) != source_hash_before:
                raise RuntimeError("source changed during conversion; retry this file")
            _write_output(out_md, result, source_hash_before, rel.as_posix(), source_type,
                          warnings=reasons)
            if reasons:
                return ("warned", rel, reasons, structured_ok, n_omitted)
            return ("succeeded", rel, None, structured_ok, n_omitted)
        except ConversionUnavailable as e:
            # Recognized but no converter available: skip knowingly with a hint.
            return ("skipped_unsupported", rel, str(e), False, 0)
        except Exception as e:  # never abort the batch
            return ("failed", rel, f"{type(e).__name__}: {e}", False, 0)

    total = len(files)

    def _timed(src):
        start = time.monotonic()
        result = handle(src)  # 5-tuple
        return (*result, time.monotonic() - start)

    completed = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for future in as_completed(pool.submit(_timed, src) for src in files):
            status, rel, detail, structured, images_omitted, elapsed = future.result()
            completed += 1
            if progress:
                print(_progress_line(completed, total, status, rel, detail, elapsed),
                      file=sys.stderr, flush=True)
            report[status] += 1
            report["images_omitted"] += images_omitted
            if structured:
                report["structured"] += 1
            if status == "failed":
                report["failures"].append({"file": rel.as_posix(), "error": detail})
            elif status == "warned":
                report["warnings"].append({"file": rel.as_posix(), "reasons": detail})
            elif status == "skipped_unsupported" and detail:
                report["skipped"].append({"file": rel.as_posix(), "reason": detail})

    report_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(report_path, json.dumps(report, ensure_ascii=False, indent=2))
    return report
