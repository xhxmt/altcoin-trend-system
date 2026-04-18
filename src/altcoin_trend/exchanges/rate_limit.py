import time
from dataclasses import dataclass, field


@dataclass
class TokenBucket:
    capacity: float
    refill_per_second: float
    available: float = field(init=False)
    _updated_at: float = field(init=False)

    def __post_init__(self) -> None:
        self.available = self.capacity
        self._updated_at = time.monotonic()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._updated_at
        self._updated_at = now
        self.available = min(self.capacity, self.available + elapsed * self.refill_per_second)

    def try_acquire(self, weight: float = 1) -> bool:
        self._refill()
        if weight > self.available:
            return False
        self.available -= weight
        return True
