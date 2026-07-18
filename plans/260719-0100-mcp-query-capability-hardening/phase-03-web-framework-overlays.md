# Phase 03: Web Framework Ingestion Overlays

## Context

Python currently marks HTTP entrypoint functions but does not create endpoint
semantic nodes. JavaScript Express and PHP Laravel lack equivalent overlay facts;
TypeScript and ASP.NET already demonstrate the target graph shape.

## Requirements

- Extract FastAPI and Django routes from Python sources/configuration.
- Extract Express routes from JavaScript sources.
- Extract Laravel routes/controllers from PHP sources.
- Emit `ApiEndpoint` facts and `HANDLES`/`SEMANTIC_OF` links with stable IDs.
- Run as non-exclusive incremental overlays after primary analyzers.

## Architecture

Create a shared `tools/web_framework` package with framework-specific extractors,
one normalized fact contract, and a provider-neutral allowlisted writer. Register
three overlay configurations with prerequisite parsers and deterministic order.

## Related Files

- `code-tiny/tools/web_framework/`
- `code-tiny/tools/graph/writer/web_framework_writer.py`
- `code-tiny/tools/sync/incremental_sync.py`
- web fixture and orchestration tests

## Implementation Steps

1. Define normalized endpoint/handler/relationship records and stable IDs.
2. Implement framework detectors/extractors with source-span evidence.
3. Implement writer and deletion/project scoping.
4. Register overlay selection/orchestration.
5. Add fixture-backed extraction and writer tests.

## Todo

- [ ] Add fixtures and failing extraction tests.
- [ ] Implement shared facts/extractors.
- [ ] Implement writer/orchestration.
- [ ] Verify incremental manifests.

## Risks

- Dynamic routes cannot always be resolved; retain raw route evidence and mark
  confidence/resolution rather than fabricating concrete paths.

## Success Criteria

- Each target framework fixture yields queryable endpoint and handler facts linked
  to canonical source symbols.

