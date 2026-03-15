#!/usr/bin/env python3
"""
Qutlas Operator Dashboard

Single-file web dashboard. Runs the simulation live and streams
data to a browser interface in real time.

Usage:
    python scripts/run_dashboard.py
    python scripts/run_dashboard.py --recipe structural
    python scripts/run_dashboard.py --steps 1200

Opens automatically at http://localhost:5050
No npm, no build step, no dependencies beyond what is already installed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import threading
import time
import webbrowser
from datetime import datetime, UTC
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# ── HTML / CSS / JS dashboard ─────────────────────────────────────────────────
DASHBOARD_HTML = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Qutlas · Operator Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Mono:wght@300;400;500&family=DM+Sans:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --navy:   #1C2166;
    --navyD:  #0F1240;
    --navyM:  #242888;
    --amber:  #FFAA00;
    --amberD: #CC8800;
    --cream:  #F0EDE6;
    --white:  #FFFFFF;
    --dim:    #8890BB;
    --rule:   rgba(255,255,255,0.08);
    --green:  #1A8A4A;
    --red:    #CC2233;
    --mono:   'DM Mono', monospace;
    --sans:   'DM Sans', sans-serif;
  }

  html, body {
    height: 100%;
    background: var(--navyD);
    color: var(--cream);
    font-family: var(--sans);
    font-size: 13px;
    overflow: hidden;
  }

  /* ── Layout ── */
  .shell {
    display: grid;
    grid-template-rows: 48px 1fr;
    height: 100vh;
  }

  /* ── Header ── */
  header {
    background: var(--navy);
    border-bottom: 1px solid var(--rule);
    display: flex;
    align-items: center;
    padding: 0 20px;
    gap: 24px;
  }
  .logo {
    font-family: var(--mono);
    font-size: 14px;
    font-weight: 500;
    letter-spacing: 0.12em;
    color: var(--white);
  }
  .logo span { color: var(--amber); }
  .header-sep { flex: 1; }
  .status-pill {
    font-family: var(--mono);
    font-size: 10px;
    letter-spacing: 0.1em;
    padding: 4px 10px;
    border-radius: 2px;
    font-weight: 500;
  }
  .pill-idle     { background: rgba(136,144,187,0.15); color: var(--dim); }
  .pill-warming  { background: rgba(255,170,0,0.15);   color: var(--amber); }
  .pill-converging { background: rgba(26,138,217,0.15); color: #4AACFF; }
  .pill-stable   { background: rgba(26,138,74,0.15);   color: #4ADB7A; }
  .pill-aborted  { background: rgba(204,34,51,0.15);   color: #FF4455; }

  .live-dot {
    width: 6px; height: 6px;
    border-radius: 50%;
    background: var(--dim);
    animation: none;
  }
  .live-dot.active {
    background: var(--amber);
    animation: pulse 1.8s ease-in-out infinite;
  }
  @keyframes pulse { 0%,100%{opacity:0.3} 50%{opacity:1} }

  /* ── Main grid ── */
  .main {
    display: grid;
    grid-template-columns: 220px 1fr 260px;
    grid-template-rows: 1fr;
    gap: 1px;
    background: var(--rule);
    overflow: hidden;
  }

  /* ── Panels ── */
  .panel {
    background: var(--navyD);
    overflow: hidden;
    display: flex;
    flex-direction: column;
  }
  .panel-head {
    padding: 10px 14px 8px;
    border-bottom: 1px solid var(--rule);
    font-family: var(--mono);
    font-size: 9px;
    letter-spacing: 0.14em;
    color: var(--dim);
    font-weight: 500;
    flex-shrink: 0;
  }
  .panel-body {
    flex: 1;
    overflow-y: auto;
    padding: 12px 14px;
    scrollbar-width: thin;
    scrollbar-color: var(--navyM) transparent;
  }

  /* ── Left panel: controls + recipe ── */
  .recipe-btn {
    width: 100%;
    text-align: left;
    background: none;
    border: 1px solid var(--rule);
    color: var(--dim);
    font-family: var(--mono);
    font-size: 10px;
    padding: 8px 10px;
    margin-bottom: 4px;
    cursor: pointer;
    transition: all 0.15s;
    letter-spacing: 0.06em;
    position: relative;
  }
  .recipe-btn:hover { border-color: var(--amber); color: var(--cream); }
  .recipe-btn.active {
    border-color: var(--amber);
    color: var(--amber);
    background: rgba(255,170,0,0.06);
  }
  .recipe-btn .r-code {
    font-size: 8px;
    color: var(--dim);
    display: block;
    margin-bottom: 2px;
    letter-spacing: 0.1em;
  }
  .recipe-btn.active .r-code { color: var(--amberD); }

  .run-btn {
    width: 100%;
    padding: 11px;
    background: var(--amber);
    color: var(--navyD);
    border: none;
    font-family: var(--mono);
    font-size: 11px;
    font-weight: 500;
    letter-spacing: 0.1em;
    cursor: pointer;
    margin-top: 12px;
    transition: background 0.15s, transform 0.1s;
  }
  .run-btn:hover { background: #FFB822; transform: translateY(-1px); }
  .run-btn:active { transform: translateY(0); }
  .run-btn:disabled { background: var(--navyM); color: var(--dim); cursor: not-allowed; transform: none; }

  .run-all-btn {
    width: 100%;
    padding: 9px;
    background: none;
    color: var(--amber);
    border: 1px solid rgba(255,170,0,0.3);
    font-family: var(--mono);
    font-size: 10px;
    letter-spacing: 0.08em;
    cursor: pointer;
    margin-top: 6px;
    transition: all 0.15s;
  }
  .run-all-btn:hover { background: rgba(255,170,0,0.08); border-color: var(--amber); }
  .run-all-btn:disabled { opacity: 0.4; cursor: not-allowed; }

  .steps-row {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-top: 10px;
  }
  .steps-row label {
    font-family: var(--mono);
    font-size: 9px;
    color: var(--dim);
    letter-spacing: 0.1em;
    white-space: nowrap;
  }
  .steps-row input {
    flex: 1;
    background: var(--navyM);
    border: 1px solid var(--rule);
    color: var(--cream);
    font-family: var(--mono);
    font-size: 11px;
    padding: 5px 8px;
    width: 100%;
  }
  .steps-row input:focus { outline: none; border-color: var(--amber); }

  .divider {
    height: 1px;
    background: var(--rule);
    margin: 14px 0;
  }

  /* ── Sensor rows ── */
  .sensor-row {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    padding: 5px 0;
    border-bottom: 1px solid rgba(255,255,255,0.04);
  }
  .sensor-label {
    font-family: var(--mono);
    font-size: 9px;
    color: var(--dim);
    letter-spacing: 0.08em;
  }
  .sensor-val {
    font-family: var(--mono);
    font-size: 12px;
    color: var(--cream);
    font-weight: 500;
  }
  .sensor-val.amber { color: var(--amber); }
  .sensor-val.green { color: #4ADB7A; }
  .sensor-val.red   { color: #FF4455; }

  /* ── Progress bar ── */
  .progress-wrap {
    margin: 10px 0 4px;
  }
  .progress-label {
    display: flex;
    justify-content: space-between;
    font-family: var(--mono);
    font-size: 9px;
    color: var(--dim);
    margin-bottom: 5px;
    letter-spacing: 0.08em;
  }
  .progress-bar {
    height: 3px;
    background: var(--navyM);
    border-radius: 1px;
    overflow: hidden;
  }
  .progress-fill {
    height: 100%;
    background: var(--amber);
    border-radius: 1px;
    width: 0%;
    transition: width 0.3s ease;
  }

  /* ── Centre panel: charts ── */
  .charts-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    grid-template-rows: 1fr 1fr;
    gap: 1px;
    background: var(--rule);
    height: 100%;
  }
  .chart-cell {
    background: var(--navyD);
    display: flex;
    flex-direction: column;
    padding: 10px 12px 8px;
    min-height: 0;
  }
  .chart-title {
    font-family: var(--mono);
    font-size: 9px;
    color: var(--dim);
    letter-spacing: 0.12em;
    margin-bottom: 6px;
    flex-shrink: 0;
    display: flex;
    justify-content: space-between;
    align-items: baseline;
  }
  .chart-current {
    font-size: 11px;
    color: var(--amber);
    font-weight: 500;
  }
  canvas {
    flex: 1;
    min-height: 0;
    width: 100% !important;
  }

  /* ── Right panel: predictions ── */
  .pred-block {
    margin-bottom: 14px;
  }
  .pred-label {
    font-family: var(--mono);
    font-size: 8px;
    color: var(--dim);
    letter-spacing: 0.12em;
    margin-bottom: 3px;
  }
  .pred-value {
    font-family: var(--mono);
    font-size: 20px;
    font-weight: 500;
    color: var(--cream);
    line-height: 1;
  }
  .pred-value .unit {
    font-size: 11px;
    color: var(--dim);
    font-weight: 300;
    margin-left: 3px;
  }
  .pred-target {
    font-family: var(--mono);
    font-size: 9px;
    color: var(--dim);
    margin-top: 2px;
  }
  .pred-target .hit { color: #4ADB7A; }
  .pred-target .miss { color: #FF8855; }

  .confidence-bar {
    height: 2px;
    background: var(--navyM);
    border-radius: 1px;
    margin-top: 4px;
    overflow: hidden;
  }
  .confidence-fill {
    height: 100%;
    background: var(--amber);
    border-radius: 1px;
    transition: width 0.5s ease;
  }

  .metric-row {
    display: flex;
    justify-content: space-between;
    padding: 4px 0;
    border-bottom: 1px solid rgba(255,255,255,0.04);
  }
  .metric-label {
    font-family: var(--mono);
    font-size: 9px;
    color: var(--dim);
    letter-spacing: 0.06em;
  }
  .metric-val {
    font-family: var(--mono);
    font-size: 10px;
    color: var(--cream);
  }

  /* ── Log ── */
  .log-wrap {
    margin-top: 10px;
    flex: 1;
    overflow: hidden;
    display: flex;
    flex-direction: column;
  }
  .log-head {
    font-family: var(--mono);
    font-size: 8px;
    color: var(--dim);
    letter-spacing: 0.12em;
    margin-bottom: 5px;
    flex-shrink: 0;
  }
  .log-list {
    flex: 1;
    overflow-y: auto;
    scrollbar-width: thin;
    scrollbar-color: var(--navyM) transparent;
  }
  .log-entry {
    font-family: var(--mono);
    font-size: 9px;
    color: var(--dim);
    padding: 2px 0;
    line-height: 1.6;
    border-bottom: 1px solid rgba(255,255,255,0.02);
  }
  .log-entry .ts { color: rgba(136,144,187,0.5); margin-right: 6px; }
  .log-entry.warn { color: #FF8855; }
  .log-entry.ok   { color: #4ADB7A; }
  .log-entry.info { color: var(--amber); }

  /* ── Results table ── */
  .results-table {
    width: 100%;
    border-collapse: collapse;
    font-family: var(--mono);
    font-size: 9px;
    margin-top: 8px;
  }
  .results-table th {
    color: var(--dim);
    letter-spacing: 0.1em;
    padding: 4px 6px;
    text-align: left;
    border-bottom: 1px solid var(--rule);
    font-weight: 400;
  }
  .results-table td {
    padding: 5px 6px;
    color: var(--cream);
    border-bottom: 1px solid rgba(255,255,255,0.03);
  }
  .results-table tr:hover td { background: rgba(255,255,255,0.02); }
  .results-table .good { color: #4ADB7A; }
  .results-table .warn { color: #FF8855; }

  .hidden { display: none !important; }
</style>
</head>
<body>
<div class="shell">

  <!-- Header -->
  <header>
    <div class="logo"><span>Q</span>UTLAS</div>
    <div class="status-pill pill-idle" id="statePill">IDLE</div>
    <div class="live-dot" id="liveDot"></div>
    <div class="header-sep"></div>
    <div style="font-family:var(--mono);font-size:9px;color:var(--dim);letter-spacing:0.1em">
      PROGRAMMABLE MATERIALS MANUFACTURING &nbsp;·&nbsp; PHASE 1
    </div>
  </header>

  <!-- Main -->
  <div class="main">

    <!-- LEFT: controls -->
    <div class="panel">
      <div class="panel-head">RECIPES</div>
      <div class="panel-body">

        <div id="recipeBtns">
          <button class="recipe-btn active" data-recipe="structural" onclick="selectRecipe(this)">
            <span class="r-code">SF-01</span>Structural
          </button>
          <button class="recipe-btn" data-recipe="high_temperature" onclick="selectRecipe(this)">
            <span class="r-code">HT-02</span>High Temperature
          </button>
          <button class="recipe-btn" data-recipe="electrical_insulation" onclick="selectRecipe(this)">
            <span class="r-code">EI-03</span>Electrical Insulation
          </button>
          <button class="recipe-btn" data-recipe="corrosion_resistant" onclick="selectRecipe(this)">
            <span class="r-code">CR-04</span>Corrosion Resistant
          </button>
          <button class="recipe-btn" data-recipe="precision_structural" onclick="selectRecipe(this)">
            <span class="r-code">PS-05</span>Precision Structural
          </button>
        </div>

        <div class="steps-row">
          <label>STEPS</label>
          <input type="number" id="stepsInput" value="1200" min="200" max="5000" step="200">
        </div>

        <button class="run-btn" id="runBtn" onclick="runSingle()">▶  RUN RECIPE</button>
        <button class="run-all-btn" id="runAllBtn" onclick="runAll()">RUN ALL RECIPES</button>

        <div class="divider"></div>

        <div class="panel-head" style="padding:0 0 8px;border:none">SENSORS</div>

        <div class="progress-wrap" id="progressWrap" style="display:none">
          <div class="progress-label">
            <span>PROGRESS</span>
            <span id="progressPct">0%</span>
          </div>
          <div class="progress-bar"><div class="progress-fill" id="progressFill"></div></div>
        </div>

        <div class="sensor-row">
          <span class="sensor-label">FURNACE TEMP</span>
          <span class="sensor-val amber" id="sTemp">—</span>
        </div>
        <div class="sensor-row">
          <span class="sensor-label">FIBER DIAMETER</span>
          <span class="sensor-val" id="sDiam">—</span>
        </div>
        <div class="sensor-row">
          <span class="sensor-label">DRAW SPEED</span>
          <span class="sensor-val" id="sSpeed">—</span>
        </div>
        <div class="sensor-row">
          <span class="sensor-label">VISCOSITY</span>
          <span class="sensor-val" id="sVisc">—</span>
        </div>
        <div class="sensor-row">
          <span class="sensor-label">SETPOINT TEMP</span>
          <span class="sensor-val" id="sSP">—</span>
        </div>
        <div class="sensor-row">
          <span class="sensor-label">TIMESTEP</span>
          <span class="sensor-val" id="sStep">—</span>
        </div>

      </div>
    </div>

    <!-- CENTRE: charts -->
    <div class="panel" style="padding:0">
      <div class="charts-grid">
        <div class="chart-cell">
          <div class="chart-title">
            FURNACE TEMPERATURE (°C)
            <span class="chart-current" id="curTemp">—</span>
          </div>
          <canvas id="chartTemp"></canvas>
        </div>
        <div class="chart-cell">
          <div class="chart-title">
            FIBER DIAMETER (µm)
            <span class="chart-current" id="curDiam">—</span>
          </div>
          <canvas id="chartDiam"></canvas>
        </div>
        <div class="chart-cell">
          <div class="chart-title">
            TENSILE PREDICTION (GPa)
            <span class="chart-current" id="curTensile">—</span>
          </div>
          <canvas id="chartTensile"></canvas>
        </div>
        <div class="chart-cell">
          <div class="chart-title">
            THERMAL STABILITY (°C)
            <span class="chart-current" id="curThermal">—</span>
          </div>
          <canvas id="chartThermal"></canvas>
        </div>
      </div>
    </div>

    <!-- RIGHT: predictions + log -->
    <div class="panel">
      <div class="panel-head">PREDICTIONS</div>
      <div class="panel-body" style="display:flex;flex-direction:column;gap:0">

        <div class="pred-block">
          <div class="pred-label">TENSILE STRENGTH</div>
          <div class="pred-value" id="predTensile">—<span class="unit">GPa</span></div>
          <div class="pred-target" id="predTensileTarget"></div>
          <div class="confidence-bar"><div class="confidence-fill" id="confFill" style="width:0%"></div></div>
        </div>

        <div class="pred-block">
          <div class="pred-label">ELASTIC MODULUS</div>
          <div class="pred-value" id="predModulus">—<span class="unit">GPa</span></div>
        </div>

        <div class="pred-block">
          <div class="pred-label">THERMAL STABILITY</div>
          <div class="pred-value" id="predThermal">—<span class="unit">°C</span></div>
        </div>

        <div class="pred-block">
          <div class="pred-label">DIAMETER CV</div>
          <div class="pred-value" id="predCV">—<span class="unit">%</span></div>
        </div>

        <div class="divider"></div>

        <div class="metric-row">
          <span class="metric-label">PREDICTIONS MADE</span>
          <span class="metric-val" id="mPreds">0</span>
        </div>
        <div class="metric-row">
          <span class="metric-label">ADJUSTMENTS</span>
          <span class="metric-val" id="mAdj">0</span>
        </div>
        <div class="metric-row">
          <span class="metric-label">WITHIN TOLERANCE</span>
          <span class="metric-val" id="mTol">—</span>
        </div>
        <div class="metric-row">
          <span class="metric-label">CONFIDENCE</span>
          <span class="metric-val" id="mConf">—</span>
        </div>

        <div class="divider"></div>

        <!-- Results table (shown after run) -->
        <div id="resultsWrap" class="hidden">
          <div class="pred-label" style="margin-bottom:6px">COMPLETED RUNS</div>
          <table class="results-table">
            <thead>
              <tr>
                <th>RECIPE</th>
                <th>TENSILE</th>
                <th>THERMAL</th>
                <th>STABLE</th>
              </tr>
            </thead>
            <tbody id="resultsBody"></tbody>
          </table>
        </div>

        <div class="log-wrap">
          <div class="log-head">SYSTEM LOG</div>
          <div class="log-list" id="logList"></div>
        </div>

      </div>
    </div>

  </div>
</div>

<script>
// ── Chart setup ──────────────────────────────────────────────────────────────
const MAX_POINTS = 200;

const charts = {};
const chartData = {
  temp:    { labels:[], data:[] },
  diam:    { labels:[], data:[] },
  tensile: { labels:[], data:[] },
  thermal: { labels:[], data:[] },
};

function makeChart(id, color, targetLine) {
  const canvas = document.getElementById(id);
  const ctx    = canvas.getContext('2d');
  const cfg = {
    labels: [],
    datasets: [{
      data: [],
      borderColor: color,
      borderWidth: 1.5,
      pointRadius: 0,
      tension: 0.3,
      fill: true,
      backgroundColor: hexAlpha(color, 0.07),
    }]
  };
  if (targetLine !== undefined) {
    cfg.datasets.push({
      data: [],
      borderColor: 'rgba(255,255,255,0.15)',
      borderWidth: 1,
      borderDash: [4,4],
      pointRadius: 0,
      fill: false,
    });
  }
  return { ctx, cfg, target: targetLine };
}

function hexAlpha(hex, alpha) {
  const r = parseInt(hex.slice(1,3),16);
  const g = parseInt(hex.slice(3,5),16);
  const b = parseInt(hex.slice(5,7),16);
  return `rgba(${r},${g},${b},${alpha})`;
}

// Simple canvas chart renderer
function drawChart(key, canvas, color, targetVal) {
  const d     = chartData[key];
  const ctx   = canvas.getContext('2d');
  const W     = canvas.width  = canvas.offsetWidth  * devicePixelRatio;
  const H     = canvas.height = canvas.offsetHeight * devicePixelRatio;
  ctx.clearRect(0, 0, W, H);

  if (d.data.length < 2) return;

  const pad = { top:6, right:6, bottom:18, left:40 };
  const pw  = W - pad.left - pad.right;
  const ph  = H - pad.top  - pad.bottom;

  const vals  = d.data;
  const ymin  = Math.min(...vals) * 0.98;
  const ymax  = Math.max(...vals) * 1.02 || 1;
  const xStep = pw / (MAX_POINTS - 1);

  const toX = i  => pad.left + i * xStep;
  const toY = v  => pad.top  + ph - ((v - ymin) / (ymax - ymin)) * ph;

  // Grid lines
  ctx.strokeStyle = 'rgba(255,255,255,0.05)';
  ctx.lineWidth   = 1;
  for (let i = 0; i <= 4; i++) {
    const y = pad.top + (ph / 4) * i;
    ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(pad.left + pw, y); ctx.stroke();
    const v = ymax - ((ymax - ymin) / 4) * i;
    ctx.fillStyle = 'rgba(136,144,187,0.6)';
    ctx.font      = `${9 * devicePixelRatio}px DM Mono, monospace`;
    ctx.textAlign = 'right';
    ctx.fillText(v.toFixed(1), pad.left - 4, y + 3 * devicePixelRatio);
  }

  // Target line
  if (targetVal !== undefined && targetVal > ymin && targetVal < ymax) {
    const ty = toY(targetVal);
    ctx.strokeStyle = 'rgba(255,255,255,0.2)';
    ctx.setLineDash([4 * devicePixelRatio, 4 * devicePixelRatio]);
    ctx.lineWidth   = 1;
    ctx.beginPath(); ctx.moveTo(pad.left, ty); ctx.lineTo(pad.left + pw, ty); ctx.stroke();
    ctx.setLineDash([]);
  }

  // Fill
  const start = Math.max(0, vals.length - MAX_POINTS);
  const slice = vals.slice(start);
  ctx.beginPath();
  slice.forEach((v, i) => {
    const x = toX(i); const y = toY(v);
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  ctx.lineTo(toX(slice.length - 1), pad.top + ph);
  ctx.lineTo(toX(0), pad.top + ph);
  ctx.closePath();
  const grad = ctx.createLinearGradient(0, pad.top, 0, pad.top + ph);
  grad.addColorStop(0, hexAlpha(color, 0.18));
  grad.addColorStop(1, hexAlpha(color, 0.0));
  ctx.fillStyle = grad;
  ctx.fill();

  // Line
  ctx.beginPath();
  ctx.strokeStyle = color;
  ctx.lineWidth   = 1.5 * devicePixelRatio;
  slice.forEach((v, i) => {
    const x = toX(i); const y = toY(v);
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  ctx.stroke();
}

// ── State ────────────────────────────────────────────────────────────────────
let selectedRecipe = 'structural';
let isRunning      = false;
let currentTargets = {};
let allResults     = [];

const RECIPES = {
  structural:            { code:'SF-01', tensile:2.9, thermal:650 },
  high_temperature:      { code:'HT-02', tensile:2.5, thermal:760 },
  electrical_insulation: { code:'EI-03', tensile:2.6, thermal:620 },
  corrosion_resistant:   { code:'CR-04', tensile:2.7, thermal:640 },
  precision_structural:  { code:'PS-05', tensile:3.1, thermal:660 },
};

function selectRecipe(btn) {
  document.querySelectorAll('.recipe-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  selectedRecipe = btn.dataset.recipe;
  currentTargets = RECIPES[selectedRecipe];
  log(`Recipe selected: ${btn.dataset.recipe}`, 'info');
}

// ── Run ──────────────────────────────────────────────────────────────────────
function setRunning(val) {
  isRunning = val;
  document.getElementById('runBtn').disabled    = val;
  document.getElementById('runAllBtn').disabled = val;
  document.getElementById('progressWrap').style.display = val ? 'block' : 'none';
  document.getElementById('liveDot').className  = val ? 'live-dot active' : 'live-dot';
  if (!val) {
    document.getElementById('progressFill').style.width = '0%';
    document.getElementById('progressPct').textContent  = '0%';
  }
}

function setProgress(pct) {
  document.getElementById('progressFill').style.width = pct + '%';
  document.getElementById('progressPct').textContent  = Math.round(pct) + '%';
}

function setState(state) {
  const pill = document.getElementById('statePill');
  const map  = {
    idle: 'pill-idle', warming: 'pill-warming',
    converging: 'pill-converging', stable: 'pill-stable', aborted: 'pill-aborted'
  };
  pill.className = 'status-pill ' + (map[state] || 'pill-idle');
  pill.textContent = state.toUpperCase();
}

function runSingle() {
  const steps = parseInt(document.getElementById('stepsInput').value) || 1200;
  clearCharts();
  allResults = [];
  document.getElementById('resultsWrap').classList.add('hidden');
  currentTargets = RECIPES[selectedRecipe];
  fetch('/run', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ recipe: selectedRecipe, steps })
  });
}

function runAll() {
  const steps = parseInt(document.getElementById('stepsInput').value) || 1200;
  clearCharts();
  allResults = [];
  document.getElementById('resultsWrap').classList.add('hidden');
  document.getElementById('resultsBody').innerHTML = '';
  fetch('/run_all', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ steps })
  });
}

function clearCharts() {
  Object.values(chartData).forEach(d => { d.labels = []; d.data = []; });
  ['sTemp','sDiam','sSpeed','sVisc','sSP','sStep',
   'predTensile','predModulus','predThermal','predCV',
   'curTemp','curDiam','curTensile','curThermal'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.textContent = id.startsWith('pred') ? '—' : '—';
  });
  document.getElementById('confFill').style.width = '0%';
}

// ── SSE event stream ─────────────────────────────────────────────────────────
const evtSource = new EventSource('/stream');

evtSource.onmessage = function(e) {
  const msg = JSON.parse(e.data);

  if (msg.type === 'start') {
    setRunning(true);
    setState('warming');
    log(`Starting: ${msg.recipe} · ${msg.steps} steps`, 'info');
    currentTargets = RECIPES[msg.recipe] || {};
    // Highlight active recipe button
    document.querySelectorAll('.recipe-btn').forEach(b => {
      b.classList.toggle('active', b.dataset.recipe === msg.recipe);
    });
  }

  else if (msg.type === 'step') {
    const d = msg.data;

    // Update sensors
    if (d.temp)  { document.getElementById('sTemp').textContent  = d.temp.toFixed(1) + ' °C'; }
    if (d.diam && d.diam > 1)  { document.getElementById('sDiam').textContent  = d.diam.toFixed(2) + ' µm'; }
    if (d.speed) { document.getElementById('sSpeed').textContent = d.speed.toFixed(2) + ' m/s'; }
    if (d.visc)  { document.getElementById('sVisc').textContent  = d.visc.toFixed(0) + ' cP'; }
    if (d.sp)    { document.getElementById('sSP').textContent    = d.sp.toFixed(1) + ' °C'; }
    document.getElementById('sStep').textContent = d.step;
    setState(d.state || 'converging');
    setProgress(d.pct || 0);

    // Charts
    const t = d.step;
    if (d.temp) {
      chartData.temp.data.push(d.temp);
      document.getElementById('curTemp').textContent = d.temp.toFixed(1) + '°C';
    }
    if (d.diam && d.diam > 1) {
      chartData.diam.data.push(d.diam);
      document.getElementById('curDiam').textContent = d.diam.toFixed(2) + 'µm';
    }
    if (d.tensile) {
      chartData.tensile.data.push(d.tensile);
      document.getElementById('curTensile').textContent = d.tensile.toFixed(3) + 'GPa';
      document.getElementById('predTensile').innerHTML =
        d.tensile.toFixed(3) + '<span class="unit">GPa</span>';
      if (currentTargets.tensile) {
        const diff = Math.abs(d.tensile - currentTargets.tensile);
        const hit  = diff < 0.25;
        document.getElementById('predTensileTarget').innerHTML =
          `Target: ${currentTargets.tensile} GPa &nbsp;` +
          `<span class="${hit?'hit':'miss'}">${hit ? '✓ within range' : '⟳ converging'}</span>`;
      }
    }
    if (d.thermal) {
      chartData.thermal.data.push(d.thermal);
      document.getElementById('curThermal').textContent = d.thermal.toFixed(0) + '°C';
      document.getElementById('predThermal').innerHTML =
        d.thermal.toFixed(0) + '<span class="unit">°C</span>';
    }
    if (d.modulus) {
      document.getElementById('predModulus').innerHTML =
        d.modulus.toFixed(1) + '<span class="unit">GPa</span>';
    }
    if (d.cv !== undefined) {
      document.getElementById('predCV').innerHTML =
        d.cv.toFixed(3) + '<span class="unit">%</span>';
    }
    if (d.confidence !== undefined) {
      document.getElementById('confFill').style.width = (d.confidence * 100) + '%';
      document.getElementById('mConf').textContent = (d.confidence * 100).toFixed(0) + '%';
    }
    if (d.within_tol !== undefined) {
      document.getElementById('mTol').textContent = d.within_tol ? '✓ YES' : 'NO';
      document.getElementById('mTol').style.color = d.within_tol ? '#4ADB7A' : '#FF8855';
    }
    if (d.pred_count !== undefined) document.getElementById('mPreds').textContent = d.pred_count;
    if (d.adj_count  !== undefined) document.getElementById('mAdj').textContent  = d.adj_count;

    // Redraw charts every 5 steps
    if (d.step % 5 === 0) redrawCharts();
  }

  else if (msg.type === 'result') {
    const r = msg.data;
    allResults.push(r);
    const tbody = document.getElementById('resultsBody');
    const row   = document.createElement('tr');
    const hit   = r.tensile && Math.abs(r.tensile - (RECIPES[r.recipe]?.tensile||0)) < 0.25;
    row.innerHTML = `
      <td>${r.recipe.replace('_',' ')}</td>
      <td class="${hit?'good':'warn'}">${r.tensile?.toFixed(3)||'—'}</td>
      <td>${r.thermal?.toFixed(0)||'—'}</td>
      <td class="${r.stable_pct>50?'good':'warn'}">${r.stable_pct?.toFixed(0)||0}%</td>
    `;
    tbody.appendChild(row);
    document.getElementById('resultsWrap').classList.remove('hidden');
    log(`✓ ${r.recipe}: tensile=${r.tensile?.toFixed(3)}GPa stable=${r.stable_pct?.toFixed(0)}%`, 'ok');
  }

  else if (msg.type === 'done') {
    setRunning(false);
    setState('idle');
    redrawCharts();
    log('Run complete', 'ok');
  }

  else if (msg.type === 'error') {
    setRunning(false);
    setState('idle');
    log('Error: ' + msg.message, 'warn');
  }
};

function redrawCharts() {
  const canvases = {
    temp:    { id:'chartTemp',    color:'#FF8844', target: undefined },
    diam:    { id:'chartDiam',    color:'#4AACFF', target: currentTargets.diameter },
    tensile: { id:'chartTensile', color:'#FFAA00', target: currentTargets.tensile },
    thermal: { id:'chartThermal', color:'#4ADB7A', target: currentTargets.thermal },
  };
  Object.entries(canvases).forEach(([key, cfg]) => {
    drawChart(key, document.getElementById(cfg.id), cfg.color, cfg.target);
  });
}

// ── Log ──────────────────────────────────────────────────────────────────────
function log(msg, type='') {
  const list = document.getElementById('logList');
  const ts   = new Date().toTimeString().slice(0,8);
  const div  = document.createElement('div');
  div.className = 'log-entry ' + type;
  div.innerHTML = `<span class="ts">${ts}</span>${msg}`;
  list.appendChild(div);
  list.scrollTop = list.scrollHeight;
  if (list.children.length > 80) list.removeChild(list.firstChild);
}

// ── Init ─────────────────────────────────────────────────────────────────────
window.addEventListener('resize', redrawCharts);
log('Dashboard ready', 'ok');
log('Select a recipe and press RUN', '');
</script>
</body>
</html>'''


# ── SSE event queue ───────────────────────────────────────────────────────────
import queue
_event_queue: queue.Queue = queue.Queue()
_subscribers: list = []
_sub_lock = threading.Lock()


def broadcast(data: dict) -> None:
    msg = "data: " + json.dumps(data) + "\n\n"
    with _sub_lock:
        dead = []
        for q in _subscribers:
            try:
                q.put_nowait(msg)
            except queue.Full:
                dead.append(q)
        for q in dead:
            _subscribers.remove(q)


# ── HTTP handler ──────────────────────────────────────────────────────────────
class DashboardHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        pass  # suppress default access log

    def do_GET(self):
        if self.path == "/":
            self._html()
        elif self.path == "/stream":
            self._sse()
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        length  = int(self.headers.get("Content-Length", 0))
        body    = json.loads(self.rfile.read(length)) if length else {}
        if self.path == "/run":
            threading.Thread(
                target=run_simulation,
                args=(body.get("recipe", "structural"), body.get("steps", 1200)),
                daemon=True
            ).start()
            self._json({"ok": True})
        elif self.path == "/run_all":
            threading.Thread(
                target=run_all_recipes,
                args=(body.get("steps", 1200),),
                daemon=True
            ).start()
            self._json({"ok": True})
        else:
            self.send_response(404)
            self.end_headers()

    def _html(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(DASHBOARD_HTML.encode())

    def _json(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _sse(self):
        self.send_response(200)
        self.send_header("Content-Type",  "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection",    "keep-alive")
        self.end_headers()
        q: queue.Queue = queue.Queue(maxsize=200)
        with _sub_lock:
            _subscribers.append(q)
        try:
            while True:
                try:
                    msg = q.get(timeout=15)
                    self.wfile.write(msg.encode())
                    self.wfile.flush()
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with _sub_lock:
                if q in _subscribers:
                    _subscribers.remove(q)


# ── Simulation runner ─────────────────────────────────────────────────────────
def run_simulation(recipe_name: str, steps: int = 1200) -> dict:
    from qutlas.simulation.process  import ProcessSimulator
    from qutlas.control.controller  import AdaptiveController
    from qutlas.control.recipe_loader import RecipeLoader
    from qutlas.data_pipeline       import DataPipeline
    from qutlas.models.engine       import MaterialsEngine
    from qutlas.schema              import ControlAction

    broadcast({"type": "start", "recipe": recipe_name, "steps": steps})

    recipe     = RecipeLoader().load(recipe_name)
    sim        = ProcessSimulator(noise_level=0.02, dt=0.5)
    pipeline   = DataPipeline()
    engine     = MaterialsEngine(window_size=80, predict_every=8)
    controller = AdaptiveController()

    pipeline.on_synced(engine.on_reading)
    pipeline.on_synced(controller.on_reading)
    engine.on_prediction(controller.on_prediction)
    engine.set_recipe(recipe)
    pipeline.start()
    pipeline.reset_for_new_run()

    run = sim.start_run(recipe)
    controller.activate_recipe(recipe_name, run_id=run.run_id)

    last_reading = None
    stable_steps = 0
    decisions    = []
    controller.on_decision(decisions.append)

    broadcast_every = max(1, steps // 150)

    for step in range(steps):
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

        pred = engine.latest_prediction
        if pred and pred.within_tolerance:
            stable_steps += 1

        if step % broadcast_every == 0:
            sp_temp = controller.setpoint.furnace_temp_c if controller.latest_decision else recipe.initial_temp_c
            payload = {
                "step":  step,
                "pct":   round(step / steps * 100, 1),
                "state": controller.state.value,
                "temp":  last_reading.furnace_temp_c,
                "diam":  last_reading.fiber_diameter_um,
                "speed": last_reading.draw_speed_ms,
                "visc":  last_reading.melt_viscosity_cp,
                "sp":    sp_temp,
            }
            if pred:
                payload.update({
                    "tensile":    pred.tensile_strength_gpa,
                    "modulus":    pred.elastic_modulus_gpa,
                    "thermal":    pred.thermal_stability_c,
                    "cv":         pred.diameter_cv_pct,
                    "confidence": pred.confidence,
                    "within_tol": pred.within_tolerance,
                    "pred_count": engine.prediction_count,
                    "adj_count":  controller.stats["total_adjustments"],
                })
            broadcast({"type": "step", "data": payload})
            time.sleep(0.01)

    completed = sim.complete_run()
    pipeline.stop()

    stable_pct = round(stable_steps / max(engine.prediction_count, 1) * 100, 1)
    result = {
        "recipe":     recipe_name,
        "tensile":    completed.outcome_tensile_gpa,
        "modulus":    completed.outcome_modulus_gpa,
        "thermal":    completed.outcome_thermal_c,
        "cv":         completed.outcome_diameter_cv,
        "stable_pct": stable_pct,
    }
    broadcast({"type": "result", "data": result})
    return result


def run_all_recipes(steps: int = 1200) -> None:
    recipes = [
        "structural", "high_temperature", "electrical_insulation",
        "corrosion_resistant", "precision_structural"
    ]
    for recipe in recipes:
        run_simulation(recipe, steps)
    broadcast({"type": "done"})


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Qutlas Operator Dashboard")
    parser.add_argument("--port",   type=int, default=5050, help="Port (default: 5050)")
    parser.add_argument("--no-browser", action="store_true", help="Don't open browser")
    args = parser.parse_args()

    server = HTTPServer(("0.0.0.0", args.port), DashboardHandler)

    print(f"\n  Qutlas Dashboard")
    print(f"  Running at http://localhost:{args.port}")
    print(f"  Press Ctrl+C to stop\n")

    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(f"http://localhost:{args.port}")).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Dashboard stopped.")


if __name__ == "__main__":
    main()
