import time
import uuid
from collections import defaultdict
from threading import Lock

from app.config import settings


class SimpleRateLimiter:
    """Sliding-window limiter backed by Redis when configured, process memory otherwise."""

    def __init__(
        self,
        max_attempts: int = 5,
        window_seconds: int = 60,
        redis_url: str | None = None,
    ):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._attempts = defaultdict(list)
        self._lock = Lock()
        self._redis = None
        self._redis_prefix = "finmate:auth-rate-limit"
        if redis_url:
            try:
                from redis import Redis
            except ImportError as exc:
                raise RuntimeError("Redis is required when AUTH_RATE_LIMIT_REDIS_URL is configured") from exc
            self._redis = Redis.from_url(redis_url, decode_responses=True)
            self._redis.ping()

    def is_rate_limited(self, key: str) -> bool:
        now = time.time()
        if self._redis is not None:
            redis_key = f"{self._redis_prefix}:{key}"
            self._redis.zremrangebyscore(redis_key, "-inf", now - self.window_seconds)
            return self._redis.zcard(redis_key) >= self.max_attempts

        with self._lock:
            timestamps = [t for t in self._attempts[key] if now - t < self.window_seconds]
            self._attempts[key] = timestamps
            return len(timestamps) >= self.max_attempts

    def record_attempt(self, key: str) -> None:
        now = time.time()
        if self._redis is not None:
            redis_key = f"{self._redis_prefix}:{key}"
            pipe = self._redis.pipeline()
            pipe.zadd(redis_key, {f"{now:.9f}:{uuid.uuid4().hex}": now})
            pipe.zremrangebyscore(redis_key, "-inf", now - self.window_seconds)
            pipe.expire(redis_key, self.window_seconds * 2)
            pipe.execute()
            return

        with self._lock:
            self._attempts[key].append(now)

    def reset(self, key: str) -> None:
        if self._redis is not None:
            self._redis.delete(f"{self._redis_prefix}:{key}")
            return
        with self._lock:
            self._attempts.pop(key, None)

    def clear(self) -> None:
        if self._redis is not None:
            keys = list(self._redis.scan_iter(match=f"{self._redis_prefix}:*"))
            if keys:
                self._redis.delete(*keys)
            return
        with self._lock:
            self._attempts.clear()


auth_rate_limiter = SimpleRateLimiter(
    max_attempts=settings.auth_rate_limit_max_attempts,
    window_seconds=settings.auth_rate_limit_window_seconds,
    redis_url=settings.auth_rate_limit_redis_url,
)
