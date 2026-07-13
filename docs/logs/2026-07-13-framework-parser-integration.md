# Framework Parser Scan and MCP Integration — 2026-07-13

## Context

The framework integration plan required Spring, Servlet/JSP, and MyBatis facts to participate in the normal code scan and unified MCP flow. Although the work was blocked by the in-progress Neo4j-to-FalkorDB migration, the user explicitly approved overriding that dependency; the override and resulting compatibility boundary are recorded in `plans/260713-1638-framework-parser-integration/plan.md:8` and `plans/260713-1638-framework-parser-integration/reports/validation-report.md:6`.

## Change

The scan now keeps Java/Kotlin as canonical base-language owners and runs framework analyzers as ordered, non-exclusive overlays, as defined in `code-tiny/tools/sync/incremental_sync.py:59` and `code-tiny/tools/sync/incremental_sync.py:91`. Provider-neutral writers use the shared graph driver contract, including the MyBatis writer at `code-tiny/tools/graph/writer/mybatis_writer.py:57`. MCP framework aliases, labels, traversal relationships, searchable properties, and Servlet/JSP generation freshness are centralized in `code-tiny/mcp/framework_registry.py:12` and routed through the unified backend at `code-tiny/mcp/unified_mcp.py:168`.

Live FalkorDB validation exposed structured MyBatis properties that neither graph provider accepts directly. `graph_property_value()` now preserves primitive values and primitive arrays while encoding dictionaries and other structured values as deterministic JSON at `code-tiny/tools/mybatis/models.py:11`; both node and relationship serialization apply the normalization at `code-tiny/tools/mybatis/models.py:427` and `code-tiny/tools/mybatis/models.py:464`, with regression coverage at `tests/test_framework_graph_contract.py:34`.

## Impact

Impact level: medium. Code scans can now ingest all three framework overlays without transferring canonical symbol ownership away from the base-language analyzers, and MCP queries can search and traverse the resulting framework facts through one shared contract. Full and incremental isolated FalkorDB runs validated ingestion, deletion cleanup, update routing, and active Servlet/JSP generations (`plans/260713-1638-framework-parser-integration/reports/validation-report.md:19` and `plans/260713-1638-framework-parser-integration/reports/validation-report.md:36`). Remaining risk is bounded but explicit: live Neo4j parity and full cross-provider MCP execution are deferred until the provider migration completes (`plans/260713-1638-framework-parser-integration/reports/validation-report.md:69`).

## Decision

Base-language ownership plus framework overlays was chosen because Java, Kotlin, XML, and JSP artifacts can contribute to multiple framework models; assigning each file to a single framework parser would lose enrichment or duplicate canonical symbols. The implementation therefore preserves stable canonical nodes and links provider-neutral framework facts through semantic relationships. Narrow compatibility shims were preferred over expanding the unfinished graph migration. Live FalkorDB full and incremental behavior was accepted as the executable provider validation, while live Neo4j and cross-provider MCP parity remain an explicit exclusion rather than an unverified claim.

## References

- plan: `plans/260713-1638-framework-parser-integration/plan.md:1`
- validation: `plans/260713-1638-framework-parser-integration/reports/validation-report.md:1`
- commit: `5005e24b80cee86ff6a13c0a8e78201ad1b003c4`
- commit: `0d3b8cc07f94c7e684cb0bede1535f2a6a1fc5e2`
