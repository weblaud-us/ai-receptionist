"""
Circuit breakers (audit SRE-08): stop waiting on a dependency that is down.

    b = breaker("pms:api.au1.cliniko.com")
    if not b.allow(): fail fast
    ... call ...; b.success() or b.failure()

FAILURES transient failures within WINDOW_S open the circuit for OPEN_S: calls fail at once (the
voice agent then takes a message instead of making the caller sit through timeouts). After OPEN_S
one trial call goes through; success closes the circuit, failure opens it again. Per process: each
API replica / worker learns on its own, which is enough to protect the calls it is handling.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Deque, Dict, Optional

logger = logging.getLogger(__name__)

FAILURES = 5
WINDOW_S = 60.0
OPEN_S = 30.0


class CircuitBreaker:
    def __init__(self, name: str, *, failures: int = FAILURES, window_s: float = WINDOW_S,
                 open_s: float = OPEN_S) -> None:
        self.name = name
        self.failures, self.window_s, self.open_s = failures, window_s, open_s
        self._recent: Deque[float] = deque()
        self._opened_at: Optional[float] = None
        self._trial = False

    @property
    def open(self) -> bool:
        return self._opened_at is not None

    def allow(self, now: Optional[float] = None) -> bool:
        if self._opened_at is None:
            return True
        now = time.monotonic() if now is None else now
        if now - self._opened_at >= self.open_s and not self._trial:
            self._trial = True                       # half-open: one call finds out
            return True
        return False

    def success(self) -> None:
        if self._opened_at is not None:
            logger.info("Circuit %s closed again", self.name)
        self._opened_at, self._trial = None, False
        self._recent.clear()

    def failure(self, now: Optional[float] = None) -> None:
        now = time.monotonic() if now is None else now
        if self._trial:                              # the trial call failed: stay open
            self._opened_at, self._trial = now, False
            return
        self._recent.append(now)
        while self._recent and now - self._recent[0] > self.window_s:
            self._recent.popleft()
        if self._opened_at is None and len(self._recent) >= self.failures:
            self._opened_at = now
            logger.warning("Circuit %s opened after %d failures in %.0fs", self.name, len(self._recent),
                           self.window_s)


_breakers: Dict[str, CircuitBreaker] = {}


def breaker(name: str) -> CircuitBreaker:
    b = _breakers.get(name)
    if b is None:
        b = _breakers[name] = CircuitBreaker(name)
    return b


def reset() -> None:
    _breakers.clear()
