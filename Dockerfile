# wichy Docker Image
#
# Build:
#   docker build -t wichy .
#
# Run:
#   docker run -it --rm \
#     -v /path/to/project:/workspace \
#     -p 7891:7891 \
#     --add-host=host.docker.internal:host-gateway \
#     wichy
#
# With ~/.wichy mount (skills, root agents):
#   docker run -it --rm \
#     -v /path/to/project:/workspace \
#     -v ~/.wichy:/home/wichy/.wichy \
#     -p 7891:7891 \
#     --add-host=host.docker.internal:host-gateway \
#     wichy

# -----------------------------------------------------------------------------
# Stage 1: Builder
# -----------------------------------------------------------------------------
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create venv for build
RUN python -m venv /build/venv
ENV PATH="/build/venv/bin:$PATH"

# Install build tool (after venv is created)
RUN pip install --no-cache-dir --upgrade pip build

# Copy pyproject.toml and README.md FIRST (for better layer caching)
COPY pyproject.toml README.md ./

# THEN copy source code
COPY src/ ./src/

# Build the wheel
RUN python -m build --wheel

# -----------------------------------------------------------------------------
# Stage 2: Runtime
# -----------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

# Install Playwright system dependencies for Chromium directly
# (avoiding the pip install playwright && playwright install-deps pattern)
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Core Chromium dependencies
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
    # Additional Chromium dependencies
    libatspi2.0-0 \
    libxshmfence1 \
    libglu1-mesa \
    fonts-liberation \
    libappindicator3-1 \
    libdbus-1-3 \
    libx11-xcb1 \
    libxcb1 \
    && rm -rf /var/lib/apt/lists/*

# Create the wichy user with home directory and locked password
RUN useradd -r -m -d /home/wichy -s /bin/bash wichy && passwd -l wichy

# Create the Python venv at /opt/wichy (owned by root, readable by wichy)
RUN python -m venv /opt/wichy

# Copy wheel from builder stage
COPY --from=builder /build/dist/wichy-*.whl /tmp/

# Install wichy wheel plus all dependencies into the venv
RUN /opt/wichy/bin/pip install --no-cache-dir /tmp/wichy-*.whl && \
    rm /tmp/wichy-*.whl

# Create playwright browser cache directory and set ownership for wichy user
RUN mkdir -p /home/wichy/.cache/ms-playwright && \
    chown -R wichy:wichy /home/wichy/.cache

# Install Playwright chromium-headless-shell browser (as root, with PLAYWRIGHT_BROWSERS_PATH set)
# This installs browsers to /home/wichy/.cache/ms-playwright which wichy user can access
# Using chromium-headless-shell saves ~580MB compared to full chromium
ENV PLAYWRIGHT_BROWSERS_PATH="/home/wichy/.cache/ms-playwright"
RUN /opt/wichy/bin/playwright install chromium-headless-shell

# Set proper ownership and permissions on /opt/wichy
# Root owns the venv, wichy group can read/execute
RUN chown -R root:wichy /opt/wichy && \
    chmod -R 755 /opt/wichy

# Create workspace directory for user mounts
RUN mkdir -p /workspace && chown -R wichy:wichy /workspace

# Switch to wichy user
USER wichy

# Environment variables
ENV PATH="/opt/wichy/bin:$PATH"
ENV WICHY_CONTAINER=1
ENV WICHY_OLLAMA_BASE_URL="http://host.docker.internal:11434/v1"
ENV WICHY_SERVER_HOST="0.0.0.0"

# Working directory for mounted projects
WORKDIR /workspace

# Expose the server port
EXPOSE 7891

# Entrypoint - wichy starts REPL by default when no args passed
ENTRYPOINT ["/opt/wichy/bin/wichy"]