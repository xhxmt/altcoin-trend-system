from altcoin_trend.exchanges.rate_limit import TokenBucket


def test_token_bucket_rejects_invalid_configuration():
    for kwargs in (
        {"capacity": 0, "refill_per_second": 1},
        {"capacity": -1, "refill_per_second": 1},
        {"capacity": 1, "refill_per_second": -1},
        {"capacity": float("nan"), "refill_per_second": 1},
        {"capacity": 1, "refill_per_second": float("inf")},
    ):
        try:
            TokenBucket(**kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for {kwargs}")


def test_token_bucket_allows_within_capacity():
    bucket = TokenBucket(capacity=10, refill_per_second=1)

    assert bucket.try_acquire(4) is True
    assert bucket.available == 6


def test_token_bucket_rejects_over_budget_without_sleeping():
    bucket = TokenBucket(capacity=3, refill_per_second=1)

    assert bucket.try_acquire(4) is False
    assert bucket.available == 3


def test_token_bucket_rejects_non_positive_weights():
    bucket = TokenBucket(capacity=3, refill_per_second=1)

    for weight in (0, -1, float("nan"), float("inf")):
        try:
            bucket.try_acquire(weight)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for weight={weight}")


def test_token_bucket_refills_over_time(monkeypatch):
    from altcoin_trend.exchanges import rate_limit

    clock = iter([100.0, 100.0, 100.12])
    monkeypatch.setattr(rate_limit.time, "monotonic", lambda: next(clock))

    bucket = TokenBucket(capacity=3, refill_per_second=10)
    assert bucket.try_acquire(3) is True

    assert bucket.try_acquire(1) is True
