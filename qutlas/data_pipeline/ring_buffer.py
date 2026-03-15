"""
Ring Buffer

A fixed-size circular buffer for storing sensor readings.
New readings overwrite the oldest when the buffer is full.

This is the in-memory store that sits between raw sensor ingestion
and the models. It provides the sliding window of recent process
history that the Materials Engine needs for property prediction.

Design goals:
  - O(1) append and read
  - Zero allocation after initialisation
  - Thread-safe for single-writer / multiple-reader use
  - Efficient windowed access (last N readings)
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Generic, Iterator, TypeVar

T = TypeVar("T")


class RingBuffer(Generic[T]):
    """
    Fixed-capacity circular buffer.

    When capacity is reached, the oldest item is silently dropped
    to make room for the new one. This is the correct behaviour
    for a streaming sensor data buffer — we always want the most
    recent window, not backpressure.

    Usage:
        buf = RingBuffer[SensorReading](capacity=10_000)
        buf.append(reading)
        last_100 = buf.last(100)
        all_readings = list(buf)
    """

    def __init__(self, capacity: int) -> None:
        if capacity < 1:
            raise ValueError(f"Capacity must be at least 1, got {capacity}")
        self._capacity = capacity
        self._data: deque[T] = deque(maxlen=capacity)
        self._lock = threading.RLock()
        self._total_written: int = 0   # total items ever appended

    # ── Write ───────────────────────────────────────────────────────────

    def append(self, item: T) -> None:
        """
        Append an item. If the buffer is full, the oldest item is dropped.

        Thread-safe.
        """
        with self._lock:
            self._data.append(item)
            self._total_written += 1

    def extend(self, items: list[T]) -> None:
        """Append multiple items in order. Thread-safe."""
        with self._lock:
            for item in items:
                self._data.append(item)
                self._total_written += 1

    def clear(self) -> None:
        """Remove all items. Thread-safe."""
        with self._lock:
            self._data.clear()

    # ── Read ────────────────────────────────────────────────────────────

    def last(self, n: int) -> list[T]:
        """
        Return the most recent n items, oldest first.

        If n > len(self), returns all available items.
        Thread-safe — returns a snapshot copy.

        Args:
            n: number of items to return

        Returns:
            List of up to n items, oldest first
        """
        with self._lock:
            available = list(self._data)
            return available[-n:] if n < len(available) else available

    def latest(self) -> T | None:
        """Return the most recently appended item, or None if empty."""
        with self._lock:
            return self._data[-1] if self._data else None

    def oldest(self) -> T | None:
        """Return the oldest item in the buffer, or None if empty."""
        with self._lock:
            return self._data[0] if self._data else None

    def snapshot(self) -> list[T]:
        """Return a copy of all current items, oldest first. Thread-safe."""
        with self._lock:
            return list(self._data)

    def window(self, start: int, end: int) -> list[T]:
        """
        Return items by index slice [start:end].

        Indices are relative to the current buffer contents, not total
        items written. Use last() for most recent N pattern.

        Args:
            start: inclusive start index
            end:   exclusive end index

        Returns:
            List of items in range
        """
        with self._lock:
            data = list(self._data)
            return data[start:end]

    # ── Properties ──────────────────────────────────────────────────────

    @property
    def capacity(self) -> int:
        """Maximum number of items the buffer can hold."""
        return self._capacity

    @property
    def size(self) -> int:
        """Current number of items in the buffer."""
        with self._lock:
            return len(self._data)

    @property
    def is_full(self) -> bool:
        """True when the buffer has reached capacity."""
        with self._lock:
            return len(self._data) == self._capacity

    @property
    def is_empty(self) -> bool:
        """True when the buffer contains no items."""
        with self._lock:
            return len(self._data) == 0

    @property
    def total_written(self) -> int:
        """
        Total number of items ever appended, including dropped items.
        Use this to detect data loss: if total_written > capacity,
        some items have been overwritten.
        """
        return self._total_written

    @property
    def drop_count(self) -> int:
        """Number of items dropped due to buffer overflow."""
        return max(0, self._total_written - self._capacity)

    # ── Iteration ───────────────────────────────────────────────────────

    def __iter__(self) -> Iterator[T]:
        """Iterate over all items, oldest first. Returns a snapshot."""
        return iter(self.snapshot())

    def __len__(self) -> int:
        return self.size

    def __repr__(self) -> str:
        return (
            f"RingBuffer(capacity={self._capacity}, "
            f"size={self.size}, "
            f"total_written={self._total_written})"
        )
