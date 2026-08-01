# =============================================================================
# AI Platform — AI Core Service
# =============================================================================
.PHONY: help dev run test lint format migrate docker-build docker-push clean

# Default
help: ## Show available commands
	@echo "AI Platform — AI Core Service"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# =============================================================================
# Development
# =============================================================================

install: ## Install all dependencies
	pip install -e ".[dev]"

dev: ## Start dev server with auto-reload
	PYTHONPATH=src uvicorn ai_platform.main:app --host 0.0.0.0 --port 8000 --reload

run: ## Start production server
	PYTHONPATH=src uvicorn ai_platform.main:app --host 0.0.0.0 --port 8000 --workers 4

# =============================================================================
# Testing
# =============================================================================

test: ## Run all tests (unit + integration)
	PYTHONPATH=src pytest tests/ -v --tb=short

test-unit: ## Run unit tests only
	PYTHONPATH=src pytest tests/unit/ -v --tb=short

test-integration: ## Run integration tests (requires DB + Redis)
	PYTHONPATH=src pytest tests/integration/ -v --tb=short

test-cov: ## Run tests with coverage report
	PYTHONPATH=src pytest tests/ -v --cov=ai_platform --cov-report=term-missing --cov-report=html

# =============================================================================
# Code Quality
# =============================================================================

lint: ## Run linters (ruff + mypy)
	ruff check src/ tests/
	ruff format --check src/ tests/
	mypy src/ai_platform/

format: ## Auto-fix and format code
	ruff check --fix src/ tests/
	ruff format src/ tests/

typecheck: ## Run type checking only
	mypy src/ai_platform/

# =============================================================================
# Database
# =============================================================================

migrate-init: ## Initialize Alembic
	alembic init alembic

migrate-create: ## Create new migration (usage: make migrate-create msg="add_users_table")
	alembic revision --autogenerate -m "$(msg)"

migrate-up: ## Apply all pending migrations
	alembic upgrade head

migrate-down: ## Revert last migration
	alembic downgrade -1

migrate-history: ## Show migration history
	alembic history --verbose

# =============================================================================
# Docker
# =============================================================================

docker-build: ## Build Docker image
	docker build -t ai-platform-core:latest .

docker-run: ## Run Docker container locally
	docker run -p 8000:8000 --env-file .env ai-platform-core:latest

docker-push: ## Push Docker image to registry (set REGISTRY env var)
	docker tag ai-platform-core:latest $(REGISTRY)/ai-platform-core:latest
	docker push $(REGISTRY)/ai-platform-core:latest

# =============================================================================
# Infrastructure (from repo root)
# =============================================================================

infra-up: ## Start all infrastructure services
	cd ../../infra && docker compose up -d

infra-down: ## Stop infrastructure services
	cd ../../infra && docker compose down

infra-logs: ## View infrastructure logs
	cd ../../infra && docker compose logs -f

infra-prod: ## Start full production stack (infra + app)
	cd ../../infra && docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build

# =============================================================================
# Utilities
# =============================================================================

clean: ## Clean generated files
	rm -rf .pytest_cache htmlcov .coverage .mypy_cache .ruff_cache __pycache__
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true

shell: ## Open Python shell with project context
	PYTHONPATH=src python -c "from ai_platform.config import get_settings; print('Settings loaded:', get_settings().app_env)"

health: ## Check local server health
	curl -s http://localhost:8000/health | python -m json.tool

metrics: ## Fetch Prometheus metrics
	curl -s http://localhost:8000/metrics | head -30
