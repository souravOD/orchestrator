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

# Authenticate against private ConferInc repos via build-time token
ARG GITHUB_TOKEN

# Install pipeline packages so `python -m <module>` works in subprocess mode.
# Pin to specific tags/commits in production for reproducibility.
RUN pip install --no-cache-dir \
    "prebronze-to-bronze @ git+https://${GITHUB_TOKEN}@github.com/ConferInc/prebronze-to-bronze.git@main" \
    "bronze-to-silver @ git+https://${GITHUB_TOKEN}@github.com/ConferInc/bronze-to-silver.git@main" \
    "silver-to-gold @ git+https://${GITHUB_TOKEN}@github.com/ConferInc/silver-to-gold.git@main" \
    "nutrition-usda-fetcher @ git+https://${GITHUB_TOKEN}@github.com/ConferInc/Nutrition_USDA_Fetching.git@main"

# Clone Gold-to-Neo4j pipeline for in-process import by neo4j_adapter
RUN git clone --depth 1 https://${GITHUB_TOKEN}@github.com/ConferInc/gold-to-neo4j.git /app/gold-to-neo4j \
    && pip install --no-cache-dir -r /app/gold-to-neo4j/requirements.txt 2>/dev/null || true

# Copy application code
COPY . .

# Expose API port
EXPOSE 8100

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8100/health || exit 1

# Default command: start the orchestrator API server
CMD ["python", "-m", "orchestrator", "serve"]
