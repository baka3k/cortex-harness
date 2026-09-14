#!/usr/bin/env python3
"""Phase-13 mind fixtures — build the doc corpus and run the PYTHON doc
ingest ONCE to populate the qdrant collection + FalkorDB graph the parity
servers share.

Corpus: deterministic markdown files under `fixtures/mind_corpus/`
(Vietnamese + English, recurring entities). Ingest follows the phase-14
mock/parity protocol with ONE change: the vector stage is REAL — paragraphs
are embedded with doc-tiny's bge-m3 SentenceTransformer and upserted into the
remote Qdrant server, exactly like `mcp_graph_rag.semantic_search` expects
(payload: text/source_id/paragraph_id/entity_ids/entity_mentions/
project_id/project_id_normalized). Entities come from the deterministic
regex miner (`mine_entities`, phase-14 protocol) — LLM/GLiNER disabled —
plus synthetic CO_OCCURS relations between co-located entities so the
graph-expansion lanes have RELATED edges to walk.

Storage targets: project `mindfix` registered in `fixtures/mind_config/`
with storage_backend=remote → qdrant http://127.0.0.1:6333 and FalkorDB
redis://127.0.0.1:6379, graph/collection `mindfix_doc`. Both the record
(Python) and compare (Rust) servers resolve the same targets through the
same registry file.

Usage (from repo root):
    .venv/bin/python scripts/rust_mcp/ingest_mind_fixture.py [--force]

Idempotent: skips when the collection + graph already hold the corpus
(deterministic build), unless --force.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "doc-tiny"))
sys.path.insert(0, str(REPO_ROOT / "code-tiny"))
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
CORPUS_DIR = FIXTURE_DIR / "mind_corpus"
CONFIG_DIR = FIXTURE_DIR / "mind_config"

PROJECT_ID = "mindfix"
GRAPH = "mindfix_doc"
COLLECTION = "mindfix_doc"
QDRANT_URL = "http://127.0.0.1:6333"
FALKORDB_URI = "redis://127.0.0.1:6379"
SOURCE_PREFIX = "mindfix"
MAX_PARA_CHARS = 1200
MIN_PARA_CHARS = 40

# ---------------------------------------------------------------- corpus ----

CORPUS: dict[str, str] = {
    "digital-key-overview.md": """# Digital Key 3.0 overview

Digital Key 3.0 là chuẩn mới cho phép người dùng mở khóa xe thông qua
điện thoại, dựa trên nền tảng BLE và UWB của Apple và Samsung. Chuẩn
ISO 15118 định nghĩa giao thức Plug&Charge cho việc xác thực và thanh
toán tự động giữa xe và trạm sạc.

Hệ sinh thái Digital Key 3.0 bao gồm Car Connectivity Consortium
(CCC) làm cơ quan chuẩn hóa, cùng các nhà sản xuất như BMW, Audi và
Genesis. Ở Việt Nam, Viettel и VinFast đã công bố thử nghiệm khóa xe
số trên mẫu SUV điện mới.

Ứng dụng Digital Key trên iOS sử dụng Secure Enclave để lưu trữ
khóa riêng, trong khi Android dùng StrongBox. Cả hai nền tảng đều
yêu cầu xác thực sinh trắc học trước khi phát tín hiệu BLE/UWB.

Short.
""",
    "falkordb-ops.md": """# FalkorDB operations runbook

FalkorDB là graph database chạy trên Redis, hỗ trợ truy vấn Cypher với
extension GRAPH.QUERY. Cụm production của DeerFlow sử dụng FalkorDB 4.0
trên EC2 với persistent storage qua AOF everysec.

Khi FalkorDB hết bộ nhớ, thao tác GRAPH.RO_QUERY sẽ trả lỗi OOM và
cần restart service bằng systemd. Log của FalkorDB ghi tại
/var/log/falkordb/redis.log và được ship sang Grafana qua Loki.

Backup của FalkorDB chạy hằng tuần bằng GRAPH.DUMP sang S3, trong khi
Qdrant backup vector collection cùng lúc để đảm bảo tính nhất quán
giữa graph và vector index của DeerFlow.
""",
    "qdrant-vector-search.md": """# Qdrant vector search internals

Qdrant là vector database hỗ trợ HNSW index với khoảng cách cosine
và euclid. Mỗi point trong Qdrant gồm vector 1024 chiều (bge-m3) và
payload JSON chứa source_id, paragraph_id và entity_mentions.

Collection trong Qdrant được shard theo project: collection mindfix_doc
chứa tài liệu của project này. Khi truy vấn semantic_search, hệ thống
embed câu hỏi bằng bge-m3 rồi search top-k trong collection tương ứng.

Để tối ưu Qdrant, DeerFlow bật on-disk payload index cho trường
source_id và quantization scalar int8, giúp giảm RAM 4 lần mà mất
chưa tới 1% độ chính xác recall@10.
""",
    "n8n-stock-api.md": """# n8n stock API integration

The SSI FastConnect API exposes the iOrder and ekos systems through
n8n webhooks. Each webhook signs its payload with JWT tokens issued
by the SSI OAuth2 endpoint, refreshed every 23 hours by a cron node.

The n8n workflow streams order book updates into DeerFlow, which
classifies them with DeerFlow's DeerFlow NLU layer before writing
summaries to the DeerFlow document store. Failures page the on-call
engineer through Grafana OnCall.

Rate limits: SSI FastConnect allows 5 requests per second per token
for iOrder market data and 30 requests per minute for ekos account
queries. Backoff uses exponential jitter in the n8n function node.
""",
    "deerflow-architecture.md": """# DeerFlow system architecture

DeerFlow là deep-research pipeline gồm 4 tầng: planner, researcher,
coder và reporter. Tầng researcher dùng DeerFlow retrieval kết hợp
FalkorDB graph context và Qdrant vector context để tổng hợp câu trả
lời có trích dẫn.

Planner của DeerFlow chạy trên LLM GPT-4o với prompt template quản
lý bằng LangExtract. Coder thực thi sandbox Python trên Docker,
ghi kết quả vào MinIO trước khi reporter tổng hợp báo cáo cuối.

Toàn bộ DeerFlow chạy trên AWS ap-southeast-1, EC2 c6i.2xlarge,
với Grafana giám sát và S3 lưu artifact. Deploy dùng Docker
Compose trong dev và EKS cho production.
""",
    "guides/security-baseline.md": """# Security baseline

Mọi webhook của n8n phải xác thực JWT trước khi xử lý payload.
Khóa ký JWT xoay mỗi 30 ngày và lưu trong Vault của HashiCorp.
Audit log ghi tại /var/log/deerflow/audit.jsonl.

Chuẩn ISO 15118 và Digital Key 3.0 yêu cầu bảo vệ giao tiếp bằng
TLS 1.3; certificate của CCC root CA được pin trong firmware của
BMW, Audi và Genesis. Thiết bị không tuân thủ sẽ bị từ chối khi
thực hiện Plug&Charge.

Cảnh báo bảo mật gửi tới Grafana OnCall và kênh Slack #deerflow-sec
trong vòng 5 phút kể từ khi Qdrant hoặc FalkorDB phát hiện truy
vấn bất thường.
""",
}

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
    r"nginx|Postgres|RedisJSON|deerflow|DeerFlow|MinIO|Vault|LangExtract|Secure Enclave|"
    r"StrongBox|UWB|BLE|EKS|AWS|Loki)\b"
)


def mine_entities(paragraph: str) -> list[dict]:
    """Deterministic regex NER — the shared phase-14 fixture-provider mock."""
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


# ------------------------------------------------------------------ main ----


def prepare_registry() -> Path:
    """Write the remote-backend registry file used by BOTH parity servers."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config_path = CONFIG_DIR / "mindfix.json"
    config_path.write_text(
        json.dumps(
            {
                "active": True,
                "project": {"code": PROJECT_ID},
                "storage_backend": "remote",
                "remote": {
                    "qdrant_url": QDRANT_URL,
                    "falkordb_uri": FALKORDB_URI,
                },
                "doc": {
                    "env": {
                        "FALKORDB_GRAPH": GRAPH,
                        "QDRANT_COLLECTION": COLLECTION,
                        "EMBEDDING_MODEL": "BAAI/bge-m3",
                        "device": "cpu",
                    }
                },
            },
            indent=1,
        )
        + "\n",
        encoding="utf-8",
    )
    return config_path


def write_corpus() -> list[Path]:
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, text in sorted(CORPUS.items()):
        path = CORPUS_DIR / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return sorted(written)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    prepare_registry()
    write_corpus()

    # Heavy imports (doc-tiny + torch + qdrant).
    from qdrant_client import QdrantClient  # noqa: F401
    from qdrant_client.http import models as qmodels
    from sentence_transformers import SentenceTransformer

    import graphrag_ingest_langextract as G
    from graph_store import FalkorDBGraphStore
    from tools.graph.driver.falkordb_driver import FalkorDBDriver

    from types import SimpleNamespace

    qdrant = QdrantClient(url=QDRANT_URL, check_compatibility=False)
    driver = FalkorDBDriver(uri=FALKORDB_URI, _suppress_deprecation=True)

    existing = [c.name for c in qdrant.get_collections().collections]
    populated = COLLECTION in existing
    if not populated or args.force:
        if populated:
            qdrant.delete_collection(COLLECTION)
        driver.execute_query_sync(
            "MATCH (n) DETACH DELETE n", {}, GRAPH
        )
        # `create_collection` of graphrag_ingest_langextract: bge-m3 dense
        # head = 1024 dims, COSINE (same as the doc-tiny ingest main).
        qdrant.create_collection(
            COLLECTION,
            vectors_config=qmodels.VectorParams(size=1024, distance=qmodels.Distance.COSINE),
        )
    else:
        print(f"[ingest] {COLLECTION} already populated — skipping (use --force to rebuild)")
        return 0

    # Deterministic fixture provider (LLM/GLiNER disabled) + CO_OCCURS
    # relations between entities sharing a paragraph.
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
        entities = mine_entities(text)
        relations = [
            {
                "source": entities[i]["name"],
                "target": entities[i + 1]["name"],
                "relation": "CO_OCCURS",
            }
            for i in range(len(entities) - 1)
        ]
        return G.build_graph_components_from_entities(
            entities,
            relations,
            merge_entities=merge_entities,
            normalize_mode=normalize_mode,
            project_id_normalized=project_id_normalized,
        )

    G.build_graph_components = fixture_provider

    store = FalkorDBGraphStore(driver, GRAPH)
    embedder = SentenceTransformer("BAAI/bge-m3", local_files_only=True, device="cpu")

    args = SimpleNamespace(
        entity_provider="fixture",
        no_batch=True,
        gliner_batch_size=1,
        graph_batch_size=8,
        no_entity_merge=False,
        entity_normalize_mode="aggressive",
        max_paragraph_chars=MAX_PARA_CHARS,
        min_paragraph_chars=MIN_PARA_CHARS,
        skip_llm_short=True,
        project_id=PROJECT_ID,
        source_id=None,
        collection=COLLECTION,
        gliner_labels="PERSON,ORG,PRODUCT,GPE,DATE,TECH,CRYPTO,STANDARD",
        gliner_threshold=0.3,
        spacy_model="en_core_web_sm",
        ruler_json=None,
    )

    files = G._iter_input_files(CORPUS_DIR)
    paragraphs_total = 0
    for file_path in files:
        rel = G._safe_source_id(CORPUS_DIR, file_path)
        source_id = f"{SOURCE_PREFIX}__{rel}"
        raw_text = G._read_input_text(file_path)
        G.process_text(
            raw_text,
            source_id,
            args,
            store,
            qdrant,
            embedder,
            project_id_normalized=PROJECT_ID,
        )
        paragraphs_total += len(G.split_paragraphs(raw_text.strip(), MAX_PARA_CHARS))

    count = qdrant.count(COLLECTION, exact=True).count
    nodes, _, _ = driver.execute_query_sync(
        "MATCH (e:Entity) RETURN count(e) AS n", {}, GRAPH
    )
    relations, _, _ = driver.execute_query_sync(
        "MATCH ()-[r:RELATED]->() RETURN count(r) AS n", {}, GRAPH
    )
    print(
        f"[ingest] done: files={len(files)} paragraphs≈{paragraphs_total} "
        f"qdrant_points={count} entities={nodes[0]['n']} relations={relations[0]['n']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
