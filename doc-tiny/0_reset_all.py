import argparse
import os

from qdrant_client import QdrantClient

from graph_store import add_graph_store_args, create_graph_store_from_args


def reset_graph(store) -> None:
    with store.session() as session:
        session.run("MATCH (n) DETACH DELETE n")


def reset_qdrant(
    collection: str,
    url: str | None,
    host: str,
    port: int,
    api_key: str | None,
) -> None:
    if url:
        client = QdrantClient(url=url, api_key=api_key)
    else:
        client = QdrantClient(host=host, port=port, api_key=api_key)
    try:
        client.delete_collection(collection_name=collection)
    except Exception as exc:
        message = str(exc)
        if "Not found" not in message and "does not exist" not in message:
            raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset graph data and Qdrant collection.")
    add_graph_store_args(parser)
    parser.add_argument("--neo4j-uri", default=os.getenv("NEO4J_URI", "bolt://localhost:7687"))
    parser.add_argument("--neo4j-user", default=os.getenv("NEO4J_USER", "neo4j"))
    parser.add_argument("--neo4j-pass", default=os.getenv("NEO4J_PASS", "password"))
    parser.add_argument("--qdrant-url", default=os.getenv("QDRANT_URL"))
    parser.add_argument("--qdrant-host", default=os.getenv("QDRANT_HOST", "localhost"))
    parser.add_argument("--qdrant-port", type=int, default=int(os.getenv("QDRANT_PORT", "6333")))
    parser.add_argument("--qdrant-api-key", default=os.getenv("QDRANT_KEY"))
    parser.add_argument(
        "--qdrant-collection",
        default=os.getenv("QDRANT_COLLECTION_DOC", "graphrag_entities"),
    )
    args = parser.parse_args()

    store = create_graph_store_from_args(args)
    print(f"Resetting {store.provider} graph...")
    try:
        reset_graph(store)
    finally:
        store.close()
    print("Graph reset complete.")

    print("Resetting Qdrant collection...")
    reset_qdrant(
        args.qdrant_collection,
        args.qdrant_url,
        args.qdrant_host,
        args.qdrant_port,
        args.qdrant_api_key,
    )
    print("Qdrant reset complete.")


if __name__ == "__main__":
    main()
