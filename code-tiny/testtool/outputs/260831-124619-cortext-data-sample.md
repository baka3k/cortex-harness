---
type: "MCP fixed-suite execution report"
date: "2026-08-31"
suite: "cortext-data-sample"
project: "cortext"
---

# MCP fixed-suite report: cortext-data-sample

## Summary

- Overall: **FAIL**
- Started: `2026-08-31T12:46:18.939284+07:00`
- Finished: `2026-08-31T12:46:19.712922+07:00`
- Duration: `0.77s`
- Project: `cortext`
- Parser: `python`
- Live inventory: `44` tools across `2` servers
- Cases executed: `11` (`10` passed, `1` failed)
- Evidence: advertised schema, exact input, complete parsed JSON-RPC output.

| Status | Count |
| --- | ---: |
| SUCCESS_DATA | 8 |
| SUCCESS_EMPTY | 2 |
| TOOL_ERROR | 1 |
| PROTOCOL_ERROR | 0 |
| CLIENT_EXCEPTION | 0 |

### Error codes

| Code | Count |
| --- | ---: |
| `collection_unavailable` | 1 |

### Inventory validation

- PASS: every live tool has exactly one fixed case.

## Execution index

| # | Server | Tool | Result | Status | Error code | Contract | Time (ms) |
| ---: | --- | --- | --- | --- | --- | --- | ---: |
| 1 | `graph_mcp` | `search_functions` | PASS | SUCCESS_DATA |  | PASS | 165.33 |
| 2 | `graph_mcp` | `list_databases` | PASS | SUCCESS_DATA |  | PASS | 6.50 |
| 3 | `graph_mcp` | `list_parsers` | PASS | SUCCESS_DATA |  | PASS | 5.84 |
| 4 | `graph_mcp` | `get_project_modules` | PASS | SUCCESS_DATA |  | PASS | 10.93 |
| 5 | `graph_mcp` | `get_symbol` | PASS | SUCCESS_DATA |  | PASS | 5.26 |
| 6 | `graph_mcp` | `list_mcp_functions` | PASS | SUCCESS_DATA |  | PASS | 5.34 |
| 7 | `graph_mcp` | `semantic_search` | PASS | SUCCESS_DATA |  | PASS | 402.57 |
| 8 | `graph_mcp` | `explore_graph` | PASS | SUCCESS_DATA |  | PASS | 24.80 |
| 9 | `mind_mcp` | `list_source_ids` | PASS | SUCCESS_EMPTY |  | PASS | 4.97 |
| 10 | `mind_mcp` | `list_qdrant_collections` | PASS | SUCCESS_EMPTY |  | PASS | 10.23 |
| 11 | `mind_mcp` | `semantic_search` | FAIL | TOOL_ERROR | `collection_unavailable` | PASS | 52.65 |

## 01. `graph_mcp.search_functions`

- Result: **PASS**
- Status: **SUCCESS_DATA**
- Contract: **PASS**
- Duration: `165.33 ms`
- Endpoint: `http://127.0.0.1:8788/mcp`
- Expected status: `SUCCESS_DATA`

### Description

Search for functions/classes/types by name or qualified name. Returns BOTH node details AND IDs.

### Input schema

```json
{
  "additionalProperties": false,
  "properties": {
    "query": {
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
    "limit": {
      "default": 50,
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
    "framework": {
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
    "kinds": {
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
  "query": "render_markdown",
  "limit": 5,
  "project_id": "cortext",
  "parser_type": "python",
  "content_mode": "summary",
  "include_raw_fields": false
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
      "tool": "search_functions"
    },
    "content": [
      {
        "type": "text",
        "text": "Success: 3 results. Read structuredContent.data."
      }
    ],
    "structuredContent": {
      "ok": true,
      "data": {
        "db": "cortext",
        "results": [
          {
            "id": "main/1@code-tiny/testtool/mcp_batch_report.py",
            "labels": [
              "Function"
            ],
            "properties": {
              "name": "main",
              "node_type": "code",
              "qualified_name": "main",
              "kind": "function",
              "file_path": "code-tiny/testtool/mcp_batch_report.py",
              "start_line": 622,
              "end_line": 665,
              "arity": 1,
              "exported": false,
              "visibility": "unknown",
              "is_public_api": false,
              "visibility_source": "",
              "export_evidence": "",
              "signature": "",
              "external": false,
              "builtin": false,
              "react_role": "",
              "middleware_kind": "",
              "project_id": "cortext",
              "project_id_normalized": "cortext",
              "project_name": "cortext",
              "language": "python",
              "repo": "/Users/hieplq1.aip/AI/cortex-harness",
              "build_system": "",
              "updated_at": "2026-08-31T02:55:37.306Z",
              "content_mode": "summary",
              "content": "Performs unknown operation on main (takes 1 parameter)"
            }
          },
          {
            "id": "render_markdown/7@code-tiny/testtool/mcp_batch_report.py",
            "labels": [
              "Function"
            ],
            "properties": {
              "name": "render_markdown",
              "node_type": "code",
              "qualified_name": "render_markdown",
              "kind": "function",
              "file_path": "code-tiny/testtool/mcp_batch_report.py",
              "start_line": 450,
              "end_line": 588,
              "arity": 7,
              "exported": false,
              "visibility": "unknown",
              "is_public_api": false,
              "visibility_source": "",
              "export_evidence": "",
              "signature": "",
              "external": false,
              "builtin": false,
              "react_role": "",
              "middleware_kind": "",
              "project_id": "cortext",
              "project_id_normalized": "cortext",
              "project_name": "cortext",
              "language": "python",
              "repo": "/Users/hieplq1.aip/AI/cortex-harness",
              "build_system": "",
              "updated_at": "2026-08-31T02:55:37.306Z",
              "content_mode": "summary",
              "content": "Performs markdown operation (takes 7 parameters)"
            }
          },
          {
            "id": "test_markdown_report_contains_schema_input_raw_output_and_contract_result/0@tests/test_mcp_batch_report.py",
            "labels": [
              "Function"
            ],
            "properties": {
              "name": "test_markdown_report_contains_schema_input_raw_output_and_contract_result",
              "node_type": "code",
              "qualified_name": "test_markdown_report_contains_schema_input_raw_output_and_contract_result",
              "kind": "function",
              "file_path": "tests/test_mcp_batch_report.py",
              "start_line": 93,
              "end_line": 136,
              "arity": 0,
              "exported": false,
              "visibility": "unknown",
              "is_public_api": false,
              "visibility_source": "",
              "export_evidence": "",
              "signature": "",
              "external": false,
              "builtin": false,
              "react_role": "",
              "middleware_kind": "",
              "project_id": "cortext",
              "project_id_normalized": "cortext",
              "project_name": "cortext",
              "language": "python",
              "repo": "/Users/hieplq1.aip/AI/cortex-harness",
              "build_system": "",
              "updated_at": "2026-08-31T02:55:37.658Z",
              "content_mode": "summary",
              "content": "Performs unknown operation on test markdown report contains schema input raw output and contract result"
            }
          }
        ],
        "ids": [
          "main/1@code-tiny/testtool/mcp_batch_report.py",
          "render_markdown/7@code-tiny/testtool/mcp_batch_report.py",
          "test_markdown_report_contains_schema_input_raw_output_and_contract_result/0@tests/test_mcp_batch_report.py"
        ],
        "query_engine": "graph_generic",
        "capability": {
          "requested_parser": "python",
          "canonical_parser": "python",
          "query_engine": "graph_generic",
          "support_level": "partial",
          "support": {
            "symbols": "full",
            "calls": "partial",
            "endpoints": "partial",
            "database": "none"
          },
          "default_relationships_applied": []
        }
      },
      "error": null
    },
    "isError": false
  }
}
```

## 02. `graph_mcp.list_databases`

- Result: **PASS**
- Status: **SUCCESS_DATA**
- Contract: **PASS**
- Duration: `6.50 ms`
- Endpoint: `http://127.0.0.1:8788/mcp`
- Expected status: `SUCCESS_DATA`

### Description

List available graph databases or graph names from the active provider.

### Input schema

```json
{
  "additionalProperties": false,
  "properties": {
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
{}
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
      "tool": "list_databases"
    },
    "content": [
      {
        "type": "text",
        "text": "Success. Read structuredContent.data."
      }
    ],
    "structuredContent": {
      "ok": true,
      "data": {
        "databases": [
          "cortext",
          "khong-ton-tai-xyz",
          "bench-project",
          "stock",
          "procsample"
        ],
        "default": "cortext",
        "query_engine": "graph_generic",
        "capability": {
          "requested_parser": null,
          "canonical_parser": null,
          "query_engine": "graph_generic",
          "support_level": "generic",
          "support": {
            "symbols": "generic",
            "calls": "generic",
            "endpoints": "none",
            "database": "none"
          },
          "default_relationships_applied": []
        }
      },
      "error": null
    },
    "isError": false
  }
}
```

## 03. `graph_mcp.list_parsers`

- Result: **PASS**
- Status: **SUCCESS_DATA**
- Contract: **PASS**
- Duration: `5.84 ms`
- Endpoint: `http://127.0.0.1:8788/mcp`
- Expected status: `SUCCESS_DATA`

### Description

List available parser types supported by unified MCP.

### Input schema

```json
{
  "additionalProperties": false,
  "properties": {
    "detail_level": {
      "default": "summary",
      "type": "string"
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
  "detail_level": "summary"
}
```

### Raw parsed JSON-RPC output

```json
{
  "jsonrpc": "2.0",
  "id": 5,
  "result": {
    "_meta": {
      "contract": "cortex.mcp.tool-result",
      "contractVersion": "1.0",
      "tool": "list_parsers"
    },
    "content": [
      {
        "type": "text",
        "text": "Success. Read structuredContent.data."
      }
    ],
    "structuredContent": {
      "ok": true,
      "data": {
        "parsers": [
          ".net",
          "ajs",
          "android",
          "android-kotlin",
          "apache-struts",
          "apache_struts",
          "asp.net-core",
          "asp.net-framework",
          "aspnet-core",
          "aspnet-framework",
          "aspnet_core",
          "aspnet_framework",
          "aspnetcore",
          "aspnetframework",
          "bash",
          "c",
          "c#",
          "c++",
          "clang",
          "cobol",
          "cobol85",
          "cplus",
          "cpp",
          "cs",
          "csharp",
          "dart",
          "delphi",
          "django",
          "dotnet",
          "express",
          "express.js",
          "fastapi",
          "flask",
          "flutter",
          "flutter-dart",
          "flutter_dart",
          "gnucobol",
          "go",
          "ibm-cobol",
          "java",
          "javascript",
          "jobnet",
          "jp1",
          "js",
          "jsp",
          "jvm",
          "kotlin",
          "kotlin-android",
          "laravel",
          "my-batis",
          "mybatis",
          "nest.js",
          "nestjs",
          "node",
          "nodejs",
          "oracle-plsql",
          "pascal",
          "perl",
          "php",
          "pl/sql",
          "plsql",
          "pro*c",
          "pro-c",
          "proc",
          "py",
          "python",
          "rust",
          "servlet",
          "servlet-jsp",
          "servlet_jsp",
          "sh",
          "shell",
          "spring",
          "spring-boot",
          "spring_boot",
          "sql",
          "struts",
          "struts2",
          "swift",
          "symfony",
          "ts",
          "tsx",
          "typescript",
          "vb6",
          "vba",
          "vbnet",
          "vbscript",
          "visual_basic"
        ],
        "capabilities": [
          {
            "canonical_parser": "android",
            "aliases": [
              "android",
              "android-kotlin",
              "kotlin-android"
            ],
            "query_engine": "android_graph",
            "support_level": "full",
            "support": {
              "symbols": "full",
              "calls": "full",
              "endpoints": "none",
              "database": "none"
            },
            "generation_scoped": false
          },
          {
            "canonical_parser": "aspnet_core",
            "aliases": [
              "asp.net-core",
              "aspnet-core",
              "aspnet_core",
              "aspnetcore"
            ],
            "query_engine": "graph_generic",
            "support_level": "full",
            "support": {
              "symbols": "full",
              "calls": "full",
              "endpoints": "full",
              "database": "full"
            },
            "generation_scoped": true
          },
          {
            "canonical_parser": "aspnet_framework",
            "aliases": [
              "asp.net-framework",
              "aspnet-framework",
              "aspnet_framework",
              "aspnetframework"
            ],
            "query_engine": "graph_generic",
            "support_level": "full",
            "support": {
              "symbols": "full",
              "calls": "full",
              "endpoints": "full",
              "database": "full"
            },
            "generation_scoped": true
          },
          {
            "canonical_parser": "cobol",
            "aliases": [
              "cobol",
              "cobol85",
              "gnucobol",
              "ibm-cobol"
            ],
            "query_engine": "graph_generic",
            "support_level": "full",
            "support": {
              "symbols": "full",
              "calls": "full",
              "endpoints": "none",
              "database": "none"
            },
            "generation_scoped": false
          },
          {
            "canonical_parser": "cplus",
            "aliases": [
              "c",
              "c++",
              "clang",
              "cplus",
              "cpp",
              "pro*c",
              "pro-c",
              "proc"
            ],
            "query_engine": "graph_generic",
            "support_level": "full",
            "support": {
              "symbols": "full",
              "calls": "full",
              "endpoints": "none",
              "database": "full"
            },
            "generation_scoped": false
          },
          {
            "canonical_parser": "csharp",
            "aliases": [
              ".net",
              "c#",
              "cs",
              "csharp",
              "dotnet"
            ],
            "query_engine": "graph_generic",
            "support_level": "full",
            "support": {
              "symbols": "full",
              "calls": "full",
              "endpoints": "none",
              "database": "none"
            },
            "generation_scoped": false
          },
          {
            "canonical_parser": "delphi",
            "aliases": [
              "delphi",
              "pascal"
            ],
            "query_engine": "graph_generic",
            "support_level": "generic",
            "support": {
              "symbols": "generic",
              "calls": "generic",
              "endpoints": "none",
              "database": "none"
            },
            "generation_scoped": false
          },
          {
            "canonical_parser": "flutter",
            "aliases": [
              "dart",
              "flutter",
              "flutter-dart",
              "flutter_dart"
            ],
            "query_engine": "graph_generic",
            "support_level": "full",
            "support": {
              "symbols": "full",
              "calls": "full",
              "endpoints": "none",
              "database": "none"
            },
            "generation_scoped": false
          },
          {
            "canonical_parser": "go",
            "aliases": [
              "go"
            ],
            "query_engine": "graph_generic",
            "support_level": "generic",
            "support": {
              "symbols": "generic",
              "calls": "generic",
              "endpoints": "none",
              "database": "none"
            },
            "generation_scoped": false
          },
          {
            "canonical_parser": "javascript",
            "aliases": [
              "express",
              "express.js",
              "javascript",
              "js",
              "node",
              "nodejs"
            ],
            "query_engine": "graph_generic",
            "support_level": "partial",
            "support": {
              "symbols": "full",
              "calls": "partial",
              "endpoints": "partial",
              "database": "none"
            },
            "generation_scoped": false
          },
          {
            "canonical_parser": "jp1",
            "aliases": [
              "ajs",
              "jobnet",
              "jp1"
            ],
            "query_engine": "graph_generic",
            "support_level": "generic",
            "support": {
              "symbols": "generic",
              "calls": "generic",
              "endpoints": "none",
              "database": "none"
            },
            "generation_scoped": false
          },
          {
            "canonical_parser": "jvm",
            "aliases": [
              "java",
              "jvm",
              "kotlin"
            ],
            "query_engine": "graph_generic",
            "support_level": "generic",
            "support": {
              "symbols": "generic",
              "calls": "generic",
              "endpoints": "none",
              "database": "none"
            },
            "generation_scoped": false
          },
          {
            "canonical_parser": "mybatis",
            "aliases": [
              "my-batis",
              "mybatis"
            ],
            "query_engine": "graph_generic",
            "support_level": "full",
            "support": {
              "symbols": "full",
              "calls": "full",
              "endpoints": "none",
              "database": "full"
            },
            "generation_scoped": false
          },
          {
            "canonical_parser": "perl",
            "aliases": [
              "perl"
            ],
            "query_engine": "graph_generic",
            "support_level": "generic",
            "support": {
              "symbols": "generic",
              "calls": "generic",
              "endpoints": "none",
              "database": "none"
            },
            "generation_scoped": false
          },
          {
            "canonical_parser": "php",
            "aliases": [
              "laravel",
              "php",
              "symfony"
            ],
            "query_engine": "graph_generic",
            "support_level": "partial",
            "support": {
              "symbols": "full",
              "calls": "partial",
              "endpoints": "partial",
              "database": "none"
            },
            "generation_scoped": false
          },
          {
            "canonical_parser": "plsql",
            "aliases": [
              "oracle-plsql",
              "pl/sql",
              "plsql"
            ],
            "query_engine": "graph_generic",
            "support_level": "full",
            "support": {
              "symbols": "full",
              "calls": "none",
              "endpoints": "none",
              "database": "full"
            },
            "generation_scoped": false
          },
          {
            "canonical_parser": "python",
            "aliases": [
              "django",
              "fastapi",
              "flask",
              "py",
              "python"
            ],
            "query_engine": "graph_generic",
            "support_level": "partial",
            "support": {
              "symbols": "full",
              "calls": "partial",
              "endpoints": "partial",
              "database": "none"
            },
            "generation_scoped": false
          },
          {
            "canonical_parser": "rust",
            "aliases": [
              "rust"
            ],
            "query_engine": "graph_generic",
            "support_level": "generic",
            "support": {
              "symbols": "generic",
              "calls": "generic",
              "endpoints": "none",
              "database": "none"
            },
            "generation_scoped": false
          },
          {
            "canonical_parser": "servlet_jsp",
            "aliases": [
              "jsp",
              "servlet",
              "servlet-jsp",
              "servlet_jsp"
            ],
            "query_engine": "graph_generic",
            "support_level": "full",
            "support": {
              "symbols": "full",
              "calls": "full",
              "endpoints": "full",
              "database": "none"
            },
            "generation_scoped": true
          },
          {
            "canonical_parser": "shell",
            "aliases": [
              "bash",
              "sh",
              "shell"
            ],
            "query_engine": "graph_generic",
            "support_level": "generic",
            "support": {
              "symbols": "generic",
              "calls": "generic",
              "endpoints": "none",
              "database": "none"
            },
            "generation_scoped": false
          },
          {
            "canonical_parser": "spring",
            "aliases": [
              "spring",
              "spring-boot",
              "spring_boot"
            ],
            "query_engine": "graph_generic",
            "support_level": "full",
            "support": {
              "symbols": "full",
              "calls": "full",
              "endpoints": "full",
              "database": "full"
            },
            "generation_scoped": false
          },
          {
            "canonical_parser": "sql",
            "aliases": [
              "sql"
            ],
            "query_engine": "graph_generic",
            "support_level": "full",
            "support": {
              "symbols": "full",
              "calls": "none",
              "endpoints": "none",
              "database": "full"
            },
            "generation_scoped": false
          },
          {
            "canonical_parser": "struts",
            "aliases": [
              "apache-struts",
              "apache_struts",
              "struts",
              "struts2"
            ],
            "query_engine": "graph_generic",
            "support_level": "full",
            "support": {
              "symbols": "full",
              "calls": "full",
              "endpoints": "full",
              "database": "full"
            },
            "generation_scoped": false
          },
          {
            "canonical_parser": "swift",
            "aliases": [
              "swift"
            ],
            "query_engine": "graph_generic",
            "support_level": "generic",
            "support": {
              "symbols": "generic",
              "calls": "generic",
              "endpoints": "none",
              "database": "none"
            },
            "generation_scoped": false
          },
          {
            "canonical_parser": "typescript",
            "aliases": [
              "nest.js",
              "nestjs",
              "ts",
              "tsx",
              "typescript"
            ],
            "query_engine": "graph_generic",
            "support_level": "full",
            "support": {
              "symbols": "full",
              "calls": "full",
              "endpoints": "none",
              "database": "none"
            },
            "generation_scoped": false
          },
          {
            "canonical_parser": "vbnet",
            "aliases": [
              "vbnet"
            ],
            "query_engine": "graph_generic",
            "support_level": "generic",
            "support": {
              "symbols": "generic",
              "calls": "generic",
              "endpoints": "none",
              "database": "none"
            },
            "generation_scoped": false
          },
          {
            "canonical_parser": "visual_basic",
            "aliases": [
              "vb6",
              "vba",
              "vbscript",
              "visual_basic"
            ],
            "query_engine": "graph_generic",
            "support_level": "generic",
            "support": {
              "symbols": "generic",
              "calls": "generic",
              "endpoints": "none",
              "database": "none"
            },
            "generation_scoped": false
          }
        ],
        "detail_level": "summary",
        "capability_contract_version": 1,
        "default_query_engine": "graph_generic",
        "active_parser_type": null,
        "active_capability": {
          "requested_parser": null,
          "canonical_parser": null,
          "query_engine": "graph_generic",
          "support_level": "generic",
          "support": {
            "symbols": "generic",
            "calls": "generic",
            "endpoints": "none",
            "database": "none"
          }
        }
      },
      "error": null
    },
    "isError": false
  }
}
```

## 04. `graph_mcp.get_project_modules`

- Result: **PASS**
- Status: **SUCCESS_DATA**
- Contract: **PASS**
- Duration: `10.93 ms`
- Endpoint: `http://127.0.0.1:8788/mcp`
- Expected status: `SUCCESS`

### Description

Return canonical project modules, descriptors, and dependencies.

### Input schema

```json
{
  "additionalProperties": false,
  "properties": {
    "project_id": {
      "default": "",
      "type": "string"
    },
    "module_id": {
      "default": "",
      "type": "string"
    },
    "module_path": {
      "default": "",
      "type": "string"
    },
    "include_dependencies": {
      "default": true,
      "type": "boolean"
    },
    "offset": {
      "default": 0,
      "type": "integer"
    },
    "limit": {
      "default": 50,
      "type": "integer"
    },
    "parser_type": {
      "default": "",
      "type": "string"
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
  "include_dependencies": true,
  "offset": 0,
  "limit": 50,
  "parser_type": "python"
}
```

### Raw parsed JSON-RPC output

```json
{
  "jsonrpc": "2.0",
  "id": 6,
  "result": {
    "_meta": {
      "contract": "cortex.mcp.tool-result",
      "contractVersion": "1.0",
      "tool": "get_project_modules"
    },
    "content": [
      {
        "type": "text",
        "text": "Success. Read structuredContent.data."
      }
    ],
    "structuredContent": {
      "ok": true,
      "data": {
        "project_id": "cortext",
        "modules": [
          {
            "module_id": "project-module:cortext:.",
            "name": "cortex-harness",
            "module_path": ".",
            "kind": "root",
            "languages": [],
            "frameworks": [],
            "build_systems": [
              "make"
            ],
            "source_roots": [],
            "descriptor_ids": [
              "project-descriptor:33388070b10ca6a8e04fc1a3",
              "project-descriptor:8f63fc5c50dc81eb4cad47f2",
              "project-descriptor:9de3211edf827bf0d8a27ca0",
              "project-descriptor:bca04c1558a4b50b1688cc82",
              "project-descriptor:d25bc4bdfeadb2d1ec2a7f1f"
            ],
            "descriptors": [
              {
                "descriptor_type": "make",
                "id": "project-descriptor:8f63fc5c50dc81eb4cad47f2",
                "parse_depth": "dependency",
                "path": "Makefile",
                "role": "topology"
              },
              {
                "descriptor_type": "package_manifest",
                "id": "project-descriptor:9de3211edf827bf0d8a27ca0",
                "parse_depth": "identity",
                "path": "cortex-harness.sln",
                "role": "topology"
              },
              {
                "descriptor_type": "unknown",
                "id": "project-descriptor:d25bc4bdfeadb2d1ec2a7f1f",
                "parse_depth": "unsupported",
                "path": "env.example",
                "role": "secret-bearing"
              },
              {
                "descriptor_type": "package_manifest",
                "id": "project-descriptor:bca04c1558a4b50b1688cc82",
                "parse_depth": "identity",
                "path": "pyproject.toml",
                "role": "dependency"
              },
              {
                "descriptor_type": "package_manifest",
                "id": "project-descriptor:33388070b10ca6a8e04fc1a3",
                "parse_depth": "identity",
                "path": "uv.lock",
                "role": "generated"
              }
            ],
            "dependencies": [],
            "internal_dependencies": [],
            "external_dependencies": [],
            "confidence": "medium",
            "diagnostics": []
          },
          {
            "module_id": "project-module:cortext:DYNAMIC_MODULE",
            "name": "DYNAMIC_MODULE",
            "module_path": "DYNAMIC_MODULE",
            "kind": "unknown",
            "languages": [],
            "frameworks": [],
            "build_systems": [],
            "source_roots": [],
            "descriptor_ids": [],
            "descriptors": [],
            "dependencies": [],
            "internal_dependencies": [],
            "external_dependencies": [],
            "confidence": "high",
            "diagnostics": []
          },
          {
            "module_id": "project-module:cortext:app",
            "name": "app",
            "module_path": "app",
            "kind": "unknown",
            "languages": [],
            "frameworks": [],
            "build_systems": [],
            "source_roots": [],
            "descriptor_ids": [],
            "descriptors": [],
            "dependencies": [],
            "internal_dependencies": [],
            "external_dependencies": [],
            "confidence": "high",
            "diagnostics": []
          },
          {
            "module_id": "project-module:cortext:code-tiny",
            "name": "code-tiny",
            "module_path": "code-tiny",
            "kind": "unknown",
            "languages": [],
            "frameworks": [],
            "build_systems": [],
            "source_roots": [],
            "descriptor_ids": [
              "project-descriptor:5a248aa34b12fad3f3bcb4fe"
            ],
            "descriptors": [
              {
                "descriptor_type": "package_manifest",
                "id": "project-descriptor:5a248aa34b12fad3f3bcb4fe",
                "parse_depth": "identity",
                "path": "code-tiny/.env.example",
                "role": "secret-bearing"
              }
            ],
            "dependencies": [],
            "internal_dependencies": [],
            "external_dependencies": [],
            "confidence": "high",
            "diagnostics": []
          },
          {
            "module_id": "project-module:cortext:code-tiny/tools/common/aspnet/roslyn_worker",
            "name": "roslyn_worker",
            "module_path": "code-tiny/tools/common/aspnet/roslyn_worker",
            "kind": "unknown",
            "languages": [],
            "frameworks": [],
            "build_systems": [],
            "source_roots": [],
            "descriptor_ids": [
              "project-descriptor:4ee5a582d08f0112c3495ebc"
            ],
            "descriptors": [
              {
                "descriptor_type": "package_manifest",
                "id": "project-descriptor:4ee5a582d08f0112c3495ebc",
                "parse_depth": "identity",
                "path": "code-tiny/tools/common/aspnet/roslyn_worker/AspNetRoslynWorker.csproj",
                "role": "topology"
              }
            ],
            "dependencies": [],
            "internal_dependencies": [],
            "external_dependencies": [],
            "confidence": "high",
            "diagnostics": []
          },
          {
            "module_id": "project-module:cortext:code-tiny/tools/vb/roslyn_worker",
            "name": "roslyn_worker",
            "module_path": "code-tiny/tools/vb/roslyn_worker",
            "kind": "unknown",
            "languages": [],
            "frameworks": [],
            "build_systems": [],
            "source_roots": [],
            "descriptor_ids": [
              "project-descriptor:4997bb8d4db788a91d5ce1d0"
            ],
            "descriptors": [
              {
                "descriptor_type": "package_manifest",
                "id": "project-descriptor:4997bb8d4db788a91d5ce1d0",
                "parse_depth": "identity",
                "path": "code-tiny/tools/vb/roslyn_worker/RoslynVbWorker.csproj",
                "role": "topology"
              }
            ],
            "dependencies": [],
            "internal_dependencies": [],
            "external_dependencies": [],
            "confidence": "high",
            "diagnostics": []
          },
          {
            "module_id": "project-module:cortext:doc-tiny/http:",
            "name": "http:",
            "module_path": "doc-tiny/http:",
            "kind": "unknown",
            "languages": [],
            "frameworks": [],
            "build_systems": [],
            "source_roots": [],
            "descriptor_ids": [
              "project-descriptor:c81b1cfdd77bb9d5be4868e3"
            ],
            "descriptors": [
              {
                "descriptor_type": "package_manifest",
                "id": "project-descriptor:c81b1cfdd77bb9d5be4868e3",
                "parse_depth": "identity",
                "path": "doc-tiny/http:/.localhost:6333.cortex-owner.lock",
                "role": "generated"
              }
            ],
            "dependencies": [],
            "internal_dependencies": [],
            "external_dependencies": [],
            "confidence": "high",
            "diagnostics": []
          },
          {
            "module_id": "project-module:cortext:doc-tiny/http:/localhost:6333",
            "name": "localhost:6333",
            "module_path": "doc-tiny/http:/localhost:6333",
            "kind": "unknown",
            "languages": [],
            "frameworks": [],
            "build_systems": [],
            "source_roots": [],
            "descriptor_ids": [
              "project-descriptor:bcba80a4976903af940b4314"
            ],
            "descriptors": [
              {
                "descriptor_type": "package_manifest",
                "id": "project-descriptor:bcba80a4976903af940b4314",
                "parse_depth": "identity",
                "path": "doc-tiny/http:/localhost:6333/.lock",
                "role": "generated"
              }
            ],
            "dependencies": [],
            "internal_dependencies": [],
            "external_dependencies": [],
            "confidence": "high",
            "diagnostics": []
          },
          {
            "module_id": "project-module:cortext:feature",
            "name": "feature",
            "module_path": "feature",
            "kind": "unknown",
            "languages": [],
            "frameworks": [],
            "build_systems": [],
            "source_roots": [],
            "descriptor_ids": [],
            "descriptors": [],
            "dependencies": [],
            "internal_dependencies": [],
            "external_dependencies": [],
            "confidence": "high",
            "diagnostics": []
          },
          {
            "module_id": "project-module:cortext:library",
            "name": "library",
            "module_path": "library",
            "kind": "unknown",
            "languages": [],
            "frameworks": [],
            "build_systems": [],
            "source_roots": [],
            "descriptor_ids": [],
            "descriptors": [],
            "dependencies": [],
            "internal_dependencies": [],
            "external_dependencies": [],
            "confidence": "high",
            "diagnostics": []
          },
          {
            "module_id": "project-module:cortext:native",
            "name": "native",
            "module_path": "native",
            "kind": "unknown",
            "languages": [],
            "frameworks": [],
            "build_systems": [],
            "source_roots": [],
            "descriptor_ids": [],
            "descriptors": [],
            "dependencies": [],
            "internal_dependencies": [],
            "external_dependencies": [],
            "confidence": "high",
            "diagnostics": []
          },
          {
            "module_id": "project-module:cortext:tests/fixtures/aspnet-core-application",
            "name": "aspnet-core-application",
            "module_path": "tests/fixtures/aspnet-core-application",
            "kind": "unknown",
            "languages": [],
            "frameworks": [],
            "build_systems": [],
            "source_roots": [],
            "descriptor_ids": [
              "project-descriptor:446df1a7df9ed427dfbc79e1",
              "project-descriptor:56c3fbeec1d3a98b77cbcc91"
            ],
            "descriptors": [
              {
                "descriptor_type": "package_manifest",
                "id": "project-descriptor:446df1a7df9ed427dfbc79e1",
                "parse_depth": "identity",
                "path": "tests/fixtures/aspnet-core-application/CoreWeb.csproj",
                "role": "topology"
              },
              {
                "descriptor_type": "package_manifest",
                "id": "project-descriptor:56c3fbeec1d3a98b77cbcc91",
                "parse_depth": "identity",
                "path": "tests/fixtures/aspnet-core-application/appsettings.json",
                "role": "framework"
              }
            ],
            "dependencies": [],
            "internal_dependencies": [],
            "external_dependencies": [],
            "confidence": "high",
            "diagnostics": []
          },
          {
            "module_id": "project-module:cortext:tests/fixtures/aspnet-framework-application",
            "name": "aspnet-framework-application",
            "module_path": "tests/fixtures/aspnet-framework-application",
            "kind": "unknown",
            "languages": [],
            "frameworks": [],
            "build_systems": [],
            "source_roots": [],
            "descriptor_ids": [
              "project-descriptor:c0b8b7ca84ef48690e099499"
            ],
            "descriptors": [
              {
                "descriptor_type": "package_manifest",
                "id": "project-descriptor:c0b8b7ca84ef48690e099499",
                "parse_depth": "identity",
                "path": "tests/fixtures/aspnet-framework-application/LegacyWeb.csproj",
                "role": "topology"
              }
            ],
            "dependencies": [],
            "internal_dependencies": [],
            "external_dependencies": [],
            "confidence": "high",
            "diagnostics": []
          },
          {
            "module_id": "project-module:cortext:tests/fixtures/flutter-app",
            "name": "cortex_flutter_fixture",
            "module_path": "tests/fixtures/flutter-app",
            "kind": "unknown",
            "languages": [],
            "frameworks": [
              "flutter"
            ],
            "build_systems": [],
            "source_roots": [],
            "descriptor_ids": [
              "project-descriptor:9f1d59761a683c7a0f5d6299"
            ],
            "descriptors": [
              {
                "descriptor_type": "package_manifest",
                "id": "project-descriptor:9f1d59761a683c7a0f5d6299",
                "parse_depth": "identity",
                "path": "tests/fixtures/flutter-app/pubspec.yaml",
                "role": "dependency"
              }
            ],
            "dependencies": [],
            "internal_dependencies": [],
            "external_dependencies": [],
            "confidence": "high",
            "diagnostics": []
          },
          {
            "module_id": "project-module:cortext:tests/fixtures/framework-java-app",
            "name": "framework-java-app",
            "module_path": "tests/fixtures/framework-java-app",
            "kind": "maven_module",
            "languages": [
              "java",
              "kotlin"
            ],
            "frameworks": [
              "mybatis",
              "servlet_jsp",
              "spring"
            ],
            "build_systems": [
              "maven"
            ],
            "source_roots": [],
            "descriptor_ids": [
              "project-descriptor:c022224341b802b2234649ed"
            ],
            "descriptors": [
              {
                "descriptor_type": "maven_pom",
                "id": "project-descriptor:c022224341b802b2234649ed",
                "parse_depth": "dependency",
                "path": "tests/fixtures/framework-java-app/pom.xml",
                "role": "dependency"
              }
            ],
            "dependencies": [],
            "internal_dependencies": [],
            "external_dependencies": [],
            "confidence": "high",
            "diagnostics": []
          },
          {
            "module_id": "project-module:cortext:tests/fixtures/framework-java-app/src/main/resources",
            "name": "framework-java-app",
            "module_path": "tests/fixtures/framework-java-app/src/main/resources",
            "kind": "unknown",
            "languages": [],
            "frameworks": [],
            "build_systems": [],
            "source_roots": [],
            "descriptor_ids": [
              "project-descriptor:78714b44ba8e47d323aefe4d"
            ],
            "descriptors": [
              {
                "descriptor_type": "package_manifest",
                "id": "project-descriptor:78714b44ba8e47d323aefe4d",
                "parse_depth": "identity",
                "path": "tests/fixtures/framework-java-app/src/main/resources/application.yml",
                "role": "framework"
              }
            ],
            "dependencies": [],
            "internal_dependencies": [],
            "external_dependencies": [],
            "confidence": "high",
            "diagnostics": []
          },
          {
            "module_id": "project-module:cortext:tests/fixtures/framework-java-app/src/main/webapp/WEB-INF",
            "name": "WEB-INF",
            "module_path": "tests/fixtures/framework-java-app/src/main/webapp/WEB-INF",
            "kind": "unknown",
            "languages": [],
            "frameworks": [],
            "build_systems": [],
            "source_roots": [],
            "descriptor_ids": [
              "project-descriptor:50459a96dbb2d95bc0c31cab"
            ],
            "descriptors": [
              {
                "descriptor_type": "package_manifest",
                "id": "project-descriptor:50459a96dbb2d95bc0c31cab",
                "parse_depth": "identity",
                "path": "tests/fixtures/framework-java-app/src/main/webapp/WEB-INF/web.xml",
                "role": "framework"
              }
            ],
            "dependencies": [],
            "internal_dependencies": [],
            "external_dependencies": [],
            "confidence": "high",
            "diagnostics": []
          },
          {
            "module_id": "project-module:cortext:tests/fixtures/legacy-migration-e2e",
            "name": "legacy-migration-e2e",
            "module_path": "tests/fixtures/legacy-migration-e2e",
            "kind": "unknown",
            "languages": [],
            "frameworks": [],
            "build_systems": [],
            "source_roots": [],
            "descriptor_ids": [
              "project-descriptor:70e8bb3b7bfac0424a147efa"
            ],
            "descriptors": [
              {
                "descriptor_type": "runtime_config",
                "id": "project-descriptor:70e8bb3b7bfac0424a147efa",
                "parse_depth": "identity",
                "path": "tests/fixtures/legacy-migration-e2e/settings.ini",
                "role": "configuration"
              }
            ],
            "dependencies": [],
            "internal_dependencies": [],
            "external_dependencies": [],
            "confidence": "high",
            "diagnostics": []
          },
          {
            "module_id": "project-module:cortext:tests/fixtures/project-topology",
            "name": "mixed-topology",
            "module_path": "tests/fixtures/project-topology",
            "kind": "unknown",
            "languages": [],
            "frameworks": [],
            "build_systems": [
              "gradle"
            ],
            "source_roots": [],
            "descriptor_ids": [
              "project-descriptor:76c0f7d49c7c2adf4f0dfb8e"
            ],
            "descriptors": [
              {
                "descriptor_type": "gradle_settings",
                "id": "project-descriptor:76c0f7d49c7c2adf4f0dfb8e",
                "parse_depth": "topology",
                "path": "tests/fixtures/project-topology/settings.gradle.kts",
                "role": "topology"
              }
            ],
            "dependencies": [],
            "internal_dependencies": [],
            "external_dependencies": [],
            "confidence": "high",
            "diagnostics": []
          },
          {
            "module_id": "project-module:cortext:tests/fixtures/project-topology/ant",
            "name": "ant",
            "module_path": "tests/fixtures/project-topology/ant",
            "kind": "unknown",
            "languages": [],
            "frameworks": [],
            "build_systems": [
              "ant"
            ],
            "source_roots": [],
            "descriptor_ids": [
              "project-descriptor:bbb5dd54a374035f1e8a12ed"
            ],
            "descriptors": [
              {
                "descriptor_type": "ant_build",
                "id": "project-descriptor:bbb5dd54a374035f1e8a12ed",
                "parse_depth": "topology",
                "path": "tests/fixtures/project-topology/ant/build.xml",
                "role": "topology"
              }
            ],
            "dependencies": [],
            "internal_dependencies": [],
            "external_dependencies": [],
            "confidence": "high",
            "diagnostics": []
          },
          {
            "module_id": "project-module:cortext:tests/fixtures/project-topology/api",
            "name": "api",
            "module_path": "tests/fixtures/project-topology/api",
            "kind": "unknown",
            "languages": [],
            "frameworks": [],
            "build_systems": [],
            "source_roots": [],
            "descriptor_ids": [
              "project-descriptor:79ad0c63e824945d62605c8f"
            ],
            "descriptors": [
              {
                "descriptor_type": "protobuf",
                "id": "project-descriptor:79ad0c63e824945d62605c8f",
                "parse_depth": "semantic",
                "path": "tests/fixtures/project-topology/api/service.proto",
                "role": "interface"
              }
            ],
            "dependencies": [],
            "internal_dependencies": [],
            "external_dependencies": [],
            "confidence": "high",
            "diagnostics": []
          },
          {
            "module_id": "project-module:cortext:tests/fixtures/project-topology/app",
            "name": "app",
            "module_path": "tests/fixtures/project-topology/app",
            "kind": "android_application",
            "languages": [
              "java",
              "kotlin"
            ],
            "frameworks": [
              "spring"
            ],
            "build_systems": [
              "gradle"
            ],
            "source_roots": [],
            "descriptor_ids": [
              "project-descriptor:4d25b262081f7578b5f81cc6",
              "project-descriptor:52dba7f522b290a549fc8691",
              "project-descriptor:851ee8fce39204f778e232c0",
              "project-descriptor:9e46382247fbe33c2a02bbd6",
              "project-descriptor:f2cf325a8ec1787e7328c08b"
            ],
            "descriptors": [
              {
                "descriptor_type": "gradle_build",
                "id": "project-descriptor:4d25b262081f7578b5f81cc6",
                "parse_depth": "dependency",
                "path": "tests/fixtures/project-topology/app/build.gradle.kts",
                "role": "dependency"
              },
              {
                "descriptor_type": "android_manifest",
                "id": "project-descriptor:851ee8fce39204f778e232c0",
                "parse_depth": "semantic",
                "path": "tests/fixtures/project-topology/app/src/main/AndroidManifest.xml",
                "role": "framework"
              },
              {
                "descriptor_type": "resource",
                "id": "project-descriptor:f2cf325a8ec1787e7328c08b",
                "parse_depth": "semantic",
                "path": "tests/fixtures/project-topology/app/src/main/res/layout/activity_main.xml",
                "role": "resource"
              },
              {
                "descriptor_type": "resource",
                "id": "project-descriptor:52dba7f522b290a549fc8691",
                "parse_depth": "semantic",
                "path": "tests/fixtures/project-topology/app/src/main/res/navigation/main_nav.xml",
                "role": "resource"
              },
              {
                "descriptor_type": "resource",
                "id": "project-descriptor:9e46382247fbe33c2a02bbd6",
                "parse_depth": "semantic",
                "path": "tests/fixtures/project-topology/app/src/main/res/values/strings.xml",
                "role": "resource"
              }
            ],
            "dependencies": [],
            "internal_dependencies": [],
            "external_dependencies": [],
            "confidence": "medium",
            "diagnostics": []
          },
          {
            "module_id": "project-module:cortext:tests/fixtures/project-topology/dynamic",
            "name": "dynamic",
            "module_path": "tests/fixtures/project-topology/dynamic",
            "kind": "unknown",
            "languages": [],
            "frameworks": [],
            "build_systems": [
              "gradle"
            ],
            "source_roots": [],
            "descriptor_ids": [
              "project-descriptor:921da786911f9025298ea2d2"
            ],
            "descriptors": [
              {
                "descriptor_type": "gradle_settings",
                "id": "project-descriptor:921da786911f9025298ea2d2",
                "parse_depth": "topology",
                "path": "tests/fixtures/project-topology/dynamic/settings.gradle",
                "role": "topology"
              }
            ],
            "dependencies": [],
            "internal_dependencies": [],
            "external_dependencies": [],
            "confidence": "medium",
            "diagnostics": []
          },
          {
            "module_id": "project-module:cortext:tests/fixtures/project-topology/feature",
            "name": "feature",
            "module_path": "tests/fixtures/project-topology/feature",
            "kind": "android_dynamic_feature",
            "languages": [
              "java",
              "kotlin"
            ],
            "frameworks": [],
            "build_systems": [
              "gradle"
            ],
            "source_roots": [],
            "descriptor_ids": [
              "project-descriptor:2eae181cbcbb9863c15d0f83"
            ],
            "descriptors": [
              {
                "descriptor_type": "gradle_build",
                "id": "project-descriptor:2eae181cbcbb9863c15d0f83",
                "parse_depth": "dependency",
                "path": "tests/fixtures/project-topology/feature/build.gradle.kts",
                "role": "dependency"
              }
            ],
            "dependencies": [],
            "internal_dependencies": [],
            "external_dependencies": [],
            "confidence": "high",
            "diagnostics": []
          },
          {
            "module_id": "project-module:cortext:tests/fixtures/project-topology/jvm",
            "name": "parent",
            "module_path": "tests/fixtures/project-topology/jvm",
            "kind": "maven_module",
            "languages": [
              "java",
              "kotlin"
            ],
            "frameworks": [],
            "build_systems": [
              "maven"
            ],
            "source_roots": [],
            "descriptor_ids": [
              "project-descriptor:b2221ebb1d57e82ad7ba9eae"
            ],
            "descriptors": [
              {
                "descriptor_type": "maven_pom",
                "id": "project-descriptor:b2221ebb1d57e82ad7ba9eae",
                "parse_depth": "dependency",
                "path": "tests/fixtures/project-topology/jvm/pom.xml",
                "role": "dependency"
              }
            ],
            "dependencies": [],
            "internal_dependencies": [],
            "external_dependencies": [],
            "confidence": "high",
            "diagnostics": []
          },
          {
            "module_id": "project-module:cortext:tests/fixtures/project-topology/jvm/child",
            "name": "child",
            "module_path": "tests/fixtures/project-topology/jvm/child",
            "kind": "maven_module",
            "languages": [
              "java",
              "kotlin"
            ],
            "frameworks": [
              "mybatis"
            ],
            "build_systems": [
              "maven"
            ],
            "source_roots": [],
            "descriptor_ids": [
              "project-descriptor:006e7586219007cc5683e84e"
            ],
            "descriptors": [
              {
                "descriptor_type": "maven_pom",
                "id": "project-descriptor:006e7586219007cc5683e84e",
                "parse_depth": "dependency",
                "path": "tests/fixtures/project-topology/jvm/child/pom.xml",
                "role": "dependency"
              }
            ],
            "dependencies": [],
            "internal_dependencies": [],
            "external_dependencies": [],
            "confidence": "high",
            "diagnostics": []
          },
          {
            "module_id": "project-module:cortext:tests/fixtures/project-topology/jvm/child/src/main/resources",
            "name": "resources",
            "module_path": "tests/fixtures/project-topology/jvm/child/src/main/resources",
            "kind": "unknown",
            "languages": [],
            "frameworks": [
              "mybatis"
            ],
            "build_systems": [],
            "source_roots": [],
            "descriptor_ids": [
              "project-descriptor:9186a29de6bacaeb6e0dc0e6"
            ],
            "descriptors": [
              {
                "descriptor_type": "package_manifest",
                "id": "project-descriptor:9186a29de6bacaeb6e0dc0e6",
                "parse_depth": "identity",
                "path": "tests/fixtures/project-topology/jvm/child/src/main/resources/mybatis-config.xml",
                "role": "framework"
              }
            ],
            "dependencies": [],
            "internal_dependencies": [],
            "external_dependencies": [],
            "confidence": "high",
            "diagnostics": []
          },
          {
            "module_id": "project-module:cortext:tests/fixtures/project-topology/library",
            "name": "library",
            "module_path": "tests/fixtures/project-topology/library",
            "kind": "android_library",
            "languages": [
              "java",
              "kotlin"
            ],
            "frameworks": [],
            "build_systems": [
              "gradle"
            ],
            "source_roots": [],
            "descriptor_ids": [
              "project-descriptor:a8951bb1437a79af528f1012"
            ],
            "descriptors": [
              {
                "descriptor_type": "gradle_build",
                "id": "project-descriptor:a8951bb1437a79af528f1012",
                "parse_depth": "dependency",
                "path": "tests/fixtures/project-topology/library/build.gradle",
                "role": "dependency"
              }
            ],
            "dependencies": [],
            "internal_dependencies": [],
            "external_dependencies": [],
            "confidence": "high",
            "diagnostics": []
          },
          {
            "module_id": "project-module:cortext:tests/fixtures/project-topology/malformed",
            "name": "malformed",
            "module_path": "tests/fixtures/project-topology/malformed",
            "kind": "unknown",
            "languages": [],
            "frameworks": [],
            "build_systems": [],
            "source_roots": [],
            "descriptor_ids": [
              "project-descriptor:41817689bf2e24c89d19a34e"
            ],
            "descriptors": [
              {
                "descriptor_type": "maven_pom",
                "id": "project-descriptor:41817689bf2e24c89d19a34e",
                "parse_depth": "identity",
                "path": "tests/fixtures/project-topology/malformed/pom.xml",
                "role": "identity"
              }
            ],
            "dependencies": [],
            "internal_dependencies": [],
            "external_dependencies": [],
            "confidence": "medium",
            "diagnostics": []
          },
          {
            "module_id": "project-module:cortext:tests/fixtures/project-topology/native",
            "name": "native",
            "module_path": "tests/fixtures/project-topology/native",
            "kind": "unknown",
            "languages": [],
            "frameworks": [],
            "build_systems": [
              "cmake",
              "make"
            ],
            "source_roots": [],
            "descriptor_ids": [
              "project-descriptor:127edf93e98b3255d173457c",
              "project-descriptor:b946d3cbe8f55f9665903bf0"
            ],
            "descriptors": [
              {
                "descriptor_type": "cmake",
                "id": "project-descriptor:b946d3cbe8f55f9665903bf0",
                "parse_depth": "dependency",
                "path": "tests/fixtures/project-topology/native/CMakeLists.txt",
                "role": "topology"
              },
              {
                "descriptor_type": "make",
                "id": "project-descriptor:127edf93e98b3255d173457c",
                "parse_depth": "dependency",
                "path": "tests/fixtures/project-topology/native/Makefile",
                "role": "topology"
              }
            ],
            "dependencies": [],
            "internal_dependencies": [],
            "external_dependencies": [],
            "confidence": "high",
            "diagnostics": []
          },
          {
            "module_id": "project-module:cortext:tests/fixtures/project-topology/native/extra",
            "name": "extra",
            "module_path": "tests/fixtures/project-topology/native/extra",
            "kind": "unknown",
            "languages": [],
            "frameworks": [],
            "build_systems": [],
            "source_roots": [],
            "descriptor_ids": [],
            "descriptors": [],
            "dependencies": [],
            "internal_dependencies": [],
            "external_dependencies": [],
            "confidence": "high",
            "diagnostics": []
          },
          {
            "module_id": "project-module:cortext:tests/fixtures/shell-application",
            "name": "shell-application",
            "module_path": "tests/fixtures/shell-application",
            "kind": "unknown",
            "languages": [],
            "frameworks": [],
            "build_systems": [],
            "source_roots": [],
            "descriptor_ids": [
              "project-descriptor:86fbf21bdb37906c71484a84"
            ],
            "descriptors": [
              {
                "descriptor_type": "runtime_config",
                "id": "project-descriptor:86fbf21bdb37906c71484a84",
                "parse_depth": "identity",
                "path": "tests/fixtures/shell-application/settings.ini",
                "role": "configuration"
              }
            ],
            "dependencies": [],
            "internal_dependencies": [],
            "external_dependencies": [],
            "confidence": "high",
            "diagnostics": []
          }
        ],
        "total": 32,
        "offset": 0,
        "limit": 50,
        "has_more": false,
        "capability": {
          "requested_parser": "python",
          "canonical_parser": "python",
          "query_engine": "graph_generic",
          "support_level": "partial",
          "support": {
            "symbols": "full",
            "calls": "partial",
            "endpoints": "partial",
            "database": "none"
          },
          "default_relationships_applied": [
            "HAS_DESCRIPTOR",
            "DEPENDS_ON"
          ]
        },
        "capability_diagnostics": {
          "schema_status": "available",
          "support_status": "supported",
          "requested_relationships": [
            "HAS_DESCRIPTOR",
            "DEPENDS_ON"
          ],
          "used_relationships": [
            "HAS_DESCRIPTOR",
            "DEPENDS_ON"
          ],
          "omitted_relationships": [],
          "available_relationships": [
            "ALTERS",
            "CALLS",
            "CONDITIONAL",
            "DEFINES",
            "EXITS",
            "FALLS_THROUGH",
            "GOES_TO_DYNAMIC",
            "INCLUDES",
            "PERFORMS_THRU",
            "READS",
            "REFERENCES",
            "RETURNS",
            "WRITES",
            "CONTAINS",
            "IMPORTS",
            "DECLARES",
            "EXTENDS",
            "DECLARES_INTENT_ACTION",
            "DECLARES_COMPONENT",
            "INHERITS_FROM",
            "OVERRIDES",
            "HAS_FILE",
            "HAS_INVOCATION",
            "NEXT",
            "SEMANTIC_OF",
            "HANDLES",
            "APPLIES_TO",
            "FORWARDS_TO",
            "MAPS_TO",
            "RESOLVES_TO",
            "DECLARES_METHOD",
            "DECLARES_STATEMENT",
            "BINDS_STATEMENT",
            "HAS_RESULT_MAPPING",
            "MAPS_COLUMN",
            "MAPS_PROPERTY",
            "DEPENDS_ON_PARAMETER",
            "READS_FROM",
            "REFERENCES_COLUMN",
            "USES_RESULT_MAP",
            "LOADS_FROM",
            "POSTS_BACK_TO",
            "DEPENDS_ON",
            "RENDERS",
            "WRITES_SESSION",
            "HANDLED_BY",
            "MAPPED_TO",
            "REFERENCES_TABLE",
            "WRITES_TO",
            "SAME_MODULE",
            "HAS_DESCRIPTOR",
            "EXPOSES_ENDPOINT",
            "DECLARES_SERVICE",
            "HAS_RPC",
            "USES_FRAMEWORK",
            "EXPOSES_API"
          ],
          "explicit_request": false,
          "required_relationships": [
            "HAS_DESCRIPTOR"
          ],
          "missing_required_relationships": [],
          "required_labels": [
            "ProjectModule",
            "BuildDescriptor"
          ],
          "available_labels": [
            "Project",
            "Repository",
            "Workflow",
            "Alias",
            "Action",
            "Advice",
            "AndroidComponent",
            "AndroidAnnotation",
            "AndroidHandlerMessage",
            "AndroidIntentAction",
            "AndroidManifest",
            "AndroidNavRoute",
            "AndroidResource",
            "ApiEndpoint",
            "ApplicationEvent",
            "AspNetAnalysisState",
            "Aspect",
            "AsyncBoundary",
            "AuthenticationScheme",
            "Authority",
            "AuthorizationPolicy",
            "BuildConfiguration",
            "BuildDescriptor",
            "CacheOperation",
            "CacheRegion",
            "CallSite",
            "Class",
            "CobolCicsCommand",
            "CobolCopybook",
            "CobolDataItem",
            "CobolFile",
            "CobolParagraph",
            "CobolProgram",
            "CobolSection",
            "CobolSqlStatement",
            "Constant",
            "ConfigurationKey",
            "Controller",
            "DataRepository",
            "Database",
            "DatabaseColumn",
            "DatabaseTable",
            "Dependency",
            "Directory",
            "Document",
            "Enum",
            "ErrorPage",
            "Event",
            "Field",
            "File",
            "Filter",
            "FilterMapping",
            "FrameworkInstance",
            "Function",
            "FunctionType",
            "GradleDependency",
            "GradleModule",
            "GrpcEndpoint",
            "GrpcService",
            "GraphWriteReceipt",
            "HttpEndpoint",
            "HttpHandler",
            "HttpModule",
            "Interface",
            "InfraNode",
            "JSPView",
            "JpaEntity",
            "Jp1Unit",
            "JspExpression",
            "JspTag",
            "Layout",
            "LifecycleEvent",
            "Listener",
            "Message",
            "MessageDestination",
            "MessageEndpoint",
            "Middleware",
            "Model",
            "MyBatisArtifact",
            "MyBatisCache",
            "MyBatisConfig",
            "MyBatisDynamicNode",
            "MyBatisExtension",
            "MyBatisInclude",
            "MyBatisJavaProperty",
            "MyBatisMapper",
            "MyBatisMapperMethod",
            "MyBatisModule",
            "MyBatisParameter",
            "MyBatisResultMap",
            "MyBatisResultMapping",
            "MyBatisSpringBridge",
            "MyBatisSqlFragment",
            "MyBatisSqlJoin",
            "MyBatisSqlParameter",
            "MyBatisSqlProvider",
            "MyBatisSqlStatement",
            "MyBatisStatement",
            "MyBatisXmlDocument",
            "Namespace",
            "Navigator",
            "Package",
            "PageHandler",
            "Paragraph",
            "ParseRun",
            "PartialView",
            "Pointcut",
            "Procedure",
            "ProjectModule",
            "Property",
            "RazorPage",
            "Resource",
            "Result",
            "RouteParam",
            "Route",
            "ScheduledTask",
            "SemanticCoverage",
            "SecurityConstraint",
            "SecurityFilterChain",
            "SecurityRule",
            "Service",
            "BatchProgram",
            "ShellFunction",
            "ShellInvocation",
            "ShellScript",
            "Servlet",
            "ServletJspAnalysisState",
            "ServletJspModule",
            "ServletMapping",
            "SessionState",
            "SqlCursor",
            "SqlDirective",
            "SqlHostVariable",
            "SqlStatement",
            "SpringApplication",
            "SpringBean",
            "SpringConfiguration",
            "SpringModule",
            "StateSlot",
            "Table",
            "Template",
            "TransactionBoundary",
            "Type",
            "UIControl",
            "UnknownFunction",
            "ValidationConstraint",
            "ValidationRule",
            "Variable",
            "View",
            "ViewModel",
            "WebConfiguration",
            "WebDescriptor",
            "WebFormPage",
            "WebTarget",
            "WelcomePage"
          ],
          "missing_required_labels": [],
          "label_schema_status": "available"
        }
      },
      "error": null
    },
    "isError": false
  }
}
```

## 05. `graph_mcp.get_symbol`

- Result: **PASS**
- Status: **SUCCESS_DATA**
- Contract: **PASS**
- Duration: `5.26 ms`
- Endpoint: `http://127.0.0.1:8788/mcp`
- Expected status: `SUCCESS`

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
  "node_id": "main/1@code-tiny/testtool/mcp_batch_report.py",
  "project_id": "cortext",
  "content_mode": "summary",
  "include_raw_fields": false,
  "parser_type": "python"
}
```

### Raw parsed JSON-RPC output

```json
{
  "jsonrpc": "2.0",
  "id": 7,
  "result": {
    "_meta": {
      "contract": "cortex.mcp.tool-result",
      "contractVersion": "1.0",
      "tool": "get_symbol"
    },
    "content": [
      {
        "type": "text",
        "text": "Success. Read structuredContent.data."
      }
    ],
    "structuredContent": {
      "ok": true,
      "data": {
        "db": "cortext",
        "found": true,
        "node": {
          "id": "main/1@code-tiny/testtool/mcp_batch_report.py",
          "labels": [],
          "properties": {
            "name": "main",
            "node_type": "code",
            "qualified_name": "main",
            "kind": "function",
            "file_path": "code-tiny/testtool/mcp_batch_report.py",
            "start_line": 622,
            "end_line": 665,
            "arity": 1,
            "exported": false,
            "visibility": "unknown",
            "is_public_api": false,
            "visibility_source": "",
            "export_evidence": "",
            "signature": "",
            "external": false,
            "builtin": false,
            "react_role": "",
            "middleware_kind": "",
            "project_id": "cortext",
            "project_id_normalized": "cortext",
            "project_name": "cortext",
            "language": "python",
            "repo": "/Users/hieplq1.aip/AI/cortex-harness",
            "build_system": "",
            "updated_at": "2026-08-31T02:55:37.306Z",
            "content_mode": "summary",
            "content": "Performs unknown operation on main (takes 1 parameter)"
          }
        },
        "query_engine": "graph_generic",
        "capability": {
          "requested_parser": "python",
          "canonical_parser": "python",
          "query_engine": "graph_generic",
          "support_level": "partial",
          "support": {
            "symbols": "full",
            "calls": "partial",
            "endpoints": "partial",
            "database": "none"
          },
          "default_relationships_applied": []
        }
      },
      "error": null
    },
    "isError": false
  }
}
```

## 06. `graph_mcp.list_mcp_functions`

- Result: **PASS**
- Status: **SUCCESS_DATA**
- Contract: **PASS**
- Duration: `5.34 ms`
- Endpoint: `http://127.0.0.1:8788/mcp`
- Expected status: `SUCCESS`

### Description

List all available MCP functions/tools with their descriptions, inputs (parameters), and outputs.

### Input schema

```json
{
  "additionalProperties": false,
  "properties": {},
  "type": "object"
}
```

### Output schema advertised by `tools/list`

```json
null
```

### Input executed

```json
{}
```

### Raw parsed JSON-RPC output

```json
{
  "jsonrpc": "2.0",
  "id": 8,
  "result": {
    "_meta": {
      "contract": "cortex.mcp.tool-result",
      "contractVersion": "1.0",
      "tool": "list_mcp_functions"
    },
    "content": [
      {
        "type": "text",
        "text": "Success. Read structuredContent.data."
      }
    ],
    "structuredContent": {
      "ok": true,
      "data": {
        "total_count": 39,
        "parameter_guidelines": {
          "always_call_first": "list_mcp_functions",
          "rules": [
            "Use exact parameter names from tool metadata; avoid inventing aliases.",
            "Send required fields explicitly on every call.",
            "Pass parser_type on every call to select a query profile (see list_parsers for aliases).",
            "When list-like params are accepted, prefer arrays over comma-separated strings.",
            "On error.invalid_parameters, follow required_params + example and retry once."
          ]
        },
        "functions": [
          {
            "name": "search_functions",
            "description": "Search for functions/classes/types by name or qualified name. Returns BOTH node details AND IDs.",
            "use_cases": [
              "Find function by name",
              "Search for class",
              "Get symbol ID for further queries",
              "Fuzzy search across codebase"
            ],
            "inputs": [
              {
                "name": "query",
                "type": "Any",
                "required": true,
                "description": "Search terms separated by | (e.g., 'MyClass|MyFunc'). Case-insensitive substring match."
              },
              {
                "name": "limit",
                "type": "Any",
                "required": false,
                "description": "Max results (default: 50)"
              },
              {
                "name": "top_k",
                "type": "Any",
                "required": false,
                "description": "Alias of limit (max results)."
              },
              {
                "name": "project_id",
                "type": "Any",
                "required": false,
                "description": "Project identifier — selects the graph shard via the project registry. Omit for env-default full search."
              },
              {
                "name": "content_mode",
                "type": "Any",
                "required": false,
                "description": "Output format: 'auto', 'summary', 'comment', 'code', 'name'"
              },
              {
                "name": "include_raw_fields",
                "type": "Any",
                "required": false,
                "description": "Include raw graph-node properties (default: false)"
              },
              {
                "name": "framework",
                "type": "Any",
                "required": false,
                "description": "Optional framework filter: spring, struts, servlet_jsp, mybatis, or flutter."
              },
              {
                "name": "kinds",
                "type": "Any",
                "required": false,
                "description": "Optional framework node-kind filters."
              },
              {
                "name": "parser_type",
                "type": "Any",
                "required": false,
                "description": "Parser alias to scope this call (e.g. 'python', 'spring', 'android'). Omit to fan out across query engines (results deduplicated by node id). See list_parsers."
              },
              {
                "name": "payload",
                "type": "Any",
                "required": false,
                "description": "Optional dict merged over the typed parameters (escape hatch)."
              }
            ],
            "output": "Dict with 'results' (node list), 'ids' (ID list), 'db' (database used)",
            "example": "search_functions(query='handleClick|onClick', limit=10)"
          },
          {
            "name": "search_by_code",
            "description": "Search for code snippets by matching text in function bodies/implementations.",
            "use_cases": [
              "Find functions containing specific code",
              "Search for API usage",
              "Locate string literals",
              "Find regex patterns in code"
            ],
            "inputs": [
              {
                "name": "query",
                "type": "Any",
                "required": true,
                "description": "Code text to search for (case-sensitive)"
              },
              {
                "name": "limit",
                "type": "Any",
                "required": false,
                "description": "Max results (default: 50)"
              },
              {
                "name": "top_k",
                "type": "Any",
                "required": false,
                "description": "Alias of limit (max results)."
              },
              {
                "name": "project_id",
                "type": "Any",
                "required": false,
                "description": "Project identifier — selects the graph shard via the project registry. Omit for env-default full search."
              },
              {
                "name": "content_mode",
                "type": "Any",
                "required": false,
                "description": "Output format: 'auto', 'summary', 'comment', 'code', 'name'"
              },
              {
                "name": "include_raw_fields",
                "type": "Any",
                "required": false,
                "description": "Include raw graph-node properties (default: false)"
              },
              {
                "name": "parser_type",
                "type": "Any",
                "required": false,
                "description": "Parser alias to scope this call (e.g. 'python', 'spring', 'android'). Omit to fan out across query engines (results deduplicated by node id). See list_parsers."
              },
              {
                "name": "payload",
                "type": "Any",
                "required": false,
                "description": "Optional dict merged over the typed parameters (escape hatch)."
              }
            ],
            "output": "Dict with matching nodes containing the code snippet",
            "example": "search_by_code(query='malloc|calloc', limit=20)"
          },
          {
            "name": "get_symbol",
            "description": "Fetch detailed metadata for a specific node by its ID.",
            "use_cases": [
              "Get full details of a function",
              "Inspect symbol properties",
              "View documentation/comments",
              "Get source code"
            ],
            "inputs": [
              {
                "name": "node_id",
                "type": "Any",
                "required": true,
                "description": "Node ID from search results"
              },
              {
                "name": "project_id",
                "type": "Any",
                "required": false,
                "description": "Project identifier — selects the graph shard via the project registry. Omit for env-default full search."
              },
              {
                "name": "content_mode",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "include_raw_fields",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "parser_type",
                "type": "Any",
                "required": false,
                "description": "Parser alias to scope this call (e.g. 'python', 'spring', 'android'). Omit to fan out across query engines (results deduplicated by node id). See list_parsers."
              },
              {
                "name": "payload",
                "type": "Any",
                "required": false,
                "description": "Optional dict merged over the typed parameters (escape hatch)."
              }
            ],
            "output": "Dict with node metadata (name, qualified_name, file_path, signature, code, comment, etc.)",
            "example": "get_symbol(node_id='func_12345')"
          },
          {
            "name": "get_node_details",
            "description": "Batch fetch metadata for multiple nodes by their IDs (more efficient than repeated get_symbol).",
            "use_cases": [
              "Get details for multiple functions at once",
              "Batch lookup after search",
              "Process search results"
            ],
            "inputs": [
              {
                "name": "node_ids",
                "type": "Any",
                "required": true,
                "description": "List of node IDs"
              },
              {
                "name": "project_id",
                "type": "Any",
                "required": false,
                "description": "Project identifier — selects the graph shard via the project registry. Omit for env-default full search."
              },
              {
                "name": "content_mode",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "include_raw_fields",
                "type": "Any",
                "required": false,
                "description": "Include raw graph-node properties (default: false)"
              },
              {
                "name": "parser_type",
                "type": "Any",
                "required": false,
                "description": "Parser alias to scope this call (e.g. 'python', 'spring', 'android'). Omit to fan out across query engines (results deduplicated by node id). See list_parsers."
              },
              {
                "name": "payload",
                "type": "Any",
                "required": false,
                "description": "Optional dict merged over the typed parameters (escape hatch)."
              }
            ],
            "output": "Dict with list of node metadata",
            "example": "get_node_details(node_ids=['func_1', 'func_2', 'func_3'])"
          },
          {
            "name": "query_subgraph",
            "description": "Get call graph context around a function: who calls it (callers) and what it calls (callees).",
            "use_cases": [
              "Understand function dependencies",
              "Find all callers of a function",
              "Trace function call tree",
              "Impact analysis"
            ],
            "inputs": [
              {
                "name": "function_id",
                "type": "Any",
                "required": true,
                "description": "Starting function node ID"
              },
              {
                "name": "direction",
                "type": "Any",
                "required": false,
                "description": "'out' (callees), 'in' (callers), 'both' (default)"
              },
              {
                "name": "max_depth",
                "type": "Any",
                "required": false,
                "description": "Graph traversal depth (default: 2)"
              },
              {
                "name": "include_possible",
                "type": "Any",
                "required": false,
                "description": "Include POSSIBLE_CALLS edges."
              },
              {
                "name": "include_fp",
                "type": "Any",
                "required": false,
                "description": "Include CALLS_FUNCTION_POINTER edges."
              },
              {
                "name": "rel_types",
                "type": "Any",
                "required": false,
                "description": "Relationship types to traverse (default: CALLS)."
              },
              {
                "name": "relationship_types",
                "type": "Any",
                "required": false,
                "description": "Filter by rel types (default: CALLS)"
              },
              {
                "name": "query_profile",
                "type": "Any",
                "required": false,
                "description": "C/C++/Pro*C only. Evidence view: 'strict' (accepted direct semantic CALLS only) or 'conservative' (unions POSSIBLE_CALLS/CALLS_FUNCTION_POINTER without relabeling). Results carry semantic_coverage; an empty traversal over an incomplete frontier returns outcome='incomplete'."
              },
              {
                "name": "parser_type",
                "type": "Any",
                "required": false,
                "description": "Parser alias to scope this call (e.g. 'python', 'spring', 'android'). Omit to fan out across query engines (results deduplicated by node id). See list_parsers."
              },
              {
                "name": "project_id",
                "type": "Any",
                "required": false,
                "description": "Project identifier — selects the graph shard via the project registry. Omit for env-default full search."
              },
              {
                "name": "content_mode",
                "type": "Any",
                "required": false,
                "description": "Output format: 'auto', 'summary', 'comment', 'code', 'name'"
              },
              {
                "name": "include_raw_fields",
                "type": "Any",
                "required": false,
                "description": "Include raw graph-node properties (default: false)"
              },
              {
                "name": "payload",
                "type": "Any",
                "required": false,
                "description": "Optional dict merged over the typed parameters (escape hatch)."
              }
            ],
            "output": "Dict with 'nodes' and 'edges' forming the subgraph",
            "example": "query_subgraph(function_id='func_main', max_depth=3, direction='out')"
          },
          {
            "name": "find_paths",
            "description": "Find all call paths between two specific functions.",
            "use_cases": [
              "Trace how function A reaches function B",
              "Find execution paths",
              "Understand call chains",
              "Debug control flow"
            ],
            "inputs": [
              {
                "name": "start_function_id",
                "type": "Any",
                "required": true,
                "description": "Starting function ID"
              },
              {
                "name": "end_function_id",
                "type": "Any",
                "required": true,
                "description": "Target function ID"
              },
              {
                "name": "max_depth",
                "type": "Any",
                "required": false,
                "description": "Max path length (default: 5)"
              },
              {
                "name": "include_possible",
                "type": "Any",
                "required": false,
                "description": "Include POSSIBLE_CALLS edges."
              },
              {
                "name": "include_fp",
                "type": "Any",
                "required": false,
                "description": "Include CALLS_FUNCTION_POINTER edges."
              },
              {
                "name": "parser_type",
                "type": "Any",
                "required": false,
                "description": "Parser alias to scope this call (e.g. 'python', 'spring', 'android'). Omit to fan out across query engines (results deduplicated by node id). See list_parsers."
              },
              {
                "name": "project_id",
                "type": "Any",
                "required": false,
                "description": "Project identifier — selects the graph shard via the project registry. Omit for env-default full search."
              },
              {
                "name": "rel_types",
                "type": "Any",
                "required": false,
                "description": "Relationship types to traverse (default: CALLS)."
              },
              {
                "name": "relationship_types",
                "type": "Any",
                "required": false,
                "description": "Relationship types to traverse (alias of rel_types)."
              },
              {
                "name": "content_mode",
                "type": "Any",
                "required": false,
                "description": "Output format: 'auto', 'summary', 'comment', 'code', 'name'"
              },
              {
                "name": "include_raw_fields",
                "type": "Any",
                "required": false,
                "description": "Include raw graph-node properties (default: false)"
              },
              {
                "name": "payload",
                "type": "Any",
                "required": false,
                "description": "Optional dict merged over the typed parameters (escape hatch)."
              }
            ],
            "output": "Dict with list of paths, each path containing nodes and edges",
            "example": "find_paths(start_function_id='main', end_function_id='malloc')"
          },
          {
            "name": "find_path_between_module",
            "description": "Find call paths between modules/files (by file path pattern). Supports bidirectional search.",
            "use_cases": [
              "Find how module A uses module B",
              "Trace cross-module dependencies",
              "Architectural analysis",
              "Module coupling analysis"
            ],
            "inputs": [
              {
                "name": "source_modules",
                "type": "Any",
                "required": true,
                "description": "Source file path patterns (e.g., ['sample_module', 'SampleModule'])"
              },
              {
                "name": "target_modules",
                "type": "Any",
                "required": true,
                "description": "Target file path patterns"
              },
              {
                "name": "source_module",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "target_module",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "max_depth",
                "type": "Any",
                "required": false,
                "description": "Max path length (default: 6)"
              },
              {
                "name": "direction",
                "type": "Any",
                "required": false,
                "description": "'out', 'in', 'both' (default: 'out', auto-retries with 'both')"
              },
              {
                "name": "include_possible",
                "type": "Any",
                "required": false,
                "description": "Include POSSIBLE_CALLS edges"
              },
              {
                "name": "include_fp",
                "type": "Any",
                "required": false,
                "description": "Include function pointer calls"
              },
              {
                "name": "parser_type",
                "type": "Any",
                "required": false,
                "description": "Parser alias to scope this call (e.g. 'python', 'spring', 'android'). Omit to fan out across query engines (results deduplicated by node id). See list_parsers."
              },
              {
                "name": "project_id",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "content_mode",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "include_raw_fields",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "payload",
                "type": "Any",
                "required": false,
                "description": "Optional dict merged over the typed parameters (escape hatch)."
              }
            ],
            "output": "Dict with paths between modules, graph visualization data",
            "example": "find_path_between_module(source_modules=['sample_module'], target_modules=['sample_target'], direction='both')"
          },
          {
            "name": "listup_symbols_matching_file_path",
            "description": "List all symbols (functions/classes/types) in files matching path pattern. Supports filtering by node type.",
            "use_cases": [
              "List all functions in a file",
              "Get classes in a module",
              "Inventory symbols in directory",
              "Extract API surface"
            ],
            "inputs": [
              {
                "name": "modules",
                "type": "Any",
                "required": true,
                "description": "File path patterns to match"
              },
              {
                "name": "module",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "node_types",
                "type": "Any",
                "required": false,
                "description": "Filter by types: ['Function'], ['Class', 'Type'], etc. (default: all symbols)"
              },
              {
                "name": "max_depth",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "project_id",
                "type": "Any",
                "required": false,
                "description": "Project identifier — selects the graph shard via the project registry. Omit for env-default full search."
              },
              {
                "name": "content_mode",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "include_raw_fields",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "parser_type",
                "type": "Any",
                "required": false,
                "description": "Parser alias to scope this call (e.g. 'python', 'spring', 'android'). Omit to fan out across query engines (results deduplicated by node id). See list_parsers."
              },
              {
                "name": "payload",
                "type": "Any",
                "required": false,
                "description": "Optional dict merged over the typed parameters (escape hatch)."
              }
            ],
            "output": "Dict with list of symbols matching path and type filters",
            "example": "listup_symbols_matching_file_path(modules=['sample_module.c'], node_types=['Function'])"
          },
          {
            "name": "listup_class_matching_path",
            "description": "List all functions/methods declared in classes matching name pattern.",
            "use_cases": [
              "Get all methods of a class",
              "Class API inventory",
              "Find member functions"
            ],
            "inputs": [
              {
                "name": "class_names",
                "type": "Any",
                "required": true,
                "description": "Class name patterns"
              },
              {
                "name": "class_name",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "project_id",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "content_mode",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "include_raw_fields",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "parser_type",
                "type": "Any",
                "required": false,
                "description": "Parser alias to scope this call (e.g. 'python', 'spring', 'android'). Omit to fan out across query engines (results deduplicated by node id). See list_parsers."
              },
              {
                "name": "payload",
                "type": "Any",
                "required": false,
                "description": "Optional dict merged over the typed parameters (escape hatch)."
              }
            ],
            "output": "Dict with classes and their functions",
            "example": "listup_class_matching_path(class_names=['SampleClass', 'SampleHandler'])"
          },
          {
            "name": "list_up_entrypoint",
            "description": "Find entry point functions: functions in target modules that are called from OUTSIDE those modules.",
            "use_cases": [
              "Find public API of a module",
              "Identify module boundaries",
              "Locate exported functions",
              "API surface analysis"
            ],
            "inputs": [
              {
                "name": "modules",
                "type": "Any",
                "required": true,
                "description": "Module/file path patterns"
              },
              {
                "name": "module",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "limit",
                "type": "Any",
                "required": false,
                "description": "Max results (default: 200)"
              },
              {
                "name": "top_k",
                "type": "Any",
                "required": false,
                "description": "Alias of limit (max results)."
              },
              {
                "name": "project_id",
                "type": "Any",
                "required": false,
                "description": "Project identifier — selects the graph shard via the project registry. Omit for env-default full search."
              },
              {
                "name": "content_mode",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "include_raw_fields",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "parser_type",
                "type": "Any",
                "required": false,
                "description": "Parser alias to scope this call (e.g. 'python', 'spring', 'android'). Omit to fan out across query engines (results deduplicated by node id). See list_parsers."
              },
              {
                "name": "payload",
                "type": "Any",
                "required": false,
                "description": "Optional dict merged over the typed parameters (escape hatch)."
              }
            ],
            "output": "Dict with entry point functions",
            "example": "list_up_entrypoint(modules=['src/api/'])"
          },
          {
            "name": "trace_flow",
            "description": "Advanced flow tracing with custom relationship types (CALLS, POSSIBLE_CALLS, function pointers, etc.).",
            "use_cases": [
              "Custom relationship traversal",
              "Trace with specific edge types",
              "Advanced graph queries"
            ],
            "inputs": [
              {
                "name": "start_id",
                "type": "Any",
                "required": true,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "end_id",
                "type": "Any",
                "required": true,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "parser_type",
                "type": "Any",
                "required": false,
                "description": "Parser alias to scope this call (e.g. 'python', 'spring', 'android'). Omit to fan out across query engines (results deduplicated by node id). See list_parsers."
              },
              {
                "name": "max_depth",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "direction",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "rel_types",
                "type": "Any",
                "required": false,
                "description": "Custom relationship types to traverse"
              },
              {
                "name": "relationship_types",
                "type": "Any",
                "required": false,
                "description": "Relationship types to traverse (alias of rel_types)."
              },
              {
                "name": "limit",
                "type": "Any",
                "required": false,
                "description": "Max results."
              },
              {
                "name": "top_k",
                "type": "Any",
                "required": false,
                "description": "Alias of limit (max results)."
              },
              {
                "name": "debug",
                "type": "Any",
                "required": false,
                "description": "Include debugging details in the response."
              },
              {
                "name": "project_id",
                "type": "Any",
                "required": false,
                "description": "Project identifier — selects the graph shard via the project registry. Omit for env-default full search."
              },
              {
                "name": "content_mode",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "include_raw_fields",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "payload",
                "type": "Any",
                "required": false,
                "description": "Optional dict merged over the typed parameters (escape hatch)."
              }
            ],
            "output": "Dict with traced paths using specified relationships",
            "example": "trace_flow(start_id='sample_function', end_id='target_function', rel_types=['CALLS', 'POSSIBLE_CALLS'])"
          },
          {
            "name": "trace_flow_between_module",
            "description": "Advanced module-to-module flow tracing with custom relationships.",
            "use_cases": [
              "Custom module dependency analysis",
              "Trace specific edge types between modules"
            ],
            "inputs": [
              {
                "name": "source_modules",
                "type": "Any",
                "required": true,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "target_modules",
                "type": "Any",
                "required": true,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "source_module",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "target_module",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "parser_type",
                "type": "Any",
                "required": false,
                "description": "Parser alias to scope this call (e.g. 'python', 'spring', 'android'). Omit to fan out across query engines (results deduplicated by node id). See list_parsers."
              },
              {
                "name": "max_depth",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "direction",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "rel_types",
                "type": "Any",
                "required": false,
                "description": "Relationship types to traverse (default: CALLS)."
              },
              {
                "name": "relationship_types",
                "type": "Any",
                "required": false,
                "description": "Relationship types to traverse (alias of rel_types)."
              },
              {
                "name": "limit",
                "type": "Any",
                "required": false,
                "description": "Max results."
              },
              {
                "name": "top_k",
                "type": "Any",
                "required": false,
                "description": "Alias of limit (max results)."
              },
              {
                "name": "project_id",
                "type": "Any",
                "required": false,
                "description": "Project identifier — selects the graph shard via the project registry. Omit for env-default full search."
              },
              {
                "name": "content_mode",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "include_raw_fields",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "payload",
                "type": "Any",
                "required": false,
                "description": "Optional dict merged over the typed parameters (escape hatch)."
              }
            ],
            "output": "Dict with module flow paths",
            "example": "trace_flow_between_module(source_modules=['sample_module'], target_modules=['target_module'], rel_types=['CALLS'])"
          },
          {
            "name": "compute_scc",
            "description": "Compute strongly connected components (SCC) from a directed dependency graph.",
            "use_cases": [
              "Detect dependency cycles before migration",
              "Map nodes to SCC groups",
              "Prepare condensation before topological planning"
            ],
            "inputs": [
              {
                "name": "nodes",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "edges",
                "type": "Any",
                "required": false,
                "description": "Edge records containing source/target-style fields"
              },
              {
                "name": "parser_type",
                "type": "Any",
                "required": false,
                "description": "Parser profile to route the query (see list_parsers)."
              },
              {
                "name": "edge_semantics",
                "type": "Any",
                "required": false,
                "description": "depends_on (default) or calls"
              },
              {
                "name": "include_singletons",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              }
            ],
            "output": "Dict with components[{scc_id,nodes,size,is_cycle}], node_to_scc, and cycle_summary{total_scc,reported_scc,cyclic_scc,self_loops}",
            "example": "compute_scc(nodes=['A','B'], edges=[{'from':'A','to':'B'}])"
          },
          {
            "name": "topological_sort",
            "description": "Topologically sort dependency graph and return linear order and/or parallel waves. Supports SCC auto-condensation when cycles exist.",
            "use_cases": [
              "Get migration execution order",
              "Split work into parallel waves",
              "Handle cyclic graphs with SCC fallback"
            ],
            "inputs": [
              {
                "name": "nodes",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "edges",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "parser_type",
                "type": "Any",
                "required": false,
                "description": "Parser profile to route the query (see list_parsers)."
              },
              {
                "name": "edge_semantics",
                "type": "Any",
                "required": false,
                "description": "depends_on (default) or calls"
              },
              {
                "name": "output_mode",
                "type": "Any",
                "required": false,
                "description": "linear | waves | both (default)"
              },
              {
                "name": "on_cycle",
                "type": "Any",
                "required": false,
                "description": "auto_condense_scc (default) or error"
              }
            ],
            "output": "Dict with is_dag, unresolved_nodes, unresolved_cycles, diagnostics, optional condensed{node_to_scc,...}, and linear_order/waves depending on output_mode",
            "example": "topological_sort(nodes=['A','B'], edges=[{'from':'A','to':'B'}], output_mode='both')"
          },
          {
            "name": "plan_dependency_order",
            "description": "Plan module-level dependency order from CALLS edges.",
            "use_cases": [
              "Module migration sequencing",
              "Wave-based execution planning by module",
              "Cycle diagnostics at module level"
            ],
            "inputs": [
              {
                "name": "modules",
                "type": "Any",
                "required": true,
                "description": "Module tokens matched against file_path"
              },
              {
                "name": "parser_type",
                "type": "Any",
                "required": false,
                "description": "Parser profile to route the query (see list_parsers)."
              },
              {
                "name": "project_id",
                "type": "Any",
                "required": false,
                "description": "Project identifier — selects the graph shard via the project registry. Omit for env-default full search."
              },
              {
                "name": "edge_semantics",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "on_cycle",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              }
            ],
            "output": "Dict with waves[{wave,modules}], module_order, depends_on_map, module_dependencies, unresolved_cycles, unresolved_nodes, node_to_scc",
            "example": "plan_dependency_order(modules=['auth','payment'])"
          },
          {
            "name": "plan_file_dependency_order",
            "description": "Plan file-level dependency order per module from CALLS edges.",
            "use_cases": [
              "Detailed file migration order",
              "Parallel file waves per module",
              "Cross-module dependency visibility"
            ],
            "inputs": [
              {
                "name": "modules",
                "type": "Any",
                "required": true,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "parser_type",
                "type": "Any",
                "required": false,
                "description": "Parser profile to route the query (see list_parsers)."
              },
              {
                "name": "project_id",
                "type": "Any",
                "required": false,
                "description": "Project identifier — selects the graph shard via the project registry. Omit for env-default full search."
              },
              {
                "name": "edge_semantics",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "on_cycle",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "include_cross_module",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "max_files_per_module",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              }
            ],
            "output": "Dict with cross_module_edges and modules[]. Each module includes waves[{wave,files}], file_order, depends_on_map, unresolved_cycles, node_to_scc, file_dependencies",
            "example": "plan_file_dependency_order(modules=['auth','payment'], include_cross_module=true)"
          },
          {
            "name": "plan_function_dependency_order",
            "description": "Plan function-level dependency order per module from CALLS edges.",
            "use_cases": [
              "Function migration sequencing with metadata",
              "Wave execution planning by function",
              "Cycle/SCC diagnostics at function granularity"
            ],
            "inputs": [
              {
                "name": "modules",
                "type": "Any",
                "required": true,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "parser_type",
                "type": "Any",
                "required": false,
                "description": "Parser profile to route the query (see list_parsers)."
              },
              {
                "name": "project_id",
                "type": "Any",
                "required": false,
                "description": "Project identifier — selects the graph shard via the project registry. Omit for env-default full search."
              },
              {
                "name": "edge_semantics",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "on_cycle",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "include_cross_module",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "include_lambdas",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "max_functions_per_module",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              }
            ],
            "output": "Dict with cross_module_edges and modules[]. For each module: waves[{wave,function_ids,functions}], function_order_ids, function_order (with id,name,qualified_name,file_path), depends_on_map, unresolved_cycles, unresolved_nodes, node_to_scc, function_dependencies",
            "example": "plan_function_dependency_order(modules=['auth','payment'], include_cross_module=true)"
          },
          {
            "name": "find_screen_workflows",
            "description": "Discover ranked screen-only NAVIGATE workflows for a React/TS project. Input either a pair (node_a + node_b) or a single node_a with a direction. Paths contain only nodes with react_role='screen', are simple (no repeats), and are ranked by aggregate edge confidence DESC, total call_depth ASC, length ASC.",
            "use_cases": [
              "List all business flows between two screens (e.g. RewardHome -> GoldTransfer)",
              "List all workflows that start from, end at, or touch a single screen",
              "Discover nested-navigator paths where an outer screen reaches an inner screen via components"
            ],
            "inputs": [
              {
                "name": "project_id",
                "type": "Any",
                "required": false,
                "description": "Project identifier — selects the graph shard via the project registry. Omit for env-default full search."
              },
              {
                "name": "node_a",
                "type": "Any",
                "required": true,
                "description": "Screen name (case-insensitive) or symbol_id. Source in pair mode; anchor in single mode."
              },
              {
                "name": "node_b",
                "type": "Any",
                "required": false,
                "description": "Second screen. When provided, pair mode is used; otherwise single mode."
              },
              {
                "name": "direction",
                "type": "Any",
                "required": false,
                "description": "single-mode only: 'inbound' | 'outbound' | 'bidirectional' (default)"
              },
              {
                "name": "max_hops",
                "type": "Any",
                "required": false,
                "description": "Max NAVIGATE hops on a path (default 8, capped at 20)"
              },
              {
                "name": "max_paths",
                "type": "Any",
                "required": false,
                "description": "Max workflows returned after dedup/rank (default 100, cap 1000)"
              },
              {
                "name": "include_entry_function",
                "type": "Any",
                "required": false,
                "description": "Reserved: attach entry function metadata to each workflow"
              },
              {
                "name": "include_api_calls",
                "type": "Any",
                "required": false,
                "description": "Reserved: attach API calls reachable from each workflow"
              },
              {
                "name": "parser_type",
                "type": "Any",
                "required": false,
                "description": "Parser profile to route the query (see list_parsers)."
              },
              {
                "name": "payload",
                "type": "Any",
                "required": false,
                "description": "Optional dict merged over the typed parameters (escape hatch)."
              }
            ],
            "output": "Dict with keys: mode, direction, project_id, resolved (name->candidates), workflows (ranked list with path, edges, length, aggregate_confidence, total_call_depth, direction), uncertainties, truncated",
            "example": "find_screen_workflows(project_id='my-app', node_a='RewardHome', node_b='GoldTransfer')"
          },
          {
            "name": "explore_graph",
            "description": "Intent-aware, multi-strategy Graph Explorer search. Accepts natural language, paragraphs, or vague descriptions. Combines semantic vector search, BM25 keyword search, and call-graph expansion with automatic query understanding (entity/domain signal extraction). Returns explainable, ranked results with per-node WHY reasons. Supports English and Vietnamese input.",
            "use_cases": [
              "Search by describing a bug in plain language",
              "Find all nodes related to a domain concept (auth, payment, order…)",
              "Paste a paragraph of requirements and find relevant code",
              "Discover entry points + call-graph neighbors in one query",
              "Multi-language natural language search (EN + VI)"
            ],
            "inputs": [
              {
                "name": "query",
                "type": "Any",
                "required": true,
                "description": "Natural language text: keyword, sentence, or multi-line paragraph"
              },
              {
                "name": "mode",
                "type": "Any",
                "required": false,
                "description": "'semantic' | 'hybrid' (default) | 'graph_expanded'"
              },
              {
                "name": "top_k",
                "type": "Any",
                "required": false,
                "description": "Max matched nodes to return (default: 10)"
              },
              {
                "name": "collection",
                "type": "Any",
                "required": false,
                "description": "Qdrant collection name or project scope prefix"
              },
              {
                "name": "debug",
                "type": "Any",
                "required": false,
                "description": "Include per-signal score breakdown in results"
              },
              {
                "name": "parser_type",
                "type": "Any",
                "required": false,
                "description": "Parser capability profile used for labels, properties, and graph relationships. Use 'cplus' or any of its aliases (proc, pro*c, pro-c) for C/C++/Pro*C sources."
              },
              {
                "name": "project_id",
                "type": "Any",
                "required": false,
                "description": "Project identifier — selects the graph shard via the project registry. Omit for env-default full search."
              }
            ],
            "output": "Dict with: matched_nodes (list of nodes with reason/score/signals), entry_points (subset of high-importance nodes), related_paths (graph-expanded neighbors), explanation (human-readable summary), confidence (0.0–1.0), query_analysis (extracted intent/entities/domain_signals), mode, and retrieval (provider/database/degraded-mode diagnostics)",
            "example": "explore_graph(query='function xử lý thanh toán bị lỗi khi user chưa login', mode='graph_expanded', top_k=15)"
          },
          {
            "name": "semantic_search",
            "description": "Semantic code search using Qdrant vector embeddings. Optionally expands vector hits through the configured graph database.",
            "use_cases": [
              "Find semantically similar code",
              "Search by natural language",
              "Locate similar implementations",
              "Documentation search"
            ],
            "inputs": [
              {
                "name": "query",
                "type": "Any",
                "required": true,
                "description": "Natural language query or code snippet"
              },
              {
                "name": "mode",
                "type": "Any",
                "required": false,
                "description": "'code', 'comment', 'hybrid'"
              },
              {
                "name": "top_k",
                "type": "Any",
                "required": false,
                "description": "Number of results (default: 10)"
              },
              {
                "name": "model_path",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "qdrant_url",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "collection",
                "type": "Any",
                "required": false,
                "description": "Qdrant collection name or project scope prefix"
              },
              {
                "name": "collection_comment",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "collection_code",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "content_mode",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "include_raw_fields",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "show_snippet",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "show_comment",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "expand_graph",
                "type": "Any",
                "required": false,
                "description": "When true, expand Qdrant seed hits through the configured graph database"
              },
              {
                "name": "graph_depth",
                "type": "Any",
                "required": false,
                "description": "Graph traversal depth for expanded neighbors (default: 2, max: 5)"
              },
              {
                "name": "graph_direction",
                "type": "Any",
                "required": false,
                "description": "'out', 'in', or 'both' (default: both)"
              },
              {
                "name": "graph_rel_types",
                "type": "Any",
                "required": false,
                "description": "Comma-separated relationship types for expansion"
              },
              {
                "name": "graph_limit",
                "type": "Any",
                "required": false,
                "description": "Maximum graph-expanded nodes to return (default: 50)"
              },
              {
                "name": "project_id",
                "type": "Any",
                "required": false,
                "description": "Case-insensitive Qdrant payload filter. Omit to search all projects in the selected or discovered collections."
              },
              {
                "name": "payload",
                "type": "Any",
                "required": false,
                "description": "Optional dict merged over the typed parameters (escape hatch)."
              }
            ],
            "output": "Dict with semantic results and optional graph_expansion context containing exact hop distance, seed_id/seed_ids provenance, expanded node metadata, and edges",
            "example": "semantic_search(query='allocate memory safely', top_k=5, collection='MYPROJECT', expand_graph=true, graph_depth=2)"
          },
          {
            "name": "get_ipc_message",
            "description": "Query IPC/message records by sender/receiver (graph Message nodes first, JSON fallback).",
            "use_cases": [
              "Find IPC between components",
              "Trace message passing",
              "Android Intent flows",
              "Event communication"
            ],
            "inputs": [
              {
                "name": "sender",
                "type": "Any",
                "required": false,
                "description": "Sender component pattern. If only sender provided, returns list of receivers."
              },
              {
                "name": "receiver",
                "type": "Any",
                "required": false,
                "description": "Receiver component pattern. If only receiver provided, returns list of senders."
              },
              {
                "name": "senders",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "receivers",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "project_id",
                "type": "Any",
                "required": false,
                "description": "Project identifier — selects the graph shard via the project registry. Omit for env-default full search."
              },
              {
                "name": "payload",
                "type": "Any",
                "required": false,
                "description": "Optional dict merged over the typed parameters (escape hatch)."
              }
            ],
            "output": "Dict with IPC message details or sender/receiver lists",
            "example": "get_ipc_message(sender='Activity', receiver='Service')"
          },
          {
            "name": "list_possible_calls",
            "description": "List POSSIBLE_CALLS relationships (function pointer calls, virtual calls, callback registrations).",
            "use_cases": [
              "Find indirect calls",
              "Trace callback chains",
              "Virtual function analysis",
              "Function pointer usage"
            ],
            "inputs": [
              {
                "name": "limit",
                "type": "Any",
                "required": false,
                "description": "Max results (default: 100)"
              },
              {
                "name": "top_k",
                "type": "Any",
                "required": false,
                "description": "Alias of limit (max results)."
              },
              {
                "name": "project_id",
                "type": "Any",
                "required": false,
                "description": "Project identifier — selects the graph shard via the project registry. Omit for env-default full search."
              },
              {
                "name": "content_mode",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "include_raw_fields",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "parser_type",
                "type": "Any",
                "required": false,
                "description": "Parser alias to scope this call (e.g. 'python', 'spring', 'android'). Omit to fan out across query engines (results deduplicated by node id). See list_parsers."
              },
              {
                "name": "payload",
                "type": "Any",
                "required": false,
                "description": "Optional dict merged over the typed parameters (escape hatch)."
              }
            ],
            "output": "Dict with POSSIBLE_CALLS edges",
            "example": "list_possible_calls(limit=50)"
          },
          {
            "name": "annotate_node",
            "description": "Add or update annotations (notes/tags/severity) on a node for documentation/review purposes.",
            "use_cases": [
              "Mark functions for review",
              "Tag security issues",
              "Add documentation notes",
              "Flag technical debt"
            ],
            "inputs": [
              {
                "name": "node_id",
                "type": "Any",
                "required": true,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "note",
                "type": "Any",
                "required": false,
                "description": "Text note"
              },
              {
                "name": "tags",
                "type": "Any",
                "required": false,
                "description": "Comma-separated tags"
              },
              {
                "name": "severity",
                "type": "Any",
                "required": false,
                "description": "Severity level (e.g., 'high', 'medium', 'low')"
              },
              {
                "name": "project_id",
                "type": "Any",
                "required": false,
                "description": "Project identifier — selects the graph shard via the project registry. Omit for env-default full search."
              },
              {
                "name": "content_mode",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "include_raw_fields",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "payload",
                "type": "Any",
                "required": false,
                "description": "Optional dict merged over the typed parameters (escape hatch)."
              }
            ],
            "output": "Dict with updated node",
            "example": "annotate_node(node_id='func_123', note='Buffer overflow risk', severity='high')"
          },
          {
            "name": "list_databases",
            "description": "List available graph databases or graph names from the active provider.",
            "use_cases": [
              "Discover available projects",
              "Switch between databases",
              "Check database names"
            ],
            "inputs": [
              {
                "name": "payload",
                "type": "Any",
                "required": false,
                "description": "Optional dict merged over the typed parameters (escape hatch)."
              }
            ],
            "output": "Dict with list of database names",
            "example": "list_databases()"
          },
          {
            "name": "list_qdrant_collections",
            "description": "List all Qdrant vector collections for semantic search.",
            "use_cases": [
              "Discover available collections",
              "Check embeddings status"
            ],
            "inputs": [
              {
                "name": "qdrant_url",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "include_vectors",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "payload",
                "type": "Any",
                "required": false,
                "description": "Optional dict merged over the typed parameters (escape hatch)."
              }
            ],
            "output": "Dict with Qdrant collections and metadata",
            "example": "list_qdrant_collections()"
          },
          {
            "name": "list_parsers",
            "description": "List canonical parser profiles, aliases, query engines, and dimensional support. Defaults to a compact summary; request full only for diagnostics.",
            "use_cases": [
              "Check supported languages",
              "Discover parser options",
              "Inspect support boundaries"
            ],
            "inputs": [
              {
                "name": "detail_level",
                "type": "Any",
                "required": false,
                "description": "summary (default) or full diagnostic capability details"
              }
            ],
            "output": "Parser aliases, canonical capabilities, active capability, and default query engine",
            "example": "list_parsers(detail_level='summary')"
          },
          {
            "name": "inspect_parser_capabilities",
            "description": "Compare advertised parser support with schema observed in the active graph provider.",
            "use_cases": [
              "Check whether parser graph facts are actually ingested",
              "Diagnose capability_unavailable before running a query",
              "Detect provider schema drift"
            ],
            "inputs": [
              {
                "name": "parser_type",
                "type": "Any",
                "required": false,
                "description": "Parser profile; uses the active profile when omitted"
              },
              {
                "name": "project_id",
                "type": "Any",
                "required": false,
                "description": "Project identifier — selects the graph shard via the project registry. Omit for env-default full search."
              }
            ],
            "output": "Advertised/effective support, schema fingerprint, missing evidence, and recommendation",
            "example": "inspect_parser_capabilities(parser_type='python', db='my_project')"
          },
          {
            "name": "list_mcp_functions",
            "description": "List all available MCP tools with descriptions, parameters, and use cases. Call this FIRST to discover what tools are available before making other calls.",
            "use_cases": [
              "Tool discovery",
              "Understand available capabilities",
              "Get parameter reference before calling a tool"
            ],
            "inputs": [],
            "output": "Dict with total_count and functions list (name, description, use_cases, inputs, output, example)",
            "example": "list_mcp_functions()"
          },
          {
            "name": "reconstruct_flow",
            "description": "Reconstruct POSSIBLE execution flows from candidate graph paths. Produces a grounded, traceable flow representation mapping to real node_ids consumable by AI agents for reasoning, explanation, and impact analysis.",
            "use_cases": [
              "Reconstruct backend call flows from path data",
              "Reconstruct frontend event → handler → API → navigation flows",
              "Build hybrid UI-to-backend flows",
              "Explain how a trigger reaches a target function",
              "Impact analysis with ordered execution context"
            ],
            "inputs": [
              {
                "name": "entry_context_json",
                "type": "Any",
                "required": true,
                "description": "JSON string: {\"type\": \"backend|frontend|hybrid\", \"entry_point\": str, \"entry_node_id\": str, \"screen\": str|null, \"trigger\": str|null}"
              },
              {
                "name": "paths_json",
                "type": "Any",
                "required": true,
                "description": "JSON string: array of path objects. Each path: {\"path_id\": str, \"nodes\": [{node_id, name, mapped_type, location}], \"edges\": [{from, to, type}]}"
              }
            ],
            "output": "{\"flows\": [...], \"uncertainties\": [...]}. Each flow: flow_id, title, type, confidence (high/medium/low), entry_node_id, paths_used, discarded_paths, steps[]. Each step: step_id, node_id, name, mapped_type, path_ids, relation (direct_edge/same_path_sequence/inferred_bridge/shared_state/unknown), reason_text, uncertainty (low/medium/high).",
            "example": "reconstruct_flow(entry_context_json='{\"type\":\"backend\",\"entry_point\":\"main\",\"entry_node_id\":\"n1\",\"screen\":null,\"trigger\":null}', paths_json='[{\"path_id\":\"path_1\",\"nodes\":[{\"node_id\":\"n1\",\"name\":\"main\",\"mapped_type\":\"function\",\"location\":{\"file\":\"main.c\",\"line\":10}}],\"edges\":[]}]')"
          },
          {
            "name": "find_callers_of_endpoint",
            "description": "Return frontend functions/screens that call a specific backend API endpoint via Function -> CALLS_API -> ApiCall -> MATCHES -> ApiEndpoint.",
            "use_cases": [
              "Find all screens calling a backend endpoint",
              "Trace endpoint usage from frontend",
              "Impact analysis before backend API changes"
            ],
            "inputs": [
              {
                "name": "endpoint_path",
                "type": "Any",
                "required": true,
                "description": "Backend endpoint path (e.g. '/api/users/:id')"
              },
              {
                "name": "http_method",
                "type": "Any",
                "required": false,
                "description": "HTTP method filter (GET/POST/PUT/DELETE/ALL)"
              },
              {
                "name": "be_project_id",
                "type": "Any",
                "required": false,
                "description": "Case-insensitive backend project_id filter"
              },
              {
                "name": "fe_project_id",
                "type": "Any",
                "required": false,
                "description": "Case-insensitive frontend project_id filter"
              },
              {
                "name": "project_id",
                "type": "Any",
                "required": false,
                "description": "Project identifier — selects the graph shard via the project registry. Omit for env-default full search."
              },
              {
                "name": "parser_type",
                "type": "Any",
                "required": false,
                "description": "Parser capability profile for relationship validation. Use 'cplus' or any of its aliases (proc, pro*c, pro-c) for C/C++/Pro*C sources."
              }
            ],
            "output": "Dict with endpoint_path, callers (frontend symbols), and total",
            "example": "find_callers_of_endpoint(endpoint_path='/api/users/:id', http_method='GET')"
          },
          {
            "name": "get_api_call_chain",
            "description": "Return fullstack call chain from frontend component or endpoint to backend layers (ApiEndpoint, Controller, Service, Repository, Database).",
            "use_cases": [
              "Trace end-to-end FE -> BE -> DB execution chain",
              "Understand backend dependencies of a screen",
              "Audit data access path for an API"
            ],
            "inputs": [
              {
                "name": "component_name",
                "type": "Any",
                "required": false,
                "description": "Frontend component/screen name"
              },
              {
                "name": "endpoint_path",
                "type": "Any",
                "required": false,
                "description": "Backend endpoint path (used when component_name is not provided)"
              },
              {
                "name": "fe_project_id",
                "type": "Any",
                "required": false,
                "description": "Case-insensitive frontend project_id filter"
              },
              {
                "name": "be_project_id",
                "type": "Any",
                "required": false,
                "description": "Case-insensitive backend project_id filter"
              },
              {
                "name": "project_id",
                "type": "Any",
                "required": false,
                "description": "Project identifier — selects the graph shard via the project registry. Omit for env-default full search."
              },
              {
                "name": "max_depth",
                "type": "Any",
                "required": false,
                "description": "Max frontend CALLS hops (default: 5)"
              },
              {
                "name": "parser_type",
                "type": "Any",
                "required": false,
                "description": "Parser capability profile for endpoint-chain traversal defaults. Use 'cplus' or any of its aliases (proc, pro*c, pro-c) for C/C++/Pro*C sources."
              }
            ],
            "output": "Dict with chains (fe/api/be/database segments) and total",
            "example": "get_api_call_chain(component_name='UserProfileScreen', max_depth=5)"
          },
          {
            "name": "analyze_workflow_impact",
            "description": "Analyze change impact for a function/screen at call-graph and workflow levels, including risk scoring and recommendation.",
            "use_cases": [
              "Estimate blast radius before refactoring",
              "Detect workflow-level regression risk",
              "Prioritize test scope by impact severity"
            ],
            "inputs": [
              {
                "name": "function_id",
                "type": "Any",
                "required": true,
                "description": "Function/screen symbol_id to analyze"
              },
              {
                "name": "project_id",
                "type": "Any",
                "required": false,
                "description": "Project identifier — selects the graph shard via the project registry. Omit for env-default full search."
              },
              {
                "name": "direction",
                "type": "Any",
                "required": false,
                "description": "Traversal direction: downstream or upstream"
              },
              {
                "name": "max_depth",
                "type": "Any",
                "required": false,
                "description": "Traversal depth cap (default: 4, max: 4)"
              },
              {
                "name": "parser_type",
                "type": "Any",
                "required": false,
                "description": "Parser capability profile for impact traversal defaults. Use 'cplus' or any of its aliases (proc, pro*c, pro-c) for C/C++/Pro*C sources."
              }
            ],
            "output": "Dict with risk_score, impacted_nodes, and workflow_impact details",
            "example": "analyze_workflow_impact(function_id='func_123', direction='downstream')"
          },
          {
            "name": "find_workflows_containing",
            "description": "Find workflows containing a function directly (HAS_STEP) or indirectly (reachable through CALLS chain).",
            "use_cases": [
              "List workflows affected by a function change",
              "Validate workflow ownership of a code path",
              "Support regression planning by workflow coverage"
            ],
            "inputs": [
              {
                "name": "function_id",
                "type": "Any",
                "required": true,
                "description": "Function symbol_id or file_path anchor"
              },
              {
                "name": "project_id",
                "type": "Any",
                "required": false,
                "description": "Project identifier — selects the graph shard via the project registry. Omit for env-default full search."
              },
              {
                "name": "include_indirect",
                "type": "Any",
                "required": false,
                "description": "Include CALLS-chain derived workflows (default: true)"
              },
              {
                "name": "max_depth",
                "type": "Any",
                "required": false,
                "description": "Indirect traversal depth cap (default: 4, max: 4)"
              },
              {
                "name": "parser_type",
                "type": "Any",
                "required": false,
                "description": "Parser capability profile for workflow traversal defaults. Use 'cplus' or any of its aliases (proc, pro*c, pro-c) for C/C++/Pro*C sources."
              }
            ],
            "output": "Dict with direct_workflows, indirect_workflows, and total",
            "example": "find_workflows_containing(function_id='func_123', include_indirect=True)"
          },
          {
            "name": "get_project_modules",
            "description": "Return canonical project modules, descriptors, and bounded dependency context.",
            "use_cases": [
              "Understand repository module topology",
              "List internal and external dependencies"
            ],
            "inputs": [
              {
                "name": "project_id",
                "type": "Any",
                "required": true,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "module_id",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "module_path",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "include_dependencies",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "offset",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "limit",
                "type": "Any",
                "required": false,
                "description": "Max results."
              },
              {
                "name": "parser_type",
                "type": "Any",
                "required": true,
                "description": "Required for this tool. Pass a parser alias (e.g. 'android', 'spring', 'typescript'). See list_parsers."
              }
            ],
            "output": "Paginated canonical module records with capability diagnostics",
            "example": "get_project_modules(project_id='shop', parser_type='android', limit=50)"
          },
          {
            "name": "get_public_apis",
            "description": "Return strict source-level public/exported APIs owned by project modules.",
            "use_cases": [
              "Inventory module API surfaces",
              "Find exported types and functions"
            ],
            "inputs": [
              {
                "name": "project_id",
                "type": "Any",
                "required": true,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "module_id",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "symbol_kinds",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "language",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "include_inferred",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "offset",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "limit",
                "type": "Any",
                "required": false,
                "description": "Max results."
              },
              {
                "name": "parser_type",
                "type": "Any",
                "required": true,
                "description": "Required for this tool. Pass a parser alias (e.g. 'android', 'spring', 'typescript'). See list_parsers."
              }
            ],
            "output": "Paginated APIs with visibility, evidence, confidence, and module ownership",
            "example": "get_public_apis(project_id='shop', parser_type='kotlin', language='kotlin')"
          },
          {
            "name": "get_endpoints",
            "description": "Return a normalized HTTP, route, page, service, and gRPC endpoint inventory.",
            "use_cases": [
              "Inventory exposed endpoints",
              "Find handlers across frameworks"
            ],
            "inputs": [
              {
                "name": "project_id",
                "type": "Any",
                "required": true,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "module_id",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "protocol",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "framework",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "http_method",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "query",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "offset",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "limit",
                "type": "Any",
                "required": false,
                "description": "Max results."
              },
              {
                "name": "parser_type",
                "type": "Any",
                "required": true,
                "description": "Required for this tool. Pass a parser alias (e.g. 'android', 'spring', 'typescript'). See list_parsers."
              }
            ],
            "output": "Paginated normalized endpoint records retaining original labels",
            "example": "get_endpoints(project_id='shop', parser_type='spring', protocol='grpc')"
          },
          {
            "name": "get_module_architecture_summary",
            "description": "Return bounded indexed-graph architecture context for one module or all modules.",
            "use_cases": [
              "Summarize a module architecture",
              "Understand project technology and interfaces"
            ],
            "inputs": [
              {
                "name": "project_id",
                "type": "Any",
                "required": true,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "module_id",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "all_modules",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "detail_level",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "item_limit",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "parser_type",
                "type": "Any",
                "required": true,
                "description": "Required for this tool. Pass a parser alias (e.g. 'android', 'spring', 'typescript'). See list_parsers."
              }
            ],
            "output": "Counts and bounded samples for modules, APIs, endpoints, frameworks, and special files",
            "example": "get_module_architecture_summary(project_id='shop', parser_type='android', module_id='project-module:shop:app')"
          },
          {
            "name": "get_project_special_files",
            "description": "Return decisive project/configuration files with safe summaries and parse coverage.",
            "use_cases": [
              "Find build and framework descriptors",
              "Inspect architecture evidence safely"
            ],
            "inputs": [
              {
                "name": "project_id",
                "type": "Any",
                "required": true,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "module_id",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "role",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "parser",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "framework",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "parse_depth",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "status",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "include_generated",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "offset",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "limit",
                "type": "Any",
                "required": false,
                "description": "Max results."
              },
              {
                "name": "parser_type",
                "type": "Any",
                "required": true,
                "description": "Required for this tool. Pass a parser alias (e.g. 'android', 'spring', 'typescript'). See list_parsers."
              }
            ],
            "output": "Paginated redaction-safe special-file records with provenance and diagnostics",
            "example": "get_project_special_files(project_id='shop', parser_type='android', role='topology')"
          },
          {
            "name": "get_framework_context",
            "description": "Return bounded framework instances and dimension-specific context support.",
            "use_cases": [
              "Understand detected frameworks",
              "Inspect routes, security, persistence, UI, and deployment coverage"
            ],
            "inputs": [
              {
                "name": "project_id",
                "type": "Any",
                "required": true,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "module_id",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "framework",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "dimensions",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "offset",
                "type": "Any",
                "required": false,
                "description": "Typed parameter accepted by the registered tool schema."
              },
              {
                "name": "limit",
                "type": "Any",
                "required": false,
                "description": "Max results."
              },
              {
                "name": "parser_type",
                "type": "Any",
                "required": true,
                "description": "Required for this tool. Pass a parser alias (e.g. 'android', 'spring', 'typescript'). See list_parsers."
              }
            ],
            "output": "Paginated framework contexts with coverage, evidence, and diagnostics",
            "example": "get_framework_context(project_id='shop', parser_type='spring', framework='spring')"
          }
        ]
      },
      "error": null
    },
    "isError": false
  }
}
```

## 07. `graph_mcp.semantic_search`

- Result: **PASS**
- Status: **SUCCESS_DATA**
- Contract: **PASS**
- Duration: `402.57 ms`
- Endpoint: `http://127.0.0.1:8788/mcp`
- Expected status: `SUCCESS`

### Description

Semantic code search using Qdrant vector embeddings. Optionally expands vector hits through the configured graph database.

### Input schema

```json
{
  "additionalProperties": false,
  "properties": {
    "query": {
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
    "mode": {
      "default": "combined",
      "type": "string"
    },
    "top_k": {
      "default": 10,
      "type": "integer"
    },
    "model_path": {
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
    "qdrant_url": {
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
    "collection": {
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
    "collection_comment": {
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
    "collection_code": {
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
    "show_snippet": {
      "default": false,
      "type": "boolean"
    },
    "show_comment": {
      "default": false,
      "type": "boolean"
    },
    "expand_graph": {
      "default": false,
      "type": "boolean"
    },
    "graph_depth": {
      "default": 2,
      "type": "integer"
    },
    "graph_direction": {
      "default": "both",
      "type": "string"
    },
    "graph_rel_types": {
      "anyOf": [
        {},
        {
          "type": "null"
        }
      ],
      "default": null
    },
    "graph_limit": {
      "default": 50,
      "type": "integer"
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
  "query": "mcp batch report markdown evidence suite",
  "mode": "combined",
  "top_k": 5,
  "content_mode": "summary",
  "include_raw_fields": false,
  "show_snippet": false,
  "show_comment": false,
  "expand_graph": false,
  "graph_depth": 2,
  "graph_direction": "both",
  "graph_limit": 20,
  "project_id": "cortext"
}
```

### Raw parsed JSON-RPC output

```json
{
  "jsonrpc": "2.0",
  "id": 9,
  "result": {
    "_meta": {
      "contract": "cortex.mcp.tool-result",
      "contractVersion": "1.0",
      "tool": "semantic_search"
    },
    "content": [
      {
        "type": "text",
        "text": "Success: 5 results. Read structuredContent.data."
      }
    ],
    "structuredContent": {
      "ok": true,
      "data": {
        "mode": "combined",
        "query": "mcp batch report markdown evidence suite",
        "results": [
          {
            "id": "d13ccbec-ef28-5d3a-ba46-b5d83ad18cf0",
            "version": 0,
            "score": 0.7210013786188582,
            "payload": {
              "node_type": "function",
              "symbol_id": "render_markdown/7@code-tiny/testtool/mcp_batch_report.py",
              "qualified_name": "render_markdown",
              "name": "render_markdown",
              "kind": "function",
              "scope_name": null,
              "file_path": "code-tiny/testtool/mcp_batch_report.py",
              "start_line": 450,
              "end_line": 588,
              "arity": 7,
              "exported": false,
              "intent": "side_effect",
              "inferred_doc": true,
              "doc_confidence": 0.547,
              "side_effect": true,
              "is_entrypoint": false,
              "entrypoint_kind": "",
              "project_id": "cortext",
              "project_name": "cortext",
              "language": "python",
              "repo": "/Users/hieplq1.aip/AI/cortex-harness",
              "build_system": "",
              "project_id_normalized": "cortext",
              "content_mode": "summary",
              "content": "Performs markdown operation (takes 7 parameters)"
            },
            "_collection": "cortext_4ee207813f__python_functions"
          },
          {
            "id": "6fcddfb1-042c-58e8-9f40-f70e4d6206ef",
            "version": 0,
            "score": 0.6983788442283728,
            "payload": {
              "node_type": "file",
              "symbol_id": "file::tests/test_mcp_batch_report.py",
              "file_path": "tests/test_mcp_batch_report.py",
              "project_id": "cortext",
              "project_name": "cortext",
              "language": "python",
              "repo": "/Users/hieplq1.aip/AI/cortex-harness",
              "build_system": "",
              "project_id_normalized": "cortext",
              "content_mode": "summary",
              "content": "tests/test_mcp_batch_report.py"
            },
            "_collection": "cortext_4ee207813f__python_functions"
          },
          {
            "id": "42902968-37f9-5a20-92ff-3816b31d644f",
            "version": 0,
            "score": 0.695988364656374,
            "payload": {
              "node_type": "function",
              "symbol_id": "BenchmarkSemanticSearchTests::test_markdown_writer_emits_all_sections/1@tests/test_benchmark_semantic_search.py",
              "qualified_name": "BenchmarkSemanticSearchTests::test_markdown_writer_emits_all_sections",
              "name": "test_markdown_writer_emits_all_sections",
              "kind": "function",
              "scope_name": "BenchmarkSemanticSearchTests",
              "file_path": "tests/test_benchmark_semantic_search.py",
              "start_line": 74,
              "end_line": 84,
              "arity": 1,
              "exported": false,
              "intent": "unknown",
              "inferred_doc": true,
              "doc_confidence": 0.025,
              "side_effect": true,
              "is_entrypoint": false,
              "entrypoint_kind": "",
              "project_id": "cortext",
              "project_name": "cortext",
              "language": "python",
              "repo": "/Users/hieplq1.aip/AI/cortex-harness",
              "build_system": "",
              "project_id_normalized": "cortext",
              "content_mode": "summary",
              "content": "Performs unknown operation on test markdown writer emits all sections (takes 1 parameter)"
            },
            "_collection": "cortext_4ee207813f__python_functions"
          },
          {
            "id": "ef5be47e-a70e-574c-b2c5-c102cff6b451",
            "version": 0,
            "score": 0.6837549219154303,
            "payload": {
              "node_type": "function",
              "symbol_id": "test_markdown_report_contains_schema_input_raw_output_and_contract_result/0@tests/test_mcp_batch_report.py",
              "qualified_name": "test_markdown_report_contains_schema_input_raw_output_and_contract_result",
              "name": "test_markdown_report_contains_schema_input_raw_output_and_contract_result",
              "kind": "function",
              "scope_name": null,
              "file_path": "tests/test_mcp_batch_report.py",
              "start_line": 93,
              "end_line": 136,
              "arity": 0,
              "exported": false,
              "intent": "unknown",
              "inferred_doc": true,
              "doc_confidence": 0.025,
              "side_effect": true,
              "is_entrypoint": false,
              "entrypoint_kind": "",
              "project_id": "cortext",
              "project_name": "cortext",
              "language": "python",
              "repo": "/Users/hieplq1.aip/AI/cortex-harness",
              "build_system": "",
              "project_id_normalized": "cortext",
              "content_mode": "summary",
              "content": "Performs unknown operation on test markdown report contains schema input raw output and contract result"
            },
            "_collection": "cortext_4ee207813f__python_functions"
          },
          {
            "id": "f18b4008-b4e9-59b4-a149-7a2d683d2bec",
            "version": 0,
            "score": 0.6811404476833303,
            "payload": {
              "node_type": "function",
              "symbol_id": "write_markdown/2@scripts/benchmark_semantic_search.py",
              "qualified_name": "write_markdown",
              "name": "write_markdown",
              "kind": "function",
              "scope_name": null,
              "file_path": "scripts/benchmark_semantic_search.py",
              "start_line": 389,
              "end_line": 417,
              "arity": 2,
              "exported": false,
              "intent": "side_effect",
              "inferred_doc": true,
              "doc_confidence": 0.595,
              "side_effect": true,
              "is_entrypoint": false,
              "entrypoint_kind": "",
              "project_id": "cortext",
              "project_name": "cortext",
              "language": "python",
              "repo": "/Users/hieplq1.aip/AI/cortex-harness",
              "build_system": "",
              "project_id_normalized": "cortext",
              "content_mode": "summary",
              "content": "Performs markdown operation (takes 2 parameters)"
            },
            "_collection": "cortext_4ee207813f__python_functions"
          }
        ],
        "content_mode": "summary",
        "graph_expansion": {
          "enabled": false,
          "seed_ids": [
            "render_markdown/7@code-tiny/testtool/mcp_batch_report.py",
            "tests/test_mcp_batch_report.py",
            "BenchmarkSemanticSearchTests::test_markdown_writer_emits_all_sections/1@tests/test_benchmark_semantic_search.py",
            "test_markdown_report_contains_schema_input_raw_output_and_contract_result/0@tests/test_mcp_batch_report.py",
            "write_markdown/2@scripts/benchmark_semantic_search.py"
          ],
          "depth": 2,
          "direction": "both",
          "relationship_types": [
            "CALLS",
            "USES_TYPE",
            "REFERENCES",
            "INHERITS"
          ],
          "results": [],
          "edges": []
        },
        "query_engine": "graph_generic",
        "capability": {
          "requested_parser": null,
          "canonical_parser": null,
          "query_engine": "graph_generic",
          "support_level": "generic",
          "support": {
            "symbols": "generic",
            "calls": "generic",
            "endpoints": "none",
            "database": "none"
          },
          "default_relationships_applied": []
        }
      },
      "error": null
    },
    "isError": false
  }
}
```

## 08. `graph_mcp.explore_graph`

- Result: **PASS**
- Status: **SUCCESS_DATA**
- Contract: **PASS**
- Duration: `24.80 ms`
- Endpoint: `http://127.0.0.1:8788/mcp`
- Expected status: `SUCCESS`

### Description

Intent-aware, multi-strategy Graph Explorer search. Accepts natural language, paragraphs, or vague descriptions (English or Vietnamese). Extracts entities, domain signals, and actions from the query, then fuses semantic vector search + BM25 keyword search + call-graph expansion. Returns explainable ranked nodes with per-node WHY reasons, entry points, related graph paths, and overall confidence score.

### Input schema

```json
{
  "additionalProperties": false,
  "properties": {
    "query": {
      "default": "",
      "type": "string",
      "description": "Natural language text (keyword, sentence, or multi-line paragraph)."
    },
    "mode": {
      "default": "hybrid",
      "type": "string",
      "description": "\"semantic\" | \"hybrid\" (default) | \"graph_expanded\""
    },
    "top_k": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "number"
        },
        {
          "type": "string"
        }
      ],
      "default": "",
      "description": "Max matched nodes (default 10)."
    },
    "collection": {
      "default": "",
      "type": "string",
      "description": "Qdrant collection name override."
    },
    "debug": {
      "default": false,
      "type": "boolean",
      "description": "Include per-signal score breakdown in each node."
    },
    "parser_type": {
      "default": "",
      "type": "string"
    },
    "project_id": {
      "default": "",
      "type": "string",
      "description": "Restrict every retrieval and expansion stage to one project."
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
  "query": "mcp batch report markdown evidence suite",
  "mode": "hybrid",
  "top_k": 5,
  "debug": false,
  "parser_type": "python",
  "project_id": "cortext"
}
```

### Raw parsed JSON-RPC output

```json
{
  "jsonrpc": "2.0",
  "id": 10,
  "result": {
    "_meta": {
      "contract": "cortex.mcp.tool-result",
      "contractVersion": "1.0",
      "tool": "explore_graph"
    },
    "content": [
      {
        "type": "text",
        "text": "Success. Read structuredContent.data."
      }
    ],
    "structuredContent": {
      "ok": true,
      "data": {
        "matched_nodes": [
          {
            "node_id": "dart::cortext::50eaa40267b9cdc14f35c8f6",
            "name": "FixtureApp",
            "qualified_name": "FixtureApp",
            "kind": "class",
            "file_path": "tests/fixtures/flutter-app/lib/app.dart",
            "score": 0.03,
            "reason": "Retrieved via recent modification (0.30). Located in tests/fixtures/flutter-app/lib/app.dart.",
            "is_entry_point": false,
            "hop_distance": 0,
            "signals": {
              "freshness": 0.3
            },
            "properties": {
              "node_id": "dart::cortext::50eaa40267b9cdc14f35c8f6",
              "intent": "",
              "exported": false,
              "side_effect": false,
              "return_type": "",
              "project_id": "cortext",
              "language": "dart",
              "_source": "graph_keyword",
              "bm25": 0.0
            }
          },
          {
            "node_id": "dart::cortext::a75a8709250aeb915d7f1899",
            "name": "build",
            "qualified_name": "FixtureApp.build",
            "kind": "method",
            "file_path": "tests/fixtures/flutter-app/lib/app.dart",
            "score": 0.03,
            "reason": "Retrieved via recent modification (0.30). Located in tests/fixtures/flutter-app/lib/app.dart.",
            "is_entry_point": true,
            "hop_distance": 0,
            "signals": {
              "freshness": 0.3
            },
            "properties": {
              "node_id": "dart::cortext::a75a8709250aeb915d7f1899",
              "intent": "",
              "exported": true,
              "side_effect": false,
              "return_type": "",
              "project_id": "cortext",
              "language": "dart",
              "_source": "graph_keyword",
              "bm25": 0.0
            }
          },
          {
            "node_id": "tests/fixtures/legacy-migration-e2e/program.pc",
            "name": "tests/fixtures/legacy-migration-e2e/program.pc",
            "qualified_name": "",
            "kind": "",
            "file_path": "",
            "score": 0.03,
            "reason": "Retrieved via recent modification (0.30).",
            "is_entry_point": false,
            "hop_distance": 0,
            "signals": {
              "freshness": 0.3
            },
            "properties": {
              "node_id": "tests/fixtures/legacy-migration-e2e/program.pc",
              "intent": "",
              "exported": false,
              "side_effect": false,
              "return_type": "",
              "project_id": "cortext",
              "language": "cplus",
              "_source": "graph_keyword",
              "bm25": 0.0
            }
          },
          {
            "node_id": "tests/fixtures/cplus_semantic_calls/virtual.cpp",
            "name": "tests/fixtures/cplus_semantic_calls/virtual.cpp",
            "qualified_name": "",
            "kind": "",
            "file_path": "",
            "score": 0.03,
            "reason": "Retrieved via recent modification (0.30).",
            "is_entry_point": false,
            "hop_distance": 0,
            "signals": {
              "freshness": 0.3
            },
            "properties": {
              "node_id": "tests/fixtures/cplus_semantic_calls/virtual.cpp",
              "intent": "",
              "exported": false,
              "side_effect": false,
              "return_type": "",
              "project_id": "cortext",
              "language": "cplus",
              "_source": "graph_keyword",
              "bm25": 0.0
            }
          },
          {
            "node_id": "tests/fixtures/cplus_semantic_calls/overload.cpp",
            "name": "tests/fixtures/cplus_semantic_calls/overload.cpp",
            "qualified_name": "",
            "kind": "",
            "file_path": "",
            "score": 0.03,
            "reason": "Retrieved via recent modification (0.30).",
            "is_entry_point": false,
            "hop_distance": 0,
            "signals": {
              "freshness": 0.3
            },
            "properties": {
              "node_id": "tests/fixtures/cplus_semantic_calls/overload.cpp",
              "intent": "",
              "exported": false,
              "side_effect": false,
              "return_type": "",
              "project_id": "cortext",
              "language": "cplus",
              "_source": "graph_keyword",
              "bm25": 0.0
            }
          }
        ],
        "entry_points": [
          {
            "node_id": "dart::cortext::a75a8709250aeb915d7f1899",
            "name": "build",
            "qualified_name": "FixtureApp.build",
            "kind": "method",
            "file_path": "tests/fixtures/flutter-app/lib/app.dart",
            "score": 0.03,
            "reason": "Retrieved via recent modification (0.30). Located in tests/fixtures/flutter-app/lib/app.dart.",
            "is_entry_point": true,
            "hop_distance": 0,
            "signals": {
              "freshness": 0.3
            },
            "properties": {
              "node_id": "dart::cortext::a75a8709250aeb915d7f1899",
              "intent": "",
              "exported": true,
              "side_effect": false,
              "return_type": "",
              "project_id": "cortext",
              "language": "dart",
              "_source": "graph_keyword",
              "bm25": 0.0
            }
          }
        ],
        "related_paths": [],
        "explanation": "Found 5 matching node(s) using hybrid (semantic + keyword) search. Query involves domain signals: ui. Top match: 'FixtureApp' (confidence 3%). 1 node(s) identified as entry points.",
        "confidence": 0.03,
        "query_analysis": {
          "intent": "default",
          "entities": [],
          "keywords": [
            "mcp",
            "batch",
            "report",
            "markdown",
            "evidence",
            "suite"
          ],
          "actions": [],
          "domain_signals": [
            "ui"
          ],
          "embedding_text": "mcp batch report markdown evidence suite ui component render view state frontend",
          "raw_query": "mcp batch report markdown evidence suite"
        },
        "mode": "hybrid",
        "retrieval": {
          "graph_provider": "falkordb",
          "graph_database": "cortext",
          "graph_databases": [
            "cortext"
          ],
          "qdrant_collections": [
            "cortext"
          ],
          "graph_requested": true,
          "graph_connected": true,
          "graph_expansion_requested": false,
          "semantic_enabled": false,
          "degraded": false
        },
        "query_engine": "graph_generic",
        "capability": {
          "requested_parser": "python",
          "canonical_parser": "python",
          "query_engine": "graph_generic",
          "support_level": "partial",
          "support": {
            "symbols": "full",
            "calls": "partial",
            "endpoints": "partial",
            "database": "none"
          }
        },
        "capability_diagnostics": {
          "schema_status": "available",
          "support_status": "partial",
          "requested_relationships": [
            "CALLS",
            "USES_TYPE",
            "REFERENCES",
            "INHERITS",
            "ALIASES",
            "ALIAS_OF",
            "DECLARES",
            "CONTAINS",
            "DEPENDS_ON",
            "IMPORTS",
            "EXPORTS",
            "IMPLEMENTS",
            "EXTENDS",
            "CALLS_API",
            "MATCHES",
            "HANDLES",
            "HANDLED_BY",
            "MAPPED_TO",
            "USES",
            "QUERIES",
            "RETURNS",
            "INJECTS"
          ],
          "used_relationships": [
            "CALLS",
            "REFERENCES",
            "DECLARES",
            "CONTAINS",
            "DEPENDS_ON",
            "IMPORTS",
            "EXTENDS",
            "HANDLES",
            "HANDLED_BY",
            "MAPPED_TO",
            "RETURNS"
          ],
          "omitted_relationships": [
            "USES_TYPE",
            "INHERITS",
            "ALIASES",
            "ALIAS_OF",
            "EXPORTS",
            "IMPLEMENTS",
            "CALLS_API",
            "MATCHES",
            "USES",
            "QUERIES",
            "INJECTS"
          ],
          "available_relationships": [
            "ALTERS",
            "CALLS",
            "CONDITIONAL",
            "DEFINES",
            "EXITS",
            "FALLS_THROUGH",
            "GOES_TO_DYNAMIC",
            "INCLUDES",
            "PERFORMS_THRU",
            "READS",
            "REFERENCES",
            "RETURNS",
            "WRITES",
            "CONTAINS",
            "IMPORTS",
            "DECLARES",
            "EXTENDS",
            "DECLARES_INTENT_ACTION",
            "DECLARES_COMPONENT",
            "INHERITS_FROM",
            "OVERRIDES",
            "HAS_FILE",
            "HAS_INVOCATION",
            "NEXT",
            "SEMANTIC_OF",
            "HANDLES",
            "APPLIES_TO",
            "FORWARDS_TO",
            "MAPS_TO",
            "RESOLVES_TO",
            "DECLARES_METHOD",
            "DECLARES_STATEMENT",
            "BINDS_STATEMENT",
            "HAS_RESULT_MAPPING",
            "MAPS_COLUMN",
            "MAPS_PROPERTY",
            "DEPENDS_ON_PARAMETER",
            "READS_FROM",
            "REFERENCES_COLUMN",
            "USES_RESULT_MAP",
            "LOADS_FROM",
            "POSTS_BACK_TO",
            "DEPENDS_ON",
            "RENDERS",
            "WRITES_SESSION",
            "HANDLED_BY",
            "MAPPED_TO",
            "REFERENCES_TABLE",
            "WRITES_TO",
            "SAME_MODULE",
            "HAS_DESCRIPTOR",
            "EXPOSES_ENDPOINT",
            "DECLARES_SERVICE",
            "HAS_RPC",
            "USES_FRAMEWORK",
            "EXPOSES_API"
          ],
          "explicit_request": false
        }
      },
      "error": null
    },
    "isError": false
  }
}
```

## 09. `mind_mcp.list_source_ids`

- Result: **PASS**
- Status: **SUCCESS_EMPTY**
- Contract: **PASS**
- Duration: `4.97 ms`
- Endpoint: `http://127.0.0.1:8789/mcp`
- Expected status: `SUCCESS`

### Description

List available source_id values from Neo4j (Paragraph nodes).

### Input schema

```json
{
  "properties": {
    "limit": {
      "default": 50,
      "title": "Limit",
      "type": "integer"
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
    }
  },
  "title": "list_source_idsArguments",
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
  "limit": 10,
  "project_id": "cortext"
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
      "tool": "list_source_ids"
    },
    "content": [
      {
        "type": "text",
        "text": "Success: 0 items. Read structuredContent.data."
      }
    ],
    "structuredContent": {
      "ok": true,
      "data": [],
      "error": null
    },
    "isError": false
  }
}
```

## 10. `mind_mcp.list_qdrant_collections`

- Result: **PASS**
- Status: **SUCCESS_EMPTY**
- Contract: **PASS**
- Duration: `10.23 ms`
- Endpoint: `http://127.0.0.1:8789/mcp`
- Expected status: `SUCCESS`

### Description

List Qdrant collections.

        Per the unified ingest/query contract:
        - ``project_id`` is optional. When omitted (or empty), returns every
          collection (``None`` semantics = full-search across all projects).
        - When supplied AND registered, filters to that project's collection.
        - When supplied but not registered, fails closed with the project
          registry error instead of silently querying another project's data.
        

### Input schema

```json
{
  "properties": {
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
    }
  },
  "title": "list_qdrant_collectionsArguments",
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
  "project_id": "cortext"
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
      "tool": "list_qdrant_collections"
    },
    "content": [
      {
        "type": "text",
        "text": "Success: 0 items. Read structuredContent.data."
      }
    ],
    "structuredContent": {
      "ok": true,
      "data": [],
      "error": null
    },
    "isError": false
  }
}
```

## 11. `mind_mcp.semantic_search`

- Result: **FAIL**
- Status: **TOOL_ERROR**
- Contract: **PASS**
- Duration: `52.65 ms`
- Endpoint: `http://127.0.0.1:8789/mcp`
- Expected status: `SUCCESS`
- Actual error code: `collection_unavailable`

### Expectation mismatches

- expected SUCCESS, received TOOL_ERROR

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
  "id": 5,
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
