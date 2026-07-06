import argparse
import os

from graph_store import add_graph_store_args, create_graph_store_from_args


def main() -> None:
    parser = argparse.ArgumentParser(description="Create graph indexes for GraphRAG.")
    add_graph_store_args(parser)
    parser.add_argument("--neo4j-uri", default=os.getenv("NEO4J_URI", "bolt://localhost:7687"))
    parser.add_argument("--neo4j-user", default=os.getenv("NEO4J_USER", "neo4j"))
    parser.add_argument("--neo4j-pass", default=os.getenv("NEO4J_PASS", "password"))
    args = parser.parse_args()

    store = create_graph_store_from_args(args)
    try:
        store.setup_indexes()
    finally:
        store.close()


if __name__ == "__main__":
    main()
