# syntax=docker/dockerfile:1

# ---------- build stage ----------
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build \
 # config.js is generated at runtime by nginx; drop the dev copy from the bundle
 && rm -f dist/config.js

# ---------- runtime stage ----------
FROM nginxinc/nginx-unprivileged:1.27-alpine AS runtime

# Runtime configuration (override per environment; never baked into the bundle):
#   API_BASE_URL     backend base URL; empty = same origin (Kubernetes Ingress routes /api)
#   APP_ENV          development | staging | production (informational)
#   CSP_CONNECT_SRC  extra origins the SPA may call (e.g. http://localhost:8000 when the API is on another origin)
#   CSP_IMG_SRC      extra image origins (e.g. the OBS/object-storage endpoint serving product images)
ENV API_BASE_URL="" \
    APP_ENV="production" \
    CSP_CONNECT_SRC="" \
    CSP_IMG_SRC="" \
    NGINX_ENVSUBST_FILTER="^(API_BASE_URL|APP_ENV|CSP_CONNECT_SRC|CSP_IMG_SRC)$" \
    NGINX_ENVSUBST_OUTPUT_DIR="/etc/nginx/conf.d"

USER root
RUN rm -f /etc/nginx/conf.d/default.conf
COPY nginx/*.template /etc/nginx/templates/
COPY --from=build /app/dist /usr/share/nginx/html
RUN chown -R 101:101 /etc/nginx/conf.d && chmod -R a+rX /usr/share/nginx/html /etc/nginx/templates
USER 101

EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1:8080/health || exit 1
