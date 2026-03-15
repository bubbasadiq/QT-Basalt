# Qutlas

**Programmable Materials Manufacturing Infrastructure**

Qutlas is a closed-loop manufacturing platform that transforms structural materials production from a fixed industrial process into a software-defined system. The platform continuously observes the production environment, predicts material properties in real time, and adjusts manufacturing parameters to converge on defined performance targets.

The first application is programmable basalt fiber manufacturing.

---

## Repository Structure

```
qutlas/
├── hardware/               # Firmware and sensor drivers
│   ├── firmware/           # Real-time control code (C/C++)
│   └── sensor-drivers/     # Hardware abstraction for each sensor type
│
├── data-pipeline/          # Sensor ingestion, schema, buffering, export
│
├── models/                 # Physics models and ML inference
│   ├── physics/            # Deterministic process models
│   ├── inference/          # Trained property prediction models
│   └── training/           # Training data, experiments, evaluation
│
├── control/                # Adaptive control logic and recipes
│
├── platform-api/           # External REST and WebSocket API
│
├── dashboard/              # Operator and analytics interfaces
│
├── infrastructure/         # Edge and cloud deployment config
│
├── docs/                   # Technical documentation
│
└── tests/                  # Unit, integration, and simulation tests
```

---

## Getting Started

### Prerequisites

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) for Python package management
- Rust 1.75+ with Cargo (for firmware and sensor drivers)
- Docker (for local infrastructure)

### Setup

```bash
# Clone the repository
git clone https://github.com/qutlas/qutlas.git
cd qutlas

# Install Python dependencies
uv sync

# Run the process simulator
python -m simulation.run

# Run all tests
uv run pytest
```

---

## Development Phases

| Phase | Focus | Status |
|-------|-------|--------|
| 1 | Repository scaffold, data schema, physics models | In Progress |
| 2 | Process simulator, inference models, sensor drivers | Planned |
| 3 | Closed-loop control, adaptive recipes | Planned |
| 4 | Pilot manufacturing line integration | Planned |

---

## Architecture Overview

The platform operates as a five-layer closed loop:

```
Sensor Network  →  Data Layer  →  Materials Engine  →  Adaptive Control  →  Manufacturing Execution
      ↑                                                                                ↓
      └────────────────────── Feedback (param adjust) ───────────────────────────────┘
```

Each layer has a defined interface. The execution layer is application-specific. The intelligence layers above it are designed to generalise across material classes.

---

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md) for development guidelines, branching strategy, and code standards.

---

## License

Proprietary. All rights reserved. © 2026 Qutlas.
