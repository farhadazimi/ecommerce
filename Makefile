# Plan B e-commerce platform - developer & CI entry points.   `make help` lists targets.
SHELL := /bin/bash
PY ?= python3.12
VENV ?= .venv
BIN := $(VENV)/bin
REGISTRY ?= local
TAG ?= dev
OVERLAY ?= local
PLAYWRIGHT_IMAGE ?= mcr.microsoft.com/playwright/python:v1.52.0-noble

.DEFAULT_GOAL := help
.PHONY: help install lint test test-mysql coverage frontend-install frontend-lint frontend-test frontend-build \
        check up down logs ps seed e2e ui-e2e images push k8s-render k8s-validate k8s-deploy kind-up kind-down lock clean

help: ## show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# ------------------------------------------------------------------ local setup
install: ## create the Python venv and install all dependencies (+ frontend)
	$(PY) -m venv $(VENV)
	$(BIN)/pip install -q --upgrade pip
	$(BIN)/pip install -q -c constraints.txt -r requirements-dev.txt
	cd frontend && npm ci --no-audit --no-fund

lock: ## regenerate constraints.txt from the venv
	@echo "# Pinned runtime dependency versions (pip install -c constraints.txt). Regenerate with: make lock" > constraints.txt
	$(BIN)/pip freeze --exclude-editable | grep -viE '^(pytest|pytest-cov|coverage|ruff|pyyaml|iniconfig|pluggy|packaging|playwright|greenlet|pyee)==' >> constraints.txt

# ------------------------------------------------------------------ quality
lint: ## ruff (Python) + eslint/tsc (frontend)
	$(BIN)/ruff check .
	cd frontend && npm run lint --silent

test: ## all Python unit + API + integration tests (SQLite, in-memory cache, local storage)
	$(BIN)/pytest -q

test-mysql: ## same tests against MySQL 8 + Redis (set TEST_DATABASE_URL / TEST_REDIS_HOST)
	@test -n "$$TEST_DATABASE_URL" || (echo "set TEST_DATABASE_URL=mysql+pymysql://user:pw@host:3306/db" && exit 1)
	$(BIN)/pytest -q

coverage: ## tests with coverage report
	$(BIN)/pytest -q --cov --cov-report=term-missing:skip-covered --cov-report=xml

frontend-test: ## vitest
	cd frontend && npm test --silent

frontend-build: ## production bundle
	cd frontend && npm run build --silent

check: lint test frontend-test ## everything CI runs before building images

# ------------------------------------------------------------------ docker compose
up: ## build + start the full local stack (http://localhost:8080)
	docker compose up -d --build
	@echo "Storefront http://localhost:8080 | API docs http://localhost:8000/api/docs | admin@example.com / Admin123!"

down: ## stop the stack and delete local volumes
	docker compose down -v

logs: ## follow application logs
	docker compose logs -f backend order-service frontend

ps: ## stack status
	docker compose ps

seed: ## (re)load demo data into the compose database
	docker compose run --rm -e SEED_DEMO_DATA=true migrate

e2e: ## API end-to-end tests against a running stack (E2E_API_URL, default compose)
	E2E_API_URL=$${E2E_API_URL:-http://localhost:8000} E2E_FRONTEND_URL=$${E2E_FRONTEND_URL:-http://localhost:8080} \
		$(BIN)/pytest tests/e2e -m e2e -v

ui-e2e: ## browser end-to-end test (Playwright in Docker) against BASE_URL (default compose)
	docker run --rm --network host -v "$(CURDIR)/tests/e2e:/e2e" -e BASE_URL=$${BASE_URL:-http://localhost:8080} \
		-e API_URL=$${API_URL:-http://localhost:8000} $(PLAYWRIGHT_IMAGE) \
		sh -c "pip install -q --break-system-packages playwright==1.52.0 httpx >/dev/null && python /e2e/ui_flow.py"

# ------------------------------------------------------------------ images
images: ## build the three images as $(REGISTRY)/ecommerce-*:$(TAG)
	docker build -f backend/Dockerfile -t $(REGISTRY)/ecommerce-backend:$(TAG) .
	docker build -f order-service/Dockerfile -t $(REGISTRY)/ecommerce-order-service:$(TAG) .
	docker build -t $(REGISTRY)/ecommerce-frontend:$(TAG) frontend

push: images ## push images to $(REGISTRY) (e.g. SWR: swr.<region>.<domain>/<org>)
	for i in backend order-service frontend; do docker push $(REGISTRY)/ecommerce-$$i:$(TAG); done

# ------------------------------------------------------------------ kubernetes
k8s-render: ## print the manifests of OVERLAY (local|staging|production)
	kubectl kustomize infrastructure/kubernetes/overlays/$(OVERLAY)

k8s-validate: ## schema-validate every overlay with kubeconform
	@for o in local staging production; do \
		echo "== $$o"; kubectl kustomize infrastructure/kubernetes/overlays/$$o | kubeconform -strict -summary -kubernetes-version 1.31.0 - || exit 1; \
	done

k8s-deploy: ## deploy OVERLAY to the current kube-context (migrations first, then rollout)
	scripts/k8s-deploy.sh $(OVERLAY)

kind-up: ## local Kubernetes rehearsal: kind + ingress-nginx + full stack (http://shop.localtest.me)
	scripts/kind-up.sh

kind-down: ## delete the kind cluster
	kind delete cluster --name ecommerce

clean: ## remove caches and build output
	rm -rf .pytest_cache .ruff_cache .coverage coverage.xml htmlcov frontend/dist
	find . -name __pycache__ -prune -exec rm -rf {} +
