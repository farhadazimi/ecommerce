"""Prometheus metrics (scraped by CCE monitoring and forwarded to Cloud Eye).

Each pod runs a single Uvicorn worker and scales horizontally with replicas, so the
default (single-process) Prometheus registry is correct.
"""

from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

HTTP_REQUESTS = Counter(
    "http_requests_total", "HTTP requests processed", ["service", "method", "route", "status"]
)
HTTP_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["service", "method", "route"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10),
)
HTTP_ERRORS = Counter("http_errors_total", "HTTP responses with status >= 500", ["service", "route"])
HTTP_IN_FLIGHT = Gauge("http_requests_in_flight", "Requests currently being processed", ["service"])

USERS_REGISTERED = Counter("users_registered_total", "Customer registrations")
LOGINS = Counter("user_logins_total", "Login attempts", ["result"])
ACTIVE_USERS = Gauge("active_users", "Distinct authenticated users seen in the last 15 minutes")
ORDERS_CREATED = Counter("orders_created_total", "Orders placed")
ORDER_STATUS_CHANGES = Counter("order_status_changes_total", "Order status transitions", ["to_status"])
PAYMENTS = Counter("payments_total", "Payment attempts", ["result"])
PAYMENT_FAILURES = Counter("payment_failures_total", "Failed payment attempts", ["reason"])
INVOICES = Counter("invoices_generated_total", "Invoice generation attempts", ["result"])
DEPENDENCY_UP = Gauge("dependency_up", "1 if the dependency passed its last readiness check", ["dependency"])


def render_metrics() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
