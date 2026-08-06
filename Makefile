# Resolve an interpreter without assuming a virtualenv exists. A clean clone has no
# .venv, and the one command a reviewer types must not be the one that fails.
PY ?= $(shell command -v python3 2>/dev/null || command -v python)
VENV := .venv/bin/python
ifneq ($(wildcard $(VENV)),)
PY := $(VENV)
endif

.PHONY: help setup run session strategies verify test audit figures clean

help:
	@echo "make run        replay every session end to end and judge it   <- start here"
	@echo "make session    replay a single session"
	@echo "make strategies list what can be plugged in"
	@echo "make verify     run the test suite"
	@echo "make audit      replay the scanner autopsy (stdlib only, ~1s)"
	@echo "make figures    regenerate docs/figures (stdlib SVG, deterministic)"
	@echo "make setup      create .venv with pinned deps"

setup:
	$(PY) -m venv .venv
	.venv/bin/pip install -q --upgrade pip
	.venv/bin/pip install -q -r requirements.txt
	@echo "ready — now run: make verify"

verify:
	@# No -q here: pytest.ini already sets it, and a second -q suppresses the
	@# "N passed" line — leaving the one command a reviewer runs with no result.
	@$(PY) -m pytest || (echo ""; \
	  echo "If this failed on a missing package, run 'make setup' first."; exit 1)

test: verify

audit:
	@$(PY) scripts/replay_hour_scan.py

clean:
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf .pytest_cache

run:
	@$(PY) -m src.app.cli backtest

session:
	@$(PY) -m src.app.cli run

strategies:
	@$(PY) -m src.app.cli strategies

figures:
	@$(PY) scripts/make_figures.py
