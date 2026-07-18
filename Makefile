.PHONY: help install test test-verbose build build-wheel build-sdist clean clean-all run-server server check-manifest lint format pipx-install pipx-reinstall pipx-uninstall docker-build docker-run docker-clean

# Virtual environment
VENV_PATH ?= host_venv
VENV_ACTIVATE = . $(VENV_PATH)/bin/activate &&

# Default target
help:
	@echo "Wichy - Agentic LLM for coding"
	@echo ""
	@echo "Available targets:"
	@echo "  install          - Install project dependencies in editable mode"
	@echo "  test             - Run pytest tests"
	@echo "  test-verbose     - Run tests with verbose output"
	@echo "  build            - Build both wheel and sdist"
	@echo "  build-wheel      - Build wheel only"
	@echo "  build-sdist      - Build source distribution only"
	@echo "  clean            - Clean build artifacts (build/, dist/, *.egg-info)"
	@echo "  clean-all        - Clean everything including virtual environment and caches"
	@echo "  run-server       - Start the Flask server in foreground"
	@echo "  server           - Alias for run-server"
	@echo "  check-manifest   - Verify all required files are included in package"
	@echo "  lint             - Run linter (ruff or flake8)"
	@echo "  format           - Auto-format code (black)"
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

# Install in editable mode with dependencies
install:
	@echo "Installing wichy in editable mode..."
	$(VENV_ACTIVATE) pip install -e .

# Run tests
test:
	@echo "Running tests..."
	$(VENV_ACTIVATE) pytest tests/

test-verbose:
	@echo "Running tests (verbose)..."
	$(VENV_ACTIVATE) pytest tests/ -vv

# Build package
build: clean build-wheel build-sdist

build-wheel:
	@echo "Building wheel..."
	$(VENV_ACTIVATE) python -m build --wheel

build-sdist:
	@echo "Building source distribution..."
	$(VENV_ACTIVATE) python -m build --sdist

# Clean build artifacts
clean:
	@echo "Cleaning build artifacts..."
	rm -rf build/ dist/ *.egg-info/
	@echo "Clean complete."

# Deep clean (including venv, caches)
clean-all: clean
	@echo "Cleaning all generated files..."
	rm -rf venv .venv .pytest_cache htmlcov .coverage .tox/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	@echo "Deep clean complete."

# Run the server
run-server:
	@echo "Starting Wichy server..."
	$(VENV_ACTIVATE) python -m wichy.server

server: run-server

# Check manifest to ensure all files are included
check-manifest:
	@echo "Checking MANIFEST.in and package data..."
	@echo ""
	@echo "=== Static assets in source ==="
	@find src/wichy -type d -name static -exec sh -c 'echo "\n{}:"; ls -la {} 2>/dev/null || echo "  (empty)"' \;
	@echo ""
	@echo "=== Templates in source ==="
	@find src/wichy -type d -name templates -exec sh -c 'echo "\n{}:"; ls -la {} 2>/dev/null || echo "  (empty)"' \;
	@echo ""
	@echo "=== Building wheel and checking contents ==="
	$(VENV_ACTIVATE) python -m build --wheel 2>/dev/null
	@echo ""
	@echo "=== Static assets in wheel ==="
	@unzip -l dist/wichy-*.whl 2>/dev/null | grep -E '/static/' | head -20 || echo "  (none found)"
	@echo ""
	@echo "=== Templates in wheel ==="
	@unzip -l dist/wichy-*.whl 2>/dev/null | grep -E '/templates/' | head -20 || echo "  (none found)"
	@echo ""
	@echo "=== Comparing source vs wheel ==="
	@echo "Source static files: $$(find src/wichy -path '*/static/*' -type f | wc -l | tr -d ' ')"
	@echo "Wheel static files:  $$(unzip -l dist/wichy-*.whl 2>/dev/null | grep -E '/static/' | wc -l | tr -d ' ')"
	@echo "Source templates:    $$(find src/wichy -path '*/templates/*' -type f | wc -l | tr -d ' ')"
	@echo "Wheel templates:     $$(unzip -l dist/wichy-*.whl 2>/dev/null | grep -E '/templates/' | wc -l | tr -d ' ')"

# Linting (requires ruff or flake8)
lint:
	@echo "Linting code..."
	$(VENV_ACTIVATE) (command -v ruff >/dev/null 2>&1 && ruff check --fix) || \
	(command -v flake8 >/dev/null 2>&1 && flake8) || \
	echo "No linter found. Install with: pip install ruff"

# Code formatting (requires black)
format:
	@echo "Formatting code..."
	$(VENV_ACTIVATE) command -v black >/dev/null 2>&1 && black --target-version py310 src/ tests/ || \
	echo "Black not found. Install with: pip install black"

# Quick development cycle: install, test, build
dev: install test
	@echo "Development setup complete!"

# Package validation (requires twine)
validate: build
	@echo "Validating package..."
	$(VENV_ACTIVATE) command -v twine >/dev/null 2>&1 && twine check dist/* || \
	echo "twine not found. Install with: pip install twine"

# Pipx installation (isolated env, like production)
pipx-install: build
	@echo "Installing via pipx..."
	pipx install --force dist/wichy-*.whl

pipx-reinstall: pipx-uninstall build
	@echo "Reinstalling via pipx..."
	pipx install dist/wichy-*.whl

pipx-uninstall:
	@echo "Uninstalling from pipx..."
	-pipx uninstall wichy 2>/dev/null || true

# Docker targets
docker-build: build-wheel
	@echo "Building Docker image..."
	docker build -t wichy .

docker-run:
	@echo "Running Docker container..."
	docker run -it --rm -v $(PWD):/workspace -p 7891:7891 --add-host=host.docker.internal:host-gateway wichy

docker-clean:
	@echo "Removing Docker image..."
	docker rmi wichy 2>/dev/null || true
