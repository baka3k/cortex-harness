#!/usr/bin/env python3
"""Byte-for-byte SHA verification of live link records (plan 261019-1700
Phase 04, task 4).

Standalone, READ-ONLY, never imported by the suite. Samples active link
records, recomputes the expected doc/code SHA from the live baselines
using the contract rules (LINK_CONTRACT.md §1.5), and diffs. Exit code 0
only when all gates pass:

1. every sampled record's SHAs match the recomputed ones byte-for-byte
   (null == null counts as a match only when the resolver independently
   returns null — an explained absence, never a silent guess);
2. ≥95% of ALL active records carry non-null source_sha on both sides;
3. every null is explained by the join (baseline genuinely lacks the
   file, or an ambiguous cross-folder match) — zero unexplained nulls.

Usage:
  PYTHONPATH=code-tiny:link-tiny:. .venv/bin/python \\
      link-tiny/tests/verify_record_shas.py \\
      --project-dir /path/to/project --project-id <project> [--sample 20]
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

_WORKER_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _WORKER_DIR.parent
for _entry in (str(_REPO_ROOT), str(_REPO_ROOT / "code-tiny"), str(_WORKER_DIR)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import link_engine  # noqa: E402
import link_store  # noqa: E402
import registry_facade  # noqa: E402


def _find_config_dir(project_dir: Path):
    import os

    env = os.environ.get("CORTEX_HARNESS_CONFIG_PATH")
    if env:
        candidate = Path(env).expanduser()
        if candidate.is_file():
            return candidate.parent
    default = project_dir / ".cortext-harness" / "config"
    return default if default.is_dir() else None


def _code_source_folders(config_dir) -> list:
    if config_dir is None:
        return []
    folders = []
    for candidate in sorted(config_dir.glob("*.json")):
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        source = (payload.get("code") or {}).get("source") or {}
        for project in source.get("projects") or []:
            folders.extend(str(f) for f in project.get("folder") or [])
    return [f for f in folders if f]


def _record_identity(record) -> tuple:
    code = record.get("code") or {}
    lines = code.get("lines") or [None, None]
    return (
        link_store.posix_path(code.get("path")),
        str(code.get("name") or ""),
        (lines[0], lines[1]),
        str(code.get("target_type") or "symbol"),
    )


def _live_symbol_repos(targets, project_dir: Path, config_dir) -> dict:
    """``{EngineSymbol.identity → repo}`` read from the live code graph."""
    from tools.graph.read_only_opener import open_read_only_graph
    from link_worker import _graph_endpoint, _load_config_mapping
    from cortex_harness.storage import resolve_storage

    config_mapping = _load_config_mapping(config_dir)
    resolved = resolve_storage(project_dir, config=config_mapping)
    endpoint = _graph_endpoint(resolved, role="code", provider=targets.provider)
    graph = open_read_only_graph(
        backend=targets.provider, graph_name=targets.code_graph, **endpoint
    )
    try:
        symbols, _stats = link_engine.read_code_symbols_report(
            graph, link_engine._norm(targets.project_id), targets.parser_type
        )
    finally:
        graph.close()
    # first-wins per identity — mirrors the engine's join cache exactly
    # (duplicate identities across scan folders must resolve one way)
    symbol_repo: dict = {}
    for symbol in symbols:
        symbol_repo.setdefault(symbol.identity, symbol.repo)
    return symbol_repo


def main() -> int:
    parser = argparse.ArgumentParser(prog="verify_record_shas.py")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--project-dir", default=".")
    parser.add_argument("--config-dir", default=None)
    parser.add_argument("--sample", type=int, default=20)
    parser.add_argument("--seed", type=int, default=20261019)
    args = parser.parse_args()

    project_dir = Path(args.project_dir).expanduser().resolve()
    config_dir = Path(args.config_dir).expanduser() if args.config_dir else None
    if config_dir is None:
        config_dir = _find_config_dir(project_dir)
    registry_config_dir = (
        config_dir.parent
        if (config_dir is not None and config_dir.is_file())
        else config_dir
    )
    targets = registry_facade.resolve_project_targets(
        args.project_id, config_dir=registry_config_dir
    )

    # Recompute the baselines exactly as the worker does.
    merged = link_engine.load_sync_state_hashes(project_dir)
    doc_shas, fallback_code_shas = link_engine.split_shas_by_side(merged)
    folders = _code_source_folders(config_dir)
    folder_shas = link_engine.load_code_folder_inventories(folders, targets.project_id)
    code_shas = link_engine.load_code_inventory_hashes(
        folders, targets.project_id, project_dir
    ) or fallback_code_shas
    doc_index = link_engine.build_doc_sha_index(doc_shas)
    code_index = link_engine.CodeShaIndex(folder_shas, code_shas)

    # Records don't carry `repo` (join input, not schema) — restore it from
    # the live graph so the re-verification takes the same (repo, path)
    # route the build took. Read-only, paginated, same opener as the engine.
    symbol_repo = _live_symbol_repos(
        targets, project_dir, config_dir
    )

    links_dir = registry_facade.links_dir(targets.project_id, project_dir=project_dir)
    active = link_store.latest_links(links_dir, status="active")
    if not active:
        print("FAIL: no active records found — run 'dev sync link' first")
        return 1

    state = link_store.load_state(links_dir) or {}

    def expected_doc(record) -> tuple:
        doc_id = str((record.get("doc") or {}).get("doc_id") or "")
        return doc_id, link_engine._doc_sha_for(doc_id, doc_index)

    def expected_code(record) -> tuple:
        code = record.get("code") or {}
        path = str(code.get("path") or "")
        repo = symbol_repo.get(_record_identity(record))
        sha, outcome = code_index.resolve(repo, path)
        return path, sha, outcome

    mismatches: list[str] = []
    unexplained: list[str] = []
    doc_null = code_null = 0
    for record in active:
        doc_id, want_doc = expected_doc(record)
        got_doc = (record.get("doc") or {}).get("source_sha")
        path, want_code, outcome = expected_code(record)
        got_code = (record.get("code") or {}).get("source_sha")
        if got_doc != want_doc:
            mismatches.append(
                f"doc sha {doc_id!r}: record={got_doc!r} expected={want_doc!r}"
            )
        if got_code != want_code:
            mismatches.append(
                f"code sha {path!r}: record={got_code!r} expected={want_code!r} "
                f"(outcome={outcome})"
            )
        if got_doc is None:
            doc_null += 1
            if want_doc is not None:
                unexplained.append(f"doc {doc_id!r} null but baseline has it")
        if got_code is None:
            code_null += 1
            if want_code is not None:
                unexplained.append(f"code {path!r} null but join found a SHA")

    total = len(active)
    doc_nonnull_ratio = 1 - doc_null / total
    code_nonnull_ratio = 1 - code_null / total

    rng = random.Random(args.seed)
    sample = rng.sample(active, min(args.sample, total))
    sample_checked = 0
    sample_mismatch = 0
    for record in sample:
        doc_id, want_doc = expected_doc(record)
        path, want_code, outcome = expected_code(record)
        sample_checked += 1
        if (
            (record["doc"].get("source_sha") != want_doc)
            or (record["code"].get("source_sha") != want_code)
        ):
            sample_mismatch += 1

    sha_join = state.get("sha_join") or {}
    print(f"# verify_record_shas — {targets.project_id}")
    print()
    print("| gate | value |")
    print("| --- | --- |")
    print(f"| active records | {total} |")
    print(f"| sampled (byte-for-byte) | {sample_checked}, mismatches {sample_mismatch} |")
    print(f"| doc non-null | {doc_nonnull_ratio:.4f} |")
    print(f"| code non-null | {code_nonnull_ratio:.4f} |")
    print(f"| unexplained nulls | {len(unexplained)} |")
    print(
        f"| state.json sha_join | {json.dumps(sha_join, sort_keys=True)} |"
    )
    for line in mismatches[:10]:
        print(f"- MISMATCH: {line}")
    for line in unexplained[:10]:
        print(f"- UNEXPLAINED: {line}")

    gates = (
        sample_mismatch == 0
        and doc_nonnull_ratio >= 0.95
        and code_nonnull_ratio >= 0.95
        and not mismatches
        and not unexplained
    )
    print()
    print("RESULT:", "PASS" if gates else "FAIL")
    return 0 if gates else 1


if __name__ == "__main__":
    raise SystemExit(main())
