"""
LivingDoc Pipeline — run all phases in order.

Phases:
  1. summarize       — query Neo4j → LLM → cache/*.json + _index.jsonl
  2. vectorize       — cache/*.json → embed → Neo4j SET summary + Qdrant upsert
  3. link            — cache/*.json → embed → Qdrant search → MERGE Paragraph/Document links
  4. louvain         — GDS Louvain → SET communityId + MERGE InfraNode + BELONGS_TO
  5. summarize-infra — query InfraNode (pending_summary) → collect member summaries → LLM → SET name/summary
  6. vectorize-infra  — InfraNode (summarized) → embed name+summary → Qdrant upsert

Usage examples:
  # Run all phases with defaults:
  python3 livingdoc/living-doc-pipeline.py --neo4j-pass abcd1234

  # Run only vectorize:
  python3 livingdoc/living-doc-pipeline.py --neo4j-pass abcd1234 --only vectorize

  # Skip summarize (already done):
  python3 livingdoc/living-doc-pipeline.py --neo4j-pass abcd1234 --skip-summarize
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path


PHASES = ["summarize", "vectorize", "link", "louvain", "summarize-infra", "vectorize-infra"]


def get_env(name, default=None):
    return os.getenv(name, default)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run LivingDoc phases in order: summarize, vectorize, link, louvain.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # ── Phase selection ──────────────────────────────────────────────────────
    parser.add_argument("--skip-summarize", action="store_true", help="Skip summarize phase")
    parser.add_argument("--skip-vectorize", action="store_true", help="Skip vectorize phase")
    parser.add_argument("--skip-link",      action="store_true", help="Skip link phase")
    parser.add_argument("--skip-louvain",          action="store_true", help="Skip louvain phase")
    parser.add_argument("--skip-summarize-infra", action="store_true", help="Skip summarize-infra phase")
    parser.add_argument("--skip-vectorize-infra", action="store_true", help="Skip vectorize-infra phase")
    parser.add_argument(
        "--only",
        choices=PHASES,
        help="Run a single phase only.",
    )

    # ── Common: Neo4j ─────────────────────────────────────────────────────────
    parser.add_argument("--neo4j-uri",      default=get_env("NEO4J_URI",      "bolt://localhost:7687"))
    parser.add_argument("--neo4j-user",     default=get_env("NEO4J_USER",     "neo4j"))
    parser.add_argument("--neo4j-pass", default=get_env("NEO4J_PASS"))
    parser.add_argument("--project-id",     default=get_env("PROJECT_ID",     "digital_key_main"))

    # ── Common: Embedding ─────────────────────────────────────────────────────
    parser.add_argument("--embed-model",  default=get_env("CODE_EMBEDDING_MODEL",  "BAAI/bge-m3"))
    parser.add_argument("--embed-device", default=get_env("EMBEDDING_DEVICE", "mps"))

    # ── Common: Qdrant ───────────────────────────────────────────────────────
    parser.add_argument("--qdrant-url",        default=get_env("QDRANT_URL",        "http://localhost:6333"))
    parser.add_argument("--qdrant-collection", default=get_env("QDRANT_COLLECTION_CODE", "graph_rag_entities"))

    # ── Common: cache ────────────────────────────────────────────────────────
    parser.add_argument("--cache-dir", default=get_env("CACHE_DIR", "cache"))

    # ── Step 1: Summarize ────────────────────────────────────────────────────
    parser.add_argument("--llm-api-base",  default=get_env("LLM_API_BASE",  "http://localhost:11434/v1"))
    parser.add_argument("--llm-api-key",   default=get_env("LLM_API_KEY",   "local"))
    parser.add_argument("--llm-model",     default=get_env("LLM_MODEL",     "deepseek-coder-v2"))
    parser.add_argument("--node-labels",   default=get_env("NODE_LABELS",   "Function,Class,AndroidComponent"))
    parser.add_argument("--nodes-list-path", default=get_env("NODES_LIST_PATH", "cache/_nodes.jsonl"))
    parser.add_argument(
        "--summarize-skip-existing",
        default=get_env("SUMMARIZE_SKIP_EXISTING", "1"),
        help="Set to 0 to re-summarize nodes that already have a cache file.",
    )

    # ── Step 2: Vectorize ────────────────────────────────────────────────────
    parser.add_argument(
        "--vectorize-skip-existing",
        default=get_env("VECTORIZE_SKIP_EXISTING", "0"),
        help="Set to 1 to skip nodes already in Qdrant.",
    )
    parser.add_argument(
        "--qdrant-create",
        default=get_env("QDRANT_CREATE", "1"),
        help="Set to 0 to skip auto-create Qdrant collection.",
    )
    parser.add_argument(
        "--require-index",
        default=get_env("REQUIRE_INDEX", "1"),
        help="Set to 0 to allow cache files without _index.jsonl mapping.",
    )

    # ── Step 3: Link ─────────────────────────────────────────────────────────
    parser.add_argument("--top-k",           type=int,   default=int(get_env("TOP_K",            "3")))
    parser.add_argument("--score-threshold", type=float, default=float(get_env("SCORE_THRESHOLD", "0.6")))
    parser.add_argument(
        "--link-both",
        default=get_env("LINK_BOTH", "1"),
        help="Set to 1 to link both Paragraph and Document nodes.",
    )

    # ── Step 5: Summarize-Infra ─────────────────────────────────────────────
    parser.add_argument("--infra-pending-status", default=get_env("PENDING_STATUS",  "pending_summary"))
    parser.add_argument("--infra-done-status",    default=get_env("DONE_STATUS",     "summarized"))
    parser.add_argument("--infra-min-members",    type=int, default=int(get_env("MIN_MEMBERS", "2")))
    parser.add_argument("--infra-max-functions",  type=int, default=int(get_env("MAX_FUNCTIONS", "30")))
    parser.add_argument("--infra-llm-sleep",      type=float, default=float(get_env("INFRA_LLM_SLEEP", "0")))

    # ── Step 4: Louvain ──────────────────────────────────────────────────────
    parser.add_argument("--graph-name",         default=get_env("GDS_GRAPH_NAME",     "functionGraph"))
    parser.add_argument("--node-label",         default=get_env("NODE_LABEL",         "Function"))
    parser.add_argument("--rel-type",           default=get_env("REL_TYPE",           "CALLS"))
    parser.add_argument("--orientation",        default=get_env("ORIENTATION",        "UNDIRECTED"))
    parser.add_argument("--write-property",     default=get_env("WRITE_PROPERTY",     "communityId"))
    parser.add_argument("--min-community-size", type=int, default=int(get_env("MIN_COMMUNITY_SIZE", "4")))
    parser.add_argument("--infra-label",        default=get_env("INFRA_LABEL",        "InfraNode"))
    parser.add_argument("--infra-id-field",     default=get_env("INFRA_ID_FIELD",     "id"))
    parser.add_argument("--infra-status",       default=get_env("INFRA_STATUS",       "pending_summary"))
    parser.add_argument("--belongs-rel",        default=get_env("BELONGS_REL",        "BELONGS_TO"))
    parser.add_argument(
        "--drop-graph",
        default=get_env("DROP_GRAPH", "0"),
        help="Set to 1 to drop existing GDS in-memory graph before projecting.",
    )
    parser.add_argument(
        "--drop-after",
        default=get_env("DROP_AFTER", "0"),
        help="Set to 1 to drop GDS graph after Louvain finishes.",
    )

    # ── Misc ─────────────────────────────────────────────────────────────────
    parser.add_argument("--verbose", action="store_true")

    args = parser.parse_args()
    if not args.NEO4J_PASS:
        print("Missing required: NEO4J_PASS/--neo4j-pass", file=sys.stderr)
        sys.exit(2)
    return args


def run_phase(phase: str, cmd: list):
    print(f"\n{'='*60}")
    print(f"  PHASE: {phase.upper()}")
    print(f"{'='*60}")
    print("  " + " \\\n    ".join(cmd))
    print()
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        print(f"\n[pipeline] Phase '{phase}' FAILED (exit {result.returncode})", file=sys.stderr)
        sys.exit(result.returncode)
    print(f"\n[pipeline] Phase '{phase}' completed OK")


def build_summarize_cmd(args, base_dir) -> list:
    return [
        sys.executable, str(base_dir / "living-doc-summarize.py"),
        "--neo4j-uri",       args.neo4j_uri,
        "--neo4j-user",      args.neo4j_user,
        "--neo4j-pass",  args.NEO4J_PASS,
        "--llm-api-base",    args.llm_api_base,
        "--llm-api-key",     args.llm_api_key,
        "--llm-model",       args.llm_model,
        "--project-id",      args.project_id,
        "--node-labels",     args.node_labels,
        "--cache-dir",       args.cache_dir,
        "--nodes-list-path", args.nodes_list_path,
        "--skip-existing",   args.summarize_skip_existing,
    ] + (["--verbose"] if args.verbose else [])


def build_vectorize_cmd(args, base_dir) -> list:
    return [
        sys.executable, str(base_dir / "living-doc-vectorize.py"),
        "--neo4j-uri",      args.neo4j_uri,
        "--neo4j-user",     args.neo4j_user,
        "--neo4j-pass", args.NEO4J_PASS,
        "--cache-dir",      args.cache_dir,
        "--embed-model",    args.embed_model,
        "--embed-device",   args.embed_device,
        "--qdrant-url",     args.qdrant_url,
        "--collection",     args.qdrant_collection,
        "--qdrant-create",  args.qdrant_create,
        "--skip-existing",  args.vectorize_skip_existing,
        "--require-index",  args.require_index,
    ] + (["--verbose"] if args.verbose else [])


def build_link_cmd(args, base_dir) -> list:
    return [
        sys.executable, str(base_dir / "living-doc-link.py"),
        "--neo4j-uri",       args.neo4j_uri,
        "--neo4j-user",      args.neo4j_user,
        "--neo4j-pass",  args.NEO4J_PASS,
        "--cache-dir",       args.cache_dir,
        "--collection",      args.qdrant_collection,
        "--embed-model",     args.embed_model,
        "--embed-device",    args.embed_device,
        "--qdrant-url",      args.qdrant_url,
        "--top-k",           str(args.top_k),
        "--score-threshold", str(args.score_threshold),
        "--require-index",   args.require_index,
        "--link-both",       args.link_both,
    ] + (["--verbose"] if args.verbose else [])


def build_summarize_infra_cmd(args, base_dir) -> list:
    return [
        sys.executable, str(base_dir / "living-doc-summarize-infra.py"),
        "--neo4j-uri",          args.neo4j_uri,
        "--neo4j-user",         args.neo4j_user,
        "--neo4j-pass",     args.NEO4J_PASS,
        "--project-id",         args.project_id,
        "--infra-label",        args.infra_label,
        "--belongs-rel",        args.belongs_rel,
        "--node-label",         args.node_label,
        "--llm-api-base",       args.llm_api_base,
        "--llm-api-key",        args.llm_api_key,
        "--llm-model",          args.llm_model,
        "--pending-status",     args.infra_pending_status,
        "--done-status",        args.infra_done_status,
        "--min-members",        str(args.infra_min_members),
        "--max-functions",      str(args.infra_max_functions),
        "--llm-sleep",          str(args.infra_llm_sleep),
        "--skip-existing",      args.summarize_skip_existing,
    ] + (["--verbose"] if args.verbose else [])


def build_vectorize_infra_cmd(args, base_dir) -> list:
    return [
        sys.executable, str(base_dir / "living-doc-vectorize-infra.py"),
        "--neo4j-uri",      args.neo4j_uri,
        "--neo4j-user",     args.neo4j_user,
        "--neo4j-pass", args.NEO4J_PASS,
        "--project-id",     args.project_id,
        "--infra-label",    args.infra_label,
        "--done-status",    args.infra_done_status,
        "--embed-model",    args.embed_model,
        "--embed-device",   args.embed_device,
        "--qdrant-url",     args.qdrant_url,
        "--collection",     args.qdrant_collection,
        "--qdrant-create",  args.qdrant_create,
        "--skip-existing",  args.vectorize_skip_existing,
        "--cache-dir",      args.cache_dir,
    ] + (["--verbose"] if args.verbose else [])


def build_louvain_cmd(args, base_dir) -> list:
    return [
        sys.executable, str(base_dir / "living-doc-louvain.py"),
        "--neo4j-uri",          args.neo4j_uri,
        "--neo4j-user",         args.neo4j_user,
        "--neo4j-pass",     args.NEO4J_PASS,
        "--project-id",         args.project_id,
        "--graph-name",         args.graph_name,
        "--node-label",         args.node_label,
        "--rel-type",           args.rel_type,
        "--orientation",        args.orientation,
        "--write-property",     args.write_property,
        "--min-community-size", str(args.min_community_size),
        "--infra-label",        args.infra_label,
        "--infra-id-field",     args.infra_id_field,
        "--infra-status",       args.infra_status,
        "--belongs-rel",        args.belongs_rel,
        "--drop-graph",         args.drop_graph,
        "--drop-after",         args.drop_after,
    ] + (["--verbose"] if args.verbose else [])


def main():
    args = parse_args()
    base_dir = Path(__file__).resolve().parent

    builders = {
        "summarize":       build_summarize_cmd,
        "vectorize":       build_vectorize_cmd,
        "link":            build_link_cmd,
        "louvain":         build_louvain_cmd,
        "summarize-infra": build_summarize_infra_cmd,
        "vectorize-infra": build_vectorize_infra_cmd,
    }

    skip_flags = {
        "summarize":       args.skip_summarize,
        "vectorize":       args.skip_vectorize,
        "link":            args.skip_link,
        "louvain":         args.skip_louvain,
        "summarize-infra": args.skip_summarize_infra,
        "vectorize-infra": args.skip_vectorize_infra,
    }

    phases_to_run = [args.only] if args.only else PHASES

    for phase in phases_to_run:
        if not args.only and skip_flags.get(phase):
            print(f"[pipeline] Skipping phase: {phase}")
            continue
        script = base_dir / f"living-doc-{phase}.py"
        if not script.exists():
            print(f"[pipeline] Missing script: {script}", file=sys.stderr)
            sys.exit(2)
        cmd = builders[phase](args, base_dir)
        run_phase(phase, cmd)

    print("\n[pipeline] All phases completed.")


if __name__ == "__main__":
    main()
