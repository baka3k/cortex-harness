POWERSHELL ?= powershell.exe
LIFECYCLE := scripts/mcp-lifecycle.ps1

.PHONY: help build infra-up infra-down doctor start stop

help:
	@$(POWERSHELL) -NoProfile -ExecutionPolicy Bypass -File $(LIFECYCLE) help

build:
	$(POWERSHELL) -NoProfile -ExecutionPolicy Bypass -File $(LIFECYCLE) build

infra-up:
	$(POWERSHELL) -NoProfile -ExecutionPolicy Bypass -File $(LIFECYCLE) infra-up

infra-down:
	$(POWERSHELL) -NoProfile -ExecutionPolicy Bypass -File $(LIFECYCLE) infra-down

doctor:
	$(POWERSHELL) -NoProfile -ExecutionPolicy Bypass -File $(LIFECYCLE) doctor

start:
	$(POWERSHELL) -NoProfile -ExecutionPolicy Bypass -File $(LIFECYCLE) start

stop:
	$(POWERSHELL) -NoProfile -ExecutionPolicy Bypass -File $(LIFECYCLE) stop
