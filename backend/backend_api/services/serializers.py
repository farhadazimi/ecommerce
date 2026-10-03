"""ORM -> API model conversion (includes resolving object keys to URLs)."""

from __future__ import annotations

import logging

from ecommerce_common.models import Inventory, Product, User
from ecommerce_common.storage import StorageService

from ..schemas import CategoryRef, ImageOut, ProductOut, ProfileOut, StockOut, UserOut

logger = logging.getLogger("ecommerce.serializers")


def safe_url(storage: StorageService, key: str | None) -> str | None:
    if not key:
        return None
    try:
        return storage.url_for(key)
    except Exception:  # noqa: BLE001 - a broken URL must not break the page
        logger.warning("could not build object URL", extra={"object_key": key})
        return None


def stock_of(inv: Inventory | None) -> StockOut:
    available = inv.available if inv else 0
    threshold = inv.low_stock_threshold if inv else 0
    return StockOut(available=available, in_stock=available > 0, low_stock=0 < available <= threshold)


def product_out(product: Product, storage: StorageService, currency: str) -> ProductOut:
    images = [ImageOut(id=i.id, url=safe_url(storage, i.object_key) or "", is_primary=i.is_primary) for i in product.images]
    return ProductOut(
        id=product.id,
        sku=product.sku,
        name=product.name,
        slug=product.slug,
        description=product.description,
        price=product.price,
        currency=currency,
        is_active=product.is_active,
        category=CategoryRef(id=product.category.id, name=product.category.name, slug=product.category.slug)
        if product.category
        else None,
        stock=stock_of(product.inventory),
        image_url=images[0].url if images else None,
        images=images,
        created_at=product.created_at,
        updated_at=product.updated_at,
    )


def user_out(user: User, storage: StorageService | None = None, with_profile: bool = True) -> UserOut:
    profile = None
    if with_profile and user.profile is not None:
        p = user.profile
        profile = ProfileOut(
            phone=p.phone,
            address_line1=p.address_line1,
            address_line2=p.address_line2,
            city=p.city,
            postal_code=p.postal_code,
            country=p.country,
            avatar_url=safe_url(storage, p.avatar_key) if storage else None,
        )
    return UserOut(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role_name,
        is_active=user.is_active,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
        profile=profile,
    )
