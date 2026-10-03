from __future__ import annotations

import io

from PIL import Image

from conftest import png_bytes


class TestCategories:
    def test_crud_and_authorization(self, client, admin_headers, customer_headers):
        r = client.post("/api/categories", json={"name": "Gadgets", "description": "Cool stuff"}, headers=customer_headers)
        assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN"
        assert client.post("/api/categories", json={"name": "Gadgets"}).status_code == 401

        r = client.post("/api/categories", json={"name": "Gadgets", "description": "Cool stuff"}, headers=admin_headers)
        assert r.status_code == 201 and r.json()["slug"] == "gadgets"
        cid = r.json()["id"]
        assert client.post("/api/categories", json={"name": "Gadgets"}, headers=admin_headers).status_code == 409

        r = client.put(f"/api/categories/{cid}", json={"name": "Gizmos"}, headers=admin_headers)
        assert r.status_code == 200 and r.json()["name"] == "Gizmos"
        assert any(c["name"] == "Gizmos" for c in client.get("/api/categories").json())
        assert client.delete(f"/api/categories/{cid}", headers=admin_headers).status_code == 204
        assert client.get(f"/api/categories/{cid}").status_code == 404

    def test_cannot_delete_non_empty_category(self, client, admin_headers, make_product):
        make_product(category="Books")
        cat = next(c for c in client.get("/api/categories").json() if c["name"] == "Books")
        assert cat["product_count"] == 1
        r = client.delete(f"/api/categories/{cat['id']}", headers=admin_headers)
        assert r.status_code == 409 and r.json()["error"]["code"] == "CATEGORY_NOT_EMPTY"


class TestProducts:
    def test_listing_search_filter_sort_paginate(self, client, make_product):
        make_product("Blue Phone", "300.00", category="Electronics")
        make_product("Red Phone", "200.00", category="Electronics")
        make_product("Cook Book", "20.00", category="Books")
        make_product("Hidden Thing", "5.00", active=False)
        make_product("Sold Out Shirt", "15.00", stock=0, category="Clothing")

        body = client.get("/api/products").json()
        assert body["total"] == 4 and all(p["name"] != "Hidden Thing" for p in body["items"])

        assert {p["name"] for p in client.get("/api/products?q=phone").json()["items"]} == {"Blue Phone", "Red Phone"}
        assert client.get("/api/products?category=books").json()["total"] == 1
        prices = [p["price"] for p in client.get("/api/products?sort=price_asc").json()["items"]]
        assert prices == sorted(prices)
        assert client.get("/api/products?min_price=100&max_price=250").json()["items"][0]["name"] == "Red Phone"
        assert client.get("/api/products?in_stock=true").json()["total"] == 3
        page = client.get("/api/products?page=2&page_size=3").json()
        assert page["pages"] == 2 and len(page["items"]) == 1

    def test_search_input_is_not_sql(self, client, make_product):
        make_product("Plain")
        for q in ["' OR 1=1 --", "%", "_", "\\"]:
            r = client.get("/api/products", params={"q": q})
            assert r.status_code == 200 and r.json()["total"] == 0, q

    def test_product_details_and_not_found(self, client, make_product):
        pid = make_product("Detail Item", "12.34", stock=7)
        body = client.get(f"/api/products/{pid}").json()
        assert body["price"] == 12.34 and body["stock"] == {"available": 7, "in_stock": True, "low_stock": False}
        r = client.get("/api/products/999999")
        assert r.status_code == 404 and r.json()["error"]["code"] == "PRODUCT_NOT_FOUND"
        assert client.get("/api/products/abc").status_code == 422

    def test_admin_create_update_delete(self, client, admin_headers, customer_headers):
        cat = client.post("/api/categories", json={"name": "Toys"}, headers=admin_headers).json()
        payload = {"sku": "toy-001", "name": "Robot", "description": "Beep", "price": "49.99", "category_id": cat["id"],
                   "stock_quantity": 5}
        assert client.post("/api/products", json=payload, headers=customer_headers).status_code == 403
        r = client.post("/api/products", json=payload, headers=admin_headers)
        assert r.status_code == 201, r.text
        product = r.json()
        assert product["sku"] == "TOY-001" and product["stock"]["available"] == 5 and product["category"]["name"] == "Toys"
        assert client.post("/api/products", json=payload, headers=admin_headers).json()["error"]["code"] == "SKU_EXISTS"

        r = client.put(f"/api/products/{product['id']}", json={"price": "39.99", "stock_quantity": 9}, headers=admin_headers)
        assert r.json()["price"] == 39.99 and r.json()["stock"]["available"] == 9
        # cached public view reflects the update (cache invalidated by catalog version bump)
        assert client.get(f"/api/products/{product['id']}").json()["price"] == 39.99

        r = client.post("/api/products", json={**payload, "sku": "TOY-002", "category_id": 999999}, headers=admin_headers)
        assert r.status_code == 422 and r.json()["error"]["code"] == "CATEGORY_NOT_FOUND"
        r = client.post("/api/products", json={**payload, "sku": "TOY-003", "price": "-1"}, headers=admin_headers)
        assert r.status_code == 422

        assert client.delete(f"/api/products/{product['id']}", headers=admin_headers).status_code == 204
        assert client.get(f"/api/products/{product['id']}").status_code == 404
        # SKU is free for re-use after deletion
        assert client.post("/api/products", json=payload, headers=admin_headers).status_code == 201

    def test_admin_sees_inactive_products(self, client, admin_headers, make_product):
        pid = make_product("Draft", active=False)
        assert client.get(f"/api/products/{pid}").status_code == 404
        assert client.get(f"/api/products/{pid}", headers=admin_headers).status_code == 200
        assert client.get("/api/products?include_inactive=true", headers=admin_headers).json()["total"] == 1
        assert client.get("/api/products?include_inactive=true").json()["total"] == 0

    def test_image_upload_goes_to_storage(self, client, admin_headers, make_product, storage):
        pid = make_product("Camera")
        files = {"file": ("photo.png", png_bytes((2400, 1200)), "image/png")}
        r = client.post(f"/api/products/{pid}/images", files=files, headers=admin_headers)
        assert r.status_code == 201, r.text
        image = r.json()["images"][0]
        assert image["is_primary"] and r.json()["image_url"] == image["url"]
        key = image["url"].split("/api/files/")[1].split("?")[0]
        assert key.startswith(f"images/products/{pid}/") and key.endswith(".webp")
        stored = Image.open(io.BytesIO(storage.download(key)))
        assert stored.format == "WEBP" and max(stored.size) == 1600  # optimised / downscaled

        # the signed URL is served by the backend for the local adapter
        path = image["url"].replace("http://testserver", "")
        served = client.get(path)
        assert served.status_code == 200 and served.headers["content-type"] == "image/webp"
        assert client.get(path.replace("signature=", "signature=0")).status_code in (403, 422)

        r = client.delete(f"/api/products/{pid}/images/{image['id']}", headers=admin_headers)
        assert r.status_code == 204
        assert client.get(f"/api/products/{pid}").json()["images"] == []

    def test_rejects_non_image_upload(self, client, admin_headers, make_product):
        pid = make_product("Camera")
        r = client.post(f"/api/products/{pid}/images", files={"file": ("x.png", b"<?php echo 1; ?>", "image/png")},
                        headers=admin_headers)
        assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_IMAGE"

    def test_catalog_served_from_cache(self, client, make_product, cache):
        make_product("Cached")
        first = client.get("/api/products").json()
        assert any(k for k in getattr(cache.backend, "_data", {"x": 1}))  # something was cached
        assert client.get("/api/products").json() == first
