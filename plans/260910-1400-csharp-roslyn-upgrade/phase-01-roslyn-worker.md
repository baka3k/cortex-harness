# Phase 01 — Roslyn Worker Contract and Proof of Concept

## Goal
Tạo `CSharpRoslynWorker` .NET project — một subprocess tool nhận manifest JSON, load C# files qua Roslyn, và emit structured JSON evidence.

## Template Source
**Model trên**: `code-tiny/tools/common/aspnet/roslyn_worker/Program.cs` (460 lines)
- Already uses `CSharpCompilation.Create()` with `TrustedPlatformReferences`
- Already handles manifest JSON protocol, per-file evidence extraction
- Protocol: `--manifest <request.json>` → JSON stdout
- Evidence format: types, members, invocations, attributes, diagnostics
- **Extend** (not rewrite) this pattern for primary C# analysis needs

## Scope

### Worker Project Structure
```
code-tiny/tools/csharp/roslyn_worker/
├── CSharpRoslynWorker.csproj    # Target net8.0;net9.0, refs Microsoft.CodeAnalysis.CSharp.Workspaces
├── Program.cs                    # CLI entry, manifest parsing, JSON output
├── CompilationService.cs         # Workspace/syntax mode compilation
├── MemberExtractor.cs            # Full member inventory extraction
├── SemanticExtractor.cs          # Resolved symbols, inheritance, generics
├── AttributeExtractor.cs         # Attribute/annotation extraction
├── XmlDocExtractor.cs            # XML documentation comment parsing
├── CallResolver.cs               # Resolved call targets (not just name-based)
└── ProtocolModels.cs             # JSON contract DTOs
```

### Protocol Contract

**Request (stdin/manifest JSON):**
```json
{
  "protocol_version": "csharp-v1",
  "root": "/absolute/path/to/project",
  "files": ["relative/path/File1.cs", "relative/path/File2.cs"],
  "semantic_mode": "auto",
  "project_path": "relative/path/Project.csproj",
  "workspace_timeout_ms": 120000,
  "file_timeout_ms": 60000,
  "max_file_bytes": 2097152,
  "extract_members": true,
  "extract_semantic": true,
  "extract_attributes": true,
  "extract_xml_docs": true,
  "extract_calls_resolved": true
}
```

**Response (stdout JSON):**
```json
{
  "protocol_version": "csharp-v1",
  "coverage_status": "full|partial|syntax_only|empty",
  "workspace_kind": "solution|project|syntax",
  "semantic_enabled": true,
  "results": [
    {
      "file_path": "relative/path/File1.cs",
      "ok": true,
      "evidence": {
        "namespaces": [...],
        "types": [
          {
            "name": "MyClass",
            "qualified_name": "MyNamespace.MyClass",
            "kind": "class",
            "canonical_symbol_id": "MyNamespace.MyClass/1@File1.cs",
            "base_types": ["System.Object"],
            "implemented_interfaces": ["IMyInterface"],
            "type_parameters": ["T"],
            "type_constraints": {"T": ["class", "new()"]},
            "is_abstract": false,
            "is_sealed": false,
            "is_static": false,
            "is_partial": false,
            "is_record": false,
            "accessibility": "public",
            "attributes": ["Serializable"],
            "xml_doc": "<summary>...</summary>",
            "start_line": 10,
            "end_line": 50
          }
        ],
        "members": [
          {
            "name": "MyMethod",
            "qualified_name": "MyNamespace.MyClass.MyMethod",
            "kind": "method",
            "canonical_symbol_id": "MyNamespace.MyClass::MyMethod/2@File1.cs",
            "return_type": "System.Threading.Tasks.Task<System.String>",
            "type_parameters": [],
            "is_async": true,
            "is_static": false,
            "is_virtual": true,
            "is_override": false,
            "is_abstract": false,
            "is_extension_method": false,
            "accessibility": "public",
            "attributes": ["HttpGet"],
            "parameters": [
              {"name": "id", "type_name": "System.Int32", "is_optional": false}
            ],
            "xml_doc": "<summary>...</summary>",
            "start_line": 20,
            "end_line": 35
          },
          {
            "name": "MyProperty",
            "kind": "property",
            "type_name": "System.String",
            "accessibility": "public",
            "has_getter": true,
            "has_setter": true,
            "is_auto_property": true,
            "is_static": false,
            "is_virtual": false,
            "attributes": ["Required"],
            "start_line": 15,
            "end_line": 15
          },
          {
            "name": "_myField",
            "kind": "field",
            "type_name": "System.Int32",
            "accessibility": "private",
            "is_static": false,
            "is_const": false,
            "is_readonly": true,
            "start_line": 12,
            "end_line": 12
          }
        ],
        "events": [...],
        "delegates": [...],
        "calls": [
          {
            "caller_id": "MyNamespace.MyClass::MyMethod/2@File1.cs",
            "callee_name": "GetDataAsync",
            "callee_id": "MyNamespace.DataService::GetDataAsync/1@DataService.cs",
            "callee_arity": 1,
            "resolved": true,
            "is_async": true
          }
        ],
        "usings": [
          {"alias": "System.Collections.Generic", "is_static": false, "resolved": true}
        ]
      }
    }
  ],
  "project_metadata": {
    "project_path": "relative/path/Project.csproj",
    "target_framework": "net8.0",
    "sdk": "Microsoft.NET.Sdk.Web",
    "output_type": "Exe",
    "nuget_packages": [
      {"name": "Microsoft.EntityFrameworkCore", "version": "8.0.0", "is_development": false}
    ],
    "project_references": ["../Shared/Shared.csproj"]
  },
  "diagnostics": [
    {"file_path": "...", "severity": "warning", "message": "..."}
  ],
  "semantic_errors": []
}
```

### Compilation Modes

| Mode | Trigger | Capability |
| --- | --- | --- |
| Workspace | .sln/.csproj found + loadable | Full semantic: resolved types, calls, inheritance |
| Syntax | No project or workspace load fails | Syntax tree only: names, structure, no resolution |
| Partial | Workspace loaded but some files fail | Mixed: resolved where possible, syntax-only for failures |

### Implementation Steps

1. **T01**: Tạo `CSharpRoslynWorker.csproj` với dependencies:
   - `Microsoft.CodeAnalysis.CSharp` (4.x)
   - `Microsoft.CodeAnalysis.CSharp.Workspaces`
   - `Microsoft.CodeAnalysis.Workspaces.MSBuild` (optional, for workspace mode)
   - `System.Text.Json`

2. **T02**: Implement `Program.cs` — CLI entry point:
   - Parse `--manifest <path>` argument
   - Load manifest JSON
   - Select compilation mode
   - Dispatch to extractors
   - Output JSON to stdout

3. **T03**: Implement `CompilationService.cs`:
   - Workspace mode: load .sln/.csproj via MSBuild workspace
   - Syntax mode: parse individual files with `CSharpSyntaxTree`
   - Timeout enforcement per file and per workspace
   - Diagnostic collection

4. **T04**: Implement `MemberExtractor.cs`:
   - Walk `SemanticModel` or `SyntaxTree`
   - Extract all member types: methods, properties, fields, events, delegates
   - Capture accessibility, modifiers, type info
   - Handle partial classes (merge in workspace mode)

5. **T05**: Implement `SemanticExtractor.cs`:
   - Resolved base types and interfaces
   - Generic type parameters and constraints
   - Type kind detection (class, struct, interface, enum, record)
   - `INamedTypeSymbol` navigation

6. **T06**: Implement `AttributeExtractor.cs`:
   - Extract attribute names (resolved, without "Attribute" suffix)
   - Capture attribute arguments where constant
   - Associate with target (type, member, parameter)

7. **T07**: Implement `XmlDocExtractor.cs`:
   - Parse `///` XML documentation comments
   - Extract summary, params, returns, remarks
   - Associate with declaring symbol

8. **T08**: Implement `CallResolver.cs`:
   - Resolve `IMethodSymbol` for invocations
   - Map to canonical_symbol_id when target is in analyzed files
   - Mark resolved=true/false
   - Detect async calls, virtual dispatch

9. **T09**: Implement `ProtocolModels.cs`:
   - DTO classes matching JSON contract
   - Serialization with `System.Text.Json`

10. **T10**: Worker tests:
    - Build test (dotnet build -c Release)
    - Protocol test (valid manifest → valid JSON)
    - Workspace mode test (small .csproj)
    - Syntax mode test (standalone .cs)
    - Timeout test
    - Malformed input test
    - Empty file list test

## Acceptance Criteria

- Worker builds with `dotnet build -c Release` on net8.0+
- Protocol version is deterministic and validated
- Workspace mode resolves types, calls, inheritance for simple projects
- Syntax mode works without project files
- Output JSON matches contract schema
- Timeouts are enforced (workspace + per-file)
- No source code is emitted in output (security)
- Worker handles encoding edge cases (UTF-8 BOM, CP932)

## Dependencies

- .NET SDK 8.0+ must be available on host
- Fallback: if dotnet unavailable, Python adapter catches OSError and signals fallback
