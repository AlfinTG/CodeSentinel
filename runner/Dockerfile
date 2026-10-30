# ==============================================================================
# CodeSentinel Isolated Check Runner Container (Phase 1)
# ------------------------------------------------------------------------------
# Hardened, unprivileged execution environment for running deterministic check
# commands (pytest, python, npm, node).
#
# Security Controls Enforced:
#   1. Non-root user identity: UID 10001, GID 10001.
#   2. Read-only root filesystem compatible (--read-only).
#   3. Network disabled at runtime (--network none).
#   4. Linux capabilities dropped (--cap-drop=ALL).
#   5. No new privileges (--security-opt=no-new-privileges).
#   6. Working directory strictly confined to /workspace.
# ==============================================================================

FROM python:3.12-slim

# Install system dependencies, Node.js 20 LTS, and security certificates
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    build-essential \
    git \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Pre-install standard testing frameworks for Python and Node
RUN pip install --no-cache-dir \
    pytest>=8.0.0 \
    pytest-asyncio>=0.23.0 \
    pytest-cov>=4.1.0

# Create dedicated non-root user matching CodeSentinel config (10001:10001)
RUN groupadd --gid 10001 runnergroup \
    && useradd --uid 10001 --gid 10001 --create-home --shell /bin/bash runneruser

# Create workspace directory with correct permissions
RUN mkdir -p /workspace && chown -R runneruser:runnergroup /workspace

WORKDIR /workspace

# Switch to unprivileged runner user
USER 10001:10001

# Entrypoint default: report tool versions or wait for invocation
CMD ["python", "--version"]

