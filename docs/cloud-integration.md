# Cloud integration (Pardis Cloud Phase 1)

How each managed service from the Phase 1 document is consumed. The application never
provisions cloud resources itself; it consumes them through **environment variables** and
**adapters**, so a provider change means configuration, not code.

| Adapter / interface | Location | Implementations |
|---|---|---|
| `StorageService` (`upload`, `download`, `delete`, `generate_signed_url`, `public_url`) | `libs/common/ecommerce_common/storage/` | `S3StorageService` (OBS, SeaweedFS/MinIO), `LocalStorageService` (dev only) |
| `CacheService` (fail-soft) | `libs/common/ecommerce_common/cache.py` | `RedisCache` (DCS), `MemoryCache` (dev only) |
| `NotificationService` | `libs/common/ecommerce_common/notifications.py` | `log`, `webhook` (SMN HTTP publisher / relay) |
| `PaymentGateway` | `order-service/order_service/services/payment_gateway.py` | `SimulatedPaymentGateway` (replace with a real PSP) |
| `Database` (SQLAlchemy engine + pool) | `libs/common/ecommerce_common/db.py` | MySQL 8 / RDS (production), SQLite (tests) |

---

## 1. RDS for MySQL (document §6.1)

| Variable | Example | Notes |
|---|---|---|
| `DATABASE_HOST` | `10.0.3.15` | private address in the Database subnet |
| `DATABASE_PORT` | `3306` | RDS-SG must allow it from CCE-SG only |
| `DATABASE_NAME` / `DATABASE_USER` | `ecommerce` / `ecommerce_app` | |
| `DATABASE_PASSWORD` | *Secret* | |
| `DATABASE_URL` | – | optional full SQLAlchemy URL (overrides the parts) |
| `DATABASE_POOL_SIZE` / `DATABASE_MAX_OVERFLOW` | `10` / `10` | per pod. Budget: `(pool+overflow) × replicas × 2 services` must stay below RDS `max_connections` |
| `DATABASE_POOL_RECYCLE` | `1800` | keep it lower than RDS `wait_timeout` |
| `DATABASE_SSL_CA` | `/etc/rds/ca.pem` | mount the RDS CA bundle to enforce TLS in transit |

* **Connection pooling** is on, with `pool_pre_ping`, so connections dropped by an RDS failover or restart are replaced transparently.
* **Isolation** is `READ COMMITTED`. Stock changes use `SELECT … FOR UPDATE` in primary-key order: no oversell, no deadlocks. This is tested with 10 concurrent checkouts on MySQL.
* **Schema** changes go through Alembic (`database/migrations`) and run as the `db-migrate` Job. A MySQL advisory lock serialises concurrent runs.
* **Backups** use RDS automated daily backups with 7–35 days of retention and point-in-time recovery. Take an on-demand backup before migrations in production. The application contains no backup logic (document §10.2).
* **Slow query log:** enable it in the RDS parameter group and ship it to LTS (document §9.2).
* **Phase 2:** read replicas and a primary/standby setup need no code change, only endpoint configuration.

## 2. DCS for Redis (document §6.2)

| Variable | Example | Notes |
|---|---|---|
| `REDIS_HOST` / `REDIS_PORT` | `10.0.3.20` / `6379` | DCS-SG allows 6379 from CCE-SG only |
| `REDIS_PASSWORD` | *Secret* | |
| `REDIS_DB` / `REDIS_SSL` | `0` / `false` | |
| `REDIS_REQUIRED` | `true` | readiness fails when DCS is unreachable (otherwise it only degrades) |
| `CATALOG_CACHE_TTL` / `CART_CACHE_TTL` | `120` / `300` | seconds |

Usage (all keys are prefixed with `ecom:` and have TTLs):

| Key | Purpose | Invalidation |
|---|---|---|
| `ecom:catalog:version` | catalog cache generation | bumped on every product, category or stock change |
| `ecom:catalog:{ver}:products:{hash}` / `…:product:{id}` / `…:categories` | product listing/detail cache | version bump + TTL |
| `ecom:cart:{user}` | rendered cart | deleted on every cart write and on order placement |
| `ecom:revoked:{jti}` | logout deny-list (session management) | TTL = remaining token lifetime |
| `ecom:ratelimit:{scope}:{ip}:{minute}` | per-IP rate limiting | 61 s TTL |
| `ecom:active_users` (sorted set) | active users in the last 15 min (metric) | trimmed on write |

**Failure behaviour:** every Redis call has a 1 s timeout. On error, reads become cache misses,
writes are skipped, rate limiting fails open, and one warning is logged per 10 s. So a DCS outage
slows the service down but does not take it down (tested in `tests/integration/test_failure_handling.py`).
Enable DCS persistence and set `maxmemory-policy allkeys-lru` (document §6.2).

## 3. OBS object storage (document §7.1)

| Variable | Example | Notes |
|---|---|---|
| `STORAGE_BACKEND` | `s3` | `local` is refused outside development |
| `OBS_ENDPOINT` | `https://obs.<region>.<domain>` | endpoint the pods use (via NAT or a VPC endpoint) |
| `OBS_PUBLIC_ENDPOINT` | *(optional)* | endpoint used when signing browser URLs, if it differs |
| `OBS_BUCKET_IMAGES` | `ecommerce-images` | product images + avatars |
| `OBS_BUCKET_INVOICES` | `ecommerce-invoices` | invoices (private, SSE requested on upload) |
| `OBS_BUCKET` | – | fallback single bucket (brief) when the per-purpose buckets are not set |
| `OBS_ACCESS_KEY` / `OBS_SECRET_KEY` | *Secret* | IAM credentials limited to these buckets |
| `OBS_REGION` | `<region>` | |
| `OBS_ADDRESSING_STYLE` | `virtual` | `path` for emulators |
| `OBS_IMAGES_PUBLIC_BASE_URL` | *(optional)* | public-read bucket or CDN URL for images (otherwise pre-signed URLs) |
| `OBS_SIGNED_URL_TTL` | `3600` | seconds; invoice links use 600 s |

**Object layout** (document §7.1 bucket organization):

```
ecommerce-images/images/products/{product_id}/{uuid}.webp
ecommerce-images/images/avatars/{user_id}/{uuid}.webp
ecommerce-invoices/invoices/{yyyy}/{mm}/INV-{yyyymm}-{order_id}.pdf
```

* Uploaded images are decoded, validated, downscaled (1600 px for products, 512 px for avatars) and re-encoded as WebP before upload. This matches the document's "image optimization" guidance.
* Buckets stay **private**. Browsers get time-limited **pre-signed URLs**; the document allows "public read for product images (or use signed URLs)".
* **Versioning** should be on for `ecommerce-invoices`, with lifecycle rules such as moving invoices older than 90 days to a colder storage class and expiring non-current versions after 30 days. The application never overwrites or deletes invoices.
* `/backups/database/` (in the document) is **not** written by the application, because RDS uses native backups.
* **Failure behaviour:** a failed upload returns `503 STORAGE_UNAVAILABLE`. A payment that succeeds while OBS is down stays committed; its invoice is generated later by the Order Service reaper or on the next invoice request.
* **Why S3:** OBS exposes an S3-compatible API with SigV4, which boto3 speaks natively. Local development uses SeaweedFS with the same adapter, so the OBS code path is exercised in compose and kind.

## 4. ELB, Ingress and forwarded headers (document §5.2, §4.1)

```
Internet → DNS → Anti-DDoS → CFW → WAF → ELB (80/443, TLS) → Ingress → frontend:80 | backend:8000
```

* Routing: `/api/*` goes to the Backend API; everything else goes to the SPA. `/metrics`, `/internal/*` and the Order Service are not routed. The frontend uses `API_BASE_URL=""`, meaning the same origin.
* **TLS** terminates at the ELB (or WAF). With HTTPS, `COOKIE_SECURE=true` and HSTS is emitted when `X-Forwarded-Proto: https` comes from a trusted proxy.
* **Forwarded headers:** `ProxyHeadersMiddleware` honours `X-Forwarded-For`, `-Proto` and `-Host` only if the direct peer is in `TRUSTED_PROXIES`. The client IP is the right-most untrusted hop, so a client can't spoof it by sending the header itself. With ingress-nginx, set `use-forwarded-headers: "true"` in the controller ConfigMap so the ELB's `X-Forwarded-For` is kept.
* **Health checks:**
  * ELB → `/health` (the frontend's nginx, which is cheap)
  * external API probes → `/api/health`
  * Kubernetes → `/health` (liveness) and `/ready` (readiness)
* **Session persistence** is not required: the services are stateless and JWTs are validated on every pod.
* **Keep-alive:** Uvicorn keep-alive is 75 s, longer than a typical 60 s ELB idle timeout, which avoids sporadic 502s.
* **CCE specifics:** the production overlay switches to `ingressClassName: cce` with ELB annotations, and changes the backend and frontend Services to `NodePort` (ELB → CCE nodes, per document §5.2). Verify the annotation names against the Pardis CCE documentation.

## 5. Observability: Cloud Eye, LTS, CTS, SMN (document §9–§10)

### Logs → LTS

All containers log **one JSON object per line** to stdout/stderr:

```json
{"timestamp":"2026-09-30T08:52:44.918+00:00","level":"INFO","service":"backend-api","logger":"ecommerce.access",
 "message":"http_request","request_id":"a3f6…","http_method":"POST","path":"/api/orders","route":"/api/orders",
 "status_code":201,"duration_ms":48.2,"client_ip":"203.0.113.7","user_agent":"Mozilla/5.0 …","scheme":"https"}
```

* A `request_id` is taken from `X-Request-ID` or generated, returned in the response header, and propagated to the Order Service, so one ID correlates the full request path.
* `user_id` is added wherever it is known.
* Keys containing `password`, `token`, `secret`, `authorization`, `cookie`, `card_number`, `cvv` or `access_key` are masked (`***`). Only the last 4 card digits are stored.
* Business and audit events (`"event": "<NAME>"`, logger `ecommerce.events`): `USER_REGISTERED`, `USER_LOGIN`, `USER_LOGIN_FAILED`, `USER_LOGOUT`, `PRODUCT_CREATED`, `PRODUCT_UPDATED`, `PRODUCT_DELETED`, `CATEGORY_*`, `INVENTORY_UPDATED`, `ORDER_CREATED`, `PAYMENT_CREATED`, `PAYMENT_SUCCESS`, `PAYMENT_FAILED`, `ORDER_STATUS_CHANGED`, `INVOICE_GENERATED`, `INVOICE_FAILED`, `USER_STATUS_CHANGED`.
* LTS setup: collect container stdout for namespace `ecommerce`, then create saved searches and alarms, for example `event:PAYMENT_FAILED` or `level:ERROR`.

### Metrics → Cloud Eye

`GET /metrics` on each pod (Prometheus format; not exposed through the Ingress). The pods carry
`prometheus.io/scrape` annotations for CCE cloud-native monitoring, which forwards the metrics to Cloud Eye / AOM.

| Metric | Type | Use |
|---|---|---|
| `http_requests_total{service,method,route,status}` | counter | request count, error rate |
| `http_request_duration_seconds{service,method,route}` | histogram | latency (alarm: p95 > 2 s) |
| `http_errors_total{service,route}` | counter | 5xx count (alarm: error rate > 5%) |
| `http_requests_in_flight{service}` | gauge | saturation |
| `active_users` | gauge | distinct users in the last 15 min |
| `orders_created_total`, `order_status_changes_total{to_status}` | counter | business KPIs |
| `payments_total{result}`, `payment_failures_total{reason}` | counter | payment health |
| `users_registered_total`, `user_logins_total{result}` | counter | auth activity / brute force |
| `invoices_generated_total{result}` | counter | OBS / invoice pipeline |
| `dependency_up{dependency}` | gauge | last readiness result for database/redis |

Recommended Cloud Eye alarms (document §9.1): pod CPU > 80% for 5 min, memory > 85% for 5 min,
error rate > 5%, latency > 2 s, `dependency_up == 0`, a spike in `payment_failures_total`, and RDS/DCS/ELB native metrics. Route alarms to SMN.

### CTS

CTS records cloud-control-plane operations (CCE, RDS, OBS, IAM, CFW changes). The application
complements it with application-level audit events in LTS (who changed an order status, stock, a product or a user).

### SMN

* The `alerts` / `operations` / `security` topics are fed by Cloud Eye, CFW, WAF and HSS alarms (platform configuration).
* The `business` topic is fed by the application: set `NOTIFICATION_BACKEND=webhook`, `SMN_WEBHOOK_URL=<HTTP endpoint that publishes to the SMN topic>` and `SMN_WEBHOOK_TOKEN` (Secret). The app publishes order confirmations and status changes asynchronously, so delivery never blocks checkout.

## 6. IAM, HSS, CFW, WAF, Anti-DDoS, VPC Flow Logs, NAT, Bastion

These are platform controls with no application code, but the app is built to fit them:

* **IAM:** give the application a dedicated OBS identity (bucket-scoped policy) and give humans separate accounts. The pods need no cloud API credentials beyond OBS.
* **WAF:** the API accepts JSON and multipart only, uses conventional REST paths, and returns 4xx for malformed input, which makes the OWASP rules effective. Start in learning mode. The largest legitimate body is a 5 MB image upload (the ingress allows 6 MB).
* **CFW:** inbound traffic is only 80/443 to the ELB. Outbound from CCE needs OBS (if not via a VPC endpoint), the SWR registry, the SMN endpoint and OS/package mirrors.
* **Anti-DDoS / CC protection:** complemented by the app's per-IP rate limits (Redis).
* **Security groups:** ELB-SG → CCE-SG → RDS-SG (3306) and DCS-SG (6379), as in document §11.2 step 2.
* **HSS:** the bastion runs the HSS agent. Container images are minimal, non-root, and should be scanned in SWR.
* **VPC Flow Logs → LTS:** enable them for all subnets; the app needs nothing.
* **EVS / CBS:** only node and bastion disks, backed up by CBS (document §10.2). The app does not use PVCs.

## 7. Replacing Plan B with Mall-Swarm (or vice versa)

Both applications sit behind the same Ingress host and consume the same RDS, DCS and OBS
instances (in separate databases, key prefixes and buckets or prefixes). Switching means
repointing the Ingress backend services and the DNS/ELB listener; none of the infrastructure changes.
This project depends on nothing from Mall-Swarm.
