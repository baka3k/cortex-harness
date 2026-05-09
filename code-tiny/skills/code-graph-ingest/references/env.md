# Environment variables

## Neo4j
- `NEO4J_URI` (e.g., `bolt://localhost:7687`)
- `NEO4J_USER`
- `NEO4J_PASSWORD`
- `NEO4J_DB` (optional)
- `NEO4J_STATE_PATH` (optional resume state override)

## Qdrant
- `QDRANT_URL` (e.g., `http://localhost:6333`)
- `QDRANT_COLLECTION` (default varies by analyzer)
- `QDRANT_CACHE_DIR` (optional cache root override)

## Embeddings
- `EMBED_MODEL` (model id or local path)
- `JINA_MODEL_PATH` (fallback when `EMBED_MODEL` unset)
- `EMBED_DEVICE` (`cpu`, `cuda`, `mps`, or `auto` for Kotlin)

## Project metadata
- `PROJECT_ID`
- `PROJECT_NAME`
- `PROJECT_LANGUAGE`
- `PROJECT_REPO`
- `PROJECT_BUILD_SYSTEM`
