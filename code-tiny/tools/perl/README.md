# Perl Tree-sitter analyzer

The Perl analyzer performs deterministic, syntax-level analysis for Perl 5 files ending in `.pl`, `.pm`, or `.t`. It extracts packages, named subroutines, `my`/`our`/`local` variables, `use`/`require`/`no` dependencies, direct and qualified calls, method/coderef calls, comments, and inline POD. It never executes Perl or module hooks.

Install the shared requirements, then run a service-free preview:

```bash
python -m tools.perl.perl_analyzer \
  --root /path/to/project \
  --project-id example \
  --dry-run --pretty
```

Use `--include-docs` to include bounded, redacted comments and POD. Incremental invocations accept `--incremental`, `--changed-files-manifest`, `--deleted-files-manifest`, `--cache-dir`, and `--ignore-cache`. Graph persistence uses the shared provider arguments and `LanguageCodeWriter`; without graph settings, the analyzer emits normalized JSON only.

Resolution is deliberately conservative. Exact project-local qualified calls, current-package calls, and unique statically imported calls may become `CALLS`. Method dispatch, coderefs, symbolic references, `eval`, dynamic `require`, `AUTOLOAD`, and ambiguous targets remain explicitly unresolved. Standalone `.pod` files and extensionless shebang scripts are not automatically owned.

The grammar is pinned to `tree-sitter-perl==1.2.1`. Normalized output reports the Tree-sitter runtime version, grammar package version, ABI, coverage, diagnostics, dependency indexes, and stable semantic IDs. Source, documentation, diagnostic, and cache output is bounded and common credential shapes are redacted.
