# Plan B Shop: cloud-ready e-commerce platform for Pardis Cloud (Phase 1)

A complete online store (storefront, checkout with payment simulation, PDF invoices, admin back office), built as
**Plan B for Mall-Swarm** and designed from the start for the **Phase 1 single-VPC architecture**:

```
Users → Cloud DNS → Anti-DDoS → CFW → WAF → ELB → CCE Ingress → Frontend | Backend API → Order Service
                                                                            ↓            ↓
                                                                   RDS MySQL · DCS Redis · OBS
```

It is containerised, runs on Kubernetes (CCE), keeps no state in containers, gets all configuration from the
environment, and consumes RDS, DCS and OBS through replaceable adapters. It depends on nothing from Mall-Swarm.

---

## Contents
1. [What the application is](#1-what-the-application-is)
2. [Architecture](#2-architecture)
3. [Technologies](#3-technologies)
4. [Local setup](#4-local-setup)
5. [Environment variables](#5-environment-variables)
6. [Database setup](#6-database-setup)
7. [Running with Docker](#7-running-with-docker)
8. [Running tests](#8-running-tests)
9. [API documentation](#9-api-documentation)
10. [Kubernetes deployment](#10-kubernetes-deployment)
11. [Production configuration](#11-production-configuration)
12. [OBS integration](#12-obs-integration)
13. [Redis integration](#13-redis-integration)
14. [RDS integration](#14-rds-integration)
15. [ELB / Ingress integration](#15-elb--ingress-integration)
16. [Health checks](#16-health-checks)
17. [Monitoring / logging](#17-monitoring--logging)
18. [Security considerations](#18-security-considerations)
19. [Troubleshooting](#19-troubleshooting)

---

## 1. What the application is

**Customers** can:
* register, log in, log out, and edit their profile (address, password, avatar)
* browse by category, search, filter by price and stock, sort, and view product details with images and live stock
* manage a cart (add, change quantity, remove, clear), with shipping cost and stock warnings
* check out with an idempotent order placement that reserves stock
* pay through a **payment simulator** (success and decline test cards, retry after a decline)
* get an order confirmation and a **PDF invoice** stored in object storage
* view their order history and order details (status timeline, payments), and cancel an unpaid order

**Administrators** can:
* see a dashboard: revenue, product, order and user counts, orders by status, low/out-of-stock counts, active users, recent orders
* manage products: create, edit, delete, upload images
* manage categories and inventory (set or adjust stock and thresholds)
* manage orders: search and filter, and change status (processing → shipped → delivered, or cancel with restock/refund)
* activate or deactivate users

Unpaid orders expire automatically after 30 minutes (their stock is released). Invoices that could not be stored
during an OBS outage are regenerated automatically.

**Demo accounts** (development seed only): `admin@example.com / Admin123!` and `customer@example.com / Customer123!`.

## 2. Architecture

| Component | Path | Role |
|---|---|---|
| Frontend | [`frontend/`](frontend) | React SPA served by unprivileged nginx. The backend URL comes from `/config.js` at runtime (`API_BASE_URL`). |
| Backend API | [`backend/`](backend) | Public REST API: auth, profile, catalog, cart, inventory, admin, stats. Delegates orders and payments. |
| Order Service | [`order-service/`](order-service) | Internal: order placement, stock reservation, payment gateway, invoices, lifecycle, reaper. |
| Shared library | [`libs/common/`](libs/common) | config, models, JSON logs, metrics, middleware, `StorageService` / `CacheService` / `NotificationService` adapters |
| Database | [`database/`](database) | Alembic migrations + idempotent demo seed |
| Infrastructure | [`infrastructure/kubernetes/`](infrastructure/kubernetes) | Kustomize base + `local` / `staging` / `production` overlays |

Full diagrams (cloud topology, internal communication, order sequence, ER model): **[docs/architecture.md](docs/architecture.md)**.

```
ecommerce/
├── frontend/                 React + TS + Vite, nginx template, Dockerfile, vitest
├── backend/                  backend_api/ (routers, services, schemas), tests/, Dockerfile
├── order-service/            order_service/ (orders, payment gateway, invoices, reaper), tests/, Dockerfile
├── libs/common/              ecommerce_common/ (shared by both Python services)
├── database/                 alembic.ini, migrations/, seed/seed.py
├── infrastructure/kubernetes base/ + overlays/{local,staging,production}
├── tests/                    unit/, integration/, e2e/ (live API + Playwright UI flow)
├── scripts/                  k8s-deploy.sh, kind-up.sh
├── docs/                     architecture.md, api.md, deployment.md, cloud-integration.md, openapi.json
├── docker-compose.yml        full local stack
├── .gitlab-ci.yml            lint → test → build → docker → deploy
├── Makefile                  `make help`
├── .env.example  .env.development.example  .env.staging.example  .env.production.example
└── constraints.txt           pinned Python dependency versions
```

## 3. Technologies

| Layer | Choice | Why |
|---|---|---|
| Backend & Order Service | Python 3.12, FastAPI, Pydantic v2, Uvicorn | typed validation, automatic OpenAPI, small footprint |
| Persistence | SQLAlchemy 2, Alembic, PyMySQL | MySQL 8 / RDS; row locks; migrations |
| Cache | redis-py | DCS Redis 5.0+ compatible |
| Object storage | boto3 (S3 API, SigV4) | OBS is S3-compatible; the same code runs against SeaweedFS locally |
| Security | bcrypt, PyJWT | password hashing, JWT |
| Invoices / images | ReportLab, Pillow | PDF generation, image validation and optimisation |
| Observability | JSON logging, prometheus-client | LTS / Cloud Eye |
| Frontend | React 18, TypeScript, Vite, React Router, plain CSS | – |
| Containers | multi-stage Docker, `python:3.12-slim`, `nginx-unprivileged` | non-root, read-only root filesystem |
| Orchestration | Kubernetes (CCE), Kustomize | HPA, PDB, NetworkPolicy, Jobs |
| CI/CD | GitLab CI | registry (SWR) configurable |
| Local stand-ins | MySQL 8.0, Redis 7.4, SeaweedFS 4.48 (S3) | mimic RDS / DCS / OBS |
| Tests | pytest, httpx, vitest + Testing Library, Playwright | unit → integration → E2E → browser |

## 4. Local setup

Requirements: Python 3.12, Node 22, Docker with Compose v2 (kind and kubectl for the Kubernetes rehearsal).

```bash
make install      # venv + pinned Python deps + frontend deps
make check        # lint + all tests
make up           # full stack in Docker → http://localhost:8080
```

To run the services directly without Docker, see [docs/deployment.md §1](docs/deployment.md#1-local-development).
Development mode works without Redis (in-memory fallback) and with the local filesystem storage adapter.

## 5. Environment variables

All configuration is read from the environment (`libs/common/ecommerce_common/config.py`). In **staging and
production the services refuse to start** if secrets are missing, too short, or still placeholders, or if a
dev-only adapter (local storage, in-memory cache) is selected.

| Group | Variables |
|---|---|
| Runtime | `APP_ENV` (development/test/staging/production), `SERVICE_NAME`, `LOG_LEVEL`, `LOG_JSON` |
| RDS | `DATABASE_HOST`, `DATABASE_PORT`, `DATABASE_NAME`, `DATABASE_USER`, `DATABASE_PASSWORD`*, `DATABASE_URL`, `DATABASE_POOL_SIZE`, `DATABASE_MAX_OVERFLOW`, `DATABASE_POOL_RECYCLE`, `DATABASE_SSL_CA` |
| DCS | `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD`*, `REDIS_DB`, `REDIS_SSL`, `REDIS_REQUIRED`, `CATALOG_CACHE_TTL`, `CART_CACHE_TTL` |
| OBS | `STORAGE_BACKEND` (s3/local), `OBS_ENDPOINT`, `OBS_PUBLIC_ENDPOINT`, `OBS_BUCKET`, `OBS_BUCKET_IMAGES`, `OBS_BUCKET_INVOICES`, `OBS_ACCESS_KEY`*, `OBS_SECRET_KEY`*, `OBS_REGION`, `OBS_ADDRESSING_STYLE`, `OBS_IMAGES_PUBLIC_BASE_URL`, `OBS_SIGNED_URL_TTL`, `MAX_UPLOAD_MB` |
| Security | `JWT_SECRET`*, `JWT_EXPIRES_MINUTES`, `INTERNAL_SERVICE_TOKEN`*, `COOKIE_SECURE`, `COOKIE_SAMESITE`, `COOKIE_DOMAIN`, `CORS_ORIGINS`, `TRUSTED_PROXIES`, `RATE_LIMIT_ENABLED`, `RATE_LIMIT_DEFAULT_PER_MINUTE`, `RATE_LIMIT_AUTH_PER_MINUTE`, `EXPOSE_API_DOCS` |
| Service discovery | `ORDER_SERVICE_URL` (`http://order-service:8001`), `ORDER_SERVICE_TIMEOUT`, `PUBLIC_BASE_URL` (local storage links only) |
| SMN | `NOTIFICATION_BACKEND` (log/webhook), `SMN_WEBHOOK_URL`, `SMN_WEBHOOK_TOKEN`* |
| Business | `CURRENCY`, `SHIPPING_FLAT_FEE`, `FREE_SHIPPING_THRESHOLD`, `ORDER_PAYMENT_TIMEOUT_MINUTES`, `ORDER_REAPER_ENABLED`, `ORDER_REAPER_INTERVAL_SECONDS` |
| Frontend container | `API_BASE_URL` (empty = same origin), `APP_ENV`, `CSP_CONNECT_SRC`, `CSP_IMG_SRC` |
| Seed (dev/demo) | `SEED_ADMIN_EMAIL`, `SEED_ADMIN_PASSWORD`, `SEED_CUSTOMER_EMAIL`, `SEED_CUSTOMER_PASSWORD`, `SEED_IMAGES`, `SEED_ALLOW_PRODUCTION` |

\* = secret: Kubernetes Secret `ecommerce-secrets`, never committed. Templates:
[`.env.example`](.env.example) (compose), [`.env.development.example`](.env.development.example),
[`.env.staging.example`](.env.staging.example), [`.env.production.example`](.env.production.example).

## 6. Database setup

The schema is created **only by migrations**; nothing is created by hand.

```bash
cd database && alembic upgrade head          # uses the DATABASE_* env vars
python database/seed/seed.py                 # optional demo data (idempotent; refused in production)
```

* Tables: `users`, `roles`, `user_profiles`, `categories`, `products`, `inventory`, `product_images`, `cart`,
  `cart_items`, `orders`, `order_items`, `payments`, `order_status_history`. The ADMIN/CUSTOMER roles are inserted by the migration.
* In Kubernetes the `db-migrate` Job runs `alembic upgrade head` before each rollout
  (`scripts/k8s-deploy.sh`). In compose, the `migrate` service does the same.
* `alembic check` runs in the test suite, so ORM models and migrations can never drift apart.

## 7. Running with Docker

```bash
cp .env.example .env    # optional
docker compose up -d --build     # or: make up
docker compose ps                # all services healthy
make e2e                         # API end-to-end against the stack
make ui-e2e                      # browser end-to-end (Playwright container)
docker compose down -v           # or: make down
```

| Service | Image | Port |
|---|---|---|
| frontend | `ecommerce-frontend` (nginx-unprivileged) | 8080 |
| backend | `ecommerce-backend` | 8000 (`/api/docs`) |
| order-service | `ecommerce-order-service` | internal 8001 |
| mysql / redis / object-storage | stand-ins for RDS / DCS / OBS | 3307 / – / 8333 |
| storage-init, migrate | one-off jobs (buckets, migrations + seed) | – |

The images are multi-stage, run as non-root (uid 10001 / 101), have a read-only root filesystem (compose `read_only: true`),
include `HEALTHCHECK`s, log to stdout, and default to `APP_ENV=production`, so a misconfigured deployment fails fast.

## 8. Running tests

```bash
make test          # 100+ pytest tests: unit + API + integration (SQLite, in-memory cache, local storage)
TEST_DATABASE_URL=mysql+pymysql://u:p@127.0.0.1:3306/ecommerce_test TEST_REDIS_HOST=127.0.0.1 \
TEST_MIGRATION_DATABASE_URL=mysql+pymysql://u:p@127.0.0.1:3306/migtest make test   # against MySQL 8 + Redis
make frontend-test # vitest (21 tests)
make lint          # ruff + eslint/tsc
make e2e / make ui-e2e   # against a running deployment (compose, kind, staging)
```

| Suite | Covers |
|---|---|
| `tests/unit` | config validation, password hashing, JWT (expiry, tampering, `alg=none`), pricing, cache fail-soft, storage adapters (bucket routing, signed URLs, path traversal), forwarded-header spoofing, request IDs, log masking |
| `backend/tests` | health, readiness, metrics, security headers, CORS, auth flows, rate limiting, categories, products (search, SQL-injection input, admin CRUD, images), cart, profile, admin |
| `order-service/tests` | service auth, order placement, stock reservation, idempotency, ownership, payments (success, decline, retry, idempotent confirm), invoices, OBS outage with deferred invoice, status machine, cancel/restock/refund, reaper, **10 concurrent checkouts (MySQL)** |
| `tests/integration` | the full 19-step customer flow through Backend → Order Service, checkout race, failure handling (DB/Redis/order-service/OBS down, invalid input, no leaks), migrations upgrade/check/downgrade + seed idempotency |
| `tests/e2e` | the live API flow and the Playwright browser flow (storefront + admin) against any deployment |

## 9. API documentation

* Reference with request, response and error examples: **[docs/api.md](docs/api.md)**
* OpenAPI/Swagger: `GET /api/docs` (live), [`docs/openapi.json`](docs/openapi.json) (exported)

Endpoint groups: Authentication, Profile, Products, Categories, Cart, Orders, Payment, Admin, Health.

## 10. Kubernetes deployment

Manifests are in [`infrastructure/kubernetes/`](infrastructure/kubernetes). The base holds `namespace.yaml`,
`configmap.yaml`, `secrets.yaml` (template), `frontend-deployment.yaml`, `frontend-service.yaml`,
`backend-deployment.yaml`, `backend-service.yaml`, `order-deployment.yaml`, `order-service.yaml`, `ingress.yaml`,
`hpa.yaml`, `pdb.yaml`, `networkpolicy.yaml` and `migration-job.yaml`.

```bash
make k8s-validate                      # kubeconform --strict, all overlays
make kind-up                           # local rehearsal: kind + ingress-nginx + metrics-server + full stack
E2E_API_URL=http://shop.localtest.me E2E_FRONTEND_URL=http://shop.localtest.me make e2e
scripts/k8s-deploy.sh production       # CCE (after configuring the overlay + Secret)
```

Step-by-step CCE instructions, including the SQL for the RDS user, the Secret creation and rollback: **[docs/deployment.md](docs/deployment.md)**.

## 11. Production configuration

* Edit `infrastructure/kubernetes/overlays/production/*.yaml`, replacing every `REPLACE_ME_*` value (RDS and DCS private addresses, OBS endpoint and region, domain, ELB id).
* Create the Secret `ecommerce-secrets` from a secure source; the CI does this from protected variables.
* `APP_ENV=production`, `COOKIE_SECURE=true`, `EXPOSE_API_DOCS=false`, `REDIS_REQUIRED=true`, `STORAGE_BACKEND=s3`.
* Push images to SWR (`make push REGISTRY=swr.<region>.<domain>/<org> TAG=x.y.z`) and set them with `kustomize edit set image`.
* Production runs 3 replicas per tier, HPA minimum 3, the ELB ingress class, and NodePort services for the ELB backends.

See [.env.production.example](.env.production.example) for every value with comments.

## 12. OBS integration

`StorageService` (`upload`, `download`, `delete`, `generate_signed_url`) has an S3 adapter for OBS and a local
adapter for development. Keys follow the document's layout: `images/products/…`, `images/avatars/…`,
`invoices/{yyyy}/{mm}/…`. They are routed to `OBS_BUCKET_IMAGES` / `OBS_BUCKET_INVOICES`, falling back to `OBS_BUCKET`.

* Buckets are private and browsers use pre-signed URLs; optionally, images can be served from a public/CDN URL.
* Invoices are uploaded with SSE requested.
* Uploaded images are re-encoded as WebP.

Details: [docs/cloud-integration.md §3](docs/cloud-integration.md#3-obs-object-storage-document-71).

## 13. Redis integration

Redis (DCS) caches the catalog (versioned keys), rendered carts, and categories. It also holds the logout
deny-list, the per-IP rate limits and active-user tracking. Every call has a 1 s timeout and is
**fail-soft**: if Redis is down, the app serves from RDS. `REDIS_REQUIRED=true` makes readiness depend on
Redis. Details: [docs/cloud-integration.md §2](docs/cloud-integration.md#2-dcs-for-redis-document-62).

## 14. RDS integration

* MySQL 8 dialect with a connection pool (`pool_pre_ping` and `pool_recycle`, sized per pod).
* `READ COMMITTED` isolation; `SELECT … FOR UPDATE` in primary-key order for stock.
* Optional TLS (`DATABASE_SSL_CA`).
* Migrations run as a Job, serialised with an advisory lock.
* Backups are RDS-native (automated daily, 7–35 days); there is no application backup code.

Details: [docs/cloud-integration.md §1](docs/cloud-integration.md#1-rds-for-mysql-document-61).

## 15. ELB / Ingress integration

* Ingress routing: `/api` → backend, `/` → frontend. The Order Service and `/metrics` are never exposed.
* TLS terminates at the ELB/WAF. The app trusts `X-Forwarded-For/-Proto/-Host` only from `TRUSTED_PROXIES`, and takes the client IP as the right-most untrusted hop (spoof-resistant).
* HSTS and secure cookies are used behind HTTPS.
* Uvicorn keep-alive (75 s) is longer than the ELB idle timeout.
* The production overlay uses the CCE ELB ingress (verify the annotations for your cluster).

Details: [docs/cloud-integration.md §4](docs/cloud-integration.md#4-elb-ingress-and-forwarded-headers-document-52-41).

## 16. Health checks

| Endpoint | Meaning | Used by |
|---|---|---|
| `GET /health` | process alive (never checks dependencies, so an RDS outage doesn't restart pods) | liveness / startup probes, ELB (frontend), Docker `HEALTHCHECK` |
| `GET /ready` | RDS reachable (+ DCS when required); 503 while draining | readiness probes |
| `GET /api/health` | liveness through the Ingress path | ELB / Cloud Eye HTTP probes |

## 17. Monitoring / logging

* Logs are JSON lines on stdout/stderr, collected by LTS. Each carries a `request_id` (propagated across services), a `user_id`, method, route, status, `duration_ms` and client IP. Secrets are masked.
* Business and audit events: `USER_REGISTERED`, `USER_LOGIN`, `PRODUCT_CREATED`, `PRODUCT_UPDATED`, `ORDER_CREATED`, `PAYMENT_SUCCESS`, `PAYMENT_FAILED`, `ORDER_STATUS_CHANGED`, `INVENTORY_UPDATED`, `INVOICE_GENERATED`, …
* Prometheus `/metrics` exposes request count and latency, error count, in-flight requests, active users, orders created, payments, payment failures by reason, invoices, and dependency status. CCE monitoring forwards these to Cloud Eye; the pods carry `prometheus.io/*` annotations.
* SMN: Cloud Eye alarms go to SMN topics, and the app publishes order notifications through the webhook adapter.

Recommended alarms and LTS searches: [docs/cloud-integration.md §5](docs/cloud-integration.md#5-observability-cloud-eye-lts-cts-smn-document-910).

## 18. Security considerations

* bcrypt password hashing; JWT (HS256, `exp`/`iss`/`jti`) in an **HttpOnly SameSite cookie** (`Secure` in production) or a Bearer header; logout revokes the token in Redis.
* Roles ADMIN/CUSTOMER enforced server-side; customers are strictly scoped to their own cart, orders and payments.
* The Order Service accepts only calls with the internal service token (constant-time compare), is ClusterIP-only, and a NetworkPolicy allows the backend alone.
* Pydantic validation on every input: markup characters are rejected in text fields, queries are parameterised, `LIKE` wildcards are escaped, and uploads are decoded and re-encoded with size and decompression limits.
* Security headers (CSP, frame-deny, nosniff, referrer policy, HSTS over HTTPS); strict CORS allow-list; per-IP rate limiting (general and auth).
* Errors never include stack traces or internals (`request_id` for support). The CVV is never stored; only the card's last 4 digits.
* No secrets in source or images. Placeholders are rejected at start-up. `.dockerignore` and `.gitignore` exclude `.env` files.
* Containers run as non-root with a read-only root filesystem, all capabilities dropped, `seccomp` RuntimeDefault, the PSA `restricted` profile, and no service-account token.
* Platform layers (Anti-DDoS, CFW, WAF, security groups, HSS) are complementary: see [docs/cloud-integration.md §6](docs/cloud-integration.md#6-iam-hss-cfw-waf-anti-ddos-vpc-flow-logs-nat-bastion).

## 19. Troubleshooting

| Problem | Fix |
|---|---|
| A container exits with `Insecure or incomplete configuration` | Provide the listed secrets/vars, or set `APP_ENV=development` for local runs |
| `/ready` is 503 | `curl /ready` shows which dependency failed (`checks.database` / `checks.redis`); check endpoints, security groups, credentials |
| 503 `ORDER_SERVICE_UNAVAILABLE` | order-service pods and endpoints, `ORDER_SERVICE_URL`, NetworkPolicy |
| 503 `STORAGE_UNAVAILABLE` | OBS endpoint, credentials, bucket names, NAT/VPC endpoint. Paid orders are safe; invoices are retried |
| CORS errors in the browser | add the exact storefront origin to `CORS_ORIGINS` |
| Images or invoices blocked | include the OBS origin in the frontend's `CSP_IMG_SRC`; `OBS_PUBLIC_ENDPOINT` must be reachable by browsers |
| Logged client IP is the proxy's | add the ingress/ELB CIDR to `TRUSTED_PROXIES` and enable `use-forwarded-headers` on ingress-nginx |
| Compose `migrate` fails | `docker compose logs migrate`; the DB volume from an older run can be reset with `make down` |
| HPA targets `<unknown>` | install metrics-server / the CCE monitoring add-on |

More: [docs/deployment.md §6](docs/deployment.md#6-troubleshooting).

---

### Known limitations
* Payments are simulated (`SimulatedPaymentGateway`). A real PSP means implementing `PaymentGateway` and adding a webhook endpoint.
* The CCE ELB-ingress annotations follow the Huawei-Cloud-compatible CCE conventions and must be checked against the Pardis CCE documentation.
* Single currency; no tax calculation; no e-mail delivery (SMN webhook integration point provided).
* JWTs are signed with HS256 and a shared secret. For multi-team key rotation, move to RS256 with a KMS-managed key (Phase 2, KMS).
