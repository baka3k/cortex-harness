#!/usr/bin/env python3
"""Drop a project's code Qdrant collection(s) so the next ingest re-indexes clean.

The code-side counterpart of ``doc-tiny/0_reset_all.py``: a model swap (or a
corrupted index) needs a real drop + re-ingest — copying vectors across
cannot fix a wrong-model collection (see ``rebuild_vector_collection.py``
for the copy-only tool it deliberately is not).

Contract (docs/UNIFIED_INGEST_QUERY_CONTRACT.md): the code collection name
equals ``project_id``; message-scan siblings live in ``{base}_mess`` with
hash vectors that survive a model swap untouched. Those are only dropped
with ``--include-messages``.

Usage:
  python code-tiny/scripts/reset_code_collection.py --project-id X --dry-run
  python code-tiny/scripts/reset_code_collection.py --project-id X --force
  python code-tiny/scripts/reset_code_collection.py --collection C --force

Back up first (``db_transfer export``) — a drop is not recoverable without
one, and rolling back a model swap re-imports the backup.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

CODE_TINY = Path(__file__).resolve().parents[1]
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.common import embedding_marker, qdrant_layout_cache  # noqa: E402
from tools.common.local_qdrant import (  # noqa: E402
    MESSAGE_COLLECTION_SUFFIX,
    default_local_qdrant_path,
    get_code_qdrant_store,
)


def _targets(project_id: str | None, collection: str | None, include_messages: bool) -> list[str]:
    if collection:
        names = [collection]
    elif project_id:
        names = [project_id]
    else:
        raise SystemExit("Either --project-id or --collection is required.")
    if include_messages:
        base = collection or project_id
        if base and not base.endswith(MESSAGE_COLLECTION_SUFFIX):
            stem = base[: -len("_functions")] if base.endswith("_functions") else base
            names.append(f"{stem}{MESSAGE_COLLECTION_SUFFIX}")
    # A message collection holds hash vectors, not model embeddings — never
    # a default target, and never an explicit one without the flag.
    if not include_messages:
        for name in names:
            if name.endswith(MESSAGE_COLLECTION_SUFFIX):
                raise SystemExit(
                    f"Refusing to drop message collection {name!r} without "
                    "--include-messages (hash vectors, not model embeddings)."
                )
    return list(dict.fromkeys(names))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Drop code Qdrant collections for a clean re-ingest."
    )
    parser.add_argument("--project-id", default=os.getenv("PROJECT_ID"))
    parser.add_argument(
        "--collection",
        help="Override the collection name (default: the project id).",
    )
    parser.add_argument(
        "--include-messages",
        action="store_true",
        help="Also drop the {base}_mess hash-vector sibling (default: never).",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Report targets without deleting."
    )
    parser.add_argument(
        "--force", action="store_true", help="Required for actual deletion."
    )
    args = parser.parse_args()

    if not args.dry_run and not args.force:
        raise SystemExit("Refusing destructive reset without --force; use --dry-run first.")

    project_id = (args.project_id or "").strip() or None
    names = _targets(project_id, (args.collection or "").strip() or None, args.include_messages)

    # Route through the storage factory so remote-mode projects resolve the
    # same backend the ingest writes to.
    store = get_code_qdrant_store(project_id=project_id)
    location = (
        f"project {project_id!r}"
        if project_id
        else f"local store {default_local_qdrant_path()}"
    )
    print(f"Resetting code Qdrant collections for {location}:")
    dropped = 0
    for name in names:
        exists = store.collection_exists(name)
        suffix = " (hash vectors — message scan)" if name.endswith(MESSAGE_COLLECTION_SUFFIX) else ""
        if not exists:
            print(f"  - {name}{suffix}: not present, nothing to do.")
            continue
        try:
            count = int(getattr(store.count(name, count_filter=None, exact=True), "count", 0))
        except Exception:
            count = -1
        print(f"  - {name}{suffix}: {count} points")
        if args.dry_run:
            continue
        store.delete_collection(name)
        dropped += 1
        print("    dropped.")
    if args.dry_run:
        print("Dry-run complete; no data deleted.")
        return
    if not dropped and names:
        # A silent "nothing dropped" on a remote-mode project means the
        # collection lives on another backend — make the operator look.
        raise SystemExit(
            "No targeted collection exists on the resolved backend "
            f"({location}). Pass --project-id so the storage factory "
            "resolves the project's own backend, or check the name."
        )
    if dropped:
        qdrant_layout_cache.invalidate()
    embedding_marker.reset_sentinel_cache()
    print(f"Reset complete ({dropped} collection(s) dropped). Re-run 'dev sync code' to re-ingest.")


if __name__ == "__main__":
    main()
