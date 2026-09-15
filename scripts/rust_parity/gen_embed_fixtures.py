#!/usr/bin/env python3
"""Generate golden embedding fixtures for the Rust `cortex-embed` parity gates.

Two planes, because the repo runs two models with two different arithmetic
signatures (see `plans/260914-1706-onnx-embedding-spike/findings.md`):

    --plane code  jinaai/jina-embeddings-v3  mean-pool + L2-normalize, 8194 tokens
                  ingest lane  -> `SentenceTransformer.encode(normalize_embeddings=True)`
                  query lane   -> `embed_runtime.embed_query` (AutoModel `.encode`)
    --plane doc   BAAI/bge-m3                CLS-pool + L2-normalize, 8192 tokens
                  both lanes   -> `SentenceTransformer.encode(...)` (worker + ingest
                  share that call, so they share one spec)

Corpus is real stock: source files of this repository for the code plane, and the
`docs/` + `wiki/` markdown split by the production `split_paragraphs` for the doc
plane; query strings are recovered from the recorded MCP contract fixtures.

Everything runs on CPU with pinned threads: the parity gate must be re-runnable.

Outputs:
    rust/crates/cortex-embed/tests/fixtures/jina_golden.json   (--plane code)
    rust/crates/cortex-embed/tests/fixtures/bge_golden.json    (--plane doc)

Usage:
    .venv/bin/python scripts/rust_parity/gen_embed_fixtures.py --plane code --limit 500
    .venv/bin/python scripts/rust_parity/gen_embed_fixtures.py --plane doc  --limit 300
"""

# === Phase-08 archive notice (2026-09-15) ===
# PY side archived at phase-08 cutover, fixtures = golden.
# Python analyzer entry points were retired at the phase-08 cutover;
# fixtures under tests/fixtures/ are now the golden reference.
# Do not attempt to re-run the Python side — tools/<lang>/<lang>_analyzer.py
# no longer exists. See plans/260915-analyzer-layer-rust-cutover/reports/phase08-cutover.md.
# === end archive notice ===


from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FIXTURE_DIR = REPO / "rust" / "crates" / "cortex-embed" / "tests" / "fixtures"
COSINE_GATE = 0.999
PROJECT_ID = "cortex-harness"


@dataclass(frozen=True)
class PlaneCfg:
    plane: str
    model: str
    fixture: str
    graph: str
    batch_size: int
    max_chars: int
    max_length: int
    strip: bool
    # `primary_vector_sync` truyền tường minh normalize_embeddings=True;
    # `embed_worker.py`/graphrag_ingest thì không (module 2_Normalize đã làm việc đó).
    normalize_arg: bool
    ingest_corpus: str  # "code-files" | "doc-paragraphs"


PLANES = {
    "code": PlaneCfg(
        plane="code",
        model="jinaai/jina-embeddings-v3",
        fixture="jina_golden.json",
        graph=".cache/embed/jina-v3-onnx-fp32/model.onnx",
        batch_size=8,
        max_chars=4_000,
        max_length=8194,
        strip=True,
        normalize_arg=True,
        ingest_corpus="code-files",
    ),
    "doc": PlaneCfg(
        plane="doc",
        model="BAAI/bge-m3",
        fixture="bge_golden.json",
        graph=".cache/embed/BAAI--bge-m3/model.onnx",
        batch_size=32,  # worker/ingest gọi encode() không truyền batch_size
        max_chars=0,  # doc plane không char-bound; paragraph đã bị cắt khi split
        max_length=8192,
        strip=True,
        normalize_arg=False,
        ingest_corpus="doc-paragraphs",
    ),
}

QUERY_TOOLS = {
    "semantic_search",
    "explore_graph",
    "query_graph_rag_langextract",
    "search_functions",
}
QUERY_KEYS = ("query", "text", "node_a", "search_query", "input")
QUERY_SOURCES = (
    REPO / "scripts" / "rust_mcp" / "fixtures" / "contract_fixtures.json",
    REPO / "scripts" / "rust_mcp" / "fixtures" / "mind_fixtures.json",
)

FALLBACK_QUERIES = [
    "find the function that upserts vectors into qdrant",
    "làm sao để thêm parser mới cho analyzer",
    "where is the HNSW index configured",
    "sync orchestrator embedding pass",
    "cosine similarity check between python and rust",
]
FALLBACK_DOC_QUERIES = [
    "quy trình cutover FalkorDB sang LadybugDB",
    "how does the MCP tool-result contract versioning work",
    "thông số HNSW tuning cho collection 1024-dim",
    "what happens when the embedding backend is rolled back",
]

SKIP_DIRS = {
    ".venv", ".git", ".cache", ".qwen", ".mimosa", "node_modules", "target",
    "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", "dist", "build",
    "local_qdrant_db", "local_falkordb_db", ".serena",
    # Harness không được tự nhúng mình: thêm/sửa một script parity mà làm corpus
    # trôi thì fixture golden không tái lập được nữa (đã xảy ra với scripts/).
    "scripts", "plans", "tests", "installers",
}
SOURCE_SUFFIXES = (
    ".py", ".rs", ".go", ".java", ".kt", ".ts", ".js", ".tsx", ".jsx", ".c", ".h",
    ".cpp", ".hpp", ".cs", ".php", ".rb", ".swift", ".pl", ".sh", ".sql", ".vb", ".dart",
)


def _bootstrap_paths() -> None:
    # Giống [tool.pytest.ini_options] của repo: `tools.common.*` cần code-tiny,
    # `embed_runtime` import trực tiếp cần tools/common.
    sys.path.insert(0, str(REPO / "code-tiny"))
    sys.path.insert(0, str(REPO / "code-tiny" / "tools" / "common"))
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")


def _walk(suffixes: tuple[str, ...]) -> list[Path]:
    """Ứng viên corpus, theo thứ tự ổn định và phân bố đều.

    Sort theo sha256(đường dẫn) chứ không theo đường dẫn: sort theo tên làm 500
    slot đầu tiên rơi hết vào một thư mục đầu bảng chữ cái, còn sort ngẫu nhiên
    có khoá thì lấy mẫu đều toàn repo và không đổi khi file khác được thêm/xoá.
    """
    candidates = []
    for path in REPO.rglob("*"):
        if not path.is_file() or path.suffix not in suffixes:
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        candidates.append(path)
    candidates.sort(key=lambda path: (hashlib.sha256(
        path.relative_to(REPO).as_posix().encode("utf-8")).hexdigest(), str(path)))
    return candidates


def build_code_texts(limit: int) -> list[tuple[str, str]]:
    """Real source files -> analyzer-shaped payloads -> the shared ingest contract."""
    from tools.common.primary_vector_sync import documents_from_payloads

    collected: list[tuple[str, str]] = []
    for path in _walk(SOURCE_SUFFIXES):
        if len(collected) >= limit:
            break
        try:
            body = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if len(body.strip()) < 40:
            continue
        relative = path.relative_to(REPO).as_posix()
        comment = next(
            (line.lstrip("#*/! \t") for line in body.splitlines()[:12] if line.strip()), ""
        )
        documents = documents_from_payloads(
            [
                {
                    "symbol_id": f"{relative}::{path.stem}",
                    "project_id": PROJECT_ID,
                    "node_type": "file",
                    "qualified_name": relative,
                    "file_path": relative,
                    "language": path.suffix.lstrip("."),
                    "source": {
                        "code": body[:3_800],
                        "comment": comment[:200],
                        "summary": "",
                    },
                }
            ],
            parser="parity",
            root_scope=PROJECT_ID,
            max_chars=4_000,
        )
        collected.extend((relative, document.text) for document in documents)
    return collected


def _split_paragraphs(raw: str, max_chars: int) -> list[str]:
    """Production splitter from doc-tiny; falls back to an equivalent copy offline."""
    try:
        sys.path.insert(0, str(REPO / "doc-tiny"))
        from graphrag_ingest_langextract import split_paragraphs  # type: ignore

        return list(split_paragraphs(raw, max_chars))
    except Exception as exc:  # pragma: no cover - heavy optional imports
        print(f"[gen] NOTE: using local split_paragraphs copy ({type(exc).__name__}: {exc})")
        import re

        paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", raw) if p.strip()]
        out: list[str] = []
        for paragraph in paragraphs:
            if len(paragraph) <= max_chars:
                out.append(paragraph)
            else:
                out.extend(
                    paragraph[i : i + max_chars].strip()
                    for i in range(0, len(paragraph), max_chars)
                )
        return out


def build_doc_texts(limit: int, max_paragraph_chars: int, min_paragraph_chars: int) -> list[tuple[str, str]]:
    collected: list[tuple[str, str]] = []
    for path in _walk((".md", ".txt")):
        if len(collected) >= limit:
            break
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        relative = path.relative_to(REPO).as_posix()
        for index, paragraph in enumerate(_split_paragraphs(raw, max_paragraph_chars)):
            if len(paragraph) < min_paragraph_chars:
                continue
            collected.append((f"{relative}#{index}", paragraph))
            if len(collected) >= limit:
                break
    return collected


def build_query_texts(cfg: PlaneCfg, doc_plane: bool) -> list[tuple[str, str]]:
    queries: list[tuple[str, str]] = []
    seen: set[str] = set()
    for candidate in QUERY_SOURCES:
        if not candidate.is_file():
            continue
        recorded = json.loads(candidate.read_text(encoding="utf-8"))
        for case in recorded.get("cases", []):
            if case.get("tool") not in QUERY_TOOLS:
                continue
            arguments = case.get("arguments") or {}
            for key in QUERY_KEYS:
                value = arguments.get(key)
                if isinstance(value, str) and 3 < len(value.strip()) < 400:
                    text = value.strip()
                    if text in seen:
                        continue
                    seen.add(text)
                    queries.append((f"{candidate.name}#{case.get('id')}", text))
    if not queries:
        pool = FALLBACK_DOC_QUERIES if doc_plane else FALLBACK_QUERIES
        queries = [(f"fallback#{i}", text) for i, text in enumerate(pool)]
    return queries


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--plane", choices=sorted(PLANES), default="code")
    parser.add_argument("--limit", type=int, default=500, help="ingest texts (gate: code>=500, doc>=300)")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--max-paragraph-chars", type=int, default=500,
                        help="doc plane only: dev.py truyền MAX_PARAGRAPH_CHARS=500")
    parser.add_argument("--min-paragraph-chars", type=int, default=150)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    cfg = PLANES[args.plane]
    import numpy as np
    import torch

    _bootstrap_paths()
    import embed_runtime as er
    from transformers import AutoTokenizer

    torch.set_num_threads(args.threads)

    if cfg.ingest_corpus == "code-files":
        corpus = build_code_texts(args.limit)
    else:
        corpus = build_doc_texts(args.limit, args.max_paragraph_chars, args.min_paragraph_chars)
    queries = build_query_texts(cfg, doc_plane=cfg.plane == "doc")
    if not corpus:
        raise SystemExit(f"[gen] empty {cfg.plane} ingest corpus — check repo path assumptions")
    print(f"[gen] plane={cfg.plane} model={cfg.model} ingest={len(corpus)} queries={len(queries)} device=cpu")

    st = er.get_sentence_transformer(cfg.model, device="cpu")
    ingest_vectors = st.encode(
        [text for _, text in corpus],
        batch_size=cfg.batch_size,
        convert_to_numpy=True,
        normalize_embeddings=cfg.normalize_arg,
    )
    if cfg.plane == "code":
        query_vectors = [er.embed_query(text, cfg.model, device_name="cpu") for _, text in queries]
        tokenizer = AutoTokenizer.from_pretrained(cfg.model, trust_remote_code=True)
    else:
        query_vectors = st.encode(
            [text for _, text in queries],
            batch_size=cfg.batch_size,
            convert_to_numpy=True,
        )
        tokenizer = AutoTokenizer.from_pretrained(cfg.model)

    cases: list[dict] = []
    truncated = 0

    def emit(name: str, lane: str, text: str, vector) -> None:
        nonlocal truncated
        # SentenceTransformer.tokenize strip(); lane embed_query (code/query) thì không.
        prepared = text.strip() if (cfg.strip and not (cfg.plane == "code" and lane == "query")) else text
        ids = tokenizer(prepared)["input_ids"]
        if len(ids) >= cfg.max_length:
            truncated += 1
        cases.append(
            {
                "name": name,
                "lane": lane,
                "text": text,
                "truncated": len(ids) >= cfg.max_length,
                "expected": {
                    "ids": ids,
                    "dimension": len(vector),
                    "vector": [round(float(value), 8) for value in vector],
                },
            }
        )

    for (origin, text), vector in zip(corpus, ingest_vectors):
        emit(f"ingest/{origin}", "ingest", text, vector)
    for (origin, text), vector in zip(queries, query_vectors):
        emit(f"query/{origin}", "query", text, vector)

    norms = [float(np.linalg.norm(case["expected"]["vector"])) for case in cases]
    dimensions = {case["expected"]["dimension"] for case in cases}
    out = Path(args.out) if args.out else FIXTURE_DIR / cfg.fixture
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generator": "scripts/rust_parity/gen_embed_fixtures.py",
        "plane": cfg.plane,
        "model": cfg.model,
        "graph": cfg.graph if (REPO / cfg.graph).exists() else None,
        "strip": cfg.strip,
        "max_chars": cfg.max_chars,
        "reference": {
            "device": "cpu",
            "threads": args.threads,
            "batch_size": cfg.batch_size,
            "ingest_call": (
                "SentenceTransformer.encode(normalize_embeddings=True)"
                if cfg.plane == "code"
                else "SentenceTransformer.encode() (2_Normalize module)"
            ),
            "query_call": (
                "embed_runtime.embed_query -> AutoModel.encode"
                if cfg.plane == "code"
                else "embed_worker.py SentenceTransformer.encode()"
            ),
            "lora": "disabled (no task/prompt passed by any lane)",
            "max_length": cfg.max_length,
            "torch": torch.__version__,
            "platform": f"{platform.system()}/{platform.machine()}",
        },
        "gate": {"cosine": COSINE_GATE, "token_id_mismatches": 0, "dimension": 1024},
        "cases": cases,
    }
    out.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    print(
        f"[gen] wrote {os.path.relpath(out, REPO)} "
        f"({out.stat().st_size / 1e6:.2f} MB, {len(cases)} cases)"
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
