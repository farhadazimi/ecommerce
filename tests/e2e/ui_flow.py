"""Browser end-to-end test of the storefront + admin UI (Playwright, headless Chromium).

Run against any deployment (uses the seeded admin account):

    docker run --rm --network host -v "$PWD/tests/e2e:/e2e" -e BASE_URL=http://localhost:8080 \
        -e API_URL=http://localhost:8000 mcr.microsoft.com/playwright/python:v1.52.0-noble \
        sh -c "pip install -q pytest-playwright==0.7.0 httpx && python /e2e/ui_flow.py"

Screenshots of every step are written to $SCREENSHOT_DIR (default /e2e/screenshots).
"""

from __future__ import annotations

import os
import re
import sys
import uuid
from pathlib import Path

import httpx
from playwright.sync_api import expect, sync_playwright

BASE = os.environ.get("BASE_URL", "http://localhost:8080").rstrip("/")
API = os.environ.get("API_URL", BASE).rstrip("/")
ADMIN_EMAIL = os.environ.get("E2E_ADMIN_EMAIL", "admin@example.com")
ADMIN_PASSWORD = os.environ.get("E2E_ADMIN_PASSWORD", "Admin123!")
SHOTS = Path(os.environ.get("SCREENSHOT_DIR", "/e2e/screenshots"))
SHOTS.mkdir(parents=True, exist_ok=True)


def step(page, name: str) -> None:
    page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=True)
    print(f"  ✓ {name}")


def main() -> int:
    product = next(p for p in httpx.get(f"{API}/api/products?in_stock=true&sort=price_desc", timeout=10).json()["items"]
                   if p["stock"]["available"] >= 3)
    email = f"ui-{uuid.uuid4().hex[:8]}@example.com"
    console_errors: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)

        # 1. open website
        page.goto(BASE)
        expect(page.locator(".product-card, .card").first).to_be_visible(timeout=15000)
        step(page, "01-home")

        # 2-3. register (auto login)
        page.goto(f"{BASE}/register")
        page.get_by_label("Full name").fill("UI Customer")
        page.get_by_label("E-mail").fill(email)
        passwords = page.locator("input[type=password]")
        passwords.nth(0).fill("UiPass12345")
        passwords.nth(1).fill("UiPass12345")
        page.get_by_role("button", name="Register").click()
        expect(page).not_to_have_url(re.compile(r"/register"), timeout=10000)
        step(page, "02-registered")

        # 4. browse listing + search
        page.goto(f"{BASE}/products")
        expect(page.locator(".product-card, .card").first).to_be_visible()
        step(page, "03-products")

        # 5-6. product details, add to cart with quantity 1
        page.goto(f"{BASE}/products/{product['id']}")
        expect(page.get_by_role("heading", name=product["name"])).to_be_visible()
        page.get_by_role("button", name="Add to cart").click()
        expect(page.locator(".cart-badge")).to_have_text("1", timeout=10000)
        step(page, "04-product-added")

        # 7. change quantity in the cart (+1)
        page.goto(f"{BASE}/cart")
        expect(page.get_by_role("heading", name="Shopping cart")).to_be_visible()
        page.get_by_role("button", name="Increase").first.click()
        expect(page.locator(".cart-badge")).to_have_text("2", timeout=10000)
        step(page, "05-cart")

        # 8-9. checkout -> confirm order
        page.get_by_role("button", name="Proceed to checkout").click()
        expect(page.get_by_role("heading", name="Checkout").first).to_be_visible()
        page.get_by_label("Full name").fill("UI Customer")
        page.get_by_label("Address", exact=True).fill("1 Cloud Street")
        page.get_by_label("City").fill("Tehran")
        page.get_by_label("Postal code").fill("10001")
        page.get_by_label("Country").fill("Iran")
        step(page, "06-checkout")
        page.get_by_role("button", name=re.compile("Confirm order")).click()

        # 10. payment simulation: declined first, then success
        expect(page.get_by_role("heading", name="Payment")).to_be_visible(timeout=10000)
        page.get_by_label("Card number").fill("4000 0000 0000 0002")
        page.get_by_role("button", name=re.compile(r"^Pay ")).click()
        expect(page.get_by_role("alert").filter(has_text="Payment declined")).to_be_visible(timeout=10000)
        step(page, "07-payment-declined")
        page.get_by_label("Card number").fill("4242 4242 4242 4242")
        page.get_by_role("button", name="Retry payment").click()

        # 11-16. success -> confirmation page
        expect(page).to_have_url(re.compile(r"/orders/\d+\?confirmed=1"), timeout=15000)
        expect(page.get_by_text("Your payment was successful")).to_be_visible()
        order_id = int(re.search(r"/orders/(\d+)", page.url).group(1))
        step(page, "08-order-confirmed")

        # 14-15. invoice PDF opens from object storage
        with ctx.expect_event("request", predicate=lambda r: "invoices/" in r.url) as invoice_request:
            page.get_by_role("button", name=re.compile("Download invoice")).click()
        invoice_url = invoice_request.value.url
        pdf = httpx.get(invoice_url, timeout=10)
        assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF"), pdf.status_code
        print(f"  ✓ invoice PDF downloaded from object storage: {invoice_url.split('?')[0]} ({len(pdf.content)} bytes)")

        # 17. order history
        page.goto(f"{BASE}/orders")
        expect(page.get_by_text(re.compile(r"ORD-\d{8}-")).first).to_be_visible()
        step(page, "09-order-history")

        # 18-19. admin sees the order and updates its status
        admin = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
        admin.goto(f"{BASE}/login")
        admin.get_by_label("E-mail").fill(ADMIN_EMAIL)
        admin.get_by_label("Password").fill(ADMIN_PASSWORD)
        admin.get_by_role("button", name="Log in").click()
        expect(admin).to_have_url(re.compile(r"/admin"), timeout=10000)
        step(admin, "10-admin-dashboard")
        admin.goto(f"{BASE}/admin/orders/{order_id}")
        admin.get_by_role("button", name=re.compile(r"Mark Processing", re.I)).click()
        expect(admin.get_by_role("button", name=re.compile(r"Mark Shipped", re.I))).to_be_visible(timeout=10000)
        admin.get_by_role("button", name=re.compile(r"Mark Shipped", re.I)).click()
        expect(admin.get_by_role("button", name=re.compile(r"Mark Delivered", re.I))).to_be_visible(timeout=10000)
        step(admin, "11-admin-order-shipped")
        for section in ("products", "inventory", "categories", "users"):
            admin.goto(f"{BASE}/admin/{section}")
            expect(admin.locator("table, form").first).to_be_visible(timeout=10000)
            step(admin, f"12-admin-{section}")

        # customer sees the new status
        page.goto(f"{BASE}/orders/{order_id}")
        expect(page.get_by_text(re.compile("Shipped", re.I)).first).to_be_visible()
        step(page, "13-customer-sees-shipped")
        browser.close()

    # 401 = anonymous session probe (GET /api/auth/me) and 402 = the deliberately declined card
    real_errors = [e for e in console_errors if not re.search(r"status of 40[12]", e)]
    if real_errors:
        print("Browser console errors:", *real_errors, sep="\n  ")
        return 1
    print("UI flow passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
