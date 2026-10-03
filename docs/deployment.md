# Deployment guide

| Target | Command | Purpose |
|---|---|---|
| Local, no containers | `make install && make test` | development and unit/integration tests |
| Docker Compose | `make up` | full local stack (MySQL, Redis, S3-compatible OBS stand-in) |
| kind (local Kubernetes) | `make kind-up` | rehearsal of the CCE deployment with Ingress, NetworkPolicy, HPA, Jobs |
| Pardis Cloud CCE | GitLab CI (`deploy:staging` / `deploy:production`) or `scripts/k8s-deploy.sh` | staging and production |

---

## 1. Local development

Prerequisites: Python 3.12, Node 22, Docker 24+ with Compose v2.

```bash
make install                  # .venv + pinned deps + frontend npm ci
make test                     # 100+ pytest tests (SQLite, in-memory cache, local storage)
make lint frontend-test       # ruff + eslint + vitest
```

Run the services against local MySQL/Redis (optional):

```bash
docker compose up -d mysql redis              # MySQL on 127.0.0.1:3307
cp .env.development.example .env.development   # adjust if needed
set -a; . ./.env.development; set +a
(cd database && ../.venv/bin/alembic upgrade head) && .venv/bin/python database/seed/seed.py
.venv/bin/uvicorn order_service.main:create_app --factory --app-dir order-service --port 8001 &
.venv/bin/uvicorn backend_api.main:create_app --factory --app-dir backend --port 8000 --reload &
(cd frontend && npm run dev)                  # http://localhost:5173
```

Without MySQL you can set `DATABASE_URL=sqlite:///./dev.db`. Without Redis, leave `REDIS_HOST`
empty: the in-memory fallback is used. That fallback is refused in staging and production.

## 2. Docker Compose (complete stack, one command)

```bash
cp .env.example .env     # optional: every variable has a local default
make up                  # = docker compose up -d --build
```

| URL | What |
|---|---|
| http://localhost:8080 | storefront + admin UI |
| http://localhost:8000/api/docs | Swagger UI (OpenAPI) |
| http://localhost:8333 | S3 endpoint of the OBS stand-in (pre-signed URLs point here) |

Demo accounts (development seed only): `admin@example.com / Admin123!` and `customer@example.com / Customer123!`.

Start-up order (enforced with `depends_on` conditions): mysql/redis/object-storage healthy →
`storage-init` (creates buckets) → `migrate` (Alembic + seed) → order-service → backend → frontend.

Verification:

```bash
make e2e       # API end-to-end (register → cart → order → declined + successful payment → invoice → admin)
make ui-e2e    # the same journey in headless Chromium (Playwright container)
make down      # stop and delete volumes
```

## 3. Kubernetes manifests

```
infrastructure/kubernetes/
├── base/                         # environment-neutral manifests
│   ├── namespace.yaml            # PSA "restricted"
│   ├── serviceaccount.yaml       # no API token mounted
│   ├── configmap.yaml            # ecommerce-config + frontend-config
│   ├── secrets.yaml              # TEMPLATE ONLY (not applied)
│   ├── frontend-deployment.yaml / frontend-service.yaml
│   ├── backend-deployment.yaml  / backend-service.yaml
│   ├── order-deployment.yaml    / order-service.yaml
│   ├── ingress.yaml              # /api → backend, / → frontend
│   ├── hpa.yaml  pdb.yaml  networkpolicy.yaml
│   ├── migration-job.yaml        # alembic upgrade head
│   └── kustomization.yaml
└── overlays/
    ├── local/        # kind: + MySQL/Redis/SeaweedFS stand-ins, dev secret, seed Job
    ├── staging/      # namespace ecommerce-staging
    └── production/   # CCE: ELB ingress annotations, NodePort services, 3 replicas, SWR images
```

Every Deployment has:
* resource requests and limits
* startup, liveness (`/health`) and readiness (`/ready`) probes
* `preStop` drain and a rolling update with `maxUnavailable: 0`
* spread across zones and nodes
* a non-root, read-only-root-filesystem, `drop: [ALL]`, `seccomp` security context
* HPA (CPU 70% / memory 80%) and a PDB (`minAvailable: 1`)

Validate and render:

```bash
make k8s-validate                 # kubeconform --strict on all overlays
make k8s-render OVERLAY=production
```

### 3.1 Local rehearsal on kind

```bash
make kind-up        # kind + ingress-nginx + metrics-server, builds and loads images, deploys, seeds
E2E_API_URL=http://shop.localtest.me E2E_FRONTEND_URL=http://shop.localtest.me make e2e
make kind-down
```

`*.localtest.me` resolves to the loopback address. The stack is served at http://shop.localtest.me,
and pre-signed object URLs use http://objects.localtest.me.

## 4. Pardis Cloud CCE (staging / production)

### 4.1 One-time platform setup (document §11.2, steps 1–5 and 7–10)

1. **Network:** VPC `10.0.0.0/16` with Public `10.0.1.0/24` (NAT Gateway + EIP, Bastion), CCE `10.0.2.0/24`,
   Database `10.0.3.0/24` and Storage `10.0.4.0/24` subnets.
2. **Security groups:**
   * ELB-SG: 80/443 from `0.0.0.0/0`
   * CCE-SG: from ELB-SG
   * RDS-SG: 3306 from CCE-SG
   * DCS-SG: 6379 from CCE-SG
3. **RDS MySQL 8.0** in the Database subnet, with automated backups (7–35 days). Create the schema and an app user:
   ```sql
   CREATE DATABASE ecommerce CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
   CREATE USER 'ecommerce_app'@'10.0.2.%' IDENTIFIED BY '<strong password>';
   GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, DROP, REFERENCES ON ecommerce.* TO 'ecommerce_app'@'10.0.2.%';
   ```
   (You can use a separate DDL user for the migration Job and a DML-only user for the pods.)
4. **DCS Redis 5.0+** with a password and persistence enabled.
5. **OBS:**
   * buckets `ecommerce-images` and `ecommerce-invoices`, private, with versioning, lifecycle rules and SSE
   * an IAM user or agency whose policy is limited to those buckets
   * optionally, a public-read or CDN setup for images (then set `OBS_IMAGES_PUBLIC_BASE_URL`)
6. **CCE cluster:** at least 3 nodes across AZs, plus the add-ons for metrics-server/monitoring and the ELB ingress controller (or the nginx-ingress add-on).
7. **ELB:** listeners on 80 and 443 (TLS 1.2+ policy, certificate) and health checks on `/health`. Put WAF and CFW in front as described in document §8.
8. **SWR:** an organization for the images, and a long-term login for CI.
9. **Monitoring:** Cloud Eye, LTS (collect container stdout for namespace `ecommerce`), CTS, SMN topics — see [cloud-integration.md](cloud-integration.md).

### 4.2 Configure the overlay

Edit `overlays/production/configmap-patch.yaml` and `ingress-patch.yaml`, replacing every `REPLACE_ME_*` value:
* RDS and DCS private addresses
* OBS endpoint and region
* shop domain
* ELB id
* images bucket host (for the CSP)

Check the ingress annotations against the Pardis CCE documentation for your cluster version:
`kubernetes.io/elb.id`, `kubernetes.io/elb.class`, `kubernetes.io/elb.port` and `ingressClassName: cce`.

### 4.3 Create the Secret (never commit it)

```bash
kubectl create namespace ecommerce
kubectl -n ecommerce create secret generic ecommerce-secrets \
  --from-literal=DATABASE_PASSWORD='…' --from-literal=REDIS_PASSWORD='…' \
  --from-literal=OBS_ACCESS_KEY='…'   --from-literal=OBS_SECRET_KEY='…' \
  --from-literal=JWT_SECRET="$(openssl rand -hex 32)" \
  --from-literal=INTERNAL_SERVICE_TOKEN="$(openssl rand -hex 24)" \
  --from-literal=SMN_WEBHOOK_TOKEN=''
```

The GitLab deploy job does the same from protected, masked CI/CD variables. If any secret is
missing, too short, or still a placeholder, the pods fail fast at start-up with a clear error.

### 4.4 Build, push, deploy

```bash
make push REGISTRY=swr.<region>.<domain>/<org> TAG=1.0.0
cd infrastructure/kubernetes/overlays/production
kustomize edit set image ecommerce-backend=swr.<region>.<domain>/<org>/ecommerce-backend:1.0.0 \
                         ecommerce-order-service=swr.<region>.<domain>/<org>/ecommerce-order-service:1.0.0 \
                         ecommerce-frontend=swr.<region>.<domain>/<org>/ecommerce-frontend:1.0.0
cd - && scripts/k8s-deploy.sh production
```

`scripts/k8s-deploy.sh` applies config, services, ingress and policies, then runs the `db-migrate`
Job and waits for it (Alembic is serialised with a MySQL advisory lock), then rolls out
order-service → backend → frontend and waits for each rollout.

### 4.5 Post-deployment validation (document §11.3)

```bash
kubectl -n ecommerce get pods,svc,ingress,hpa,pdb
curl -fsS https://<domain>/health && curl -fsS https://<domain>/api/health
E2E_API_URL=https://<domain> E2E_FRONTEND_URL=https://<domain> \
  E2E_ADMIN_EMAIL=… E2E_ADMIN_PASSWORD=… make e2e
```

Also verify:
* `/metrics` and `/internal/*` are **not** reachable from the internet
* WAF blocks an SQL-injection probe
* security groups block direct RDS/DCS access from outside the CCE subnet

### 4.6 Rollback

```bash
kubectl -n ecommerce rollout undo deploy/backend deploy/order-service deploy/frontend
```

Migrations are forward-only in production. Make schema changes backward compatible with the
previous release (expand → migrate → contract) so a rollback never needs a downgrade.

## 5. CI/CD (GitLab)

Pipeline stages in `.gitlab-ci.yml`:

| Stage | Jobs |
|---|---|
| **lint** | ruff, eslint and tsc, kubeconform on all overlays |
| **test** | `test:unit` (SQLite, coverage + JUnit); `test:integration-mysql` (MySQL 8 + Redis 7 services, including migrations and the concurrency test); `test:frontend` (vitest) |
| **build** | frontend bundle |
| **docker** | build and push the 3 images to `$REGISTRY` (SWR) with tag `$CI_COMMIT_SHORT_SHA` (default branch and tags only) |
| **deploy** | `deploy:staging` runs automatically on the default branch; `deploy:production` is manual, on tags |

Required variables are listed at the top of `.gitlab-ci.yml`. The registry is configurable
(`REGISTRY`) and defaults to the GitLab registry.

## 6. Troubleshooting

| Symptom | Check |
|---|---|
| Pod `CrashLoopBackOff` right after deploy | `kubectl logs` shows `Insecure or incomplete configuration: …`: fix the Secret/ConfigMap |
| `/ready` returns 503 and `checks.database.status=error` | RDS address, RDS-SG allows CCE-SG on 3306, credentials, CFW rules (document §12.5) |
| `checks.redis.status=error` | DCS address/password, DCS-SG on 6379. With `REDIS_REQUIRED=false` the app keeps serving from RDS |
| `503 STORAGE_UNAVAILABLE` on uploads | OBS endpoint reachable (NAT/VPC endpoint), AK/SK, bucket names. Paid orders still succeed; invoices are generated later by the reaper |
| `503 ORDER_SERVICE_UNAVAILABLE` | `kubectl -n ecommerce get endpoints order-service`, and the NetworkPolicy allows backend → order-service |
| Browser shows CORS errors | `CORS_ORIGINS` must list the exact storefront origin (scheme + host + port) |
| Images or invoices not loading | CSP `CSP_IMG_SRC` must include the OBS origin, and the pre-signed URL host must be reachable by browsers (`OBS_PUBLIC_ENDPOINT`) |
| Client IPs in logs are wrong | `TRUSTED_PROXIES` must include the ingress/ELB source ranges, and the ingress must forward `X-Forwarded-For` |
| Migration Job fails | `kubectl -n ecommerce logs job/db-migrate`, and check the DDL permissions of the DB user |
| HPA shows `<unknown>` | the metrics-server / monitoring add-on is not installed |
