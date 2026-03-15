"""
Multi-Sensor Synchronisation

In a real production environment, different sensors sample at
different rates and have different latencies. A pyrometer reading
a furnace temperature and a laser micrometer measuring fiber diameter
will not naturally arrive at the same time.

This module takes the raw stream of sensor readings and produces
synchronised windows — snapshots where all sensor values are
aligned to a common timestamp using nearest-neighbour interpolation.

This is essential for model training: every training sample needs
a complete feature vector where all sensor values correspond to the
same physical moment in the process.

Approach:
  - Assign each reading to a time bin of fixed width (default: 10ms)
  - Within each bin, use the most recent value for each sensor field
  - Flag bins where any critical sensor is missing
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, UTC, timedelta
from typing import Optional

from qutlas.schema import DataSource, SensorReading

logger = logging.getLogger(__name__)


@dataclass
class SyncConfig:
    """Configuration for the synchronisation layer."""
    bin_width_ms:        float = 10.0    # time bin width in milliseconds
    max_age_bins:        int   = 5       # how many bins back a value remains valid
    require_temperature: bool  = True    # treat missing temp as invalid
    require_diameter:    bool  = True    # treat missing diameter as invalid


@dataclass
class SyncedReading:
    """
    A synchronised sensor snapshot — all values aligned to a common timestamp.

    Unlike SensorReading (which represents a single sensor event),
    a SyncedReading represents the best available state of all sensors
    at a particular moment in time.
    """
    bin_timestamp:       datetime         # start of the time bin
    source:              DataSource

    # Sensor values (None if no valid reading within age window)
    furnace_temp_c:      Optional[float]
    melt_viscosity_cp:   Optional[float]
    melt_flow_rate:      Optional[float]
    fiber_diameter_um:   Optional[float]
    draw_speed_ms:       Optional[float]
    draw_tension_n:      Optional[float]
    cooling_zone_temp_c: Optional[float]
    airflow_rate_lpm:    Optional[float]

    # Quality metadata
    complete:            bool = False    # True if all required fields present
    missing_fields:      list[str] = field(default_factory=list)
    run_id:              Optional[str] = None
    sequence:            int = 0

    @classmethod
    def from_reading(cls, reading: SensorReading, bin_ts: datetime) -> "SyncedReading":
        """Create a SyncedReading directly from a single SensorReading."""
        synced = cls(
            bin_timestamp       = bin_ts,
            source              = reading.source,
            furnace_temp_c      = reading.furnace_temp_c,
            melt_viscosity_cp   = reading.melt_viscosity_cp,
            melt_flow_rate      = reading.melt_flow_rate,
            fiber_diameter_um   = reading.fiber_diameter_um,
            draw_speed_ms       = reading.draw_speed_ms,
            draw_tension_n      = reading.draw_tension_n,
            cooling_zone_temp_c = reading.cooling_zone_temp_c,
            airflow_rate_lpm    = reading.airflow_rate_lpm,
            run_id              = reading.run_id,
            sequence            = reading.sequence,
        )
        synced._compute_completeness()
        return synced

    def _compute_completeness(self) -> None:
        missing = []
        if self.furnace_temp_c is None:
            missing.append("furnace_temp_c")
        if self.fiber_diameter_um is None:
            missing.append("fiber_diameter_um")
        if self.draw_speed_ms is None:
            missing.append("draw_speed_ms")
        self.missing_fields = missing
        self.complete = len(missing) == 0

    def to_feature_vector(self) -> list[float]:
        """
        Return sensor values as a flat list for model input.
        Missing values are filled with 0.0.

        Feature order is fixed and must match the model's expected input.
        """
        return [
            self.furnace_temp_c      or 0.0,
            self.melt_viscosity_cp   or 0.0,
            self.melt_flow_rate      or 0.0,
            self.fiber_diameter_um   or 0.0,
            self.draw_speed_ms       or 0.0,
            self.draw_tension_n      or 0.0,
            self.cooling_zone_temp_c or 0.0,
            self.airflow_rate_lpm    or 0.0,
        ]

    @staticmethod
    def feature_names() -> list[str]:
        """Feature names corresponding to to_feature_vector() output."""
        return [
            "furnace_temp_c",
            "melt_viscosity_cp",
            "melt_flow_rate",
            "fiber_diameter_um",
            "draw_speed_ms",
            "draw_tension_n",
            "cooling_zone_temp_c",
            "airflow_rate_lpm",
        ]


class StreamSynchroniser:
    """
    Synchronises a stream of SensorReadings into fixed time bins.

    Maintains a sliding state of the most recent known value for
    each sensor field. On each call to sync(), produces a SyncedReading
    representing the current state of all sensors.

    Usage:
        sync = StreamSynchroniser()
        sync.update(reading)          # feed a new reading
        synced = sync.current()       # get the current synchronised snapshot
        window = sync.window(n=100)   # get the last 100 synced snapshots
    """

    def __init__(self, config: SyncConfig | None = None) -> None:
        self.config  = config or SyncConfig()
        self._state: dict[str, float | None] = {
            "furnace_temp_c":      None,
            "melt_viscosity_cp":   None,
            "melt_flow_rate":      None,
            "fiber_diameter_um":   None,
            "draw_speed_ms":       None,
            "draw_tension_n":      None,
            "cooling_zone_temp_c": None,
            "airflow_rate_lpm":    None,
        }
        self._history:  list[SyncedReading] = []
        self._source:   DataSource = DataSource.SIMULATOR
        self._run_id:   Optional[str] = None
        self._sequence: int = 0

    def update(self, reading: SensorReading) -> SyncedReading:
        """
        Incorporate a new sensor reading and return the current
        synchronised snapshot.

        Args:
            reading: incoming sensor reading

        Returns:
            SyncedReading representing all known sensor state at this moment
        """
        # Merge new values into state (only update non-None values)
        fields = [
            "furnace_temp_c", "melt_viscosity_cp", "melt_flow_rate",
            "fiber_diameter_um", "draw_speed_ms", "draw_tension_n",
            "cooling_zone_temp_c", "airflow_rate_lpm",
        ]
        for f in fields:
            val = getattr(reading, f, None)
            if val is not None:
                self._state[f] = val

        self._source   = reading.source
        self._run_id   = reading.run_id
        self._sequence = reading.sequence

        # Compute bin timestamp
        bin_ts = self._bin_timestamp(reading.timestamp)

        synced = SyncedReading(
            bin_timestamp       = bin_ts,
            source              = self._source,
            furnace_temp_c      = self._state["furnace_temp_c"],
            melt_viscosity_cp   = self._state["melt_viscosity_cp"],
            melt_flow_rate      = self._state["melt_flow_rate"],
            fiber_diameter_um   = self._state["fiber_diameter_um"],
            draw_speed_ms       = self._state["draw_speed_ms"],
            draw_tension_n      = self._state["draw_tension_n"],
            cooling_zone_temp_c = self._state["cooling_zone_temp_c"],
            airflow_rate_lpm    = self._state["airflow_rate_lpm"],
            run_id              = self._run_id,
            sequence            = self._sequence,
        )
        synced._compute_completeness()
        self._history.append(synced)
        return synced

    def current(self) -> SyncedReading | None:
        """Return the most recent synchronised reading."""
        return self._history[-1] if self._history else None

    def window(self, n: int) -> list[SyncedReading]:
        """Return the last n synchronised readings, oldest first."""
        return self._history[-n:] if n < len(self._history) else list(self._history)

    def reset(self) -> None:
        """Clear all state. Call at the start of a new production run."""
        for key in self._state:
            self._state[key] = None
        self._history.clear()
        self._sequence = 0
        logger.debug("StreamSynchroniser reset")

    def _bin_timestamp(self, ts: datetime) -> datetime:
        """Round a timestamp down to the nearest bin boundary."""
        bin_us  = int(self.config.bin_width_ms * 1000)
        epoch   = ts.timestamp()
        binned  = int(epoch * 1_000_000 / bin_us) * bin_us / 1_000_000
        return datetime.fromtimestamp(binned, tz=UTC)
