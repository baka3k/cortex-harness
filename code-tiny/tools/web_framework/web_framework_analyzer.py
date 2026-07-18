from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Optional, Sequence, Tuple

_ROOT_DIR = Path(__file__).resolve().parents[2]
if str(_ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(_ROOT_DIR))

from tools.graph.cli import add_graph_provider_args, create_graph_driver_from_args, prepare_graph_args  # noqa: E402
from tools.graph.writer.web_framework_writer import WebFrameworkWriter  # noqa: E402
from tools.web_framework.pipeline import analyze_project  # noqa: E402


def _manifest(path: str) -> Tuple[str, ...]:
    if not path:
        return ()
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    values = payload.get("paths", ()) if isinstance(payload, dict) else payload
    return tuple(str(item).replace("\\", "/") for item in values or ())


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Web framework semantic overlay", allow_abbrev=False)
    parser.add_argument("--root", required=True)
    parser.add_argument("--project-id", default="")
    parser.add_argument("--project-name", default="")
    parser.add_argument("--framework", required=True, choices=("fastapi_django", "express_js", "laravel"))
    parser.add_argument("--commit-sha-before", default="")
    parser.add_argument("--commit-sha-after", default="")
    parser.add_argument("--incremental", action="store_true")
    parser.add_argument("--changed-files-manifest", default="")
    parser.add_argument("--deleted-files-manifest", default="")
    parser.add_argument("--ignore-cache", action="store_true")
    parser.add_argument("--disable-message-scan", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--neo4j-uri", default=os.getenv("NEO4J_URI"))
    parser.add_argument("--neo4j-user", default=os.getenv("NEO4J_USER"))
    parser.add_argument("--neo4j-password", default=os.getenv("NEO4J_PASS") or os.getenv("NEO4J_PASSWORD"))
    parser.add_argument("--neo4j-db", default=os.getenv("NEO4J_DB", "neo4j"))
    add_graph_provider_args(parser)
    return parser.parse_args(argv)


async def _write(args: argparse.Namespace, node_rows, relationship_rows, deleted_paths) -> dict:
    if not prepare_graph_args(args):
        return {"nodes": 0, "relationships": 0, "deleted": 0, "disabled": True}
    driver = await create_graph_driver_from_args(args)
    if driver is None:
        return {"nodes": 0, "relationships": 0, "deleted": 0, "disabled": True}
    framework_names = {
        "fastapi_django": ("fastapi", "django"),
        "express_js": ("express_js",),
        "laravel": ("laravel",),
    }[args.framework]
    writer = WebFrameworkWriter(driver, database=args.neo4j_db)
    try:
        deleted = 0
        for framework in framework_names:
            if deleted_paths:
                deleted += await writer.delete_paths(args.project_id, framework, deleted_paths)
        summary = await writer.write_all(node_rows=node_rows, relationship_rows=relationship_rows)
        summary["deleted"] = deleted
        return summary
    finally:
        closed = driver.close()
        if hasattr(closed, "__await__"):
            await closed


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"Root not found: {root}", file=sys.stderr)
        return 2
    args.project_id = args.project_id or root.name
    frameworks = {
        "fastapi_django": ("fastapi", "django"),
        "express_js": ("express_js",),
        "laravel": ("laravel",),
    }[args.framework]
    changed = _manifest(args.changed_files_manifest) if args.incremental else ()
    deleted = _manifest(args.deleted_files_manifest) if args.incremental else ()
    result = analyze_project(root, args.project_id, frameworks, selected_paths=changed or None)
    node_rows, relationship_rows = result.graph_rows()
    if args.dry_run:
        print(json.dumps({"nodes": node_rows, "relationships": relationship_rows}, ensure_ascii=False))
        return 0
    try:
        summary = asyncio.run(_write(args, node_rows, relationship_rows, deleted))
    except Exception as exc:
        print(f"[web_framework] graph write failed: {exc}", file=sys.stderr)
        return 3
    print(
        "[overlay] framework=%s endpoints=%d relationships=%d graph=%s"
        % (args.framework, len(node_rows), len(relationship_rows), summary)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
