ifeq ($(OS),Windows_NT)
PYTHON ?= $(if $(wildcard .venv/Scripts/python.exe),.venv/Scripts/python.exe,python)
OWNER_OPTION := -Owner
BIN_SUFFIX := .exe
else
PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
OWNER_OPTION := --owner
BIN_SUFFIX :=
endif
UV ?= uv
export UV

# Phase-06 cutover: the dev/make layer is the cortex-dev binary only (no
# Python rollback; downgrade = reinstall the previous release binary).
# Resolution: CORTEX_DEV_BIN -> installed prefix -> repo release build.
# PYTHON stays only for the deliberate Python-reference targets (parity/embed).
CORTEX_DEV_BIN ?= $(firstword $(wildcard rust/target/release/cortex-dev$(BIN_SUFFIX)) $(wildcard rust/target/debug/cortex-dev$(BIN_SUFFIX)) $(HOME)/.local/bin/cortex-dev$(BIN_SUFFIX))
LIFECYCLE := $(CORTEX_DEV_BIN)

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

.PHONY: help update build install uninstall infra-up infra-down storage-layout storage-init storage-migrate-layout storage-backup export-db export import-db import doctor start stop sync code doc sync-code-stop sync-doc-stop \
	rust-build rust-test rust-clippy rust-check rust-pyo3 rust-fixtures rust-clean \
	ort-ensure embed-artifacts embed-jina-onnx embed-bge-onnx embed-parity \
	embed-jina-onnx-if-needed embed-bge-onnx-if-needed embed-artifacts-if-needed \
	embedding-install \
	journal-shadow-diff

help:
	@$(LIFECYCLE) help

# `update` refreshes the working tree and every dependency layer in one shot:
#   git pull -> cargo fetch (rust crates) -> uv venv + pip install (python)
#   -> ONNX embed artifacts, last because it is by far the slowest layer.
# The Python layer follows the dev-lifecycle convention: create `.venv` via
# `uv venv` when missing, then install root + code-tiny + doc-tiny
# requirements plus the editable root in one `uv pip install --python`
# invocation. Embed artifacts are fetched only when the pinned graph is
# missing (`embed-artifacts-if-needed`); ONNX Runtime shared library stays
# with `make build` (ort-ensure).
ifeq ($(OS),Windows_NT)
VENV_READY := $(wildcard .venv/Scripts/python.exe)
else
VENV_READY := $(wildcard .venv/bin/python)
endif

update:
	git pull --ff-only
	$(CARGO) fetch --manifest-path $(RUST_DIR)/Cargo.toml
ifeq ($(VENV_READY),)
	$(UV) venv
endif
	$(UV) pip install --python $(PYTHON) -r requirements.txt -r code-tiny/requirements.txt -r doc-tiny/requirements.txt -e .
	@echo "update: code + dependencies refreshed."
	@echo "update: ONNX embed artifacts last — on a fresh machine this downloads/exports ~4.2 GB of model weights and is the slowest step (many minutes)."
	@$(MAKE) --no-print-directory embed-artifacts-if-needed
	@echo "update: done. Run 'make build' to rebuild the cortex-dev binary (and 'make install' if it is installed)."

# `build` also provisions ONNX Runtime for cortex-embed: everything the runtime
# needs to load a graph is installed here, model weights are not (see embed-artifacts).
build: ort-ensure
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
	$(CORTEX_DEV_BIN) export-db $(if $(OUTPUT),--output $(OUTPUT)) $(if $(PROJECT_ID),--project-id $(PROJECT_ID)) $(if $(ROLE),--role $(ROLE))

export:
	$(CORTEX_DEV_BIN) export $(PROJECT_ID) $(if $(OUTPUT),--output $(OUTPUT)) $(if $(ROLE),--role $(ROLE))

import-db:
ifndef ARCHIVE
	$(error Usage: make import-db ARCHIVE=/path/to/project.cortexdb [OVERWRITE=1] [ROLE=code|doc|both])
endif
	$(CORTEX_DEV_BIN) import-db --archive $(ARCHIVE) $(if $(filter 1 true yes,$(OVERWRITE)),--overwrite) $(if $(ROLE),--role $(ROLE))

import:
ifndef ARCHIVE
	$(error Usage: make import ARCHIVE=/path/to/project.cortexdb [OVERWRITE=1] [ROLE=code|doc|both])
endif
	$(CORTEX_DEV_BIN) import $(ARCHIVE) $(if $(filter 1 true yes,$(OVERWRITE)),--overwrite) $(if $(ROLE),--role $(ROLE))

doctor:
	$(LIFECYCLE) doctor

# Natural-language aliases requested for operational use:
#   make sync code stop
#   make sync doc stop
sync:
ifeq ($(word 3,$(MAKECMDGOALS)),stop)
ifeq ($(word 2,$(MAKECMDGOALS)),code)
	$(CORTEX_DEV_BIN) sync code stop
else ifeq ($(word 2,$(MAKECMDGOALS)),doc)
	$(CORTEX_DEV_BIN) sync doc stop
else
	$(error Usage: make sync code stop OR make sync doc stop)
endif
else
	$(error Usage: make sync code stop OR make sync doc stop)
endif

code doc:
	@:

sync-code-stop:
	$(CORTEX_DEV_BIN) sync code stop

sync-doc-stop:
	$(CORTEX_DEV_BIN) sync doc stop

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

# --- ONNX embedding spike (plans/260914-1706-onnx-embedding-spike) --------
# cortex-embed runs ONNX Runtime through `ort` load-dynamic, so a shared library
# must exist at runtime. `make build` provisions it from the pinned `onnxruntime`
# wheel (same ORT build the parity fixtures were measured with).
ort-ensure:
	$(CORTEX_DEV_BIN) ensure-ort

# Model graphs are multi-GB and deliberately NOT part of `build`:
#   jina-v3  -> must be self-exported (the official HF ONNX requires a `task_id`
#               input and cannot express "LoRA off"; see findings C3)
#   bge-m3   -> official HF `onnx/` subtree is clean, just download it
embed-artifacts: embed-jina-onnx embed-bge-onnx

embed-jina-onnx:
	$(PYTHON) scripts/rust_parity/export_jina_onnx.py --verify

embed-bge-onnx:
	$(PYTHON) scripts/rust_parity/fetch_bge_onnx.py

# update-friendly variants: no-op when the pinned graph already exists.
# Presence of `model.onnx` is the completeness check — both fetch scripts pin
# bytes via metadata.json (digest pin, cortex-embed gate G7) and neither
# re-exports/refetches an existing graph unless forced. Make-level wildcard
# guards (not shell `test`) so the targets stay cross-platform.
EMBED_JINA_GRAPH := .cache/embed/jina-v3-onnx-fp32/model.onnx
EMBED_BGE_GRAPH := .cache/embed/BAAI--bge-m3/model.onnx

embed-jina-onnx-if-needed:
ifeq ($(wildcard $(EMBED_JINA_GRAPH)),)
	@echo "[embed] jina-v3 ONNX graph MISSING — downloading torch weights + exporting + verifying (multi-GB, the slowest step, expect many minutes)..."
	$(PYTHON) scripts/rust_parity/export_jina_onnx.py --verify
else
	@echo "[embed] jina-v3 graph present ($(EMBED_JINA_GRAPH)) — skip"
endif

embed-bge-onnx-if-needed:
ifeq ($(wildcard $(EMBED_BGE_GRAPH)),)
	@echo "[embed] bge-m3 ONNX graph MISSING — downloading pinned snapshot from Hugging Face (~2.1 GB)..."
	$(PYTHON) scripts/rust_parity/fetch_bge_onnx.py
else
	@echo "[embed] bge-m3 graph present ($(EMBED_BGE_GRAPH)) — skip"
endif

embed-artifacts-if-needed: embed-jina-onnx-if-needed embed-bge-onnx-if-needed

# Standalone on-demand embedding install: provisions everything cortex-embed
# needs at runtime (ONNX Runtime dylib + the two pinned model graphs) without
# a full `make build`/`make update`. Idempotent — the `*-if-needed` legs no-op
# when the graphs are already present, so re-running is always cheap.
# Force a re-export/re-download of one model with `make embed-jina-onnx` /
# `make embed-bge-onnx`.
embedding-install: ort-ensure embed-artifacts-if-needed

# Re-run the Python reference dump + the Rust cosine/token-id gate. Needs both
# artifacts above; the Rust half is `#[ignore]`d so CI stays weights-free.
embed-parity: ort-ensure
	$(PYTHON) scripts/rust_parity/gen_embed_fixtures.py --limit 500
	$(CARGO) test -p cortex-embed --manifest-path $(RUST_DIR)/Cargo.toml --test embed_golden -- --ignored --nocapture --test-threads=1

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

# Journal Rust shadow (Track B, phase 1309-2104): replay JSONL capture qua
# journal core Rust thành shadow store rồi diff DB-state với store Python.
# Usage: make journal-shadow-diff INPUT=<capture.jsonl> PYTHON_STORE=<python.sqlite3> RUST_STORE=<rust.sqlite3> [STRICT_TIME=1]
journal-shadow-diff:
ifndef INPUT
	$(error Usage: make journal-shadow-diff INPUT=<capture.jsonl> PYTHON_STORE=<python.sqlite3> RUST_STORE=<rust.sqlite3> [STRICT_TIME=1])
endif
ifndef PYTHON_STORE
	$(error PYTHON_STORE is required)
endif
ifndef RUST_STORE
	$(error RUST_STORE is required)
endif
	$(RUST_LINK_ENV) $(CARGO) build --release -p cortex-graph-driver --bin replay_journal --manifest-path $(RUST_DIR)/Cargo.toml
	$(RUST_DIR)/target/release/replay_journal --input $(INPUT) --output $(RUST_STORE)
	$(PYTHON) $(PARITY_DIR)/diff_journal_stores.py $(PYTHON_STORE) $(RUST_STORE) $(if $(filter 1,$(STRICT_TIME)),--strict-time)
