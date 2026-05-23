# VB Family Analyzer Guide (VB.NET, VB6, VBA, VBScript)

Tài liệu này mô tả cách cài môi trường và chạy nhóm analyzer VB trong `hyper-graph`.

## 1) Scope

Các script:

- `tools/vb/vbnet_analyzer.py`
- `tools/vb/vb6_analyzer.py`
- `tools/vb/vba_analyzer.py`
- `tools/vb/vbscript_analyzer.py`

Pipeline giữ nguyên chuẩn chung của repo:

1. incremental cleanup (Neo4j/Qdrant)
2. parse source
3. resolve call graph
4. write Neo4j
5. embed + upsert Qdrant
6. message scan

## 2) Yêu cầu môi trường

### Bắt buộc

- Python 3.11+ (khuyến nghị 3.12)
- Neo4j
- Qdrant (nếu muốn semantic vector search)

### Cho VB.NET Roslyn engine

- .NET SDK (khuyến nghị 9.x; 8.x vẫn hỗ trợ)
- Worker target: `net8.0;net9.0`
- Analyzer sẽ tự build worker khi chạy VB.NET ở mode `auto|roslyn`

Kiểm tra nhanh:

```bash
dotnet --info
dotnet --list-runtimes
```

## 3) Cài đặt

Từ thư mục `hyper-dev/hyper-graph`:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`requirements.txt` đã bao gồm grammar packages cho:

- VB.NET tree-sitter
- VB6 tree-sitter
- VBA tree-sitter
- VBScript tree-sitter

## 4) Biến môi trường khuyến nghị

```bash
export ROOT=/path/to/source
export PROJECT_ID=my_project
export PROJECT_NAME=my_project

export NEO4J_URI=bolt://localhost:7687
export NEO4J_USER=neo4j
export NEO4J_PASS=your_password
export NEO4J_DB=neo4j

export QDRANT_URL=http://localhost:6333
export QDRANT_COLLECTION=my_project
export CODE_EMBEDDING_MODEL=jinaai/jina-embeddings-v3
export EMBED_DEVICE=cpu
```

## 5) Chạy analyzer VB

### 5.1 Dry-run

```bash
.venv/bin/python tools/vb/vbnet_analyzer.py --root "$ROOT" --dry-run
.venv/bin/python tools/vb/vb6_analyzer.py --root "$ROOT" --dry-run
.venv/bin/python tools/vb/vba_analyzer.py --root "$ROOT" --dry-run
.venv/bin/python tools/vb/vbscript_analyzer.py --root "$ROOT" --dry-run
```

### 5.2 VB.NET mặc định (Roslyn AUTO)

```bash
.venv/bin/python tools/vb/vbnet_analyzer.py \
  --root "$ROOT" \
  --project-id "$PROJECT_ID" \
  --project-name "$PROJECT_NAME" \
  --neo4j-uri "$NEO4J_URI" \
  --neo4j-user "$NEO4J_USER" \
  --neo4j-password "$NEO4J_PASS" \
  --neo4j-db "$NEO4J_DB" \
  --qdrant-url "$QDRANT_URL" \
  --qdrant-collection "$QDRANT_COLLECTION" \
  --vbnet-parser-engine auto \
  --vbnet-semantic auto \
  --verbose
```

### 5.3 Ép Roslyn syntax-only

```bash
.venv/bin/python tools/vb/vbnet_analyzer.py \
  --root "$ROOT" \
  --vbnet-parser-engine roslyn \
  --vbnet-semantic off \
  --disable-message-scan \
  --verbose
```

### 5.4 Ép fallback regex (không dùng Roslyn)

```bash
.venv/bin/python tools/vb/vbnet_analyzer.py \
  --root "$ROOT" \
  --vbnet-parser-engine regex \
  --disable-message-scan \
  --verbose
```

### 5.5 Incremental theo owner manifest

```bash
.venv/bin/python tools/vb/vbnet_analyzer.py \
  --root "$ROOT" \
  --project-id "$PROJECT_ID" \
  --project-name "$PROJECT_NAME" \
  --incremental \
  --changed-files-manifest .cache/owner_manifests/$PROJECT_ID/vbnet_changed_owner.json \
  --deleted-files-manifest .cache/owner_manifests/$PROJECT_ID/vbnet_deleted_owner.json \
  --vbnet-parser-engine auto \
  --vbnet-semantic auto \
  --verbose
```

## 6) Cờ quan trọng cho VB.NET

- `--vbnet-parser-engine auto|roslyn|regex`  
  Default: `auto` (ưu tiên Roslyn, lỗi thì fallback regex).
- `--vbnet-semantic auto|on|off`  
  Default: `auto`:
  - Có `.sln/.vbproj` => semantic path
  - Không có workspace => syntax-only
- `--vbnet-roslyn-worker-project <path.csproj>`  
  Override đường dẫn worker.
- `--vbnet-roslyn-timeout-sec <float>`
- `--vbnet-roslyn-workspace-timeout-ms <int>`
- `--vbnet-roslyn-file-timeout-ms <int>`

## 7) Ý nghĩa log chính

- `[cleanup][qdrant] files=...`  
  Xóa vector cũ cho file changed/deleted trước khi ingest mới.
- `[parse][start] parser=vbnet files=N ...`  
  Bắt đầu phase parse, `N` là số file cần parse trong batch hiện tại.
- `[parse][engine] parser=vbnet engine=... semantic=...`  
  Engine thực tế của VB.NET (Roslyn/regex).
- `[parse][progress] parser=vbnet completed=x/N ...`  
  Tiến độ parse file.
- `[parse][fallback] ...`  
  Roslyn fail (batch/file), chuyển sang regex để không block toàn job.
- `[parse][done] parser=vbnet parsed=N/N`  
  Kết thúc phase parse.
- `[vb] calls: total/resolved`  
  Tỷ lệ resolve call sau parse.
- `[SCAN_RESULT] parser=vbnet files=... functions=... classes=...`  
  Tổng kết parser.

## 8) Troubleshooting nhanh

### VB.NET chạy lâu bất thường

1. Bật `--verbose` và kiểm tra có nhiều `[parse][fallback]` không.
2. Nếu fallback nhiều, kiểm tra:
   - `dotnet --list-runtimes`
   - `dotnet build tools/vb/roslyn_worker/RoslynVbWorker.csproj -c Release`
3. Cần debug parser thuần (không ghi DB/vector):
   - bỏ `--neo4j-*`
   - bỏ `--qdrant-url`
   - thêm `--disable-message-scan`

### Roslyn không dùng được trên máy hiện tại

- Tạm thời chạy:
  - `--vbnet-parser-engine regex`

## 9) Ghi chú compatibility

- `parse_meta` của VB.NET đã có thêm các field:
  - `parser_engine`, `semantic_mode`, `semantic_enabled`
  - `fallback_reason`, `worker_elapsed_ms`
  - `workspace_kind`, `solution_or_project_path`
  - `semantic_errors`, `requested_engine`
- `PARSE_CACHE_VERSION` đã bump để tránh cache contract cũ.
