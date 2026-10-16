"""Seed a fresh falkordblite store for the real-MCP repo-scope matrix.

Creates graph `hyper_graph` (the server's default candidate) with code
nodes spanning both stored repo flavors + a basename-prefix decoy, CALLS
edges (incl. one cross-repo caller), a Class+DECLARES, IPC Message nodes,
and a Workflow+HAS_STEP (to prove workflow tools reject repo).
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT / "code-tiny"))

FUNCTIONS = [
    # id, name, qualified_name, file_path, repo, kind
    ("f1", "handler_one", "svc::handler_one", "/repo/s3/src/handler.py", "/Users/dev/workspaces/s3", None),
    ("f2", "handler_two", "svc::handler_two", "/repo/s3/src/handler2.py", "/Users/dev/workspaces/s3", None),
    ("f3", "mapper_one", "svc::mapper_one", "/repo/s3/src/mapper.py", "cplus_proj/s3", None),
    ("f4", "decoy_fn", "xs::decoy_fn", "/repo/xs3/src/decoy.py", "/Users/dev/workspaces/xs3", None),
    ("f5", "other_fn", "s1::other_fn", "/repo/s1/src/other.py", "/Users/dev/workspaces/s1", None),
    ("f6", "orphan_fn", "legacy::orphan_fn", "/legacy/old.py", None, None),
]
# caller → callee (f4 is a CROSS-REPO caller of f1: callers-of-f1 must
# survive repo scoping on f1 only)
CALLS = [("f1", "f2"), ("f2", "f3"), ("f4", "f1")]


async def seed(path: Path) -> None:
    from tools.graph.driver.falkordb_driver import FalkorDBDriver

    driver = FalkorDBDriver(path=path)
    db = "hyper_graph"
    for fid, name, qualified, file_path, repo, kind in FUNCTIONS:
        props = {
            "id": fid,
            "name": name,
            "qualified_name": qualified,
            "file_path": file_path,
            "kind": kind,
        }
        set_lines = ", ".join(f"n.{k} = ${k}" for k in props)
        repo_expr = "$repo" if repo is not None else "NULL"
        query = (
            f"CREATE (n:Function) SET {set_lines}, n.repo = {repo_expr} RETURN n.id AS id"
        )
        props["repo"] = repo
        records, _, _ = await driver.execute_query(query, props, db)
        assert records, f"seed function {fid} failed"

    for src, dst in CALLS:
        await driver.execute_query(
            "MATCH (a:Function {id: $src}), (b:Function {id: $dst}) "
            "CREATE (a)-[:CALLS {site_id: $src + '->' + $dst}]->(b) RETURN 1 AS ok",
            {"src": src, "dst": dst},
            db,
        )

    # File nodes + CONTAINS (module path templates match via File-CONTAINS-Function)
    files = [
        ("file1", "/repo/s3/src/handler.py", "/Users/dev/workspaces/s3", ["f1"]),
        ("file2", "/repo/s3/src/handler2.py", "/Users/dev/workspaces/s3", ["f2"]),
        ("file3", "/repo/s3/src/mapper.py", "cplus_proj/s3", ["f3"]),
        ("file4", "/repo/xs3/src/decoy.py", "/Users/dev/workspaces/xs3", ["f4"]),
        ("file5", "/repo/s1/src/other.py", "/Users/dev/workspaces/s1", ["f5"]),
    ]
    for fid, fpath, repo, funcs in files:
        await driver.execute_query(
            "CREATE (f:File {id: $id, path: $path, file_path: $path, repo: $repo}) RETURN f.id AS id",
            {"id": fid, "path": fpath, "repo": repo},
            db,
        )
        for func_id in funcs:
            await driver.execute_query(
                "MATCH (f:File {id: $fid}), (n:Function {id: $nid}) CREATE (f)-[:CONTAINS]->(n) RETURN 1 AS ok",
                {"fid": fid, "nid": func_id},
                db,
            )

    # Class + DECLARES for listup_class_matching_path
    await driver.execute_query(
        "CREATE (c:Class {id: 'c1', name: 'HandlerClass', qualified_name: 'svc::HandlerClass', repo: '/Users/dev/workspaces/s3'}) "
        "RETURN c.id AS id",
        {},
        db,
    )
    await driver.execute_query(
        "MATCH (c:Class {id: 'c1'}), (f:Function {id: 'f2'}) CREATE (c)-[:DECLARES]->(f) RETURN 1 AS ok",
        {},
        db,
    )

    # IPC Message nodes (repo comes from message_scan writes)
    for mid, sender, receiver, repo in (
        ("m1", "svc_auth", "ui_panel", "/Users/dev/workspaces/s3"),
        ("m2", "svc_auth", "bg_worker", "cplus_proj/s3"),
        ("m3", "other_svc", "ui_panel", "/Users/dev/workspaces/s1"),
    ):
        repo_expr = "$repo" if repo is not None else "NULL"
        await driver.execute_query(
            f"CREATE (m:Message {{id: $id}}) SET m.sender = $sender, m.receiver = $receiver, "
            f"m.project_id = 'proj', m.project_id_normalized = 'proj', m.repo = {repo_expr} RETURN 1 AS ok",
            {"id": mid, "sender": sender, "receiver": receiver, "repo": repo},
            db,
        )

    # Workflow + HAS_STEP (Workflow nodes carry NO repo by design)
    await driver.execute_query(
        "CREATE (w:Workflow {workflow_id: 'wf1', name: 'Login Flow', domain: 'auth', confidence: 0.9, kind: 'screen'}) "
        "RETURN w.workflow_id AS id",
        {},
        db,
    )
    await driver.execute_query(
        "MATCH (w:Workflow {workflow_id: 'wf1'}), (f:Function {id: 'f1'}) "
        "CREATE (w)-[:HAS_STEP {order: 1}]->(f) RETURN 1 AS ok",
        {},
        db,
    )

    # Sanity: count check
    records, _, _ = await driver.execute_query(
        "MATCH (n) RETURN count(n) AS c", {}, db
    )
    print(f"seeded {records[0]['c']} nodes into {path}")
    driver.close()


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="repo_matrix_")) / "matrix.rdb"
    target.parent.mkdir(parents=True, exist_ok=True)
    asyncio.run(seed(target))
    print(target)
