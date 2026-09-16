# Phase 01 — JVM/Web Overlay Audit Report

**Date:** 2026-09-16
**Branch:** feat/change-db
**Scope:** Audit 9 framework parsers (struts, servlet_jsp, spring, aspnet_framework,
aspnet_core, mybatis, database_sql, database_plsql, plus framework-related overlays)
for `*Facts` shape compatibility với `documents_from_categories`
(`cortex-sync/src/vector_sync.rs:275-402`).

---

## Required fields (per `documents_from_categories`)

| Field | Type | Source (priority) |
|---|---|---|
| `id` OR `symbol_id` | String | required, error if missing |
| `project_id` | String | required, error if missing |
| `kind` OR `node_type` | String | optional, defaults from category name |
| `name` OR `qualified_name` | String | optional, used in text composition |
| `file_path` OR `path` | String | optional, used in text + payload |
| `code` OR `summary` OR `comment` OR `note` | String | optional, used in text composition |
| `start_line` / `end_line` | Number | optional, payload |
| `project_name`, `repo`, `language` | String | optional, payload |

## Audit per parser

### 1. StrutsFact (`analyzer-jvm-overlays/src/struts/models.rs:239`)

| Field | Present? | Source |
|---|---|---|
| `stable_id` → symbol_id | ✓ | line 258 (`symbol_id.into(self.stable_id)`) |
| `project_id` | ✓ | line 246 |
| `kind` | ✓ | line 240 + line 261 |
| `name` | ✓ | line 241 + line 260 |
| `file_path` | ✓ | via `source.file_path` (line 267) |
| `start_line`/`end_line` | ✓ | via `source.start_line`/`end_line` (lines 268-269) |
| `code`/`summary`/`comment` | △ | NO direct field — substitute `raw_value`/`resolved_value`/`properties` (none direct) |
| `qualified_name` | △ | NO — derived from `name` |
| `project_name`, `language` | ✓ | lines 247, 265 |

**Decision:** WIRE via adapter. `to_graph_node()` exists (line 255) returns `Map<String, Value>` —
adapter chỉ cần gọi `to_graph_node()` rồi wrap `Value::Object(...)`.

**Coverage:** 100% fields needed; code/summary substitute via `raw_value` (already in graph row).

### 2. ServletJspFact (`analyzer-jvm-overlays/src/servlet_jsp/models.rs:375`)

| Field | Present? | Source |
|---|---|---|
| `stable_id` → symbol_id | ✓ | line 411 |
| `project_id` | ✓ | line 379 |
| `kind` | ✓ | line 376 + line 414 |
| `name` | ✓ | line 377 + line 413 |
| `file_path` | ✓ | via `source.file_path` (line 420) |
| `start_line`/`end_line` | ✓ | via `source.start_line`/`end_line` (lines 421-422) |
| `code`/`summary`/`comment` | △ | NO direct — `raw_value`/`resolved_value` (lines 429-435) |
| `qualified_name` | △ | NO — derive from `name` |

**Decision:** WIRE via adapter. `to_graph_node(generation_id)` (line 395) returns
`Map<String, Value>` — adapter use directly.

### 3. SpringFact (`analyzer-jvm-overlays/src/spring/models.rs:77`)

| Field | Present? | Source |
|---|---|---|
| `stable_id` → symbol_id | ✓ | line 99 |
| `project_id` | ✓ | line 81 |
| `kind` | ✓ | line 78 + line 101 |
| `name` | ✓ | line 79 + line 100 |
| `file_path` | ✓ | via `source.file_path` (line 106) |
| `start_line`/`end_line` | ✓ | via `source.start_line`/`end_line` (lines 107-108) |
| `code`/`summary`/`comment` | △ | NO direct — `raw_value`/`resolved_value` (lines 112-113) |
| `qualified_name` | △ | NO — derive from `name` |

**Decision:** WIRE via adapter. `to_graph_node()` (line 96) returns `Map<String, Value>`.

### 4. AspNet SemanticFact (`analyzer-web-overlays/src/aspnet/models.rs:126`)

| Field | Present? | Source |
|---|---|---|
| `stable_id` → symbol_id | ✓ | line 158 |
| `project_id` | ✓ | line 131 |
| `kind` | ✓ | line 127 + line 161 |
| `name` | ✓ | line 128 + line 160 |
| `file_path` | ✓ | via `source.file_path` (line 166) |
| `start_line`/`end_line` | ✓ | via `source.start_line`/`end_line` (lines 167-168) |
| `code`/`summary`/`comment` | △ | NO direct — derive from `properties` Map<String, Value> |
| `qualified_name` | △ | NO — derive from `name` + `framework` |

**Decision:** WIRE via adapter. **NOTE**: `to_graph_node(generation_id)` returns
`Result<Map<String, Value>, String>` (line 144) — adapter must handle Result.

### 5. Mybatis Facts (`analyzer-sql-family/src/mybatis/models.rs`)

Structures: `MapperInterfaceFact`, `MapperMethodFact`, `MapperParameterFact`,
`AnnotationFact`.

| Field | Present (MapperInterfaceFact)? |
|---|---|
| `stable_id` → symbol_id | ✓ line 239 |
| `project_id` | △ (NOT in MapperInterfaceFact) |
| `kind` | △ (no `kind` field; derive from struct name) |
| `name` | ✓ |
| `file_path` | △ via `source.file_path` |
| `start_line`/`end_line` | △ via `source.start_line`/`end_line` |
| `code`/`summary`/`comment` | △ — derive from `signature` (MapperMethodFact) |

**Decision:** **CARVE-OUT** cho phase-01. Mybatis Facts thiếu `project_id` field trực tiếp —
phải inject (analyzer sở hữu). Adapter cần thêm 1 trait method `project_id()` — không
generic. Đề xuất phase-02 spin-up riêng (sau khi unit-test pass cho 4 Facts trên). Hoặc
phase-02 làm 4+1 = 5 Facts sau khi Mybatis adapter thêm `project_id` injection.

### 6. database_sql, database_plsql

Hai parser này dùng `database_schema/` module. Cần verify riêng — **defer phase-02**.

### 7. struts (Framework AnalyzerConfig — `cortex-sync/src/registry.rs:153-161`)

`writes_vectors: false` ở registry — KHÔNG vào embedding pass loop (registry.rs:2105
gate `config.writes_vectors`). Wire ở phase-02 cần flip flag lên `true` SAU KHI adapter
verified cho struts facts.

---

## Tổng kết

| Parser | Adapter scope | Decision | Notes |
|---|---|---|---|
| struts | StrutsFact | WIRE | `to_graph_node()` returns Map directly |
| servlet_jsp | ServletJspFact | WIRE | same |
| spring | SpringFact | WIRE | same |
| aspnet_framework, aspnet_core | SemanticFact | WIRE | `to_graph_node()` returns Result — adapter handle |
| mybatis | MapperInterfaceFact + 3 sibling Facts | CARVE-OUT (phase-02) | thiếu `project_id` field |
| database_sql, database_plsql | (defer) | DEFER phase-02 | cần audit riêng |
| flutter, fastapi_django, laravel, express_js | (out of scope) | OUT | registry `writes_vectors: false` |

## Adapter design

**Trait `FactsRow` (in `cortex-graph-writer/src/facts_adapter.rs`):**

```rust
pub trait FactsRow {
    fn symbol_id(&self) -> &str;
    fn project_id(&self) -> &str;
    fn kind(&self) -> &str;
    fn name(&self) -> &str;
    fn file_path(&self) -> Option<&str>;
    fn code_or_summary(&self) -> Option<&str>;  // raw_value/resolved_value/properties
    fn start_line(&self) -> Option<i64>;
    fn end_line(&self) -> Option<i64>;
    fn language(&self) -> &str;
    fn framework(&self) -> &str;  // "struts", "spring", etc.
}

pub fn facts_to_embedding_categories<T: FactsRow>(
    parser: &str,
    facts_rows: &[T],
) -> Vec<(String, Vec<serde_json::Value>)> {
    let rows: Vec<Value> = facts_rows.iter().map(|f| {
        let mut row = Map::new();
        row.insert("id".into(), json!(f.symbol_id()));
        row.insert("symbol_id".into(), json!(f.symbol_id()));
        row.insert("project_id".into(), json!(f.project_id()));
        row.insert("kind".into(), json!(f.kind()));
        row.insert("node_type".into(), json!(format!("{}_{}", f.framework(), f.kind())));
        row.insert("name".into(), json!(f.name()));
        row.insert("qualified_name".into(), json!(format!("{}::{}", f.framework(), f.name())));
        if let Some(p) = f.file_path() { row.insert("file_path".into(), json!(p)); }
        if let Some(c) = f.code_or_summary() { row.insert("code".into(), json!(c)); }
        if let Some(l) = f.start_line() { row.insert("start_line".into(), json!(l)); }
        if let Some(l) = f.end_line() { row.insert("end_line".into(), json!(l)); }
        row.insert("language".into(), json!(f.language()));
        Value::Object(row)
    }).collect();
    vec![(format!("{}_facts", parser), rows)]
}
```

## Test fixture (phase-01)

`cortex-graph-writer/src/facts_adapter.rs` module test:
- Mock StrutsFact (3 facts) + AspNet SemanticFact (3 facts)
- Call `facts_to_embedding_categories("struts", &mock_struts)`
- Assert: `Vec<(String, Vec<Value>)>` non-empty; each Value has `symbol_id`/`project_id`/`kind`
- Assert: re-feed result to `vector_sync::documents_from_categories` — no error
  (round-trip shape parity)

## Gate dependencies

- Phase-02 cần `facts_adapter` module xong trước khi wire 4 framework analyzers (struts,
  servlet_jsp, spring, aspnet_framework, aspnet_core).
- Phase-02 mybatis/database_sql/database_plsql: spin-up sau khi adapter mở rộng cho
  thiếu `project_id` field (v2 của adapter).

## Open questions

- **OQ1**: Mybatis Facts thiếu `project_id` field — adapter v2 cần inject từ analyzer scope
  (closure capture `project_id`). Phase-02 deal.
- **OQ2**: 4 framework Facts đều KHÔNG có `code` field — substitute bằng `raw_value` ở graph row.
  Nếu `raw_value` rỗng (ví dụ servlet_jsp không có source value), `documents_from_categories`
  sẽ compose text không có `[code]` line — không error nhưng text ngắn. Acceptable cho MVP.