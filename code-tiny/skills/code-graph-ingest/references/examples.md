# Command templates

## Dry run (any language)
```bash
python tools/<lang>/<lang>_analyzer.py --root /path/to/src --dry-run
```

## Kotlin
```bash
python tools/kotlin/kotlin_analyzer.py \
  --root /path/to/kotlin \
  --neo4j-uri bolt://localhost:7687 \
  --neo4j-user neo4j \
  --neo4j-password password \
  --qdrant-url http://localhost:6333 \
  --qdrant-collection kotlin_functions \
  --embed-model jinaai/jina-embeddings-v3 \
  --device auto \
  --verbose
```

## Java
```bash
python tools/java/java_analyzer.py \
  --root /path/to/java \
  --neo4j-uri bolt://localhost:7687 \
  --neo4j-user neo4j \
  --neo4j-password password \
  --qdrant-url http://localhost:6333 \
  --qdrant-collection java_functions \
  --embed-model jinaai/jina-embeddings-v3 \
  --device cpu \
  --verbose
```

## TypeScript
```bash
python tools/ts/ts_analyzer.py \
  --root /path/to/typescript \
  --neo4j-uri bolt://localhost:7687 \
  --neo4j-user neo4j \
  --neo4j-password password \
  --qdrant-url http://localhost:6333 \
  --qdrant-collection typescript_functions \
  --embed-model jinaai/jina-embeddings-v3 \
  --device auto \
  --verbose
```

## JavaScript
```bash
python tools/js/js_analyzer.py \
  --root /path/to/javascript \
  --neo4j-uri bolt://localhost:7687 \
  --neo4j-user neo4j \
  --neo4j-password password \
  --qdrant-url http://localhost:6333 \
  --qdrant-collection javascript_functions \
  --embed-model jinaai/jina-embeddings-v3 \
  --device auto \
  --verbose
```

## PHP
```bash
python tools/php/php_analyzer.py \
  --root /path/to/php \
  --neo4j-uri bolt://localhost:7687 \
  --neo4j-user neo4j \
  --neo4j-password password \
  --qdrant-url http://localhost:6333 \
  --qdrant-collection php_functions \
  --embed-model jinaai/jina-embeddings-v3 \
  --device auto \
  --verbose
```

## SQL
```bash
python tools/sql/sql_analyzer.py \
  --root /path/to/sql \
  --neo4j-uri bolt://localhost:7687 \
  --neo4j-user neo4j \
  --neo4j-password password \
  --qdrant-url http://localhost:6333 \
  --qdrant-collection sql_functions \
  --embed-model jinaai/jina-embeddings-v3 \
  --device auto \
  --verbose
```

## PL/SQL
```bash
python tools/plsql/plsql_analyzer.py \
  --root /path/to/plsql \
  --neo4j-uri bolt://localhost:7687 \
  --neo4j-user neo4j \
  --neo4j-password password \
  --qdrant-url http://localhost:6333 \
  --qdrant-collection plsql_functions \
  --embed-model jinaai/jina-embeddings-v3 \
  --device auto \
  --verbose
```

## C#
```bash
python tools/csharp/csharp_analyzer.py \
  --root /path/to/csharp \
  --neo4j-uri bolt://localhost:7687 \
  --neo4j-user neo4j \
  --neo4j-password password \
  --qdrant-url http://localhost:6333 \
  --qdrant-collection csharp_functions \
  --embed-model jinaai/jina-embeddings-v3 \
  --device cpu \
  --verbose
```

## C/C++
```bash
python tools/cplus/cplus_analyzer.py \
  --root /path/to/cpp \
  --neo4j-uri bolt://localhost:7687 \
  --neo4j-user neo4j \
  --neo4j-password password \
  --qdrant-url http://localhost:6333 \
  --qdrant-collection cplus_functions \
  --embed-model jinaai/jina-embeddings-v3 \
  --device cpu \
  --verbose
```
