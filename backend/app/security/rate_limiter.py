import time
from collections import defaultdict
from threading import Lock

class SimpleRateLimiter:
    def __init__(self, max_attempts: int = 5, window_seconds: int = 60):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._attempts = defaultdict(list)
        self._lock = Lock()

    def is_rate_limited(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            timestamps = [t for t in self._attempts[key] if now - t < self.window_seconds]
            self._attempts[key] = timestamps
            return len(timestamps) >= self.max_attempts

    def record_attempt(self, key: str) -> None:
        now = time.time()
        with self._lock:
            self._attempts[key].append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._attempts.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._attempts.clear()

auth_rate_limiter = SimpleRateLimiter(max_attempts=5, window_seconds=60)