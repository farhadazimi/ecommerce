# syntax=docker/dockerfile:1.7
# Backend API image. Build from the repository root (shared library is in libs/common):
#   docker build -f backend/Dockerfile -t ecommerce-backend .
ARG PYTHON_IMAGE=python:3.12-slim

# ---------------------------------------------------------------- build stage
FROM ${PYTHON_IMAGE} AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
WORKDIR /build
COPY constraints.txt ./
COPY libs/common ./libs/common
COPY backend/requirements.txt ./requirements.txt
RUN pip install -c constraints.txt ./libs/common -r requirements.txt \
    && find /opt/venv -name "__pycache__" -prune -exec rm -rf {} +

# ---------------------------------------------------------------- runtime stage
FROM ${PYTHON_IMAGE} AS runtime
LABEL org.opencontainers.image.title="ecommerce-backend" \
      org.opencontainers.image.description="Plan B e-commerce Backend API (Pardis Cloud CCE)"
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    APP_ENV=production \
    SERVICE_NAME=backend-api \
    PORT=8000
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY backend/backend_api ./backend_api
# migrations + seed ship with the backend image; they run as a one-off Job, never at app start
COPY database ./database
RUN python -m compileall -q /app /opt/venv/lib
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"]
STOPSIGNAL SIGTERM
# One worker per container: scale with replicas/HPA. Keep-alive > ELB idle timeout to avoid 502s.
CMD ["sh", "-c", "exec uvicorn backend_api.main:create_app --factory --host 0.0.0.0 --port ${PORT} --no-access-log --no-proxy-headers --no-server-header --timeout-keep-alive 75 --timeout-graceful-shutdown 25"]
