"""
Operator Dashboard

Rich terminal interface for monitoring a live production run.
Connects to the platform API via WebSocket and displays:

  - Live sensor readings with trend indicators
  - Property prediction panel with tolerance status
  - Controller state and current setpoints
  - Run timeline and stability metrics
  - Alert panel for safety events

Run:
  python -m qutlas.dashboard.operator --api http://localhost:8000
  qutlas dashboard

The dashboard polls the REST API for status and subscribes
to the WebSocket for live telemetry.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from datetime import datetime
from typing import Any, Optional

import httpx
import typer
from rich.columns import Columns
from rich.console import Console
from rich.layout import Layout
from rich.live   import Live
from rich.panel  import Panel
from rich.table  import Table
from rich.text   import Text
from rich import box

console = Console()
app     = typer.Typer()


# ── Dashboard state ─────────────────────────────────────────────────────────

class DashboardState:
    def __init__(self) -> None:
        self.sensor:     dict[str, Any] = {}
        self.prediction: dict[str, Any] = {}
        self.status:     dict[str, Any] = {}
        self.alerts:     deque[str]     = deque(maxlen=6)
        self.temp_history:   deque[float] = deque(maxlen=60)
        self.diam_history:   deque[float] = deque(maxlen=60)
        self.conf_history:   deque[float] = deque(maxlen=60)
        self.tick:       int  = 0
        self.connected:  bool = False
        self.start_time: float = time.monotonic()

    @property
    def uptime(self) -> str:
        s = int(time.monotonic() - self.start_time)
        return f"{s//3600:02d}:{(s%3600)//60:02d}:{s%60:02d}"


state = DashboardState()


# ── Layout renderers ────────────────────────────────────────────────────────

def render_header() -> Panel:
    ctrl_state = state.status.get("controller", {}).get("state", "—")
    recipe     = state.status.get("active_recipe") or "—"
    run_id     = (state.status.get("active_run") or "—")
    run_short  = run_id[:8] if run_id != "—" else "—"

    conn_dot = "[green]●[/green]" if state.connected else "[red]●[/red]"
    conn_txt = "CONNECTED" if state.connected else "DISCONNECTED"

    state_color = {
        "idle":       "dim",
        "warming":    "yellow",
        "converging": "cyan",
        "stable":     "green",
        "aborted":    "red",
    }.get(ctrl_state, "white")

    t = Text()
    t.append("  QUTLAS ", style="bold white")
    t.append("·  Platform Monitor", style="dim")
    t.append(f"    {conn_dot} {conn_txt}", style="")
    t.append(f"    recipe: ", style="dim")
    t.append(recipe, style="amber1")
    t.append(f"    run: ", style="dim")
    t.append(run_short, style="cyan")
    t.append(f"    state: ", style="dim")
    t.append(ctrl_state.upper(), style=state_color)
    t.append(f"    uptime: {state.uptime}", style="dim")

    return Panel(t, style="bold", padding=(0, 1))


def render_sensors() -> Panel:
    s = state.sensor

    def val(key: str, unit: str, fmt: str = ".1f") -> str:
        v = s.get(key)
        if v is None:
            return "[dim]—[/dim]"
        return f"{v:{fmt}}{unit}"

    def trend(history: deque) -> str:
        if len(history) < 3:
            return "[dim]~[/dim]"
        delta = history[-1] - history[-3]
        if delta > 0.5:   return "[red]↑[/red]"
        if delta < -0.5:  return "[cyan]↓[/cyan]"
        return "[green]→[/green]"

    tbl = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    tbl.add_column(style="dim", min_width=18)
    tbl.add_column(min_width=14)
    tbl.add_column(min_width=4)

    tbl.add_row("Furnace temp",    val("temp_c",   " °C"),  trend(state.temp_history))
    tbl.add_row("Fiber diameter",  val("diam_um",  " µm"),  trend(state.diam_history))
    tbl.add_row("Draw speed",      val("speed_ms", " m/s"), "")
    tbl.add_row("Melt viscosity",  val("visc_cp",  " cP"),  "")
    tbl.add_row("Airflow",         val("airflow",  " L/min"), "")

    ctrl = state.status.get("controller", {})
    tbl.add_row("", "", "")
    tbl.add_row("[dim]Setpoint temp[/dim]",
                f"[yellow]{ctrl.get('current_temp_sp', '—'):.1f} °C[/yellow]"
                if ctrl.get("current_temp_sp") else "[dim]—[/dim]", "")
    tbl.add_row("[dim]Setpoint speed[/dim]",
                f"[yellow]{ctrl.get('current_speed_sp', '—'):.2f} m/s[/yellow]"
                if ctrl.get("current_speed_sp") else "[dim]—[/dim]", "")

    return Panel(tbl, title="[bold]Sensors[/bold]", border_style="blue")


def render_predictions() -> Panel:
    p = state.prediction

    def pval(key: str, unit: str, fmt: str = ".3f") -> str:
        v = p.get(key)
        if v is None:
            return "[dim]—[/dim]"
        return f"{v:{fmt}}{unit}"

    conf = p.get("confidence")
    conf_color = (
        "green" if conf and conf > 0.7 else
        "yellow" if conf and conf > 0.4 else
        "red"
    )
    conf_str = f"[{conf_color}]{conf:.2f}[/{conf_color}]" if conf else "[dim]—[/dim]"

    within = p.get("within_tol")
    tol_str = (
        "[green]✓ WITHIN TOLERANCE[/green]" if within
        else "[yellow]⟳ CONVERGING[/yellow]" if within is False
        else "[dim]—[/dim]"
    )

    tbl = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    tbl.add_column(style="dim", min_width=20)
    tbl.add_column(min_width=16)

    tbl.add_row("Tensile strength",  pval("tensile_gpa", " GPa"))
    tbl.add_row("Elastic modulus",   pval("modulus_gpa", " GPa", ".2f"))
    tbl.add_row("Thermal stability", pval("thermal_c",   " °C",  ".0f"))
    tbl.add_row("Diameter CV",       pval("diameter_cv", " %",   ".2f"))
    tbl.add_row("", "")
    tbl.add_row("Confidence",        conf_str)
    tbl.add_row("Status",            tol_str)
    tbl.add_row("[dim]Model[/dim]",  f"[dim]{p.get('model', '—')}[/dim]")

    return Panel(tbl, title="[bold]Predictions[/bold]", border_style="magenta")


def render_sparkline(
    history: deque,
    label:   str,
    lo:      float,
    hi:      float,
    color:   str = "cyan",
) -> Panel:
    if not history:
        return Panel("[dim]waiting for data...[/dim]", title=label)

    chars = "▁▂▃▄▅▆▇█"
    width = min(50, len(history))
    vals  = list(history)[-width:]
    span  = max(hi - lo, 1e-6)

    bar = ""
    for v in vals:
        idx = int((v - lo) / span * (len(chars) - 1))
        idx = max(0, min(len(chars) - 1, idx))
        bar += chars[idx]

    latest = f"{history[-1]:.1f}" if history else "—"
    return Panel(
        f"[{color}]{bar}[/{color}]  [{color}]{latest}[/{color}]",
        title=label,
        padding=(0, 1),
    )


def render_alerts() -> Panel:
    if not state.alerts:
        content = "[dim]No alerts[/dim]"
    else:
        lines = []
        for a in reversed(state.alerts):
            lines.append(f"[yellow]⚠[/yellow] {a}")
        content = "\n".join(lines)
    return Panel(content, title="[bold]Alerts[/bold]", border_style="yellow")


def render_pipeline() -> Panel:
    pipe = state.status.get("pipeline", {})
    ctrl = state.status.get("controller", {})
    eng  = state.status.get("engine", {})

    buf_size  = pipe.get("buffer_size", 0)
    buf_cap   = pipe.get("buffer_capacity", 10000)
    fill_pct  = int(buf_size / max(buf_cap, 1) * 100)
    fill_bar  = "█" * (fill_pct // 5) + "░" * (20 - fill_pct // 5)

    tbl = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    tbl.add_column(style="dim", min_width=20)
    tbl.add_column()

    tbl.add_row("Buffer",         f"[cyan]{fill_bar}[/cyan] {fill_pct}%")
    tbl.add_row("Accepted",       str(pipe.get("total_accepted", "—")))
    tbl.add_row("Dropped",        str(pipe.get("total_dropped",  "—")))
    tbl.add_row("Predictions",    str(eng.get("prediction_count", "—")))
    tbl.add_row("Stable count",   str(ctrl.get("consecutive_stable", "—")))
    tbl.add_row("Engine ready",   "[green]yes[/green]" if eng.get("is_ready") else "[yellow]warming[/yellow]")

    return Panel(tbl, title="[bold]Pipeline[/bold]", border_style="green")


def build_layout() -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="main"),
        Layout(name="footer", size=6),
    )
    layout["main"].split_row(
        Layout(name="left"),
        Layout(name="right"),
    )
    layout["left"].split_column(
        Layout(name="sensors"),
        Layout(name="predictions"),
    )
    layout["right"].split_column(
        Layout(name="temp_spark"),
        Layout(name="diam_spark"),
        Layout(name="pipeline"),
    )
    return layout


def update_layout(layout: Layout) -> None:
    state.tick += 1
    layout["header"].update(render_header())
    layout["sensors"].update(render_sensors())
    layout["predictions"].update(render_predictions())
    layout["temp_spark"].update(
        render_sparkline(state.temp_history, "Furnace Temp (°C)", 1400, 1600, "red")
    )
    layout["diam_spark"].update(
        render_sparkline(state.diam_history, "Fiber Diameter (µm)", 6, 22, "cyan")
    )
    layout["pipeline"].update(render_pipeline())
    layout["footer"].update(render_alerts())


# ── Async tasks ─────────────────────────────────────────────────────────────

async def poll_status(api_url: str) -> None:
    """Poll /status every 2 seconds."""
    async with httpx.AsyncClient(base_url=api_url, timeout=3.0) as client:
        while True:
            try:
                r = await client.get("/status")
                state.status    = r.json()
                state.connected = True
            except Exception:
                state.connected = False
            await asyncio.sleep(2.0)


async def poll_prediction(api_url: str) -> None:
    """Poll /predictions/latest every second."""
    async with httpx.AsyncClient(base_url=api_url, timeout=3.0) as client:
        while True:
            try:
                r = await client.get("/predictions/latest")
                p = r.json()
                state.prediction = p
                if p.get("confidence") is not None:
                    state.conf_history.append(p["confidence"])
            except Exception:
                pass
            await asyncio.sleep(1.0)


async def ws_listener(api_url: str) -> None:
    """Subscribe to WebSocket telemetry stream."""
    import websockets  # type: ignore[import]
    ws_url = api_url.replace("http", "ws") + "/ws/telemetry"
    while True:
        try:
            async with websockets.connect(ws_url) as ws:
                state.connected = True
                async for raw in ws:
                    msg = json.loads(raw)
                    if msg["type"] == "sensor":
                        d = msg["data"]
                        state.sensor = {
                            "temp_c":   d.get("temp_c"),
                            "diam_um":  d.get("diam_um"),
                            "speed_ms": d.get("speed_ms"),
                            "visc_cp":  d.get("visc_cp"),
                            "airflow":  d.get("airflow"),
                        }
                        if d.get("temp_c"):
                            state.temp_history.append(d["temp_c"])
                        if d.get("diam_um"):
                            state.diam_history.append(d["diam_um"])
                    elif msg["type"] == "prediction":
                        state.prediction = msg["data"]
        except Exception:
            state.connected = False
            await asyncio.sleep(2.0)


async def render_loop(layout: Layout) -> None:
    """Update the layout every 500ms."""
    while True:
        update_layout(layout)
        await asyncio.sleep(0.5)


# ── Entry point ──────────────────────────────────────────────────────────────

def run_dashboard(api_url: str = "http://localhost:8000") -> None:
    """Start the operator dashboard."""
    layout = build_layout()

    async def main() -> None:
        tasks = [
            asyncio.create_task(poll_status(api_url)),
            asyncio.create_task(poll_prediction(api_url)),
            asyncio.create_task(render_loop(layout)),
        ]
        try:
            async with asyncio.TaskGroup() as tg:
                for t in tasks:
                    tg.create_task(asyncio.shield(t))
        except* Exception:
            pass

    with Live(layout, refresh_per_second=4, screen=True):
        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            pass


@app.command()
def operator(
    api: str = typer.Option("http://localhost:8000", help="Platform API URL"),
) -> None:
    """Launch the operator terminal dashboard."""
    run_dashboard(api)


if __name__ == "__main__":
    app()
