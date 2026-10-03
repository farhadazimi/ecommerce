# syntax=docker/dockerfile:1.7
# Order Service image. Build from the repository root:
#   docker build -f order-service/Dockerfile -t ecommerce-order-service .
ARG PYTHON_IMAGE=python:3.12-slim

# ---------------------------------------------------------------- build stage
FROM ${PYTHON_IMAGE} AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
WORKDIR /build
COPY constraints.txt ./
COPY libs/common ./libs/common
COPY order-service/requirements.txt ./requirements.txt
RUN pip install -c constraints.txt ./libs/common -r requirements.txt \
    && find /opt/venv -name "__pycache__" -prune -exec rm -rf {} +

# ---------------------------------------------------------------- runtime stage
FROM ${PYTHON_IMAGE} AS runtime
LABEL org.opencontainers.image.title="ecommerce-order-service" \
      org.opencontainers.image.description="Plan B e-commerce Order Service (internal, Pardis Cloud CCE)"
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    APP_ENV=production \
    SERVICE_NAME=order-service \
    PORT=8001
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY order-service/order_service ./order_service
RUN python -m compileall -q /app /opt/venv/lib
USER 10001:10001
EXPOSE 8001
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=2).status == 200 else 1)"]
STOPSIGNAL SIGTERM
CMD ["sh", "-c", "exec uvicorn order_service.main:create_app --factory --host 0.0.0.0 --port ${PORT} --no-access-log --no-proxy-headers --no-server-header --timeout-keep-alive 75 --timeout-graceful-shutdown 25"]
