"""Tiny CLI for manual use and testing (the MCP server is the agent-facing entry).

    rag-retriever index <path> [--no-recursive]
    rag-retriever search "<query>" [-k 5]
    rag-retriever list
    rag-retriever stats
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import replace
from pathlib import Path

from .config import Config, split_csv
from .pipeline import Retriever

_FLAG_RE = re.compile(r"""add_argument\(\s*["'](-{1,2}[a-zA-Z][\w-]*)""")
_ENV_RE = re.compile(r"""(?:environ\.get\(|environ\[|getenv\(|_env\w*\()\s*["']([A-Z][A-Z0-9_]{2,})""")


def _list_knobs_json() -> str:
    """本模块所有决策旋钮：CLI 标志 + 读取的环境变量（自查本包源码，装机布局下也可用）。
    自动派生——新增一个旋钮会自动进入 --list-knobs，被能力契约完备性核对。"""
    pkg = Path(__file__).parent
    flags: set[str] = set()
    env: set[str] = set()
    for p in pkg.glob("*.py"):
        src = p.read_text(encoding="utf-8")
        flags |= set(_FLAG_RE.findall(src))
        env |= set(_ENV_RE.findall(src))
    return json.dumps({"module": "rag-retriever", "flags": sorted(flags), "env": sorted(env)},
                      ensure_ascii=False)


def main() -> None:
    # Emit UTF-8 regardless of platform locale, so piped JSON stays valid
    # (Windows consoles otherwise default to GBK/cp1252 and corrupt non-ASCII).
    # stderr too: Chinese error messages (readable RuntimeErrors, the offline
    # heads-up in embed.py) otherwise get backslashreplace-escaped into
    # unreadable \uXXXX when a caller captures stderr via a pipe (subprocess
    # capture_output — exactly what install.py's offline probe and lawiki's
    # rag.py wrapper both do), defeating the point of making them readable.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    if "--list-knobs" in sys.argv[1:]:
        print(_list_knobs_json())
        return

    parser = argparse.ArgumentParser(prog="rag-retriever")
    parser.add_argument(
        "--data-dir",
        help="vector-store directory (per-case isolation); "
        "overrides RAG_DATA_DIR. Place before the subcommand.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_index = sub.add_parser("index", help="index a file or folder")
    p_index.add_argument("path")
    p_index.add_argument("--no-recursive", action="store_true")
    p_index.add_argument(
        "--source-root",
        help="store each file's source as a POSIX path relative to this root "
        "(e.g. the case dir, yielding `_md/合同/采购.md`); default: absolute path",
    )
    p_index.add_argument(
        "--metadata-fields",
        help="comma-separated frontmatter fields to carry through as per-hit "
        "metadata (e.g. quality,source_type); overrides RAG_METADATA_FIELDS",
    )
    p_index.add_argument(
        "--exclude",
        help="comma-separated filename globs to skip (e.g. report.json,*.tmp)",
    )

    p_search = sub.add_parser("search", help="search indexed documents")
    p_search.add_argument("query")
    p_search.add_argument("-k", type=int, default=5)
    p_search.add_argument(
        "--json", action="store_true",
        help="emit hits as a JSON array (for programmatic consumers)",
    )
    p_search.add_argument(
        "--filter", dest="source_prefix", default=None,
        help="scope search to sources under this path prefix (e.g. a case dir)",
    )
    p_search.add_argument(
        "--show-parent", action="store_true",
        help="also print each hit's enclosing parent block (small-to-big context)",
    )

    sub.add_parser("list", help="list indexed documents")
    sub.add_parser("stats", help="show status")

    p_doctor = sub.add_parser(
        "doctor", help="check (and optionally repair) index/manifest integrity"
    )
    p_doctor.add_argument(
        "--fix", action="store_true",
        help="rebuild the manifest from the table if they have drifted",
    )

    args = parser.parse_args()
    cfg = Config.load()
    if args.data_dir:
        cfg = replace(cfg, data_dir=Path(args.data_dir))
    mf = getattr(args, "metadata_fields", None)
    if mf:
        cfg = replace(cfg, metadata_fields=split_csv(mf))
    r = Retriever(cfg)

    if args.cmd == "index":
        result = r.index_path(
            args.path, recursive=not args.no_recursive,
            source_root=args.source_root, exclude=split_csv(args.exclude or ""),
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        # Surface a partial batch loudly: skipped files are isolated (batch still
        # completes, exit 0 — degradation over abort), but the caller must see it
        # rather than mistake a partial index for a clean one.
        if result["files_skipped"]:
            print(f"{result['files_skipped']} file(s) skipped and NOT indexed "
                  f"(see 'skipped' above for reasons).", file=sys.stderr)
    elif args.cmd == "search":
        hits = r.search(args.query, k=args.k, source_prefix=args.source_prefix)
        if args.json:
            print(json.dumps(hits, ensure_ascii=False, indent=2))
            return
        if not hits:
            print("No relevant passages found.")
            return
        for i, h in enumerate(hits, 1):
            print(f"\n[{i}] {h['source']} (chunk {h['ord']}, score {h['score']})")
            print(h["text"])
            if args.show_parent and h.get("parent_text"):
                print(f"--- parent ---\n{h['parent_text']}")
    elif args.cmd == "list":
        print(json.dumps(r.list_sources(), ensure_ascii=False, indent=2))
    elif args.cmd == "stats":
        print(json.dumps(r.stats(), ensure_ascii=False, indent=2))
    elif args.cmd == "doctor":
        print(json.dumps(r.doctor(fix=args.fix), ensure_ascii=False, indent=2))
    else:  # pragma: no cover
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
