# Contributing to Qutlas

## Branching Strategy

```
main          — stable, deployable
dev           — integration branch, all PRs merge here first
feature/*     — new features (branch from dev)
fix/*         — bug fixes (branch from dev)
experiment/*  — exploratory work, not expected to merge cleanly
```

Never commit directly to `main`. All changes go through a PR against `dev`.

## Commit Convention

```
type(scope): short description

Types: feat | fix | data | model | docs | test | refactor | ci
Scope: pipeline | physics | control | api | dashboard | infra

Examples:
  feat(physics): add Avrami crystallization model
  fix(pipeline): correct timestamp alignment in multi-sensor sync
  data(training): add 50 labeled structural fiber runs
  model(inference): update property predictor to v0.3
```

## Code Standards

- Python: formatted with `ruff`, type-checked with `mypy --strict`
- All public functions and classes require docstrings
- All new modules require corresponding tests in `tests/`
- Physics constants must cite their source (paper, standard, or datasheet)

## Running Checks Locally

```bash
# Format
uv run ruff format .

# Lint
uv run ruff check .

# Type check
uv run mypy qutlas/

# Tests
uv run pytest
```

All checks must pass before a PR can be merged.

## Data and Model Artifacts

- Raw production run data lives in `data-pipeline/export/runs/` and is gitignored
- Model checkpoints live in `models/training/checkpoints/` and are gitignored
- Schemas and config are always committed
- Never commit sensor data, run exports, or model weights to the repository

## Experiment Tracking

All model training runs are tracked with MLflow. Before training:

```bash
mlflow ui --backend-store-uri ./models/training/experiments/mlruns
```

Log every experiment. Do not delete runs, even failed ones.
