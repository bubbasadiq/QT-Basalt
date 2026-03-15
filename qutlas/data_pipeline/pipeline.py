"""
Data Pipeline Orchestrator

Wires the ingestion, synchronisation, and export layers into
a single coherent pipeline object. This is what the rest of the
platform interacts with — not the individual layers directly.

The pipeline is the data backbone of the Qutlas platform:

  SensorReading (from hardware or simulator)
       ↓
  SensorIngestion  (validate, buffer, notify)
       ↓
  StreamSynchroniser  (align to time bins)
       ↓
  SyncedReading  (complete feature vector)
       ↓
  MaterialsEngine  (property prediction)
       ↓
  RunExporter  (on run completion → Parquet)

Usage:
    pipeline = DataPipeline()
    pipeline.start()

    # Feed readings in from simulator or hardware:
    pipeline.ingest(reading)

    # Get the current synchronised state for the control layer:
    current = pipeline.current_synced()

    # Get a window for model inference:
    window = pipeline.synced_window(n=100)

    # On run completion:
    pipeline.export_run(completed_run)

    pipeline.stop()
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Optional

from qutlas.schema import ProductionRun, SensorReading
from qutlas.data_pipeline.ingestion  import IngestionConfig, SensorIngestion
from qutlas.data_pipeline.ring_buffer import RingBuffer
from qutlas.data_pipeline.sync       import StreamSynchroniser, SyncedReading, SyncConfig
from qutlas.data_pipeline.export     import RunExporter

logger = logging.getLogger(__name__)


class DataPipeline:
    """
    End-to-end data pipeline for the Qutlas manufacturing platform.

    Provides a single interface for:
      - Feeding in sensor readings
      - Reading back synchronised data
      - Exporting completed runs

    All internal layers are accessible via attributes if fine-grained
    control is needed.
    """

    def __init__(
        self,
        ingestion_config:  IngestionConfig | None = None,
        sync_config:       SyncConfig      | None = None,
        export_root:       Path | str      | None = None,
    ) -> None:
        self.ingestion  = SensorIngestion(ingestion_config)
        self.sync       = StreamSynchroniser(sync_config)
        self.exporter   = RunExporter(export_root)

        # Wire sync as a callback on accepted ingestion readings
        self.ingestion.on_reading(self._on_reading)

        self._synced_callbacks: list[Callable[[SyncedReading], None]] = []

    # ── Lifecycle ───────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the pipeline. Must be called before ingesting data."""
        self.ingestion.start()
        logger.info("DataPipeline started")

    def stop(self) -> None:
        """Stop the pipeline gracefully."""
        self.ingestion.stop()
        logger.info(
            f"DataPipeline stopped. "
            f"Buffer: {self.ingestion.buffer.size} readings. "
            f"Dropped: {self.ingestion.stats.total_dropped}."
        )

    def reset_for_new_run(self) -> None:
        """
        Reset synchroniser state at the start of a new production run.
        Clears per-run history while preserving the ingestion buffer.
        """
        self.sync.reset()
        logger.debug("Pipeline reset for new run")

    # ── Data flow ───────────────────────────────────────────────────────

    def ingest(self, reading: SensorReading) -> bool:
        """
        Feed a sensor reading into the pipeline.

        Args:
            reading: raw sensor reading from hardware or simulator

        Returns:
            True if accepted, False if dropped by validation
        """
        return self.ingestion.ingest(reading)

    def ingest_batch(self, readings: list[SensorReading]) -> int:
        """Ingest a batch. Returns count of accepted readings."""
        return self.ingestion.ingest_batch(readings)

    # ── Queries ─────────────────────────────────────────────────────────

    def current_synced(self) -> SyncedReading | None:
        """
        Return the most recent synchronised sensor snapshot.
        This is what the control layer polls for current process state.
        """
        return self.sync.current()

    def synced_window(self, n: int) -> list[SyncedReading]:
        """
        Return the last n synchronised readings, oldest first.
        Used by the Materials Engine as its input feature window.

        Args:
            n: window size

        Returns:
            List of SyncedReadings, oldest first
        """
        return self.sync.window(n)

    def raw_window(self, n: int) -> list[SensorReading]:
        """
        Return the last n raw (unsynced) sensor readings.
        Used for debugging and diagnostics.
        """
        return self.ingestion.last(n)

    # ── Export ──────────────────────────────────────────────────────────

    def export_run(self, run: ProductionRun) -> Path:
        """
        Export a completed production run to disk.

        Args:
            run: completed ProductionRun

        Returns:
            Path to export directory
        """
        return self.exporter.export(run)

    # ── Callbacks ───────────────────────────────────────────────────────

    def on_synced(self, callback: Callable[[SyncedReading], None]) -> None:
        """
        Register a callback invoked on each new synchronised reading.

        This is how the Materials Engine subscribes to live data:
            pipeline.on_synced(materials_engine.on_new_reading)
        """
        self._synced_callbacks.append(callback)

    # ── Stats ───────────────────────────────────────────────────────────

    @property
    def stats(self) -> dict:
        """Summary statistics for monitoring and debugging."""
        s = self.ingestion.stats
        buf = self.ingestion.buffer
        return {
            "total_received":  s.total_received,
            "total_accepted":  s.total_accepted,
            "total_dropped":   s.total_dropped,
            "gap_warnings":    s.gap_warnings,
            "buffer_size":     buf.size,
            "buffer_capacity": buf.capacity,
            "buffer_full":     buf.is_full,
            "drop_count":      buf.drop_count,
        }

    # ── Private ─────────────────────────────────────────────────────────

    def _on_reading(self, reading: SensorReading) -> None:
        """Called by ingestion on each accepted reading. Updates sync state."""
        synced = self.sync.update(reading)
        for cb in self._synced_callbacks:
            try:
                cb(synced)
            except Exception as e:
                logger.warning(f"Synced callback error: {e}")
