.PHONY: setup setup-python setup-frontend test test-backend test-frontend build-frontend verify verify-full pipeline-skip-ai dashboard clean-pyc

PYTHON ?= python3
PIP ?= pip
FRONTEND_DIR := dashboard/frontend

setup: setup-python setup-frontend

setup-python:
	$(PYTHON) -m venv venv
	. venv/bin/activate && $(PIP) install -r requirements.txt -r requirements-dev.txt

setup-frontend:
	cd $(FRONTEND_DIR) && npm install

test: test-backend test-frontend

test-backend:
	pytest

test-frontend:
	cd $(FRONTEND_DIR) && npm test

build-frontend:
	cd $(FRONTEND_DIR) && npm run build

verify:
	bash scripts/verify_local.sh

verify-full:
	bash scripts/verify_local.sh --full

pipeline-skip-ai:
	bash run_master.sh --skip-ai

dashboard:
	bash start_dashboard.sh

clean-pyc:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type f -name '*.pyc' -delete
