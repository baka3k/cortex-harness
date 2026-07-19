---
kind: dependency_management
name: Python dependency manifests and per-subproject requirements
category: dependency_management
scope:
    - '**'
source_files:
    - requirements.txt
    - pyproject.toml
    - code-tiny/requirements.txt
    - doc-tiny/requirements.txt
---

The repository uses a flat, pip-based Python dependency strategy with no lockfiles or vendoring. Three requirements.txt files declare runtime packages for the top-level harness, the code-tiny analyzer service, and the doc-tiny GraphRAG service; a minimal pyproject.toml only configures setuptools build metadata and the dev CLI entry point. There is no Poetry, Pipenv, pdm, uv.lock, or Pipfile; .gitignore mentions uv.lock but none exists in the tree. The .venv/ directory is present but empty (not committed). CI workflows install dependencies via pip install -e . and ad-hoc pip install calls rather than reading manifest files.

Key files:
- Root: requirements.txt, pyproject.toml
- Subprojects: code-tiny/requirements.txt, doc-tiny/requirements.txt

Conventions observed:
- Each subproject ships its own requirements.txt listing the exact packages it needs (e.g., code-tiny pins tree-sitter-perl==1.2.1 and constrains transformers<5.0 for Windows).
- Version pinning is inconsistent — some entries are bare names (neo4j, fastmcp), others use >=x,<y ranges (portalocker>=3.2,<4, python-dotenv>=1.2.2), and one is an exact pin (tree-sitter-perl==1.2.1). No shared constraints file or lockfile enforces consistency across subprojects.
- The root pyproject.toml declares only a small core set of dependencies (click, requests, neo4j, falkordb, qdrant-client, python-dotenv, portalocker) plus the dev script entry point; heavy analyzers and ML stacks live in the subproject manifests.
- External language tooling (Tree-sitter parsers, Roslyn workers, Cobol native libs) is pulled as Python wheels or prebuilt binaries alongside the Python deps; there is no separate Go/Node/Cargo manifest at the repo root.

Developer rules implied by current practice:
- Keep per-subproject requirements.txt files up to date when adding new analyzer or service dependencies.
- Prefer explicit version bounds (>=x,<y) over bare names to avoid drift.
- Do not commit generated artifacts like .venv/ or uv.lock.
- When changing transitive-heavy packages (e.g., torch, transformers), document platform-specific notes in comments next to the requirement.