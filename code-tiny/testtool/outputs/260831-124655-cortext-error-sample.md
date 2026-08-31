---
type: "MCP fixed-suite execution report"
date: "2026-08-31"
suite: "cortext-error-sample"
project: "cortext"
---

# MCP fixed-suite report: cortext-error-sample

## Summary

- Overall: **PASS**
- Started: `2026-08-31T12:46:54.846756+07:00`
- Finished: `2026-08-31T12:46:55.003889+07:00`
- Duration: `0.16s`
- Project: `cortext`
- Parser: `python`
- Live inventory: `44` tools across `2` servers
- Cases executed: `3` (`3` passed, `0` failed)
- Evidence: advertised schema, exact input, complete parsed JSON-RPC output.

| Status | Count |
| --- | ---: |
| SUCCESS_DATA | 0 |
| SUCCESS_EMPTY | 0 |
| TOOL_ERROR | 3 |
| PROTOCOL_ERROR | 0 |
| CLIENT_EXCEPTION | 0 |

### Error codes

| Code | Count |
| --- | ---: |
| `collection_unavailable` | 1 |
| `missing_required_parameters` | 2 |

### Inventory validation

- PASS: every live tool has exactly one fixed case.

## Execution index

| # | Server | Tool | Result | Status | Error code | Contract | Time (ms) |
| ---: | --- | --- | --- | --- | --- | --- | ---: |
| 1 | `graph_mcp` | `get_symbol` | PASS | TOOL_ERROR | `missing_required_parameters` | PASS | 4.87 |
| 2 | `graph_mcp` | `trace_flow` | PASS | TOOL_ERROR | `missing_required_parameters` | PASS | 4.14 |
| 3 | `mind_mcp` | `semantic_search` | PASS | TOOL_ERROR | `collection_unavailable` | PASS | 82.86 |

## 01. `graph_mcp.get_symbol`

- Result: **PASS**
- Status: **TOOL_ERROR**
- Contract: **PASS**
- Duration: `4.87 ms`
- Endpoint: `http://127.0.0.1:8788/mcp`
- Expected status: `TOOL_ERROR`
- Expected error code: `missing_required_parameters`
- Actual error code: `missing_required_parameters`

### Description

Fetch detailed metadata for a specific node by its ID.

### Input schema

```json
{
  "additionalProperties": false,
  "properties": {
    "node_id": {
      "default": null,
      "title": "Node Id"
    },
    "project_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null
    },
    "content_mode": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null
    },
    "include_raw_fields": {
      "default": false,
      "type": "boolean"
    },
    "parser_type": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null
    },
    "payload": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "default": null
    }
  },
  "type": "object"
}
```

### Output schema advertised by `tools/list`

```json
null
```

### Input executed

```json
{
  "project_id": "cortext",
  "parser_type": "python"
}
```

### Raw parsed JSON-RPC output

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "_meta": {
      "contract": "cortex.mcp.tool-result",
      "contractVersion": "1.0",
      "tool": "get_symbol"
    },
    "content": [
      {
        "type": "text",
        "text": "node_id is required."
      }
    ],
    "structuredContent": {
      "ok": false,
      "data": null,
      "error": {
        "code": "missing_required_parameters",
        "message": "node_id is required.",
        "retryable": false,
        "details": {
          "missing_parameters": [
            "node_id"
          ]
        }
      }
    },
    "isError": true
  }
}
```

## 02. `graph_mcp.trace_flow`

- Result: **PASS**
- Status: **TOOL_ERROR**
- Contract: **PASS**
- Duration: `4.14 ms`
- Endpoint: `http://127.0.0.1:8788/mcp`
- Expected status: `TOOL_ERROR`
- Expected error code: `missing_required_parameters`
- Actual error code: `missing_required_parameters`

### Description

Advanced flow tracing with custom relationship types (CALLS, POSSIBLE_CALLS, function pointers, etc.).

### Input schema

```json
{
  "additionalProperties": false,
  "properties": {
    "start_id": {
      "default": null,
      "title": "Start Id"
    },
    "end_id": {
      "default": null,
      "title": "End Id"
    },
    "parser_type": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null
    },
    "max_depth": {
      "default": 6,
      "type": "integer"
    },
    "direction": {
      "default": "out",
      "type": "string"
    },
    "rel_types": {
      "anyOf": [
        {
          "items": {
            "type": "string"
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "default": null
    },
    "relationship_types": {
      "anyOf": [
        {},
        {
          "type": "null"
        }
      ],
      "default": null
    },
    "limit": {
      "default": 30,
      "type": "integer"
    },
    "top_k": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "default": null
    },
    "debug": {
      "default": false,
      "type": "boolean"
    },
    "project_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null
    },
    "content_mode": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null
    },
    "include_raw_fields": {
      "default": false,
      "type": "boolean"
    },
    "payload": {
      "anyOf": [
        {
          "additionalProperties": true,
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "default": null
    }
  },
  "type": "object"
}
```

### Output schema advertised by `tools/list`

```json
null
```

### Input executed

```json
{
  "project_id": "cortext",
  "parser_type": "python"
}
```

### Raw parsed JSON-RPC output

```json
{
  "jsonrpc": "2.0",
  "id": 4,
  "result": {
    "_meta": {
      "contract": "cortex.mcp.tool-result",
      "contractVersion": "1.0",
      "tool": "trace_flow"
    },
    "content": [
      {
        "type": "text",
        "text": "start_id is required."
      }
    ],
    "structuredContent": {
      "ok": false,
      "data": null,
      "error": {
        "code": "missing_required_parameters",
        "message": "start_id is required.",
        "retryable": false,
        "details": {
          "missing_parameters": [
            "start_id",
            "end_id"
          ]
        }
      }
    },
    "isError": true
  }
}
```

## 03. `mind_mcp.semantic_search`

- Result: **PASS**
- Status: **TOOL_ERROR**
- Contract: **PASS**
- Duration: `82.86 ms`
- Endpoint: `http://127.0.0.1:8789/mcp`
- Expected status: `TOOL_ERROR`
- Expected error code: `collection_unavailable`
- Actual error code: `collection_unavailable`

### Description

Vector-only search in Qdrant. Returns passages without graph expansion.

        Per Phase 05 of the unified ingest/query contract plan, ``project_id``
        scopes the query to one project's shard; omit it to search across all
        projects. The Qdrant collection is resolved through the registry when
        ``project_id`` is given; the explicit ``collection`` arg still wins as
        an escape hatch.
        

### Input schema

```json
{
  "properties": {
    "query": {
      "title": "Query",
      "type": "string"
    },
    "top_k": {
      "default": 5,
      "title": "Top K",
      "type": "integer"
    },
    "source_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Source Id"
    },
    "collection": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Collection"
    },
    "project_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Project Id"
    },
    "max_passage_chars": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Max Passage Chars"
    },
    "include_entity_ids": {
      "default": true,
      "title": "Include Entity Ids",
      "type": "boolean"
    },
    "include_entity_mentions": {
      "default": false,
      "title": "Include Entity Mentions",
      "type": "boolean"
    }
  },
  "required": [
    "query"
  ],
  "title": "semantic_searchArguments",
  "type": "object"
}
```

### Output schema advertised by `tools/list`

```json
null
```

### Input executed

```json
{
  "query": "vector search latency optimization",
  "top_k": 5,
  "project_id": "cortext",
  "max_passage_chars": 500,
  "include_entity_ids": true,
  "include_entity_mentions": false
}
```

### Raw parsed JSON-RPC output

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "_meta": {
      "contract": "cortex.mcp.tool-result",
      "contractVersion": "1.0",
      "tool": "semantic_search"
    },
    "content": [
      {
        "type": "text",
        "text": "Requested document collection is not ingested or unavailable: cortext_doc"
      }
    ],
    "structuredContent": {
      "ok": false,
      "data": null,
      "error": {
        "code": "collection_unavailable",
        "message": "Requested document collection is not ingested or unavailable: cortext_doc",
        "retryable": false,
        "details": {}
      }
    },
    "isError": true
  }
}
```
