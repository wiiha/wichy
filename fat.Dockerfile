# wichy Docker Image - "Fat" Single-Stage Variant
#
# This is a consolidated single-stage version of the multi-stage Dockerfile.
# While it produces a larger image, it uses smart layer ordering to maximize
# Docker build cache efficiency:
#
# Layer 1: System packages + tools + user setup (changes least often)
# Layer 2: Dependency metadata (pyproject.toml + README — changes with deps only)
# Layer 3: Playwright + browser install (changes with deps only)
# Layer 4: Source code (changes most often during development)
# Layer 5: Build + install wheel (changes with source or deps)
#
# Build:
#   docker build -f fat.Dockerfile -t wichy-fat .
#
# Run:
#   docker run -it --rm \
#     -v /path/to/project:/workspace \
#     -p 7891:7891 \
#     --add-host=host.docker.internal:host-gateway \
#     wichy-fat
#
# With ~/.wichy mount (skills, root agents):
#   docker run -it --rm \
#     -v /path/to/project:/workspace \
#     -v ~/.wichy:/home/wichy/.wichy \
#     -p 7891:7891 \
#     --add-host=host.docker.internal:host-gateway \
#     wichy-fat

FROM python:3.12-slim

# -----------------------------------------------------------------------------
# Layer 1: System packages, tools, user setup (changes LEAST often)
# All system-level setup in ONE consolidated RUN to minimize layers
# -----------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Core Chromium dependencies (for Playwright)
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libcairo2 \
    libatspi2.0-0 \
    libxshmfence1 \
    libglu1-mesa \
    fonts-liberation \
    libappindicator3-1 \
    libdbus-1-3 \
    libx11-xcb1 \
    libxcb1 \
    # Version control
    git \
    # File/text utilities
    fd-find \
    jq \
    # Archive tools
    zip \
    unzip \
    gzip \
    # Build tools (for compiling native extensions)
    build-essential \
    # Process management
    procps \
    # Runtime utilities
    curl \
    wget \
    tmux \
    && rm -rf /var/lib/apt/lists/* \
    # Install ripgrep (not in Debian slim repos — fetch from GitHub releases)
    && RG_VERSION="14.1.0" \
    && curl -fsSL "https://github.com/BurntSushi/ripgrep/releases/download/${RG_VERSION}/ripgrep-${RG_VERSION}-x86_64-unknown-linux-musl.tar.gz" -o /tmp/ripgrep.tar.gz \
    && tar -xzf /tmp/ripgrep.tar.gz -C /tmp \
    && mv /tmp/ripgrep-"${RG_VERSION}"-x86_64-unknown-linux-musl/rg /usr/local/bin/rg \
    && rm -rf /tmp/ripgrep.tar.gz /tmp/ripgrep-*-x86_64-unknown-linux-musl \
    # Install Go (pinned version)
    && GO_VERSION="1.25.0" \
    && curl -fsSL "https://go.dev/dl/go${GO_VERSION}.linux-amd64.tar.gz" -o /tmp/go.tar.gz \
    && tar -C /usr/local -xzf /tmp/go.tar.gz \
    && rm /tmp/go.tar.gz \
    && ln -s /usr/local/go/bin/go /usr/local/bin/go \
    && ln -s /usr/local/go/bin/gofmt /usr/local/bin/gofmt \
    # Create the wichy user with home directory and locked password
    && useradd -r -m -d /home/wichy -s /bin/bash wichy && passwd -l wichy \
    # Create the Python venv at /opt/wichy
    && python -m venv /opt/wichy \
    # Set proper ownership and permissions on /opt/wichy (root owns, wichy group can read/execute)
    && chown -R root:wichy /opt/wichy \
    && chmod -R 755 /opt/wichy \
    # Create workspace directory for user mounts
    && mkdir -p /workspace && chown -R wichy:wichy /workspace

# -----------------------------------------------------------------------------
# Layer 2: Dependency metadata (changes only when dependencies change)
# -----------------------------------------------------------------------------
COPY pyproject.toml README.md /build/

# -----------------------------------------------------------------------------
# Layer 3: Playwright + browser install (changes only when deps change)
# Extract playwright version from pyproject.toml and install browser
# This avoids ~106MB download on every source code change
# -----------------------------------------------------------------------------
ENV PLAYWRIGHT_BROWSERS_PATH="/home/wichy/.cache/ms-playwright"
RUN /opt/wichy/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/wichy/bin/pip install --no-cache-dir "$(grep -oP 'playwright==[\d.]+' /build/pyproject.toml)" \
    && /opt/wichy/bin/playwright install chromium-headless-shell \
    && chown -R wichy:wichy /home/wichy/.cache

# -----------------------------------------------------------------------------
# Layer 4: Source code (changes MOST often during development)
# Note: pyproject.toml and README.md copied again for build context
# -----------------------------------------------------------------------------
COPY src/ /build/src/

# -----------------------------------------------------------------------------
# Layer 5: Build and install wheel (changes with source or deps)
# -----------------------------------------------------------------------------
RUN /opt/wichy/bin/pip install --no-cache-dir --upgrade build \
    && cd /build \
    && /opt/wichy/bin/python -m build --wheel \
    && /opt/wichy/bin/pip install --no-cache-dir /build/dist/wichy-*.whl \
    && rm -rf /build

# Switch to wichy user
USER wichy

# Environment variables
ENV PATH="/usr/local/go/bin:/opt/wichy/bin:$PATH"
ENV WICHY_CONTAINER=1
ENV WICHY_OLLAMA_BASE_URL="http://host.docker.internal:11434/v1"
ENV WICHY_SERVER_HOST="0.0.0.0"

# Working directory for mounted projects
WORKDIR /workspace

# Expose the server port
EXPOSE 7891

# Entrypoint - wichy starts REPL by default when no args passed
ENTRYPOINT ["/opt/wichy/bin/wichy"]
