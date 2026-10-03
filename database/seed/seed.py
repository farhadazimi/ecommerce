"""Idempotent demo data for local development / staging demos.

    python database/seed/seed.py

* creates an admin and a customer account, categories, products, inventory
* generates simple product images and uploads them through StorageService (OBS/local)
* safe to run repeatedly: existing rows (matched by e-mail / SKU / slug) are left untouched
* refuses to run in production unless SEED_ALLOW_PRODUCTION=true and explicit passwords are given
"""

from __future__ import annotations

import io
import logging
import os
import re
import sys
from decimal import Decimal

from sqlalchemy import select

from ecommerce_common.config import get_settings
from ecommerce_common.db import Database
from ecommerce_common.log import configure_logging
from ecommerce_common.models import Category, Inventory, Product, ProductImage, Role, RoleName, User, UserProfile
from ecommerce_common.security import hash_password
from ecommerce_common.storage import build_storage, product_image_key

logger = logging.getLogger("ecommerce.seed")

CATEGORIES = [
    ("Electronics", "electronics", "Phones, audio, computers and accessories"),
    ("Books", "books", "Printed books across all genres"),
    ("Clothing", "clothing", "Apparel for every season"),
    ("Home & Kitchen", "home-kitchen", "Everything for your home"),
    ("Sports & Outdoors", "sports-outdoors", "Gear for an active lifestyle"),
]

# sku, name, category slug, price, stock, description, colour
PRODUCTS = [
    ("EL-1001", "Wireless Noise-Cancelling Headphones", "electronics", "199.99", 25, "Over-ear Bluetooth headphones with 30h battery life and active noise cancelling.", (37, 99, 235)),
    ("EL-1002", "Smartphone X12 128GB", "electronics", "649.00", 12, "6.1\" OLED display, dual camera, 128 GB storage.", (17, 24, 39)),
    ("EL-1003", "USB-C Fast Charger 65W", "electronics", "39.90", 80, "GaN charger for laptops, tablets and phones.", (75, 85, 99)),
    ("EL-1004", "Mechanical Keyboard", "electronics", "89.50", 30, "Tenkeyless mechanical keyboard with hot-swappable switches.", (124, 58, 237)),
    ("EL-1005", "4K Webcam", "electronics", "74.99", 3, "Ultra HD webcam with dual microphones. Limited stock!", (8, 145, 178)),
    ("BK-2001", "Cloud Architecture Patterns", "books", "44.00", 40, "Designing scalable, resilient applications for the cloud.", (180, 83, 9)),
    ("BK-2002", "Kubernetes in Practice", "books", "52.00", 35, "Hands-on guide to running containers in production.", (5, 150, 105)),
    ("BK-2003", "The Pragmatic Engineer", "books", "29.99", 60, "Timeless advice for software professionals.", (190, 18, 60)),
    ("CL-3001", "Classic Cotton T-Shirt", "clothing", "19.99", 150, "100% organic cotton, regular fit.", (220, 38, 38)),
    ("CL-3002", "Waterproof Rain Jacket", "clothing", "119.00", 18, "Breathable, seam-sealed shell for all weather.", (22, 163, 74)),
    ("CL-3003", "Running Sneakers", "clothing", "84.90", 0, "Lightweight running shoes with cushioned sole. Currently out of stock.", (234, 88, 12)),
    ("HK-4001", "Stainless Steel Cookware Set", "home-kitchen", "159.00", 10, "10-piece induction-ready cookware set.", (100, 116, 139)),
    ("HK-4002", "Espresso Machine", "home-kitchen", "329.00", 7, "15-bar pump espresso machine with milk frother.", (120, 53, 15)),
    ("HK-4003", "Ceramic Coffee Mug Set", "home-kitchen", "24.50", 90, "Set of 4 dishwasher-safe ceramic mugs.", (217, 119, 6)),
    ("SP-5001", "Yoga Mat Pro", "sports-outdoors", "49.00", 45, "6 mm non-slip yoga mat with carry strap.", (147, 51, 234)),
    ("SP-5002", "Insulated Water Bottle 1L", "sports-outdoors", "27.00", 120, "Keeps drinks cold for 24h and hot for 12h.", (14, 165, 233)),
    ("SP-5003", "Trail Backpack 30L", "sports-outdoors", "94.00", 22, "Lightweight hiking backpack with rain cover.", (63, 98, 18)),
]


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:200]


def _password(var: str, dev_default: str, production: bool) -> str:
    value = os.environ.get(var)
    if value:
        return value
    if production:
        sys.exit(f"{var} must be set when seeding outside development")
    return dev_default


def make_image(name: str, colour: tuple[int, int, int]) -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (800, 800), colour)
    draw = ImageDraw.Draw(img)
    for i in range(0, 800, 40):  # subtle diagonal pattern
        draw.line([(i, 0), (0, i)], fill=tuple(min(c + 18, 255) for c in colour), width=6)
        draw.line([(800, i), (i, 800)], fill=tuple(max(c - 18, 0) for c in colour), width=6)
    draw.rounded_rectangle([60, 300, 740, 500], radius=30, fill=(255, 255, 255))
    words, lines, line = name.split(), [], ""
    for w in words:
        if len(line) + len(w) > 22:
            lines.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    lines.append(line)
    y = 400 - len(lines) * 22
    for text_line in lines[:3]:
        width = draw.textlength(text_line, font_size=36)
        draw.text(((800 - width) / 2, y), text_line, fill=colour, font_size=36)
        y += 44
    out = io.BytesIO()
    img.save(out, format="WEBP", quality=80)
    return out.getvalue()


def main() -> None:
    settings = get_settings()
    configure_logging("seed", settings.log_level, settings.log_json)
    production = settings.app_env == "production"
    if production and os.environ.get("SEED_ALLOW_PRODUCTION", "").lower() != "true":
        sys.exit("Refusing to seed demo data in production (set SEED_ALLOW_PRODUCTION=true to override)")
    with_images = os.environ.get("SEED_IMAGES", "true").lower() == "true"

    db = Database(settings)
    storage = build_storage(settings) if with_images else None
    session = db.session()
    created = {"users": 0, "categories": 0, "products": 0, "images": 0}
    try:
        roles = {r.name: r for r in session.scalars(select(Role))}
        if not roles:
            sys.exit("Roles missing - run 'alembic upgrade head' first")

        accounts = [
            (os.environ.get("SEED_ADMIN_EMAIL", "admin@example.com"), _password("SEED_ADMIN_PASSWORD", "Admin123!", production), "Store Admin", RoleName.ADMIN),
            (os.environ.get("SEED_CUSTOMER_EMAIL", "customer@example.com"), _password("SEED_CUSTOMER_PASSWORD", "Customer123!", production), "Demo Customer", RoleName.CUSTOMER),
        ]
        for email, password, name, role in accounts:
            if session.scalar(select(User).where(User.email == email)) is None:
                user = User(email=email, password_hash=hash_password(password), full_name=name, role=roles[role])
                user.profile = UserProfile(address_line1="1 Cloud Street", city="Tehran", postal_code="10001", country="Iran")
                session.add(user)
                created["users"] += 1

        cats: dict[str, Category] = {}
        for name, slug, desc in CATEGORIES:
            cat = session.scalar(select(Category).where(Category.slug == slug))
            if cat is None:
                cat = Category(name=name, slug=slug, description=desc)
                session.add(cat)
                created["categories"] += 1
            cats[slug] = cat
        session.flush()

        for sku, name, cat_slug, price, stock, desc, colour in PRODUCTS:
            product = session.scalar(select(Product).where(Product.sku == sku))
            if product is None:
                product = Product(
                    sku=sku, name=name, slug=_slug(name),
                    description=desc, price=Decimal(price), category=cats[cat_slug], is_active=True,
                )
                product.inventory = Inventory(quantity=stock, reserved=0, low_stock_threshold=5)
                session.add(product)
                session.flush()
                created["products"] += 1
            if storage and not product.images:
                key = product_image_key(product.id, f"seed-{sku.lower()}.webp")
                try:
                    storage.upload(key, make_image(name, colour), "image/webp")
                    session.add(ProductImage(product_id=product.id, object_key=key, content_type="image/webp", is_primary=True))
                    created["images"] += 1
                except Exception as exc:  # noqa: BLE001 - images are optional demo data
                    logger.warning("image upload skipped", extra={"sku": sku, "error_type": type(exc).__name__})
        session.commit()
    finally:
        session.close()
        db.dispose()
    logger.info("seed complete", extra=created)
    print(f"Seed complete: {created}")


if __name__ == "__main__":
    main()
