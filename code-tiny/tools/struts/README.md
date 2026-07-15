# Struts 2 Analyzer

`tools.struts` implements the XML-first MVP from `Struts Analyzer Design Specification v2`.

It currently reconstructs:

- Struts filter declarations and mappings from `web.xml`
- packages, inheritance, constants, actions, interceptors, stacks, results, result types, and exception mappings from Struts XML
- recursively included Struts configuration files
- ordered effective interceptor chains, including nested stack references and per-reference parameters
- HTTP action routes, wildcard markers, view targets, redirects, and chained actions
- field/action validators from `*-validation.xml` and Java `validate()` hooks through the shared Tree-sitter Java runtime
- deterministic semantic graph facts and relationships suitable for downstream writers

Convention Plugin annotations and classpath-scanned action routes are detected but not resolved in this MVP. The result is marked `partial` when the Convention Plugin is present.

## Scan filtering

The recursive scanner prunes directories that cannot contain source inputs needed by the analyzer, including version-control metadata, IDE metadata, Java build outputs, generated sources, dependency directories, caches, test reports, virtual environments, and temporary directories. Hidden directories are skipped as well.

It also ignores compiled Java archives, IDE metadata files, logs, temporary files, editor backups, and operating-system metadata. Directory and file exclusions support glob patterns and are matched case-insensitively for consistent behavior across platforms.

Run it with:

```bash
PYTHONPATH=. python -m tools.struts.struts_analyzer \
  --root /path/to/struts/project \
  --project-id legacy-app \
  --output /tmp/legacy-app-struts.json
```

The public Python API is `tools.struts.run_struts_analysis(...)`.
