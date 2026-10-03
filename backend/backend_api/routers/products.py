from __future__ import annotations

import hashlib
import json
import logging
import math
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ecommerce_common.cache import CacheService
from ecommerce_common.config import Settings
from ecommerce_common.errors import AppError, ConflictError, NotFoundError
from ecommerce_common.log import log_event
from ecommerce_common.models import Cart, CartItem, Category, Inventory, Product, ProductImage, RoleName, User, utcnow
from ecommerce_common.storage import StorageService, StorageUnavailableError, product_image_key

from ..deps import get_cache, get_db, get_optional_user, get_settings, get_storage, require_admin
from ..schemas import Page, ProductIn, ProductOut, ProductUpdate
from ..services.images import process_image, read_upload
from ..services.serializers import product_out
from ..services.text import like_pattern, slugify

router = APIRouter(prefix="/api/products", tags=["Products"])
logger = logging.getLogger("ecommerce.products")

SortOption = Literal["newest", "price_asc", "price_desc", "name"]


def _load(db: Session, product_id: int, include_inactive: bool = False) -> Product:
    product = db.get(Product, product_id)
    if product is None or product.deleted_at is not None or (not product.is_active and not include_inactive):
        raise NotFoundError("Product not found", code="PRODUCT_NOT_FOUND")
    return product


def _unique_slug(db: Session, base: str, sku: str, exclude_id: int | None = None) -> str:
    slug = slugify(base)
    query = select(Product.id).where(Product.slug == slug)
    if exclude_id:
        query = query.where(Product.id != exclude_id)
    if db.scalar(query) is None:
        return slug
    return f"{slug}-{slugify(sku)}"[:220]


def _check_category(db: Session, category_id: int | None) -> None:
    if category_id is not None and db.get(Category, category_id) is None:
        raise AppError("Category does not exist", code="CATEGORY_NOT_FOUND", status_code=422)


@router.get("", response_model=Page[ProductOut], summary="List / search products")
def list_products(
    q: str | None = Query(default=None, max_length=100, description="Search in name, description and SKU"),
    category: str | None = Query(default=None, max_length=120, description="Category slug or id"),
    min_price: Decimal | None = Query(default=None, ge=0),
    max_price: Decimal | None = Query(default=None, ge=0),
    in_stock: bool = False,
    sort: SortOption = "newest",
    page: int = Query(default=1, ge=1, le=10_000),
    page_size: int = Query(default=12, ge=1, le=100),
    include_inactive: bool = Query(default=False, description="Admin only: include inactive products"),
    db: Session = Depends(get_db),
    cache: CacheService = Depends(get_cache),
    storage: StorageService = Depends(get_storage),
    settings: Settings = Depends(get_settings),
    user: User | None = Depends(get_optional_user),
) -> Page[ProductOut]:
    include_inactive = include_inactive and user is not None and user.role_name == RoleName.ADMIN
    params = {
        "q": q, "category": category, "min": str(min_price), "max": str(max_price),
        "in_stock": in_stock, "sort": sort, "page": page, "size": page_size,
    }
    cache_key = None
    if not include_inactive:
        digest = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()
        cache_key = cache.key("catalog", cache.catalog_version(), "products", digest)
        cached = cache.get_json(cache_key)
        if cached is not None:
            return Page[ProductOut](**cached)

    query = select(Product).where(Product.deleted_at.is_(None))
    if not include_inactive:
        query = query.where(Product.is_active.is_(True))
    if q and q.strip():
        pattern = like_pattern(q.strip())
        query = query.where(
            or_(
                Product.name.like(pattern, escape="\\"),
                Product.description.like(pattern, escape="\\"),
                Product.sku.like(pattern, escape="\\"),
            )
        )
    if category:
        cat_filter = Category.id == int(category) if category.isdigit() else Category.slug == category
        query = query.join(Category, Product.category_id == Category.id).where(cat_filter)
    if min_price is not None:
        query = query.where(Product.price >= min_price)
    if max_price is not None:
        query = query.where(Product.price <= max_price)
    if in_stock:
        query = query.join(Inventory, Inventory.product_id == Product.id).where(
            Inventory.quantity - Inventory.reserved > 0
        )

    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    order = {
        "newest": (Product.created_at.desc(), Product.id.desc()),
        "price_asc": (Product.price.asc(), Product.id),
        "price_desc": (Product.price.desc(), Product.id),
        "name": (Product.name.asc(), Product.id),
    }[sort]
    products = db.scalars(query.order_by(*order).offset((page - 1) * page_size).limit(page_size)).unique().all()
    result = Page[ProductOut](
        items=[product_out(p, storage, settings.currency) for p in products],
        total=total,
        page=page,
        page_size=page_size,
        pages=math.ceil(total / page_size) if total else 0,
    )
    if cache_key:
        cache.set_json(cache_key, result.model_dump(mode="json"), ttl=settings.catalog_cache_ttl)
    return result


@router.get("/{product_id}", response_model=ProductOut, summary="Product details")
def get_product(
    product_id: int,
    db: Session = Depends(get_db),
    cache: CacheService = Depends(get_cache),
    storage: StorageService = Depends(get_storage),
    settings: Settings = Depends(get_settings),
    user: User | None = Depends(get_optional_user),
) -> ProductOut:
    is_admin = user is not None and user.role_name == RoleName.ADMIN
    key = cache.key("catalog", cache.catalog_version(), "product", product_id)
    if not is_admin:
        cached = cache.get_json(key)
        if cached is not None:
            return ProductOut(**cached)
    result = product_out(_load(db, product_id, include_inactive=is_admin), storage, settings.currency)
    if not is_admin:
        cache.set_json(key, result.model_dump(mode="json"), ttl=settings.catalog_cache_ttl)
    return result


@router.post("", response_model=ProductOut, status_code=status.HTTP_201_CREATED, summary="Create product (admin)")
def create_product(
    body: ProductIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    cache: CacheService = Depends(get_cache),
    storage: StorageService = Depends(get_storage),
    settings: Settings = Depends(get_settings),
) -> ProductOut:
    _check_category(db, body.category_id)
    product = Product(
        sku=body.sku.upper(),
        name=body.name,
        slug=_unique_slug(db, body.name, body.sku),
        description=body.description,
        price=body.price,
        category_id=body.category_id,
        is_active=body.is_active,
    )
    product.inventory = Inventory(quantity=body.stock_quantity, reserved=0, low_stock_threshold=body.low_stock_threshold)
    db.add(product)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("A product with this SKU already exists", code="SKU_EXISTS") from exc
    db.refresh(product)
    cache.bump_catalog_version()
    log_event("PRODUCT_CREATED", user_id=admin.id, product_id=product.id, sku=product.sku)
    return product_out(product, storage, settings.currency)


@router.put("/{product_id}", response_model=ProductOut, summary="Update product (admin; partial update)")
def update_product(
    product_id: int,
    body: ProductUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    cache: CacheService = Depends(get_cache),
    storage: StorageService = Depends(get_storage),
    settings: Settings = Depends(get_settings),
) -> ProductOut:
    product = _load(db, product_id, include_inactive=True)
    changes = body.model_dump(exclude_unset=True)
    if "category_id" in changes:
        _check_category(db, changes["category_id"])
    stock = changes.pop("stock_quantity", None)
    threshold = changes.pop("low_stock_threshold", None)
    for field, value in changes.items():
        if value is None and field not in ("description", "category_id"):
            continue
        if field == "sku":
            value = value.upper()
        setattr(product, field, value)
    if "name" in changes and changes["name"]:
        product.slug = _unique_slug(db, product.name, product.sku, exclude_id=product.id)
    if stock is not None or threshold is not None:
        inv = db.scalar(select(Inventory).where(Inventory.product_id == product.id).with_for_update().execution_options(populate_existing=True))
        if inv is None:
            inv = Inventory(product_id=product.id, quantity=0, reserved=0)
            db.add(inv)
        if stock is not None:
            if stock < inv.reserved:
                raise ConflictError(
                    f"Stock cannot be lower than the {inv.reserved} unit(s) reserved by pending orders",
                    code="STOCK_BELOW_RESERVED",
                )
            inv.quantity = stock
        if threshold is not None:
            inv.low_stock_threshold = threshold
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("A product with this SKU already exists", code="SKU_EXISTS") from exc
    db.refresh(product)
    cache.bump_catalog_version()
    log_event("PRODUCT_UPDATED", user_id=admin.id, product_id=product.id, fields=sorted(body.model_fields_set))
    return product_out(product, storage, settings.currency)


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete product (admin, soft delete)")
def delete_product(
    product_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    cache: CacheService = Depends(get_cache),
) -> Response:
    """Soft delete keeps order history intact; the product disappears from the catalog and carts."""
    product = _load(db, product_id, include_inactive=True)
    product.deleted_at = utcnow()
    product.is_active = False
    # free the SKU / slug for re-use
    product.sku = f"{product.sku[:40]}-DEL{product.id}"[:64]
    product.slug = f"{product.slug[:190]}-deleted-{product.id}"
    affected_users = db.scalars(
        select(Cart.user_id).join(CartItem, CartItem.cart_id == Cart.id).where(CartItem.product_id == product_id)
    ).all()
    db.execute(delete(CartItem).where(CartItem.product_id == product_id))
    db.commit()
    for uid in affected_users:
        cache.invalidate_cart(uid)
    cache.bump_catalog_version()
    log_event("PRODUCT_DELETED", user_id=admin.id, product_id=product_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{product_id}/images",
    response_model=ProductOut,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a product image (admin; stored in OBS images/products/)",
)
def upload_image(
    product_id: int,
    file: UploadFile = File(...),
    is_primary: bool = Form(default=False),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    cache: CacheService = Depends(get_cache),
    storage: StorageService = Depends(get_storage),
    settings: Settings = Depends(get_settings),
) -> ProductOut:
    product = _load(db, product_id, include_inactive=True)
    raw = read_upload(file, settings.max_upload_mb * 1024 * 1024)
    data, content_type, filename = process_image(raw, max_side=1600)
    key = product_image_key(product.id, filename)
    storage.upload(key, data, content_type)
    make_primary = is_primary or not product.images
    if make_primary:
        for img in product.images:
            img.is_primary = False
    product.images.append(
        ProductImage(object_key=key, content_type=content_type, is_primary=make_primary, sort_order=len(product.images))
    )
    db.commit()
    db.refresh(product)
    cache.bump_catalog_version()
    log_event("PRODUCT_IMAGE_UPLOADED", user_id=admin.id, product_id=product.id, object_key=key)
    return product_out(product, storage, settings.currency)


@router.delete(
    "/{product_id}/images/{image_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a product image (admin)"
)
def delete_image(
    product_id: int,
    image_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    cache: CacheService = Depends(get_cache),
    storage: StorageService = Depends(get_storage),
) -> Response:
    image = db.get(ProductImage, image_id)
    if image is None or image.product_id != product_id:
        raise NotFoundError("Image not found", code="IMAGE_NOT_FOUND")
    key, was_primary = image.object_key, image.is_primary
    db.delete(image)
    db.flush()
    if was_primary:
        next_img = db.scalar(
            select(ProductImage).where(ProductImage.product_id == product_id).order_by(ProductImage.sort_order)
        )
        if next_img:
            next_img.is_primary = True
    db.commit()
    try:
        storage.delete(key)
    except StorageUnavailableError:
        logger.warning("object not deleted from storage", extra={"object_key": key})
    cache.bump_catalog_version()
    log_event("PRODUCT_IMAGE_DELETED", user_id=admin.id, product_id=product_id, object_key=key)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
