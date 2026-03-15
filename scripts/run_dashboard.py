#!/usr/bin/env python3
"""Qutlas Operator Dashboard v2 — specification-driven, interactive."""
from __future__ import annotations
import argparse,json,queue,sys,threading,time,webbrowser
from datetime import datetime,UTC
from http.server import BaseHTTPRequestHandler,HTTPServer
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent.parent))

HTML="""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Qutlas · Operator Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@300;400;500&family=IBM+Plex+Sans:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --navy:#1C2166;--navyD:#0F1240;--navyM:#232880;
  --amber:#FFAA00;--cream:#F0EDE6;--white:#FFFFFF;
  --dim:#8890BB;--rule:rgba(255,255,255,0.07);
  --green:#1A8A4A;--greenL:#4ADB7A;--red:#CC2233;--redL:#FF4455;--blue:#4AACFF;
  --mono:'IBM Plex Mono',monospace;--sans:'IBM Plex Sans',sans-serif;
}
html,body{height:100%;background:var(--navyD);color:var(--cream);font-family:var(--sans);font-size:13px;overflow:hidden}
.shell{display:grid;grid-template-rows:44px 1fr;height:100vh}
header{background:var(--navy);border-bottom:1px solid var(--rule);display:flex;align-items:center;padding:0 16px;gap:14px}
.logo{font-family:var(--mono);font-size:13px;font-weight:500;letter-spacing:.14em;color:var(--white)}
.logo b{color:var(--amber)}.hdr-sep{flex:1}
.state-pill{font-family:var(--mono);font-size:9px;letter-spacing:.12em;padding:3px 9px;border-radius:2px;font-weight:500;transition:all .3s}
.s-idle{background:rgba(136,144,187,.12);color:var(--dim)}
.s-warming{background:rgba(255,170,0,.14);color:var(--amber)}
.s-converging{background:rgba(74,172,255,.14);color:var(--blue)}
.s-stable{background:rgba(26,138,74,.18);color:var(--greenL)}
.s-aborted{background:rgba(204,34,51,.18);color:var(--redL)}
.dot{width:6px;height:6px;border-radius:50%;background:var(--dim)}
.dot.on{background:var(--amber);animation:blink 1.6s ease-in-out infinite}
@keyframes blink{0%,100%{opacity:.25}50%{opacity:1}}
.hdr-info{font-family:var(--mono);font-size:9px;color:var(--dim);letter-spacing:.1em}
.main{display:grid;grid-template-columns:300px 1fr 280px;gap:1px;background:var(--rule);overflow:hidden}
.col{background:var(--navyD);display:flex;flex-direction:column;overflow:hidden}
.ph{padding:9px 14px 8px;border-bottom:1px solid var(--rule);font-family:var(--mono);font-size:9px;letter-spacing:.14em;color:var(--dim);flex-shrink:0;display:flex;align-items:center;gap:8px}
.ph-badge{background:var(--amber);color:var(--navyD);font-size:8px;padding:1px 6px;letter-spacing:.1em;font-weight:500;border-radius:1px}
.pb{flex:1;overflow-y:auto;padding:12px 14px;scrollbar-width:thin;scrollbar-color:var(--navyM) transparent}
.sec{font-family:var(--mono);font-size:8px;letter-spacing:.16em;color:var(--amber);margin:14px 0 7px;font-weight:500}
.sec:first-child{margin-top:0}
.field{margin-bottom:8px}
.field label{display:block;font-family:var(--mono);font-size:8px;letter-spacing:.12em;color:var(--dim);margin-bottom:4px}
.field input,.field select{width:100%;background:var(--navyM);border:1px solid var(--rule);color:var(--cream);font-family:var(--mono);font-size:11px;padding:7px 9px;outline:none;appearance:none;transition:border-color .15s}
.field input:focus,.field select:focus{border-color:var(--amber)}
.field .hint{font-family:var(--mono);font-size:8px;color:var(--dim);margin-top:3px;opacity:.7}
.sw{margin-bottom:10px}
.sw label{display:flex;justify-content:space-between;align-items:baseline;font-family:var(--mono);font-size:8px;letter-spacing:.12em;color:var(--dim);margin-bottom:5px}
.sw label span{color:var(--cream);font-size:10px}
input[type=range]{width:100%;height:3px;background:var(--navyM);appearance:none;outline:none;cursor:pointer;border-radius:2px}
input[type=range]::-webkit-slider-thumb{appearance:none;width:12px;height:12px;border-radius:50%;background:var(--amber);cursor:pointer;box-shadow:0 0 0 2px rgba(255,170,0,.2)}
.presets{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:10px}
.pbtn{font-family:var(--mono);font-size:9px;letter-spacing:.06em;padding:5px 10px;background:none;border:1px solid var(--rule);color:var(--dim);cursor:pointer;transition:all .15s}
.pbtn:hover{border-color:var(--amber);color:var(--cream)}
.pbtn.active{border-color:var(--amber);color:var(--amber);background:rgba(255,170,0,.07)}
.btn-run{width:100%;padding:11px;background:var(--amber);color:var(--navyD);border:none;font-family:var(--mono);font-size:11px;font-weight:500;letter-spacing:.1em;cursor:pointer;margin-top:10px;transition:background .15s,transform .1s}
.btn-run:hover{background:#FFB822;transform:translateY(-1px)}
.btn-run:disabled{background:var(--navyM);color:var(--dim);cursor:not-allowed;transform:none}
.btn-ghost{width:100%;padding:9px;background:none;border:1px solid rgba(255,170,0,.25);color:var(--amber);font-family:var(--mono);font-size:10px;letter-spacing:.08em;cursor:pointer;margin-top:5px;transition:all .15s}
.btn-ghost:hover{background:rgba(255,170,0,.07);border-color:var(--amber)}
.btn-ghost:disabled{opacity:.35;cursor:not-allowed}
.div{height:1px;background:var(--rule);margin:12px 0}
.computed-box{border:1px solid var(--rule);padding:10px 12px;margin-top:10px;display:none}
.computed-box.show{display:block}
.cb-head{font-family:var(--mono);font-size:8px;letter-spacing:.12em;color:var(--dim);margin-bottom:6px}
.prog-wrap{margin:8px 0 4px;display:none}
.prog-labels{display:flex;justify-content:space-between;font-family:var(--mono);font-size:8px;color:var(--dim);margin-bottom:4px;letter-spacing:.08em}
.prog-bar{height:2px;background:var(--navyM);overflow:hidden}
.prog-fill{height:100%;background:var(--amber);width:0;transition:width .3s}
.charts-grid{display:grid;grid-template-columns:1fr 1fr;grid-template-rows:1fr 1fr;gap:1px;background:var(--rule);height:100%}
.chart-cell{background:var(--navyD);display:flex;flex-direction:column;padding:10px 12px 8px;min-height:0}
.chart-title{font-family:var(--mono);font-size:9px;color:var(--dim);letter-spacing:.12em;margin-bottom:4px;flex-shrink:0;display:flex;justify-content:space-between;align-items:baseline}
.chart-cur{font-size:11px;color:var(--amber);font-weight:500}
.chart-tgt{font-size:9px;color:rgba(255,255,255,.2)}
canvas{flex:1;min-height:0;width:100%!important}
.readout{display:flex;justify-content:space-between;align-items:baseline;padding:6px 0;border-bottom:1px solid rgba(255,255,255,.04)}
.rl{font-family:var(--mono);font-size:9px;color:var(--dim);letter-spacing:.08em}
.rv{font-family:var(--mono);font-size:12px;color:var(--cream);font-weight:400;transition:color .3s}
.rv.amber{color:var(--amber)}.rv.green{color:var(--greenL)}.rv.red{color:var(--redL)}
.pred-big{margin-bottom:12px}
.pred-big .pl{font-family:var(--mono);font-size:8px;letter-spacing:.14em;color:var(--dim);margin-bottom:2px}
.pred-big .pv{font-family:var(--mono);font-size:22px;font-weight:400;color:var(--cream);line-height:1;transition:color .3s}
.pred-big .pv .u{font-size:11px;color:var(--dim);margin-left:3px}
.pred-big .pt{font-family:var(--mono);font-size:8px;color:var(--dim);margin-top:2px}
.pt .hit{color:var(--greenL)}.pt .miss{color:#FF8855}
.conf-bar{height:2px;background:var(--navyM);margin-top:4px;overflow:hidden}
.conf-fill{height:100%;background:var(--amber);transition:width .5s}
.log-box{flex:1;overflow-y:auto;scrollbar-width:thin;scrollbar-color:var(--navyM) transparent}
.ll{font-family:var(--mono);font-size:9px;color:var(--dim);padding:2px 0;border-bottom:1px solid rgba(255,255,255,.02);line-height:1.6}
.ll .ts{color:rgba(136,144,187,.4);margin-right:5px}
.ll.ok{color:var(--greenL)}.ll.warn{color:#FF8855}.ll.info{color:var(--amber)}
.rt{width:100%;border-collapse:collapse;font-family:var(--mono);font-size:9px;margin-top:6px}
.rt th{color:var(--dim);letter-spacing:.1em;padding:3px 5px;text-align:left;border-bottom:1px solid var(--rule);font-weight:400}
.rt td{padding:4px 5px;color:var(--cream);border-bottom:1px solid rgba(255,255,255,.03)}
.rt .g{color:var(--greenL)}.rt .w{color:#FF8855}
.hidden{display:none!important}
</style></head><body>
<div class="shell">
<header>
  <div class="logo"><b>Q</b>UTLAS</div>
  <div class="state-pill s-idle" id="statePill">IDLE</div>
  <div class="dot" id="dot"></div>
  <div class="hdr-sep"></div>
  <div class="hdr-info">PROGRAMMABLE MATERIALS MANUFACTURING &nbsp;·&nbsp; PHASE 1 SIMULATOR</div>
</header>
<div class="main">

<!-- LEFT -->
<div class="col">
<div class="ph">MATERIAL SPECIFICATION <span class="ph-badge">LIVE</span></div>
<div class="pb">
  <div class="sec">INDUSTRY PRESETS</div>
  <div class="presets">
    <button class="pbtn" onclick="applyPreset('telecom',this)">Telecom</button>
    <button class="pbtn" onclick="applyPreset('oilgas',this)">Oil &amp; Gas</button>
    <button class="pbtn" onclick="applyPreset('aerospace',this)">Aerospace</button>
    <button class="pbtn" onclick="applyPreset('construction',this)">Construction</button>
    <button class="pbtn" onclick="applyPreset('robotics',this)">Robotics</button>
    <button class="pbtn" onclick="applyPreset('ev',this)">EV / Auto</button>
  </div>

  <div class="sec">TARGET MATERIAL PROPERTIES</div>

  <div class="sw">
    <label>TENSILE STRENGTH (GPa) <span id="vTensile">2.9</span></label>
    <input type="range" id="sTensile" min="2.0" max="3.4" step="0.05" value="2.9" oninput="sl('tensile')">
    <div style="display:flex;justify-content:space-between;font-family:var(--mono);font-size:8px;color:var(--dim);margin-top:2px"><span>2.0</span><span>GPa</span><span>3.4</span></div>
  </div>
  <div class="sw">
    <label>ELASTIC MODULUS (GPa) <span id="vModulus">85</span></label>
    <input type="range" id="sModulus" min="70" max="95" step="1" value="85" oninput="sl('modulus')">
    <div style="display:flex;justify-content:space-between;font-family:var(--mono);font-size:8px;color:var(--dim);margin-top:2px"><span>70</span><span>GPa</span><span>95</span></div>
  </div>
  <div class="sw">
    <label>THERMAL STABILITY (°C) <span id="vThermal">650</span></label>
    <input type="range" id="sThermal" min="550" max="820" step="10" value="650" oninput="sl('thermal')">
    <div style="display:flex;justify-content:space-between;font-family:var(--mono);font-size:8px;color:var(--dim);margin-top:2px"><span>550°C</span><span>service temp</span><span>820°C</span></div>
  </div>
  <div class="sw">
    <label>FIBER DIAMETER (µm) <span id="vDiameter">13</span></label>
    <input type="range" id="sDiameter" min="8" max="20" step="0.5" value="13" oninput="sl('diameter')">
    <div style="display:flex;justify-content:space-between;font-family:var(--mono);font-size:8px;color:var(--dim);margin-top:2px"><span>8µm fine</span><span>diameter</span><span>20µm coarse</span></div>
  </div>

  <div class="sec">APPLICATION CONTEXT</div>
  <div class="field">
    <label>APPLICATION TYPE</label>
    <select id="appType" onchange="onApp()">
      <option value="">Select...</option>
      <option value="enclosure">Hardware enclosure / housing</option>
      <option value="structural">Structural reinforcement</option>
      <option value="thermal">Thermal insulation</option>
      <option value="electrical">Electrical insulation</option>
      <option value="composite">Composite layup</option>
      <option value="rebar">Rebar / civil reinforcement</option>
      <option value="cable">Cable protection / conduit</option>
      <option value="custom">Custom specification</option>
    </select>
  </div>
  <div class="field">
    <label>OPERATING ENVIRONMENT</label>
    <select id="envType" onchange="updateRecipe()">
      <option value="standard">Standard (indoor, dry)</option>
      <option value="outdoor">Outdoor / UV exposure</option>
      <option value="marine">Marine / salt water</option>
      <option value="chemical">Chemical / acid exposure</option>
      <option value="high_temp">High temperature (&gt;400°C)</option>
      <option value="rf">RF / electromagnetic sensitive</option>
    </select>
  </div>
  <div class="field">
    <label>SIMULATION STEPS</label>
    <input type="number" id="stepsInput" value="1200" min="400" max="4000" step="200">
    <div class="hint">Higher = more convergence time</div>
  </div>

  <div class="computed-box" id="cbox">
    <div class="cb-head">COMPUTED RECIPE</div>
    <div id="ctext" style="font-family:var(--mono);font-size:10px;color:var(--cream);line-height:1.9"></div>
  </div>

  <button class="btn-run" id="runBtn" onclick="runSpec()">&#9654;&nbsp; SYNTHESISE MATERIAL</button>
  <button class="btn-ghost" id="runAllBtn" onclick="runAll()">RUN ALL INDUSTRY PRESETS</button>

  <div class="prog-wrap" id="progWrap">
    <div class="prog-labels">
      <span id="progLabel">RUNNING</span><span id="progPct">0%</span>
    </div>
    <div class="prog-bar"><div class="prog-fill" id="progFill"></div></div>
  </div>
</div></div>

<!-- CENTRE -->
<div class="col" style="padding:0">
<div class="charts-grid">
  <div class="chart-cell">
    <div class="chart-title">FURNACE TEMPERATURE (°C)<span class="chart-cur" id="curTemp">—</span></div>
    <canvas id="cTemp"></canvas>
  </div>
  <div class="chart-cell">
    <div class="chart-title">FIBER DIAMETER (µm)<span><span class="chart-tgt" id="tDiam"></span><span class="chart-cur" id="curDiam">—</span></span></div>
    <canvas id="cDiam"></canvas>
  </div>
  <div class="chart-cell">
    <div class="chart-title">TENSILE PREDICTION (GPa)<span><span class="chart-tgt" id="tTensile"></span><span class="chart-cur" id="curTensile">—</span></span></div>
    <canvas id="cTensile"></canvas>
  </div>
  <div class="chart-cell">
    <div class="chart-title">THERMAL STABILITY (°C)<span><span class="chart-tgt" id="tThermal"></span><span class="chart-cur" id="curThermal">—</span></span></div>
    <canvas id="cThermal"></canvas>
  </div>
</div></div>

<!-- RIGHT -->
<div class="col">
<div class="ph">LIVE OUTPUT</div>
<div class="pb" style="display:flex;flex-direction:column">
  <div class="sec" style="margin-top:0">PREDICTIONS</div>
  <div class="pred-big">
    <div class="pl">TENSILE STRENGTH</div>
    <div class="pv" id="pTensile">—<span class="u">GPa</span></div>
    <div class="pt" id="pTT"></div>
    <div class="conf-bar"><div class="conf-fill" id="confFill" style="width:0"></div></div>
  </div>
  <div class="pred-big">
    <div class="pl">ELASTIC MODULUS</div>
    <div class="pv" id="pModulus">—<span class="u">GPa</span></div>
  </div>
  <div class="pred-big">
    <div class="pl">THERMAL STABILITY</div>
    <div class="pv" id="pThermal">—<span class="u">°C</span></div>
  </div>
  <div class="pred-big">
    <div class="pl">DIAMETER CV</div>
    <div class="pv" id="pCV">—<span class="u">%</span></div>
  </div>
  <div class="div"></div>
  <div class="readout"><span class="rl">FURNACE TEMP</span><span class="rv amber" id="rTemp">—</span></div>
  <div class="readout"><span class="rl">FIBER DIAMETER</span><span class="rv" id="rDiam">—</span></div>
  <div class="readout"><span class="rl">DRAW SPEED</span><span class="rv" id="rSpeed">—</span></div>
  <div class="readout"><span class="rl">VISCOSITY</span><span class="rv" id="rVisc">—</span></div>
  <div class="readout"><span class="rl">TEMP SETPOINT</span><span class="rv" id="rSP">—</span></div>
  <div class="div"></div>
  <div class="readout"><span class="rl">PREDICTIONS MADE</span><span class="rv" id="mPreds">0</span></div>
  <div class="readout"><span class="rl">ADJUSTMENTS</span><span class="rv" id="mAdj">0</span></div>
  <div class="readout"><span class="rl">WITHIN TOLERANCE</span><span class="rv" id="mTol">—</span></div>
  <div class="readout"><span class="rl">CONFIDENCE</span><span class="rv" id="mConf">—</span></div>
  <div class="div"></div>
  <div id="resultsWrap" class="hidden">
    <div class="sec" style="margin-top:0">COMPLETED RUNS</div>
    <table class="rt"><thead><tr><th>RECIPE</th><th>TENSILE</th><th>THERMAL</th><th>STABLE</th></tr></thead>
    <tbody id="resultsBody"></tbody></table>
    <div class="div"></div>
  </div>
  <div class="sec" style="margin-top:0">SYSTEM LOG</div>
  <div class="log-box" id="logBox"></div>
</div></div>

</div></div>
<script>
const MAX=200;
const cd={temp:[],diam:[],tensile:[],thermal:[]};
const spec={tensile:2.9,modulus:85,thermal:650,diameter:13};
let targets={tensile:2.9,thermal:650,diameter:13};

const PRESETS={
  telecom:     {tensile:2.6,modulus:80,thermal:620,diameter:10,app:'enclosure',env:'rf'},
  oilgas:      {tensile:2.7,modulus:82,thermal:640,diameter:14,app:'cable',env:'marine'},
  aerospace:   {tensile:2.5,modulus:78,thermal:760,diameter:11,app:'thermal',env:'high_temp'},
  construction:{tensile:2.9,modulus:85,thermal:650,diameter:13,app:'rebar',env:'standard'},
  robotics:    {tensile:3.1,modulus:90,thermal:660,diameter:9, app:'enclosure',env:'standard'},
  ev:          {tensile:2.8,modulus:83,thermal:700,diameter:12,app:'enclosure',env:'high_temp'},
};

function cap(s){return s.charAt(0).toUpperCase()+s.slice(1)}
function sl(k){
  const v=parseFloat(document.getElementById('s'+cap(k)).value);
  spec[k]=v;
  document.getElementById('v'+cap(k)).textContent=v;
  updateRecipe();updateTgtLabels();
  log(`${k}: ${v}`,'info');
}
function updateTgtLabels(){
  document.getElementById('tDiam').textContent=`target ${spec.diameter}µm · `;
  document.getElementById('tTensile').textContent=`target ${spec.tensile}GPa · `;
  document.getElementById('tThermal').textContent=`target ${spec.thermal}°C · `;
}
updateTgtLabels();

function applyPreset(k,btn){
  const p=PRESETS[k];
  document.querySelectorAll('.pbtn').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  ['tensile','modulus','thermal','diameter'].forEach(key=>{
    document.getElementById('s'+cap(key)).value=p[key];
    spec[key]=p[key];
    document.getElementById('v'+cap(key)).textContent=p[key];
  });
  if(p.app)document.getElementById('appType').value=p.app;
  if(p.env)document.getElementById('envType').value=p.env;
  updateRecipe();updateTgtLabels();
  log(`Preset: ${k}`,'info');
}

function onApp(){
  const m={enclosure:'rf',structural:'standard',thermal:'high_temp',
    electrical:'rf',composite:'standard',rebar:'standard',cable:'marine',custom:'standard'};
  const v=document.getElementById('appType').value;
  if(m[v])document.getElementById('envType').value=m[v];
  updateRecipe();
}

function computeRecipeName(){
  if(spec.thermal>=720)return'high_temperature';
  if(spec.diameter<=10)return'precision_structural';
  const env=document.getElementById('envType').value;
  if(env==='marine'||env==='chemical')return'corrosion_resistant';
  if(env==='rf')return'electrical_insulation';
  if(spec.tensile>=3.0)return'precision_structural';
  return'structural';
}

function updateRecipe(){
  const r=computeRecipeName();
  const names={structural:'Structural Reinforcement (SF-01)',high_temperature:'High Temperature (HT-02)',
    electrical_insulation:'Electrical Insulation (EI-03)',corrosion_resistant:'Corrosion Resistant (CR-04)',
    precision_structural:'Precision Structural (PS-05)'};
  const temps={structural:1480,high_temperature:1540,electrical_insulation:1460,
    corrosion_resistant:1470,precision_structural:1500};
  document.getElementById('ctext').innerHTML=
    '<b style="color:var(--amber)">'+names[r]+'</b><br>'+
    'Furnace target: '+temps[r]+'°C<br>'+
    'Target tensile: '+spec.tensile+' GPa<br>'+
    'Target thermal: '+spec.thermal+'°C<br>'+
    'Target diameter: '+spec.diameter+' µm';
  document.getElementById('cbox').classList.add('show');
  targets={tensile:spec.tensile,thermal:spec.thermal,diameter:spec.diameter};
}
updateRecipe();

function runSpec(){
  const r=computeRecipeName();
  const s=parseInt(document.getElementById('stepsInput').value)||1200;
  clearCharts();
  document.getElementById('resultsWrap').classList.add('hidden');
  fetch('/run',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({recipe:r,steps:s,
      custom_tensile:spec.tensile,custom_thermal:spec.thermal,custom_diameter:spec.diameter})});
}
function runAll(){
  const s=parseInt(document.getElementById('stepsInput').value)||1200;
  clearCharts();
  document.getElementById('resultsBody').innerHTML='';
  document.getElementById('resultsWrap').classList.add('hidden');
  fetch('/run_all',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({steps:s})});
}

function setRunning(v){
  document.getElementById('runBtn').disabled=v;
  document.getElementById('runAllBtn').disabled=v;
  document.getElementById('progWrap').style.display=v?'block':'none';
  document.getElementById('dot').className=v?'dot on':'dot';
  if(!v){document.getElementById('progFill').style.width='0%';document.getElementById('progPct').textContent='0%';}
}
function setProgress(pct,lbl){
  document.getElementById('progFill').style.width=pct+'%';
  document.getElementById('progPct').textContent=Math.round(pct)+'%';
  if(lbl)document.getElementById('progLabel').textContent=lbl.toUpperCase();
}
function setState(s){
  const pill=document.getElementById('statePill');
  const m={idle:'s-idle',warming:'s-warming',converging:'s-converging',stable:'s-stable',aborted:'s-aborted'};
  pill.className='state-pill '+(m[s]||'s-idle');
  pill.textContent=s.toUpperCase();
}

const es=new EventSource('/stream');
es.onmessage=e=>{
  const msg=JSON.parse(e.data);
  if(msg.type==='start'){setRunning(true);setState('warming');log('Started: '+msg.recipe,'info');}
  else if(msg.type==='step'){
    const d=msg.data;
    setState(d.state||'converging');setProgress(d.pct||0,d.state);
    if(d.temp!=null){document.getElementById('rTemp').textContent=d.temp.toFixed(1)+' °C';}
    if(d.diam!=null&&d.diam>1){document.getElementById('rDiam').textContent=d.diam.toFixed(2)+' µm';}
    if(d.speed!=null)document.getElementById('rSpeed').textContent=d.speed.toFixed(2)+' m/s';
    if(d.visc!=null)document.getElementById('rVisc').textContent=d.visc.toFixed(0)+' cP';
    if(d.sp!=null)document.getElementById('rSP').textContent=d.sp.toFixed(1)+' °C';
    if(d.temp!=null){cd.temp.push(d.temp);document.getElementById('curTemp').textContent=d.temp.toFixed(1)+'°C';}
    if(d.diam!=null&&d.diam>1){cd.diam.push(d.diam);document.getElementById('curDiam').textContent=d.diam.toFixed(2)+'µm';}
    if(d.tensile!=null){
      cd.tensile.push(d.tensile);
      document.getElementById('curTensile').textContent=d.tensile.toFixed(3)+'GPa';
      document.getElementById('pTensile').innerHTML=d.tensile.toFixed(3)+'<span class="u">GPa</span>';
      const hit=Math.abs(d.tensile-targets.tensile)<0.3;
      document.getElementById('pTT').innerHTML='Target: '+targets.tensile+' GPa &nbsp;<span class="'+(hit?'hit':'miss')+'">'+(hit?'✓ within range':'⟳ converging')+'</span>';
      document.getElementById('pTensile').style.color=hit?'var(--greenL)':'var(--cream)';
    }
    if(d.thermal!=null){
      cd.thermal.push(d.thermal);
      document.getElementById('curThermal').textContent=d.thermal.toFixed(0)+'°C';
      document.getElementById('pThermal').innerHTML=d.thermal.toFixed(0)+'<span class="u">°C</span>';
    }
    if(d.modulus!=null)document.getElementById('pModulus').innerHTML=d.modulus.toFixed(1)+'<span class="u">GPa</span>';
    if(d.cv!=null)document.getElementById('pCV').innerHTML=d.cv.toFixed(3)+'<span class="u">%</span>';
    if(d.confidence!=null){
      document.getElementById('confFill').style.width=(d.confidence*100)+'%';
      document.getElementById('mConf').textContent=(d.confidence*100).toFixed(0)+'%';
    }
    if(d.within_tol!=null){
      const el=document.getElementById('mTol');
      el.textContent=d.within_tol?'✓ YES':'NO';
      el.className='rv '+(d.within_tol?'green':'');
    }
    if(d.pred_count!=null)document.getElementById('mPreds').textContent=d.pred_count;
    if(d.adj_count!=null)document.getElementById('mAdj').textContent=d.adj_count;
    if(d.step%5===0)redraw();
  }
  else if(msg.type==='result'){
    const r=msg.data;
    const tbody=document.getElementById('resultsBody');
    const hit=r.tensile&&Math.abs(r.tensile-targets.tensile)<0.3;
    const tr=document.createElement('tr');
    tr.innerHTML='<td>'+r.recipe.replace(/_/g,' ')+'</td>'+
      '<td class="'+(hit?'g':'w')+'">'+(r.tensile?.toFixed(3)||'—')+'</td>'+
      '<td>'+(r.thermal?.toFixed(0)||'—')+'</td>'+
      '<td class="'+(r.stable_pct>50?'g':'w')+'">'+(r.stable_pct?.toFixed(0)||0)+'%</td>';
    tbody.appendChild(tr);
    document.getElementById('resultsWrap').classList.remove('hidden');
    log('✓ '+r.recipe+': '+r.tensile?.toFixed(3)+'GPa · '+r.stable_pct?.toFixed(0)+'% stable','ok');
  }
  else if(msg.type==='done'){setRunning(false);setState('idle');redraw();log('All runs complete','ok');}
  else if(msg.type==='error'){setRunning(false);setState('idle');log('Error: '+msg.message,'warn');}
};

function redraw(){
  drawChart('cTemp',   cd.temp,   '#FF8844',undefined);
  drawChart('cDiam',   cd.diam,   '#4AACFF',targets.diameter);
  drawChart('cTensile',cd.tensile,'#FFAA00',targets.tensile);
  drawChart('cThermal',cd.thermal,'#4ADB7A',targets.thermal);
}
function drawChart(id,data,color,target){
  const canvas=document.getElementById(id);
  const ctx=canvas.getContext('2d');
  const W=canvas.width=canvas.offsetWidth*devicePixelRatio;
  const H=canvas.height=canvas.offsetHeight*devicePixelRatio;
  ctx.clearRect(0,0,W,H);
  if(data.length<2)return;
  const pad={t:6,r:6,b:18,l:42};
  const pw=W-pad.l-pad.r,ph=H-pad.t-pad.b;
  const vals=data.slice(-MAX);
  const ymin=Math.min(...vals)*0.97,ymax=Math.max(...vals)*1.03||1;
  const xStep=pw/(MAX-1);
  const toX=i=>pad.l+i*xStep;
  const toY=v=>pad.t+ph-((v-ymin)/(ymax-ymin||1))*ph;
  ctx.strokeStyle='rgba(255,255,255,0.05)';ctx.lineWidth=1;
  for(let i=0;i<=3;i++){
    const y=pad.t+(ph/3)*i;
    ctx.beginPath();ctx.moveTo(pad.l,y);ctx.lineTo(pad.l+pw,y);ctx.stroke();
    const v=ymax-((ymax-ymin)/3)*i;
    ctx.fillStyle='rgba(136,144,187,0.55)';
    ctx.font=(9*devicePixelRatio)+'px IBM Plex Mono,monospace';
    ctx.textAlign='right';
    ctx.fillText(v.toFixed(1),pad.l-3,y+3*devicePixelRatio);
  }
  if(target!=null){
    const ty=toY(target);
    if(ty>=pad.t&&ty<=pad.t+ph){
      ctx.save();ctx.strokeStyle='rgba(255,170,0,0.35)';ctx.lineWidth=1;
      ctx.setLineDash([3*devicePixelRatio,4*devicePixelRatio]);
      ctx.beginPath();ctx.moveTo(pad.l,ty);ctx.lineTo(pad.l+pw,ty);ctx.stroke();ctx.restore();
    }
  }
  ctx.beginPath();
  vals.forEach((v,i)=>{i===0?ctx.moveTo(toX(i),toY(v)):ctx.lineTo(toX(i),toY(v));});
  ctx.lineTo(toX(vals.length-1),pad.t+ph);ctx.lineTo(toX(0),pad.t+ph);ctx.closePath();
  const r=parseInt(color.slice(1,3),16),g=parseInt(color.slice(3,5),16),b=parseInt(color.slice(5,7),16);
  const grad=ctx.createLinearGradient(0,pad.t,0,pad.t+ph);
  grad.addColorStop(0,`rgba(${r},${g},${b},0.18)`);
  grad.addColorStop(1,`rgba(${r},${g},${b},0.0)`);
  ctx.fillStyle=grad;ctx.fill();
  ctx.beginPath();ctx.strokeStyle=color;ctx.lineWidth=1.5*devicePixelRatio;
  vals.forEach((v,i)=>{i===0?ctx.moveTo(toX(i),toY(v)):ctx.lineTo(toX(i),toY(v));});
  ctx.stroke();
}
function clearCharts(){
  Object.keys(cd).forEach(k=>cd[k]=[]);
  ['curTemp','curDiam','curTensile','curThermal'].forEach(id=>document.getElementById(id).textContent='—');
  ['pTensile','pModulus','pThermal','pCV'].forEach(id=>{
    const u=id==='pTensile'||id==='pModulus'?'GPa':id==='pThermal'?'°C':'%';
    document.getElementById(id).innerHTML='—<span class="u">'+u+'</span>';
  });
  document.getElementById('confFill').style.width='0';
  document.getElementById('pTensile').style.color='var(--cream)';
}
function log(msg,type=''){
  const box=document.getElementById('logBox');
  const ts=new Date().toTimeString().slice(0,8);
  const div=document.createElement('div');
  div.className='ll '+type;
  div.innerHTML='<span class="ts">'+ts+'</span>'+msg;
  box.appendChild(div);box.scrollTop=box.scrollHeight;
  if(box.children.length>100)box.removeChild(box.firstChild);
}
window.addEventListener('resize',redraw);
log('Dashboard ready','ok');
log('Adjust sliders or pick a preset, then press Synthesise','');
</script></body></html>"""

_subs=[];_sub_lock=threading.Lock()

def broadcast(data):
    msg="data: "+json.dumps(data)+"\n\n"
    with _sub_lock:
        dead=[]
        for q in _subs:
            try: q.put_nowait(msg)
            except queue.Full: dead.append(q)
        for q in dead: _subs.remove(q)

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def do_GET(self):
        if self.path=="/":
            self.send_response(200);self.send_header("Content-Type","text/html; charset=utf-8");self.end_headers()
            self.wfile.write(HTML.encode())
        elif self.path=="/stream":
            self.send_response(200);self.send_header("Content-Type","text/event-stream")
            self.send_header("Cache-Control","no-cache");self.send_header("Connection","keep-alive");self.end_headers()
            q=queue.Queue(maxsize=300)
            with _sub_lock: _subs.append(q)
            try:
                while True:
                    try: self.wfile.write(q.get(timeout=15).encode());self.wfile.flush()
                    except queue.Empty: self.wfile.write(b": ping\n\n");self.wfile.flush()
            except (BrokenPipeError,ConnectionResetError): pass
            finally:
                with _sub_lock:
                    if q in _subs: _subs.remove(q)
        else: self.send_response(404);self.end_headers()
    def do_POST(self):
        n=int(self.headers.get("Content-Length",0))
        body=json.loads(self.rfile.read(n)) if n else {}
        if self.path=="/run":
            threading.Thread(target=run_sim,args=(body.get("recipe","structural"),body.get("steps",1200),body),daemon=True).start()
        elif self.path=="/run_all":
            threading.Thread(target=run_all_sims,args=(body.get("steps",1200),),daemon=True).start()
        self.send_response(200);self.send_header("Content-Type","application/json");self.end_headers()
        self.wfile.write(b'{"ok":true}')

def run_sim(recipe_name,steps,opts={}):
    from qutlas.simulation.process import ProcessSimulator
    from qutlas.control.controller import AdaptiveController
    from qutlas.control.recipe_loader import RecipeLoader
    from qutlas.data_pipeline import DataPipeline
    from qutlas.models.engine import MaterialsEngine
    from qutlas.schema import ControlAction
    import dataclasses
    broadcast({"type":"start","recipe":recipe_name,"steps":steps})
    base=RecipeLoader().load(recipe_name)
    ov={}
    if opts.get("custom_tensile"):  ov["target_tensile_gpa"]=opts["custom_tensile"]
    if opts.get("custom_thermal"):  ov["target_thermal_c"]=opts["custom_thermal"]
    if opts.get("custom_diameter"): ov["target_diameter_um"]=opts["custom_diameter"]
    recipe=dataclasses.replace(base,**ov) if ov else base
    sim=ProcessSimulator(noise_level=0.02,dt=0.5)
    pipeline=DataPipeline()
    engine=MaterialsEngine(window_size=80,predict_every=8)
    controller=AdaptiveController()
    pipeline.on_synced(engine.on_reading)
    pipeline.on_synced(controller.on_reading)
    engine.on_prediction(controller.on_prediction)
    engine.set_recipe(recipe)
    pipeline.start();pipeline.reset_for_new_run()
    run=sim.start_run(recipe)
    controller.activate_recipe(recipe_name,run_id=run.run_id)
    stable_steps=0;every=max(1,steps//160)
    for step in range(steps):
        decision=controller.latest_decision
        if decision and step>5:
            sp=decision.setpoint
            action=ControlAction(timestamp=datetime.now(UTC),run_id=run.run_id,
                furnace_temp_setpoint_c=sp.furnace_temp_c,
                draw_speed_setpoint_ms=sp.draw_speed_ms,
                cooling_airflow_setpoint=sp.airflow_lpm)
        else: action=None
        lr=sim.step(action);lr.run_id=run.run_id;pipeline.ingest(lr)
        pred=engine.latest_prediction
        if pred and pred.within_tolerance: stable_steps+=1
        if step%every==0:
            payload={"step":step,"pct":round(step/steps*100,1),"state":controller.state.value,
                "temp":lr.furnace_temp_c,"diam":lr.fiber_diameter_um,
                "speed":lr.draw_speed_ms,"visc":lr.melt_viscosity_cp,
                "sp":controller.setpoint.furnace_temp_c}
            if pred:
                payload.update({"tensile":pred.tensile_strength_gpa,"modulus":pred.elastic_modulus_gpa,
                    "thermal":pred.thermal_stability_c,"cv":pred.diameter_cv_pct,
                    "confidence":pred.confidence,"within_tol":pred.within_tolerance,
                    "pred_count":engine.prediction_count,"adj_count":controller.stats["total_adjustments"]})
            broadcast({"type":"step","data":payload});time.sleep(0.008)
    completed=sim.complete_run();pipeline.stop()
    stable_pct=round(stable_steps/max(engine.prediction_count,1)*100,1)
    broadcast({"type":"result","data":{"recipe":recipe_name,"tensile":completed.outcome_tensile_gpa,
        "modulus":completed.outcome_modulus_gpa,"thermal":completed.outcome_thermal_c,
        "cv":completed.outcome_diameter_cv,"stable_pct":stable_pct}})
    return completed

def run_all_sims(steps):
    for r in ["structural","high_temperature","electrical_insulation","corrosion_resistant","precision_structural"]:
        run_sim(r,steps)
    broadcast({"type":"done"})

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--port",type=int,default=5050)
    parser.add_argument("--no-browser",action="store_true")
    args=parser.parse_args()
    server=HTTPServer(("0.0.0.0",args.port),Handler)
    print(f"\n  Qutlas Operator Dashboard\n  http://localhost:{args.port}\n")
    if not args.no_browser:
        threading.Timer(0.8,lambda:webbrowser.open(f"http://localhost:{args.port}")).start()
    try: server.serve_forever()
    except KeyboardInterrupt: print("\n  Stopped.")

if __name__=="__main__": main()
