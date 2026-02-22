# syntax=docker/dockerfile:1
FROM python:3.11-slim AS base

WORKDIR /app

# Install system deps
RUN apt-get update && \
    apt-get install -y --no-install-recommends git curl && \
    rm -rf /var/lib/apt/lists/*

# Copy and install Python deps
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e ".[pipelines]" 2>/dev/null || pip install --no-cache-dir -e .

# Copy application code
COPY . .

# Expose API port
EXPOSE 8100

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8100/health || exit 1

# Default command: start the orchestrator API server
CMD ["python", "-m", "orchestrator", "serve"]
