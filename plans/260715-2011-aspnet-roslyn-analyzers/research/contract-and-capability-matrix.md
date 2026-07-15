---
type: contract and capability report
date: 2026-07-15
---

# ASP.NET Analyzer Contract and Capability Matrix

## Summary

The analyzers are detector-gated semantic overlays on the canonical `csharp`
analyzer. They never own or replace `.cs` nodes. Compiler-backed facts link to
canonical C# coordinates with `SEMANTIC_OF`; syntax/configuration evidence that
cannot be resolved remains `partial`, `unresolved`, or `dynamic`.

## Frozen Contract

| Area | Decision |
| --- | --- |
| Protocol | `aspnet-roslyn-v1`; one JSON manifest request and one JSON response |
| Worker | .NET 8 console worker; `Microsoft.CodeAnalysis.CSharp.Workspaces` and `Microsoft.Build.Locator` pinned in the project file |
| Semantic modes | `off` = syntax only; `auto` = workspace then syntax-only partial; `on` = workspace required and failure is an error |
| Workspace selection | Explicit project first, otherwise lexically first solution, then project; loose files use syntax mode |
| Target frameworks | Deterministic project order; evidence includes project and target metadata; duplicate symbols dedupe by stable coordinate |
| C# ownership | `csharp` remains the sole `.cs` owner; ASP.NET facts use canonical relative path, qualified symbol, kind, and span anchors |
| Stable IDs | SHA-256 over project, module, framework, kind, and semantic coordinates; checkout roots never participate |
| Serialization | Sorted, deduplicated, ASCII JSON with stable separators; emitted root is `.` |
| Detection | Per module; strong evidence is required. Mixed repositories may activate both overlays in different modules |
| Ambiguity | Conflicting Core/Framework evidence emits a diagnostic and preserves both evidence sets; directory order never decides |
| Graph | PascalCase node labels and uppercase relationship names from the shared migration model |
| Ordering | Middleware, filters, modules, lifecycle events, and route sequences use explicit zero-based `position` properties |
| Redaction | Secret-like keys and connection-string credentials are redacted before diagnostics, previews, caches, or graph rows |
| XML | `xml.etree.ElementTree` over bounded text; DTD/entity declarations are rejected before parsing |
| JSON | Bounded UTF-8 input; duplicate-key diagnostics; malformed input is partial, not fatal to unrelated artifacts |
| Execution | No project build, generator, template execution, runtime instrumentation, network fetch, or analyzed application launch |

## Detection Matrix

| Overlay | Strong evidence | Supporting evidence | Deleted strong candidates |
| --- | --- | --- | --- |
| ASP.NET Core | `Microsoft.NET.Sdk.Web`, `Microsoft.AspNetCore.App`, ASP.NET Core package reference | Program/Startup patterns, Razor files, `appsettings*.json` | web project, Razor file, appsettings file, Program/Startup source |
| ASP.NET Framework | `System.Web`, legacy web project GUID/target, `web.config` with `system.web`, Global.asax, Web Forms markup | App_Start, packages.config, MVC/Web API references | `web.config`, Global.asax, `.aspx/.ascx/.master/.asmx/.ashx` |

Two supporting signals equal one activation signal. A strong signal activates
the module independently. Unrelated C# and plain SDK projects do not activate.

## Requirement-to-Evidence Matrix

| Requirement | Evidence owner | Output | Acceptance evidence |
| --- | --- | --- | --- |
| Hosting and application startup | Roslyn invocation/attribute evidence plus Program/Startup/Global.asax artifacts | `ApplicationEvent`, `Middleware`, `Service`, `INITIALIZES`, `INVOKES` | Core and Framework fixture startup paths |
| Routes and endpoints | Attributes, mapping invocations, route config, web.config handler mappings | `Route`, `HttpEndpoint`, `MAPPED_TO`, `HANDLED_BY` | Controller, Razor Page, Minimal API, MVC5/Web API fixture routes |
| Ordered pipeline | Invocation source order and declared configuration order | `PASSES_THROUGH` with `position`, `branch`, `terminal` | Repeated-run ordered path assertions |
| Controllers/actions/results | Compiler type/member evidence and conservative syntax fallback | `Controller`, `Action`, `Result`, `RETURNS_RESULT` | Attribute and conventional controller fixtures |
| Razor/views | Bounded directive and reference parser | `RazorPage`, `PageHandler`, `View`, `Layout`, `PartialView`, `RENDERS` | Layout/partial/page fixture assertions |
| Web Forms | Bounded XML-like directive/control/event parser | `WebFormPage`, state/event facts, `POSTS_BACK_TO`, `WRITES_SESSION` | Page/master/control/postback fixture assertions |
| DI and services | Service registration and constructor parameter evidence | `Service`, `Repository`, `INJECTS`, `DEPENDS_ON` | Core service registration fixture |
| Configuration | Safe XML/JSON flattening with provenance and redaction | `ConfigurationKey`, `READS_CONFIG`, auth/session facts | Secret-redaction and provenance assertions |
| Canonical C# anchors | Worker symbol coordinate or conservative file/span coordinate | `SEMANTIC_OF` | No duplicated canonical C# nodes |
| Incremental closure | Project/module/artifact dependency index | affected module/artifact sets | Full vs changed/deleted parity tests |

## Capability States

| Capability | MVP status | Partial/deferred behavior |
| --- | --- | --- |
| Roslyn C# syntax | MVP | Worker absence is an explicit unavailable capability |
| SDK-style Core workspace | MVP when SDK and restore assets are available | `auto` degrades to syntax-only partial |
| Legacy Framework workspace | Host-dependent | Missing reference assemblies produce partial coverage |
| Razor directives/layouts/partials | MVP bounded source parser | Generated Razor semantics and custom tag-helper execution are deferred |
| Web Forms directives/controls/events | MVP bounded source parser | Generated fields, custom build providers, runtime control trees are deferred |
| Constant route/middleware mappings | MVP | Dynamic/fluent wrappers remain dynamic/unresolved |
| Runtime DI/reflection | Deferred | Diagnostic only; no resolved edge is created |
| Machine/site configuration inheritance | Deferred unless files are in the checkout | Local provenance remains explicit |
| Source transformation | Non-goal | Analyzer emits migration semantics only |

## Resource and Failure Policy

| Budget | Default |
| --- | --- |
| Source/config/view file | 4 MiB / 1 MiB / 2 MiB |
| Diagnostics per file/project | 1,000 / 100,000 |
| Facts/relationships per project | 2,000,000 / 4,000,000 |
| Include/reference depth | 32 |
| Worker process/workspace/file timeout | 600 s / 120 s / 60 s |

Containment, malformed worker output, protocol mismatch, and `semantic=on`
workspace failure are errors. Optional malformed artifacts, unsupported dynamic
constructs, and `semantic=auto` workspace failure are partial diagnostics.
Truncation is deterministic and may be promoted to a CLI failure with
`--fail-on truncation`.

## Unresolved Gate

Live Neo4j/FalkorDB parity remains gated by the in-progress
`neo4j-to-falkordb-migration` plan. Query-shape and provider-neutral writer
tests are required now; live provider acceptance is not waived when either
service is unavailable.
