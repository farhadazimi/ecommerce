"""Shopping cart. Source of truth is RDS (``cart`` / ``cart_items``); the rendered cart is
cached per user in Redis and invalidated on every change."""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ecommerce_common.cache import CacheService
from ecommerce_common.config import Settings
from ecommerce_common.errors import AppError, ConflictError, NotFoundError
from ecommerce_common.models import Cart, CartItem, Product, User
from ecommerce_common.pricing import money, shipping_fee
from ecommerce_common.storage import StorageService

from ..deps import get_cache, get_current_user, get_db, get_settings, get_storage
from ..schemas import CartItemIn, CartItemOut, CartItemUpdate, CartOut, CartProduct
from ..services.serializers import safe_url

router = APIRouter(prefix="/api/cart", tags=["Cart"])


def _get_or_create_cart(db: Session, user_id: int) -> Cart:
    cart = db.scalar(select(Cart).where(Cart.user_id == user_id))
    if cart is None:
        cart = Cart(user_id=user_id)
        db.add(cart)
        try:
            db.flush()
        except IntegrityError:  # concurrent creation by another request/pod
            db.rollback()
            cart = db.scalar(select(Cart).where(Cart.user_id == user_id))
    return cart


def build_cart(cart: Cart | None, storage: StorageService, settings: Settings) -> CartOut:
    items: list[CartItemOut] = []
    warnings: list[str] = []
    subtotal = Decimal("0")
    for item in (cart.items if cart else []):
        p = item.product
        available = p.inventory.available if p.inventory else 0
        purchasable = p.is_active and p.deleted_at is None
        line_total = money(p.price * item.quantity)
        if not purchasable:
            warnings.append(f"'{p.name}' is no longer available")
        elif item.quantity > available:
            warnings.append(f"Only {available} unit(s) of '{p.name}' are in stock")
        subtotal += line_total
        image = p.images[0].object_key if p.images else None
        items.append(
            CartItemOut(
                id=item.id,
                product=CartProduct(
                    id=p.id, name=p.name, slug=p.slug, sku=p.sku, price=p.price,
                    image_url=safe_url(storage, image), is_active=purchasable,
                ),
                quantity=item.quantity,
                unit_price=p.price,
                line_total=line_total,
                available=available,
                in_stock=purchasable and available >= item.quantity,
            )
        )
    subtotal = money(subtotal)
    fee = shipping_fee(subtotal, settings)
    return CartOut(
        id=cart.id if cart else None,
        items=items,
        item_count=sum(i.quantity for i in items),
        subtotal=subtotal,
        shipping_fee=fee,
        total=money(subtotal + fee),
        currency=settings.currency,
        warnings=warnings,
    )


def _purchasable_product(db: Session, product_id: int) -> Product:
    product = db.get(Product, product_id)
    if product is None or product.deleted_at is not None or not product.is_active:
        raise NotFoundError("Product not found", code="PRODUCT_NOT_FOUND")
    return product


def _check_stock(product: Product, quantity: int) -> None:
    available = product.inventory.available if product.inventory else 0
    if quantity > available:
        raise ConflictError(
            f"Only {available} unit(s) of '{product.name}' are available",
            code="INSUFFICIENT_INVENTORY",
            details=[{"product_id": product.id, "requested": quantity, "available": available}],
        )


def _respond(db: Session, user: User, cache: CacheService, storage: StorageService, settings: Settings) -> CartOut:
    cache.invalidate_cart(user.id)
    db.expire_all()
    cart = db.scalar(select(Cart).where(Cart.user_id == user.id))
    return build_cart(cart, storage, settings)


@router.get("", response_model=CartOut, summary="Get my cart")
def get_cart(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    cache: CacheService = Depends(get_cache),
    storage: StorageService = Depends(get_storage),
    settings: Settings = Depends(get_settings),
) -> CartOut:
    key = cache.key("cart", user.id)
    cached = cache.get_json(key)
    if cached is not None:
        return CartOut(**cached)
    cart = db.scalar(select(Cart).where(Cart.user_id == user.id))
    result = build_cart(cart, storage, settings)
    cache.set_json(key, result.model_dump(mode="json"), ttl=settings.cart_cache_ttl)
    return result


@router.post("/items", response_model=CartOut, status_code=status.HTTP_201_CREATED, summary="Add a product to my cart")
def add_item(
    body: CartItemIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    cache: CacheService = Depends(get_cache),
    storage: StorageService = Depends(get_storage),
    settings: Settings = Depends(get_settings),
) -> CartOut:
    product = _purchasable_product(db, body.product_id)
    cart = _get_or_create_cart(db, user.id)
    existing = next((i for i in cart.items if i.product_id == product.id), None)
    new_qty = (existing.quantity if existing else 0) + body.quantity
    if new_qty > 99:
        raise AppError("Maximum quantity per product is 99", code="QUANTITY_LIMIT", status_code=422)
    _check_stock(product, new_qty)
    if existing:
        existing.quantity = new_qty
    else:
        cart.items.append(CartItem(product_id=product.id, quantity=body.quantity))
    db.commit()
    return _respond(db, user, cache, storage, settings)


def _own_item(db: Session, user: User, item_id: int) -> CartItem:
    item = db.get(CartItem, item_id)
    if item is None or item.cart.user_id != user.id:
        raise NotFoundError("Cart item not found", code="CART_ITEM_NOT_FOUND")
    return item


@router.put("/items/{item_id}", response_model=CartOut, summary="Change the quantity of a cart item")
def update_item(
    item_id: int,
    body: CartItemUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    cache: CacheService = Depends(get_cache),
    storage: StorageService = Depends(get_storage),
    settings: Settings = Depends(get_settings),
) -> CartOut:
    item = _own_item(db, user, item_id)
    _check_stock(_purchasable_product(db, item.product_id), body.quantity)
    item.quantity = body.quantity
    db.commit()
    return _respond(db, user, cache, storage, settings)


@router.delete("/items/{item_id}", response_model=CartOut, summary="Remove a cart item")
def delete_item(
    item_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    cache: CacheService = Depends(get_cache),
    storage: StorageService = Depends(get_storage),
    settings: Settings = Depends(get_settings),
) -> CartOut:
    db.delete(_own_item(db, user, item_id))
    db.commit()
    return _respond(db, user, cache, storage, settings)


@router.delete("", response_model=CartOut, summary="Empty my cart")
def clear_cart(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    cache: CacheService = Depends(get_cache),
    storage: StorageService = Depends(get_storage),
    settings: Settings = Depends(get_settings),
) -> CartOut:
    cart = db.scalar(select(Cart).where(Cart.user_id == user.id))
    if cart:
        cart.items.clear()
        db.commit()
    return _respond(db, user, cache, storage, settings)
