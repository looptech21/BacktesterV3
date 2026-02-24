"""Order queue with t+1 leakage guard.

Ported from BacktesterV2/execution/order_queue.py.
Runs inside the LEAN Docker container alongside QM_MVP.py.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from datetime import datetime

from backtester.models import OrderIntent


@dataclass(order=True)
class _QueueItem:
    earliest_exec_ts_utc: datetime
    sequence: int
    intent: OrderIntent = field(compare=False)


class OrderQueue:
    def __init__(self) -> None:
        self._heap: list[_QueueItem] = []
        self._seq = 0

    def enqueue(self, intent: OrderIntent) -> None:
        if intent.earliest_exec_ts_utc <= intent.signal_ts_utc:
            raise ValueError(
                "Timestamp leakage guard violated: earliest_exec_ts_utc must be strictly after signal_ts_utc"
            )
        self._seq += 1
        heapq.heappush(
            self._heap,
            _QueueItem(
                earliest_exec_ts_utc=intent.earliest_exec_ts_utc,
                sequence=self._seq,
                intent=intent,
            ),
        )

    def pop_ready(self, now_utc: datetime) -> list[OrderIntent]:
        ready: list[OrderIntent] = []
        while self._heap and self._heap[0].earliest_exec_ts_utc <= now_utc:
            ready.append(heapq.heappop(self._heap).intent)
        return ready

    def __len__(self) -> int:
        return len(self._heap)
