import time
import math
from dataclasses import dataclass, field


@dataclass
class TokenBucket:
    capacity: float
    refill_per_second: float
    available: float = field(init=False)
    _updated_at: float = field(init=False)

    def __post_init__(self) -> None:
        if not math.isfinite(self.capacity):
            raise ValueError("capacity must be finite")
        if not math.isfinite(self.refill_per_second):
            raise ValueError("refill_per_second must be finite")
        if self.capacity <= 0:
            raise ValueError("capacity must be > 0")
        if self.refill_per_second < 0:
            raise ValueError("refill_per_second must be >= 0")
        self.available = self.capacity
        self._updated_at = time.monotonic()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._updated_at
        self._updated_at = now
        self.available = min(self.capacity, self.available + elapsed * self.refill_per_second)

    def try_acquire(self, weight: float = 1) -> bool:
        if not math.isfinite(weight):
            raise ValueError("weight must be finite")
        if weight <= 0:
            raise ValueError("weight must be > 0")
        self._refill()
        if weight > self.available:
            return False
        self.available -= weight
        return True
