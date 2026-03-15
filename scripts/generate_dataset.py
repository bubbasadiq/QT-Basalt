#!/usr/bin/env python3
"""
Generate Training Dataset

Runs the process simulator across a range of conditions
to build a labeled dataset for model training.

Usage:
    python scripts/generate_dataset.py
    python scripts/generate_dataset.py --runs 500 --output models/training/datasets/sim_v1.json
    python scripts/generate_dataset.py --runs 100 --quick
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console  import Console
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn

from qutlas.models.training import SimulatorDataGenerator

console = Console()


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Qutlas training dataset")
    parser.add_argument("--runs",   type=int, default=200,
                        help="Number of simulation runs (default: 200)")
    parser.add_argument("--steps",  type=int, default=400,
                        help="Steps per run (default: 400)")
    parser.add_argument("--output", default="models/training/datasets/sim_v1.json",
                        help="Output file path")
    parser.add_argument("--quick",  action="store_true",
                        help="Quick mode: 50 runs, 200 steps")
    args = parser.parse_args()

    if args.quick:
        args.runs  = 50
        args.steps = 200

    output = Path(args.output)

    console.print()
    console.print("[bold]Qutlas[/bold] · Training Dataset Generator")
    console.print(f"  Runs    : [cyan]{args.runs}[/cyan]")
    console.print(f"  Steps   : [cyan]{args.steps}[/cyan] per run")
    console.print(f"  Output  : [cyan]{output}[/cyan]")
    console.print()

    gen   = SimulatorDataGenerator(steps_per_run=args.steps, dt=0.1)
    t0    = time.monotonic()
    samples = gen.generate(n_runs=args.runs)

    if not samples:
        console.print("[red]No samples generated — check for errors above.[/red]")
        sys.exit(1)

    gen.save(samples, output)
    elapsed = time.monotonic() - t0

    # Summary stats
    tensile_vals = [s.tensile_gpa for s in samples]
    thermal_vals = [s.thermal_c   for s in samples]
    recipes      = {}
    for s in samples:
        recipes[s.recipe_name] = recipes.get(s.recipe_name, 0) + 1

    console.print(f"[green]✓[/green] Generated [cyan]{len(samples)}[/cyan] samples in [dim]{elapsed:.1f}s[/dim]")
    console.print()
    console.print("[bold]Dataset Summary[/bold]")
    console.print(f"  Tensile range  : {min(tensile_vals):.3f} – {max(tensile_vals):.3f} GPa")
    console.print(f"  Thermal range  : {min(thermal_vals):.0f} – {max(thermal_vals):.0f} °C")
    console.print(f"  Samples by recipe:")
    for name, count in sorted(recipes.items()):
        console.print(f"    {name:<28} {count}")
    console.print()
    console.print(f"  Saved to [cyan]{output}[/cyan]")
    console.print()


if __name__ == "__main__":
    main()
