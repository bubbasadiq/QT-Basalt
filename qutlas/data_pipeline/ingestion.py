"""
Sensor Ingestion Layer

Accepts sensor readings from any source — real hardware drivers,
the process simulator, or replayed historical data — and routes
them into the ring buffer with timestamp validation and
basic quality checks.

The ingestion layer is the entry point for all data into the platform.
It is deliberately thin: validate, tag, store. No transformation here.
Transformation happens in the sync and processing layers downstream.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, UTC
from typing import Callable, Optional

from qutlas.schema import DataSource, SensorReading
from qutlas.data_pipeline.ring_buffer import RingBuffer

logger = logging.getLogger(__name__)


@dataclass
class IngestionConfig:
    """Configuration for the ingestion layer."""
    buffer_capacity:     int   = 10_000     # ring buffer size
    sample_rate_hz:      float = 100.0      # expected sample rate
    max_gap_seconds:     float = 1.0        # max tolerated gap before warning
    min_temp_c:          float = 20.0       # plausibility lower bound
    max_temp_c:          float = 1700.0     # plausibility upper bound
    min_diameter_um:     float = 1.0        # plausibility lower bound
    max_diameter_um:     float = 100.0      # plausibility upper bound
    drop_invalid:        bool  = True       # drop readings that fail validation


@dataclass
class IngestionStats:
    """Running statistics for the ingestion layer."""
    total_received:    int   = 0
    total_accepted:    int   = 0
    total_dropped:     int   = 0
    validation_errors: int   = 0
    gap_warnings:      int   = 0
    last_received_at:  Optional[datetime] = None
    started_at:        datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def acceptance_rate(self) -> float:
        if self.total_received == 0:
            return 1.0
        return self.total_received / self.total_received

    @property
    def drop_rate(self) -> float:
        if self.total_received == 0:
            return 0.0
        return self.total_dropped / self.total_received


class SensorIngestion:
    """
    Validates and stores incoming sensor readings.

    Provides a single `ingest()` method that can be called from
    hardware drivers, the simulator, or a replay system.

    Supports optional callbacks on new data — used to notify
    downstream consumers (e.g. the Materials Engine) without polling.

    Usage:
        ingestion = SensorIngestion()
        ingestion.start()

        # From hardware driver or simulator:
        ingestion.ingest(reading)

        # Read back recent data:
        last_100 = ingestion.buffer.last(100)

        ingestion.stop()
    """

    def __init__(self, config: IngestionConfig | None = None) -> None:
        self.config  = config or IngestionConfig()
        self.buffer  = RingBuffer[SensorReading](self.config.buffer_capacity)
        self.stats   = IngestionStats()
        self._lock   = threading.Lock()
        self._callbacks: list[Callable[[SensorReading], None]] = []
        self._running = False

    # ── Lifecycle ───────────────────────────────────────────────────────

    def start(self) -> None:
        """Mark the ingestion layer as active."""
        self._running = True
        logger.info(
            "Sensor ingestion started "
            f"(buffer={self.config.buffer_capacity}, "
            f"rate={self.config.sample_rate_hz}Hz)"
        )

    def stop(self) -> None:
        """Stop accepting new readings."""
        self._running = False
        logger.info(
            f"Sensor ingestion stopped. "
            f"Accepted {self.stats.total_accepted} / "
            f"{self.stats.total_received} readings."
        )

    # ── Ingestion ───────────────────────────────────────────────────────

    def ingest(self, reading: SensorReading) -> bool:
        """
        Validate and store an incoming sensor reading.

        Args:
            reading: the sensor reading to ingest

        Returns:
            True if the reading was accepted, False if dropped
        """
        with self._lock:
            self.stats.total_received += 1
            self.stats.last_received_at = datetime.now(UTC)

            # Validate
            errors = self._validate(reading)
            if errors:
                self.stats.validation_errors += len(errors)
                for err in errors:
                    logger.debug(f"Validation: {err}")
                if self.config.drop_invalid:
                    self.stats.total_dropped += 1
                    return False

            # Check for time gaps
            self._check_gap(reading)

            # Store
            self.buffer.append(reading)
            self.stats.total_accepted += 1

        # Notify callbacks outside the lock to avoid deadlocks
        for cb in self._callbacks:
            try:
                cb(reading)
            except Exception as e:
                logger.warning(f"Ingestion callback error: {e}")

        return True

    def ingest_batch(self, readings: list[SensorReading]) -> int:
        """
        Ingest a batch of readings in order.

        Args:
            readings: list of readings to ingest

        Returns:
            Number of readings accepted
        """
        accepted = sum(1 for r in readings if self.ingest(r))
        return accepted

    # ── Callbacks ───────────────────────────────────────────────────────

    def on_reading(self, callback: Callable[[SensorReading], None]) -> None:
        """
        Register a callback to be called on each accepted reading.

        Callbacks are called synchronously after ingestion.
        Keep them fast — offload heavy work to a separate thread.

        Args:
            callback: function accepting a SensorReading
        """
        self._callbacks.append(callback)

    def remove_callback(self, callback: Callable[[SensorReading], None]) -> None:
        """Remove a previously registered callback."""
        self._callbacks = [c for c in self._callbacks if c is not callback]

    # ── Queries ─────────────────────────────────────────────────────────

    def latest(self) -> SensorReading | None:
        """Return the most recent reading."""
        return self.buffer.latest()

    def last(self, n: int) -> list[SensorReading]:
        """Return the n most recent readings, oldest first."""
        return self.buffer.last(n)

    def readings_since(self, since: datetime) -> list[SensorReading]:
        """
        Return all readings with timestamps after `since`.

        Args:
            since: lower bound timestamp (exclusive)

        Returns:
            List of readings in chronological order
        """
        return [
            r for r in self.buffer.snapshot()
            if r.timestamp > since
        ]

    # ── Private ─────────────────────────────────────────────────────────

    def _validate(self, reading: SensorReading) -> list[str]:
        """
        Run plausibility checks on a reading.

        Returns a list of error messages. Empty list means valid.
        """
        errors: list[str] = []
        cfg = self.config

        if reading.furnace_temp_c is not None:
            if not (cfg.min_temp_c <= reading.furnace_temp_c <= cfg.max_temp_c):
                errors.append(
                    f"furnace_temp_c={reading.furnace_temp_c:.1f} "
                    f"outside [{cfg.min_temp_c}, {cfg.max_temp_c}]"
                )

        if reading.fiber_diameter_um is not None:
            if not (cfg.min_diameter_um <= reading.fiber_diameter_um <= cfg.max_diameter_um):
                errors.append(
                    f"fiber_diameter_um={reading.fiber_diameter_um:.1f} "
                    f"outside [{cfg.min_diameter_um}, {cfg.max_diameter_um}]"
                )

        if reading.draw_speed_ms is not None:
            if reading.draw_speed_ms < 0.0:
                errors.append(f"draw_speed_ms={reading.draw_speed_ms:.2f} is negative")

        if reading.melt_viscosity_cp is not None:
            if reading.melt_viscosity_cp <= 0.0:
                errors.append(f"melt_viscosity_cp={reading.melt_viscosity_cp:.1f} must be positive")

        return errors

    def _check_gap(self, reading: SensorReading) -> None:
        """Warn if there is an unexpected gap since the last reading."""
        last = self.buffer.latest()
        if last is None:
            return
        gap = (reading.timestamp - last.timestamp).total_seconds()
        if gap > self.config.max_gap_seconds:
            self.stats.gap_warnings += 1
            logger.warning(
                f"Sensor gap detected: {gap:.2f}s "
                f"(max={self.config.max_gap_seconds}s)"
            )
