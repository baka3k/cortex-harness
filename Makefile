ifeq ($(OS),Windows_NT)
LIFECYCLE := powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/mcp-lifecycle.ps1
OWNER_OPTION := -Owner
else
PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
LIFECYCLE := $(PYTHON) scripts/mcp-lifecycle.py
OWNER_OPTION := --owner
endif

.PHONY: help build install uninstall infra-up infra-down storage-layout storage-init storage-migrate-layout storage-backup doctor start stop

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

start:
	$(LIFECYCLE) start $(START_ARGS)

stop:
	$(LIFECYCLE) stop $(STOP_ARGS)
