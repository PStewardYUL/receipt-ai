# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 — Build React frontend
# ─────────────────────────────────────────────────────────────────────────────
FROM node:20-alpine AS frontend-build

WORKDIR /build

# Install dependencies first (layer cache)
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --legacy-peer-deps

# Copy source and build
COPY frontend/ ./
RUN npm run build
# Output: /build/dist/


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 — Python backend + embedded static files
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# System deps:
#   curl        — used by Docker health check
#   poppler-utils — pdftoppm for PDF→image rasterisation (optional but recommended)
#   libgl1, libglib2.0-0 — required for PaddleOCR image processing
#   libgomp1    — required for PaddlePaddle (OpenMP)
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        poppler-utils \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps (separate layer for cache efficiency)
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend source
COPY backend/ .

# Copy React build output into /app/static/
# FastAPI StaticFiles mounts this directory and serves it directly.
COPY --from=frontend-build /build/dist/ /app/static/

# Persistent data dirs (override with volume mounts)
RUN mkdir -p /data /logs

# ─────────────────────────────────────────────────────────────────────────────
ENV PYTHONUNBUFFERED=1 \
    DATABASE_PATH=/data/receipts.db \
    LOG_LEVEL=INFO

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
