"""Concurrent checkouts must never oversell (SELECT ... FOR UPDATE on inventory rows).

Runs only against MySQL (TEST_DATABASE_URL) - SQLite has no row-level locking.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select

from conftest import SHIPPING, TEST_DB_URL
from ecommerce_common import security
from ecommerce_common.errors import ConflictError
from ecommerce_common.models import Cart, CartItem, Inventory, Order, Role, RoleName, User

pytestmark = pytest.mark.skipif(not (TEST_DB_URL or "").startswith("mysql"), reason="needs MySQL row locks")


def test_parallel_checkouts_do_not_oversell(order_app, clean_db, make_product):
    from order_service.schemas import OrderCreate

    pid = make_product("Hot item", stock=3)
    user_ids = []
    with clean_db.transaction() as s:
        role = s.scalar(select(Role).where(Role.name == RoleName.CUSTOMER))
        for i in range(10):
            u = User(email=f"buyer{i}@example.com", password_hash=security.hash_password("x1234567"), full_name=f"B{i}", role=role)
            s.add(u)
            s.flush()
            cart = Cart(user_id=u.id)
            s.add(cart)
            s.flush()
            s.add(CartItem(cart_id=cart.id, product_id=pid, quantity=1))
            user_ids.append(u.id)

    service = order_app.state.order_service

    def checkout(uid: int) -> str:
        session = clean_db.session()
        try:
            service.place_order(session, uid, OrderCreate(**SHIPPING))
            return "ok"
        except ConflictError:
            return "sold_out"
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(checkout, user_ids))

    assert results.count("ok") == 3 and results.count("sold_out") == 7
    with clean_db.transaction() as s:
        inv = s.get(Inventory, pid)
        assert (inv.quantity, inv.reserved) == (3, 3)
        assert len(s.scalars(select(Order)).all()) == 3
