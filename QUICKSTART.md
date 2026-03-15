# Qutlas — Quickstart

Get the full platform running in under five minutes.

---

## Prerequisites

- Python 3.11+
- [uv](https://github.com/astral-sh/uv): `pip install uv`

---

## 1. Install

```bash
git clone https://github.com/qutlas/qutlas.git
cd qutlas
uv sync
```

---

## 2. Run the MVP Simulation

Runs all five fiber recipes through the full closed-loop stack
and prints a comparison report.

```bash
python scripts/run_mvp.py --all
```

Single recipe, more steps:
```bash
python scripts/run_mvp.py --recipe high_temperature --steps 1000
```

---

## 3. Start the Platform API

```bash
uvicorn qutlas.platform_api.app:app --reload --port 8000
```

Then in a second terminal, start a run:
```bash
curl -X POST http://localhost:8000/runs/start \
  -H "Content-Type: application/json" \
  -d '{"recipe_name": "structural"}'
```

Check live sensor data:
```bash
curl http://localhost:8000/sensors/latest
curl http://localhost:8000/predictions/latest
curl http://localhost:8000/status
```

---

## 4. Launch the Operator Dashboard

With the API running in one terminal:
```bash
python -m qutlas.dashboard.operator
```

Live terminal UI showing sensor readings, property predictions,
controller state, and pipeline metrics.

---

## 5. Generate a Training Dataset

```bash
python scripts/generate_dataset.py --quick
# 50 runs, ~30 seconds, saved to models/training/datasets/sim_v1.json

python scripts/generate_dataset.py --runs 500
# Full dataset for Phase 2 model training
```

---

## 6. Using the CLI

```bash
uv run qutlas --help

uv run qutlas simulate --all
uv run qutlas simulate --recipe structural --steps 800
uv run qutlas api
uv run qutlas dashboard
uv run qutlas recipes
uv run qutlas generate-dataset --quick
```

---

## 7. Run Tests

```bash
uv run pytest                          # all tests
uv run pytest tests/unit/              # unit tests only
uv run pytest tests/integration/      # integration tests only
uv run pytest -v --tb=short            # verbose output
```

---

## API Reference (summary)

| Method | Endpoint              | Description                    |
|--------|-----------------------|--------------------------------|
| GET    | /health               | Liveness check                 |
| GET    | /status               | Full platform status           |
| GET    | /recipes              | List available recipes         |
| GET    | /recipes/{name}       | Recipe detail                  |
| POST   | /runs/start           | Start a production run         |
| POST   | /runs/stop            | Stop the active run            |
| GET    | /runs/current         | Current run status             |
| GET    | /predictions/latest   | Latest property prediction     |
| GET    | /sensors/latest       | Latest sensor reading          |
| WS     | /ws/telemetry         | Live sensor + prediction stream|

Interactive docs at: `http://localhost:8000/docs`

---

## Platform Stack (Phase 1)

```
scripts/run_mvp.py
       │
       ├── ProcessSimulator      (furnace + draw physics)
       │       ↓
       ├── DataPipeline          (ingestion, ring buffer, sync)
       │       ↓
       ├── MaterialsEngine       (feature engineering, prediction)
       │       ↓
       ├── AdaptiveController    (state machine, multi-variable control)
       │       ↓
       └── RunExporter           (Parquet to disk)
```

All components are independently testable and replaceable.
The simulation layer is a drop-in replacement for real hardware —
the platform layer above it does not know the difference.
