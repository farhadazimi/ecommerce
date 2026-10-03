"""Shared pytest fixtures.

By default tests run against SQLite + in-memory cache + local storage (fast, no services).
Point them at real services to test RDS/DCS compatibility:

    TEST_DATABASE_URL=mysql+pymysql://user:pass@127.0.0.1:3306/ecommerce_test \
    TEST_REDIS_HOST=127.0.0.1 TEST_REDIS_PASSWORD=... pytest
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from ecommerce_common import security
from ecommerce_common.cache import CacheService, MemoryCache, RedisCache
from ecommerce_common.config import Settings
from ecommerce_common.db import Database
from ecommerce_common.models import Base, Category, Inventory, Product, Role, RoleName, User, UserProfile
from ecommerce_common.storage import LocalStorageService

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL")
TEST_REDIS_HOST = os.environ.get("TEST_REDIS_HOST")

ADMIN_EMAIL, ADMIN_PASSWORD = "admin@example.com", "AdminPass123"
CUSTOMER_EMAIL, CUSTOMER_PASSWORD = "customer@example.com", "CustomerPass123"


@pytest.fixture(autouse=True)
def _fast_bcrypt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(security, "_BCRYPT_ROUNDS", 4)


@pytest.fixture(scope="session")
def session_tmp(tmp_path_factory: pytest.TempPathFactory):
    return tmp_path_factory.mktemp("session")


@pytest.fixture(scope="session")
def base_settings(session_tmp) -> Settings:
    return Settings(
        app_env="test",
        service_name="test",
        log_json=True,
        log_level="WARNING",
        database_url=TEST_DB_URL or f"sqlite:///{session_tmp}/test.db",
        redis_host=TEST_REDIS_HOST,
        redis_password=os.environ.get("TEST_REDIS_PASSWORD"),
        redis_port=int(os.environ.get("TEST_REDIS_PORT", "6379")),
        redis_db=15,
        storage_backend="local",
        local_storage_path=str(session_tmp / "storage"),
        public_base_url="http://testserver",
        rate_limit_enabled=False,
        order_reaper_enabled=False,
        cors_origins="http://localhost:8080",
        trusted_proxies="127.0.0.1/32,10.0.0.0/8",
    )


@pytest.fixture
def settings(base_settings: Settings) -> Settings:
    return base_settings


@pytest.fixture(scope="session")
def database(base_settings: Settings) -> Iterator[Database]:
    db = Database(base_settings)
    Base.metadata.drop_all(db.engine)
    Base.metadata.create_all(db.engine)
    yield db
    db.dispose()


@pytest.fixture(autouse=False)
def clean_db(database: Database) -> Database:
    """Empty every table and re-insert the reference roles before a test."""
    with database.transaction() as s:
        for table in reversed(Base.metadata.sorted_tables):
            s.execute(delete(table))
        s.add_all([Role(name=RoleName.ADMIN), Role(name=RoleName.CUSTOMER)])
    return database


@pytest.fixture
def cache(base_settings: Settings) -> CacheService:
    if TEST_REDIS_HOST:
        svc = CacheService(RedisCache(base_settings), prefix=f"test{uuid.uuid4().hex[:6]}")
        svc.backend.client.flushdb()  # type: ignore[attr-defined]
        return svc
    return CacheService(MemoryCache(), prefix="test")


@pytest.fixture
def storage(base_settings: Settings, tmp_path) -> LocalStorageService:
    return LocalStorageService(base_settings.model_copy(update={"local_storage_path": str(tmp_path / "objects")}))


@pytest.fixture
def order_app(settings: Settings, clean_db: Database, cache: CacheService, storage: LocalStorageService):
    from order_service.main import create_app

    return create_app(settings.model_copy(update={"service_name": "order-service"}), database=clean_db, cache=cache, storage=storage)


@pytest.fixture
def order_http(order_app) -> TestClient:
    return TestClient(order_app, base_url="http://order-service")


@pytest.fixture
def backend_app(settings: Settings, clean_db: Database, cache: CacheService, storage: LocalStorageService, order_http):
    from backend_api.main import create_app
    from backend_api.services.order_client import OrderServiceClient

    s = settings.model_copy(update={"service_name": "backend-api"})
    return create_app(
        s, database=clean_db, cache=cache, storage=storage, order_client=OrderServiceClient(s, client=order_http)
    )


@pytest.fixture
def client(backend_app) -> TestClient:
    return TestClient(backend_app)


# ------------------------------------------------------------------ data helpers
def _make_user(db: Database, email: str, password: str, role: str, name: str) -> int:
    with db.transaction() as s:
        role_obj = s.scalar(select(Role).where(Role.name == role))
        user = User(email=email, password_hash=security.hash_password(password), full_name=name, role=role_obj)
        user.profile = UserProfile()
        s.add(user)
        s.flush()
        return user.id


@pytest.fixture
def admin_user(clean_db: Database) -> int:
    return _make_user(clean_db, ADMIN_EMAIL, ADMIN_PASSWORD, RoleName.ADMIN, "Test Admin")


@pytest.fixture
def customer_user(clean_db: Database) -> int:
    return _make_user(clean_db, CUSTOMER_EMAIL, CUSTOMER_PASSWORD, RoleName.CUSTOMER, "Test Customer")


def login(client: TestClient, email: str, password: str) -> dict[str, str]:
    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    client.cookies.clear()  # use explicit bearer headers in tests
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture
def admin_headers(client: TestClient, admin_user: int) -> dict[str, str]:
    return login(client, ADMIN_EMAIL, ADMIN_PASSWORD)


@pytest.fixture
def customer_headers(client: TestClient, customer_user: int) -> dict[str, str]:
    return login(client, CUSTOMER_EMAIL, CUSTOMER_PASSWORD)


@pytest.fixture
def make_product(clean_db: Database):
    counter = {"n": 0}

    def _make(name: str = "Test Product", price: str = "10.00", stock: int = 10, active: bool = True,
              category: str | None = "General") -> int:
        counter["n"] += 1
        with clean_db.transaction() as s:
            cat = None
            if category:
                cat = s.scalar(select(Category).where(Category.name == category))
                if cat is None:
                    cat = Category(name=category, slug=category.lower().replace(" ", "-"))
                    s.add(cat)
            p = Product(sku=f"SKU-{counter['n']:04d}", name=name, slug=f"{name.lower().replace(' ', '-')}-{counter['n']}",
                        description=f"{name} description", price=Decimal(price), category=cat, is_active=active)
            p.inventory = Inventory(quantity=stock, reserved=0, low_stock_threshold=2)
            s.add(p)
            s.flush()
            return p.id

    return _make


SHIPPING = {
    "shipping_name": "Test Customer",
    "shipping_phone": "+98 21 1234 5678",
    "shipping_address_line1": "1 Cloud Street",
    "shipping_city": "Tehran",
    "shipping_postal_code": "10001",
    "shipping_country": "Iran",
}

CARD_OK = {"card_number": "4242 4242 4242 4242", "card_holder": "Test Customer", "expiry_month": 12, "expiry_year": 2035, "cvv": "123"}
CARD_DECLINED = {**CARD_OK, "card_number": "4000 0000 0000 0002"}


def png_bytes(size: tuple[int, int] = (64, 64), colour: tuple[int, int, int] = (200, 30, 30)) -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, format="PNG")
    return buf.getvalue()
