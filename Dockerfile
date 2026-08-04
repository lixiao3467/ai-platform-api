# AI Core Service — Multi-stage Docker build

# =============================================================================
# Stage 1: Builder — install dependencies
# =============================================================================
FROM python:3.11-slim AS builder

WORKDIR /build

# Install uv for fast dependency resolution
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency files first (layer caching — rarely changes)
COPY pyproject.toml README.md ./

# Install dependencies to a virtual environment
RUN uv venv /build/.venv && \
    uv pip install --python /build/.venv/bin/python .

# =============================================================================
# Stage 2: Runtime — minimal production image
# =============================================================================
FROM python:3.11-slim AS runtime

# Install curl for healthcheck (lighter than importing Python + httpx)
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/* && \
    groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /build/.venv /app/.venv

# Copy application source (changes frequently — kept last for cache efficiency)
COPY src/ /app/src/
COPY alembic/ /app/alembic/
COPY alembic.ini /app/
COPY config/ /app/config/
COPY pyproject.toml /app/

# Add venv to PATH
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app/src"
ENV PYTHONUNBUFFERED=1

# Non-root ownership of /app so the app user can write temp files if needed
RUN chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Health check — fast liveness probe using curl (no Python overhead)
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://localhost:8000/live > /dev/null || exit 1

# Expose port
EXPOSE 8000

# Run the application
# PORT env var is set by Railway (defaults to 8000)
CMD ["sh", "-c", "python -m uvicorn ai_platform.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
