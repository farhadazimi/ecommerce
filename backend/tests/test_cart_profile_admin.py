from __future__ import annotations

from conftest import png_bytes


class TestCart:
    def test_add_update_remove_clear(self, client, customer_headers, make_product):
        p1 = make_product("Mouse", "25.00", stock=10)
        p2 = make_product("Pad", "10.00", stock=10)
        empty = client.get("/api/cart", headers=customer_headers).json()
        assert empty["items"] == [] and empty["total"] == 0

        r = client.post("/api/cart/items", json={"product_id": p1, "quantity": 2}, headers=customer_headers)
        assert r.status_code == 201
        cart = client.post("/api/cart/items", json={"product_id": p2}, headers=customer_headers).json()
        assert cart["item_count"] == 3 and cart["subtotal"] == 60.0 and cart["shipping_fee"] == 5.0 and cart["total"] == 65.0

        # adding the same product again increments
        cart = client.post("/api/cart/items", json={"product_id": p1, "quantity": 1}, headers=customer_headers).json()
        item1 = next(i for i in cart["items"] if i["product"]["id"] == p1)
        assert item1["quantity"] == 3

        cart = client.put(f"/api/cart/items/{item1['id']}", json={"quantity": 5}, headers=customer_headers).json()
        assert next(i for i in cart["items"] if i["id"] == item1["id"])["line_total"] == 125.0
        assert cart["shipping_fee"] == 0.0  # free shipping over threshold

        cart = client.delete(f"/api/cart/items/{item1['id']}", headers=customer_headers).json()
        assert [i["product"]["id"] for i in cart["items"]] == [p2]
        cart = client.delete("/api/cart", headers=customer_headers).json()
        assert cart["items"] == [] and client.get("/api/cart", headers=customer_headers).json()["item_count"] == 0

    def test_stock_and_validation_errors(self, client, customer_headers, make_product):
        pid = make_product("Rare", stock=2)
        r = client.post("/api/cart/items", json={"product_id": pid, "quantity": 3}, headers=customer_headers)
        assert r.status_code == 409 and r.json()["error"]["code"] == "INSUFFICIENT_INVENTORY"
        assert r.json()["error"]["details"][0]["available"] == 2
        r = client.post("/api/cart/items", json={"product_id": 999999}, headers=customer_headers)
        assert r.status_code == 404 and r.json()["error"]["code"] == "PRODUCT_NOT_FOUND"
        assert client.post("/api/cart/items", json={"product_id": pid, "quantity": 0}, headers=customer_headers).status_code == 422
        assert client.get("/api/cart").status_code == 401

    def test_cannot_touch_other_users_items(self, client, customer_headers, admin_headers, make_product):
        pid = make_product("Mine")
        item_id = client.post("/api/cart/items", json={"product_id": pid}, headers=customer_headers).json()["items"][0]["id"]
        r = client.put(f"/api/cart/items/{item_id}", json={"quantity": 2}, headers=admin_headers)
        assert r.status_code == 404 and r.json()["error"]["code"] == "CART_ITEM_NOT_FOUND"
        assert client.delete(f"/api/cart/items/{item_id}", headers=admin_headers).status_code == 404

    def test_cart_reflects_deleted_product(self, client, customer_headers, admin_headers, make_product):
        pid = make_product("Gone soon")
        client.post("/api/cart/items", json={"product_id": pid}, headers=customer_headers)
        client.delete(f"/api/products/{pid}", headers=admin_headers)
        assert client.get("/api/cart", headers=customer_headers).json()["items"] == []


class TestProfile:
    def test_update_profile_password_and_avatar(self, client, customer_headers):
        r = client.put("/api/profile", json={"full_name": "Renamed", "city": "Shiraz", "phone": "+98 912 000 0000"},
                       headers=customer_headers)
        assert r.status_code == 200 and r.json()["full_name"] == "Renamed" and r.json()["profile"]["city"] == "Shiraz"
        assert client.put("/api/profile", json={"city": "<script>"}, headers=customer_headers).status_code == 422

        r = client.put("/api/profile/password", json={"current_password": "wrong", "new_password": "NewPass123"},
                       headers=customer_headers)
        assert r.status_code == 401
        r = client.put("/api/profile/password", json={"current_password": "CustomerPass123", "new_password": "NewPass123"},
                       headers=customer_headers)
        assert r.status_code == 200
        assert client.post("/api/auth/login", json={"email": "customer@example.com", "password": "NewPass123"}).status_code == 200

        r = client.post("/api/profile/avatar", files={"file": ("me.png", png_bytes(), "image/png")}, headers=customer_headers)
        assert r.status_code == 200 and "images/avatars/" in r.json()["profile"]["avatar_url"]


class TestAdmin:
    def test_admin_endpoints_require_admin(self, client, customer_headers):
        for path in ("/api/admin/users", "/api/admin/orders", "/api/admin/statistics", "/api/admin/inventory"):
            assert client.get(path, headers=customer_headers).status_code == 403, path
            assert client.get(path).status_code == 401, path

    def test_users_statistics_inventory(self, client, admin_headers, customer_user, make_product):
        pid = make_product("Low", stock=1)
        make_product("None", stock=0)
        users = client.get("/api/admin/users", headers=admin_headers).json()
        assert users["total"] == 2
        assert client.get("/api/admin/users?role=CUSTOMER", headers=admin_headers).json()["total"] == 1

        stats = client.get("/api/admin/statistics", headers=admin_headers).json()
        assert stats["products"] == 2 and stats["users"] == 2 and stats["customers"] == 1 and stats["orders"] == 0
        assert stats["low_stock_products"] == 1 and stats["out_of_stock_products"] == 1

        inv = client.get("/api/admin/inventory?low_stock=true", headers=admin_headers).json()
        assert inv["total"] == 2
        r = client.put(f"/api/admin/inventory/{pid}", json={"adjust": 10}, headers=admin_headers)
        assert r.json()["quantity"] == 11 and not r.json()["low_stock"]
        r = client.put(f"/api/admin/inventory/{pid}", json={"quantity": 3, "low_stock_threshold": 4}, headers=admin_headers)
        assert r.json()["quantity"] == 3 and r.json()["low_stock"]
        r = client.put(f"/api/admin/inventory/{pid}", json={"adjust": -10}, headers=admin_headers)
        assert r.status_code == 409 and r.json()["error"]["code"] == "STOCK_BELOW_RESERVED"
        assert client.put(f"/api/admin/inventory/{pid}", json={}, headers=admin_headers).status_code == 422

    def test_admin_cannot_deactivate_self(self, client, admin_headers, admin_user):
        r = client.put(f"/api/admin/users/{admin_user}/status", json={"is_active": False}, headers=admin_headers)
        assert r.status_code == 400 and r.json()["error"]["code"] == "CANNOT_DEACTIVATE_SELF"
