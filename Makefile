POWERSHELL ?= powershell.exe
LIFECYCLE := scripts/mcp-lifecycle.ps1

.PHONY: help build start stop

help:
	@$(POWERSHELL) -NoProfile -ExecutionPolicy Bypass -File $(LIFECYCLE) help

build:
	$(POWERSHELL) -NoProfile -ExecutionPolicy Bypass -File $(LIFECYCLE) build

start:
	$(POWERSHELL) -NoProfile -ExecutionPolicy Bypass -File $(LIFECYCLE) start

stop:
	$(POWERSHELL) -NoProfile -ExecutionPolicy Bypass -File $(LIFECYCLE) stop
