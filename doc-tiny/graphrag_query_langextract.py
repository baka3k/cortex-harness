import argparse
import os
import textwrap
from pathlib import Path
from typing import Any, Dict, List, Tuple

from sentence_transformers import SentenceTransformer

from embedding_utils import resolve_embedding_device, resolve_embedding_model, st_query_encode_kwargs, DEFAULT_DOC_EMBEDDING_MODEL
from graph_store import add_graph_store_args, create_graph_store_from_args
from doc_local_qdrant import get_document_qdrant_store

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None


def _load_env() -> None:
    if load_dotenv is None:
        return
    env_path = Path(__file__).with_name(".env")
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
    else:
        load_dotenv()


def qdrant_search(
    client: Any, collection: str, query_vector: List[float], top_k: int,
    sparse_vector: Tuple[List[int], List[float]] | None = None,
) -> List[Dict[str, Any]]:
    if sparse_vector is not None and hasattr(client, "query_points"):
        # Fused dense+sparse RRF (only reached when the caller verified the
        # collection declares the sparse vector).
        try:
            from tools.common.qdrant_query_support import hybrid_fusion_query

            kwargs = hybrid_fusion_query(
                query_vector, sparse_vector, vector_name=None,
                top_k=top_k, query_filter=None,
            )
            hits = client.query_points(
                collection_name=collection,
                with_payload=True,
                with_vectors=False,
                **kwargs,
            ).points
        except Exception as exc:  # noqa: BLE001 - degrade to dense-only
            print(f"[query] fusion failed ({exc}); dense-only retry")
            hits = client.search(
                collection_name=collection, query_vector=query_vector, limit=top_k,
            )
    else:
        hits = client.search(
            collection_name=collection,
            query_vector=query_vector,
            limit=top_k,
        )
    results = []
    for hit in hits:
        if hit.payload is None:
            continue
        results.append(
            {
                "score": getattr(hit, "score", None),
                "text": hit.payload.get("text"),
                "entity_ids": hit.payload.get("entity_ids") or [],
                "source_id": hit.payload.get("source_id"),
            }
        )
    return results


def fetch_related_graph(driver, entity_ids: List[str]) -> List[Dict[str, Any]]:
    if not entity_ids:
        return []
    query = """
    MATCH (e:Entity)-[r]-(related)
    WHERE e.id IN $entity_ids
    RETURN e, r, related
    """
    with driver.session() as session:
        result = session.run(query, entity_ids=entity_ids)
        return [dict(record) for record in result]


def format_graph_context(subgraph: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    nodes = set()
    edges = []
    for entry in subgraph:
        entity = entry.get("e")
        related = entry.get("related")
        relation = entry.get("r")
        if not entity or not related or not relation:
            continue
        nodes.add(entity.get("name"))
        nodes.add(related.get("name"))
        edges.append(f"{entity.get('name')} {relation.get('type', 'RELATED')} {related.get('name')}")
    return {"nodes": sorted(n for n in nodes if n), "edges": edges}


def _ollama_generate(prompt: str, model_id: str, model_url: str) -> str:
    import json
    from urllib import request

    endpoint = model_url.rstrip("/") + "/api/generate"
    payload = json.dumps(
        {
            "model": model_id,
            "prompt": prompt,
            "stream": False,
        }
    ).encode("utf-8")
    req = request.Request(endpoint, data=payload, headers={"Content-Type": "application/json"})
    with request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("response", "") or ""


def llm_generate(prompt: str, model_id: str, model_url: str | None) -> str:
    model_id = model_id.strip()
    lower_model = model_id.lower()
    if lower_model.startswith("gemini"):
        import google.generativeai as genai

        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("LANGEXTRACT_API_KEY")
        if not api_key:
            raise RuntimeError("Missing GEMINI_API_KEY or LANGEXTRACT_API_KEY for Gemini.")
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(model_id)
        response = model.generate_content(prompt)
        return getattr(response, "text", "") or ""

    if lower_model.startswith("gpt-") or lower_model.startswith("o"):
        from openai import OpenAI

        api_key = os.getenv("OPENAI_API_KEY") or os.getenv("LANGEXTRACT_API_KEY")
        client = OpenAI(api_key=api_key) if api_key else OpenAI()
        response = client.chat.completions.create(
            model=model_id,
            messages=[
                {
                    "role": "system",
                    "content": "Answer the question using the provided graph context and citations if possible.",
                },
                {"role": "user", "content": prompt},
            ],
        )
        return response.choices[0].message.content or ""

    if model_url:
        return _ollama_generate(prompt, model_id, model_url)

    raise RuntimeError(f"Unsupported model_id for generation: {model_id}")


def build_generation_prompt(query: str, graph_context: Dict[str, List[str]], passages: List[str]) -> str:
    nodes_str = ", ".join(graph_context.get("nodes") or [])
    edges_str = "; ".join(graph_context.get("edges") or [])
    passages_str = "\n\n".join(passages)
    return textwrap.dedent(
        f"""\
        You are an assistant with access to a knowledge graph and retrieved passages.

        Nodes: {nodes_str}
        Edges: {edges_str}

        Passages:
        {passages_str}

        Answer the user question using the graph context and passages.
        Question: {query}
        """
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", required=True, help="User query")
    parser.add_argument("--collection", default="graphrag_entities")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--embedding-model", default=None)
    parser.add_argument("--embedding-device", default=None)
    add_graph_store_args(parser)
    parser.add_argument("--neo4j-uri", default=os.getenv("NEO4J_URI", "bolt://localhost:7687"))
    parser.add_argument("--neo4j-user", default=os.getenv("NEO4J_USER", "neo4j"))
    parser.add_argument("--neo4j-pass", default=os.getenv("NEO4J_PASS", "password"))
    parser.add_argument("--qdrant-path", default=os.getenv("QDRANT_DOC_PATH"))
    parser.add_argument("--llm-model", default=os.getenv("LANGEXTRACT_MODEL_ID", "gemini-2.5-flash"))
    parser.add_argument("--langextract-model-id", default=os.getenv("LANGEXTRACT_MODEL_ID"))
    parser.add_argument("--langextract-model-url", default=os.getenv("LANGEXTRACT_MODEL_URL"))
    args = parser.parse_args()

    _load_env()
    _set_langextract_overrides(args.langextract_model_id, args.langextract_model_url)

    model_name, local_files_only = resolve_embedding_model(
        args.embedding_model, DEFAULT_DOC_EMBEDDING_MODEL
    )
    device = resolve_embedding_device(args.embedding_device)
    embedder = SentenceTransformer(model_name, local_files_only=local_files_only, device=device)
    query_vector = embedder.encode([args.query], **st_query_encode_kwargs(model_name))[0].tolist()

    qdrant = get_document_qdrant_store(args.qdrant_path)

    # Hybrid: sparse half + single-collection capability probe (all-or-none
    # trivially reduces to this one collection).
    sparse_vector = None
    try:
        from embedding_utils import hybrid_enabled

        if hybrid_enabled(model_name):
            from tools.common import embed_runtime

            sparse_vector = embed_runtime.encode_sparse(
                model_name, [args.query],
                device=str(embedder.device), model=embedder,
            )[0]
            info = qdrant.get_collection_info(args.collection)
            if not getattr(
                getattr(getattr(info, "config", None), "params", None),
                "sparse_vectors", None,
            ):
                sparse_vector = None
    except Exception as exc:  # noqa: BLE001 - dense-only on any sparse failure
        print(f"[query] hybrid sparse unavailable ({exc}); dense-only")
        sparse_vector = None

    hits = qdrant_search(qdrant, args.collection, query_vector, args.top_k,
                         sparse_vector=sparse_vector)
    if not hits:
        print("No Qdrant hits found.")
        return

    entity_ids = []
    passages = []
    for hit in hits:
        passages.append(hit.get("text") or "")
        entity_ids.extend(hit.get("entity_ids") or [])
    entity_ids = list(dict.fromkeys(entity_ids))

    driver = create_graph_store_from_args(args)
    try:
        subgraph = fetch_related_graph(driver, entity_ids)
        graph_context = format_graph_context(subgraph)
    finally:
        driver.close()

    prompt = build_generation_prompt(args.query, graph_context, passages)
    answer = llm_generate(prompt, args.llm_model, args.langextract_model_url)

    print("\nAnswer:\n")
    print(answer)


if __name__ == "__main__":
    main()
def _set_langextract_overrides(model_id: str | None, model_url: str | None) -> None:
    if model_id:
        os.environ["LANGEXTRACT_MODEL_ID"] = model_id
    if model_url:
        os.environ["LANGEXTRACT_MODEL_URL"] = model_url
