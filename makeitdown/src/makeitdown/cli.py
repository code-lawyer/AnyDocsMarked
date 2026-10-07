import argparse
import json
import os
import re
import sys
from pathlib import Path

from .ocr_local import LocalOCR
from .ocr_mineru import MinerULocal
from .pipeline import convert_tree
from .quality import QualityThresholds
from .structure import HeadingStructurer


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="makeitdown",
        description="Batch convert documents to high-fidelity Markdown for LLM knowledge bases.",
    )
    p.add_argument("input", help="input directory to scan recursively")
    p.add_argument("-o", "--output", help="output directory (default: <input>_md)")
    p.add_argument("--ocr-engine", choices=["local", "cloud", "auto"], default="cloud",
                   help="OCR backend (default: cloud — uploads documents, needs --cloud-consent; "
                        "use 'local' to keep documents on-device)")
    p.add_argument("--ocr-model", default=None,
                   help="OCR model; applies to whichever backend runs "
                        "(local default PP-StructureV3, cloud default PaddleOCR-VL-1.6)")
    p.add_argument("--cloud-token", default=None,
                   help="AI Studio token (default: env PADDLEOCR_AISTUDIO_TOKEN)")
    p.add_argument("--workers", type=int, default=os.cpu_count() or 4,
                   help="number of concurrent workers")
    p.add_argument("--skip-existing", action="store_true",
                   help="skip files whose .md output is newer than the source "
                        "(mtime-based; drop the flag to force a full re-convert)")
    p.add_argument("--text-threshold", type=int, default=50,
                   help="avg chars/page below which a PDF is treated as scanned")
    p.add_argument("--pdf-reader-inspector", action="store_true",
                   help="read PDFs page by page via the optional pdf-inspector extra "
                        "(default off; a missing extra or extract error falls back and warns)")
    p.add_argument("--report", default=None, help="path to report.json")
    p.add_argument("--strict", action="store_true",
                   help="exit non-zero if any file failed (for scripts/CI; "
                        "default keeps exit 0 so a partial batch is not treated as fatal)")
    p.add_argument("--no-quality-check", dest="quality_check", action="store_false",
                   help="disable output quality checks (treat all output as clean)")
    p.add_argument("--keep-images", action="store_true",
                   help="keep images extracted from scans (default: text-only output)")
    # Defaults sourced from QualityThresholds so there is one source of truth.
    qt = QualityThresholds()
    p.add_argument("--ocr-cross-check", action="store_true",
                   help="run a second OCR engine (MinerU) and flag disagreements "
                        "(opt-in; default off)")
    p.add_argument("--cloud-consent", action="store_true",
                   help="consent to uploading documents/text to external OCR or LLM services")
    p.add_argument("--cross-check-mode", choices=["cloud", "local", "auto"], default="cloud",
                   help="MinerU verifier mode for --ocr-cross-check (default: cloud)")
    p.add_argument("--warn-cross-check-ratio", type=float, default=qt.cross_check_disagreement_ratio,
                   help="warn if dual-OCR disagreement ratio exceeds this (0-1)")
    p.add_argument("--warn-min-chars", type=int, default=qt.min_chars,
                   help="warn if non-whitespace char count is below this")
    p.add_argument("--warn-min-chars-per-page", type=int, default=qt.min_chars_per_page,
                   help="warn if avg chars/page (multi-page docs) is below this")
    p.add_argument("--warn-garbled-ratio", type=float, default=qt.garbled_ratio,
                   help="warn if garbled-character ratio exceeds this (0-1)")
    p.add_argument("--warn-repeat-count", type=int, default=qt.repeat_count,
                   help="warn if a line repeats more than this many times")
    p.add_argument("--warn-min-confidence", type=float, default=qt.min_confidence,
                   help="warn if any OCR region scores below this (0-1, when known)")
    # LLM heading-structure reconstruction (opt-in; OCR output only).
    p.add_argument("--structure-headings", action="store_true",
                   help="rebuild heading levels of OCR output via an LLM "
                        "(needs --llm-base-url/--llm-model/--llm-api-key)")
    p.add_argument("--llm-base-url", default=None,
                   help="OpenAI-compatible endpoint (default: env MAKEITDOWN_LLM_BASE_URL)")
    p.add_argument("--llm-model", default=None,
                   help="LLM model name (default: env MAKEITDOWN_LLM_MODEL)")
    p.add_argument("--llm-api-key", default=None,
                   help="LLM API key (default: env MAKEITDOWN_LLM_API_KEY)")
    p.add_argument("--llm-max-heading-len", type=int, default=80,
                   help="max length of a candidate heading line")
    p.add_argument("--llm-max-lines", type=int, default=1500,
                   help="skip structuring if candidate lines exceed this")
    p.add_argument("--llm-max-heading-ratio", type=float, default=0.35,
                   help="if headings exceed this fraction, treat doc as unstructured")
    return p


_ENV_RE = re.compile(r"""(?:environ\.get\(|environ\[|getenv\()\s*["']([A-Z][A-Z0-9_]{2,})""")


def _read_env_names() -> list[str]:
    """本包源码里实际读取的环境变量名（自查同模块源码，装机布局下也可用）。
    自动派生，故新增一个 env 读取会自动进入 --list-knobs、被能力契约完备性核对。"""
    names: set[str] = set()
    for p in Path(__file__).parent.glob("*.py"):
        names |= set(_ENV_RE.findall(p.read_text(encoding="utf-8")))
    return sorted(names)


def _list_knobs_json() -> str:
    """本模块所有决策旋钮：CLI 标志（从 parser 派生）+ 读取的环境变量（自查源码）。
    供能力接线契约的完备性核对（每个旋钮须登记 FLOOR/CHOICE/OUT）。"""
    parser = _build_parser()
    # argparse 内建的 -h/--help 不是决策旋钮，在源头滤掉——让"什么算旋钮"只有一处定义，
    # 消费方（能力契约完备性核对）不必再各维护一份忽略集。
    builtins = {"-h", "--help"}
    flags = sorted({opt for a in parser._actions for opt in a.option_strings} - builtins)
    return json.dumps({"module": "makeitdown", "flags": flags, "env": _read_env_names()},
                      ensure_ascii=False)


def main(argv: list[str] | None = None) -> int:
    raw = sys.argv[1:] if argv is None else argv
    if "--list-knobs" in raw:
        print(_list_knobs_json())
        return 0
    args = _build_parser().parse_args(argv)
    input_dir = Path(args.input)
    if not input_dir.is_dir():
        print(f"error: 输入路径不是目录：{input_dir}\n"
              f"makeitdown 只接受目录输入（递归转换其中所有文件）；"
              f"若要转单个文件，请把它放进一个目录再指向该目录。",
              file=sys.stderr)
        return 2
    output_dir = Path(args.output) if args.output else Path(f"{input_dir}_md")
    input_resolved = input_dir.resolve()
    output_resolved = output_dir.resolve()
    if output_resolved == input_resolved or output_resolved.is_relative_to(input_resolved):
        print("error: 输出目录不能位于输入目录内部，否则重跑时会递归转换既有输出。", file=sys.stderr)
        return 2
    token = args.cloud_token or os.environ.get("PADDLEOCR_AISTUDIO_TOKEN")
    report_path = Path(args.report) if args.report else output_dir / "report.json"
    from .cloud_consent import CLOUD_NOTICE, has_consent
    consented = has_consent(args.cloud_consent)

    thresholds = QualityThresholds(
        min_chars=args.warn_min_chars,
        min_chars_per_page=args.warn_min_chars_per_page,
        garbled_ratio=args.warn_garbled_ratio,
        repeat_count=args.warn_repeat_count,
        min_confidence=args.warn_min_confidence,
    )

    structurer = None
    if args.structure_headings:
        base_url = args.llm_base_url or os.environ.get("MAKEITDOWN_LLM_BASE_URL")
        model = args.llm_model or os.environ.get("MAKEITDOWN_LLM_MODEL")
        api_key = args.llm_api_key or os.environ.get("MAKEITDOWN_LLM_API_KEY")
        if not (base_url and model and api_key):
            print(
                "error: --structure-headings requires --llm-base-url / --llm-model / "
                "--llm-api-key (or env MAKEITDOWN_LLM_BASE_URL / MAKEITDOWN_LLM_MODEL / "
                "MAKEITDOWN_LLM_API_KEY)",
                file=sys.stderr,
            )
            return 2
        structurer = HeadingStructurer(
            base_url, api_key, model,
            max_heading_len=args.llm_max_heading_len,
            max_input_lines=args.llm_max_lines,
            max_heading_ratio=args.llm_max_heading_ratio,
            cloud_consent=consented,
        )

    mineru_token = os.environ.get("MINERU_API_TOKEN")
    # Will a cloud engine ACTUALLY run? auto prefers local when available.
    primary_is_cloud = args.ocr_engine == "cloud" or (
        args.ocr_engine == "auto" and not LocalOCR.is_available())
    verifier_is_cloud = args.ocr_cross_check and (
        args.cross_check_mode == "cloud" or (
            args.cross_check_mode == "auto" and not MinerULocal.is_available()))
    llm_will_run = args.structure_headings
    cloud_will_run = primary_is_cloud or verifier_is_cloud or llm_will_run
    if cloud_will_run and not consented:
        print(CLOUD_NOTICE, file=sys.stderr)
        if primary_is_cloud or llm_will_run:
            return 2  # primary/LLM needs external processing → cannot proceed without consent
        # verifier-only cloud without consent: the verifier will skip cleanly (no upload); proceed
    elif cloud_will_run and consented:
        print("使用外部处理服务：文档或其文本将上传至配置的服务。", file=sys.stderr)

    report = convert_tree(
        input_dir, output_dir,
        ocr_engine=args.ocr_engine,
        ocr_model=args.ocr_model,
        cloud_token=token,
        workers=args.workers,
        skip_existing=args.skip_existing,
        text_threshold=args.text_threshold,
        report_path=report_path,
        quality_check=args.quality_check,
        quality_thresholds=thresholds,
        keep_images=args.keep_images,
        structurer=structurer,
        cross_check=args.ocr_cross_check,
        cross_check_ratio=args.warn_cross_check_ratio,
        cross_check_mode=args.cross_check_mode,
        cloud_consent=args.cloud_consent,
        mineru_token=mineru_token,
        pdf_reader_inspector=args.pdf_reader_inspector,
    )

    structured = (f"structured={report.get('structured', 0)} "
                  if args.structure_headings else "")
    print(
        f"Done. succeeded={report['succeeded']} warned={report['warned']} "
        f"{structured}failed={report['failed']} "
        f"skipped_existing={report['skipped_existing']} "
        f"skipped_unsupported={report['skipped_unsupported']}"
    )
    if report["warned"]:
        print(f"{report['warned']} file(s) flagged for quality. See {report_path}.",
              file=sys.stderr)
    if report.get("skipped"):
        print(f"{len(report['skipped'])} file(s) need an external converter "
              f"(see {report_path} for how to convert them).", file=sys.stderr)
    if report["failed"]:
        print(f"See {report_path} for {report['failed']} failure(s).", file=sys.stderr)
        if args.strict:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
