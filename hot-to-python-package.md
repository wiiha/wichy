# Packaging Your Python Project with pyproject.toml

## Step 1: Prepare Your Entry Point

Edit `wichy.py` to ensure it has a proper `main()` function:

```python
def main():
    # Your main application logic here
    pass

if __name__ == "__main__":
    main()
```

## Step 2: Create pyproject.toml

Create a `pyproject.toml` file in your project root:

```toml
[build-system]
requires = ["setuptools>=61.0", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "wichy"
version = "0.1.0"
description = "A short description of your project"
readme = "README.md"
authors = [
    {name = "Your Name", email = "your.email@example.com"}
]
license = {text = "MIT"}
classifiers = [
    "Development Status :: 3 - Alpha",
    "Intended Audience :: Developers",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.8",
    "Programming Language :: Python :: 3.9",
    "Programming Language :: Python :: 3.10",
    "Programming Language :: Python :: 3.11",
    "License :: OSI Approved :: MIT License",
    "Operating System :: OS Independent",
]
requires-python = ">=3.8"
dependencies = [
    # Copy dependencies from requirements.txt here
    # Example format:
    # "requests>=2.28.0",
    # "anthropic>=0.18.0",
]

[project.urls]
Homepage = "https://github.com/yourusername/wichy"
Repository = "https://github.com/yourusername/wichy"
"Bug Tracker" = "https://github.com/yourusername/wichy/issues"

[project.scripts]
wichy = "wichy:main"

[tool.setuptools]
packages = ["agents", "helpers", "tools"]

[tool.setuptools.package-data]
"*" = ["*.sh", "*.md"]
llama_server_stuff = ["*"]
```

**Important**: Copy the dependencies from your `requirements.txt` into the `dependencies` array in the proper format.

## Step 3: Create MANIFEST.in

Create a `MANIFEST.in` file to include non-Python files:

```
include README.md
include requirements.txt
include LICENSE
recursive-include llama_server_stuff *
include run_llama_server.sh
global-exclude __pycache__
global-exclude *.py[co]
```

## Step 4: Create .gitignore

Create or update your `.gitignore`:

```
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
build/
develop-eggs/
dist/
downloads/
eggs/
.eggs/
lib/
lib64/
parts/
sdist/
var/
wheels/
*.egg-info/
.installed.cfg
*.egg
.venv/
venv/
ENV/
```

## Step 5: Add a LICENSE File

If you don't have one, create a `LICENSE` file. For MIT License:

```
MIT License

Copyright (c) 2024 Your Name

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Step 6: Install in Development Mode

Test your package locally in editable mode:

```bash
pip install -e .
```

This installs your package so you can test it while still making changes. After this, you can run:

```bash
wichy
```

from anywhere in your terminal.

## Step 7: Build Your Package

Install the build tool:

```bash
pip install build
```

Build your package:

```bash
python -m build
```

This creates two files in the `dist/` directory:
- `wichy-0.1.0-py3-none-any.whl` (wheel distribution)
- `wichy-0.1.0.tar.gz` (source distribution)

## Step 8: Test the Built Package

Create a fresh virtual environment and test:

```bash
# Create new virtual environment
python -m venv test_env
source test_env/bin/activate  # On Windows: test_env\Scripts\activate

# Install your built package
pip install dist/wichy-0.1.0-py3-none-any.whl

# Test it
wichy
```

## Step 9: Publish to PyPI (Optional)

If you want to publish to PyPI:

```bash
# Install twine
pip install twine

# Upload to TestPyPI first (recommended)
twine upload --repository testpypi dist/*

# If everything works, upload to PyPI
twine upload dist/*
```

You'll need to create accounts on:
- [TestPyPI](https://test.pypi.org/account/register/) (for testing)
- [PyPI](https://pypi.org/account/register/) (for production)

## Important Notes

### Large Binary Files

Your `llama_server_stuff` directory contains large .gguf model files. Consider these options:

**Option 1: Exclude from package (Recommended)**
- Add to `.gitignore`
- Document where users should download them
- Add a download script

**Option 2: Download on first run**
- Create a setup function that downloads models when needed
- Store them in user's home directory or app data folder

**Option 3: Separate data package**
- Create a separate package for models
- Make it an optional dependency

### Example: Excluding large files

Update your `pyproject.toml`:

```toml
[tool.setuptools.package-data]
"*" = ["*.sh", "*.md"]
# Remove this line: llama_server_stuff = ["*"]
```

Add to `.gitignore`:

```
llama_server_stuff/*.gguf
```

Document in your README where users should place model files.

## Updating Your Package

When you make changes:

1. Update the version number in `pyproject.toml`
2. Rebuild: `python -m build`
3. Reinstall: `pip install --force-reinstall dist/wichy-0.1.0-py3-none-any.whl`

Or use development mode (`pip install -e .`) to avoid rebuilding during development.

## Troubleshooting

**Module not found errors**: Make sure all your packages have `__init__.py` files.

**Entry point not working**: Verify the `main()` function exists in `wichy.py` and is callable.

**Files not included**: Check your `MANIFEST.in` and `package-data` settings.

**Import errors**: Make sure you're importing using package names, e.g., `from agents import sub_agent` not `from agents.sub_agent import ...`