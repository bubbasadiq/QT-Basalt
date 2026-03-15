#!/usr/bin/env python3
"""
Qutlas MVP Simulation

Runs a complete demonstration of the full Qutlas platform stack
without requiring any hardware. Showcases all five fiber classes
through the closed-loop system and prints a detailed report.

Usage:
    python scripts/run_mvp.py
    python scripts/run_mvp.py --recipe high_temperature --steps 800
    python scripts/run_mvp.py --all       (run all five recipes)

What it demonstrates:
  1. Process simulator (furnace + draw physics)
  2. Data pipeline (ingestion, sync, ring buffer)
  3. Materials Engine (feature engineering + property prediction)
  4. Adaptive Controller (state machine, multi-variable control)
  5. Export (run record saved to disk)
  6. Different material properties produced from same feedstock
     by changing only software configuration
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, UTC
from pathlib import Path

# Make sure repo root is on path when running as script
sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console  import Console
from rich.panel    import Panel
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
from rich.table    import Table
from rich.text     import Text
from rich          import box

from qutlas.data_pipeline       import DataPipeline
from qutlas.models.engine       import MaterialsEngine
from qutlas.control.controller  import AdaptiveController
from qutlas.control.recipe_loader import RecipeLoader
from qutlas.control.types       import ControlState
from qutlas.data_pipeline.export import RunExporter
from qutlas.simulation.process  import ProcessSimulator
from qutlas.schema              import ControlAction

console = Console()
loader  = RecipeLoader()

ALL_RECIPES = [
    "structural",
    "high_temperature",
    "electrical_insulation",
    "corrosion_resistant",
    "precision_structural",
]


# ── Single run ───────────────────────────────────────────────────────────────

def run_simulation(
    recipe_name: str,
    steps:       int   = 600,
    noise:       float = 0.02,
    export:      bool  = True,
) -> dict:
    """
    Run a complete closed-loop simulation for one recipe.

    Returns a results dict with all metrics.
    """
    recipe     = loader.load(recipe_name)
    sim        = ProcessSimulator(noise_level=noise, dt=0.1)
    pipeline   = DataPipeline()
    engine     = MaterialsEngine(window_size=80, predict_every=8)
    controller = AdaptiveController()
    exporter   = RunExporter(export_root=Path("data-pipeline/export/runs"))

    # Wire platform
    pipeline.on_synced(engine.on_reading)
    pipeline.on_synced(controller.on_reading)
    engine.on_prediction(controller.on_prediction)
    engine.set_recipe(recipe)

    pipeline.start()
    pipeline.reset_for_new_run()

    run = sim.start_run(recipe)
    controller.activate_recipe(recipe_name, run_id=run.run_id)

    # Track metrics
    stable_steps      = 0
    first_stable_step = None
    predictions_made  = 0
    last_reading      = None
    decisions         = []
    controller.on_decision(decisions.append)

    with Progress(
        SpinnerColumn(),
        TextColumn(f"[cyan]{recipe_name:<24}[/cyan]"),
        BarColumn(bar_width=30),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("•"),
        TextColumn("[dim]{task.fields[status]}[/dim]"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("", total=steps, status="warming...")

        for step in range(steps):
            # Build action from controller
            decision = controller.latest_decision
            if decision and step > 5:
                sp = decision.setpoint
                action = ControlAction(
                    timestamp               = datetime.now(UTC),
                    run_id                  = run.run_id,
                    furnace_temp_setpoint_c = sp.furnace_temp_c,
                    draw_speed_setpoint_ms  = sp.draw_speed_ms,
                    cooling_airflow_setpoint= sp.airflow_lpm,
                )
            else:
                action = None

            last_reading = sim.step(action)
            last_reading.run_id = run.run_id
            pipeline.ingest(last_reading)

            # Track stability
            pred = engine.latest_prediction
            if pred:
                predictions_made += 1
                if pred.within_tolerance:
                    stable_steps += 1
                    if first_stable_step is None:
                        first_stable_step = step
                else:
                    if first_stable_step and step - first_stable_step > 20:
                        first_stable_step = None   # drifted, reset

            # Update progress
            ctrl_state = controller.state.value
            diam_str   = f"d={last_reading.fiber_diameter_um:.1f}µm" if last_reading else ""
            progress.update(
                task,
                advance=1,
                status=f"{ctrl_state}  {diam_str}",
            )

    # Complete run
    completed = sim.complete_run()
    pipeline.stop()

    # Export
    if export:
        try:
            exporter.export(completed)
        except Exception:
            pass   # Export failure should not break the demo

    # Build results
    ctrl_stats = controller.stats
    final_pred = engine.latest_prediction

    return {
        "recipe":              recipe_name,
        "run_id":              run.run_id,
        "steps":               steps,
        "final_state":         controller.state.value,
        "aborted":             controller.state == ControlState.ABORTED,

        # Simulated material outcomes
        "outcome_tensile":     completed.outcome_tensile_gpa,
        "outcome_modulus":     completed.outcome_modulus_gpa,
        "outcome_thermal":     completed.outcome_thermal_c,
        "outcome_cv":          completed.outcome_diameter_cv,

        # Recipe targets
        "target_tensile":      recipe.target_tensile_gpa,
        "target_modulus":      recipe.target_modulus_gpa,
        "target_thermal":      recipe.target_thermal_c,
        "target_diameter":     recipe.target_diameter_um,

        # Control metrics
        "predictions_made":    predictions_made,
        "stable_steps":        stable_steps,
        "stable_pct":          round(stable_steps / max(predictions_made, 1) * 100, 1),
        "first_stable_step":   first_stable_step,
        "total_decisions":     ctrl_stats["total_decisions"],
        "total_adjustments":   ctrl_stats["total_adjustments"],
        "consecutive_stable":  ctrl_stats["consecutive_stable"],

        # Final prediction
        "pred_tensile":        final_pred.tensile_strength_gpa  if final_pred else None,
        "pred_thermal":        final_pred.thermal_stability_c   if final_pred else None,
        "pred_confidence":     final_pred.confidence            if final_pred else None,
    }


# ── Reporting ─────────────────────────────────────────────────────────────────

def print_banner() -> None:
    console.print()
    console.print(Panel(
        Text.assemble(
            ("  QUTLAS  ", "bold white"),
            ("Programmable Materials Manufacturing\n", "dim"),
            ("  MVP Simulation — Closed Loop Demonstration\n", "cyan"),
            ("  Phase 1 · Physics baseline · All recipes", "dim"),
        ),
        border_style="blue",
        padding=(0, 2),
    ))
    console.print()


def print_results(results: list[dict]) -> None:
    console.print()
    console.rule("[bold]Simulation Results[/bold]")
    console.print()

    # Summary table
    tbl = Table(
        box=box.SIMPLE_HEAD,
        show_footer=False,
        title="Material Outcomes vs Targets",
        title_style="bold",
    )
    tbl.add_column("Recipe",          style="cyan",    min_width=22)
    tbl.add_column("Tensile (GPa)",   justify="right", min_width=14)
    tbl.add_column("Thermal (°C)",    justify="right", min_width=12)
    tbl.add_column("CV (%)",          justify="right", min_width=8)
    tbl.add_column("Stable %",        justify="right", min_width=9)
    tbl.add_column("Confidence",      justify="right", min_width=10)
    tbl.add_column("State",           min_width=12)

    for r in results:
        tensile_ok = (
            r["outcome_tensile"] is not None and
            abs(r["outcome_tensile"] - r["target_tensile"]) < 0.25
        )
        state_color = "red" if r["aborted"] else "green" if r["stable_pct"] > 40 else "yellow"

        tbl.add_row(
            r["recipe"],
            f"[{'green' if tensile_ok else 'yellow'}]{r['outcome_tensile']:.3f}[/{'green' if tensile_ok else 'yellow'}]"
            f"[dim] / {r['target_tensile']:.1f}[/dim]",
            f"{r['outcome_thermal']:.0f}[dim] / {r['target_thermal']:.0f}[/dim]",
            f"{r['outcome_cv']:.2f}",
            f"[{state_color}]{r['stable_pct']:.0f}%[/{state_color}]",
            f"{r['pred_confidence']:.2f}" if r["pred_confidence"] else "—",
            f"[{state_color}]{r['final_state']}[/{state_color}]",
        )

    console.print(tbl)
    console.print()

    # Platform metrics
    console.print("[bold]Platform Metrics[/bold]")
    console.print()
    total_preds = sum(r["predictions_made"] for r in results)
    total_adj   = sum(r["total_adjustments"] for r in results)
    total_steps = sum(r["steps"] for r in results)
    console.print(f"  Total timesteps simulated : [cyan]{total_steps:,}[/cyan]")
    console.print(f"  Total predictions made    : [cyan]{total_preds:,}[/cyan]")
    console.print(f"  Total parameter adjustments: [cyan]{total_adj:,}[/cyan]")
    console.print()

    # Key finding
    if len(results) > 1:
        tensile_range = (
            min(r["outcome_tensile"] for r in results if r["outcome_tensile"]),
            max(r["outcome_tensile"] for r in results if r["outcome_tensile"]),
        )
        thermal_range = (
            min(r["outcome_thermal"] for r in results if r["outcome_thermal"]),
            max(r["outcome_thermal"] for r in results if r["outcome_thermal"]),
        )
        console.print(Panel(
            Text.assemble(
                ("  Tensile strength range : ", "dim"),
                (f"{tensile_range[0]:.3f} – {tensile_range[1]:.3f} GPa\n", "white"),
                ("  Thermal stability range: ", "dim"),
                (f"{thermal_range[0]:.0f} – {thermal_range[1]:.0f} °C\n", "white"),
                ("  Produced from identical feedstock via software control only.", "cyan"),
            ),
            title="[bold]Key Finding[/bold]",
            border_style="green",
            padding=(0, 2),
        ))
    console.print()


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Qutlas MVP Simulation")
    parser.add_argument("--recipe", default="structural",
                        help="Recipe name (default: structural)")
    parser.add_argument("--steps",  type=int, default=600,
                        help="Simulation steps per run (default: 600)")
    parser.add_argument("--noise",  type=float, default=0.02,
                        help="Sensor noise level 0–1 (default: 0.02)")
    parser.add_argument("--all",    action="store_true",
                        help="Run all five fiber recipes")
    parser.add_argument("--no-export", action="store_true",
                        help="Skip exporting run records")
    args = parser.parse_args()

    print_banner()

    recipes_to_run = ALL_RECIPES if args.all else [args.recipe]
    export         = not args.no_export

    console.print(
        f"Running [cyan]{len(recipes_to_run)}[/cyan] recipe(s)  "
        f"·  [cyan]{args.steps}[/cyan] steps each  "
        f"·  noise=[cyan]{args.noise}[/cyan]\n"
    )

    results = []
    t_start = time.monotonic()

    for recipe_name in recipes_to_run:
        result = run_simulation(
            recipe_name = recipe_name,
            steps       = args.steps,
            noise       = args.noise,
            export      = export,
        )
        results.append(result)
        status = "[green]✓[/green]" if not result["aborted"] else "[red]✗[/red]"
        console.print(
            f"  {status} [cyan]{recipe_name:<26}[/cyan]"
            f"tensile={result['outcome_tensile']:.3f}GPa  "
            f"stable={result['stable_pct']:.0f}%"
        )

    elapsed = time.monotonic() - t_start
    console.print(f"\n  Completed in [dim]{elapsed:.1f}s[/dim]")

    print_results(results)


if __name__ == "__main__":
    main()
