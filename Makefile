.PHONY: help install test test-verbose build build-wheel build-sdist clean clean-all run-server server check-manifest lint format

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

# Install in editable mode with dependencies
install:
	@echo "Installing wichy in editable mode..."
	pip install -e .

# Run tests
test:
	@echo "Running tests..."
	pytest tests/

test-verbose:
	@echo "Running tests (verbose)..."
	pytest tests/ -vv

# Build package
build: clean build-wheel build-sdist

build-wheel:
	@echo "Building wheel..."
	python -m build --wheel

build-sdist:
	@echo "Building source distribution..."
	python -m build --sdist

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
	python -m wichy.server

server: run-server

# Check manifest to ensure all files are included
check-manifest:
	@echo "Checking MANIFEST.in and package data..."
	@echo "Files in src/wichy/templates:"
	@ls src/wichy/templates/ 2>/dev/null || echo "  (none)"
	@echo ""
	@echo "Files in src/wichy/graph/static:"
	@ls src/wichy/graph/static/ 2>/dev/null || echo "  (none)"
	@echo ""
	@echo "To verify wheel contents, run after build:"
	@echo "  unzip -l dist/wichy-*.whl | grep -E '(templates|graph/static)'"

# Linting (requires ruff or flake8)
lint:
	@echo "Linting code..."
	@command -v ruff >/dev/null 2>&1 && ruff . || \
	command -v flake8 >/dev/null 2>&1 && flake8 || \
	echo "No linter found. Install with: pip install ruff"

# Code formatting (requires black)
format:
	@echo "Formatting code..."
	@command -v black >/dev/null 2>&1 && black src/ tests/ || \
	echo "Black not found. Install with: pip install black"

# Quick development cycle: install, test, build
dev: install test
	@echo "Development setup complete!"

# Package validation (requires twine)
validate: build
	@echo "Validating package..."
	@command -v twine >/dev/null 2>&1 && twine check dist/* || \
	echo "twine not found. Install with: pip install twine"
