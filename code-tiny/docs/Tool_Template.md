# Analyzer Tool Design and Implementation Template

Use when designing or implementing a new analyzer under `code-tiny/tools/<tool_name>/`. Replace every `<placeholder>`, delete inapplicable sections, and record the reason when a mandatory contract cannot be met. This template generalizes the semantic-analysis structure from the Struts Analyzer Design Specification v2 and the production contracts used by the repository's Struts, Servlet/JSP, Spring, MyBatis, and language analyzers.

**MUST**: required for acceptance. **SHOULD**: expected unless a documented constraint justifies otherwise. **MAY**: optional.

## 1. Tool Identity

| Field | Value |
| --- | --- |
| Tool name | `<tool_name>` |
| Display name | `<Tool Name> Analyzer` |
| Parser/framework version | `<tool_name>-v<yyyy-mm-dd>-<revision>` |
| Primary language(s) | `<languages>` |
| Framework/domain | `<framework_or_domain>` |
| Configuration formats | `<xml/yaml/json/properties/annotations/etc.>` |
| Public Python API | `tools.<tool_name>.run_<tool_name>_analysis(...)` |
| CLI module | `python -m tools.<tool_name>.<tool_name>_analyzer` |
| Graph namespace | `<tool_name>` |
| Owners | `<team_or_owner>` |

## 2. Overview

### 2.1 Purpose

> The `<Tool Name> Analyzer` reconstructs `<framework/domain>` semantics from `<source artifacts>` so downstream graph, search, AI, and migration workflows can understand `<routes, execution flows, data mappings, state, dependencies, etc.>`.

The objective MUST be semantic reconstruction, not file enumeration or raw AST export.

### 2.2 Supported scope

- `<feature 1>`
- `<feature 2>`
- `<feature 3>`
- Cross-file relationships between `<artifact A>` and `<artifact B>`
- Ordered execution behavior where order changes meaning
- Configuration inherited or merged across files/modules

### 2.3 Non-goals

- Runtime execution or dynamic instrumentation
- Generic analysis already owned by another tool
- `<unsupported framework version or feature>`
- `<unsupported dynamic behavior>`

### 2.4 Success criteria

Deterministically reconstruct:

1. `<input>` resolves to `<semantic entity>`.
2. `<entry point>` traces through `<ordered steps>` to `<outcome>`.
3. `<configuration inheritance>` produces `<effective configuration>`.
4. Repeated runs over identical inputs produce byte-stable normalized output.

Required outputs: entry points with source evidence, framework/domain entities as first-class semantic facts, ordered execution/resolution flows, cross-file and cross-language relationships, unresolved/ambiguous/truncated/unsupported cases as diagnostics, and migration-relevant meaning (not only syntax).

## 3. Evidence and Requirements

| Requirement | Source | Implementation owner | Test evidence | Status |
| --- | --- | --- | --- | --- |
| `<semantic behavior>` | `<spec/docs/code>` | `<module/function>` | `<test>` | planned |
| `<graph node/edge>` | `<spec/docs/code>` | `<resolver>` | `<contract test>` | planned |
| `<error behavior>` | `<repository contract>` | `<pipeline>` | `<recovery test>` | planned |

- Repository code and authoritative framework documentation are evidence; assumptions are not.
- Conflicting requirements MUST be recorded and resolved before implementation.
- Unsupported but detected behavior MUST produce a capability or coverage diagnostic.
- Never fabricate relationships when evidence is absent — use `unresolved`, `ambiguous`, or `partial` states.

## 4. Technical Context

| Category | Technology or decision |
| --- | --- |
| Source languages | `<languages>` |
| Framework/runtime | `<framework/runtime>` |
| Parser foundation | `<existing Tree-sitter/parser adapter>` |
| Configuration parsers | `<safe XML/YAML/JSON/etc.>` |
| View/template parser | `<parser or not applicable>` |
| Build systems | `<Maven/Gradle/npm/etc.>` |
| Deployment formats | `<WAR/JAR/container/etc.>` |
| Graph provider | Shared repository graph-provider abstraction |
| Optional vector output | `<Qdrant collection or not applicable>` |

### 4.1 Parser strategy

Framework analyzers MUST reuse existing language parsers whenever the framework is embedded in a general-purpose language. Do not create a dedicated grammar unless the framework defines an actual language and existing parsers cannot represent it. The semantic layer owns framework interpretation.

```text
Language source ──> shared language parser ──> language AST/symbols ──┐
Configuration ───> safe format parser ──────> normalized config ─────┼─> semantic resolver
Views/templates ─> shared view parser ──────> normalized view ───────┤        │
Build metadata ──> detector ────────────────> modules/capabilities ──┘        ▼
                                                                     semantic graph
```

## 5. Source Artifact Inventory

| Artifact group | Extensions/names | Detection rule | Parser | Semantic output | Mandatory |
| --- | --- | --- | --- | --- | --- |
| Language source | `<.java/.kt/...>` | `<rule>` | `<adapter>` | `<classes/actions/etc.>` | yes |
| Primary config | `<names/patterns>` | `<rule>` | `<safe parser>` | `<packages/routes/etc.>` | yes |
| Secondary config | `<patterns>` | `<rule>` | `<parser>` | `<validation/mappings/etc.>` | no |
| Views/templates | `<patterns>` | `<rule>` | `<shared parser>` | `<view/form/include facts>` | no |
| Resource bundles | `<*.properties/etc.>` | `<rule>` | `<parser>` | `<keys/lookups>` | no |
| Build metadata | `<pom.xml/etc.>` | `<rule>` | `<detector>` | `<plugins/modules/capabilities>` | yes |
| Static resources | `<patterns>` | `<rule>` | `<scanner>` | `<only if semantically required>` | no |

For every artifact type, specify: encoding policy, maximum accepted size, malformed-input behavior, include/import resolution rules, path containment requirements, and whether missing parser support is fatal or partial.

## 6. High-Level Architecture

The default architecture is a deterministic, side-effect-free analysis core with optional output adapters. Core analysis MUST NOT require Neo4j, FalkorDB, Qdrant, network access, or credentials. Persistence is an adapter invoked after a valid result exists.

```text
Project root
   │
   ▼
Detector and bounded scanner
   │
   ├──> module/artifact inventory
   ├──> parser capability report
   └──> selected/changed/deleted path sets
   │
   ▼
Language and format parsers
   │
   ▼
Normalized intermediate representations
   │
   ▼
Cross-file semantic resolver
   │
   ├──> semantic facts
   ├──> relationships
   ├──> dependency index
   └──> diagnostics and coverage
   │
   ▼
Validated analysis result
   │
   ├──> JSON preview/diagnostics
   ├──> graph writer
   ├──> optional vector writer
   └──> incremental snapshot/cache
```

## 7. Required Package Layout

Start with the smallest layout that preserves clear ownership. Optional modules SHOULD be added only when their responsibility is real — avoid empty architectural layers.

```text
code-tiny/tools/<tool_name>/
├── __init__.py                 # public API and parser version
├── <tool_name>_analyzer.py     # CLI orchestration only
├── models.py                   # immutable contracts and serialization
├── detector.py                 # project/module/artifact discovery
├── pipeline.py                 # deterministic analysis orchestration
├── resolver.py                 # cross-file semantic resolution
├── parser_runtime.py           # parser capability checks/adapters
├── <artifact>_parser.py        # one module per meaningful format
├── cache.py                    # optional versioned parse cache
└── README.md                   # supported behavior and operation

tests/
├── fixtures/<tool_name>-app/   # minimal representative project
├── test_<tool_name>_imports.py
├── test_<tool_name>_scan.py
├── test_<tool_name>_parsers.py
├── test_<tool_name>_resolution.py
├── test_<tool_name>_graph_contract.py
├── test_<tool_name>_incremental.py
└── test_<tool_name>_error_recovery.py
```

### 7.1 Responsibility boundaries

| Module | Owns | Must not own |
| --- | --- | --- |
| `detector.py` | modules, artifact discovery, evidence, confidence | semantic relationship resolution |
| parser modules | safe parsing and source spans | graph persistence |
| `models.py` | immutable data contracts, stable IDs, serialization | filesystem traversal |
| `resolver.py` | effective configuration and cross-file links | CLI/network configuration |
| `pipeline.py` | orchestration, dedupe, budgets, coverage | provider-specific writes |
| analyzer CLI | argument validation, outputs, exit codes | framework semantic rules |
| graph writer | batches, generations, transactions, cleanup | parsing |

## 8. Bounded Deterministic Scan

### 8.1 Scan configuration

```python
IGNORED_DIR_PATTERNS = {
    ".git", ".hg", ".svn", ".idea", ".vscode", ".settings",
    "build", "dist", "out", "target", "bin", "generated", "generated-sources",
    "node_modules", ".gradle", ".cache", "__pycache__", "coverage",
    "test-results", "tmp", "temp", ".venv", "venv",
}

IGNORED_FILE_PATTERNS = {
    "*.class", "*.jar", "*.war", "*.ear",
    "*.log", "*.tmp", "*.bak", "*.swp", "*.swo", "*~",
    ".DS_Store", "Thumbs.db",
}
```

Customize for the target ecosystem. Do not exclude real source directories like `src/test` unless the tool's documented scope requires it.

### 8.2 Scanner skeleton

```python
def matches_pattern(name: str, patterns: Collection[str]) -> bool:
    normalized = name.casefold()
    return any(fnmatch.fnmatchcase(normalized, item.casefold()) for item in patterns)


def iter_source_files(root: Path) -> Iterable[Path]:
    for current_root, dirnames, filenames in os.walk(root, topdown=True):
        dirnames[:] = sorted(
            name
            for name in dirnames
            if not name.startswith(".")
            and not matches_pattern(name, IGNORED_DIR_PATTERNS)
        )
        for name in sorted(filenames):
            if matches_pattern(name, IGNORED_FILE_PATTERNS):
                continue
            if is_supported_source(name):
                yield Path(current_root) / name
```

### 8.3 Required scan invariants

- Use `os.walk(..., topdown=True)` and prune `dirnames` in place.
- Sort directory names, file names, and final outputs.
- Normalize emitted paths to project-relative POSIX paths.
- Reject or diagnose paths outside the resolved project root.
- Define symlink behavior; default to not following directory symlinks.
- Filter unsupported extensions before reading file contents.
- Apply size budgets before reading/parsing.
- Preserve explicitly declared includes/imports when valid and inside the root.
- `selected_paths` narrows work but must not make identities depend on selection order.

### 8.4 Optional ignore file

Expose `--ignore-file <path>` when custom exclusions are required. Recommended syntax: UTF-8 text, one project-relative glob per line; blank lines and `#`-prefixed lines ignored; `/` separates path segments on every platform; `!` negation unsupported unless tested and documented; invalid or outside-root paths produce diagnostics; built-in safety exclusions cannot be negated.

Do not automatically treat `.gitignore` as the analyzer contract unless the repository standardizes that behavior — build outputs and analyzer semantics may differ from version-control policy.

## 9. Parser Capabilities

Report parser availability so downstream systems can distinguish an empty project from degraded analysis.

```python
@dataclass(frozen=True)
class ParserCapability:
    language: str
    available: bool
    mandatory: bool
    parser: str
    package: str = ""
    package_version: str = ""
    abi_version: str = ""
    status: str = "ok"
    message: str = ""
```

- Mandatory parser unavailable: analysis is `partial` or fails according to CLI policy.
- Optional parser unavailable: continue and emit a warning/capability record.
- Parser selection MUST be explicit and testable; do not branch on graph-provider availability.
- Parser ABI/package versions SHOULD contribute to cache fingerprints.
- Malformed source SHOULD generate bounded diagnostics and allow other files to continue.

## 10. Semantic Model

### 10.1 Domain entities

Every framework concept that changes behavior SHOULD be a first-class fact.

| Entity kind | Identity inputs | Required properties | Evidence source |
| --- | --- | --- | --- |
| `<EntryPoint>` | `<project/module/route/method>` | `<method/path/confidence>` | `<config/annotation>` |
| `<Handler>` | `<qualified symbol>` | `<class/method>` | `<language AST>` |
| `<OrderedStep>` | `<owner/position/target>` | `<position/parameters>` | `<config>` |
| `<Outcome>` | `<owner/name/type>` | `<target/type>` | `<config/annotation>` |
| `<ValidationRule>` | `<owner/field/rule>` | `<rule/params>` | `<source>` |
| `<Plugin/Extension>` | `<name/class>` | `<capability>` | `<build/config>` |

### 10.2 Relationships

| Relationship | From | To | Ordering/metadata | Resolution rule |
| --- | --- | --- | --- | --- |
| `<MAPPED_TO>` | `<EntryPoint>` | `<Handler>` | confidence, reason | `<rule>` |
| `<PASSES_THROUGH>` | `<EntryPoint>` | `<OrderedStep>` | position | `<rule>` |
| `<RETURNS_RESULT>` | `<Handler>` | `<Outcome>` | result name | `<rule>` |
| `<RESOLVES_TO>` | `<Outcome>` | `<View/target>` | type | `<rule>` |
| `<VALIDATES_WITH>` | `<Handler>` | `<ValidationRule>` | phase | `<rule>` |

Direction and spelling MUST match existing graph conventions. Preserve execution order as an explicit property or ordered intermediary node; never infer order from storage order.

### 10.3 Semantic modeling rules

- Merge XML/configuration, annotations, code, templates, and bundles into one effective model.
- Model inherited defaults and local overrides separately from their resolved values.
- Keep raw and resolved values when resolution changes meaning.
- Classify outcome/result types explicitly; an outcome is not necessarily a view.
- Represent validation when it can alter control flow or outcomes.
- Represent plugin/convention-derived behavior with its extraction method.
- Attach confidence, extraction method, resolution status, reason, and source evidence.
- Do not create an edge to a missing generated node — emit an unresolved anchor diagnostic.

## 11. Common Data Contracts

Use immutable data classes or equivalent immutable models.

### 11.1 Source span

```python
@dataclass(frozen=True)
class SourceSpan:
    file_path: str
    start_line: int = 1
    end_line: int = 1
    start_column: int = 1
    end_column: int = 1
```

Paths MUST be normalized, project-relative, and checkout-independent in serialized output.

### 11.2 Diagnostic

```python
@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    severity: str = "warning"  # info | warning | error
    file_path: str = ""
    start_line: int = 1
    end_line: int = 1
    hint: str = ""
    details: Mapping[str, object] = field(default_factory=dict)
```

Diagnostic codes MUST be stable and namespaced: `<tool_name>.<area>.<condition>`.

### 11.3 Semantic fact

```python
@dataclass(frozen=True)
class SemanticFact:
    kind: str
    stable_id: str
    name: str
    source: SourceSpan
    project_id: str
    project_name: str
    module_id: str
    language: str
    confidence: float = 1.0
    extraction_method: str = "<tool_name>"
    resolution_status: str = "resolved"
    raw_value: str = ""
    resolved_value: str = ""
    source_symbol_id: str = ""
    properties: Mapping[str, object] = field(default_factory=dict)
```

### 11.4 Semantic relationship

```python
@dataclass(frozen=True)
class SemanticRelationship:
    stable_id: str
    from_id: str
    to_id: str
    from_label: str
    to_label: str
    type: str
    project_id: str
    module_id: str
    source: SourceSpan
    confidence: float = 1.0
    resolution_status: str = "resolved"
    reason: str = ""
    properties: Mapping[str, object] = field(default_factory=dict)
    from_generated: bool = True
    to_generated: bool = True
```

### 11.5 Analysis result

```python
@dataclass(frozen=True)
class AnalysisResult:
    project_id: str
    project_name: str
    root: str
    modules: tuple[Module, ...] = ()
    artifacts: tuple[Artifact, ...] = ()
    parser_capabilities: tuple[ParserCapability, ...] = ()
    semantic_facts: tuple[SemanticFact, ...] = ()
    relationships: tuple[SemanticRelationship, ...] = ()
    dependency_index: DependencyIndex = field(default_factory=DependencyIndex)
    diagnostics: tuple[Diagnostic, ...] = ()
    coverage_status: str = "empty"  # empty | partial | complete
    missing_anchor_count: int = 0
    ambiguity_count: int = 0
    truncation_count: int = 0
    parser_version: str = PARSER_VERSION
```

Serialization MUST redact sensitive values, sort all unordered collections, and replace absolute checkout roots with `.` or project-relative paths.

## 12. Stable Identity and Determinism

Stable semantic identity is a public contract.

```python
def stable_digest(*parts: object) -> str:
    canonical = "\x1f".join(normalize_identity_part(item) for item in parts)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def stable_semantic_id(kind: str, project_id: str, module_id: str, *parts: object) -> str:
    safe_kind = normalize_kind(kind)
    return f"<tool_name>::{safe_kind}::{stable_digest(project_id, module_id, *parts)}"
```

Identity inputs MUST use semantic coordinates (qualified names, routes, config keys, source-relative paths), excluding absolute roots, timestamps, random UUIDs, iteration order, credentials, graph generation IDs, wall-time/RSS guards, and output destinations. Normalize separators, case only where domain is case-insensitive, and default values consistently. Identities MUST remain stable across checkout location and repeated runs.

Graph storage IDs MAY include a generation ID, but `semantic_id` MUST remain generation-independent.

Deterministic output requires: sorted discovery, facts, relationships, dependencies, and diagnostics; stable deduplication keys; canonical JSON serialization; explicit ordering properties for semantic sequences; and a test comparing normalized output from at least two identical runs.

## 13. Analysis Pipeline

Implement phases explicitly:

1. Validate and resolve the project root.
2. Discover modules and artifacts with evidence/confidence.
3. Load selected, changed, and deleted paths when incremental mode is active.
4. Check parser capabilities.
5. Parse each artifact into a normalized intermediate representation.
6. Build symbol and configuration indexes.
7. Resolve inheritance, references, includes, defaults, and overrides.
8. Extract domain facts.
9. Create cross-file relationships and ordered flows.
10. Deduplicate by stable identity.
11. Enforce budgets and remove orphan relationships after truncation.
12. Build dependency indexes for incremental closure.
13. Compute coverage and bounded diagnostics.
14. Return an immutable result.
15. Persist or preview only after the result is valid.

### 13.1 Pipeline skeleton

```python
def run_<tool_name>_analysis(
    *,
    root: str,
    project_id: str,
    project_name: str = "",
    languages: Sequence[str] = (),
    selected_paths: Optional[Sequence[str]] = None,
    deleted_paths: Sequence[str] = (),
    budgets: Optional[ResourceBudgets] = None,
) -> AnalysisResult:
    project_root = Path(root).resolve()
    effective_budgets = budgets or ResourceBudgets()

    if not project_root.is_dir():
        return invalid_root_result(project_root, project_id, project_name)

    inventory = discover_project(project_root, selected_paths, effective_budgets)
    capabilities = detect_parser_capabilities(languages)
    parsed = parse_inventory(inventory, capabilities, effective_budgets)
    resolved = resolve_semantics(parsed, project_id, project_name, effective_budgets)

    facts = dedupe_and_sort_facts(resolved.facts)
    relationships = dedupe_sort_and_validate_relationships(resolved.relationships, facts)
    diagnostics = bound_and_sort_diagnostics(resolved.diagnostics, effective_budgets)
    coverage = compute_coverage(inventory, capabilities, diagnostics, resolved)

    return AnalysisResult(
        project_id=project_id,
        project_name=project_name or project_id,
        root=str(project_root),
        modules=inventory.modules,
        artifacts=inventory.artifacts,
        parser_capabilities=capabilities,
        semantic_facts=facts,
        relationships=relationships,
        dependency_index=resolved.dependency_index,
        diagnostics=diagnostics,
        coverage_status=coverage,
        missing_anchor_count=resolved.missing_anchor_count,
        ambiguity_count=resolved.ambiguity_count,
        truncation_count=resolved.truncation_count,
    )
```

## 14. Cross-File Resolution

Create a resolution matrix before implementation.

| Source | Target | Join key | Precedence | Ambiguity behavior | Missing behavior |
| --- | --- | --- | --- | --- | --- |
| `<config mapping>` | `<class/method>` | `<qualified name>` | `<rule>` | diagnostic + candidates | unresolved diagnostic |
| `<handler>` | `<ordered stack>` | `<name/inheritance>` | `<rule>` | deterministic tie-break | fallback/default |
| `<outcome>` | `<view/redirect>` | `<normalized target>` | `<rule>` | diagnostic | external target fact or unresolved |
| `<component>` | `<validation config>` | `<class/method/field>` | `<rule>` | diagnostic | no fabricated edge |

- Normalize keys once and reuse the same function in parsers and resolvers.
- Record source precedence (local override over inherited default).
- Preserve all candidates when ambiguity matters; never choose based on filesystem order.
- Bound recursive includes, inheritance, stack expansion, and dependency closure.
- Detect cycles and report them with the complete relevant path when feasible.

## 15. Dependency Index and Incremental Analysis

Incremental mode is a semantic feature, not only a file filter. The dependency index SHOULD map: files to imported/included files, configuration keys to consumers, symbols to referencing symbols, entry points to handlers and outcomes, and domain entities to affected relationships.

CLI inputs: `--incremental`, `--changed-files-manifest`, `--deleted-files-manifest`, `--commit-sha-before`, `--commit-sha-after`.

- Manifest paths MUST be normalized and root-contained.
- Changed files MUST expand through the dependency closure required for correctness.
- Deleted files MUST create tombstones or graph-cleanup work; a diagnostic alone is insufficient.
- Incremental output for an affected module MUST match the corresponding portion of a clean full run.
- Cache hits MUST not bypass dependency or deletion logic.

## 16. Resource Budgets

Bound hostile, malformed, and unexpectedly large projects.

```python
@dataclass(frozen=True)
class ResourceBudgets:
    max_source_bytes: int = <value>
    max_total_source_bytes: int = <value>
    max_parse_depth: int = <value>
    max_include_depth: int = <value>
    max_diagnostics_per_file: int = <value>
    max_diagnostics_per_project: int = <value>
    max_artifacts_per_project: int = <value>
    max_facts_per_project: int = <value>
    max_relationships_per_project: int = <value>
    max_dependency_entries: int = <value>
    max_wall_time_seconds: float = <value>
    max_peak_rss_bytes: int = <value>
```

Choose values from fixtures and realistic project measurements; do not copy another tool's numbers blindly.

Distinguish:
- **Accepted-output budgets**: may change the semantic result and belong in cache/generation fingerprints.
- **Operational abort guards** (wall time, peak RSS): decide success but should not alter successful semantic identity.

When a budget is reached: emit a stable diagnostic, increment `truncation_count`, set coverage to `partial`, preserve deterministic prefix/selection behavior, and remove relationships whose generated endpoints were truncated.

## 17. Diagnostics and Coverage

### 17.1 Coverage states

| State | Meaning |
| --- | --- |
| `empty` | No relevant supported inputs were found. |
| `complete` | Mandatory capabilities available and no known truncation/error prevented promised coverage. |
| `partial` | Relevant inputs exist, but a parser, feature, anchor, budget, or error prevented full promised coverage. |

Warnings do not automatically imply `partial`; define which warning codes represent real coverage loss.

### 17.2 Required diagnostic groups

- Invalid root or outside-root path
- Read/decode/parse failure
- Mandatory or optional parser unavailable
- Missing include/import/reference
- Ambiguous reference
- Inheritance/include cycle
- Unsupported but detected feature
- Budget truncation or operational abort
- Incremental deletion/cleanup failure
- Persistence/provider failure

Diagnostics MUST be deduplicated and sorted by a stable tuple: `(file_path, start_line, code, message)`.

## 18. Security and Data Hygiene

- Use safe parser modes; disable XML external entities and network resolution.
- Resolve every user/config-provided path and enforce project-root containment.
- Do not follow untrusted symlinks outside the root.
- Never execute analyzed code, build scripts, plugins, macros, or templates.
- Treat archives and binaries as unsupported unless a bounded, explicit parser is required.
- Redact secrets (passwords, tokens, credentials, private keys, authorization headers, connection strings) from fact properties, diagnostics, previews, logs, caches, and graph rows.
- Do not print provider credentials or full command environments.
- Bound recursion, token counts, decompression, regex work, and collection sizes.
- Use parameterized graph queries and validated relationship/label allowlists.

## 19. Cache Contract

Cache keys SHOULD include: project-relative file path, content digest, parser version and parser ABI/package version, accepted-output budget fingerprint, and tool-specific semantic options.

Cache keys MUST exclude: absolute checkout root, output paths, credentials/provider endpoints, wall-time and RSS abort guards, timestamps, and random values.

Expose `--cache-dir` and `--ignore-cache` when caching is implemented. Corrupt or incompatible cache entries MUST be ignored safely and diagnosed at most once per relevant scope.

## 20. CLI Contract

Use `argparse.ArgumentParser(..., allow_abbrev=False)`.

### 20.1 Core arguments

| Argument | Requirement |
| --- | --- |
| `--root` | Required project root |
| `--project-id` / `--project_id` | Stable project scope; environment fallback allowed |
| `--project-name` / `--project_name` | Human-readable name |
| `--language` or `--languages` | Only when multiple parser paths exist |
| `--repo` | Optional repository metadata |
| `--build-system` / `--build_system` | Optional build metadata |
| `--selected-path` | Repeatable narrow scan input when supported |
| `--ignore-file` | Optional explicit custom ignore contract |
| `--output` or tool-specific preview output | Deterministic JSON output |
| `--diagnostics-output` | Machine-readable diagnostics |
| `--dry-run` | Analyze without persistence |
| `--quiet` / `--verbose` | Stable operational logging |

### 20.2 Incremental and cache arguments

`--incremental`, `--changed-files-manifest`, `--deleted-files-manifest`, `--commit-sha-before`, `--commit-sha-after`, `--cache-dir`, `--ignore-cache`

### 20.3 Persistence arguments

Reuse the shared graph-provider arguments and `--require-neo4j` contract. Accept provider/vector/message arguments required by shared invocations even when the tool deliberately does not use a corresponding output, and document that decision.

### 20.4 Failure policy

```text
--fail-on error       nonzero when an error diagnostic exists
--fail-on partial     nonzero when coverage is not complete
--fail-on truncation  nonzero when truncation_count > 0
```

Document stable exit codes. Argument errors, analysis-policy failures, and persistence failures SHOULD be distinguishable.

## 21. Output and Graph Contract

### 21.1 JSON result

The preview output MUST include: project identity and normalized root, parser/tool version, module and artifact inventory, parser capabilities, semantic facts and relationships, dependency index, diagnostics, and coverage and quality counters. The same logical result MUST serialize identically across checkout roots after root normalization.

### 21.2 Graph node fields

Every generated fact SHOULD provide: `id` (storage identity), `semantic_id`, `symbol_id`, `generation_id`, `kind`, `name`, `framework`, `language`, `project_id`, `project_name`, `module_id`, source path and span, `confidence`, `extraction_method`, `resolution_status`, `parser_version`, and redacted graph-safe properties.

### 21.3 Graph relationship fields

Every relationship SHOULD provide: stable semantic and generation-aware storage identities, `from_id`, `to_id`, source/target labels and type, project/module/generation scope, source evidence, confidence, resolution status, reason, and typed redacted properties.

### 21.4 Persistence invariants

- Batch writes and use the repository's provider abstraction.
- Keep parsing/provider selection independent.
- Validate labels and relationship types against allowlists.
- Do not write orphan relationships.
- A rerun of the same generation is idempotent.
- Publish a generation only after all required writes succeed.
- Failed generations MUST not replace the last valid snapshot.
- Deleted semantic entities MUST be removed or tombstoned deterministically.

## 22. Public API

`__init__.py` SHOULD expose only the stable surface.

```python
PARSER_VERSION = "<tool_name>-v<version>"


def run_<tool_name>_analysis(**kwargs) -> AnalysisResult:
    from tools.<tool_name>.pipeline import run_<tool_name>_analysis as run
    return run(**kwargs)


__all__ = ["PARSER_VERSION", "AnalysisResult", "run_<tool_name>_analysis"]
```

Internal parser and resolver helpers remain private until another in-repository consumer demonstrates a stable reuse requirement.

## 23. Testing Strategy

### 23.1 Minimum test matrix

| Area | Required assertions |
| --- | --- |
| Imports | Package imports without graph/vector services or credentials |
| Scanner | Ignored dirs/files, glob/case behavior, ordering, symlinks, selected paths |
| Detector | Modules/artifacts/evidence/confidence |
| Parser | Valid, malformed, empty, oversized, alternate encoding |
| Resolver | Precedence, inheritance, includes, cycles, ambiguity, missing anchors |
| Semantic flow | Entry point through ordered processing to outcome |
| Graph contract | Node/edge fields, directions, endpoint existence, safe properties |
| Determinism | Equal normalized output across repeated runs and checkout roots |
| Incremental | Changed closure and deleted cleanup equal full-run semantics |
| Cache | Hit/miss/invalidation/corruption and version changes |
| Budgets | Deterministic truncation, diagnostic, partial coverage, no orphan edges |
| Security | Outside-root paths, XXE/network denial, secret redaction |
| CLI | Required args, aliases, help, dry-run, outputs, fail-on exit codes |
| Provider isolation | Analysis behavior does not branch on selected graph provider |
| Fixture | Representative project produces expected semantic graph |

### 23.2 Fixture requirements

The primary fixture SHOULD contain: one minimal happy-path flow, one inherited/default configuration, one local override, one cross-file reference, one ordered chain, one missing or ambiguous reference, one malformed optional artifact, one unsupported-but-detected feature, and one ignored build/generated copy to prevent duplicate scanning. Prefer small hand-authored fixtures over copied production repositories.

### 23.3 Contract assertions

Tests MUST assert semantic meaning, not only counts.

```python
self.assertIn(("EntryPoint", "MAPPED_TO", "Handler"), triples)
self.assertEqual(["step-a", "step-b", "step-c"], ordered_step_names)
self.assertEqual("partial", result.coverage_status)
self.assertTrue(any(item.code == "<tool>.resolution.missing" for item in result.diagnostics))
self.assertEqual(result_a.to_dict(), result_b.to_dict())
```

## 24. README Template

Each tool README MUST contain: purpose and supported semantics, supported and unsupported features, parser strategy and required/optional capabilities, artifact types, scan exclusions and custom ignore behavior, public Python API, CLI example and important flags, output/coverage meaning, incremental/cache behavior, test command and fixture location, and known limitations and future extensions.

```bash
PYTHONPATH=code-tiny python -m tools.<tool_name>.<tool_name>_analyzer \
  --root /path/to/project \
  --project-id example-project \
  --project-name "Example Project" \
  --dry-run \
  --output /tmp/<tool_name>-analysis.json
```

## 25. Implementation Phases

### Phase 1: Foundation
Package layout and public API, immutable models and stable identities, detector and bounded scanner, parser capability reporting, CLI dry-run and deterministic JSON.

### Phase 2: Core parsers
Mandatory language/config parsers, source spans and bounded diagnostics, normalized intermediate representations, parser unit fixtures.

### Phase 3: Semantic resolution
Indexes and precedence rules, cross-file references, ordered execution flows, missing/ambiguous/cycle handling.

### Phase 4: Graph and operations
Graph contract and provider adapter, generation-safe persistence, dependency index and incremental cleanup, cache, budgets, redaction, and automation failure policy.

### Phase 5: Hardening
Determinism and checkout-independence, malformed/large/untrusted input, performance measurement, complete documentation and acceptance evidence.

Each phase MUST end with passing focused tests. Do not defer correctness contracts (stable IDs, path normalization, diagnostics) until the final phase.

## 26. Definition of Done

### Design
- [ ] Purpose, scope, non-goals, and success criteria are specific.
- [ ] Artifact inventory and parser strategy are complete.
- [ ] Entity and relationship tables define identity and direction.
- [ ] Cross-file precedence, ambiguity, missing, and cycle rules are documented.
- [ ] Unsupported detected features have an explicit coverage policy.

### Implementation
- [ ] Package follows the smallest justified layout.
- [ ] Scanner has separate ignored directory/file patterns and deterministic ordering.
- [ ] Root containment, size limits, and safe parser modes are enforced.
- [ ] Shared language parsers are reused.
- [ ] Core analysis runs without external services.
- [ ] Facts, relationships, diagnostics, and dependencies are immutable and sorted.
- [ ] Stable IDs are checkout- and generation-independent.
- [ ] Secrets are redacted before serialization or persistence.
- [ ] Coverage and quality counters are accurate.

### Incremental and persistence
- [ ] Changed-file dependency closure is correct.
- [ ] Deleted entities are cleaned up or tombstoned.
- [ ] Graph writes are batched, idempotent, and generation-safe.
- [ ] No orphan relationships can be persisted.
- [ ] Provider selection does not change analysis semantics.

### Verification
- [ ] Scanner, parser, resolver, graph-contract, and CLI tests pass.
- [ ] Representative fixture reconstructs the promised end-to-end flow.
- [ ] Repeated runs and different checkout roots produce equal normalized output.
- [ ] Incremental results match full-run semantics for affected modules.
- [ ] Budget, malformed-input, and security tests pass.
- [ ] README commands execute successfully.
- [ ] `git diff --check` passes and only intended files changed.

## 27. Design Review Record

| Review question | Decision/evidence |
| --- | --- |
| What semantic questions can this tool answer? | `<answer>` |
| Which existing parsers and common utilities are reused? | `<answer>` |
| Which behaviors remain partial or unsupported? | `<answer>` |
| How are stable IDs formed? | `<answer>` |
| How are ordered flows preserved? | `<answer>` |
| How are ambiguity and missing anchors represented? | `<answer>` |
| What are the scan exclusions and why are they safe? | `<answer>` |
| What are the accepted-output and operational budgets? | `<answer>` |
| How does incremental deletion work? | `<answer>` |
| What proves determinism and graph integrity? | `<tests/evidence>` |

## 28. Reference Basis

Derived from these local design and implementation patterns:

- `Struts_Analyzer_Design_Spec_v2.md`: semantic-first architecture, source artifacts, framework entities, ordered request flow, cross-file resolution, parser reuse, MVP, success criteria.
- `code-tiny/tools/struts/`: XML-first semantic pipeline, resolver separation, deterministic result model, explicit scan filtering.
- `code-tiny/tools/servlet_jsp/`: parser capabilities, resource budgets, stable/generation identities, dependency indexes, redaction, coverage counters, incremental operation, automation failure policies.
- `code-tiny/tools/spring/`: detector/adapters/extractors, safe relative paths, incremental manifests, semantic deduplication.
- `code-tiny/tools/mybatis/`: rich intermediate models, parser capability contracts, analyzer configuration, CLI parity.
- `code-tiny/tools/java/`: shared language parsing and glob-aware source scanning.

When this template conflicts with a newer repository-wide contract, follow the newer verified contract and update this template in the same change.
