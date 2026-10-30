# wichy Docker Image - "Fat" Single-Stage Variant (Rootless Docker)
#
# This is the rootless-friendly variant of fat.Dockerfile.
# In rootless Docker the container's root UID maps to the host user who
# started the container, so a dedicated unprivileged container user is
# unnecessary and can complicate file ownership on mounted volumes.
# This file removes the wichy user setup while keeping everything else
# identical to fat.Dockerfile.
#
# Bundled language toolchains: Go, Node.js, Rust (selected from TARGETARCH)
#
# Build:
#   docker build -f fat.rootless.Dockerfile -t wichy-fat-rootless .
#
# Run:
#   docker run -it --rm \
#     -v /path/to/project:/workspace \
#     -p 7891:7891 \
#     --add-host=host.docker.internal:host-gateway \
#     wichy-fat-rootless
#
# With ~/.wichy mount (skills, root agents):
#   docker run -it --rm \
#     -v /path/to/project:/workspace \
#     -v ~/.wichy:/root/.wichy \
#     -p 7891:7891 \
#     --add-host=host.docker.internal:host-gateway \
#     wichy-fat-rootless

FROM python:3.12-slim

# BuildKit sets this to amd64 or arm64; every toolchain below is fetched to match it.
ARG TARGETARCH

# -----------------------------------------------------------------------------
# Layer 1: System packages, tools, user setup (changes LEAST often)
# All system-level setup in ONE consolidated RUN to minimize layers
# -----------------------------------------------------------------------------
RUN set -eu; \
    ARCH="${TARGETARCH:-$(dpkg --print-architecture)}"; \
    case "${ARCH}" in \
      amd64) GO_ARCH="amd64"; NODE_ARCH="x64"; RUST_ARCH="x86_64" ;; \
      arm64) GO_ARCH="arm64"; NODE_ARCH="arm64"; RUST_ARCH="aarch64" ;; \
      *) echo "Unsupported architecture: ${ARCH}" >&2; exit 1 ;; \
    esac; \
    apt-get update && apt-get install -y --no-install-recommends \
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
    openssh-client \
    sqlite3 \
    poppler-utils \
    # Node.js and Rust runtimes need these; build-essential pulls them in today
    libatomic1 \
    libstdc++6 \
    # tar -J for the .tar.xz archives
    xz-utils \
    && rm -rf /var/lib/apt/lists/* \
    # Install ripgrep (not in Debian slim repos — fetch from GitHub releases)
    && RG_VERSION="15.2.0" \
    && curl -fsSL "https://github.com/BurntSushi/ripgrep/releases/download/${RG_VERSION}/ripgrep-${RG_VERSION}-${RUST_ARCH}-unknown-linux-musl.tar.gz" -o /tmp/ripgrep.tar.gz \
    && tar -xzf /tmp/ripgrep.tar.gz -C /tmp \
    && mv /tmp/ripgrep-"${RG_VERSION}"-${RUST_ARCH}-unknown-linux-musl/rg /usr/local/bin/rg \
    && rm -rf /tmp/ripgrep.tar.gz /tmp/ripgrep-*-${RUST_ARCH}-unknown-linux-musl \
    # Install Go (pinned version)
    && GO_VERSION="1.25.0" \
    && curl -fsSL "https://go.dev/dl/go${GO_VERSION}.linux-${GO_ARCH}.tar.gz" -o /tmp/go.tar.gz \
    && tar -C /usr/local -xzf /tmp/go.tar.gz \
    && rm /tmp/go.tar.gz \
    && ln -s /usr/local/go/bin/go /usr/local/bin/go \
    && ln -s /usr/local/go/bin/gofmt /usr/local/bin/gofmt \
    # Install Node.js (pinned LTS)
    && NODE_VERSION="26.13.0" \
    && curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${NODE_ARCH}.tar.xz" -o /tmp/node.tar.xz \
    && tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1 --exclude=CHANGELOG.md --exclude=README.md --exclude=LICENSE \
    && rm /tmp/node.tar.xz \
    # Install Rust (pinned rustup)
    && RUSTUP_VERSION="1.29.2" \
    && curl -fsSL "https://static.rust-lang.org/rustup/archive/${RUSTUP_VERSION}/${RUST_ARCH}-unknown-linux-gnu/rustup-init" -o /tmp/rustup-init \
    && chmod +x /tmp/rustup-init \
    && RUSTUP_HOME=/usr/local/rustup CARGO_HOME=/usr/local/cargo /tmp/rustup-init -y \
         --profile minimal --default-toolchain stable --no-modify-path \
    && RUSTUP_HOME=/usr/local/rustup CARGO_HOME=/usr/local/cargo /usr/local/cargo/bin/rustup component add rustfmt clippy \
    && rm /tmp/rustup-init \
    # Create the Python venv at /opt/wichy
    && python -m venv /opt/wichy \
    # Set proper ownership and permissions on /opt/wichy
    && chmod -R 755 /opt/wichy \
    && mkdir -p /root/.npm-global \
    # Create workspace directory for user mounts
    && mkdir -p /workspace

# -----------------------------------------------------------------------------
# Layer 2: Dependency metadata (changes only when dependencies change)
# -----------------------------------------------------------------------------
COPY pyproject.toml README.md /build/

# -----------------------------------------------------------------------------
# Layer 3: Playwright + browser install (changes only when deps change)
# Extract playwright version from pyproject.toml and install browser
# This avoids ~106MB download on every source code change
# -----------------------------------------------------------------------------
ENV PLAYWRIGHT_BROWSERS_PATH="/root/.cache/ms-playwright"
RUN /opt/wichy/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/wichy/bin/pip install --no-cache-dir "$(grep -oP 'playwright==[\d.]+' /build/pyproject.toml)" \
    && /opt/wichy/bin/playwright install chromium-headless-shell

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

# Environment variables
ENV PATH="/usr/local/go/bin:/usr/local/cargo/bin:/opt/wichy/bin:/root/.npm-global/bin:$PATH"
ENV RUSTUP_HOME="/usr/local/rustup"
ENV CARGO_HOME="/usr/local/cargo"
ENV NPM_CONFIG_PREFIX="/root/.npm-global"
ENV WICHY_CONTAINER=1
ENV WICHY_OLLAMA_BASE_URL="http://host.docker.internal:11434/v1"
ENV WICHY_SERVER_HOST="0.0.0.0"

# Working directory for mounted projects
WORKDIR /workspace

# Expose the server port
EXPOSE 7891

# Entrypoint - wichy starts REPL by default when no args passed
ENTRYPOINT ["/opt/wichy/bin/wichy"]
