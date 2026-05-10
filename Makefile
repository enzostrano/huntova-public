# Huntova — common dev commands.
# Run `make help` for the menu. All targets are .PHONY (no real files
# are produced inside the repo by these rules — wheel/sdist build to
# `dist/`).

PYTHON ?= python
VENV ?= .venv

.PHONY: help install install-dev test test-fast lint format build clean release-check

help:
	@echo "Huntova development commands:"
	@echo ""
	@echo "  make install       Install the CLI from this checkout (pip install .)"
	@echo "  make install-dev   Editable install + pytest deps (pip install -e .[dev])"
	@echo "  make test          Run the full pytest suite"
	@echo "  make test-fast     Run pytest with -x (stop on first failure)"
	@echo "  make lint          Static checks: AST parse + CSS brace balance"
	@echo "  make format        No-op placeholder — black isn't wired up yet"
	@echo "  make build         Build wheel + sdist into dist/"
	@echo "  make release-check Run the full pre-release checklist (PY + CSS + pytest)"
	@echo "  make clean         Remove build/, dist/, *.egg-info/, __pycache__/"
	@echo ""
	@echo "Variables:"
	@echo "  PYTHON=$(PYTHON)   Override the Python interpreter"
	@echo ""

install:
	$(PYTHON) -m pip install .

install-dev:
	$(PYTHON) -m pip install -e .
	$(PYTHON) -m pip install pytest pytest-asyncio

test:
	$(PYTHON) -m pytest tests/ -q

test-fast:
	$(PYTHON) -m pytest tests/ -x --tb=short

lint:
	$(PYTHON) -c "import ast; [ast.parse(open(f, encoding='utf-8').read()) for f in ['server.py','cli.py','app.py','db.py']]; print('PY OK')"
	$(PYTHON) -c "s=open('static/style.css', encoding='utf-8').read(); print('CSS OK' if s.count('{')==s.count('}') else 'CSS MISMATCH')"

format:
	@echo "Formatter not wired up. Add black/ruff to pyproject.toml and update this target."

build:
	$(PYTHON) -m pip install --quiet build
	$(PYTHON) -m build

release-check: lint test
	@echo ""
	@echo "All pre-release checks passed."
	@echo "Next: bump cli.py + pyproject.toml, append CHANGELOG, run the orphan-push pipeline (see docs/RELEASE.md)."

clean:
	rm -rf build/ dist/ *.egg-info __pycache__ .pytest_cache
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name '*.pyc' -delete
