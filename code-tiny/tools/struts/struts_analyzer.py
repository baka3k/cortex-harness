from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional, Sequence

from tools.struts.pipeline import run_struts_analysis


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apache Struts 2 semantic analyzer", allow_abbrev=False)
    parser.add_argument("--root", required=True, help="Project root to analyze")
    parser.add_argument("--project-id", default="struts-project")
    parser.add_argument("--project-name", default="")
    parser.add_argument("--selected-path", action="append", default=[], help="Limit scanning to a relative path")
    parser.add_argument("--output", help="Write the semantic graph JSON to this file")
    parser.add_argument("--compact", action="store_true", help="Emit compact JSON")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    result = run_struts_analysis(
        root=args.root,
        project_id=args.project_id,
        project_name=args.project_name,
        selected_paths=args.selected_path,
    )
    payload = result.to_dict()
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":") if args.compact else None,
        indent=None if args.compact else 2,
    )
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(serialized + "\n", encoding="utf-8")
    else:
        print(serialized)
    return 1 if any(item.severity == "error" for item in result.diagnostics) else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
