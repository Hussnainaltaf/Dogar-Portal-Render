# ===========================================================================
# Dogar Portal — Production Dockerfile (multi-stage: frontend + Python)
#
# Three-stage build:
#   Stage 1 (frontend)  → Node.js 22, npm ci, tsc + vite build → app/static/spa
#   Stage 2 (builder)   → Python slim, pip install --prefix=/install
#   Stage 3 (runtime)   → Python slim-slim, copy deps + app, non-root user
#
# The .dockerignore MUST be present alongside this file — it prevents secrets,
# live data, caches, and the .git directory from leaking into image layers.
# ===========================================================================

# ──────────────────────────────────────────────────────────────────────────
# Stage 1 — Frontend SPA build
# ──────────────────────────────────────────────────────────────────────────
FROM node:22-slim AS frontend

# WORKDIR MUST be /app/frontend so that vite's outDir path.resolve(__dirname,
# '../static/spa') resolves to /app/static/spa — matching the COPY --from
# destination in the runtime stage.
WORKDIR /app/frontend

# Install deps.
#
# AUDIT 2026-08-04 (Security): the React Router security override resolves to
# react-router 8.x, whose package declares Node >=22. The frontend build stage
# therefore uses Node 22 so Docker builds do not rely on an unsupported engine.
#
# AUDIT 2026-07-31 (Medium): this stage copied only `package.json` and ran
# `npm install`, which RE-RESOLVES the whole tree on every build. Two
# consequences: image builds are not reproducible (a patch release of any
# transitive dep silently changes what ships), and the layer cache is defeated
# because npm rewrites package-lock.json inside the layer.
#
# `package*.json` copies the lockfile too *when it exists*, and `npm ci` is used
# in preference to `npm install` because it installs the locked tree verbatim
# and is materially faster. `npm ci` hard-fails without a lockfile, so it falls
# back to `npm install` for source trees that genuinely ship without one.
COPY app/frontend/package*.json ./
RUN if [ -f package-lock.json ]; then \
        npm ci --no-audit --no-fund; \
    else \
        echo "WARNING: no package-lock.json -- falling back to npm install (build is not reproducible)"; \
        npm install --no-audit --no-fund; \
    fi

# Copy source and build → output lands at /app/static/spa
COPY app/frontend/ ./
RUN npm run build

# ──────────────────────────────────────────────────────────────────────────
# Stage 2 — Python deps installer
# ──────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# System build deps for Pillow, reportlab, freetype-py, qrcode
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libjpeg-dev \
        zlib1g-dev \
        libfreetype6-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ──────────────────────────────────────────────────────────────────────────
# Stage 3 — Runtime (the actual deployable image)
# ──────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

LABEL org.opencontainers.image.title="Dogar Portal"
LABEL org.opencontainers.image.description="Multi-tenant overseas employment management portal"
LABEL org.opencontainers.image.vendor="HussnainTechVertex Pvt Ltd"

# ── Runtime OS deps ──────────────────────────────────────────────────────
# freetype6 / fontconfig → Python freetype-py (Arabic letterhead renderer)
# libpangocairo + libcairo2 → reportlab Cairo backend (PDF generation)
# tesseract-ocr            → offline passport OCR (optional but recommended)
# libheif1                 → HEIC/HEIF iPhone photo support
# zlib1g + libjpeg62-turbo → Pillow image processing
# curl                     → health-check probe
RUN apt-get update && apt-get install -y --no-install-recommends \
        libfreetype6 \
        fontconfig \
        libpangocairo-1.0-0 \
        libcairo2 \
        tesseract-ocr \
        libheif1 \
        libjpeg62-turbo \
        zlib1g \
        curl \
    && rm -rf /var/lib/apt/lists/*

# ── Python packages from builder ─────────────────────────────────────────
COPY --from=builder /install /usr/local

# ── Non-root user ────────────────────────────────────────────────────────
RUN groupadd -r dtc && useradd -r -g dtc -u 1000 -d /app dtc

WORKDIR /app

# ── Application source (excluding .git, .env, data/*, caches per .dockerignore) ─
COPY --chown=dtc:dtc . .

# ── Frontend build from Stage 1 ──────────────────────────────────────────
# Stage 1 WORKDIR is /app/frontend; vite outDir ../static/spa → /app/static/spa
COPY --from=frontend --chown=dtc:dtc /app/static/spa /app/app/static/spa

# ── Runtime directories (SQLite + uploads + PDF backgrounds) ─────────────
RUN mkdir -p data \
             data/tenants \
             app/static/uploads/photos \
             app/static/uploads/avatars \
             app/static/uploads/generated_pdfs \
             app/static/pdf_backgrounds \
    && chown -R dtc:dtc data app/static/uploads app/static/pdf_backgrounds

# ── Entrypoint ───────────────────────────────────────────────────────────
RUN chmod +x docker-entrypoint.sh

USER dtc

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:3000/api/ping -o /dev/null || exit 1

# Entrypoint applies schema migrations (main DB + per-tenant if applicable),
# then execs uvicorn as PID 1. For the rationale around --workers 1 with
# SQLite, see docker-entrypoint.sh and DEPLOY.md § L-6.
#
# H-01: --forwarded-allow-ips 127.0.0.1 — uvicorn only trusts X-Forwarded-For
# from the local reverse proxy, preventing IP spoofing against rate limiters.
ENTRYPOINT ["./docker-entrypoint.sh"]
