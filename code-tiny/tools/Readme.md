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

Parser fallback 

```text
clang_parser.py

├── parse_and_extract()

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
JUST parse & extract. 

---

# 2. Data Extraction Specification

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