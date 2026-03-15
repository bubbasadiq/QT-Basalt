"""
Tests for the data pipeline layers.
"""

from __future__ import annotations

from datetime import datetime, UTC, timedelta
from unittest.mock import MagicMock

import pytest

from qutlas.schema import DataSource, SensorReading
from qutlas.data_pipeline.ring_buffer import RingBuffer
from qutlas.data_pipeline.ingestion   import IngestionConfig, SensorIngestion
from qutlas.data_pipeline.sync        import StreamSynchroniser
from qutlas.data_pipeline.pipeline    import DataPipeline


# ── Helpers ──────────────────────────────────────────────────────────────────

def make_reading(
    temp: float = 1480.0,
    diam: float = 13.0,
    speed: float = 12.0,
    seq: int = 0,
    dt: datetime | None = None,
) -> SensorReading:
    return SensorReading(
        timestamp          = dt or datetime.now(UTC),
        source             = DataSource.SIMULATOR,
        furnace_temp_c     = temp,
        melt_viscosity_cp  = 750.0,
        melt_flow_rate     = 120.0,
        fiber_diameter_um  = diam,
        draw_speed_ms      = speed,
        draw_tension_n     = 0.05,
        cooling_zone_temp_c= 520.0,
        airflow_rate_lpm   = 50.0,
        sequence           = seq,
    )


# ── RingBuffer ────────────────────────────────────────────────────────────────

class TestRingBuffer:

    def test_append_and_size(self) -> None:
        buf = RingBuffer[int](capacity=10)
        buf.append(1)
        buf.append(2)
        assert buf.size == 2

    def test_capacity_is_enforced(self) -> None:
        buf = RingBuffer[int](capacity=3)
        for i in range(10):
            buf.append(i)
        assert buf.size == 3

    def test_oldest_item_is_dropped_first(self) -> None:
        buf = RingBuffer[int](capacity=3)
        buf.extend([1, 2, 3, 4, 5])
        assert list(buf) == [3, 4, 5]

    def test_last_returns_n_most_recent(self) -> None:
        buf = RingBuffer[int](capacity=100)
        buf.extend(list(range(20)))
        last5 = buf.last(5)
        assert last5 == [15, 16, 17, 18, 19]

    def test_last_returns_all_when_n_exceeds_size(self) -> None:
        buf = RingBuffer[int](capacity=10)
        buf.extend([1, 2, 3])
        assert buf.last(100) == [1, 2, 3]

    def test_latest_returns_most_recent(self) -> None:
        buf = RingBuffer[int](capacity=10)
        buf.extend([10, 20, 30])
        assert buf.latest() == 30

    def test_latest_returns_none_when_empty(self) -> None:
        buf = RingBuffer[int](capacity=10)
        assert buf.latest() is None

    def test_is_full_flag(self) -> None:
        buf = RingBuffer[int](capacity=3)
        assert not buf.is_full
        buf.extend([1, 2, 3])
        assert buf.is_full

    def test_is_empty_flag(self) -> None:
        buf = RingBuffer[int](capacity=10)
        assert buf.is_empty
        buf.append(1)
        assert not buf.is_empty

    def test_total_written_counts_all_appends(self) -> None:
        buf = RingBuffer[int](capacity=3)
        for i in range(10):
            buf.append(i)
        assert buf.total_written == 10

    def test_drop_count(self) -> None:
        buf = RingBuffer[int](capacity=3)
        buf.extend([1, 2, 3, 4, 5])
        assert buf.drop_count == 2

    def test_clear_empties_buffer(self) -> None:
        buf = RingBuffer[int](capacity=10)
        buf.extend([1, 2, 3])
        buf.clear()
        assert buf.is_empty

    def test_invalid_capacity_raises(self) -> None:
        with pytest.raises(ValueError):
            RingBuffer[int](capacity=0)

    def test_thread_safety(self) -> None:
        """Concurrent appends should not corrupt the buffer."""
        import threading
        buf = RingBuffer[int](capacity=1000)
        threads = [
            threading.Thread(target=lambda: [buf.append(i) for i in range(100)])
            for _ in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert buf.total_written == 1000


# ── SensorIngestion ───────────────────────────────────────────────────────────

class TestSensorIngestion:

    def setup_method(self) -> None:
        self.ingestion = SensorIngestion()
        self.ingestion.start()

    def test_valid_reading_is_accepted(self) -> None:
        r = make_reading()
        assert self.ingestion.ingest(r) is True
        assert self.ingestion.stats.total_accepted == 1

    def test_invalid_temp_is_dropped(self) -> None:
        r = make_reading(temp=3000.0)   # above max
        assert self.ingestion.ingest(r) is False
        assert self.ingestion.stats.total_dropped == 1

    def test_negative_speed_is_dropped(self) -> None:
        r = make_reading(speed=-5.0)
        assert self.ingestion.ingest(r) is False

    def test_callback_is_called_on_accepted_reading(self) -> None:
        callback = MagicMock()
        self.ingestion.on_reading(callback)
        self.ingestion.ingest(make_reading())
        callback.assert_called_once()

    def test_callback_not_called_on_dropped_reading(self) -> None:
        callback = MagicMock()
        self.ingestion.on_reading(callback)
        self.ingestion.ingest(make_reading(temp=9999.0))
        callback.assert_not_called()

    def test_gap_warning_on_large_time_gap(self) -> None:
        t1 = datetime.now(UTC)
        t2 = t1 + timedelta(seconds=5)   # 5s gap > default 1s max
        self.ingestion.ingest(make_reading(dt=t1))
        self.ingestion.ingest(make_reading(dt=t2))
        assert self.ingestion.stats.gap_warnings >= 1

    def test_batch_ingestion(self) -> None:
        readings = [make_reading(seq=i) for i in range(10)]
        accepted = self.ingestion.ingest_batch(readings)
        assert accepted == 10

    def test_readings_since_filters_correctly(self) -> None:
        t_base = datetime.now(UTC)
        for i in range(5):
            self.ingestion.ingest(make_reading(
                dt=t_base + timedelta(seconds=i)
            ))
        cutoff = t_base + timedelta(seconds=2)
        after  = self.ingestion.readings_since(cutoff)
        assert len(after) == 2   # readings at t+3 and t+4


# ── StreamSynchroniser ───────────────────────────────────────────────────────

class TestStreamSynchroniser:

    def setup_method(self) -> None:
        self.sync = StreamSynchroniser()

    def test_update_returns_synced_reading(self) -> None:
        synced = self.sync.update(make_reading())
        assert synced is not None
        assert synced.furnace_temp_c == pytest.approx(1480.0)

    def test_state_merges_across_readings(self) -> None:
        r1 = make_reading(temp=1480.0, diam=13.0)
        r2 = make_reading(temp=1490.0)
        r2.fiber_diameter_um = None   # no diameter in second reading

        self.sync.update(r1)
        synced = self.sync.update(r2)

        # Should have updated temperature but retained previous diameter
        assert synced.furnace_temp_c   == pytest.approx(1490.0)
        assert synced.fiber_diameter_um == pytest.approx(13.0)

    def test_complete_flag_set_correctly(self) -> None:
        synced = self.sync.update(make_reading())
        assert synced.complete is True

    def test_incomplete_flag_when_missing_critical_field(self) -> None:
        r = make_reading()
        r.fiber_diameter_um = None
        synced = self.sync.update(r)
        assert synced.complete is False
        assert "fiber_diameter_um" in synced.missing_fields

    def test_feature_vector_length(self) -> None:
        synced = self.sync.update(make_reading())
        fv = synced.to_feature_vector()
        assert len(fv) == len(synced.feature_names())

    def test_window_returns_correct_count(self) -> None:
        for i in range(20):
            self.sync.update(make_reading(seq=i))
        window = self.sync.window(10)
        assert len(window) == 10

    def test_reset_clears_history(self) -> None:
        for i in range(5):
            self.sync.update(make_reading())
        self.sync.reset()
        assert self.sync.current() is None


# ── DataPipeline (integration) ───────────────────────────────────────────────

class TestDataPipeline:

    def setup_method(self) -> None:
        self.pipeline = DataPipeline()
        self.pipeline.start()

    def teardown_method(self) -> None:
        self.pipeline.stop()

    def test_ingest_and_current_synced(self) -> None:
        self.pipeline.ingest(make_reading())
        current = self.pipeline.current_synced()
        assert current is not None
        assert current.furnace_temp_c == pytest.approx(1480.0)

    def test_synced_window_grows_with_readings(self) -> None:
        for i in range(15):
            self.pipeline.ingest(make_reading(seq=i))
        window = self.pipeline.synced_window(10)
        assert len(window) == 10

    def test_on_synced_callback_fires(self) -> None:
        received = []
        self.pipeline.on_synced(received.append)
        self.pipeline.ingest(make_reading())
        assert len(received) == 1

    def test_stats_are_populated(self) -> None:
        for i in range(5):
            self.pipeline.ingest(make_reading())
        stats = self.pipeline.stats
        assert stats["total_accepted"] == 5
        assert stats["buffer_size"]    == 5

    def test_full_pipeline_with_simulator(self) -> None:
        """Integration test: simulator → pipeline → synced window."""
        from qutlas.simulation.process import ProcessSimulator
        from qutlas.simulation.runner  import DEFAULT_RECIPES

        sim    = ProcessSimulator(noise_level=0.01)
        recipe = DEFAULT_RECIPES["structural"]
        sim.start_run(recipe)

        for _ in range(50):
            reading = sim.step()
            self.pipeline.ingest(reading)

        window = self.pipeline.synced_window(50)
        assert len(window) == 50
        assert all(w.complete for w in window)
