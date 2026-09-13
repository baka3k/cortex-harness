#!/usr/bin/env python3
"""Phase 14 — doc-tiny ingest + query parity gate (Scope A).

Chạy doc-tiny ingest Python (implementation tham chiếu) và ingest Rust
(`cortex-doc`, crates/cortex-doc) trên CÙNG corpus markdown nhỏ, với LLM/NER
DISABLED giống nhau trên 2 bên (mock provider = fixture replay; Qdrant/vector
stub), so:

  G1  chunking           — split_paragraphs python vs rust (byte-level JSON)
  G2  graph assembly     — build_graph_components_from_entities python vs rust
                           (canonical JSON: nodes theo insertion order + relations)
  G3  file graph writes  — FalkorDB graphs `p14_doc_py` / `p14_doc_rs`,
                           dump + diff = 0 ngoài mask (dual_write_diff helpers)
  G4  query determinist  — fetch_related_graph + format_graph_context +
                           build_generation_prompt, byte-level python vs rust

LLM-dependent stages (langextract/gemini providers, câu trả lời LLM) bị loại
khỏi parity — fixture provider mock CẢ HAI bên bằng cùng entity list đã ghi.

Usage (từ repo root):
    .venv/bin/python scripts/rust_parity/doctiny_parity.py [--with-embed]

Exit 0 = mọi gate PASS; 1 = FAIL.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "doc-tiny"))
sys.path.insert(0, str(REPO / "code-tiny"))
sys.path.insert(0, str(REPO))

RUST_BIN = REPO / "rust" / "target" / "debug" / "cortex-doc"
FIXTURE_DIR = REPO / "scripts" / "rust_parity" / "fixtures"
CORPUS_DIR = FIXTURE_DIR / "doctiny_corpus"
FIXTURE_JSON = FIXTURE_DIR / "doctiny_fixture.json"
REPORT_PATH = (
    REPO / "plans" / "260913-2130-rust-full-migration" / "reports" / "phase14-doc-tiny-parity.md"
)

HOST = "127.0.0.1"
PORT = 6379
GRAPH_PY = "p14_doc_py"
GRAPH_RS = "p14_doc_rs"
PROJECT_ID = "p14doc"
SOURCE_PREFIX = "p14doc"
MAX_PARA_CHARS = 1200
MIN_PARA_CHARS = 40

# ---------------------------------------------------------------- corpus ----

STOCK_DOCS = Path.home() / "baka3k" / "stock" / "docs"
STOCK_SUBSET = [
    "deerflow_same_ec2_deploy.md",
    "mcp_pipeline_tools.md",
    "n8n_stock_api.md",
    "system-architecture.md",
]
STOCK_TRUNCATE_CHARS = 3500

CRAFT_MD = """# Crafted parity fixture

Đây là đoạn đầu tiên có dấu tiếng Việt để kiểm tra UTF-8 byte-level.

Đoạn thứ hai có ký tự tab	và nhiều dòng liên tiếp.
FalkorDB là graph database chạy trên Redis, dùng cho parity gate này.

Short.

A

Paragraph ba mươi hơn bốn mươi ký tự: Acme Systems hợp tác với CCC tại Berlin
theo chuẩn ISO 15118 về Plug&Charge và Digital Key 3.0.

Kết thúc file crafted.
"""

CRAFT_TXT = """Plain text fixture cho doc-tiny parity.

The SSI FastConnect API exposes the iOrder and ekos systems through n8n webhooks.
Draft note trước khi publish lên wiki nội bộ.
"""


def prepare_corpus() -> list[Path]:
    """Build the deterministic corpus (stock subset + crafted files)."""
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name in STOCK_SUBSET:
        src = STOCK_DOCS / name
        if not src.exists():
            continue
        text = src.read_text(encoding="utf-8")[:STOCK_TRUNCATE_CHARS]
        dst = CORPUS_DIR / name
        dst.write_text(text, encoding="utf-8")
        written.append(dst)
    (CORPUS_DIR / "zz_crafted.md").write_text(CRAFT_MD, encoding="utf-8")
    written.append(CORPUS_DIR / "zz_crafted.md")
    (CORPUS_DIR / "zz_notes.txt").write_text(CRAFT_TXT, encoding="utf-8")
    written.append(CORPUS_DIR / "zz_notes.txt")
    # Nested dir (exercise recursion + source_id separator).
    guides = CORPUS_DIR / "guides"
    guides.mkdir(exist_ok=True)
    (guides / "ops-runbook.md").write_text(
        "Ops runbook cho cụm FalkorDB.\n\n"
        "Restart FalkorDB service bằng systemd, sau đó kiểm tra GRAPH.RO_QUERY "
        "trên port 6379 và xác nhận schema của graph p14_doc_py chưa drift. "
        "Log ghi tại /var/log/falkordb.\n",
        encoding="utf-8",
    )
    written.append(guides / "ops-runbook.md")
    # Excluded directory — phải bị cả 2 phía bỏ qua.
    excluded = CORPUS_DIR / ".git"
    excluded.mkdir(exist_ok=True)
    (excluded / "hidden.md").write_text(
        "SHOULD NOT APPEAR in any graph.", encoding="utf-8"
    )
    return sorted(written)


# ------------------------------------------------- fixture (mock NER/LLM) ----

ORG_RE = re.compile(
    r"\b([A-Z][A-Za-z0-9]+(?: (?:Systems|Corp|Inc|Bank|Group|Union|Consortium|"
    r"Committee|Agency|Solutions|Labs|Connect|API|Studio))+)\b"
)
STANDARD_RE = re.compile(r"\b((?:ISO(?:\s?/\s?IEC)?|IEC|IEEE|RFC)\s?[0-9]{2,6}(?:[-.][0-9]+)*)\b")
ACRONYM_RE = re.compile(r"\b([A-Z]{2,6})\b")
TECH_RE = re.compile(
    r"\b(FalkorDB|Redis|Qdrant|Docker|Kubernetes|Python|JavaScript|FastAPI|"
    r"n8n|webhook|webhooks|JWT|OAuth2|Digital Key 3\.0|Plug&Charge|grafana|Grafana|"
    r"nginx|Postgres|RedisJSON|deerflow| DeerFlow|DeerFlow)\b"
)


def mine_entities(paragraph: str) -> list[dict]:
    """Deterministic regex NER — the shared mock for BOTH sides.

    Deterministic: fixed pattern order, first-occurrence order, confidence
    0.87, spans via `str.find` (mirrors `entity_extractors._find_span`).
    """
    found: list[dict] = []

    def add(name: str, etype: str) -> None:
        name = name.strip()
        if not name:
            return
        if any(e["name"] == name for e in found):
            return
        start = paragraph.find(name)
        if start < 0:
            found.append({"name": name, "type": etype})
            return
        found.append(
            {
                "name": name,
                "type": etype,
                "confidence": 0.87,
                "start_char": start,
                "end_char": start + len(name),
            }
        )

    for match in ORG_RE.finditer(paragraph):
        add(match.group(1), "ORG")
    for match in STANDARD_RE.finditer(paragraph):
        add(match.group(1), "STANDARD")
    for match in TECH_RE.finditer(paragraph):
        add(match.group(1), "TECH")
    for match in ACRONYM_RE.finditer(paragraph):
        add(match.group(1), "TECH")
    return found


def build_fixture(paragraphs_by_file: dict[str, list[str]]) -> dict:
    entities_by_paragraph: dict[str, list[dict]] = {}
    for paragraphs in paragraphs_by_file.values():
        for paragraph in paragraphs:
            if paragraph not in entities_by_paragraph:
                entities_by_paragraph[paragraph] = mine_entities(paragraph)
    return {"entities_by_paragraph": entities_by_paragraph}


# ------------------------------------------------------------- canonical ----


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )


def run_rust(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    proc_env = dict(os.environ)
    proc_env.pop("FALKORDB_GRAPH", None)
    proc_env.pop("FALKORDB_DATABASE", None)
    proc_env.pop("CORTEX_EXTRA_IGNORE_DIRS", None)
    if env:
        proc_env.update(env)
    proc = subprocess.run(
        [str(RUST_BIN), *args], capture_output=True, text=True, env=proc_env, timeout=600
    )
    # println! adds exactly one trailing newline — content-only comparisons.
    proc.stdout = proc.stdout.rstrip("\n")
    return proc


# --------------------------------------------------------------- gates ----


def gate_chunking(corpus_files: list[Path], paragraphs_by_source: dict[str, list[str]]) -> tuple[bool, str]:
    detail = []
    ok = True
    for path in corpus_files:
        rel = path.relative_to(CORPUS_DIR).as_posix()
        py_chunks = paragraphs_by_source[rel]
        proc = run_rust(
            "chunk", "--file", str(path), "--max-paragraph-chars", str(MAX_PARA_CHARS)
        )
        if proc.returncode != 0:
            ok = False
            detail.append(f"{rel}: rust chunk FAILED: {proc.stderr[-300:]}")
            continue
        if proc.stdout.encode("utf-8") != canonical(py_chunks):
            ok = False
            py_canon = canonical(py_chunks).decode()
            detail.append(f"{rel}: MISMATCH\n  py={py_canon[:400]}\n  rs={proc.stdout[:400]}")
        else:
            detail.append(f"{rel}: {len(py_chunks)} paragraphs byte-identical")
    return ok, "\n".join(detail)


def gate_assembly(fixture: dict) -> tuple[bool, str]:
    from graphrag_ingest_langextract import build_graph_components_from_entities

    ok = True
    detail = []
    items = list(fixture["entities_by_paragraph"].items())
    # Synthetic relations coverage (UNKNOWN fallback nodes, default type, dedupe).
    tricky_entities = [
        {"name": "Acme Systems", "type": "ORG", "confidence": 0.9, "start_char": 0, "end_char": 12},
        {"name": "ACME SYSTEMS CORP", "type": "ORG", "confidence": 0.95, "start_char": 20, "end_char": 37},
        {"name": "Plug&Charge", "type": "TECH"},
        {"name": "Berlin", "type": "GPE", "confidence": 0.7, "start_char": 60, "end_char": 66},
        {"name": "", "type": "ORG"},
    ]
    tricky_relations = [
        {"source": "Acme Systems", "target": "Berlin", "relation": "LOCATED_IN"},
        {"source": "acme systems", "target": "Unknown Target", "relation": ""},
        {"source": "Plug&Charge", "target": "ISO 15118"},
        {"source": "", "target": "Berlin"},
    ]
    items.append(("<tricky>", (tricky_entities, tricky_relations)))

    checked = 0
    for key, value in items:
        if key == "<tricky>":
            entities, relations = value
        else:
            entities, relations = value, []
        py_nodes, py_relations = build_graph_components_from_entities(
            entities,
            relations,
            merge_entities=True,
            normalize_mode="aggressive",
            project_id_normalized=PROJECT_ID,
        )
        py_out = {
            "nodes": list(py_nodes.values()),
            "node_keys_in_order": list(py_nodes.keys()),
            "relations": py_relations,
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump({"entities": entities, "relations": relations}, handle, ensure_ascii=False)
            entity_file = handle.name
        try:
            proc = run_rust(
                "assemble",
                "--entities",
                entity_file,
                "--normalize-mode",
                "aggressive",
                "--project-id-normalized",
                PROJECT_ID,
            )
        finally:
            os.unlink(entity_file)
        if proc.returncode != 0:
            ok = False
            detail.append(f"{key[:40]}: rust assemble FAILED: {proc.stderr[-300:]}")
            continue
        if proc.stdout.encode("utf-8") != canonical(py_out):
            ok = False
            detail.append(f"{key[:40]}: MISMATCH\n  py={canonical(py_out)[:400]}\n  rs={proc.stdout[:400]}")
        else:
            checked += 1
    detail.insert(0, f"{checked}/{len(items)} entity sets canonical-identical")
    return ok, "\n".join(detail)


# ------------------------------------------------------- python ingest ----


def run_python_ingest(fixture: dict) -> tuple[bool, str]:
    """Drive doc-tiny ingest with the fixture mock + stubbed vector stage."""
    import graphrag_ingest_langextract as G
    from graph_store import FalkorDBGraphStore
    from tools.graph.driver.falkordb_driver import FalkorDBDriver
    from types import SimpleNamespace

    def fixture_provider(
        text,
        provider,
        nlp=None,
        gliner_model=None,
        gliner_labels=None,
        gliner_threshold=0.3,
        merge_entities=True,
        normalize_mode="aggressive",
        project_id_normalized=None,
    ):
        entities = fixture["entities_by_paragraph"].get(text, [])
        return G.build_graph_components_from_entities(
            entities,
            [],
            merge_entities=merge_entities,
            normalize_mode=normalize_mode,
            project_id_normalized=project_id_normalized,
        )

    G.build_graph_components = fixture_provider
    qdrant_calls = []

    def stub_qdrant(*args, **kwargs):
        qdrant_calls.append(args[2] if len(args) > 2 else kwargs)

    G.ingest_to_qdrant = stub_qdrant

    driver = FalkorDBDriver(host=HOST, port=PORT, _suppress_deprecation=True)
    store = FalkorDBGraphStore(driver, GRAPH_PY)

    args = SimpleNamespace(
        entity_provider="fixture",
        no_batch=True,
        gliner_batch_size=1,
        graph_batch_size=1,
        no_entity_merge=False,
        entity_normalize_mode="aggressive",
        max_paragraph_chars=MAX_PARA_CHARS,
        min_paragraph_chars=MIN_PARA_CHARS,
        skip_llm_short=True,
        project_id=PROJECT_ID,
        source_id=None,
        collection=f"{PROJECT_ID}_doc",
        gliner_labels="PERSON,ORG,PRODUCT,GPE,DATE,TECH,CRYPTO,STANDARD",
        gliner_threshold=0.3,
        spacy_model="en_core_web_sm",
        ruler_json=None,
    )

    files = G._iter_input_files(CORPUS_DIR)
    for file_path in files:
        rel = G._safe_source_id(CORPUS_DIR, file_path)
        source_id = f"{SOURCE_PREFIX}__{rel}"
        raw_text = G._read_input_text(file_path)
        G.process_text(
            raw_text,
            source_id,
            args,
            store,
            None,
            None,
            project_id_normalized=PROJECT_ID,
        )
    return True, f"python ingest done: {len(files)} files, {len(qdrant_calls)} vector-upserts stubbed"


# -------------------------------------------------------- graph dump/diff ----

from dual_write_diff import MASKED_PROPS as _BASE_MASKED_PROPS  # noqa: E402
from dual_write_diff import clean_graph, diff_dump  # noqa: E402
from tools.graph.driver.falkordb_driver import FalkorDBDriver  # noqa: E402

# FalkorDB engine-internal edge endpoint ids are creation-order dependent and
# masked like dual_write_diff's `_graph_id`/`_src`/`_dst` (this driver version
# exposes them as `_start_id`/`_end_id`). Mask extension lives here because
# dual_write_diff.py is a shared phase-03 artifact.
MASKED_PROPS = _BASE_MASKED_PROPS | {"_start_id", "_end_id"}

_NODE_IDENTITY_KEYS = ("id", "site_id", "fingerprint", "project_id", "name")


def doc_node_identity(props: dict, label: str) -> tuple[str, str]:
    if label == "Paragraph":
        return label, f"{props.get('source_id')}|{props.get('paragraph_id')}"
    for key in _NODE_IDENTITY_KEYS:
        value = props.get(key)
        if isinstance(value, str) and value:
            return label, value
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return label, str(value)
    return label, ""


def dump_doc_graph(driver: FalkorDBDriver, graph: str) -> dict:
    node_records, _, _ = driver.execute_query_sync("MATCH (n) RETURN n", {}, graph)
    edge_records, _, _ = driver.execute_query_sync("MATCH (a)-[r]->(b) RETURN a, r, b", {}, graph)

    nodes: dict[tuple[str, str], dict] = {}
    for record in node_records:
        node = record.get("n") or {}
        label = str(node.get("_label", ""))
        identity = doc_node_identity(node, label)
        props = {
            key: value
            for key, value in node.items()
            if key not in MASKED_PROPS and key not in _NODE_IDENTITY_KEYS
        }
        nodes[identity] = props

    edges: dict[tuple, dict] = {}
    for record in edge_records:
        source = record.get("a") or {}
        rel = record.get("r") or {}
        target = record.get("b") or {}
        s_label = str(source.get("_label", ""))
        t_label = str(target.get("_label", ""))
        key = (
            *doc_node_identity(source, s_label),
            str(rel.get("_type", "")),
            str(rel.get("source_id", "")),
            str(rel.get("paragraph_id", "")),
            *doc_node_identity(target, t_label),
        )
        props = {
            key_: value
            for key_, value in rel.items()
            if key_ not in MASKED_PROPS and key_ != "_type"
        }
        if key in edges and edges[key] != props:
            raise RuntimeError(f"edge key collision with different props: {key}")
        edges[key] = props

    return {
        "nodes": {f"{label}|{identity}": props for (label, identity), props in nodes.items()},
        "edges": {
            f"{k[0]}|{k[1]} -[{k[2]}|{k[3]}|{k[4]}]-> {k[5]}|{k[6]}": props
            for k, props in edges.items()
        },
    }


# ------------------------------------------------------------ query gate ----

QUERY_TEXT = "How does FastConnect streaming work with n8n and FalkorDB?"


def query_output_python(driver: FalkorDBDriver, graph: str, entity_ids, passages) -> bytes:
    import graphrag_query_langextract as Q
    from graph_store import FalkorDBGraphStore

    store = FalkorDBGraphStore(driver, graph)
    subgraph = Q.fetch_related_graph(store, entity_ids)
    context = Q.format_graph_context(subgraph)
    prompt = Q.build_generation_prompt(QUERY_TEXT, context, passages)
    return canonical({"nodes": context["nodes"], "edges": context["edges"], "prompt": prompt})


def gate_query(driver: FalkorDBDriver) -> tuple[bool, str, dict]:
    ok = True
    detail = []
    # Deterministic sample inputs derived from the (verified identical) graph.
    ids_result, _, _ = driver.execute_query_sync(
        "MATCH (e:Entity) RETURN e.id AS id ORDER BY id LIMIT 10", {}, GRAPH_PY
    )
    entity_ids = [row["id"] for row in ids_result]
    passage_rows, _, _ = driver.execute_query_sync(
        "MATCH (p:Paragraph) RETURN p.text AS text ORDER BY p.source_id, p.paragraph_id LIMIT 5",
        {},
        GRAPH_PY,
    )
    passages = [row["text"] for row in passage_rows]

    ids_file = FIXTURE_DIR / "doctiny_query_ids.json"
    passages_file = FIXTURE_DIR / "doctiny_query_passages.json"
    ids_file.write_text(json.dumps(entity_ids), encoding="utf-8")
    passages_file.write_text(json.dumps(passages, ensure_ascii=False), encoding="utf-8")

    py_on_py = query_output_python(driver, GRAPH_PY, entity_ids, passages)
    py_on_rs = query_output_python(driver, GRAPH_RS, entity_ids, passages)
    stats = {"entity_ids": len(entity_ids), "passages": len(passages)}

    if py_on_py != py_on_rs:
        ok = False
        detail.append("python(py-graph) != python(rs-graph) — graphs diverge via query path")
    else:
        detail.append(f"python query on both graphs byte-identical ({len(entity_ids)} ids)")

    proc = run_rust(
        "query",
        "--falkordb-uri",
        f"redis://{HOST}:{PORT}",
        "--falkordb-graph",
        GRAPH_RS,
        "--entity-ids",
        f"@{ids_file}",
        "--passages",
        str(passages_file),
        "--query-text",
        QUERY_TEXT,
    )
    if proc.returncode != 0:
        ok = False
        detail.append(f"rust query FAILED: {proc.stderr[-400:]}")
        return ok, "\n".join(detail), stats
    rs_on_rs = proc.stdout.encode("utf-8")
    if rs_on_rs != py_on_rs:
        ok = False
        detail.append("rust(rs-graph) != python(rs-graph) — byte diff below")
        detail.append(f"  py={py_on_rs.decode()[:600]}")
        detail.append(f"  rs={rs_on_rs.decode()[:600]}")
    else:
        detail.append("rust query on rs-graph byte-identical to python")
    return ok, "\n".join(detail), stats


# ----------------------------------------------------------------- main ----


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--with-embed", action="store_true", help="run the optional bge-m3 sidecar smoke gate")
    args = parser.parse_args()

    if not RUST_BIN.exists():
        print(f"cortex-doc binary missing: {RUST_BIN} — run cargo build -p cortex-doc")
        return 1

    from dotenv import load_dotenv  # noqa: F401  (doc-tiny env hygiene)

    corpus_files = prepare_corpus()
    from graphrag_ingest_langextract import split_paragraphs

    paragraphs_by_source: dict[str, list[str]] = {}
    for path in corpus_files:
        rel = path.relative_to(CORPUS_DIR).as_posix()
        raw = path.read_text(encoding="utf-8").strip()
        paragraphs_by_source[rel] = split_paragraphs(raw, MAX_PARA_CHARS)

    fixture = build_fixture(paragraphs_by_source)
    FIXTURE_JSON.write_text(json.dumps(fixture, ensure_ascii=False, indent=1), encoding="utf-8")
    total_entities = sum(len(v) for v in fixture["entities_by_paragraph"].values())
    print(f"[setup] corpus={len(corpus_files)} files, {len(paragraphs_by_source)} sources, "
          f"{total_entities} fixture entities → {FIXTURE_JSON}")

    # Registry seed for the Rust CLI (python side drives process_text directly).
    config_dir = tempfile.mkdtemp(prefix="p14registry-")
    (Path(config_dir) / "p14doc.json").write_text(
        json.dumps({"project": {"code": PROJECT_ID}}), encoding="utf-8"
    )
    rust_env = {"CORTEX_HARNESS_CONFIG_PATH": config_dir}

    driver = FalkorDBDriver(host=HOST, port=PORT, _suppress_deprecation=True)
    clean_graph(driver, GRAPH_PY)
    clean_graph(driver, GRAPH_RS)

    results: dict[str, tuple[bool, str]] = {}

    print("[G1] chunking parity")
    results["G1 chunking"] = gate_chunking(corpus_files, paragraphs_by_source)

    print("[G2] graph assembly parity")
    results["G2 assembly"] = gate_assembly(fixture)

    print("[G3] ingest graph writes (python → p14_doc_py, rust → p14_doc_rs)")
    ok, detail = run_python_ingest(fixture)
    results["G3a python ingest"] = (ok, detail)
    if ok:
        proc = run_rust(
            "ingest",
            "--folder",
            str(CORPUS_DIR),
            "--source-id",
            SOURCE_PREFIX,
            "--project-id",
            PROJECT_ID,
            "--falkordb-uri",
            f"redis://{HOST}:{PORT}",
            "--falkordb-graph",
            GRAPH_RS,
            "--max-paragraph-chars",
            str(MAX_PARA_CHARS),
            "--min-paragraph-chars",
            str(MIN_PARA_CHARS),
            "--skip-llm-short",
            "--entity-provider",
            "fixture",
            "--entity-fixture",
            str(FIXTURE_JSON),
            env=rust_env,
        )
        if proc.returncode != 0:
            results["G3b rust ingest"] = (False, f"exit {proc.returncode}: {proc.stderr[-600:]}")
        else:
            results["G3b rust ingest"] = (True, proc.stdout.strip()[-300:])
        py_dump = dump_doc_graph(driver, GRAPH_PY)
        rs_dump = dump_doc_graph(driver, GRAPH_RS)
        diff = diff_dump(py_dump, rs_dump)
        diff_total = sum(len(v) for v in diff.values())
        summary = (
            f"nodes py={len(py_dump['nodes'])} rs={len(rs_dump['nodes'])}; "
            f"edges py={len(py_dump['edges'])} rs={len(rs_dump['edges'])}; diff_total={diff_total}"
        )
        if diff_total:
            snippet = json.dumps(diff, ensure_ascii=False, sort_keys=True)[:2000]
            results["G3 graph diff"] = (False, f"{summary}\n{snippet}")
        else:
            results["G3 graph diff"] = (True, summary)

    print("[G4] deterministic query parity")
    ok, detail, qstats = gate_query(driver)
    results["G4 query"] = (ok, detail + (f"; {qstats}" if qstats else ""))

    if args.with_embed:
        print("[G5] embedding sidecar smoke (bge-m3, optional)")
        proc = run_rust(
            "embed",
            "--text",
            "Digital Key 3.0 dùng chuẩn ISO 15118",
            "--text",
            "FalkorDB là graph database trên Redis",
            env={"CORTEX_DOC_PYTHON": str(REPO / ".venv" / "bin" / "python")},
        )
        if proc.returncode != 0:
            results["G5 embed smoke (optional)"] = (False, proc.stderr[-400:])
        else:
            payload = json.loads(proc.stdout)
            dim_ok = payload["dimension"] == 1024 and all(
                len(v) == 1024 for v in payload["vectors"]
            )
            results["G5 embed smoke (optional)"] = (
                dim_ok,
                f"dimension={payload['dimension']} via {payload['python']}",
            )

    driver.close()

    # ------------------------------------------------------------- report ----
    lines = [
        "# Phase 14 — doc-tiny parity (Scope A)",
        "",
        f"- date: 2026-09-14, harness: `scripts/rust_parity/doctiny_parity.py`",
        f"- corpus: {len(corpus_files)} files ({', '.join(p.name for p in corpus_files)}), "
        f"{sum(len(v) for v in fixture['entities_by_paragraph'].values())} fixture entities",
        f"- graphs: `{GRAPH_PY}` (python doc-tiny ingest) vs `{GRAPH_RS}` (rust cortex-doc ingest) "
        f"@ {HOST}:{PORT}",
        "- LLM/NER: disabled identically on both sides (fixture replay provider; regex miner "
        "`mine_entities`); vector stage: python side stubbed, rust side excluded (local-mode "
        "Qdrant has no wire protocol — see exclusions).",
        "",
        "## Gates",
        "",
        "| gate | result | detail |",
        "|---|---|---|",
    ]
    all_ok = True
    for name, (ok, detail) in results.items():
        if "(optional)" not in name:
            all_ok = all_ok and ok
        status = "PASS" if ok else "FAIL"
        rendered = detail.replace("\n", " <br> ").replace("|", "\\|")[:1200]
        lines.append(f"| {name} | **{status}** | {rendered} |")
    lines.append("")
    lines.append(f"**OVERALL: {'PASS' if all_ok else 'FAIL'}** (optional gates excluded from verdict)")
    lines.append("")
    lines.append("### Notes")
    lines.append("")
    lines.append("- Chunking/assembly/write/query Cypher text is byte-identical to the python "
                 "module; entity ids are uuid5(NAMESPACE_URL, `{project}::{type}::{name_norm}`).")
    lines.append("- Excluded from parity (documented): live LLM extraction + answers, PDF/DOCX/"
                 "PPTX/XLSX readers (python-side), xlsx structured pipeline (python-side), "
                 "local-mode Qdrant vector store (python-embedded, no wire protocol), neo4j "
                 "provider (optional rollback path only), MCP server surface (phase 13).")
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print()
    for name, (ok, detail) in results.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {name}: {detail.splitlines()[0][:160]}")
    print(f"\nreport → {REPORT_PATH}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
