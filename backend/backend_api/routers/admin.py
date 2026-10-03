from __future__ import annotations

import math
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ecommerce_common.cache import CacheService
from ecommerce_common.config import Settings
from ecommerce_common.errors import AppError, ConflictError, NotFoundError
from ecommerce_common.log import log_event
from ecommerce_common.models import (
    Category,
    Inventory,
    Order,
    OrderStatus,
    Product,
    Role,
    RoleName,
    User,
    utcnow,
)
from ecommerce_common.pricing import money
from ecommerce_common.storage import StorageService

from ..deps import get_cache, get_db, get_order_client, get_settings, get_storage, require_admin
from ..schemas import (
    InventoryOut,
    InventoryUpdate,
    OrderStatusUpdate,
    Page,
    Statistics,
    UserOut,
    UserStatusUpdate,
)
from ..services.order_client import OrderServiceClient
from ..services.serializers import user_out
from ..services.text import like_pattern

router = APIRouter(prefix="/api/admin", tags=["Admin"], dependencies=[Depends(require_admin)])

REVENUE_STATUSES = (OrderStatus.PAID, OrderStatus.PROCESSING, OrderStatus.SHIPPED, OrderStatus.DELIVERED)


@router.get("/users", response_model=Page[UserOut], summary="List users")
def list_users(
    q: str | None = Query(default=None, max_length=100),
    role: str | None = Query(default=None, pattern="^(ADMIN|CUSTOMER)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    storage: StorageService = Depends(get_storage),
) -> Page[UserOut]:
    query = select(User).join(Role)
    if q:
        pattern = like_pattern(q.strip())
        query = query.where(or_(User.email.like(pattern, escape="\\"), User.full_name.like(pattern, escape="\\")))
    if role:
        query = query.where(Role.name == role)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    users = db.scalars(query.order_by(User.id.desc()).offset((page - 1) * page_size).limit(page_size)).unique().all()
    return Page[UserOut](
        items=[user_out(u, storage) for u in users], total=total, page=page, page_size=page_size,
        pages=math.ceil(total / page_size) if total else 0,
    )


@router.put("/users/{user_id}/status", response_model=UserOut, summary="Activate / deactivate a user")
def set_user_status(
    user_id: int,
    body: UserStatusUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    storage: StorageService = Depends(get_storage),
) -> UserOut:
    user = db.get(User, user_id)
    if user is None:
        raise NotFoundError("User not found", code="USER_NOT_FOUND")
    if user.id == admin.id and not body.is_active:
        raise AppError("You cannot deactivate your own account", code="CANNOT_DEACTIVATE_SELF")
    user.is_active = body.is_active
    db.commit()
    log_event("USER_STATUS_CHANGED", user_id=admin.id, target_user_id=user.id, is_active=body.is_active)
    return user_out(user, storage)


@router.get("/orders", summary="List all orders")
def list_orders(
    status_filter: str | None = Query(default=None, alias="status", pattern="^[A-Z_]+$"),
    q: str | None = Query(default=None, max_length=64, description="Order number or customer e-mail"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    admin: User = Depends(require_admin),
    client: OrderServiceClient = Depends(get_order_client),
) -> Any:
    return client.request(
        "GET", "/internal/orders", user=admin,
        params={"status": status_filter, "q": q, "page": page, "page_size": page_size},
    )


@router.get("/orders/{order_id}", summary="Order details (admin)")
def get_order(order_id: int, admin: User = Depends(require_admin), client: OrderServiceClient = Depends(get_order_client)) -> Any:
    return client.request("GET", f"/internal/orders/{order_id}", user=admin)


@router.put("/orders/{order_id}/status", summary="Change order status")
def update_order_status(
    order_id: int,
    body: OrderStatusUpdate,
    admin: User = Depends(require_admin),
    client: OrderServiceClient = Depends(get_order_client),
    cache: CacheService = Depends(get_cache),
) -> Any:
    result = client.request(
        "PUT", f"/internal/orders/{order_id}/status", user=admin, json=body.model_dump()
    )
    if body.status == OrderStatus.CANCELLED:
        cache.bump_catalog_version()
    return result


@router.get("/inventory", response_model=Page[InventoryOut], summary="Inventory levels")
def list_inventory(
    q: str | None = Query(default=None, max_length=100),
    low_stock: bool = False,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> Page[InventoryOut]:
    query = (
        select(Product, Inventory)
        .join(Inventory, Inventory.product_id == Product.id)
        .where(Product.deleted_at.is_(None))
    )
    if q:
        pattern = like_pattern(q.strip())
        query = query.where(or_(Product.name.like(pattern, escape="\\"), Product.sku.like(pattern, escape="\\")))
    if low_stock:
        query = query.where(Inventory.quantity - Inventory.reserved <= Inventory.low_stock_threshold)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.execute(
        query.order_by((Inventory.quantity - Inventory.reserved).asc(), Product.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).unique().all()
    items = [_inventory_out(p, inv) for p, inv in rows]
    return Page[InventoryOut](
        items=items, total=total, page=page, page_size=page_size, pages=math.ceil(total / page_size) if total else 0
    )


def _inventory_out(p: Product, inv: Inventory) -> InventoryOut:
    return InventoryOut(
        product_id=p.id, sku=p.sku, name=p.name, is_active=p.is_active, quantity=inv.quantity, reserved=inv.reserved,
        available=inv.available, low_stock_threshold=inv.low_stock_threshold,
        low_stock=inv.available <= inv.low_stock_threshold, updated_at=inv.updated_at,
    )


@router.put("/inventory/{product_id}", response_model=InventoryOut, summary="Set or adjust stock for a product")
def update_inventory(
    product_id: int,
    body: InventoryUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    cache: CacheService = Depends(get_cache),
) -> InventoryOut:
    if body.quantity is None and body.adjust is None and body.low_stock_threshold is None:
        raise AppError("Provide quantity, adjust or low_stock_threshold", code="VALIDATION_ERROR", status_code=422)
    if body.quantity is not None and body.adjust is not None:
        raise AppError("Use either quantity or adjust, not both", code="VALIDATION_ERROR", status_code=422)
    product = db.get(Product, product_id)
    if product is None or product.deleted_at is not None:
        raise NotFoundError("Product not found", code="PRODUCT_NOT_FOUND")
    inv = db.scalar(select(Inventory).where(Inventory.product_id == product_id).with_for_update().execution_options(populate_existing=True))
    if inv is None:
        inv = Inventory(product_id=product_id, quantity=0, reserved=0)
        db.add(inv)
        db.flush()
    before = inv.quantity
    new_qty = body.quantity if body.quantity is not None else inv.quantity + (body.adjust or 0)
    if new_qty < inv.reserved:
        raise ConflictError(
            f"On-hand stock cannot go below the {inv.reserved} unit(s) reserved by pending orders",
            code="STOCK_BELOW_RESERVED",
        )
    inv.quantity = new_qty
    if body.low_stock_threshold is not None:
        inv.low_stock_threshold = body.low_stock_threshold
    db.commit()
    cache.bump_catalog_version()
    log_event("INVENTORY_UPDATED", user_id=admin.id, product_id=product_id, before=before, after=new_qty)
    return _inventory_out(product, inv)


@router.get("/statistics", response_model=Statistics, summary="Dashboard statistics")
def statistics(
    db: Session = Depends(get_db),
    cache: CacheService = Depends(get_cache),
    settings: Settings = Depends(get_settings),
) -> Statistics:
    not_deleted = Product.deleted_at.is_(None)
    available = Inventory.quantity - Inventory.reserved
    by_status = dict(db.execute(select(Order.status, func.count()).group_by(Order.status)).all())
    since = utcnow() - timedelta(hours=24)
    recent = db.scalars(select(Order).order_by(Order.created_at.desc(), Order.id.desc()).limit(5)).unique().all()
    return Statistics(
        products=db.scalar(select(func.count()).select_from(Product).where(not_deleted)) or 0,
        active_products=db.scalar(
            select(func.count()).select_from(Product).where(not_deleted, Product.is_active.is_(True))
        ) or 0,
        categories=db.scalar(select(func.count()).select_from(Category)) or 0,
        users=db.scalar(select(func.count()).select_from(User)) or 0,
        customers=db.scalar(
            select(func.count()).select_from(User).join(Role).where(Role.name == RoleName.CUSTOMER)
        ) or 0,
        orders=sum(by_status.values()),
        orders_today=db.scalar(select(func.count()).select_from(Order).where(Order.created_at >= since)) or 0,
        orders_by_status={s: int(by_status.get(s, 0)) for s in OrderStatus.ALL},
        revenue=money(
            db.scalar(select(func.coalesce(func.sum(Order.total), 0)).where(Order.status.in_(REVENUE_STATUSES))) or 0
        ),
        currency=settings.currency,
        low_stock_products=db.scalar(
            select(func.count()).select_from(Inventory).join(Product)
            .where(not_deleted, available > 0, available <= Inventory.low_stock_threshold)
        ) or 0,
        out_of_stock_products=db.scalar(
            select(func.count()).select_from(Inventory).join(Product).where(not_deleted, available <= 0)
        ) or 0,
        active_users=cache.active_user_count(),
        recent_orders=[
            {
                "id": o.id, "order_number": o.order_number, "status": o.status, "total": float(o.total),
                "customer": o.user.email, "created_at": o.created_at.isoformat(),
            }
            for o in recent
        ],
    )
