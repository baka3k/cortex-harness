"""Corpus anchor scan (plan 260924 phase-02/03 evidence, M3/M4 gates).

Runs the ANTLR worker over a real VB6 corpus (default: Bookworm Revamp1) and
prints per-anchor counts straight from the hydrated payload planes:

- redim[] rows (M4 gate: 26 ReDim Preserve sites on the corpus)
- with_targets[] rows incl. block_end_line coverage (nested-With support)
- ui_access[] member/state rows, with-target share (M3 gate: >=80% of
  With-block member rows attach to a target)
- instantiations[] (New) rows
- variables is_static / with_events / is_global flags
- functions exported (public surface)

Usage:
    python tests/scan_vb6_corpus_anchors.py --root /path/to/corpus [--json OUT]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

import tools.vb.vb_analyzer_base as base  # noqa: E402

JAVA_AVAILABLE = os.path.exists("/usr/bin/java") or os.environ.get("JAVA_HOME") is not None


def scan(root: str) -> dict:
    files = [
        str(path) for path in sorted(Path(root).iterdir())
        if path.suffix.lower() in {".bas", ".cls", ".frm", ".ctl", ".pag"}
    ]
    # source-text ground truth (raw regex counts over ALL files) — the worker
    # rejects duplicate `Attribute VB_Name` modules (pre-existing guard), so
    # ANTLR-reachable anchors are a subset of the source-text anchors
    import re

    text_redim = text_with = 0
    for path in files:
        try:
            content = Path(path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        text_redim += len(re.findall(r"(?i)\bReDim\b", content))
        text_with += len(re.findall(r"(?im)^\s*With\s", content))
    payloads = asyncio.run(base._parse_vb6_with_antlr_batch(
        parse_files=files,
        all_source_files=files,
        root=root,
        parse_fn=base._PARSER_FACTORY["vb6"],
        cache_dir=None,
        parse_cache=False,
        vb6_parser_engine="antlr",
        vb6_antlr_timeout_sec=600.0,
        vb6_antlr_workspace_timeout_ms=300000,
        verbose=False,
    ))
    redim_rows = [r for p in payloads for r in p.get("redim", [])]
    with_rows = [r for p in payloads for r in p.get("with_targets", [])]
    ui_rows = [r for p in payloads for r in p.get("ui_access", [])]
    inst_rows = [r for p in payloads for r in p.get("instantiations", [])]
    variables = [v for p in payloads for v in p.get("variables", [])]
    functions = [f for p in payloads for f in p.get("functions", [])]

    with_member_rows = [
        r for r in ui_rows if r.get("via_with")
    ]
    summary = {
        "files": len(payloads),
        "files_with_source_text_anchors": len(files),
        "source_text_redim": text_redim,
        "source_text_with": text_with,
        "redim_rows": len(redim_rows),
        "redim_preserve_rows": sum(1 for r in redim_rows if r.get("preserve")),
        "with_targets": len(with_rows),
        "with_targets_with_block_end": sum(
            1 for r in with_rows if r.get("block_end_line", 0) >= r.get("line", 0)
        ),
        "ui_access_rows": len(ui_rows),
        "ui_member_rows": sum(1 for r in ui_rows if r.get("member")),
        "ui_state_rows": sum(1 for r in ui_rows if not r.get("member")),
        "ui_with_rows": len(with_member_rows),
        "instantiations": len(inst_rows),
        "static_locals": sum(1 for v in variables if getattr(v, "is_static", False)),
        "with_events_vars": sum(1 for v in variables if getattr(v, "with_events", False)),
        "global_vars": sum(1 for v in variables if getattr(v, "is_global", False)),
        "exported_procs": sum(1 for f in functions if not getattr(f, "is_private", False)),
        "private_procs": sum(1 for f in functions if getattr(f, "is_private", False)),
    }
    # M3 with-target attach share: member rows in a With block whose proc has
    # a with_target covering the row's line (raw resolution preview)
    attachable = 0
    for payload in payloads:
        targets = payload.get("with_targets", [])
        if not targets:
            continue
        for row in payload.get("ui_access", []):
            if not row.get("via_with"):
                continue
            if any(
                t["proc"] == row["proc"] and t["line"] <= row["line"] <= t["block_end_line"]
                for t in targets
            ):
                attachable += 1
    summary["with_member_rows_attachable"] = attachable
    summary["with_member_attach_share"] = (
        round(100.0 * attachable / len(with_member_rows), 1) if with_member_rows else 100.0
    )
    summary["redim_by_module"] = dict(Counter(
        r["proc"] for r in redim_rows
    ).most_common(10))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="VB6 corpus anchor scan")
    parser.add_argument("--root", required=True)
    parser.add_argument("--json", default="")
    args = parser.parse_args()
    if not JAVA_AVAILABLE:
        print("java not available", file=sys.stderr)
        return 2
    summary = scan(args.root)
    print(json.dumps(summary, indent=2))
    if args.json:
        Path(args.json).write_text(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
