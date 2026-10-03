"""Cache abstraction backed by Redis (DCS) with an in-memory development fallback.

Caching is an optimisation: when Redis fails, reads behave as cache misses and
writes are skipped so the application keeps serving from RDS. Whether Redis
is *required* for readiness is controlled by ``REDIS_REQUIRED``.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from abc import ABC, abstractmethod
from typing import Any

import redis

from .config import Settings

logger = logging.getLogger("ecommerce.cache")


class CacheBackend(ABC):
    name = "abstract"

    @abstractmethod
    def get(self, key: str) -> str | None: ...

    @abstractmethod
    def set(self, key: str, value: str, ttl: int | None = None) -> None: ...

    @abstractmethod
    def delete(self, *keys: str) -> None: ...

    @abstractmethod
    def incr(self, key: str, ttl: int) -> int:
        """Increment a counter, setting ``ttl`` when it is created. Returns the new value."""

    @abstractmethod
    def zadd_and_count(self, key: str, member: str, score: float, min_score: float) -> int: ...

    @abstractmethod
    def zcount(self, key: str, min_score: float) -> int: ...

    @abstractmethod
    def ping(self) -> None: ...


class MemoryCache(CacheBackend):
    """Process-local cache. Only suitable for development and tests."""

    name = "memory"

    def __init__(self) -> None:
        self._data: dict[str, tuple[Any, float | None]] = {}
        self._lock = threading.Lock()

    def _alive(self, key: str) -> Any:
        item = self._data.get(key)
        if item is None:
            return None
        value, expires = item
        if expires is not None and expires < time.monotonic():
            self._data.pop(key, None)
            return None
        return value

    def get(self, key: str) -> str | None:
        with self._lock:
            return self._alive(key)

    def set(self, key: str, value: str, ttl: int | None = None) -> None:
        with self._lock:
            self._data[key] = (value, time.monotonic() + ttl if ttl else None)

    def delete(self, *keys: str) -> None:
        with self._lock:
            for k in keys:
                self._data.pop(k, None)

    def incr(self, key: str, ttl: int) -> int:
        with self._lock:
            current = self._alive(key)
            if current is None:
                self._data[key] = (1, time.monotonic() + ttl)
                return 1
            _, expires = self._data[key]
            self._data[key] = (int(current) + 1, expires)
            return int(current) + 1

    def zadd_and_count(self, key: str, member: str, score: float, min_score: float) -> int:
        with self._lock:
            zset = self._alive(key) or {}
            zset[member] = score
            zset = {m: s for m, s in zset.items() if s >= min_score}
            self._data[key] = (zset, None)
            return len(zset)

    def zcount(self, key: str, min_score: float) -> int:
        with self._lock:
            zset = self._alive(key) or {}
            return sum(1 for s in zset.values() if s >= min_score)

    def ping(self) -> None:
        return None


class RedisCache(CacheBackend):
    name = "redis"

    def __init__(self, settings: Settings) -> None:
        self.client = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            password=settings.redis_password or None,
            db=settings.redis_db,
            ssl=settings.redis_ssl,
            socket_timeout=settings.redis_socket_timeout,
            socket_connect_timeout=settings.redis_socket_timeout,
            health_check_interval=30,
            decode_responses=True,
            retry_on_timeout=False,
        )

    def get(self, key: str) -> str | None:
        return self.client.get(key)

    def set(self, key: str, value: str, ttl: int | None = None) -> None:
        self.client.set(key, value, ex=ttl)

    def delete(self, *keys: str) -> None:
        if keys:
            self.client.delete(*keys)

    def incr(self, key: str, ttl: int) -> int:
        pipe = self.client.pipeline()
        pipe.incr(key)
        pipe.expire(key, ttl, nx=True)
        value, _ = pipe.execute()
        return int(value)

    def zadd_and_count(self, key: str, member: str, score: float, min_score: float) -> int:
        pipe = self.client.pipeline()
        pipe.zadd(key, {member: score})
        pipe.zremrangebyscore(key, "-inf", f"({min_score}")
        pipe.expire(key, 86400)
        pipe.zcard(key)
        return int(pipe.execute()[-1])

    def zcount(self, key: str, min_score: float) -> int:
        return int(self.client.zcount(key, min_score, "+inf"))

    def ping(self) -> None:
        self.client.ping()


class CacheService:
    """Fail-soft facade used by business code."""

    def __init__(self, backend: CacheBackend, prefix: str = "ecom") -> None:
        self.backend = backend
        self.prefix = prefix
        self._last_warning = 0.0

    @property
    def backend_name(self) -> str:
        return self.backend.name

    def key(self, *parts: Any) -> str:
        return ":".join([self.prefix, *(str(p) for p in parts)])

    def _warn(self, op: str, exc: Exception) -> None:
        now = time.monotonic()
        if now - self._last_warning > 10:  # avoid flooding logs while DCS is down
            self._last_warning = now
            logger.warning("cache unavailable, degrading", extra={"op": op, "error_type": type(exc).__name__})

    def get_json(self, key: str) -> Any | None:
        try:
            raw = self.backend.get(key)
        except redis.RedisError as exc:
            self._warn("get", exc)
            return None
        return json.loads(raw) if raw else None

    def set_json(self, key: str, value: Any, ttl: int) -> None:
        try:
            self.backend.set(key, json.dumps(value, default=str), ttl)
        except redis.RedisError as exc:
            self._warn("set", exc)

    def get(self, key: str) -> str | None:
        try:
            return self.backend.get(key)
        except redis.RedisError as exc:
            self._warn("get", exc)
            return None

    def set(self, key: str, value: str, ttl: int | None = None) -> None:
        try:
            self.backend.set(key, value, ttl)
        except redis.RedisError as exc:
            self._warn("set", exc)

    def delete(self, *keys: str) -> None:
        try:
            self.backend.delete(*keys)
        except redis.RedisError as exc:
            self._warn("delete", exc)

    def incr(self, key: str, ttl: int) -> int | None:
        try:
            return self.backend.incr(key, ttl)
        except redis.RedisError as exc:
            self._warn("incr", exc)
            return None

    # ---- catalog versioning: bump to invalidate every cached product listing ----
    def catalog_version(self) -> str:
        return self.get(self.key("catalog", "version")) or "0"

    def bump_catalog_version(self) -> None:
        self.set(self.key("catalog", "version"), str(time.time_ns()))

    def invalidate_cart(self, user_id: int) -> None:
        self.delete(self.key("cart", user_id))

    # ---- active users (sliding 15 minute window) --------------------------------
    def touch_active_user(self, user_id: int) -> None:
        now = time.time()
        try:
            self.backend.zadd_and_count(self.key("active_users"), str(user_id), now, now - 900)
        except redis.RedisError as exc:
            self._warn("zadd", exc)

    def active_user_count(self) -> int:
        try:
            return self.backend.zcount(self.key("active_users"), time.time() - 900)
        except redis.RedisError as exc:
            self._warn("zcount", exc)
            return 0

    def ping(self) -> None:
        self.backend.ping()


def build_cache(settings: Settings) -> CacheService:
    if settings.redis_enabled and settings.redis_host:
        backend: CacheBackend = RedisCache(settings)
    else:
        if settings.is_production_like:
            raise RuntimeError("Redis (DCS) must be configured outside development")
        logger.info("REDIS_HOST not set, using in-memory cache (development fallback)")
        backend = MemoryCache()
    return CacheService(backend, settings.cache_prefix)
