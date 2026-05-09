# Migration Quick Reference

## 🎯 TL;DR - What Changed

**Before**: Each analyzer had its own `Neo4jWriter` class (~500 lines, hardcoded Neo4j)
**After**: All analyzers share `LanguageCodeWriter` (database-agnostic, ~150 lines)

## 📦 New Abstraction Layer Components

### Core Framework

```
tools/graph/
├── base.py                    # Abstract interfaces
├── neo4j_driver.py            # Neo4j implementation
├── factory.py                 # Driver factory
├── language_writer.py         # ✨ NEW - Unified writer
└── operations/
    ├── function_ops.py        # Function operations
    ├── document_ops.py        # Documentation operations
    ├── infra_ops.py           # Infrastructure/modules
    ├── cross_edge_ops.py      # Cross-references
    ├── package_ops.py         # ✨ NEW - Package operations
    ├── class_ops.py           # ✨ NEW - Class operations
    ├── namespace_ops.py       # ✨ NEW - Namespace operations
    └── type_ops.py            # ✨ NEW - Type operations
```

## 🔄 Migration Checklist (Per Analyzer)

### 1️⃣ Update Imports

```python
# REMOVE
from neo4j import GraphDatabase

# ADD
import asyncio
from tools.graph import GraphDriverFactory, GraphProvider, LanguageCodeWriter
```

### 2️⃣ Delete Neo4jWriter Class

```python
# DELETE ~500 lines
class Neo4jWriter:
    def __init__(self, uri, user, password, database):
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
    ...
    def write(self, packages, classes, functions, ...):
        # 400 lines of Cypher
```

### 3️⃣ Update Function Signatures

```python
# OLD
def build_call_graph(..., neo4j_writer: Optional[Neo4jWriter], ...):

# NEW
async def build_call_graph(..., code_writer: Optional[LanguageCodeWriter], ...):
```

### 4️⃣ Replace Writer Usage

```python
# OLD
neo4j_writer.write(
    packages=[asdict(p) for p in packages],
    classes=[asdict(c) for c in classes],
    functions=[asdict(f) for f in functions],
    ...
)

# NEW
await code_writer.write_all(
    packages=package_dicts,
    classes=class_dicts,
    functions=function_dicts,
    ...
)
```

### 5️⃣ Update main() Function

```python
# OLD
def main(argv):
    neo4j_writer = Neo4jWriter(uri, user, password, db)
    build_call_graph(..., neo4j_writer=neo4j_writer)
    neo4j_writer.close()

# NEW
async def async_main(argv):
    driver = GraphDriverFactory.create_driver(GraphProvider.NEO4J, config)
    code_writer = LanguageCodeWriter(driver, database=db)
    await build_call_graph(..., code_writer=code_writer)
    driver.close()

def main(argv):
    return asyncio.run(async_main(argv))
```

## 📊 Impact Summary

### Files Requiring Changes

- ✅ kotlin_analyzer.py (migration guide created)
- ⏳ android_kotlin_analyzer.py
- ⏳ java_analyzer.py
- ⏳ python_analyzer.py
- ⏳ cplus_analyzer.py
- ⏳ csharp_analyzer.py
- ⏳ ts_analyzer.py
- ⏳ js_analyzer.py
- ⏳ php_analyzer.py
- ⏳ sql_analyzer.py
- ⏳ plsql_analyzer.py

### Lines of Code Impact (Per File)

- **Removed**: ~500 lines (Neo4jWriter class)
- **Added**: ~50 lines (driver setup + async)
- **Net**: -450 lines per file
- **Total reduction**: ~4,950 lines across 11 files!

## 🎁 Benefits

### Before Migration

```python
# ❌ Hardcoded Neo4j in 11 different files
# ❌ 500 lines of duplicated code per file
# ❌ Hard to test (no mocking)
# ❌ Impossible to swap databases
# ❌ Inconsistent across languages
```

### After Migration

```python
# ✅ Database agnostic (Neo4j, Kuzu, FalkorDB...)
# ✅ Single source of truth
# ✅ Easy to mock for tests
# ✅ Consistent behavior everywhere
# ✅ 4,950 fewer lines to maintain
```

## 🚀 Quick Start - Migrate One Analyzer

### Example: kotlin_analyzer.py

1. **Read the detailed guide**:

   ```bash
   cat tools/graph/MIGRATION_GUIDE.py
   ```

2. **Make the changes** (6 steps):
   - Update imports
   - Delete Neo4jWriter class
   - Make build_call_graph async
   - Replace writer calls
   - Update main() function
   - Test

3. **Test the migration**:
   ```bash
   python -m tools.kotlin.kotlin_analyzer \
     --root /path/to/kotlin/code \
     --neo4j-uri bolt://localhost:7687 \
     --neo4j-user neo4j \
     --neo4j-password password \
     --verbose
   ```

## 🔍 Detailed Documentation

- **Abstraction Layer**: [tools/graph/README.md](tools/graph/README.md)
- **Migration Guide**: [tools/graph/MIGRATION_GUIDE.py](tools/graph/MIGRATION_GUIDE.py)
- **Example Usage**: [tools/graph/example_usage.py](tools/graph/example_usage.py)
- **Operations API**: See individual `*_ops.py` files

## ⚠️ Important Notes

### State Management

The new `LanguageCodeWriter` supports resume capability:

```python
state = load_state(state_path)

def state_writer(s):
    write_state(state_path, s)

await code_writer.write_all(
    ...,
    state=state,
    state_writer=state_writer
)
```

### Batch Size

Control batch size via constructor:

```python
code_writer = LanguageCodeWriter(
    driver,
    batch_size=1000,  # Adjust as needed
    verbose=True
)
```

### Error Handling

Driver automatically handles connection errors:

```python
try:
    await code_writer.write_all(...)
except Exception as e:
    logger.error(f"Write failed: {e}")
finally:
    driver.close()
```

## 🆘 Troubleshooting

### "Module 'tools.graph' not found"

```bash
# Make sure you're running from project root
cd /Users/yourname/AI/graph-code-tiny
source .venv/bin/activate
python -m tools.kotlin.kotlin_analyzer ...
```

### "RuntimeError: no running event loop"

```python
# Make sure you wrapped main() correctly:
def main(argv):
    return asyncio.run(async_main(argv))  # Don't forget this!
```

### "Cannot await non-async function"

```python
# Make sure build_call_graph is async:
async def build_call_graph(...):  # Add 'async'
    await code_writer.write_all(...)  # Add 'await'
```

## 📞 Next Steps

1. **Review** the migration guide: [MIGRATION_GUIDE.py](tools/graph/MIGRATION_GUIDE.py)
2. **Migrate** kotlin_analyzer.py first (reference implementation)
3. **Test** thoroughly with your data
4. **Apply** same pattern to other 10 analyzers
5. **Celebrate** 5,000 lines of code deleted! 🎉
