from collections.abc import Callable
from dataclasses import dataclass, field
from time import monotonic


@dataclass
class _Bucket:
    tokens: float
    updated: float


@dataclass
class TokenBucketLimiter:
    """In-process token bucket per client key. Good for one replica; see README for scale-out."""

    per_minute: int
    clock: Callable[[], float] = monotonic
    _buckets: dict[str, _Bucket] = field(default_factory=dict)

    def hit(self, key: str) -> tuple[bool, float]:
        """Consume one token. Returns (allowed, seconds_until_next_token_when_denied)."""
        capacity = float(self.per_minute)
        refill_per_second = capacity / 60.0
        now = self.clock()
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = _Bucket(tokens=capacity, updated=now)
            self._buckets[key] = bucket
        bucket.tokens = min(capacity, bucket.tokens + (now - bucket.updated) * refill_per_second)
        bucket.updated = now
        if bucket.tokens >= 1.0:
            bucket.tokens -= 1.0
            return True, 0.0
        return False, (1.0 - bucket.tokens) / refill_per_second
