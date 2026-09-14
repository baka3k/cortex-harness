#!/usr/bin/env python3
"""Generate the golden embedding fixture for the Rust `cortex-embed` parity gate.

Reference = the PRODUCTION Python functions, not a re-implementation:
  * ingest lane  -> `embed_runtime.get_sentence_transformer(...).encode(
                    normalize_embeddings=True, batch_size=8)` (text built by
                    `primary_vector_sync.documents_from_payloads`, i.e. through the
                    real secret-redaction + char-bound path)
  * query lane   -> `embed_runtime.embed_query(text, model)` (AutoModel/`XLMRobertaLoRA`
                    `.encode`, adapter-free, which is what every MCP query tool uses)

Corpus is real stock: source files of this repository (the project configured in
`.cortext-harness/config/dev.json`) plus recorded MCP query strings.

Everything runs on CPU with pinned threads because the parity gate must be
re-runnable (findings C5: golden vectors are always generated on CPU).

Output: rust/crates/cortex-embed/tests/fixtures/jina_golden.json
    {"generator", "model", "graph", "reference", "gate", "cases":[
        {"name","lane","text","truncated","expected":{"ids","vector","dimension"}}]}

Usage:
    .venv/bin/python scripts/rust_parity/gen_embed_fixtures.py --limit 500
    .venv/bin/python scripts/rust_parity/gen_embed_fixtures.py --check   # only re-verify
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FIXTURE = (
    REPO / "rust" / "crates" / "cortex-embed" / "tests" / "fixtures" / "jina_golden.json"
)
GRAPH = REPO / ".cache" / "embed" / "jina-v3-onnx-fp32" / "model.onnx"
MODEL = "jinaai/jina-embeddings-v3"
PROJECT_ID = "cortex-harness"
MAX_CHARS = 4_000
BATCH_SIZE = 8
COSINE_GATE = 0.999

SKIP_DIRS = {
    ".venv",
    ".git",
    ".cache",
    ".qwen",
    ".mimosa",
    "node_modules",
    "target",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "dist",
    "build",
    "local_qdrant_db",
    "local_falkordb_db",
    ".serena",
    "code-tiny",  # vendored reference analyzers are the corpus we embed elsewhere
}
SOURCE_SUFFIXES = (
    ".py",
    ".rs",
    ".go",
    ".java",
    ".kt",
    ".ts",
    ".js",
    ".tsx",
    ".jsx",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".cs",
    ".php",
    ".rb",
    ".swift",
    ".pl",
    ".sh",
    ".sql",
    ".vb",
    ".dart",
)

# Queries recorded from the live MCP contract fixtures fall back to these if the
# fixture file is absent; both are real user-shaped code queries.
FALLBACK_QUERIES = [
    "find the function that upserts vectors into qdrant",
    "làm sao để thêm parser mới cho analyzer",
    "where is the HNSW index configured",
    "sync orchestrator embedding pass",
    "cosine similarity check between python and rust",
]


def _bootstrap_paths() -> None:
    # pytest config của repo cũng thêm hai đường này (pyproject [tool.pytest.ini_options]):
    # `tools.common.*` cần code-tiny, `embed_runtime` import trực tiếp cần tools/common.
    sys.path.insert(0, str(REPO / "code-tiny"))
    sys.path.insert(0, str(REPO / "code-tiny" / "tools" / "common"))
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")


def build_ingest_texts(limit: int) -> list[tuple[str, str]]:
    """Real files -> analyzer-shaped payloads -> the shared ingest text contract."""
    from tools.common.primary_vector_sync import documents_from_payloads

    collected: list[tuple[str, str]] = []
    for path in sorted(REPO.rglob("*")):
        if len(collected) >= limit:
            break
        if not path.is_file() or path.suffix not in SOURCE_SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        try:
            body = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if len(body.strip()) < 40:
            continue
        relative = path.relative_to(REPO).as_posix()
        head = body[: MAX_CHARS - 200]
        comment = next(
            (line.lstrip("#*/! \t") for line in body.splitlines()[:12] if line.strip()),
            "",
        )
        payloads = [
            {
                "symbol_id": f"{relative}::{path.stem}",
                "project_id": PROJECT_ID,
                "node_type": "file",
                "qualified_name": relative,
                "file_path": relative,
                "language": path.suffix.lstrip("."),
                "source": {"code": head, "comment": comment[:200], "summary": ""},
            }
        ]
        documents = documents_from_payloads(
            payloads, parser="parity", root_scope=PROJECT_ID, max_chars=MAX_CHARS
        )
        for document in documents:
            collected.append((relative, document.text))
    return collected


def build_query_texts() -> list[tuple[str, str]]:
    queries: list[tuple[str, str]] = []
    seen: set[str] = set()
    for candidate in (
        REPO / "scripts" / "rust_mcp" / "fixtures" / "contract_fixtures.json",
        REPO / "scripts" / "rust_mcp" / "fixtures" / "mind_fixtures.json",
    ):
        if not candidate.is_file():
            continue
        recorded = json.loads(candidate.read_text(encoding="utf-8"))
        for case in recorded.get("cases", []):
            if case.get("tool") not in {
                "semantic_search",
                "explore_graph",
                "query_graph_rag_langextract",
                "search_functions",
            }:
                continue
            arguments = case.get("arguments") or {}
            for key in ("query", "text", "node_a", "search_query", "input"):
                value = arguments.get(key)
                if isinstance(value, str) and 3 < len(value.strip()) < 400:
                    text = value.strip()
                    if text in seen:
                        continue
                    seen.add(text)
                    queries.append((f"{candidate.name}#{case.get('id')}", text))
    if not queries:
        queries = [(f"fallback#{index}", text) for index, text in enumerate(FALLBACK_QUERIES)]
    return queries


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=500, help="ingest texts (gate: >= 500)")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--out", default=str(FIXTURE))
    args = parser.parse_args()

    import numpy as np
    import torch

    _bootstrap_paths()
    import embed_runtime as er
    from transformers import AutoTokenizer

    torch.set_num_threads(args.threads)
    max_length = int(os.environ.get("PARITY_MAX_LENGTH", "8194"))

    corpus = build_ingest_texts(args.limit)
    queries = build_query_texts()
    if not corpus:
        raise SystemExit("[gen] empty ingest corpus — check repo path assumptions")
    print(f"[gen] ingest texts={len(corpus)} query texts={len(queries)} device=cpu")

    st = er.get_sentence_transformer(MODEL, device="cpu")
    ingest_vectors = st.encode(
        [text for _, text in corpus],
        batch_size=BATCH_SIZE,
        normalize_embeddings=True,
        convert_to_numpy=True,
    )
    query_vectors = [er.embed_query(text, MODEL, device_name="cpu") for _, text in queries]
    tokenizer = AutoTokenizer.from_pretrained(MODEL, trust_remote_code=True)

    cases: list[dict] = []
    truncated = 0

    def emit(name: str, lane: str, text: str, vector, strip: bool) -> None:
        nonlocal truncated
        prepared = text.strip() if strip else text
        ids = tokenizer(prepared)["input_ids"]
        if len(ids) >= max_length:
            truncated += 1
        cases.append(
            {
                "name": name,
                "lane": lane,
                "text": text,
                "truncated": len(ids) >= max_length,
                "expected": {
                    "ids": ids,
                    "dimension": len(vector),
                    "vector": [round(float(value), 8) for value in vector],
                },
            }
        )

    for (origin, text), vector in zip(corpus, ingest_vectors):
        emit(f"ingest/{origin}", "ingest", text, vector, strip=True)
    for (origin, text), vector in zip(queries, query_vectors):
        emit(f"query/{origin}", "query", text, vector, strip=False)

    norms = [float(np.linalg.norm(case["expected"]["vector"])) for case in cases]
    dimensions = {case["expected"]["dimension"] for case in cases}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generator": "scripts/rust_parity/gen_embed_fixtures.py",
        "model": MODEL,
        "graph": str(GRAPH.relative_to(REPO)) if GRAPH.exists() else None,
        "reference": {
            "device": "cpu",
            "threads": args.threads,
            "batch_size": BATCH_SIZE,
            "ingest_call": "SentenceTransformer.encode(normalize_embeddings=True)",
            "query_call": "embed_runtime.embed_query -> AutoModel.encode",
            "lora": "disabled (no task/prompt passed by any lane)",
            "max_length": max_length,
            "torch": torch.__version__,
            "platform": f"{platform.system()}/{platform.machine()}",
        },
        "gate": {"cosine": COSINE_GATE, "token_id_mismatches": 0, "dimension": 1024},
        "cases": cases,
    }
    out.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    shown = os.path.relpath(out, REPO)
    print(
        f"[gen] wrote {shown} ({out.stat().st_size / 1e6:.2f} MB, "
        f"{len(cases)} cases)"
    )
    print(
        f"[gen] dimensions={sorted(dimensions)} "
        f"norm[min,max]=[{min(norms):.6f},{max(norms):.6f}] truncated_cases={truncated}"
    )
    if dimensions != {1024}:
        raise SystemExit("[gen] FAIL: inconsistent vector dimension")
    if max(norms) - min(norms) > 1e-4:
        print("[gen] NOTE: norm spread > 1e-4 -> a lane is not unit-normalized")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
