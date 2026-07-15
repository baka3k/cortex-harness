from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Optional, Sequence

_ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from tools.aspnet_core.pipeline import run_aspnet_core_analysis
from tools.common.aspnet.cli_runtime import (
    add_shared_arguments, apply_graph, fail_code, load_manifest, write_outputs,
)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ASP.NET Core semantic overlay analyzer", allow_abbrev=False,
    )
    add_shared_arguments(parser, output_flag="--aspnet-core-preview-output")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    root = os.path.realpath(os.path.abspath(args.root))
    if not os.path.isdir(root):
        print(f"Root not found: {root}", file=sys.stderr)
        return 2
    project_id = args.project_id or os.path.basename(root)
    result = run_aspnet_core_analysis(
        root=root,
        project_id=project_id,
        project_name=args.project_name or project_id,
        semantic_mode=args.semantic,
        deleted_paths=load_manifest(args.deleted_files_manifest, root),
        selected_paths=load_manifest(args.changed_files_manifest, root) if args.incremental else (),
        worker_project_path=args.roslyn_worker_project or None,
        verbose=args.verbose,
    )
    write_outputs(args, result)
    if not args.quiet:
        print(
            "[aspnet_core] modules=%d facts=%d relationships=%d diagnostics=%d coverage=%s"
            % (len(result.modules), len(result.facts), len(result.relationships), len(result.diagnostics), result.coverage_status)
        )
    code = fail_code(args, result)
    if code or args.dry_run:
        return code
    try:
        summary = asyncio.run(apply_graph(args, result))
    except Exception as exc:
        print(f"[aspnet_core] graph write failed: {exc}", file=sys.stderr)
        return 3
    if args.verbose:
        print(f"[aspnet_core] graph={summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
