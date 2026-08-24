# 1. Parser Script Structure

```text
<language>_analyzer.py

├── Dataclass Definitions
│      FileDef
│      FunctionDef
│      TypeDef
│      NamespaceDef
│      FieldDef
│      AliasDef
│      TemplateDef
│      RelationEdge
│      CallEdge
│
├── Parser Initialization
│      _get_parser()
│      _parse_file()
│
├── AST Utilities
│      _node_text()
│      _extract_comment()
│      _extract_scope()
│      _extract_name()
│      ...
│
├── AST Walker
│      _walk_tree()
│
├── Symbol Extractors
│      extract_file
│      extract_namespace
│      extract_type
│      extract_function
│      extract_field
│      extract_alias
│      extract_template
│
├── Call Resolver
│      _resolve_calls()
│
├── Parse Entry
│      parse_<language>_file()
│
├── Cache Loader
│      _load_or_parse_payload()
│
├── Graph Builder
│      build_call_graph()
│
└── CLI
       main()
```


---

##  <language>_parser.py

Clang diagnostic adapter (never a structural fallback)

```text
clang_parser.py

├── parse_and_extract()  # fixture/differential inventory only

│
├── helper

│     _build_scope()

│     _extract_comment()

│     _find_enclosing_func()

│
└── return ParseResult
```

Do not resolve graph.
Do not write Neo4j/Falkor.
Do not embedding.
JUST parse & extract diagnostic/shadow evidence. Tree-sitter is the sole
structural payload owner; this adapter must never replace, cache, validate, or
publish a whole-file graph payload.

Parser-quality policy is orthogonal to Clang semantics:

- `off`: Tree-sitter structure only; quality artifacts are disabled.
- `report`: Tree-sitter structure plus quality reporting.
- `repair`: Tree-sitter structure plus bounded same-backend grammar retry.

Legacy LIBCLANG structural caches are incompatible with the current
`cplus-function-v2`/Tree-sitter policy and are ignored rather than upgraded.

---

# 2. File Scanning (`_scan_c_family_files`)

## Supported extensions

```
.c  .h  .hpp  .cpp  .cc  .cxx  .hh  .hxx
```

## Skipped directories (`_SCAN_SKIP_DIRS`)

Directories matching any entry below are pruned before descent — they will **not** be scanned.

| Category | Entries |
|---|---|
| Version control | `.git` `.hg` `.svn` |
| IDE | `.idea` `.vs` `.vscode` `.eclipse` `.settings` |
| Build outputs | `build` `out` `bin` `obj` `cmake-build-*` |
| CMake cache | `CMakeFiles` `CMakeCache.txt` `cmake_install.cmake` `Makefile` |
| Compiled artefacts | `*.o` `*.obj` `*.so` `*.dll` `*.dylib` `*.a` `*.lib` `*.exe` |
| Precompiled headers | `*.gch` `*.pch` |
| Gradle / Android NDK | `.gradle` `.externalNativeBuild` |
| Node / JS tooling | `node_modules` `dist` `target` |
| Qt generated | `moc_*` `ui_*` `qrc_*.cpp` |
| Cache | `.cache` `.parcel-cache` `__pycache__` |
| Test results | `coverage` `.test-results` `test-results` |
| Temporary | `tmp` `temp` `.tmp` `tmpdir` |
| OS specific | `.DS_Store` `Thumbs.db` |
| Misc project files | `*.pro.user` `*.user` `*.suo` |

> Note: matching is exact name comparison (`name not in _SCAN_SKIP_DIRS`).  
> Glob-style entries (e.g. `cmake-build-*`, `moc_*`) are listed for documentation purposes  
> but are **not** evaluated as globs at runtime — add fnmatch logic if wildcard support is needed.

---

# 3. Data Extraction Specification

MUST TO GENERATE

## File

Extract

```
file_path

start_line

end_line

code

comment

summary
```

extra

```
includes

using_namespaces

using_imports

macros

parse_meta
```

---

## Namespace

Extract

```
symbol_id

qualified_name

name

file_path

start_line

end_line

code

comment
```

---

## Type

Extract

```
symbol_id

qualified_name

name

kind

file_path

start_line

end_line

code

comment
```

kind

```
class

struct

interface

enum

union

record
```

---

## Function

Extract

```
symbol_id

qualified_name

name

kind

scope_name

file_path

start_byte

end_byte

start_line

end_line

arity

code

comment
```

kind

```
function

method

constructor

destructor

declaration

template
```

---

## Field

Extract

```
symbol_id

qualified_name

name

scope_name

type_signature

file_path

start_line

end_line

code
```

---

## Alias

Extract

```
symbol_id

qualified_name

name

kind

target_name

file_path

start_line

end_line
```

---

## Template

Extract

```
symbol_id

name

file_path

start_line

end_line
```

---

## Call

Extract

```
caller_id

caller_file

caller_scope

call_line

call_column

call_start_byte

call_branch_kind

call_loop_depth

call_control_frames_json

call_type

call_arity

callee_name

callee_id
```

---

## Relation

Extract

```
source_id

source_label

target_id

target_label

rel_type

properties
```

Supported

```
CONTAINS

DECLARES

EXTENDS

USES_TYPE

POINTER_TO

ALIASES

TEMPLATES

INCLUDES

CALLS_FUNCTION_POINTER

EMITS_EVENT

HANDLES_EVENT

POSSIBLE_CALLS
```

---

## Parse Meta

```
parser_language

parser_language_initial

has_error

error_nodes

header_retry_attempted

header_retry_selected

header_retry_error_nodes
```

---

# Final parser MUST RETURN payload

```text
ParseResult

├── functions
├── calls
├── types
├── namespaces
├── relations
├── function_types
├── fields
├── aliases
├── templates
├── file_def
├── using_namespaces
├── using_imports
├── includes
├── macros
└── parse_meta
```
