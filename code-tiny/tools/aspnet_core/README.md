# ASP.NET Core Analyzer

Detector-gated semantic overlay for ASP.NET Core hosting, middleware, endpoint
routing, controllers, Razor Pages/views, Minimal APIs, DI, and appsettings.
Canonical C# facts remain owned by the `csharp` analyzer.

## Quick Start

```bash
python -m tools.aspnet_core.aspnet_core_analyzer \
  --root /path/to/repository \
  --project-id example \
  --dry-run \
  --aspnet-core-preview-output /tmp/aspnet-core.json
```

## Usage

`--semantic auto` attempts a Roslyn workspace and degrades to syntax evidence
with partial coverage. `--semantic on` requires workspace semantics;
`--semantic off` uses syntax evidence only. Incremental manifests select whole
affected modules so middleware, routes, and service registrations retain their
module-level ordering and context.

Supported source artifacts include C#, SDK project metadata, `.cshtml`,
`.razor`, and `appsettings*.json`. JSON input is bounded and duplicate keys are
diagnosed. Secret-like configuration values are redacted before every output.

## Limitations

Dynamic route expressions, custom fluent wrappers, reflection-based DI,
runtime endpoint data sources, generated Razor output, and executed tag-helper
behavior remain explicit partial or unresolved evidence.
