ifeq ($(OS),Windows_NT)
PYTHON ?= $(if $(wildcard .venv/Scripts/python.exe),.venv/Scripts/python.exe,python)
LIFECYCLE := powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/mcp-lifecycle.ps1
OWNER_OPTION := -Owner
else
PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
LIFECYCLE := $(PYTHON) scripts/mcp-lifecycle.py
OWNER_OPTION := --owner
endif
UV ?= uv
export UV
DEV := $(PYTHON) cortex_harness/dev.py

# Rust workspace (graph core + retrieval brain + PyO3 bindings).
# Parity-first: golden fixtures are generated from the Python reference in
# scripts/rust_parity/ and committed under rust/crates/*/tests/fixtures/.
CARGO ?= cargo
RUST_DIR := rust
PARITY_DIR := scripts/rust_parity
# macOS linker must allow undefined symbols: the PyO3 extension module
# resolves them at import time (same flags as scripts/rust_parity/build_pyo3.sh).
ifneq ($(OS),Windows_NT)
ifeq ($(shell uname -s),Darwin)
RUST_LINK_ENV := RUSTFLAGS="$${RUSTFLAGS:-} -C link-arg=-undefined -C link-arg=dynamic_lookup"
endif
endif

.PHONY: help build install uninstall infra-up infra-down storage-layout storage-init storage-migrate-layout storage-backup export-db export import-db import doctor start stop sync code doc sync-code-stop sync-doc-stop \
	rust-build rust-test rust-clippy rust-check rust-pyo3 rust-fixtures rust-clean

help:
	@$(LIFECYCLE) help

build:
	$(LIFECYCLE) build

install:
	$(LIFECYCLE) install

uninstall:
	$(LIFECYCLE) uninstall

infra-up:
	$(LIFECYCLE) infra-up $(INFRA_ARGS)

infra-down:
	$(LIFECYCLE) infra-down

storage-layout:
	$(LIFECYCLE) storage-layout

storage-init:
	$(LIFECYCLE) storage-init

storage-migrate-layout:
	$(LIFECYCLE) storage-migrate-layout $(MIGRATE_ARGS)

storage-backup:
	$(LIFECYCLE) storage-backup $(OWNER_OPTION) $(or $(OWNER),code)

export-db:
	$(DEV) export-db $(if $(OUTPUT),--output $(OUTPUT)) $(if $(PROJECT_ID),--project-id $(PROJECT_ID)) $(if $(ROLE),--role $(ROLE))

export:
	$(DEV) export $(PROJECT_ID) $(if $(OUTPUT),--output $(OUTPUT)) $(if $(ROLE),--role $(ROLE))

import-db:
ifndef ARCHIVE
	$(error Usage: make import-db ARCHIVE=/path/to/project.cortexdb [OVERWRITE=1] [ROLE=code|doc|both])
endif
	$(DEV) import-db --archive $(ARCHIVE) $(if $(filter 1 true yes,$(OVERWRITE)),--overwrite) $(if $(ROLE),--role $(ROLE))

import:
ifndef ARCHIVE
	$(error Usage: make import ARCHIVE=/path/to/project.cortexdb [OVERWRITE=1] [ROLE=code|doc|both])
endif
	$(DEV) import $(ARCHIVE) $(if $(filter 1 true yes,$(OVERWRITE)),--overwrite) $(if $(ROLE),--role $(ROLE))

doctor:
	$(LIFECYCLE) doctor

# Natural-language aliases requested for operational use:
#   make sync code stop
#   make sync doc stop
sync:
ifeq ($(word 3,$(MAKECMDGOALS)),stop)
ifeq ($(word 2,$(MAKECMDGOALS)),code)
	$(DEV) sync code stop
else ifeq ($(word 2,$(MAKECMDGOALS)),doc)
	$(DEV) sync doc stop
else
	$(error Usage: make sync code stop OR make sync doc stop)
endif
else
	$(error Usage: make sync code stop OR make sync doc stop)
endif

code doc:
	@:

sync-code-stop:
	$(DEV) sync code stop

sync-doc-stop:
	$(DEV) sync doc stop

start:
	$(LIFECYCLE) start $(START_ARGS)

stop:
ifeq ($(firstword $(MAKECMDGOALS)),sync)
	@:
else
	$(LIFECYCLE) stop $(STOP_ARGS)
endif

# --- Rust workspace (rust/) and PyO3 parity gate -------------------------
# cargo test + cargo clippy -D warnings are the mandatory gate for every
# ported module (plans/260913-1715-rust-retrieval-graph-port). Golden
# fixtures are committed; only regenerate them when the Python reference
# changes, and re-run rust-pyo3 afterwards to confirm parity.

rust-build:
	$(RUST_LINK_ENV) $(CARGO) build --release --workspace --manifest-path $(RUST_DIR)/Cargo.toml

rust-test:
	$(CARGO) test --workspace --manifest-path $(RUST_DIR)/Cargo.toml

rust-clippy:
	$(CARGO) clippy --manifest-path $(RUST_DIR)/Cargo.toml --workspace --all-targets -- -D warnings

rust-check: rust-clippy rust-test

# Build cortex-retrieval-py (PyO3) into scripts/rust_parity/, then replay
# the Python↔Rust parity suite against the Python reference implementation.
rust-pyo3:
	bash $(PARITY_DIR)/build_pyo3.sh

# Regenerate every golden fixture from the Python reference (commit the
# diffs). BM25/fusion generators pin rank_bm25==0.2.2 via uv.
rust-fixtures:
	$(UV) run --no-project --with rank_bm25==0.2.2 python $(PARITY_DIR)/gen_bm25_fixtures.py
	$(UV) run --no-project --with rank_bm25==0.2.2 python $(PARITY_DIR)/gen_fusion_fixtures.py
	$(UV) run --no-project python $(PARITY_DIR)/gen_phase03_fixtures.py
	$(UV) run --no-project python $(PARITY_DIR)/gen_phase05_fixtures.py
	$(UV) run --no-project python $(PARITY_DIR)/gen_journal_scenario.py

rust-clean:
	$(CARGO) clean --manifest-path $(RUST_DIR)/Cargo.toml
