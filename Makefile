.DEFAULT_GOAL := help
.PHONY: help setup demo dev test lint check screenshots

UV ?= uv
TESTS ?=

help:
	@printf '%s\n' \
	  'make setup        Install the locked development dependencies' \
	  'make dev          Demo loop: quit the app, then Enter to load your edits' \
	  'make demo         Run the working source once with fresh synthetic data' \
	  'make test         Run tests (or pass TESTS="tests/test_tui.py -k clone")' \
	  'make lint         Check lint and formatting' \
	  'make check        Run lint, formatting, and the full test suite' \
	  'make screenshots  Refresh documentation captures after a UI batch'

setup:
	$(UV) sync --frozen

demo:
	$(UV) run --frozen snowbeam --demo

dev:
	@while true; do \
	  $(UV) run --frozen snowbeam --demo; \
	  printf '\nEnter: restart with current source and fresh demo data. q: finish. '; \
	  read answer || break; \
	  case "$$answer" in q|Q) break ;; esac; \
	done

test:
	$(UV) run --frozen pytest -q $(TESTS)

lint:
	$(UV) run --frozen ruff check src tests tools
	$(UV) run --frozen ruff format --check src tests tools

check: lint test

screenshots:
	$(UV) run --frozen python tools/capture_demo.py
