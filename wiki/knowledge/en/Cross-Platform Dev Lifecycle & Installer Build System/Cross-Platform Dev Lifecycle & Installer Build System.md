---
kind: build_system
name: Cross-Platform Dev Lifecycle & Installer Build System
category: build_system
scope:
    - '**'
source_files:
    - Makefile
    - scripts/mcp-lifecycle.py
    - scripts/mcp-lifecycle.ps1
    - pyproject.toml
    - requirements.txt
    - cortex_harness/dev.py
    - installers/windows/inno_setup/cortex_harness.iss
    - installers/macos/build_pkg.sh
    - installers/ubuntu/build_deb.sh
    - .github/workflows/lifecycle-macos.yml
    - .github/workflows/cobol-macos.yml
---

Cortex Harness uses a thin, cross-platform build surface centered on a single `Makefile` that delegates to a Python lifecycle orchestrator (`scripts/mcp-lifecycle.py`) and a Windows PowerShell equivalent. The system manages three layers: (1) the editable Python package install, (2) Docker-backed local infrastructure, and (3) platform-specific context-menu installer builds.

### What is used
- **Package manager**: `setuptools` + `wheel` declared in `pyproject.toml`; runtime deps are pinned in `requirements.txt`.
- **Build entry point**: root `Makefile` shims to `scripts/mcp-lifecycle.py` on POSIX or `scripts/mcp-lifecycle.ps1` on Windows.
- **Virtualenv orchestration**: lifecycle script creates `.venv`, upgrades pip, installs from `requirements.txt`, plus per-submodule `requirements.txt` files under `code-tiny/` and `doc-tiny/`, then installs the root project in editable mode (`pip install -e .`).
- **Docker-based infra**: `infra-up` / `infra-down` manage Qdrant (`qdrant/qdrant`) and FalkorDB (`falkordb/falkordb`) containers with port readiness checks.
- **MCP server launcher**: `make start` writes per-server wrapper scripts into `.cache/mcp/`, launches them in separate terminal windows via `osascript` (macOS) or `gnome-terminal`/`xterm` (Linux), and records PIDs for `make stop`.
- **Installer packaging**: Inno Setup (Windows `.iss`), `pkgbuild` (macOS `.pkg`), `dpkg-deb` (Ubuntu `.deb`) driven by shell scripts under `installers/{windows,macos,ubuntu}/`.
- **CI**: GitHub Actions workflows under `.github/workflows/` run macOS matrix jobs (Intel x86_64 + Apple Silicon arm64) exercising the installed `dev` command and analyzer suites.

### Key files
- `Makefile` — public target surface (`help|build|install|uninstall|infra-up|infra-down|doctor|start|stop`)
- `scripts/mcp-lifecycle.py` — core build/lifecycle engine (venv, docker, MCP launch, doctor)
- `scripts/mcp-lifecycle.ps1` — Windows counterpart invoked when `OS=Windows_NT`
- `pyproject.toml` — setuptools metadata, `dev` console-script entrypoint, dependency list
- `requirements.txt` — full dev/runtime dependency manifest (tree-sitter language packs, ML libs, FastMCP)
- `cortex_harness/dev.py` — Click CLI exposed as `dev` global command
- `installers/windows/inno_setup/cortex_harness.iss` — Windows installer definition (context menu registry entries, icons, post-install tasks)
- `installers/macos/build_pkg.sh` — macOS `.pkg` builder using `pkgbuild`
- `installers/ubuntu/build_deb.sh` — Ubuntu `.deb` builder using `dpkg-deb`
- `.github/workflows/lifecycle-macos.yml` — macOS matrix CI for lifecycle commands
- `.github/workflows/cobol-macos.yml` — COBOL analyzer preflight + test matrix

### Architecture and conventions
- **Single source of truth for dependencies**: `requirements.txt` is the canonical list; submodules may add their own `requirements.txt` which the lifecycle script installs in order.
- **Editable development install**: `pip install -e .` keeps `cortex_harness/` live-editable so changes take effect without rebuilds.
- **Stateful dev environment**: `.venv/` holds the isolated interpreter; `.cache/mcp/` stores generated wrappers, PID files, and per-server env snapshots.
- **Environment propagation**: `mcp_runtime_config.runtime_environment()` serializes graph-provider settings into per-server `.active.env` files sourced by each wrapper before launching the MCP script.
- **Infrastructure-as-code via Docker**: container images, names, ports, and health endpoints are declared in the lifecycle script's `INFRA_SERVICES` tuple rather than in a separate compose file.
- **Installer isolation**: each platform ships its own packaging toolchain invocation; the shared `installers/common/config_manager.py` drives cross-platform context-menu configuration.

### Rules developers should follow
- Use `make <target>` (or `dev <action>`) instead of invoking Python/pip directly; the Makefile is the only supported cross-platform entry point.
- Add new MCP servers by extending the `SERVERS` tuple in `scripts/mcp-lifecycle.py` and providing a corresponding `<name>/mcp.sh` launcher.
- Keep all runtime dependencies in `requirements.txt`; submodule-only extras go in that module's own `requirements.txt`.
- Do not hardcode absolute paths in installers — rely on `{app}` variables (Inno Setup) or `$PROJECT_ROOT` resolution in shell builders.
- When adding platform-specific packaging steps, mirror the argument parsing pattern (`--output`, `--version`, `--help`) already established in `build_pkg.sh` and `build_deb.sh`.
- CI coverage must include both Intel and Apple-Silicon runners for any change touching lifecycle or analyzer code, following the matrix shape in `lifecycle-macos.yml`.

### CI overview
Two path-filtered workflows exercise the most fragile surfaces:
- `lifecycle-macos.yml`: installs the editable package, verifies `dev help` runs outside the repo, and runs `tests.test_make_lifecycle` + `tests.test_dev_lifecycle_commands` on both architectures.
- `cobol-macos.yml`: installs tree-sitter language packs, runs the COBOL analyzer preflight against fixtures, then executes the full `test_cobol*.py` suite.