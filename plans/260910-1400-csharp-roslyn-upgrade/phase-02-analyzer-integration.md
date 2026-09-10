# Phase 02 — Primary Analyzer Integration and Fallback

## Goal
Integrate Roslyn worker vào `csharp_analyzer.py` với Roslyn-first, Tree-sitter fallback strategy.

## Scope

### Python Roslyn Adapter
Tạo `code-tiny/tools/csharp/roslyn_adapter.py` — modeled on `tools/common/aspnet/roslyn_adapter.py`:

```python
def parse_csharp_files_with_roslyn(
    *,
    root: str,
    files: List[str],
    semantic_mode: str = "auto",
    worker_project_path: Optional[str] = None,
    timeout_sec: float = 600.0,
    workspace_timeout_ms: int = 120000,
    file_timeout_ms: int = 60000,
    verbose: bool = False,
) -> Tuple[Dict[str, Dict], Dict[str, str], Dict[str, object]]:
    """Returns (success_by_relpath, errors_by_relpath, worker_meta)"""
```

Features:
- Build locking (threading.Lock)
- DLL path resolution with runtime major detection
- Manifest-based file list
- JSON protocol validation
- Error handling with stderr tail

### Analyzer Integration

Modify `csharp_analyzer.py::build_call_graph()`:

```python
async def build_call_graph(...):
    # 1. Scan files
    all_files = _scan_csharp_files(root)

    # 2. Try Roslyn first
    roslyn_available = False
    roslyn_payloads = {}
    try:
        roslyn_payloads, roslyn_errors, roslyn_meta = parse_csharp_files_with_roslyn(
            root=root, files=all_files, semantic_mode="auto", verbose=verbose
        )
        roslyn_available = True
    except (RuntimeError, OSError) as exc:
        if verbose:
            print(f"[roslyn] unavailable, falling back to tree-sitter: {exc}")

    # 3. For each file: use Roslyn payload if available, else Tree-sitter
    for file_path in all_files:
        rel_path = os.path.relpath(file_path, root)
        if roslyn_available and rel_path in roslyn_payloads:
            payload = convert_roslyn_to_payload(roslyn_payloads[rel_path])
            payload["parse_meta"]["parser_language"] = roslyn_meta.get("workspace_kind", "syntax")
        else:
            payload = _load_or_parse_payload(file_path, root, parse_cache_root, parse_cache)
            # parse_meta already has parser_language = "csharp_tree_sitter"

    # 4. Continue with existing graph/vector ingestion
```

### Parse Provenance

Every payload includes `parse_meta`:
```python
{
    "parser_language": "csharp_roslyn_workspace" | "csharp_roslyn_syntax" | "csharp_tree_sitter",
    "parser_available": True,
    "has_error": bool,
    "error_nodes": int,
    "semantic_enabled": bool,
    "coverage_status": "full" | "partial" | "syntax_only",
    "roslyn_workspace_kind": "solution" | "project" | "syntax" | "none",
}
```

### Cache Integration

- Parse cache version bump: `csharp-v2026-09-10-1`
- Cache key includes parser backend (roslyn vs tree-sitter)
- Roslyn payloads cached separately from Tree-sitter payloads
- Cache invalidation when worker version changes

### Fallback Behavior

| Condition | Behavior |
| --- | --- |
| dotnet SDK not installed | Catch OSError from subprocess, fallback to Tree-sitter |
| Worker build fails | Catch RuntimeError, fallback to Tree-sitter |
| Workspace load fails | Worker returns syntax_mode results, no fallback needed |
| Individual file fails | Worker returns ok=false for that file, fallback to Tree-sitter for that file |
| Timeout | Worker killed, fallback to Tree-sitter for timed-out files |

### CLI Arguments

Add to `parse_args()`:
```python
parser.add_argument("--roslyn-worker-project", default=None,
    help="Path to CSharpRoslynWorker.csproj (default: auto-detect)")
parser.add_argument("--semantic-mode", default="auto", choices=["auto", "on", "off"],
    help="Roslyn semantic analysis mode")
parser.add_argument("--roslyn-timeout", type=float, default=600.0,
    help="Roslyn worker timeout in seconds")
parser.add_argument("--disable-roslyn", action="store_true",
    help="Disable Roslyn, use Tree-sitter only")
```

### Implementation Steps

1. **T01**: Create `code-tiny/tools/csharp/roslyn_adapter.py`
   - Port pattern from `tools/common/aspnet/roslyn_adapter.py`
   - Add C#-specific protocol fields
   - Build locking, DLL resolution, runtime detection

2. **T02**: Create `code-tiny/tools/csharp/models.py`
   - Enhanced data classes (PropertyDef, FieldDef, EventDef, etc.)
   - Conversion functions: Roslyn evidence → analyzer payload
   - Backward compatibility: existing data classes still work

3. **T03**: Modify `csharp_analyzer.py::build_call_graph()`
   - Add Roslyn-first path
   - Add fallback logic
   - Add parse provenance to payloads
   - Update cache version

4. **T04**: Add CLI arguments
   - `--roslyn-worker-project`
   - `--semantic-mode`
   - `--roslyn-timeout`
   - `--disable-roslyn`

5. **T05**: Update `_load_or_parse_payload()`
   - Accept Roslyn payload as alternative input
   - Normalize both formats to common internal representation
   - Preserve parse_meta provenance

6. **T06**: Tests
   - `test_csharp_roslyn_adapter.py`: adapter unit tests
   - `test_csharp_fallback.py`: fallback behavior tests
   - `test_csharp_provenance.py`: parse provenance tests
   - `test_csharp_cache.py`: cache invalidation tests

## Acceptance Criteria

- Roslyn-first path works when dotnet SDK is available
- Tree-sitter fallback works when Roslyn unavailable
- Parse provenance correctly records backend used
- Cache version bump invalidates old entries
- Existing CLI arguments still work
- `--disable-roslyn` forces Tree-sitter path
- No regression in existing tests
