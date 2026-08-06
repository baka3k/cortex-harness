import argparse
import os
import sys

from qdrant_client.http import models as qmodels

from graph_store import add_graph_store_args, create_graph_store_from_args
from doc_local_qdrant import get_document_qdrant_store
from project_contract import (
    ProjectNotRegisteredError,
    project_id_lookup_key,
    resolve_project_targets,
)


def reset_graph(store, project_id: str | None = None, *, dry_run: bool = False) -> int:
    normalized = project_id_lookup_key(project_id)
    with store.session() as session:
        if normalized:
            result = session.run(
                "MATCH (n) WHERE n.project_id_normalized = $project_id_normalized "
                "RETURN count(n) AS count",
                project_id_normalized=normalized,
            ).single()
            count = int((result or {}).get("count", 0))
            if not dry_run and count:
                session.run(
                    "MATCH (n) WHERE n.project_id_normalized = $project_id_normalized "
                    "DETACH DELETE n",
                    project_id_normalized=normalized,
                )
            return count
        result = session.run("MATCH (n) RETURN count(n) AS count").single()
        count = int((result or {}).get("count", 0))
        if not dry_run and count:
            session.run("MATCH (n) DETACH DELETE n")
        return count


def reset_qdrant(
    collection: str,
    path: str | None = None,
    *,
    project_id: str | None = None,
    dry_run: bool = False,
) -> int:
    client = get_document_qdrant_store(path)
    try:
        normalized = project_id_lookup_key(project_id)
        project_filter = (
            qmodels.Filter(
                must=[qmodels.FieldCondition(
                    key="project_id_normalized",
                    match=qmodels.MatchValue(value=normalized),
                )]
            )
            if normalized
            else None
        )
        count_result = client.count(
            collection_name=collection,
            count_filter=project_filter,
            exact=True,
        )
        count = int(getattr(count_result, "count", 0))
        if not dry_run and normalized and count:
            client.delete(
                collection,
                filter_selector=qmodels.FilterSelector(filter=project_filter),
                wait=True,
            )
        elif not dry_run and not normalized:
            client.delete_collection(collection)
        return count
    except Exception as exc:
        message = str(exc)
        if "Not found" not in message and "does not exist" not in message:
            raise
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset graph data and Qdrant collection.")
    add_graph_store_args(parser)
    parser.add_argument("--neo4j-uri", default=os.getenv("NEO4J_URI", "bolt://localhost:7687"))
    parser.add_argument("--neo4j-user", default=os.getenv("NEO4J_USER", "neo4j"))
    parser.add_argument("--neo4j-pass", default=os.getenv("NEO4J_PASS", "password"))
    parser.add_argument("--qdrant-path", default=os.getenv("QDRANT_DOC_PATH"))
    parser.add_argument(
        "--qdrant-collection",
        default=os.getenv("QDRANT_COLLECTION_DOC"),
    )
    parser.add_argument("--project-id", default=os.getenv("PROJECT_ID"))
    parser.add_argument(
        "--dry-run", action="store_true", help="Report matched nodes/points without deleting"
    )
    parser.add_argument(
        "--force", action="store_true", help="Required for non-interactive deletion"
    )
    args = parser.parse_args()

    if args.project_id:
        try:
            targets = resolve_project_targets(args.project_id)
        except ProjectNotRegisteredError as exc:
            raise SystemExit(str(exc)) from exc
        if "--falkordb-graph" not in sys.argv and not os.getenv("FALKORDB_GRAPH"):
            args.falkordb_graph = targets.doc_graph
        if not args.qdrant_collection:
            args.qdrant_collection = targets.doc_qdrant_collection
    elif not args.qdrant_collection:
        args.qdrant_collection = "graphrag_entities"

    if not args.dry_run and not args.force:
        raise SystemExit("Refusing destructive reset without --force; use --dry-run first.")

    store = create_graph_store_from_args(args)
    scope = f"project {args.project_id!r}" if args.project_id else "the entire graph"
    print(f"{'Inspecting' if args.dry_run else 'Resetting'} {store.provider} graph for {scope}...")
    try:
        graph_count = reset_graph(store, args.project_id, dry_run=args.dry_run)
    finally:
        store.close()
    print(f"Graph nodes matched: {graph_count}")

    print(f"{'Inspecting' if args.dry_run else 'Resetting'} Qdrant collection...")
    qdrant_count = reset_qdrant(
        args.qdrant_collection,
        args.qdrant_path,
        project_id=args.project_id,
        dry_run=args.dry_run,
    )
    print(f"Qdrant points matched: {qdrant_count}")
    if args.dry_run:
        print("Dry-run complete; no data deleted.")
    else:
        print("Reset complete.")


if __name__ == "__main__":
    main()
