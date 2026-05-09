# LivingDoc

LivingDoc is a 3-phase pipeline that links code nodes in Neo4j with technical documents using LLM summaries, vector search, and Neo4j relationships.

## Overview

The pipeline has three phases:

1. Summarize code nodes from Neo4j into JSON files in a cache.
2. Vectorize summaries, update Neo4j summaries, and upsert vectors to Qdrant.
3. Search Qdrant to map code to documents and create Neo4j relationships.

## Prerequisites

- Neo4j running and reachable via Bolt.
- Code nodes have a stable `id` property.
- Document nodes have a stable `id` property.
- Qdrant running and reachable via HTTP.
- A vector collection that contains document vectors with payload key `doc_id`.

## Directory Layout

- `living-doc-summarize.py` Phase 1 summarization.
- `living-doc-vectorize.py` Phase 2 vectorization + Neo4j update.
- `living-doc-link.py` Phase 3 linking.
- `living-doc-ingest.py` Utility to list nodes from Neo4j.
- `living-doc-louvain.py` Utility to cluster Function nodes into InfraNode communities (GDS Louvain).
- `living-doc-pipeline.py` Orchestrator to run multiple phases in order.
- `strategy.md` Design notes.
- `cache/` Generated summaries and `_index.jsonl` mapping.

## Quick Start

```bash
export NEO4J_URI=bolt://localhost:7687
export NEO4J_USER=neo4j
export NEO4J_PASS=your_password
export PROJECT_ID=nfc_emulation_card
export QDRANT_URL=http://localhost:6333
export QDRANT_COLLECTION_CODE=livingdoc
export LLM_API_KEY=your_key

python living-doc-summarize.py
python living-doc-vectorize.py
python living-doc-link.py
```

Run all phases in one command:

```bash
python living-doc-pipeline.py
```

Skip optional phases:

```bash
python living-doc-pipeline.py --skip-louvain
```

Run a single phase:

```bash
python living-doc-pipeline.py --only summarize
```

## Script Order (VN)

Thứ tự chuẩn:
`living-doc-summarize.py` -> `living-doc-vectorize.py` -> `living-doc-link.py`

`living-doc-louvain.py` là optional và thường chạy sau khi đã có Function nodes trong Neo4j.

`living-doc-pipeline.py` mặc định chạy theo thứ tự:
`summarize` -> `vectorize` -> `link` -> `louvain`.

## living-doc-pipeline.py

Mục đích: Orchestrator chạy nhiều phase theo thứ tự. Không truyền thêm args cho từng phase, chỉ dùng env vars hiện có.
Nếu cần flags đặc biệt cho từng phase, chạy script đó trực tiếp hoặc set env var tương ứng.

Flags:

- `--skip-summarize`, `--skip-vectorize`, `--skip-link`, `--skip-louvain` để bỏ qua phase.
- `--only summarize|vectorize|link|louvain` để chạy riêng một phase.

## living-doc-summarize.py

Mục đích: Query Neo4j để lấy code nodes, gọi LLM để tóm tắt, ghi JSON vào `cache/` và `_index.jsonl`.

Đầu ra:

- `cache/{node_id}.json`
- `cache/_index.jsonl` ánh xạ file -> node_id

Biến môi trường và ý nghĩa:
| Biến | Default | Ý nghĩa |
| --- | --- | --- |
| `NEO4J_URI` | | Neo4j Bolt URI |
| `NEO4J_USER` | | Neo4j username |
| `NEO4J_PASS` | | Neo4j password |
| `PROJECT_ID` | | Lọc `n.project_id` theo `CONTAINS` |
| `NODE_LABELS` | | CSV labels để lọc node |
| `LABEL_MATCH` | `any` | `any` hoặc `all` cho `NODE_LABELS` |
| `NODE_ID_FIELD` | `id` | Property dùng làm ID ổn định |
| `CACHE_DIR` | `cache` | Thư mục cache |
| `SKIP_EXISTING` | `1` | `0` để ghi đè cache |
| `REQUIRE_NODE_ID` | `1` | `0` để cho phép node thiếu ID |
| `LIMIT` | | Giới hạn số node xử lý |
| `LLM_API_BASE` | `https://api.openai.com/v1` | Endpoint LLM |
| `LLM_API_KEY` | | API key |
| `LLM_MODEL` | `gpt-4o-mini` | Model |
| `LLM_TIMEOUT` | `60` | Timeout LLM (giây) |
| `LLM_SLEEP` | `0` | Nghỉ giữa các request (giây) |

## living-doc-vectorize.py

Mục đích: Đọc cache, update `summary` vào Neo4j, embed và upsert vector lên Qdrant.

Đầu vào:

- `cache/*.json`
- `cache/_index.jsonl` (bắt buộc nếu `REQUIRE_INDEX=1`)

Đầu ra:

- Neo4j cập nhật property `summary`
- Qdrant collection có vector + metadata

Biến môi trường và ý nghĩa:
| Biến | Default | Ý nghĩa |
| --- | --- | --- |
| `NEO4J_URI` | | Neo4j Bolt URI |
| `NEO4J_USER` | | Neo4j username |
| `NEO4J_PASS` | | Neo4j password |
| `PROJECT_ID` | | Lưu vào metadata |
| `NODE_ID_FIELD` | `id` | ID node |
| `FILE_PATH_FIELD` | `file_path` | Property lưu path gốc |
| `SUMMARY_PROPERTY` | `summary` | Property lưu summary |
| `SUMMARY_STORE` | `string` | `string` hoặc `map` |
| `CACHE_DIR` | `cache` | Thư mục cache |
| `CODE_EMBEDDING_MODEL` | `BAAI/bge-m3` | Model embedding |
| `EMBEDDING_DEVICE` | | Thiết bị (cpu/cuda) |
| `EMBED_SLEEP` | `0` | Nghỉ giữa các embed |
| `QDRANT_URL` | `http://localhost:6333` | Qdrant endpoint |
| `QDRANT_API_KEY` | | API key (nếu có) |
| `QDRANT_COLLECTION_CODE` | | Tên collection |
| `QDRANT_CREATE` | `1` | `0` để không auto-create |
| `QDRANT_STORE_SUMMARY` | `1` | `0` để không lưu full summary |
| `QDRANT_SUMMARY_KEY` | `summary` | Payload key cho summary |
| `SKIP_EXISTING` | `1` | `0` để luôn insert |
| `REQUIRE_INDEX` | `1` | `0` để cho phép thiếu `_index.jsonl` |

## living-doc-link.py

Mục đích: Embed lại summary, tìm top-k document tương tự trong Qdrant, tạo relationship trong Neo4j.

Đầu vào:

- `cache/*.json`
- Qdrant collection chứa vectors của document

Đầu ra:

- Relationship từ code node sang document (mặc định `IMPLEMENTS_LOGIC`)
- Thuộc tính `score` và `rank` trên relationship

Biến môi trường và ý nghĩa:
| Biến | Default | Ý nghĩa |
| --- | --- | --- |
| `NEO4J_URI` | | Neo4j Bolt URI |
| `NEO4J_USER` | | Neo4j username |
| `NEO4J_PASS` | | Neo4j password |
| `PROJECT_ID` | | Lọc code node theo project_id (so sánh exact) |
| `NODE_ID_FIELD` | `id` | ID code node |
| `CODE_LABEL` | | Label code node để match |
| `DOC_LABEL` | `Document` | Label doc node để match |
| `REL_TYPE` | `IMPLEMENTS_LOGIC` | Relationship mặc định |
| `DOC_ID_FIELD` | `id` | Property ID của doc node |
| `DOC_ID_KEY` | `doc_id` | Payload key trong Qdrant |
| `DOC_ID_FALLBACK_KEYS` | `paragraph_id,source_id` | Fallback payload keys |
| `LINK_BOTH` | `0` | `1` để link cả Paragraph và Document |
| `PARAGRAPH_LABEL` | `Paragraph` | Label Paragraph |
| `PARAGRAPH_ID_FIELD` | `paragraph_id` | Property id Paragraph |
| `PARAGRAPH_ID_KEY` | `paragraph_id` | Payload key Paragraph |
| `PARAGRAPH_REL` | `IMPLEMENTS_PARAGRAPH` | Relationship Paragraph |
| `DOCUMENT_LABEL` | `Document` | Label Document (khi link_both) |
| `DOCUMENT_ID_FIELD` | `id` | Property id Document |
| `DOCUMENT_ID_KEY` | `source_id` | Payload key Document |
| `DOCUMENT_REL` | `IMPLEMENTS_DOCUMENT` | Relationship Document |
| `CODE_EMBEDDING_MODEL` | `BAAI/bge-m3` | Model embedding |
| `EMBEDDING_DEVICE` | | Thiết bị (cpu/cuda) |
| `QDRANT_URL` | `http://localhost:6333` | Qdrant endpoint |
| `QDRANT_API_KEY` | | API key (nếu có) |
| `QDRANT_COLLECTION_CODE` | | Tên collection |
| `REQUIRE_DOC_KEY` | `1` | `0` để search không filter payload |
| `TOP_K` | `5` | Số kết quả mỗi node |
| `SCORE_THRESHOLD` | `0.0` | Ngưỡng score |
| `SLEEP` | `0` | Nghỉ giữa các node |
| `REQUIRE_INDEX` | `1` | `0` để cho phép thiếu `_index.jsonl` |

## living-doc-louvain.py

Mục đích: Chạy GDS Louvain trên Function nodes và materialize InfraNode.
Yêu cầu Neo4j đã cài Graph Data Science (GDS) plugin.

Đầu ra:

- Property `communityId` (hoặc `WRITE_PROPERTY`) trên Function
- Node `InfraNode` (hoặc `INFRA_LABEL`) và relationship `BELONGS_TO`

Biến môi trường và ý nghĩa:
| Biến | Default | Ý nghĩa |
| --- | --- | --- |
| `NEO4J_URI` | | Neo4j Bolt URI |
| `NEO4J_USER` | | Neo4j username |
| `NEO4J_PASS` | | Neo4j password |
| `PROJECT_ID` | | Lọc theo `project_id` dùng `CONTAINS` |
| `GDS_GRAPH_NAME` | `functionGraph` | Tên graph in-memory |
| `NODE_LABEL` | `Function` | Label node |
| `REL_TYPE` | `CALLS` | Type relationship |
| `ORIENTATION` | `UNDIRECTED` | `UNDIRECTED`, `NATURAL`, `REVERSE` |
| `WRITE_PROPERTY` | `communityId` | Property lưu community |
| `MIN_COMMUNITY_SIZE` | `4` | Kích thước cụm tối thiểu |
| `INFRA_LABEL` | `InfraNode` | Label InfraNode |
| `INFRA_ID_FIELD` | `id` | Property id InfraNode |
| `INFRA_STATUS` | `pending_summary` | Giá trị `status` mặc định |
| `BELONGS_REL` | `BELONGS_TO` | Relationship từ Function -> InfraNode |
| `DROP_GRAPH` | `0` | `1` để drop graph cũ trước khi tạo |
| `DROP_AFTER` | `0` | `1` để drop graph sau khi chạy |

## living-doc-ingest.py

Mục đích: In danh sách node ra stdout để kiểm tra dữ liệu.

Biến môi trường và ý nghĩa:
| Biến | Default | Ý nghĩa |
| --- | --- | --- |
| `NEO4J_URI` | | Neo4j Bolt URI |
| `NEO4J_USER` | | Neo4j username |
| `NEO4J_PASS` | | Neo4j password |
| `PROJECT_ID` | | Lọc `n.project_id` theo `CONTAINS` |
| `NODE_LABELS` | | CSV labels để lọc |
| `LABEL_MATCH` | `any` | `any` hoặc `all` |

## Notes

- The pipeline assumes `id` is stable and unique. Do not use Neo4j internal IDs for long-term mapping.
- If you need a stable identifier, use a custom `id` property and create a uniqueness constraint in Neo4j.
- Phase 3 will not link anything if the Qdrant collection only contains code vectors.

## Example Cypher Checks

```cypher
MATCH (n)
WHERE n.code IS NOT NULL AND NOT n:File AND NOT n:Class
RETURN count(n) AS total, count(n.id) AS with_id;
```

```cypher
MATCH (d:Document)
RETURN count(d) AS total, count(d.id) AS with_id;
```

---

# Sample

## Step1:

```
python livingdoc/living-doc-summarize.py \
    --neo4j-uri "bolt://localhost:7687" \
    --neo4j-user "neo4j" \
    --neo4j-pass "abcd1234" \
    --llm-api-base "http://localhost:11434/v1" \
    --llm-api-key "local" \
    --llm-model "deepseek-coder-v2" \
    --project-id digital_key_main \
    --node-labels  "Function,Class,AndroidComponent" \
    --nodes-list-path cache/_nodes.jsonl
```

## Step2:

```
python livingdoc/living-doc-vectorize.py \
  --neo4j-uri "bolt://localhost:7687" \
  --neo4j-user "neo4j" \
  --neo4j-pass "abcd1234" \
  --cache-dir cache \
  --embed-model "BAAI/bge-m3" \
  --qdrant-url http://localhost:6333 \
  --qdrant-create 1 \
  --skip-existing 0 \
  --require-index 1 \
  --embed-device mps \
  --collection graph_rag_entities\
  --qdrant-collection graph_rag_entities \
  --verbose
```

## Step3

```
python livingdoc/living-doc-link.py \
  --neo4j-uri "bolt://localhost:7687" \
  --neo4j-user "neo4j" \
  --neo4j-pass "abcd1234" \
  --cache-dir cache \
  --collection graph_rag_entities \
  --embed-model "BAAI/bge-m3" \
  --embed-device "mps" \
  --qdrant-url "http://localhost:6333" \
  --top-k 3 \
  --score-threshold 0.6 \
  --require-index 1 \
  --link-both 1 \
  --verbose
```

##Step4

```
python livingdoc/living-doc-louvain.py \
--neo4j-uri "bolt://localhost:7687" \
--neo4j-user "neo4j" \
--neo4j-pass "abcd1234" \
--project-id digital_key_main \
--graph-name functionGraph \
--node-label Function \
--rel-type CALLS \
--orientation UNDIRECTED \
--write-property communityId \
--min-community-size 4 \
--infra-label InfraNode \
--infra-id-field id \
--infra-status pending_summary \
--belongs-rel BELONGS_TO \
--drop-graph 0 \
--drop-after 0 \
--verbose
```

## Step5: Summarize-Infra

```
python livingdoc/living-doc-summarize-infra.py \
  --neo4j-uri "bolt://localhost:7687" \
  --neo4j-user "neo4j" \
  --neo4j-pass "abcd1234" \
  --project-id digital_key_main \
  --infra-label InfraNode \
  --belongs-rel BELONGS_TO \
  --node-label Function \
  --llm-api-base "http://localhost:11434/v1" \
  --llm-api-key "local" \
  --llm-model "deepseek-coder-v2" \
  --pending-status pending_summary \
  --done-status summarized \
  --min-members 2 \
  --max-functions 30 \
  --skip-existing 1 \
  --verbose
```

## Step6: Vectorize-Infra

```
python livingdoc/living-doc-vectorize-infra.py \
  --neo4j-uri "bolt://localhost:7687" \
  --neo4j-user "neo4j" \
  --neo4j-pass "abcd1234" \
  --project-id digital_key_main \
  --infra-label InfraNode \
  --done-status summarized \
  --embed-model "BAAI/bge-m3" \
  --embed-device mps \
  --qdrant-url "http://localhost:6333" \
  --collection digital_key_main \
  --qdrant-create 1 \
  --skip-existing 1 \
  --cache-dir cache \
  --verbose
```

--

```
python livingdoc/living-doc-pipeline.py \
 --neo4j-pass abcd1234 \
 --project-id digital_key_main \
 --verbose
```
