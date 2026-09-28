# Build frontend
FROM node:20-alpine AS frontend-build
WORKDIR /app
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ .
RUN npm run build

# Production image
FROM python:3.12-slim-bookworm
WORKDIR /app

# Install uv using the official standalone binary (no apt/pip needed)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Ensure virtualenv and uv tool binaries (semgrep) are on PATH
ENV PATH="/root/.local/bin:/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

# Copy Python dependency declarations and install
COPY backend/pyproject.toml backend/uv.lock* ./
RUN uv sync --frozen --no-dev
RUN uv tool install semgrep

# Copy backend source
COPY backend/ ./

# Copy Next.js static export into static folder
COPY --from=frontend-build /app/out ./static

# Healthcheck using Python's built-in urllib (avoids needing curl via apt-get)
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Expose FastAPI port
EXPOSE 8000

# Start the server
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]