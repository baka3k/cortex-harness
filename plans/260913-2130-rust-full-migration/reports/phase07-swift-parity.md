# Phase 07 — swift analyzer parity (python vs rust)

- chạy: 2026-09-14 03:25:38
- testdata: `tests/fixtures/swift-analyzer` (class/struct/protocol/enum/extension/methods/fields/typealias/subscript/cross-file)
- mask: `['_dst', '_edge_id', '_graph_id', '_src', 'created_at', 'last_updated', 'summary_updated_at', 'updated_at']`

### scan_result testdata_full

- py: `[SCAN_RESULT] parser=swift files=3 functions=26 classes=20 vectors=0 vector_status=disabled`
- rust: `[SCAN_RESULT] parser=swift files=3 functions=26 classes=20 vectors=0 vector_status=disabled`


### TESTDATA_FULL

- nodes: py=61 rust=61
- edges: py=114 rust=114
- diff_total: **114**

```json

{
  "nodes_only_py": {},
  "nodes_only_rust": {},
  "edges_only_py": {},
  "edges_only_rust": {},
  "props_differ": {
    "edge Template|template::networking.swift:11:11 -[TEMPLATES]-> Type|APIClient": {
      "py": {
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 0,
        "_end_id": 55
      },
      "rust": {
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 60,
        "_end_id": 5
      }
    },
    "edge Template|template::networking.swift:44:44 -[TEMPLATES]-> Function|APIClient.fetch/1@networking.swift": {
      "py": {
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 1,
        "_end_id": 39
      },
      "rust": {
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 59,
        "_end_id": 21
      }
    },
    "edge Template|template::networking.swift:44:44 -[TEMPLATES]-> Type|APIClient": {
      "py": {
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 1,
        "_end_id": 55
      },
      "rust": {
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 59,
        "_end_id": 5
      }
    },
    "edge Alias|alias::Identifier@models.swift -[ALIASES]-> Type|String": {
      "py": {
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 2,
        "_end_id": 53
      },
      "rust": {
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 58,
        "_end_id": 7
      }
    },
    "edge Alias|alias::Identifier@models.swift -[USES_TYPE]-> Type|String": {
      "py": {
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 2,
        "_end_id": 53
      },
      "rust": {
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 58,
        "_end_id": 7
      }
    },
    "edge Field|field::Shelf.items@app.swift -[USES_TYPE]-> Type|String": {
      "py": {
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 3,
        "_end_id": 53
      },
      "rust": {
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 57,
        "_end_id": 7
      }
    },
    "edge Field|field::Item.identifier@models.swift -[USES_TYPE]-> Type|String": {
      "py": {
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 5,
        "_end_id": 53
      },
      "rust": {
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 55,
        "_end_id": 7
      }
    },
    "edge Field|field::Widget.identifier@models.swift -[USES_TYPE]-> Type|String": {
      "py": {
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 6,
        "_end_id": 53
      },
      "rust": {
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 54,
        "_end_id": 7
      }
    },
    "edge Field|field::Widget.weight@models.swift -[USES_TYPE]-> Type|Double": {
      "py": {
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 7,
        "_end_id": 48
      },
      "rust": {
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 53,
        "_end_id": 12
      }
    },
    "edge Field|field::Widget.secret@models.swift -[USES_TYPE]-> Type|String": {
      "py": {
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 8,
        "_end_id": 53
      },
      "rust": {
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 52,
        "_end_id": 7
      }
    },
    "edge Field|field::Category.rank@models.swift -[USES_TYPE]-> Type|Int": {
      "py": {
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 9,
        "_end_id": 50
      },
      "rust": {
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 51,
        "_end_id": 10
      }
    },
    "edge Field|field::Endpoint.path@networking.swift -[USES_TYPE]-> Type|String": {
      "py": {
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 10,
        "_end_id": 53
      },
      "rust": {
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 50,
        "_end_id": 7
      }
    },
    "edge Field|field::APIClient.path@networking.swift -[USES_TYPE]-> Type|String": {
      "py": {
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 11,
        "_end_id": 53
      },
      "rust": {
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 49,
        "_end_id": 7
      }
    },
    "edge Field|field::APIClient.base@networking.swift -[USES_TYPE]-> Type|String": {
      "py": {
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 12,
        "_end_id": 53
      },
      "rust": {
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 48,
        "_end_id": 7
      }
    },
    "edge Field|field::APIClient.root@networking.swift -[USES_TYPE]-> Type|String": {
      "py": {
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 13,
        "_end_id": 53
      },
      "rust": {
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 47,
        "_end_id": 7
      }
    },
    "edge Field|field::HealthEndpoint.path@networking.swift -[USES_TYPE]-> Type|String": {
      "py": {
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 15,
        "_end_id": 53
      },
      "rust": {
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 45,
        "_end_id": 7
      }
    },
    "edge Function|Greeter.hello/0@app.swift -[USES_TYPE]-> Type|String": {
      "py": {
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 16,
        "_end_id": 53
      },
      "rust": {
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 44,
        "_end_id": 7
      }
    },
    "edge Function|Greeter.hello/1@app.swift -[USES_TYPE]-> Type|String": {
      "py": {
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 17,
        "_end_id": 53
      },
      "rust": {
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 43,
        "_end_id": 7
      }
    },
    "edge Function|Greeter.hello/2@app.swift -[CALLS]-> Function|Greeter.hello/1@app.swift": {
      "py": {
        "count": 1,
        "call_type": "function",
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 18,
        "_end_id": 17
      },
      "rust": {
        "count": 1,
        "call_type": "function",
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 42,
        "_end_id": 43
      }
    },
    "edge Function|Greeter.hello/2@app.swift -[POSSIBLE_CALLS]-> Function|Greeter.hello/1@app.swift": {
      "py": {
        "line": 16,
        "column": 20,
        "call_type": "function",
        "arity": 1,
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 18,
        "_end_id": 17
      },
      "rust": {
        "arity": 1,
        "call_type": "function",
        "column": 20,
        "line": 16,
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 42,
        "_end_id": 43
      }
    },
    "edge Function|Greeter.hello/2@app.swift -[USES_TYPE]-> Type|String": {
      "py": {
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 18,
        "_end_id": 53
      },
      "rust": {
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 42,
        "_end_id": 7
      }
    },
    "edge Function|Greeter.hello/2@app.swift -[USES_TYPE]-> Type|Bool": {
      "py": {
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 18,
        "_end_id": 54
      },
      "rust": {
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 42,
        "_end_id": 6
      }
    },
    "edge Function|Shelf.subscript/1@app.swift -[USES_TYPE]-> Type|Int": {
      "py": {
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 19,
        "_end_id": 50
      },
      "rust": {
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 41,
        "_end_id": 10
      }
    },
    "edge Function|Shelf.subscript/1@app.swift -[USES_TYPE]-> Type|String": {
      "py": {
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 19,
        "_end_id": 53
      },
      "rust": {
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 41,
        "_end_id": 7
      }
    },
    "edge Function|Shelf.count/0@app.swift -[USES_TYPE]-> Type|Int": {
      "py": {
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 20,
        "_end_id": 50
      },
      "rust": {
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 40,
        "_end_id": 10
      }
    },
    "edge Function|TrackedShelf.count/0@app.swift -[CALLS]-> Function|TrackedShelf.count/0@app.swift": {
      "py": {
        "count": 1,
        "call_type": "function",
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 21,
        "_end_id": 21
      },
      "rust": {
        "count": 1,
        "call_type": "function",
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 39,
        "_end_id": 39
      }
    },
    "edge Function|TrackedShelf.count/0@app.swift -[POSSIBLE_CALLS]-> Function|TrackedShelf.count/0@app.swift": {
      "py": {
        "line": 46,
        "column": 16,
        "call_type": "function",
        "arity": 0,
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 21,
        "_end_id": 21
      },
      "rust": {
        "arity": 0,
        "call_type": "function",
        "column": 16,
        "line": 46,
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 39,
        "_end_id": 39
      }
    },
    "edge Function|TrackedShelf.count/0@app.swift -[USES_TYPE]-> Type|Int": {
      "py": {
        "project_id": "parity_swift",
        "project_id_normalized": "parity_swift",
        "_start_id": 21,
        "_end_id": 50
      },
      "rust": {
        "project_id": "parity_swift",
        "project_id_normalized": 

```


### scan_result inc_seed

- py: `[SCAN_RESULT] parser=swift files=3 functions=26 classes=20 vectors=0 vector_status=disabled`
- rust: `[SCAN_RESULT] parser=swift files=3 functions=26 classes=20 vectors=0 vector_status=disabled`


### INC_SEED

- nodes: py=61 rust=61
- edges: py=114 rust=114
- diff_total: **0**


### scan_result inc_run

- py: `[SCAN_RESULT] parser=swift files=2 functions=9 classes=8 vectors=0 vector_status=disabled`
- rust: `[SCAN_RESULT] parser=swift files=2 functions=9 classes=8 vectors=0 vector_status=disabled`

- cleanup: py=(0, 0) rust=(0, 0)


### INC_RUN

- nodes: py=27 rust=27
- edges: py=34 rust=34
- diff_total: **0**


> [warn] skip stock: chỉ 0 file .swift dưới stock (ngoài skip dirs) < 3 — không có matching sources — stock scenario bỏ qua.


## Kết luận

- FAILURES: ['TESTDATA_FULL: graph diff rỗng ngoài mask']

