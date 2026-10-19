#!/usr/bin/env python3
"""Live sha-namespace probe (plan 261019-1700 Phase 01).

Standalone diagnostic — NEVER imported by the test suite. Opens both
graphs read-only (the link-tiny opener), reads the sync-state baseline and
the code inventories, and prints a markdown report pinning how each side's
graph identity maps onto its baseline keys:

* doc side — ``Paragraph.source_id`` spellings vs ``file_hashes`` keys,
  hit-rate of the ``__``-flatten rule;
* code side — per-label server counts vs unpaginated reads
  (resultset-size truncation), ``repo`` property values vs the configured
  scan folders, cross-folder shadow + collision counts.

Usage:
  PYTHONPATH=code-tiny:link-tiny:. .venv/bin/python \\
      link-tiny/tests/probe_sha_namespaces.py \\
      --project-dir /path/to/project --project-id <project> [--config-dir cfg.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

_WORKER_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _WORKER_DIR.parent
for _entry in (str(_REPO_ROOT), str(_REPO_ROOT / "code-tiny"), str(_WORKER_DIR)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import link_engine  # noqa: E402
import link_store  # noqa: E402
import registry_facade  # noqa: E402
from tools.graph.read_only_opener import open_read_only_graph  # noqa: E402


def flatten_doc_rel(rel: str) -> str:
    """Mirror dev.py's source_id rule (both separators → ``__``)."""
    posix = link_store.posix_path(rel)
    return posix.replace("/", "__").replace("\\", "__")


def _norm_repo_key(value: str) -> str:
    return str(Path(value).expanduser().resolve()).casefold()


def probe_doc_side(doc_graph: Any, project_id_normalized: str, doc_shas: Mapping[str, str]) -> List[str]:
    rows = doc_graph.run(
        "MATCH (p:Paragraph) WHERE p.project_id_normalized = $pid "
        "RETURN DISTINCT p.source_id AS sid",
        {"pid": project_id_normalized},
    )
    source_ids = [str(row.get("sid") or "") for row in rows if row.get("sid")]
    keys = set(doc_shas)
    flattened = {flatten_doc_rel(key) for key in keys}
    stems = {Path(key).stem for key in keys}

    direct_hits = sum(1 for sid in source_ids if sid in keys)
    flat_hits = sum(1 for sid in source_ids if sid in flattened and sid not in keys)
    stem_hits = sum(
        1 for sid in source_ids if sid not in keys and sid not in flattened and sid in stems
    )
    misses = len(source_ids) - direct_hits - flat_hits - stem_hits
    stem_mode = sum(1 for sid in source_ids if "__" not in sid)

    lines = [
        "### Doc side",
        "",
        "| metric | value |",
        "| --- | --- |",
        f"| graph source_ids | {len(source_ids)} |",
        f"| baseline file_hashes keys | {len(keys)} |",
        f"| direct POSIX-rel hits | {direct_hits} |",
        f"| `__`-flattened hits | {flat_hits} |",
        f"| stem fallback hits | {stem_hits} |",
        f"| unresolved | {misses} |",
        f"| single-file (stem) source_ids | {stem_mode} |",
        "",
        f"Flatten rule hit-rate on live data: **{flat_hits}/{len(source_ids)}** "
        "source_ids match `flatten(rel)` of a baseline key (direct hits counted "
        "separately).",
    ]
    unmatched = [
        sid for sid in source_ids
        if sid not in keys and sid not in flattened and sid not in stems
    ]
    for sid in unmatched[:5]:
        lines.append(f"- UNRESOLVED sample: `{sid}`")
    for key in sorted(keys)[:3]:
        lines.append(
            f"- baseline key sample: `{key}` → flatten `{flatten_doc_rel(key)}`"
        )
    return lines


def probe_code_side(
    code_graph: Any,
    project_id_normalized: str,
    folder_shas: Mapping[str, Mapping[str, str]],
) -> List[str]:
    labels = link_engine.CODE_SYMBOL_LABELS + link_engine.CODE_ENDPOINT_LABELS
    folder_keys = {_norm_repo_key(folder) for folder in folder_shas}
    lines = [
        "### Code side",
        "",
        "| label | server count | unpaginated rows | truncated |",
        "| --- | --- | --- | --- |",
    ]
    for label in labels:
        try:
            expected = code_graph.run(
                f"MATCH (n:{label}) WHERE n.project_id_normalized = $pid "
                "RETURN count(n) AS c",
                {"pid": project_id_normalized},
            )[0]["c"]
            rows = code_graph.run(
                f"MATCH (n:{label}) WHERE n.project_id_normalized = $pid "
                "RETURN id(n) AS i",
                {"pid": project_id_normalized},
            )
        except Exception as exc:  # noqa: BLE001 - probe survives dead labels
            lines.append(f"| {label} | ERR | ERR | {str(exc)[:40]} |")
            continue
        truncated = "YES" if expected and len(rows) < expected else "no"
        lines.append(f"| {label} | {expected} | {len(rows)} | {truncated} |")

    lines += [
        "",
        "`repo` values observed (classified against the configured scan folders):",
        "",
    ]
    for label in labels:
        try:
            repos = code_graph.run(
                f"MATCH (n:{label}) WHERE n.project_id_normalized = $pid "
                "RETURN DISTINCT n.repo AS repo",
                {"pid": project_id_normalized},
            )
        except Exception:  # noqa: BLE001
            repos = []
        for row in repos:
            repo = str(row.get("repo") or "")
            if not repo:
                kind = "NULL"
            elif _norm_repo_key(repo) in folder_keys:
                kind = "scan-folder"
            elif any(
                key.startswith(_norm_repo_key(repo) + "/") for key in folder_keys
            ):
                kind = "project-root (parent of scan folders)"
            else:
                kind = "foreign/unknown"
            lines.append(f"- `{repo or '<null>'}` [{kind}] ← {label}")
    return lines


def probe_inventories(folder_shas: Mapping[str, Mapping[str, str]]) -> List[str]:
    keys_by_folder = {folder: set(mapping) for folder, mapping in folder_shas.items()}
    folders = sorted(keys_by_folder)
    lines = [
        "### Inventories",
        "",
        "| folder | entries |",
        "| --- | --- |",
    ]
    for folder in folders:
        lines.append(f"| `{folder}` | {len(keys_by_folder[folder])} |")

    exact_collisions: set[str] = set()
    shadow_pairs = 0
    for index, folder_a in enumerate(folders):
        for folder_b in folders[index + 1 :]:
            keys_a, keys_b = keys_by_folder[folder_a], keys_by_folder[folder_b]
            exact_collisions |= keys_a & keys_b
            shadow_pairs += sum(
                1 for key in keys_a if any(k.endswith("/" + key) for k in keys_b)
            )
            shadow_pairs += sum(
                1 for key in keys_b if any(k.endswith("/" + key) for k in keys_a)
            )
    lines += [
        "",
        f"- exact folder-relative collisions: **{len(exact_collisions)}** "
        f"{sorted(exact_collisions)[:5]}",
        "- suffix shadows across folders (a folder-relative key of one folder "
        f"ending with `/` + another folder's key): **{shadow_pairs}**",
        "",
        "→ naive suffix matching (graph path vs any baseline key that ends with "
        "it) is ambiguous exactly where these counts are non-zero — the join "
        "must be `(repo, path)` first, with a *unique*-fallback only.",
    ]
    return lines


def _folder_inventories_local(
    code_folders: List[str], project_id: str
) -> Dict[str, Dict[str, str]]:
    """Phase-01 local copy of the folder-inventory chain.

    Mirrors the dev-side chain (`{folder}/.cache/incremental_sync/*.json` →
    `inventory_path` → `entries`) until the engine ships
    ``load_code_folder_inventories`` (Phase 02); the probe prefers the
    engine implementation when present.
    """
    import re

    safe_project = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(project_id)).strip("._") or "project"
    result: Dict[str, Dict[str, str]] = {}
    for folder in code_folders:
        cache_dir = Path(folder).expanduser() / ".cache" / "incremental_sync"
        if not cache_dir.is_dir():
            continue
        candidates = sorted(cache_dir.glob(f"{safe_project}_*.json"))
        candidates.extend(sorted(cache_dir.glob(f"{safe_project}.json")))
        newest: Optional[Mapping[str, Any]] = None
        for state_path in candidates:
            try:
                payload = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, Mapping):
                continue
            if (
                newest is None
                or str(payload.get("last_run_after") or "")
                >= str(newest.get("last_run_after") or "")
            ):
                newest = payload
        inventory_path = str((newest or {}).get("inventory_path") or "").strip()
        if not inventory_path:
            continue
        try:
            inventory = json.loads(Path(inventory_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        entries = inventory.get("entries") if isinstance(inventory, Mapping) else None
        if not isinstance(entries, Mapping):
            continue
        folder_key = str(Path(folder).expanduser().resolve())
        mapping = result.setdefault(folder_key, {})
        for rel, entry in entries.items():
            if isinstance(entry, Mapping) and entry.get("sha256"):
                mapping[link_store.posix_path(rel)] = str(entry["sha256"])
    return result


def _find_config_dir(project_dir: Path) -> Optional[Path]:
    import os

    env = os.environ.get("CORTEX_HARNESS_CONFIG_PATH")
    if env:
        candidate = Path(env).expanduser()
        if candidate.is_file():
            return candidate.parent
    default = project_dir / ".cortext-harness" / "config"
    return default if default.is_dir() else None


def _code_source_folders(config_dir: Optional[Path]) -> List[str]:
    if config_dir is None:
        return []
    folders: List[str] = []
    for candidate in sorted(config_dir.glob("*.json")):
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        source = (payload.get("code") or {}).get("source") or {}
        for project in source.get("projects") or []:
            folders.extend(str(f) for f in project.get("folder") or [])
    return [f for f in folders if f]


def main() -> int:
    parser = argparse.ArgumentParser(prog="probe_sha_namespaces.py")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--project-dir", default=".")
    parser.add_argument("--config-dir", default=None)
    parser.add_argument(
        "--skip-graphs",
        action="store_true",
        help="Only probe baselines/inventories (no graph server required).",
    )
    args = parser.parse_args()

    project_dir = Path(args.project_dir).expanduser().resolve()
    config_dir = Path(args.config_dir).expanduser() if args.config_dir else None
    if config_dir is None:
        # Without the config mapping the storage resolver would open the
        # local embedded instance (empty graphs) instead of the live store.
        config_dir = _find_config_dir(project_dir)
    registry_config_dir = (
        config_dir.parent
        if (config_dir is not None and config_dir.is_file())
        else config_dir
    )
    targets = registry_facade.resolve_project_targets(
        args.project_id, config_dir=registry_config_dir
    )

    merged = link_engine.load_sync_state_hashes(project_dir)
    doc_shas, fallback_code_shas = link_engine.split_shas_by_side(merged)
    code_folders = _code_source_folders(
        config_dir if config_dir is not None else _find_config_dir(project_dir)
    )
    loader = getattr(link_engine, "load_code_folder_inventories", None)
    folder_shas = (
        loader(code_folders, targets.project_id)
        if loader is not None
        else _folder_inventories_local(code_folders, targets.project_id)
    )
    if not folder_shas:
        folder_shas = {"<sync-state fallback>": dict(fallback_code_shas)}

    print(f"# sha-namespace probe — {targets.project_id}")
    print()
    print("## Baselines")
    print()
    print(
        f"- sync-state entries: {len(merged)} "
        f"(doc {len(doc_shas)} / code {len(fallback_code_shas)})"
    )
    print(f"- inventory folders: {len(folder_shas)}")
    print()
    if not args.skip_graphs:
        from link_worker import _graph_endpoint, _load_config_mapping
        from cortex_harness.storage import resolve_storage

        config_mapping = _load_config_mapping(config_dir)
        resolved = resolve_storage(project_dir, config=config_mapping)
        code_endpoint = _graph_endpoint(resolved, role="code", provider=targets.provider)
        doc_endpoint = _graph_endpoint(resolved, role="document", provider=targets.provider)
        code_graph = open_read_only_graph(
            backend=targets.provider, graph_name=targets.code_graph, **code_endpoint
        )
        doc_graph = open_read_only_graph(
            backend=targets.provider, graph_name=targets.doc_graph, **doc_endpoint
        )
        try:
            for line in probe_doc_side(
                doc_graph, link_engine._norm(targets.project_id), doc_shas
            ):
                print(line)
            print()
            for line in probe_code_side(
                code_graph, link_engine._norm(targets.project_id), folder_shas
            ):
                print(line)
        finally:
            code_graph.close()
            doc_graph.close()
        print()
    for line in probe_inventories(folder_shas):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
