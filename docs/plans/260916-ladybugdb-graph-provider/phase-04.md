# Phase 4: Storage Path Resolution

## Mục tiêu
Thêm LadybugDB path resolution vào storage layer, tương tự FalkorDB.

## Files cần sửa

### 1. `cortex_harness/storage/config.py`

#### Constants
```python
ENV_LADYBUG_PATH = "LADYBUG_PATH"
ENV_LADYBUG_CODE = "LADYBUG_CODE_PATH"
ENV_LADYBUG_DOC = "LADYBUG_DOC_PATH"

DEFAULT_LADYBUG_PATH = (
    Path(STORAGE_SCHEMA_VERSION) / "instances" / DEFAULT_INSTANCE_ID / "ladybug" / "code" / "data.lbdb"
)

CFG_LADYBUG_PATH = ENV_LADYBUG_PATH
CFG_LADYBUG_CODE = ENV_LADYBUG_CODE
CFG_LADYBUG_DOC = ENV_LADYBUG_DOC
```

#### `ResolvedStorage` dataclass
```python
@dataclass(frozen=True)
class ResolvedStorage:
    # existing fields...
    ladybug_path: Path
    ladybug_code_path: Optional[Path] = None
    ladybug_doc_path: Optional[Path] = None
    
    def __post_init__(self):
        # existing falkordb logic...
        ladybug_code = self.ladybug_code_path or self.ladybug_path
        ladybug_doc = self.ladybug_doc_path or (
            self.ladybug_path.parent.parent / "doc" / "data.lbdb"
        )
        object.__setattr__(self, "ladybug_code_path", Path(ladybug_code))
        object.__setattr__(self, "ladybug_doc_path", Path(ladybug_doc))
    
    def ladybug_path_for_role(self, role: StorageRole | QdrantStorageRole | str) -> Path:
        role_value = _role_value(role)
        if role_value == "code":
            return Path(self.ladybug_code_path)
        return Path(self.ladybug_doc_path)
```

### 2. `cortex_harness/storage/layout.py`
Thêm ladybug paths vào storage layout output:
```python
# Trong storage_layout():
"ladybug_path": str(resolved.ladybug_code_path),  # code role
"ladybug_path": str(resolved.ladybug_doc_path),   # doc role
```

### 3. `cortex_harness/storage/generation.py`
Thêm ladybug generation manifest support:
```python
# Generation manifest: include ladybug path
graph_path=str(generation_root / "graph" / "data.lbdb"),
```

### 4. `cortex_harness/storage/__init__.py`
Export new constants:
```python
from .config import (
    # existing...
    "DEFAULT_LADYBUG_PATH",
    "ENV_LADYBUG_PATH",
    "ENV_LADYBUG_CODE",
    "ENV_LADYBUG_DOC",
)
```

## Tests
- Default path: `v1/instances/default/ladybug/code/data.lbdb`
- Env override: `LADYBUG_PATH=/custom/path.lbdb`
- Role-based: `ladybug_path_for_role("code")` vs `ladybug_path_for_role("doc")`
- Layout output includes ladybug paths

## Acceptance
- `resolve_storage(cwd).ladybug_code_path` → correct `.lbdb` path
- `resolve_storage(cwd).ladybug_doc_path` → correct `.lbdb` path
- `dev storage-layout` shows ladybug paths
