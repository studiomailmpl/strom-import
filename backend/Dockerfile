# ── STRØM Import API — Production Dockerfile ──
# Multi-stage build for minimal image size

FROM python:3.11-slim AS base

# System deps for asyncpg + cryptography + PDF processing
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc g++ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create upload directories
RUN mkdir -p /tmp/strom-uploads/product-images

# Non-root user for security
RUN useradd -m -s /bin/bash strom && chown -R strom:strom /app /tmp/strom-uploads
USER strom

# Railway injects PORT; default to 8000 for local Docker
ENV PORT=8000

# Health check (uses $PORT dynamically)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://localhost:{os.environ.get(\"PORT\",8000)}/health')" || exit 1

EXPOSE ${PORT}

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 2 --loop uvloop"]
