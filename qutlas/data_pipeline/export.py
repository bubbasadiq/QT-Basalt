"""
Run Export Layer

Serialises completed ProductionRun records to disk in a format
suitable for model training and analysis.

Export format: Apache Parquet
  - Efficient columnar storage
  - Preserves types without schema drift
  - Compatible with pandas, PyArrow, and most ML frameworks
  - Supports predicate pushdown for efficient querying

Each completed run is exported as two files:
  - {run_id}_readings.parquet  — timestep-level sensor data
  - {run_id}_meta.json         — run metadata and material outcomes

The export directory structure:
  data-pipeline/export/runs/
    {YYYY-MM-DD}/
      {run_id}_readings.parquet
      {run_id}_meta.json
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

from qutlas.schema import ProductionRun, SensorReading

logger = logging.getLogger(__name__)

# Default export root — can be overridden via PIPELINE_EXPORT_PATH env var
DEFAULT_EXPORT_ROOT = Path("data-pipeline/export/runs")


class RunExporter:
    """
    Exports completed ProductionRun records to disk.

    Uses PyArrow for Parquet serialisation when available,
    falls back to JSON when PyArrow is not installed.

    Usage:
        exporter = RunExporter()
        exporter.export(completed_run)

        # List exported runs
        runs = exporter.list_runs()
    """

    def __init__(self, export_root: Path | str | None = None) -> None:
        self.export_root = Path(export_root or DEFAULT_EXPORT_ROOT)

    def export(self, run: ProductionRun) -> Path:
        """
        Export a completed production run to disk.

        Args:
            run: completed ProductionRun (status should be COMPLETE)

        Returns:
            Path to the directory containing the exported files
        """
        run_dir = self._run_dir(run)
        run_dir.mkdir(parents=True, exist_ok=True)

        # Export sensor readings
        readings_path = self._export_readings(run, run_dir)

        # Export metadata and outcomes
        meta_path = self._export_meta(run, run_dir)

        logger.info(
            f"Exported run {run.run_id[:8]}... "
            f"({run.timestep_count} timesteps) → {run_dir}"
        )
        return run_dir

    def export_batch(self, runs: list[ProductionRun]) -> list[Path]:
        """Export multiple runs. Returns list of export directories."""
        return [self.export(run) for run in runs]

    def list_runs(self) -> list[dict[str, Any]]:
        """
        List all exported runs by reading their metadata files.

        Returns:
            List of run metadata dicts, sorted by started_at descending
        """
        meta_files = sorted(self.export_root.rglob("*_meta.json"))
        runs = []
        for f in meta_files:
            try:
                with open(f) as fh:
                    runs.append(json.load(fh))
            except Exception as e:
                logger.warning(f"Could not read {f}: {e}")
        return sorted(runs, key=lambda r: r.get("started_at", ""), reverse=True)

    def load_readings(self, run_id: str) -> list[dict[str, Any]]:
        """
        Load sensor readings for a specific run.

        Returns a list of dicts (one per timestep).
        Tries Parquet first, falls back to JSON.
        """
        # Find the run directory
        candidates = list(self.export_root.rglob(f"{run_id}_readings.*"))
        if not candidates:
            raise FileNotFoundError(f"No readings found for run {run_id}")

        path = candidates[0]

        if path.suffix == ".parquet":
            return self._load_parquet(path)
        else:
            with open(path) as f:
                return json.load(f)

    # ── Private ─────────────────────────────────────────────────────────

    def _run_dir(self, run: ProductionRun) -> Path:
        """Determine the export directory for a run (partitioned by date)."""
        date_str = (run.started_at or datetime.now(UTC)).strftime("%Y-%m-%d")
        return self.export_root / date_str / run.run_id[:8]

    def _export_readings(self, run: ProductionRun, run_dir: Path) -> Path:
        """Export timestep sensor readings. Returns path to written file."""
        rows = [self._reading_to_dict(r) for r in run.readings]

        # Try Parquet first
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq

            table = pa.Table.from_pylist(rows)
            path  = run_dir / f"{run.run_id}_readings.parquet"
            pq.write_table(table, path, compression="snappy")
            logger.debug(f"Wrote {len(rows)} readings to Parquet: {path}")
            return path

        except ImportError:
            logger.debug("PyArrow not available, falling back to JSON")
            path = run_dir / f"{run.run_id}_readings.json"
            with open(path, "w") as f:
                json.dump(rows, f, indent=None, default=str)
            return path

    def _export_meta(self, run: ProductionRun, run_dir: Path) -> Path:
        """Export run metadata and material outcomes to JSON."""
        meta: dict[str, Any] = {
            "run_id":             run.run_id,
            "status":             run.status.value,
            "source":             run.source.value,
            "recipe":             run.recipe.name if run.recipe else None,
            "fiber_class":        run.recipe.fiber_class.value if run.recipe else None,
            "started_at":         run.started_at.isoformat() if run.started_at else None,
            "completed_at":       run.completed_at.isoformat() if run.completed_at else None,
            "duration_seconds":   run.duration_seconds,
            "timestep_count":     run.timestep_count,
            "notes":              run.notes,

            # Material outcomes from physical testing
            "outcome": {
                "tensile_gpa":   run.outcome_tensile_gpa,
                "modulus_gpa":   run.outcome_modulus_gpa,
                "diameter_cv":   run.outcome_diameter_cv,
                "thermal_c":     run.outcome_thermal_c,
            },

            # Recipe targets for comparison
            "targets": {
                "tensile_gpa":   run.recipe.target_tensile_gpa if run.recipe else None,
                "modulus_gpa":   run.recipe.target_modulus_gpa if run.recipe else None,
                "diameter_um":   run.recipe.target_diameter_um if run.recipe else None,
                "thermal_c":     run.recipe.target_thermal_c   if run.recipe else None,
            } if run.recipe else {},
        }

        path = run_dir / f"{run.run_id}_meta.json"
        with open(path, "w") as f:
            json.dump(meta, f, indent=2)
        return path

    @staticmethod
    def _reading_to_dict(r: SensorReading) -> dict[str, Any]:
        """Flatten a SensorReading to a JSON-serialisable dict."""
        return {
            "timestamp":           r.timestamp.isoformat(),
            "source":              r.source.value,
            "sequence":            r.sequence,
            "run_id":              r.run_id,
            "furnace_temp_c":      r.furnace_temp_c,
            "melt_viscosity_cp":   r.melt_viscosity_cp,
            "melt_flow_rate":      r.melt_flow_rate,
            "fiber_diameter_um":   r.fiber_diameter_um,
            "draw_speed_ms":       r.draw_speed_ms,
            "draw_tension_n":      r.draw_tension_n,
            "cooling_zone_temp_c": r.cooling_zone_temp_c,
            "airflow_rate_lpm":    r.airflow_rate_lpm,
        }

    @staticmethod
    def _load_parquet(path: Path) -> list[dict[str, Any]]:
        import pyarrow.parquet as pq
        table = pq.read_table(path)
        return table.to_pylist()
