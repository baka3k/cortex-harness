# Servlet/JSP semantic analyzer

This analyzer adds a Servlet/JSP semantic overlay to the canonical Java graph. It discovers web modules, merges `web.xml` and `@WebServlet`/`@WebFilter`/`@WebListener` declarations, reconstructs endpoint/filter/handler/view flow, and indexes JSP, EL, lifecycle, state, security, welcome, and error-page semantics.

The Java analyzer remains the owner of `File`, `Class`, `Function`, and `CALLS`. Servlet/JSP facts link to those nodes with `SEMANTIC_OF`; v1 does not create a separate JSP vector collection.

## Inspect and preview

Run a semantic-only, graphless preview:

```bash
python tools/servlet_jsp/servlet_jsp_analyzer.py \
  --root /path/to/project \
  --project-id example \
  --dry-run
```

The command prints an ASCII-safe JSON summary containing `coverage_status`, stage, create/update/delete/preserve counts, `baseline_advanced`, and the preview path. Preview artifacts are never accepted as graph-applied incremental baselines.

Use `--diagnostics-output diagnostics.json` for machine-readable diagnostics, `--quiet` for no successful output, and one of these CI gates:

- `--fail-on error` exits 4 when an error diagnostic exists.
- `--fail-on partial` exits 5 unless coverage is complete.
- `--fail-on truncation` exits 6 when any deterministic budget truncates work.

## Build Java plus the overlay

For a complete direct graph run, use the Java-first wrapper:

```bash
python tools/servlet_jsp/servlet_jsp_java_analyzer.py \
  --root /path/to/project \
  --project-id example \
  --neo4j-uri bolt://localhost:7687 \
  --neo4j-user neo4j \
  --neo4j-password secret \
  --require-neo4j 1
```

The wrapper runs Java exactly once, then runs the semantic overlay. Incremental sync invokes the primary Java analyzer and semantic overlay separately, so it uses `servlet_jsp_analyzer.py` directly.

## Apply, validate, and retry

Each module is written into an isolated shadow generation. The analyzer computes the immutable snapshot checksum, stages all module facts and occurrence relationships, validates counts, then atomically advances `ServletJspAnalysisState.active_generation`. Only after promotion does it publish the checksummed applied snapshot; failed staging therefore leaves both the previous generation and its baseline authoritative. Inactive generations are removed only after promotion.

Incremental runs validate the prior applied snapshot, expand changed and deleted paths through its dependency index, and promote complete replacements only for affected modules. Missing, corrupt, or unmatched baseline state triggers a safe full rebuild instead of best-effort deletion. Pass `--ignore-cache` to request that full rebuild explicitly.

Normal recovery is to fix the reported graph or parser issue and rerun the same command. A graphless or dry-run execution writes only a preview and never advances the applied baseline. If the Java-first wrapper stops after Java succeeds, rerun the semantic-only command; Java does not need to be repeated.

Run `scripts/setup_constraints.py` during migration. Servlet/JSP discovery uses the versioned `mcp_symbol_text_ft_v2` and `mcp_symbol_code_ft_v2` indexes; consumers filter framework nodes through the active generation state.

## Supported and bounded semantics

Supported inputs include Java Servlet APIs in both `javax` and `jakarta` namespaces, `web.xml`, classic JSP, JSPX, JSP fragments, JSTL-style tags, EL references, forms/links/resources, Java and JSP dispatch operations, scoped state/cookies, lifecycle callbacks, and descriptor security/configuration targets.

The analyzer does not execute Java, JSP, EL, containers, reflection, or programmatic runtime registration. Dynamic URL/dispatch/state values remain explicit unresolved facts. Annotation-only filter order remains unknown. Root-confined reads reject traversal and symlink escapes, XML parsing does not fetch external entities, sensitive property values are redacted, and deterministic byte/token/fact/relationship/diagnostic budgets surface partial coverage instead of silently dropping work. Wall-time and peak-RSS guards abort before graph application rather than accepting environment-dependent partial output.
