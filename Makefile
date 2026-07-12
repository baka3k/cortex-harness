ifeq ($(OS),Windows_NT)
POWERSHELL ?= powershell.exe
else
POWERSHELL ?= pwsh
endif
LIFECYCLE := scripts/mcp-lifecycle.ps1

.PHONY: help build install uninstall infra-up infra-down doctor start stop

help:
	@$(POWERSHELL) -NoProfile -ExecutionPolicy Bypass -File $(LIFECYCLE) help

build:
	$(POWERSHELL) -NoProfile -ExecutionPolicy Bypass -File $(LIFECYCLE) build

install:
	$(POWERSHELL) -NoProfile -ExecutionPolicy Bypass -File $(LIFECYCLE) install

uninstall:
	$(POWERSHELL) -NoProfile -ExecutionPolicy Bypass -File $(LIFECYCLE) uninstall

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
