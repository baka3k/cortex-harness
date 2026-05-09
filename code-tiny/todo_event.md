# Event/IDL TODO

- Decide final mapping format (JSON vs YAML) and lock field names.
- Align event IDs across repos (naming, versioning rules, namespace conventions).
- Add optional :IDL node if you want to store full schema files and link Event -> IDL.
- Add support for auto-resolving function IDs across projects (by qualified name + project_id).
- Add tooling to validate mapping file (schema + missing fields).
- Add CLI flag to fail fast on unresolved function references.
- Add ability to ingest from real IDL sources (proto/avro/openapi) instead of manual mapping.
- Add query examples for cross-project event flows (publisher -> event -> subscriber).
- Decide whether to dedupe Event nodes across projects (global event registry vs per-project events).
- Define retention policy for payload_example (keep small samples only).
