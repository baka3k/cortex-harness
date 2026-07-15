# ASP.NET Framework Analyzer

Detector-gated semantic overlay for ASP.NET Framework MVC/Web API, Web Forms,
Global.asax, System.Web modules/handlers, legacy views, configuration, and
resources. Canonical C# facts remain owned by the `csharp` analyzer.

## Quick Start

```bash
python -m tools.aspnet_framework.aspnet_framework_analyzer \
  --root /path/to/repository \
  --project-id example \
  --dry-run \
  --aspnet-framework-preview-output /tmp/aspnet-framework.json
```

## Usage

`--semantic auto` attempts a Roslyn workspace and degrades to syntax evidence
with partial coverage. `--semantic on` requires workspace semantics;
`--semantic off` uses syntax evidence only. Incremental manifests select whole
affected modules so module-level output remains internally consistent.

Supported source artifacts include `.cs`, Global.asax, `.aspx`, `.ascx`,
`.master`, `.asmx`, `.ashx`, `web.config`, `packages.config`, `.resx`, and
legacy Razor views. XML DTD/entity declarations are rejected and secret-like
configuration values are redacted before serialization or graph writes.

## Limitations

Runtime route registration, reflection, generated Web Forms fields, custom
build providers, machine/site configuration not present in the checkout, and
executed template behavior remain explicit partial or unresolved evidence.
