# REST API reference

* **Interactive docs (Swagger UI):** `GET /api/docs`, `GET /api/redoc`; machine-readable spec at `GET /api/openapi.json`
  (checked-in copy: [`docs/openapi.json`](openapi.json)). Set `EXPOSE_API_DOCS=false` to hide them in production.
* **Base URL:** same origin as the storefront behind the Ingress (`https://<shop-domain>/api/...`), or
  `http://localhost:8000` with docker compose.
* **Content type:** `application/json`; image uploads use `multipart/form-data` (field `file`).
* **Authentication:** `POST /api/auth/login` or `/register` returns a JWT **and** sets it as an `HttpOnly` cookie
  (`ecom_session`). Browsers rely on the cookie (`credentials: "include"`); other clients send
  `Authorization: Bearer <token>`. Tokens expire after `JWT_EXPIRES_MINUTES` (default 60).
* **Correlation:** send `X-Request-ID` (8–128 chars `[A-Za-z0-9._-]`) or one is generated; it is echoed in the response
  headers and in every error body.
* **Rate limits:** 300 requests/min per client IP overall; 20/min for `/api/auth/register` and `/login` → `429 RATE_LIMITED`.

All examples below were captured from the running Kubernetes deployment (tokens and signed URLs shortened).

## Error envelope

Every non-2xx response:

```json
{
  "error": {
    "code": "INSUFFICIENT_INVENTORY",
    "message": "Only 3 unit(s) of '4K Webcam' are available",
    "request_id": "6f1c9a0e2b0d4c1f9d1e3a5b7c9d0e1f",
    "details": [{"product_id": 5, "requested": 4, "available": 3}]
  }
}
```

| HTTP | Typical `code` values |
|---|---|
| 400 | `BAD_REQUEST`, `CART_EMPTY`, `INVALID_IMAGE`, `INVALID_IDEMPOTENCY_KEY`, `CANNOT_DEACTIVATE_SELF` |
| 401 | `UNAUTHENTICATED`, `INVALID_CREDENTIALS`, `INVALID_TOKEN`, `TOKEN_EXPIRED`, `TOKEN_REVOKED`, `ACCOUNT_INACTIVE` |
| 402 | `PAYMENT_DECLINED` (details: `failure_code` = `CARD_DECLINED`, `INSUFFICIENT_FUNDS`, `EXPIRED_CARD`, `INVALID_CARD_NUMBER`) |
| 403 | `FORBIDDEN` (role), `INVALID_SIGNATURE` (file link) |
| 404 | `NOT_FOUND`, `PRODUCT_NOT_FOUND`, `CATEGORY_NOT_FOUND`, `ORDER_NOT_FOUND`, `PAYMENT_NOT_FOUND`, `CART_ITEM_NOT_FOUND` |
| 409 | `EMAIL_TAKEN`, `SKU_EXISTS`, `CATEGORY_EXISTS`, `CATEGORY_NOT_EMPTY`, `INSUFFICIENT_INVENTORY`, `STOCK_BELOW_RESERVED`, `ORDER_NOT_PAYABLE`, `ORDER_NOT_CANCELLABLE`, `PAYMENT_CLOSED`, `INVALID_STATUS_TRANSITION`, `INVOICE_NOT_AVAILABLE` |
| 413 / 415 | `PAYLOAD_TOO_LARGE`, `UNSUPPORTED_MEDIA_TYPE` |
| 422 | `VALIDATION_ERROR` (details: `[{field, message}]`, submitted values are never echoed), `QUANTITY_LIMIT` |
| 429 | `RATE_LIMITED` (+ `Retry-After`) |
| 500 | `INTERNAL_ERROR` (no stack trace; look up `request_id` in LTS) |
| 503 | `DATABASE_UNAVAILABLE`, `ORDER_SERVICE_UNAVAILABLE`, `STORAGE_UNAVAILABLE`, `SERVICE_UNAVAILABLE` (+ `Retry-After`) |

---

## Health

| Method | Path | Description |
|---|---|---|
| GET | `/health` | liveness – process up (every service; frontend nginx too) |
| GET | `/ready` | readiness – DB (+ Redis if `REDIS_REQUIRED`) reachable; `503` otherwise or while draining |
| GET | `/api/health` | liveness through the Ingress path (ELB / Cloud Eye HTTP probes) |
| GET | `/metrics` | Prometheus metrics (pod network only; not routed by the Ingress) |

```http
GET /ready → 200
{"status":"ready","service":"backend-api","checks":{"database":{"status":"ok","critical":true,"latency_ms":1.2},
 "redis":{"status":"ok","critical":true,"latency_ms":0.6}}}
```

## Authentication

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/api/auth/register` | – | create a CUSTOMER account and log in |
| POST | `/api/auth/login` | – | log in (`ADMIN` or `CUSTOMER`) |
| POST | `/api/auth/logout` | cookie/bearer | revoke the token (Redis deny-list) and clear the cookie |
| GET | `/api/auth/me` | ✔ | current user + profile |

```http
POST /api/auth/register
{"email": "jane@example.com", "password": "Secret123", "full_name": "Jane Doe"}

201 Created   (Set-Cookie: ecom_session=…; HttpOnly; Path=/; SameSite=lax)
{"access_token": "eyJhbGciOiJIUzI1NiIsInR5…", "token_type": "bearer", "expires_at": "2026-09-30T10:07:08.740675Z",
 "user": {"id": 8, "email": "jane@example.com", "full_name": "Jane Doe", "role": "CUSTOMER", "is_active": true,
          "created_at": "2026-09-30T09:07:09Z", "last_login_at": null,
          "profile": {"phone": null, "address_line1": null, "city": null, "postal_code": null, "country": null, "avatar_url": null}}}
```

```http
POST /api/auth/register  {"email": "nope", "password": "short", "full_name": "J"}
422 {"error": {"code": "VALIDATION_ERROR", "message": "Request validation failed", "request_id": "d592…",
     "details": [{"field": "email", "message": "value is not a valid email address: An email address must have an @-sign."},
                 {"field": "password", "message": "String should have at least 8 characters"},
                 {"field": "full_name", "message": "String should have at least 2 characters"}]}}

POST /api/auth/login  {"email": "jane@example.com", "password": "wrong"}
401 {"error": {"code": "INVALID_CREDENTIALS", "message": "Invalid e-mail or password", "request_id": "3c1c…"}}

GET /api/auth/me   (no token)
401 {"error": {"code": "UNAUTHENTICATED", "message": "Authentication required", "request_id": "7dfb…"}}
```

## Profile

| Method | Path | Description |
|---|---|---|
| GET | `/api/profile` | my profile |
| PUT | `/api/profile` | partial update: `full_name, phone, address_line1, address_line2, city, postal_code, country` |
| PUT | `/api/profile/password` | `{current_password, new_password}` |
| POST | `/api/profile/avatar` | multipart `file` → stored at `images/avatars/{user}/…webp` in OBS |

## Products

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/products` | – | list/search. Query: `q`, `category` (slug or id), `min_price`, `max_price`, `in_stock`, `sort` (`newest`\|`price_asc`\|`price_desc`\|`name`), `page`, `page_size` (≤100), `include_inactive` (admin) |
| GET | `/api/products/{id}` | – | details (admins also see inactive products) |
| POST | `/api/products` | ADMIN | create (with initial stock) |
| PUT | `/api/products/{id}` | ADMIN | partial update (incl. `stock_quantity`, `low_stock_threshold`) |
| DELETE | `/api/products/{id}` | ADMIN | soft delete (kept for order history, removed from carts) → 204 |
| POST | `/api/products/{id}/images` | ADMIN | multipart `file` (+ `is_primary`) → OBS `images/products/{id}/…webp` |
| DELETE | `/api/products/{id}/images/{imageId}` | ADMIN | remove image → 204 |

```http
GET /api/products?q=phone&page_size=1
200
{"items": [{"id": 5, "sku": "EL-1005", "name": "4K Webcam", "slug": "4k-webcam",
            "description": "Ultra HD webcam with dual microphones. Limited stock!", "price": 74.99, "currency": "USD",
            "is_active": true, "category": {"id": 1, "name": "Electronics", "slug": "electronics"},
            "stock": {"available": 3, "in_stock": true, "low_stock": true},
            "image_url": "https://<obs-endpoint>/ecommerce-images/images/products/5/seed-el-1005.webp?X-Amz-Algorithm=AWS4-HMAC-SHA256&…",
            "images": [{"id": 5, "url": "https://<obs-endpoint>/ecommerce-images/…", "is_primary": true}],
            "created_at": "2026-09-30T09:01:53Z", "updated_at": "2026-09-30T09:01:53Z"}],
 "total": 2, "page": 1, "page_size": 1, "pages": 2}
```

```http
POST /api/products            (ADMIN)
{"sku": "EL-2001", "name": "Bluetooth Speaker", "description": "Portable, waterproof", "price": "59.90",
 "category_id": 1, "is_active": true, "stock_quantity": 40, "low_stock_threshold": 5}
201 → ProductOut          409 SKU_EXISTS          422 CATEGORY_NOT_FOUND / VALIDATION_ERROR

GET /api/products/99999
404 {"error": {"code": "PRODUCT_NOT_FOUND", "message": "Product not found", "request_id": "386a…"}}
```

## Categories

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/api/categories` | – | all categories with `product_count` |
| GET | `/api/categories/{id}` | – | one category |
| POST | `/api/categories` | ADMIN | `{name, description?, slug?}` → 201 (409 `CATEGORY_EXISTS`) |
| PUT | `/api/categories/{id}` | ADMIN | partial update |
| DELETE | `/api/categories/{id}` | ADMIN | 204; 409 `CATEGORY_NOT_EMPTY` if products still reference it |

## Cart (authenticated)

| Method | Path | Description |
|---|---|---|
| GET | `/api/cart` | my cart with line totals, subtotal, shipping (free ≥ 100.00), total, stock `warnings` |
| POST | `/api/cart/items` | `{product_id, quantity=1}` – adds (or increments) → 201 |
| PUT | `/api/cart/items/{itemId}` | `{quantity}` (1–99) |
| DELETE | `/api/cart/items/{itemId}` | remove line |
| DELETE | `/api/cart` | empty the cart |

```http
POST /api/cart/items  {"product_id": 5, "quantity": 1}
201
{"id": 6, "items": [{"id": 6, "product": {"id": 5, "name": "4K Webcam", "slug": "4k-webcam", "sku": "EL-1005", "price": 74.99,
                     "image_url": "https://…", "is_active": true},
                     "quantity": 1, "unit_price": 74.99, "line_total": 74.99, "available": 3, "in_stock": true}],
 "item_count": 1, "subtotal": 74.99, "shipping_fee": 5.0, "total": 79.99, "currency": "USD", "warnings": []}

POST /api/cart/items  {"product_id": 5, "quantity": 4}
409 {"error": {"code": "INSUFFICIENT_INVENTORY", "message": "Only 3 unit(s) of '4K Webcam' are available",
     "details": [{"product_id": 5, "requested": 4, "available": 3}], "request_id": "…"}}
```

## Orders (authenticated; served by the Order Service through the Backend API)

| Method | Path | Description |
|---|---|---|
| POST | `/api/orders` | place an order from the cart (header `Idempotency-Key` recommended). Reserves stock; status `PENDING_PAYMENT` |
| GET | `/api/orders` | my order history. Query `status`, `page`, `page_size` |
| GET | `/api/orders/{id}` | order details incl. items, shipping, payments, status history |
| POST | `/api/orders/{id}/cancel` | `{reason?}` – only while `PENDING_PAYMENT` (reservation released) |
| GET | `/api/orders/{id}/invoice` | `{invoice_number, url, expires_in}` – pre-signed OBS URL to the PDF (valid 600 s) |

```http
POST /api/orders
Idempotency-Key: checkout-2026-09-30-jane-0001
{"shipping_name": "Jane Doe", "shipping_address_line1": "1 Cloud Street", "shipping_city": "Tehran",
 "shipping_postal_code": "10001", "shipping_country": "Iran"}

201
{"id": 6, "order_number": "ORD-20260930-9319D3D8", "status": "PENDING_PAYMENT", "currency": "USD",
 "subtotal": 74.99, "shipping_fee": 5.0, "total": 79.99, "item_count": 1,
 "created_at": "2026-09-30T09:07:10+00:00", "paid_at": null, "cancelled_at": null,
 "customer": {"id": 8, "email": "jane@example.com", "full_name": "Jane Doe"},
 "invoice": {"number": null, "available": false},
 "items": [{"id": 6, "product_id": 5, "sku": "EL-1005", "product_name": "4K Webcam", "unit_price": 74.99, "quantity": 1, "line_total": 74.99}],
 "shipping": {"name": "Jane Doe", "phone": null, "address_line1": "1 Cloud Street", "address_line2": null,
              "city": "Tehran", "postal_code": "10001", "country": "Iran"},
 "notes": null, "payments": [],
 "history": [{"from_status": null, "to_status": "PENDING_PAYMENT", "note": "Order placed", "changed_by": 8,
              "created_at": "2026-09-30T09:07:10+00:00"}]}
```

Repeating the request with the same `Idempotency-Key` returns the same order (200) instead of creating a
duplicate. An empty cart gives `400 CART_EMPTY`, and a stock race gives `409 INSUFFICIENT_INVENTORY` with the affected items.

## Payment (simulator)

| Method | Path | Description |
|---|---|---|
| POST | `/api/payment/create` | `{order_id}` → a `PENDING` payment for the order total (returns the existing open one if present) |
| POST | `/api/payment/confirm` | `{payment_id, card_number, card_holder, expiry_month, expiry_year, cvv}` → charge. Idempotent |

Test cards: `4242 4242 4242 4242` / `5555 5555 5555 4444` succeed; `4000 0000 0000 0002` → `CARD_DECLINED`;
`4000 0000 0000 9995` → `INSUFFICIENT_FUNDS`; `4000 0000 0000 0069` or a past expiry → `EXPIRED_CARD`; invalid Luhn → `INVALID_CARD_NUMBER`.
The CVV is never stored; only the brand and last 4 digits are.

```http
POST /api/payment/create  {"order_id": 6}
201 {"id": 11, "payment_ref": "PAY-73D65B23E540C7DF", "order_id": 6, "provider": "SIMULATOR", "amount": 79.99,
     "currency": "USD", "status": "PENDING", "card_brand": null, "card_last4": null, "failure_code": null, …,
     "test_cards": {"success": "4242 4242 4242 4242", "declined": "4000 0000 0000 0002", "insufficient_funds": "4000 0000 0000 9995"}}

POST /api/payment/confirm {"payment_id": 11, "card_number": "4000 0000 0000 0002", "card_holder": "Jane Doe",
                           "expiry_month": 12, "expiry_year": 2030, "cvv": "123"}
402 {"error": {"code": "PAYMENT_DECLINED", "message": "The card was declined", "request_id": "5802…",
     "details": {"payment_id": 11, "order_id": 6, "failure_code": "CARD_DECLINED", "order_status": "PENDING_PAYMENT",
                 "retry_allowed": true}}}
```

A declined attempt is closed (`409 PAYMENT_CLOSED` if you confirm it again). The order stays `PENDING_PAYMENT`
with its stock reserved, and the client starts a new attempt with `/api/payment/create`:

```http
POST /api/payment/confirm {"payment_id": 12, "card_number": "4242 4242 4242 4242", …}
200
{"payment": {"id": 12, "payment_ref": "PAY-DED974D649C7ECA9", "status": "SUCCEEDED", "card_brand": "VISA", "card_last4": "4242",
             "amount": 79.99, "confirmed_at": "2026-09-30T09:07:10.057474+00:00", …},
 "order":   {"id": 6, "order_number": "ORD-20260930-9319D3D8", "status": "PAID", "total": 79.99,
             "paid_at": "2026-09-30T09:07:10+00:00", "invoice": {"number": "INV-202609-000006", "available": true}, …}}

GET /api/orders/6/invoice
200 {"order_id": 6, "invoice_number": "INV-202609-000006",
     "url": "https://<obs-endpoint>/ecommerce-invoices/invoices/2026/09/INV-202609-000006.pdf?response-content-disposition=attachment…",
     "expires_in": 600}
```

## Admin (role `ADMIN`; others get `403 FORBIDDEN`)

| Method | Path | Description |
|---|---|---|
| GET | `/api/admin/statistics` | dashboard counters, revenue, orders by status, low/out-of-stock, active users, 5 recent orders |
| GET | `/api/admin/users` | list users. Query `q`, `role`, `page`, `page_size` |
| PUT | `/api/admin/users/{id}/status` | `{is_active}` activate/deactivate (not yourself) |
| GET | `/api/admin/orders` | all orders. Query `status`, `q` (order number / e-mail), `page`, `page_size` |
| GET | `/api/admin/orders/{id}` | any order's details |
| PUT | `/api/admin/orders/{id}/status` | `{status: PROCESSING\|SHIPPED\|DELIVERED\|CANCELLED, note?}` |
| GET | `/api/admin/inventory` | stock levels (`quantity`, `reserved`, `available`). Query `q`, `low_stock`, `page` |
| PUT | `/api/admin/inventory/{productId}` | `{quantity}` (absolute) or `{adjust}` (relative), `{low_stock_threshold}` |

Allowed status transitions: `PENDING_PAYMENT→CANCELLED`, `PAID→PROCESSING|CANCELLED`, `PROCESSING→SHIPPED|CANCELLED`,
`SHIPPED→DELIVERED`. Cancelling a paid order puts the stock back and marks the payment `REFUNDED`.

```http
PUT /api/admin/orders/6/status  {"status": "PROCESSING", "note": "Packed"}   → 200 (order)
PUT /api/admin/orders/6/status  {"status": "DELIVERED"}
409 {"error": {"code": "INVALID_STATUS_TRANSITION", "message": "Cannot change order status from PROCESSING to DELIVERED",
     "details": {"from": "PROCESSING", "to": "DELIVERED", "allowed": ["CANCELLED", "SHIPPED"]}, "request_id": "c5cd…"}}

GET /api/admin/statistics
200 {"products": 17, "active_products": 17, "categories": 5, "users": 8, "customers": 7, "orders": 6, "orders_today": 6,
     "orders_by_status": {"PENDING_PAYMENT": 0, "PAID": 0, "PROCESSING": 1, "SHIPPED": 5, "DELIVERED": 0, "CANCELLED": 0},
     "revenue": 1557.91, "currency": "USD", "low_stock_products": 1, "out_of_stock_products": 1, "active_users": 8,
     "recent_orders": [{"id": 6, "order_number": "ORD-20260930-9319D3D8", "status": "PROCESSING", "total": 79.99,
                        "customer": "jane@example.com", "created_at": "2026-09-30T09:07:10+00:00"}, …]}

GET /api/admin/statistics   (as CUSTOMER)
403 {"error": {"code": "FORBIDDEN", "message": "Administrator role required", "request_id": "a2ff…"}}
```

## Internal Order Service API (not public)

Reachable only inside the cluster (ClusterIP, with a NetworkPolicy allowing backend pods only). Every call requires
`X-Internal-Token` (shared secret) and carries `X-User-Id`, `X-User-Role` and `X-Request-ID`. Swagger is at `http://order-service:8001/docs`.

`POST /internal/orders` · `GET /internal/orders` · `GET /internal/orders/{id}` · `POST /internal/orders/{id}/cancel` ·
`PUT /internal/orders/{id}/status` · `GET /internal/orders/{id}/invoice` · `POST /internal/payments` ·
`POST /internal/payments/confirm` · `GET /health` · `GET /ready` · `GET /metrics`
