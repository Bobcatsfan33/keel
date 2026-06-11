"""The viewer's single-file SPA. Dependency-free vanilla JS so ``keel view`` works
with no build step (the React/Vite bundle in ADR-006 is a later swap behind the
same JSON API)."""
from __future__ import annotations

INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>KEEL viewer</title>
<style>
  :root { --bg:#0d1117; --panel:#161b22; --line:#30363d; --fg:#c9d1d9; --mut:#8b949e;
          --ok:#3fb950; --bad:#f85149; --warn:#d29922; --acc:#58a6ff; }
  * { box-sizing:border-box; }
  body { margin:0; font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;
         background:var(--bg); color:var(--fg); }
  header { padding:10px 16px; border-bottom:1px solid var(--line); display:flex;
           align-items:center; gap:12px; }
  header b { color:var(--acc); font-size:15px; }
  header .tag { color:var(--mut); }
  .wrap { display:grid; grid-template-columns:280px 1fr; height:calc(100vh - 44px); }
  .runs { border-right:1px solid var(--line); overflow:auto; }
  .run { padding:8px 12px; border-bottom:1px solid var(--line); cursor:pointer; }
  .run:hover { background:var(--panel); }
  .run.sel { background:#1f6feb22; border-left:3px solid var(--acc); }
  .run .gid { color:var(--fg); }
  .run .rid { color:var(--mut); font-size:11px; word-break:break-all; }
  main { overflow:auto; padding:16px; }
  .bar { display:flex; gap:18px; flex-wrap:wrap; margin-bottom:14px; align-items:center; }
  .pill { padding:2px 10px; border-radius:10px; background:var(--panel);
          border:1px solid var(--line); }
  .status-completed { color:var(--ok); } .status-failed { color:var(--bad); }
  .status-paused { color:var(--warn); } .status-running { color:var(--acc); }
  table { width:100%; border-collapse:collapse; }
  th,td { text-align:left; padding:4px 8px; border-bottom:1px solid var(--line);
          vertical-align:top; }
  th { color:var(--mut); font-weight:normal; position:sticky; top:0; background:var(--bg); }
  tr.ev:hover { background:var(--panel); }
  tr.clk { cursor:pointer; }
  .t-run { color:var(--acc); } .t-step { color:var(--fg); }
  .t-llm,.t-tool { color:#a5d6ff; } .t-fail,.t-denied,.t-exceeded { color:var(--bad); }
  .t-gate,.t-warn { color:var(--warn); } .t-route { color:#d2a8ff; }
  .seam td { background:#1f6feb22; }
  .num { text-align:right; color:var(--mut); }
  .drawer { position:fixed; right:0; top:44px; bottom:0; width:46%; background:var(--panel);
            border-left:1px solid var(--line); padding:14px; overflow:auto; display:none; }
  .drawer.open { display:block; }
  .drawer pre { white-space:pre-wrap; word-break:break-word; }
  .x { float:right; cursor:pointer; color:var(--mut); }
  .muted { color:var(--mut); }
  button { background:var(--panel); color:var(--fg); border:1px solid var(--line);
           border-radius:6px; padding:3px 10px; cursor:pointer; }
</style>
</head>
<body>
<header><b>KEEL</b><span class="tag">trace viewer</span>
  <span id="hint" class="muted" style="margin-left:auto"></span></header>
<div class="wrap">
  <div class="runs" id="runs"></div>
  <main id="main"><p class="muted">Select a run.</p></main>
</div>
<div class="drawer" id="drawer"><span class="x" onclick="closeDrawer()">close ✕</span>
  <h3 id="dtitle"></h3><pre id="dbody"></pre></div>
<script>
const $ = s => document.querySelector(s);
let current = null;

async function loadRuns() {
  const runs = await (await fetch('/api/runs')).json();
  const el = $('#runs');
  if (!runs.length) { el.innerHTML = '<p class="muted" style="padding:12px">No runs yet.</p>'; return; }
  el.innerHTML = runs.map(r =>
    `<div class="run" data-id="${r.run_id}" onclick="openRun('${r.run_id}')">
       <div class="gid">${r.graph_id}</div><div class="rid">${r.run_id}</div></div>`).join('');
}

function cls(t) {
  if (t.startsWith('run.')) return t==='run.resumed'?'t-run':'t-run';
  if (t.startsWith('llm.')) return 't-llm';
  if (t.startsWith('tool.')) return t.includes('denied')?'t-denied':'t-tool';
  if (t.startsWith('route')) return 't-route';
  if (t.startsWith('gate')) return 't-gate';
  if (t.startsWith('budget')) return t.includes('exceeded')?'t-exceeded':'t-warn';
  if (t.includes('failed')) return 't-fail';
  return 't-step';
}

async function openRun(id) {
  current = id;
  document.querySelectorAll('.run').forEach(d =>
    d.classList.toggle('sel', d.dataset.id === id));
  const r = await (await fetch('/api/runs/'+id)).json();
  const cost = await (await fetch('/api/runs/'+id+'/cost')).json();
  const rows = r.events.map(e => {
    const seam = e.type==='run.resumed' ? ' seam' : '';
    const tok = e.tokens ? `${e.tokens.input}→${e.tokens.output}` : '';
    const clk = e.payload_ref ? 'clk' : '';
    const onclk = e.payload_ref ? `onclick="drill('${e.payload_ref}','${e.type} ${e.node_id||''}')"` : '';
    return `<tr class="ev${seam} ${clk}" ${onclk}>
      <td class="num">${e.seq}</td>
      <td class="${cls(e.type)}">${e.type}</td>
      <td>${e.node_id||''}</td>
      <td class="num">${tok}</td>
      <td class="num">${e.cost_usd? '$'+e.cost_usd.toFixed(5):''}</td>
      <td class="muted">${fmtData(e)}</td></tr>`;
  }).join('');
  const exp = cost.most_expensive ? `${cost.most_expensive[0]} ($${cost.most_expensive[1].toFixed(5)})` : '—';
  $('#main').innerHTML = `
    <div class="bar">
      <span class="pill status-${r.status}">${r.status}</span>
      <span class="pill">$${r.total_cost_usd.toFixed(6)}</span>
      <span class="pill">tokens ${r.total_tokens_in}→${r.total_tokens_out}</span>
      <span class="pill">${r.events.length} events</span>
      <span class="pill">priciest: ${exp}</span>
    </div>
    ${gatePanel(r)}
    <table><thead><tr><th>#</th><th>type</th><th>node</th><th>tok</th><th>cost</th><th>data</th></tr></thead>
    <tbody>${rows}</tbody></table>`;
}

function openGates(r) {
  const decided = new Set();
  for (const e of r.events)
    if (e.type==='gate.approved'||e.type==='gate.rejected'||e.type==='gate.expired') decided.add(e.node_id);
  return r.events.filter(e => e.type==='gate.opened' && !decided.has(e.node_id)).map(e => e.node_id);
}
function gatePanel(r) {
  const gates = openGates(r);
  if (!gates.length) return '';
  return gates.map(n => `<div class="bar"><span class="pill status-paused">gate: ${n}</span>
    <button onclick="decideGate('${n}','approve')">Approve</button>
    <button onclick="decideGate('${n}','reject')">Reject</button></div>`).join('');
}
async function decideGate(node, decision) {
  await fetch(`/api/runs/${current}/gates/${node}/${decision}`, {method:'POST'});
  $('#hint').textContent = `${decision}d ${node} — resumes on next worker / keel resume`;
  openRun(current);
}

function fmtData(e) {
  const d = e.data || {};
  const keys = Object.keys(d).filter(k => !['context'].includes(k));
  if (!keys.length) return e.payload_ref ? '<span class="muted">▸ payload</span>' : '';
  return keys.slice(0,4).map(k => `${k}=${JSON.stringify(d[k])}`).join(' ');
}

async function drill(ref, title) {
  const txt = await (await fetch('/api/blob/'+encodeURIComponent(ref))).text();
  $('#dtitle').textContent = title;
  $('#dbody').textContent = txt;
  $('#drawer').classList.add('open');
}
function closeDrawer() { $('#drawer').classList.remove('open'); }
loadRuns();
setInterval(loadRuns, 4000);
</script>
</body>
</html>
"""
