# COBOL analyzer security audit

date: 2026-07-14
mode: audit-fix
scope: `code-tiny/tools/cobol/*.py`
result: approved

## Summary

Files scanned: 10 production Python files. Findings after fixes: 0 Critical, 0 High, 0 Medium, 0 Low, 2 Informational. No secrets were detected. `uvx pip-audit --local` reported no known vulnerabilities, and `python -m pip check` reported no broken requirements.

The graph and memory MCP security indexes were unavailable, so the audit used direct source inspection and filesystem pattern checks as the skill-defined fallback.

## Fixed findings

1. Manifest traversal — STRIDE Tampering / OWASP A01. Relative paths such as `../outside.cbl` could enter the incremental manifest as strings. `_manifest_paths` now resolves every absolute/relative path and rejects anything outside the project root; a regression test covers the escape.
2. Qdrant path/target validation — STRIDE Tampering/SSRF / OWASP A03/A10. The configured URL and collection previously flowed into request paths without a COBOL-local validation boundary. Qdrant now requires an absolute HTTP(S) URL without embedded credentials and an allowlisted collection name.
3. Dynamic Cypher identifiers — STRIDE Tampering / OWASP A03. Review confirmed node labels are allowlisted with `^[A-Za-z][A-Za-z0-9_]*$`; relationship types originate only from internal constants. All values remain parameterized.

## Accepted informational boundaries

- `--cobol-language-library` loads a local native library by explicit operator choice. Preflight validates the exported symbol, ABI parse, and records SHA-256. This is documented as an executable-code trust boundary.
- Operator-selected embedding models may use model-defined code, consistent with the repository's existing Jina embedding path. This is not remotely user-controlled and is documented as an executable-code trust boundary.

## STRIDE / OWASP coverage

- Spoofing / A07: no authentication surface in this local analyzer.
- Tampering / A01/A03: manifest paths, graph labels, project scope, and Qdrant targets validated.
- Repudiation / A09: runtime provider, grammar ABI/checksum, diagnostics, and scan summaries are recorded.
- Information disclosure / A02: no secrets or credentials stored; embedded URL credentials are rejected.
- Denial of service / A04: bounded request timeouts and finite fixture performance guard; repository-scale parsing remains operator initiated.
- Elevation of privilege / A08: native/model executable-code boundaries require operator selection and are documented.
