#!/usr/bin/env python3
"""``dev sync link`` worker — one project per invocation.

Lifecycle contract (mirrors the code/doc sync workers):

* spawned by ``cortex_harness/dev.py`` under the ``sync-link`` advisory
  lock; discovered by ``sync_processes._is_link_worker`` so ``dev sync
  link stop`` / Ctrl+C behave;
* opens both graphs READ-ONLY through
  ``code-tiny/tools/graph/read_only_opener.py`` — zero ``StorageLease``,
  so no MCP server has to be paused;
* writes only ``links.jsonl`` / ``state.json`` under the instance links
  directory via :mod:`link_store`.

Output: one JSON summary line on stdout. Exit codes: 0 ok, 2 invalid
input (unregistered project, unsupported provider) — never a partial
write, since writes happen only after a successful build.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


_WORKER_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _WORKER_DIR.parent
for _entry in (
    str(_REPO_ROOT),
    str(_REPO_ROOT / "code-tiny"),
    str(_WORKER_DIR),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import link_engine  # noqa: E402
import registry_facade  # noqa: E402
from tools.graph.read_only_opener import (  # noqa: E402
    ReadOnlyOpenError,
    open_read_only_graph,
)


def _code_source_folders(config_path: Path | None) -> list[str]:
    """Read code scan folders from the active config file (or directory)."""
    if config_path is None:
        return []
    config_path = Path(config_path)
    candidates = (
        sorted(config_path.glob("*.json")) if config_path.is_dir() else [config_path]
    )
    folders: list[str] = []
    for candidate in candidates:
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        source = (payload.get("code") or {}).get("source") or {}
        for project in source.get("projects") or []:
            folders.extend(str(folder) for folder in project.get("folder") or [])
        legacy = source.get("folder")
        if isinstance(legacy, str):
            folders.extend(
                folder.strip() for folder in legacy.split(",") if folder.strip()
            )
    return [folder for folder in folders if folder]


def _graph_endpoint(
    resolved: Any,
    *,
    role: str,
    provider: str,
) -> dict:
    """Resolve {path|uri, …} for one graph role off ResolvedStorage."""
    remote = getattr(resolved, "remote", None)
    if provider == "falkordb" and remote is not None and getattr(remote, "falkordb_uri", None):
        return {
            "uri": remote.falkordb_uri,
            "password": getattr(remote, "falkordb_password", None),
            "ssl": bool(getattr(remote, "falkordb_ssl", False)),
        }
    if provider == "ladybug":
        path = resolved.ladybug_path_for_role(role)
    else:
        path = resolved.falkordb_path_for_role(role)
    if not path:
        raise ReadOnlyOpenError(
            f"no {provider} storage path resolved for role '{role}'; "
            "run 'dev storage-init' or register the project's storage"
        )
    return {"path": str(path)}


def _load_config_mapping(config_path: Path | None) -> dict:
    """Load the active config mapping for ``resolve_storage``.

    ``resolve_storage`` never reads the project config files itself; the
    storage backend (local vs remote) comes from this mapping. Without it
    a remote project would silently resolve to the local embedded
    instance and read the wrong (empty) store.
    """
    if config_path is None:
        return {}
    candidates = (
        sorted(config_path.glob("*.json")) if config_path.is_dir() else [config_path]
    )
    fallback: dict = {}
    for candidate in candidates:
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("active") is True:
            return payload
        fallback = fallback or payload
    return fallback


def build_project(
    project_id: str,
    *,
    project_dir: Path,
    config_dir: Path | None = None,
    dry_run: bool = False,
    reconcile: bool = False,
) -> dict:
    """Open both graphs read-only and run the pipeline for one project."""
    # dev.py passes the config FILE; the registry wants its directory.
    registry_config_dir = (
        config_dir.parent if (config_dir is not None and config_dir.is_file()) else config_dir
    )
    targets = registry_facade.resolve_project_targets(
        project_id, config_dir=registry_config_dir
    )
    from cortex_harness.storage import resolve_storage

    config_mapping = _load_config_mapping(config_dir)
    resolved = resolve_storage(
        Path(project_dir) if project_dir else registry_facade.project_root_from_config(),
        config=config_mapping,
    )
    code_endpoint = _graph_endpoint(resolved, role="code", provider=targets.provider)
    doc_endpoint = _graph_endpoint(resolved, role="document", provider=targets.provider)

    merged_hashes = link_engine.load_sync_state_hashes(project_dir)
    baseline_doc_shas, fallback_code_shas = link_engine.split_shas_by_side(merged_hashes)
    config_path = config_dir
    if config_path is None:
        env_config = os.environ.get("CORTEX_HARNESS_CONFIG_PATH")
        config_path = Path(env_config) if env_config else None
    code_folders = _code_source_folders(config_path)
    code_shas = link_engine.load_code_inventory_hashes(
        code_folders, targets.project_id, project_dir
    )
    code_folder_shas = link_engine.load_code_folder_inventories(
        code_folders, targets.project_id
    )
    if not code_shas:
        code_shas = fallback_code_shas

    links_dir = registry_facade.links_dir(targets.project_id, project_dir=project_dir)
    code_graph = open_read_only_graph(
        backend=targets.provider, graph_name=targets.code_graph, **code_endpoint
    )
    doc_graph = open_read_only_graph(
        backend=targets.provider, graph_name=targets.doc_graph, **doc_endpoint
    )
    try:
        report = link_engine.build_links(
            project_id=targets.project_id,
            links_dir=links_dir,
            code_graph=code_graph,
            doc_graph=doc_graph,
            doc_shas=baseline_doc_shas,
            code_shas=code_shas,
            parser_type=targets.parser_type,
            reconcile=reconcile,
            dry_run=dry_run,
            code_folder_shas=code_folder_shas,
        )
    finally:
        code_graph.close()
        doc_graph.close()

    return {
        "project_id": targets.project_id,
        "provider": targets.provider,
        "dry_run": report.dry_run,
        "reconcile": report.reconcile,
        "new_links": len(report.new_links),
        "staled_links": len(report.staled_links),
        "unchanged_links": report.unchanged_links,
        "doc_universe": report.doc_universe,
        "symbol_universe": report.symbol_universe,
        "symbol_universe_expected": report.symbol_universe_expected,
        "symbol_universe_by_label": report.symbol_universe_by_label,
        "sha_join": report.sha_join,
        "links_dir": str(links_dir),
        "coverage": report.coverage,
        "would_link": (
            [
                {
                    "doc": record["doc"],
                    "code": {
                        "path": record["code"]["path"],
                        "name": record["code"]["name"],
                    },
                    "score": record["score"],
                }
                for record in report.new_links[:50]
            ]
            if report.dry_run
            else []
        ),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="link_worker.py")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--project-dir", default=".")
    parser.add_argument("--config-dir", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reconcile", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    project_dir = Path(args.project_dir).expanduser().resolve()
    config_dir = Path(args.config_dir).expanduser() if args.config_dir else None
    try:
        summary = build_project(
            args.project_id,
            project_dir=project_dir,
            config_dir=config_dir,
            dry_run=args.dry_run,
            reconcile=args.reconcile,
        )
    except registry_facade.ProjectNotRegisteredError as exc:
        print(json.dumps({"error": "project_not_registered", "message": str(exc)}))
        return 2
    except ReadOnlyOpenError as exc:
        print(json.dumps({"error": "storage_unavailable", "message": str(exc)}))
        return 2
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
