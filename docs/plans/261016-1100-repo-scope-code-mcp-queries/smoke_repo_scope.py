"""Live smoke for the repo scope on real embedded stores (plan phase 07).

Gate for red-team note #3: ``ENDS WITH`` on falkordblite (dev default) was
never verified before this run. Seeds a fresh temp store with code nodes
carrying both stored repo flavors, then exercises:

  1. driver-level ENDS WITH suffix match + exact match + unscoped no-op;
  2. the real MCP tool layer (listup_symbols_matching_file_path) with
     repo="s3" against the same store;
  3. LadybugDB parity on the same queries.

Usage: .venv/bin/python docs/plans/261016-1100-repo-scope-code-mcp-queries/smoke_repo_scope.py
Exit code 0 = all checks passed.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT / "code-tiny"))
sys.path.insert(0, str(_ROOT / "code-tiny" / "mcp"))

from tools.common.project_scope import (  # noqa: E402
    prepare_repo_scope_parameters,
    repo_scope_predicate,
)

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))


# Nodes seeded with BOTH stored flavors + a basename-prefix decoy.
# f5 (repo-less) goes LAST: on the schema-first ladybug backend the raw
# CREATE here types the ``repo`` column from the first row, so a leading
# NULL would mis-type it (real writes always go through the typed manifest).
SEED_ROWS = [
    {"id": "f1", "name": "handler_one", "file_path": "/repo/s3/src/handler.py", "repo": "/Users/dev/workspaces/s3"},
    {"id": "f2", "name": "handler_two", "file_path": "/repo/s3/src/other.py", "repo": "cplus_proj/s3"},
    {"id": "f3", "name": "decoy", "file_path": "/repo/xs3/src/decoy.py", "repo": "/Users/dev/workspaces/xs3"},
    {"id": "f4", "name": "other_repo", "file_path": "/repo/s1/src/x.py", "repo": "/Users/dev/workspaces/s1"},
    {"id": "f5", "name": "no_repo", "file_path": "/legacy/old.py"},
]


async def seed_and_probe(driver) -> None:
    for row in SEED_ROWS:
        props = ", ".join(
            f"{key}: ${key}" for key in ("id", "name", "file_path")
        )
        repo_expr = "$repo" if row.get("repo") is not None else "NULL"
        query = (
            f"CREATE (n:Function {{{props}, repo: {repo_expr}}}) RETURN n.id AS id"
        )
        records, _, _ = await driver.execute_query(
            query,
            {"id": row["id"], "name": row["name"], "file_path": row["file_path"], "repo": row.get("repo")},
            "smoke_repo",
        )
        assert records, f"seed failed for {row['id']}"

    # ── 1. driver-level predicate semantics ──
    async def run(params_extra, alias="n"):
        query = (
            f"MATCH (n:Function) WHERE {repo_scope_predicate(alias)} "
            "RETURN n.id AS id ORDER BY id"
        )
        records, _, _ = await driver.execute_query(
            query, prepare_repo_scope_parameters(params_extra), "smoke_repo"
        )
        return [r["id"] for r in records]

    got = await run({"repo": "s3", "repo_suffix": "/s3"})
    check("suffix ENDS WITH matches both flavors, not xs3", got == ["f1", "f2"], ",".join(got))

    got = await run({"repo": "/Users/dev/workspaces/s3", "repo_suffix": "/Users/dev/workspaces/s3"})
    check("exact abspath match", got == ["f1"], ",".join(got))

    got = await run({"repo": None, "repo_suffix": None})
    check("unscoped no-op returns all nodes", got == ["f1", "f2", "f3", "f4", "f5"], ",".join(got))

    got = await run({"repo": "nope", "repo_suffix": "/nope"})
    check("unknown repo matches 0", got == [], ",".join(got))

    # ── 2. DISTINCT-repo helper query (warning branch) ──
    records, _, _ = await driver.execute_query(
        "MATCH (n) WHERE n.repo IS NOT NULL AND n.repo <> '' "
        "RETURN DISTINCT n.repo AS repo ORDER BY repo LIMIT 5",
        {}, "smoke_repo",
    )
    known = [r["repo"] for r in records]
    check("DISTINCT repo helper returns 4 known values", len(known) == 4, ",".join(known))


async def smoke_provider(name: str, make_driver) -> None:
    print(f"\n=== smoke[{name}] ===")
    driver = await make_driver()
    try:
        await seed_and_probe(driver)

        # ── 3. real MCP tool layer on this provider ──
        os.environ["CODE_GRAPH_PROVIDER"] = "falkordb" if name == "falkordblite" else "ladybug"
        import fastmcp_server

        async def fake_driver():
            return driver

        async def empty_databases():
            return ["smoke_repo"]

        with _patch(fastmcp_server, "_get_graph_driver", fake_driver), \
             _patch(fastmcp_server, "_list_databases", empty_databases):
            result = await fastmcp_server.tool_listup_symbols_matching_file_path(
                modules=["src"], repo="s3"
            )
        ids = sorted(node["id"] for node in result["symbols"])
        check(
            "tool layer repo='s3' returns f1+f2 only",
            ids == ["f1", "f2"],
            ",".join(ids),
        )
        check(
            "tool response carries scope block",
            result["scope"]["repo"] == "s3" and result["scope"]["filter_applied"] is True,
            str(result["scope"]),
        )
        check("tool warnings empty on hits", result["warnings"] == [], str(result["warnings"]))

        miss = await fastmcp_server.tool_listup_symbols_matching_file_path(
            modules=["src"], repo="zz9"
        )
        check(
            "unknown repo warns with known values",
            any("zz9" in w and "Known repo values" in w for w in miss["warnings"]),
            "; ".join(miss["warnings"]),
        )
    finally:
        try:
            await driver.close()
        except Exception:
            pass


class _patch:
    """Tiny context manager to avoid importing unittest in a smoke script."""

    def __init__(self, obj, attr, value):
        self.obj, self.attr, self.value = obj, attr, value

    def __enter__(self):
        self.old = getattr(self.obj, self.attr)
        setattr(self.obj, self.attr, self.value)
        return self

    def __exit__(self, *exc):
        setattr(self.obj, self.attr, self.old)
        return False


async def main() -> int:
    tmpdir = Path(tempfile.mkdtemp(prefix="repo_scope_smoke_"))

    async def make_falkordblite():
        from tools.graph.driver.falkordb_driver import FalkorDBDriver

        return FalkorDBDriver(path=tmpdir / "smoke.rdb")

    async def make_ladybug():
        from tools.graph.driver.ladybug_driver import LadybugDBDriver

        return LadybugDBDriver(tmpdir / "smoke.lbdb")

    await smoke_provider("falkordblite", make_falkordblite)
    await smoke_provider("ladybug", make_ladybug)

    failed = [name for name, ok, _ in CHECKS if not ok]
    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} checks passed")
    if failed:
        print("FAILED:", failed)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
