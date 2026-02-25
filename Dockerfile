# syntax=docker/dockerfile:1
FROM python:3.11-slim AS base

WORKDIR /app

# Install system deps
RUN apt-get update && \
    apt-get install -y --no-install-recommends git curl && \
    rm -rf /var/lib/apt/lists/*

# Copy and install orchestrator deps (no [pipelines] extra needed)
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e .

# Install pipeline packages so `python -m <module>` works in subprocess mode.
# Pin to specific tags/commits in production for reproducibility.
# TODO: Replace these placeholder URLs with your actual repository URLs.
RUN pip install --no-cache-dir \
    "prebronze-to-bronze @ git+https://github.com/your-org/prebronze-to-bronze.git@main" \
    "bronze-to-silver @ git+https://github.com/your-org/bronze-to-silver.git@main" \
    "silver-to-gold @ git+https://github.com/your-org/silver-to-gold.git@main" \
    "nutrition-usda-fetcher @ git+https://github.com/your-org/nutrition-usda-fetcher.git@main"

# Copy application code
COPY . .

# Expose API port
EXPOSE 8100

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8100/health || exit 1

# Default command: start the orchestrator API server
CMD ["python", "-m", "orchestrator", "serve"]
