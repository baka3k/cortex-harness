ifeq ($(OS),Windows_NT)
PYTHON ?= $(if $(wildcard .venv/Scripts/python.exe),.venv/Scripts/python.exe,python)
LIFECYCLE := powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/mcp-lifecycle.ps1
OWNER_OPTION := -Owner
SKILL_DEV := npx.cmd -y skill-dev
else
PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
LIFECYCLE := $(PYTHON) scripts/mcp-lifecycle.py
OWNER_OPTION := --owner
SKILL_DEV := npx -y skill-dev
endif
UV ?= uv
export UV
DEV := $(PYTHON) cortex_harness/dev.py

.PHONY: help build install uninstall infra-up infra-down storage-layout storage-init storage-migrate-layout storage-backup export-db export import-db import doctor start stop sync code doc sync-code-stop sync-doc-stop install-adlc install-devkit

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

# ----------------------------------------------------------------------------
# ADLC — Agent Development Lifecycle
# Bootstraps the agent skill pack (a.k.a. "dev-kit") published at
# https://github.com/baka3k/dev-kit via the `skill-dev` npm CLI.
#
# Usage:
#   make install-adlc                          # interactive installer
#   make install-adlc SOURCE=owner/repo        # install from a fork/mirror
#   make install-adlc SYNC_FILE=~/notes/AGENTS.md
#   make install-devkit                        # alias of install-adlc
#
# Requirements:
#   - Node.js >= 18 (for npx)
#   - Network access to npm registry (https://registry.npmjs.org)
#
# Notes:
#   - The installer is interactive by design; it prompts for:
#       (1) which skills to include
#       (2) which target agent (Claude Code / OpenCode / Qwen Code / Cursor / ...)
#       (3) install location (Global ~/.claude/skills  or  Current project)
#   - `npx -y` auto-confirms the package install prompt.
#   - Override the source repo with SOURCE=owner/repo (e.g. a private fork).
#   - Pass SYNC_FILE (repeatable) to also copy external AGENTS.md / CLAUDE.md
#     into the install target. Equivalent to skill-dev's --sync-file.
# ----------------------------------------------------------------------------
install-adlc install-devkit:
ifndef SKILL_DEV
	$(error SKILL_DEV is not set on this platform; please report a bug)
endif
	@echo ">> ADLC: bootstrapping dev-kit via $(SKILL_DEV) ..."
	@echo ">> Source: $(or $(SOURCE),https://github.com/baka3k/dev-kit)"
	@command -v node >/dev/null 2>&1 || { echo "ERROR: node not found. Install Node.js >= 18 first."; exit 1; }
	@command -v npx  >/dev/null 2>&1 || { echo "ERROR: npx not found. Install Node.js >= 18 first."; exit 1; }
	$(SKILL_DEV) $(if $(SOURCE),$(SOURCE),) \
		$(foreach f,$(SYNC_FILE),--sync-file $(f)) \
		$(if $(NO_MANIFEST),--no-manifest,)
