# Phase 06 - Data Migration And Backfill

## Goal

Move existing Neo4j graph data into FalkorDB with validation and rollback safety.

## Tasks

1. Choose migration path.
   - For production-size graphs, prefer CSV export/import or FalkorDB migration tooling.
   - For local/dev graphs, provider-level re-ingest may be sufficient.

2. Export from Neo4j.
   - Export nodes by label.
   - Export relationships by type.
   - Export indexes and constraints.
   - Preserve identifiers used by application queries.

3. Load into FalkorDB.
   - Use FalkorDB bulk loader or Python loader depending on data volume.
   - Apply schema before or after load depending on performance and constraint requirements.

4. Validate counts and samples.
   - Compare node counts by label.
   - Compare relationship counts by type.
   - Compare property key presence.
   - Run representative query outputs.

5. Rollback strategy.
   - Keep Neo4j source untouched.
   - Make application provider switch config-driven.
   - Allow reverting to Neo4j until FalkorDB validation passes.

## Validation

- Counts match within documented exceptions.
- Duplicate key checks pass for all unique identifiers.
- Random sampled nodes and relationships match by ID/properties.

## Risks

- Existing Neo4j data may violate constraints that FalkorDB later refuses asynchronously.
- Multi-database Neo4j setups may require one FalkorDB graph per database or project.

