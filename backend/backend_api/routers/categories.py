from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ecommerce_common.cache import CacheService
from ecommerce_common.errors import ConflictError, NotFoundError
from ecommerce_common.log import log_event
from ecommerce_common.models import Category, Product, User

from ..deps import get_cache, get_db, require_admin
from ..schemas import CategoryIn, CategoryOut, CategoryUpdate
from ..services.text import slugify

router = APIRouter(prefix="/api/categories", tags=["Categories"])


def _counts(db: Session) -> dict[int, int]:
    rows = db.execute(
        select(Product.category_id, func.count())
        .where(Product.deleted_at.is_(None), Product.is_active.is_(True))
        .group_by(Product.category_id)
    ).all()
    return {cid: n for cid, n in rows if cid is not None}


def _out(c: Category, counts: dict[int, int]) -> CategoryOut:
    return CategoryOut(id=c.id, name=c.name, slug=c.slug, description=c.description, product_count=counts.get(c.id, 0))


@router.get("", response_model=list[CategoryOut], summary="List categories")
def list_categories(db: Session = Depends(get_db), cache: CacheService = Depends(get_cache)) -> list[CategoryOut]:
    key = cache.key("catalog", cache.catalog_version(), "categories")
    cached = cache.get_json(key)
    if cached is not None:
        return [CategoryOut(**c) for c in cached]
    counts = _counts(db)
    result = [_out(c, counts) for c in db.scalars(select(Category).order_by(Category.name))]
    cache.set_json(key, [c.model_dump() for c in result], ttl=300)
    return result


@router.get("/{category_id}", response_model=CategoryOut, summary="Get a category")
def get_category(category_id: int, db: Session = Depends(get_db)) -> CategoryOut:
    category = db.get(Category, category_id)
    if category is None:
        raise NotFoundError("Category not found", code="CATEGORY_NOT_FOUND")
    return _out(category, _counts(db))


@router.post("", response_model=CategoryOut, status_code=status.HTTP_201_CREATED, summary="Create a category (admin)")
def create_category(
    body: CategoryIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    cache: CacheService = Depends(get_cache),
) -> CategoryOut:
    category = Category(name=body.name, slug=body.slug or slugify(body.name), description=body.description)
    db.add(category)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("A category with this name or slug already exists", code="CATEGORY_EXISTS") from exc
    cache.bump_catalog_version()
    log_event("CATEGORY_CREATED", user_id=admin.id, category_id=category.id)
    return _out(category, {})


@router.put("/{category_id}", response_model=CategoryOut, summary="Update a category (admin)")
def update_category(
    category_id: int,
    body: CategoryUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    cache: CacheService = Depends(get_cache),
) -> CategoryOut:
    category = db.get(Category, category_id)
    if category is None:
        raise NotFoundError("Category not found", code="CATEGORY_NOT_FOUND")
    for field, value in body.model_dump(exclude_unset=True).items():
        if value is not None or field == "description":
            setattr(category, field, value)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("A category with this name or slug already exists", code="CATEGORY_EXISTS") from exc
    cache.bump_catalog_version()
    log_event("CATEGORY_UPDATED", user_id=admin.id, category_id=category.id)
    return _out(category, _counts(db))


@router.delete("/{category_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a category (admin)")
def delete_category(
    category_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    cache: CacheService = Depends(get_cache),
) -> Response:
    category = db.get(Category, category_id)
    if category is None:
        raise NotFoundError("Category not found", code="CATEGORY_NOT_FOUND")
    in_use = db.scalar(
        select(func.count()).select_from(Product).where(Product.category_id == category_id, Product.deleted_at.is_(None))
    )
    if in_use:
        raise ConflictError(
            f"Category still contains {in_use} product(s); move or delete them first", code="CATEGORY_NOT_EMPTY"
        )
    db.delete(category)
    db.commit()
    cache.bump_catalog_version()
    log_event("CATEGORY_DELETED", user_id=admin.id, category_id=category_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
