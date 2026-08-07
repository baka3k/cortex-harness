ifeq ($(OS),Windows_NT)
PYTHON ?= $(if $(wildcard .venv/Scripts/python.exe),.venv/Scripts/python.exe,python)
LIFECYCLE := powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/mcp-lifecycle.ps1
OWNER_OPTION := -Owner
else
PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
LIFECYCLE := $(PYTHON) scripts/mcp-lifecycle.py
OWNER_OPTION := --owner
endif
DEV := $(PYTHON) cortex_harness/dev.py

.PHONY: help build install uninstall infra-up infra-down storage-layout storage-init storage-migrate-layout storage-backup doctor start stop sync code doc sync-code-stop sync-doc-stop

help:
	@$(LIFECYCLE) help

build:
	$(LIFECYCLE) build

install:
	$(LIFECYCLE) install

uninstall:
	$(LIFECYCLE) uninstall

infra-up:
	$(LIFECYCLE) infra-up

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
