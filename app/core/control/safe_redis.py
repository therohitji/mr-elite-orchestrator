# app/core/control/safe_redis.py
"""
Safe Redis Client with Circuit Breaker Pattern
Production-ready: Connection pooling, fail-safe fallback, automatic reconnection
"""

import redis
from redis.exceptions import RedisError, ConnectionError
import logging
import time
from typing import Optional
import os

logger = logging.getLogger(__name__)


class SafeRedis:
    """
    Redis client with circuit breaker pattern and in-memory fallback

    Features:
    - Connection pooling for performance
    - Circuit breaker to prevent cascading failures
    - Automatic reconnection attempts
    - In-memory fallback when Redis unavailable
    """

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        db: int = 0,
        max_retries: int = 3,
        circuit_breaker_timeout: int = 60,  # seconds before retry after failure
    ):
        # Get config from env or use defaults
        self.host = host or os.getenv("REDIS_HOST", "localhost")
        self.port = port or int(os.getenv("REDIS_PORT", "6379"))
        self.db = db

        self.max_retries = max_retries
        self.circuit_breaker_timeout = circuit_breaker_timeout

        # Circuit breaker state
        self.circuit_open = False
        self.circuit_open_time = 0

        # In-memory fallback (for when Redis unavailable)
        self._memory_store: dict = {}

        # Connection pool
        self.pool = None
        self.client = None
        self.available = False

        # Initialize connection
        self._connect()

    def _connect(self):
        """Establish Redis connection with retry logic"""
        try:
            # Create connection pool (more efficient than single connection)
            self.pool = redis.ConnectionPool(
                host=self.host,
                port=self.port,
                db=self.db,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
                max_connections=50,
            )

            self.client = redis.Redis(connection_pool=self.pool)

            # Test connection
            self.client.ping()

            self.available = True
            self.circuit_open = False
            logger.info(f"[SafeRedis] Connected to Redis at {self.host}:{self.port}")

        except (RedisError, ConnectionError) as e:
            logger.warning(
                f"[SafeRedis] Redis unavailable: {e}. Using in-memory fallback."
            )
            self.client = None
            self.available = False
            self._open_circuit()

    def _open_circuit(self):
        """Open circuit breaker (stop trying to connect for a while)"""
        self.circuit_open = True
        self.circuit_open_time = time.time()
        logger.warning(
            f"[SafeRedis] Circuit breaker OPEN. Will retry after {self.circuit_breaker_timeout}s"
        )

    def _check_circuit(self):
        """Check if circuit breaker should be closed (time to retry)"""
        if self.circuit_open:
            elapsed = time.time() - self.circuit_open_time
            if elapsed > self.circuit_breaker_timeout:
                logger.info(
                    "[SafeRedis] Circuit breaker timeout expired. Attempting reconnect..."
                )
                self._connect()

    def _fallback_incr(self, key: str) -> int:
        """In-memory fallback for incr()"""
        if key not in self._memory_store:
            self._memory_store[key] = 0
        self._memory_store[key] += 1
        return self._memory_store[key]

    def _fallback_incrby(self, key: str, amount: int) -> int:
        """In-memory fallback for incrby()"""
        if key not in self._memory_store:
            self._memory_store[key] = 0
        self._memory_store[key] += amount
        return self._memory_store[key]

    def _fallback_get(self, key: str) -> Optional[str]:
        """In-memory fallback for get()"""
        return str(self._memory_store.get(key)) if key in self._memory_store else None

    def incr(self, key: str, expiry: int = 60) -> int:
        """
        Increment key with expiry (atomic operation)
        Falls back to in-memory if Redis unavailable
        """
        # Check if circuit breaker should be closed
        self._check_circuit()

        if not self.available or self.circuit_open:
            logger.debug(f"[SafeRedis] Using in-memory fallback for incr({key})")
            return self._fallback_incr(key)

        try:
            value = self.client.incr(key)
            self.client.expire(key, expiry)
            return value

        except (RedisError, ConnectionError) as e:
            logger.warning(f"[SafeRedis] incr() failed: {e}. Using fallback.")
            self._open_circuit()
            return self._fallback_incr(key)

    def incrby(self, key: str, amount: int, expiry: int = 60) -> int:
        """
        Increment key by amount with expiry (atomic operation)
        Falls back to in-memory if Redis unavailable
        """
        self._check_circuit()

        if not self.available or self.circuit_open:
            logger.debug(
                f"[SafeRedis] Using in-memory fallback for incrby({key}, {amount})"
            )
            return self._fallback_incrby(key, amount)

        try:
            value = self.client.incrby(key, amount)
            self.client.expire(key, expiry)
            return value

        except (RedisError, ConnectionError) as e:
            logger.warning(f"[SafeRedis] incrby() failed: {e}. Using fallback.")
            self._open_circuit()
            return self._fallback_incrby(key, amount)

    def get(self, key: str) -> Optional[str]:
        """
        Get value by key
        Falls back to in-memory if Redis unavailable
        """
        self._check_circuit()

        if not self.available or self.circuit_open:
            return self._fallback_get(key)

        try:
            return self.client.get(key)

        except (RedisError, ConnectionError) as e:
            logger.warning(f"[SafeRedis] get() failed: {e}. Using fallback.")
            self._open_circuit()
            return self._fallback_get(key)

    def set(self, key: str, value: str, expiry: Optional[int] = None) -> bool:
        """
        Set value by key with optional expiry
        Falls back to in-memory if Redis unavailable
        """
        self._check_circuit()

        if not self.available or self.circuit_open:
            self._memory_store[key] = value
            return True

        try:
            if expiry:
                return bool(self.client.setex(key, expiry, value))
            else:
                return bool(self.client.set(key, value))

        except (RedisError, ConnectionError) as e:
            logger.warning(f"[SafeRedis] set() failed: {e}. Using fallback.")
            self._open_circuit()
            self._memory_store[key] = value
            return True

    def close(self):
        """Close Redis connection"""
        if self.client:
            self.client.close()
        if self.pool:
            self.pool.disconnect()
        logger.info("[SafeRedis] Connection closed")


# ────────────────────── SINGLETON INSTANCE ──────────────────
_redis_instance: Optional[SafeRedis] = None


def get_redis_client() -> SafeRedis:
    """
    Get singleton Redis client instance

    Usage:
        redis = get_redis_client()
        count = redis.incr("rate_limit:send_email", expiry=60)
    """
    global _redis_instance
    if _redis_instance is None:
        _redis_instance = SafeRedis()
    return _redis_instance
