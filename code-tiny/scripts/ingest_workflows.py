"""ingest_workflows.py
─────────────────────
Derives :Workflow nodes from NAVIGATE edges between screen files in Neo4j.

Algorithm
─────────
1. Query ALL NAVIGATE edges where src or tgt file_path contains '/screens/'
2. Build directed screen-navigation graph
3. Find entry screens = nodes with NO incoming NAVIGATE (user-initiated entry points)
4. For each entry screen: BFS traversal → ordered step list
5. Name the workflow from path structure (+ optional Ollama enrichment)
6. DELETE old Workflow nodes for this project
7. MERGE new :Workflow nodes + :HAS_STEP edges (pointing only to Screen-path nodes)

Result: one workflow per distinct entry screen. Each workflow shows:
- ordered screen path the user follows
- domain classification
- human-readable name and description

Usage
─────
    python scripts/ingest_workflows.py \\
        [--project-id  mfx-miniapps] \\
        [--neo4j-uri   bolt://localhost:7687] \\
        [--neo4j-user  neo4j] \\
        [--neo4j-password <pw>] \\
        [--neo4j-db    neo4j] \\
        [--dry-run] \\
        [--no-ollama]  \\
        [--ollama-model llama3.2:latest]

Environment fallbacks: NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, NEO4J_DB
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import re
import sys
from collections import deque
from typing import Any, Dict, List, Optional, Set, Tuple

_FERNET_TOKEN_RE = re.compile(r'^gAAAAA')


def _maybe_decrypt_password(password: str) -> str:
    if not _FERNET_TOKEN_RE.match(password):
        return password
    try:
        from cryptography.fernet import Fernet
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    except ImportError:
        return password
    enc_pw = os.environ.get("HYPER_PACK_ENCRYPTION_PASSWORD", "my-secret-encryption-key-2026")
    try:
        kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=b"static_salt_2026", iterations=100_000)
        key = base64.urlsafe_b64encode(kdf.derive(enc_pw.encode("utf-8")))
        return Fernet(key).decrypt(password.encode("utf-8")).decode("utf-8")
    except Exception as exc:
        print(f"[ingest_workflows] warning: could not decrypt NEO4J_PASS ({exc})", file=sys.stderr)
        return password


try:
    from neo4j import AsyncGraphDatabase
except ImportError:
    print("ERROR: neo4j package not installed. Run: pip install neo4j", file=sys.stderr)
    sys.exit(1)

# ── Domain lookup: dir name → domain ─────────────────────────────────────────
_DIR_DOMAIN: Dict[str, str] = {
    "reward":                  "loyalty",
    "rewardhistory":           "loyalty",
    "proposal":                "commendation",
    "proposallist":            "commendation",
    "celebration":             "commendation",
    "recognition":             "recognition",
    "recognitionproposal":     "recognition",
    "recognitionproposalcreation": "recognition",
    "thanks":                  "commendation",
    "commendationx":           "commendation",
    "auth":                    "auth",
    "login":                   "auth",
    "payment":                 "payment",
    "checkout":                "payment",
    "profile":                 "profile",
    "kyc":                     "kyc",
    "support":                 "support",
    "home":                    "navigation",
    "digitalcertificate":      "certification",
    "certificatesview":        "certification",
    "selectapprover":          "commendation",
    "selectrepresentative":    "commendation",
    "sampledata":              "sample",
    "samplemessagex":          "sample",
}


_INDEX_FILES = {"index.tsx", "index.ts", "index.jsx", "index.js"}
_EXT_RE = re.compile(r'\.(tsx?|jsx?)$')


def _display_name(file_path: str) -> str:
    """Return a human-readable name for a screen path.

    For `index.tsx`-style files, uses the parent folder name so that
    `src/screens/Reward/RewardHistory/index.tsx` → 'Reward History'
    instead of 'index'.
    """
    parts = file_path.replace("\\", "/").split("/")
    filename = parts[-1]
    if filename in _INDEX_FILES and len(parts) >= 2:
        # Use parent folder
        raw = parts[-2]
    else:
        raw = _EXT_RE.sub("", filename)
    return re.sub(r'([A-Z])', r' \1', raw).strip()


def _extract_path_parts(file_path: str) -> List[str]:
    """Return meaningful path segments below /screens/, substituting index.tsx → parent folder."""
    parts = file_path.replace("\\", "/").split("/")
    try:
        idx = parts.index("screens")
        result = []
        segs = parts[idx+1:]
        for i, p in enumerate(segs):
            if not p or p.lower() == "components":
                continue
            if p in _INDEX_FILES:
                # replace with parent folder if available and not already added
                parent = segs[i-1] if i > 0 else None
                if parent and parent not in result and parent.lower() != "components":
                    result.append(parent)
                # else skip — parent already in list
            else:
                result.append(p)
        return result or [parts[-2] if len(parts) >= 2 else parts[-1]]
    except ValueError:
        # Not under /screens/ — use parent folder if index file
        filename = parts[-1]
        if filename in _INDEX_FILES and len(parts) >= 2:
            return [parts[-2]]
        return [_EXT_RE.sub("", filename)]


def _screen_domain(file_path: str) -> str:
    parts = file_path.split("/")
    try:
        idx = parts.index("screens")
        dir_name = parts[idx + 1].lower() if idx + 1 < len(parts) else ""
    except ValueError:
        dir_name = ""
    return _DIR_DOMAIN.get(dir_name, dir_name or "other")


def _workflow_name_from_path(file_path: str) -> str:
    """Build a human-readable workflow name from the entry screen's path.
    
    Uses up to 3 path segments. Keeps distinguishing tokens like 'Group',
    'Individual', 'Create', 'FormProposal' even if they seem noisy — they
    disambiguate sibling entry points that share a parent directory.
    """
    path_parts = _extract_path_parts(file_path)
    # Pure noise: top-level dir words that add nothing on their own
    noise_exact = {"index", "components"}
    meaningful = [p for p in path_parts if p.lower() not in noise_exact]
    if not meaningful:
        meaningful = path_parts[:3]

    name_parts: List[str] = []
    for part in meaningful[:3]:
        part = re.sub(r'\.(tsx?|jsx?)$', '', part)
        words = re.sub(r'([A-Z])', r' \1', part).strip().split()
        name_parts.extend(words)

    # Deduplicate consecutive same words
    deduped: List[str] = []
    for w in name_parts:
        if not deduped or w.lower() != deduped[-1].lower():
            deduped.append(w)

    base = " ".join(deduped[:6]).strip()  # max 6 words
    if not base:
        base = path_parts[0] if path_parts else "Screen"

    if not base.lower().endswith("flow"):
        base += " Flow"
    return base


def _workflow_description(entry_path: str, steps: List[str], domain: str) -> str:
    """Build a brief description: 'User navigates from X through A → B → C.'"""
    screen_steps = [s for s in steps if "/screens/" in s]
    step_names = [_display_name(s) for s in screen_steps[:5]]
    entry_name = _display_name(entry_path)
    tail = "…" if len(screen_steps) > 5 else ""
    if len(step_names) <= 1:
        return f"User opens {entry_name}."
    return (
        f"User navigates from {step_names[0]} "
        f"through {' → '.join(step_names[1:4])}{tail}."
    )


def _stable_id(items: List[str]) -> str:
    raw = "|".join(sorted(items))
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _bfs_reachable(start: str, forward: Dict[str, List[str]]) -> List[str]:
    seen: Set[str] = set()
    queue: deque = deque([start])
    result: List[str] = []
    seen.add(start)
    while queue:
        node = queue.popleft()
        result.append(node)
        for nxt in forward.get(node, []):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return result


# ── Neo4j queries ─────────────────────────────────────────────────────────────

QUERY_NAVIGATE_EDGES = """
MATCH (a)-[:NAVIGATE]->(b)
WHERE a.file_path CONTAINS '/screens/' OR b.file_path CONTAINS '/screens/'
RETURN
    a.file_path   AS src_fp,
    a.name        AS src_name,
    elementId(a)  AS src_eid,
    b.file_path   AS tgt_fp,
    b.name        AS tgt_name,
    elementId(b)  AS tgt_eid
"""

DELETE_OLD_WORKFLOWS = """
MATCH (w:Workflow {project: $project})
DETACH DELETE w
"""

MERGE_WORKFLOW = """
UNWIND $rows AS row
MERGE (w:Workflow {workflow_id: row.workflow_id})
SET   w.name          = row.workflow_name,
      w.domain        = row.domain,
      w.description   = row.description,
      w.confidence    = row.confidence,
      w.entrypoint_id = row.entrypoint_id,
      w.language      = 'ts',
      w.project       = row.project,
      w.updated_at    = datetime()
RETURN count(w) AS written
"""

MERGE_HAS_STEP = """
UNWIND $rows AS row
MATCH  (w:Workflow {workflow_id: row.workflow_id})
CALL {
  WITH row
  MATCH (f)
  WHERE f.file_path = row.file_path
  RETURN f LIMIT 1
}
MERGE  (w)-[s:HAS_STEP {order: row.step_order}]->(f)
RETURN count(s) AS written
"""


# ── Ollama enrichment ─────────────────────────────────────────────────────────

def _ollama_enrich(workflows: List[Dict[str, Any]], model: str) -> List[Dict[str, Any]]:
    try:
        import ollama as _ollama  # noqa: PLC0415
    except ImportError:
        print("  Ollama not available — using heuristic names only")
        return workflows

    SYSTEM = (
        "You are a React Native UX analyst. Given a list of navigation flows, "
        "each with an id and list of screen file paths, return a JSON array. "
        "Each element must have: workflow_name (user-facing, e.g. 'Create Group Proposal'), "
        "domain (auth|payment|loyalty|commendation|recognition|kyc|profile|navigation|certification|other), "
        "description (1 short sentence: what the user does in this flow). "
        "IMPORTANT: workflow_name must NOT include the word 'Flow' — it will be appended automatically. "
        "Output ONLY a valid JSON array with exactly the same number of elements as the input. No markdown."
    )
    items = [{"id": w["workflow_id"], "screens": w["step_files"][:8]} for w in workflows]

    try:
        resp = _ollama.chat(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user",   "content": json.dumps(items, ensure_ascii=False)},
            ],
            stream=False,
        )
        raw = ((resp.get("message") or {}).get("content") or "").strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        enrichments = json.loads(raw)
        for i, w in enumerate(workflows):
            if i < len(enrichments) and isinstance(enrichments[i], dict):
                e = enrichments[i]
                if e.get("workflow_name"):
                    name = e["workflow_name"].strip()
                    if not name.lower().endswith("flow"):
                        name += " Flow"
                    w["workflow_name"] = name
                if e.get("domain"):
                    w["domain"] = e["domain"]
                if e.get("description"):
                    w["description"] = e["description"]
                w["confidence"] = 0.9
        print(f"  Ollama enriched {min(len(enrichments), len(workflows))} workflows")
    except Exception as exc:
        print(f"  Ollama enrichment failed ({exc}) — using heuristic names")

    return workflows


# ── Core logic ────────────────────────────────────────────────────────────────

async def build_workflows(
    driver,
    database: str,
    project_id: str,
    ollama_model: Optional[str],
) -> List[Dict[str, Any]]:

    # 1. Load all NAVIGATE edges involving screen paths
    async with driver.session(database=database) as session:
        result = await session.run(QUERY_NAVIGATE_EDGES)
        edge_recs = [dict(r) async for r in result]

    print(f"  Found {len(edge_recs)} NAVIGATE edges involving /screens/")
    if not edge_recs:
        return []

    # 2. Build forward/backward adjacency + eid lookup
    forward: Dict[str, List[str]] = {}
    backward: Dict[str, List[str]] = {}
    node_eid: Dict[str, str] = {}

    for rec in edge_recs:
        src = rec.get("src_fp") or ""
        tgt = rec.get("tgt_fp") or ""
        if not src or not tgt:
            continue
        forward.setdefault(src, []).append(tgt)
        backward.setdefault(tgt, []).append(src)
        if rec.get("src_eid"):
            node_eid[src] = rec["src_eid"]
        if rec.get("tgt_eid"):
            node_eid[tgt] = rec["tgt_eid"]

    all_nodes = set(forward.keys()) | set(backward.keys())

    # 3. Entry points = nodes with outgoing NAVIGATE but NO incoming NAVIGATE
    entry_points = sorted([n for n in all_nodes if not backward.get(n) and forward.get(n)])
    print(f"  {len(entry_points)} entry screens identified")

    # 4. Build one workflow per entry point
    workflows: List[Dict[str, Any]] = []

    for entry in entry_points:
        all_reachable = _bfs_reachable(entry, forward)
        # STEPS = only screen-path nodes, maintaining BFS order
        screen_steps = [n for n in all_reachable if "/screens/" in n]
        # Sub-nodes = non-screen impacted nodes (components, navigation, etc.)
        sub_nodes = [n for n in all_reachable if "/screens/" not in n]
        domain = _screen_domain(entry)
        wf_name = _workflow_name_from_path(entry)
        wf_id = _stable_id(screen_steps)

        workflows.append({
            "workflow_id":    wf_id,
            "workflow_name":  wf_name,
            "domain":         domain,
            "description":    _workflow_description(entry, screen_steps, domain),
            "confidence":     0.75,
            "entrypoint_id":  node_eid.get(entry, entry),
            "project":        project_id,
            "step_files":     screen_steps,  # ordered screen navigation path
            "sub_node_files": sub_nodes,     # impacted non-screen nodes
        })

    # 5. Optional Ollama enrichment
    if ollama_model and workflows:
        print(f"  Calling Ollama ({ollama_model}) for semantic enrichment…")
        workflows = _ollama_enrich(workflows, ollama_model)

    return workflows


async def write_to_neo4j(
    driver,
    database: str,
    workflows: List[Dict[str, Any]],
    project_id: str,
    dry_run: bool,
) -> None:
    if not workflows:
        print("  Nothing to write.")
        return

    print(f"\n  {len(workflows)} workflows | step totals:")
    for w in workflows:
        steps_preview = " → ".join(_display_name(s) for s in w["step_files"][:5])
        sub_count = len(w.get("sub_node_files", []))
        marker = "(Ollama)" if w["confidence"] >= 0.9 else ""
        print(f"    [{w['domain']:14s}] {w['workflow_name']:<45s} {len(w['step_files'])} steps, {sub_count} sub-nodes  {marker}")
        print(f"               {steps_preview}{'…' if len(w['step_files']) > 5 else ''}")

    if dry_run:
        return

    # Delete old workflows
    async with driver.session(database=database) as session:
        r = await session.run(DELETE_OLD_WORKFLOWS, {"project": project_id})
        await r.consume()
        print(f"\n  Deleted old Workflow nodes for project='{project_id}'")

    # Write Workflow nodes
    wf_rows = [{
        "workflow_id":   w["workflow_id"],
        "workflow_name": w["workflow_name"],
        "domain":        w["domain"],
        "description":   w["description"],
        "confidence":    w["confidence"],
        "entrypoint_id": w["entrypoint_id"],
        "project":       w["project"],
    } for w in workflows]

    async with driver.session(database=database) as session:
        r = await session.run(MERGE_WORKFLOW, {"rows": wf_rows})
        rec = await r.single()
        print(f"  Wrote {rec['written']} :Workflow nodes")

    # Write HAS_STEP edges in batches
    step_rows = []
    for w in workflows:
        for order, fp in enumerate(w["step_files"]):
            step_rows.append({"workflow_id": w["workflow_id"], "file_path": fp, "step_order": order})

    total_steps = 0
    for i in range(0, len(step_rows), 200):
        batch = step_rows[i:i+200]
        async with driver.session(database=database) as session:
            r = await session.run(MERGE_HAS_STEP, {"rows": batch})
            rec = await r.single()
            total_steps += rec["written"] if rec else 0

    print(f"  Wrote {total_steps} :HAS_STEP edges (screen nodes only, ordered by navigation)")


async def main_async(args: argparse.Namespace) -> None:
    print(f"Connecting to Neo4j: {args.neo4j_uri}  db={args.neo4j_db}")
    driver = AsyncGraphDatabase.driver(args.neo4j_uri, auth=(args.neo4j_user, _maybe_decrypt_password(args.neo4j_password)))

    try:
        async with driver.session(database=args.neo4j_db) as session:
            result = await session.run("RETURN 1 AS ok")
            await result.single()
        print("  Connection OK\n")

        ollama_model = None if args.no_ollama else args.ollama_model

        print("Step 1: Building workflows from NAVIGATE edges…")
        workflows = await build_workflows(driver, args.neo4j_db, args.project_id, ollama_model)
        print(f"  → {len(workflows)} workflows derived\n")

        if not workflows:
            print("No NAVIGATE edges found between screen nodes. Ensure the parser ran.")
            return

        print(f"Step 2: Writing to Neo4j (dry_run={args.dry_run})…")
        await write_to_neo4j(driver, args.neo4j_db, workflows, args.project_id, args.dry_run)
        print("\nDone.")
    finally:
        await driver.close()


def main() -> None:
    p = argparse.ArgumentParser(description="Ingest :Workflow nodes from screen NAVIGATE edges.")
    p.add_argument("--project-id",     default=os.environ.get("PROJECT_ID", "mfx-miniapps"))
    p.add_argument("--neo4j-uri",      default=os.environ.get("NEO4J_URI",      "bolt://localhost:7687"))
    p.add_argument("--neo4j-user",     default=os.environ.get("NEO4J_USER",     "neo4j"))
    p.add_argument("--neo4j-password", default=os.environ.get("NEO4J_PASSWORD", "neo4j"))
    p.add_argument("--neo4j-db",       default=os.environ.get("NEO4J_DB",       "neo4j"))
    p.add_argument("--ollama-model",   default="llama3.2:latest")
    p.add_argument("--no-ollama",      action="store_true", help="Skip Ollama, use heuristic names")
    p.add_argument("--dry-run",        action="store_true", help="Preview without writing")
    asyncio.run(main_async(p.parse_args()))


if __name__ == "__main__":
    main()
