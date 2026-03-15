"""
Qutlas CLI

Usage:
    qutlas simulate                    Run MVP simulation (single recipe)
    qutlas simulate --all              Run all five recipes
    qutlas simulate --recipe NAME      Run a specific recipe
    qutlas api                         Start the platform API server
    qutlas dashboard                   Launch the operator terminal dashboard
    qutlas generate-dataset            Generate simulator training data
    qutlas recipes                     List available fiber recipes
    qutlas version                     Print platform version
"""

import typer
from rich.console import Console

app     = typer.Typer(
    name="qutlas",
    help="Qutlas · Programmable Materials Manufacturing Platform",
    add_completion=False,
)
console = Console()


@app.command()
def simulate(
    recipe:  str   = typer.Option("structural", help="Fiber recipe name"),
    steps:   int   = typer.Option(600,          help="Simulation steps"),
    noise:   float = typer.Option(0.02,         help="Sensor noise level (0-1)"),
    all:     bool  = typer.Option(False, "--all", help="Run all five recipes"),
    export:  bool  = typer.Option(True,          help="Export run records"),
) -> None:
    """Run a closed-loop MVP simulation."""
    import subprocess, sys
    cmd = [sys.executable, "scripts/run_mvp.py",
           "--steps", str(steps), "--noise", str(noise)]
    if all:
        cmd.append("--all")
    else:
        cmd += ["--recipe", recipe]
    if not export:
        cmd.append("--no-export")
    subprocess.run(cmd)


@app.command()
def api(
    host:   str  = typer.Option("0.0.0.0", help="Bind host"),
    port:   int  = typer.Option(8000,       help="Port"),
    reload: bool = typer.Option(True,       help="Auto-reload"),
) -> None:
    """Start the platform API server."""
    import uvicorn
    console.print(f"[bold]Qutlas API[/bold] starting on [cyan]http://{host}:{port}[/cyan]")
    uvicorn.run(
        "qutlas.platform_api.app:app",
        host=host, port=port, reload=reload, log_level="info",
    )


@app.command()
def dashboard(
    api_url: str = typer.Option("http://localhost:8000", help="API URL"),
) -> None:
    """Launch the operator terminal dashboard."""
    from qutlas.dashboard.operator import run_dashboard
    run_dashboard(api_url)


@app.command(name="generate-dataset")
def generate_dataset(
    runs:   int  = typer.Option(200,  help="Number of runs"),
    steps:  int  = typer.Option(400,  help="Steps per run"),
    output: str  = typer.Option("models/training/datasets/sim_v1.json", help="Output path"),
    quick:  bool = typer.Option(False, "--quick", help="50 runs, 200 steps"),
) -> None:
    """Generate a labeled training dataset from the process simulator."""
    import subprocess, sys
    cmd = [sys.executable, "scripts/generate_dataset.py",
           "--runs", str(runs), "--steps", str(steps), "--output", output]
    if quick:
        cmd.append("--quick")
    subprocess.run(cmd)


@app.command()
def recipes() -> None:
    """List all available fiber recipes."""
    from qutlas.control.recipe_loader import RecipeLoader
    loader = RecipeLoader()
    console.print()
    console.print("[bold]Available Fiber Recipes[/bold]")
    console.print()
    for name in loader.list_available():
        r = loader.load(name)
        console.print(
            f"  [cyan]{name:<26}[/cyan]"
            f"tensile={r.target_tensile_gpa:.1f}GPa  "
            f"thermal={r.target_thermal_c:.0f}C  "
            f"diameter={r.target_diameter_um:.0f}um"
        )
    console.print()


@app.command()
def version() -> None:
    """Print the platform version."""
    from qutlas import __version__
    console.print(f"qutlas [bold]{__version__}[/bold]")


if __name__ == "__main__":
    app()
