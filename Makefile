ifeq ($(OS),Windows_NT)
LIFECYCLE := powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/mcp-lifecycle.ps1
else
PYTHON ?= python3
LIFECYCLE := $(PYTHON) scripts/mcp-lifecycle.py
endif

.PHONY: help build install uninstall infra-up infra-down doctor start stop

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

doctor:
	$(LIFECYCLE) doctor

start:
	$(LIFECYCLE) start $(START_ARGS)

stop:
	$(LIFECYCLE) stop $(STOP_ARGS)
