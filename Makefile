.PHONY: help install test test-verbose build build-wheel build-sdist clean clean-all run-server server check-manifest lint format typecheck check dev validate pipx-install pipx-reinstall pipx-uninstall docker-build docker-run docker-clean

.DELETE_ON_ERROR:

# Virtual environment:
#   - host_venv on the host system (where the user develops)
#   - venv inside the docker container (created by the agent)
# WICHY_CONTAINER=1 is set in the Dockerfile, same signal the app uses
VENV_PATH ?= $(shell echo $$WICHY_CONTAINER | grep -q 1 && echo venv || echo host_venv)
# Fail fast with a fix hint if the venv is missing, then activate per recipe line
VENV_ACTIVATE = test -x "$(VENV_PATH)/bin/activate" || { echo "Venv '$(VENV_PATH)' not found — run: make VENV_PATH=$(VENV_PATH) install" >&2; exit 1; }; . "$(VENV_PATH)/bin/activate" &&

# Default target
help:
	@echo "Wichy - Agentic LLM for coding"
	@echo ""
	@echo "Available targets:"
	@echo "  install          - Install project dependencies in editable mode (with dev extras)"
	@echo "  test             - Run pytest tests"
	@echo "  test-verbose     - Run tests with verbose output"
	@echo "  build            - Build both wheel and sdist"
	@echo "  build-wheel      - Build wheel only"
	@echo "  build-sdist      - Build source distribution only"
	@echo "  clean            - Clean build artifacts (build/, dist/, *.egg-info)"
	@echo "  clean-all        - Clean everything including virtual environment and caches"
	@echo "  run-server       - Start the Flask server in foreground (via wichy CLI)"
	@echo "  server           - Alias for run-server"
	@echo "  check-manifest   - Verify all required files are included in package"
	@echo "  lint             - Run linter (ruff)"
	@echo "  format           - Auto-format code (black)"
	@echo "  typecheck        - Run mypy type checking"
	@echo "  check            - lint + typecheck + test"
	@echo "  dev              - Quick dev cycle: install + test"
	@echo "  validate         - Validate built distributions with twine"
	@echo ""
	@echo "Pipx targets (isolated production installs):"
	@echo "  pipx-install     - Install via pipx from local wheel"
	@echo "  pipx-reinstall   - Reinstall via pipx (uninstall + install)"
	@echo "  pipx-uninstall   - Uninstall from pipx"
	@echo ""
	@echo "Docker targets:"
	@echo "  docker-build     - Build the Docker image"
	@echo "  docker-run       - Run the container interactively"
	@echo "  docker-clean     - Remove the Docker image"
	@echo ""

# Install in editable mode with dependencies and dev tooling
install:
	@echo "Installing wichy in editable mode with dev extras..."
	$(VENV_ACTIVATE) pip install -e ".[dev]"

# Run tests
test:
	@echo "Running tests..."
	$(VENV_ACTIVATE) pytest tests/

test-verbose:
	@echo "Running tests (verbose)..."
	$(VENV_ACTIVATE) pytest tests/ -vv

# Build package (sequential: clean first, then wheel, then sdist)
build: clean
	@echo "Building wheel and sdist..."
	$(MAKE) build-wheel
	$(MAKE) build-sdist

build-wheel:
	@echo "Building wheel..."
	$(VENV_ACTIVATE) python -m build --wheel

build-sdist:
	@echo "Building source distribution..."
	$(VENV_ACTIVATE) python -m build --sdist

# Clean build artifacts
clean:
	@echo "Cleaning build artifacts..."
	rm -rf build/ dist/ *.egg-info/ src/*.egg-info/
	@echo "Clean complete."

# Deep clean (including venv, caches)
clean-all: clean
	@echo "Cleaning all generated files..."
	rm -rf $(VENV_PATH) venv .venv host_venv .pytest_cache htmlcov .coverage .tox/ \
		.ruff_cache .mypy_cache tmp \
		.eggs eggs develop-eggs downloads sdist wheels var parts
	find . \( -path ./$(VENV_PATH) -o -path ./host_venv \) -prune -o \
		-type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . \( -path ./$(VENV_PATH) -o -path ./host_venv \) -prune -o \
		-type f -name "*.pyc" -exec rm -f {} + 2>/dev/null || true
	@echo "Deep clean complete."

# Run the server (canonical entrypoint via the wichy CLI)
run-server:
	@echo "Starting Wichy server..."
	$(VENV_ACTIVATE) wichy server

server: run-server

# Check manifest to ensure all files are included
check-manifest:
	@echo "Checking MANIFEST.in and package data..."
	@echo ""
	@echo "=== Static assets in source ==="
	@find src/wichy -type d -name static -exec sh -c 'printf "\n%s:\n" "$$1"; ls -la "$$1" 2>/dev/null || echo "  (empty)"' _ {} \;
	@echo ""
	@echo "=== Templates in source ==="
	@find src/wichy -type d -name templates -exec sh -c 'printf "\n%s:\n" "$$1"; ls -la "$$1" 2>/dev/null || echo "  (empty)"' _ {} \;
	@echo ""
	@echo "=== Building fresh wheel (dist/ wiped first) ==="
	rm -rf dist/
	$(VENV_ACTIVATE) python -m build --wheel 2>/dev/null
	@echo ""
	@echo "=== Static assets in wheel ==="
	@$(VENV_ACTIVATE) python -m zipfile -l dist/wichy-*.whl 2>/dev/null | grep -E '/static/' | head -20 || echo "  (none found)"
	@echo ""
	@echo "=== Templates in wheel ==="
	$(VENV_ACTIVATE) python -m zipfile -l dist/wichy-*.whl 2>/dev/null | grep -E '/templates/' | head -20 || echo "  (none found)"
	@echo ""
	@echo "=== Comparing source vs wheel ==="
	@echo "Source static files: $$(find src/wichy -path '*/static/*' -type f | wc -l | tr -d ' ')"
	@echo "Wheel static files:  $$($(VENV_ACTIVATE) python -m zipfile -l dist/wichy-*.whl 2>/dev/null | grep -E '/static/' | wc -l | tr -d ' ')"
	@echo "Source templates:    $$(find src/wichy -path '*/templates/*' -type f | wc -l | tr -d ' ')"
	@echo "Wheel templates:     $$($(VENV_ACTIVATE) python -m zipfile -l dist/wichy-*.whl 2>/dev/null | grep -E '/templates/' | wc -l | tr -d ' ')"

# Linting (ruff, explicit paths so venvs/tmp/notes are never touched)
lint:
	@echo "Linting code..."
	@$(VENV_ACTIVATE) command -v ruff >/dev/null 2>&1 || { echo "ruff not found. Install with: make install (dev extra includes ruff)"; exit 1; }
	$(VENV_ACTIVATE) ruff check --fix src/ tests/

# Code formatting (black, explicit paths)
format:
	@echo "Formatting code..."
	@$(VENV_ACTIVATE) command -v black >/dev/null 2>&1 || { echo "black not found. Install with: make install (dev extra includes black)"; exit 1; }
	$(VENV_ACTIVATE) black --target-version py310 src/ tests/

# Type checking (mypy)
typecheck:
	@echo "Type checking..."
	@$(VENV_ACTIVATE) command -v mypy >/dev/null 2>&1 || { echo "mypy not found. Install with: make install (dev extra includes mypy)"; exit 1; }
	$(VENV_ACTIVATE) mypy src/

# All quality gates: lint + typecheck + test
check: lint typecheck test
	@echo "All checks complete."

# Quick development cycle: install, test, build
dev: install test
	@echo "Development setup complete!"

# Package validation (requires twine)
validate: build
	@echo "Validating package..."
	@$(VENV_ACTIVATE) command -v twine >/dev/null 2>&1 || { echo "twine not found. Install with: make install (dev extra includes twine)"; exit 1; }
	$(VENV_ACTIVATE) twine check dist/*

# Pipx installation (isolated env, like production)
pipx-install: build-wheel
	@echo "Installing via pipx..."
	@test -n "$$(ls dist/wichy-*.whl 2>/dev/null)" || { echo "No wheel in dist/ — run: make build-wheel"; exit 1; }
	pipx install --force dist/wichy-*.whl

pipx-reinstall: pipx-uninstall build-wheel
	@echo "Reinstalling via pipx..."
	@test -n "$$(ls dist/wichy-*.whl 2>/dev/null)" || { echo "No wheel in dist/ — run: make build-wheel"; exit 1; }
	pipx install dist/wichy-*.whl

pipx-uninstall:
	@echo "Uninstalling from pipx..."
	pipx uninstall wichy 2>/dev/null || true

# Docker targets
docker-build:
	@echo "Building Docker image..."
	docker build -t wichy .

docker-run:
	@echo "Running Docker container..."
	docker run -it --rm -v $(CURDIR):/workspace -p "${WICHY_HOST_PORT:-7891}":7891 --add-host=host.docker.internal:host-gateway wichy

docker-clean:
	@echo "Removing Docker image..."
	docker rmi wichy 2>/dev/null || true