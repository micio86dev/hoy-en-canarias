from hoy_api.ratelimit import TokenBucketLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_bucket_allows_a_burst_then_rejects() -> None:
    clock = FakeClock()
    limiter = TokenBucketLimiter(per_minute=3, clock=clock)

    results = [limiter.hit("203.0.113.7") for _ in range(4)]

    assert [allowed for allowed, _ in results] == [True, True, True, False]
    assert results[-1][1] > 0  # seconds until the next token


def test_bucket_refills_over_time() -> None:
    clock = FakeClock()
    limiter = TokenBucketLimiter(per_minute=60, clock=clock)
    for _ in range(60):
        limiter.hit("203.0.113.7")
    assert limiter.hit("203.0.113.7")[0] is False

    clock.now += 1.0  # one token per second at 60/min

    assert limiter.hit("203.0.113.7")[0] is True


def test_buckets_are_per_client() -> None:
    clock = FakeClock()
    limiter = TokenBucketLimiter(per_minute=1, clock=clock)

    assert limiter.hit("198.51.100.1")[0] is True
    assert limiter.hit("198.51.100.2")[0] is True
    assert limiter.hit("198.51.100.1")[0] is False
