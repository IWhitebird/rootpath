(() => {
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
let C = {};
const readColors = () => { ['ok','warn','bad','idle','ink','muted','line','path','path-ink','surface','accent','bg','team','person'].forEach(k => { C[k] = css('--' + k); }); };
readColors();
window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', readColors);
const STATUS = s => ({ healthy: C.ok, degraded: C.warn, failing: C.bad, down: C.bad, idle: C.idle }[s] || C.idle);
const TYPES = {
  Service: { r: 9, show: true, color: () => C.accent }, Change: { r: 5, show: true, color: () => C.warn }, Flag: { r: 5, show: true, color: () => C.accent },
  Incident: { r: 6, show: true, color: () => C.bad }, Action: { r: 5, show: true, color: () => C.ok }, Team: { r: 5, show: true, color: () => C.team },
  Person: { r: 4, show: true, color: () => C.person }, Runbook: { r: 4, show: false, color: () => C.muted },
};
const ACTIONS = new Set(['rollback_service', 'restart_service', 'set_feature_flag']);
const PATH_TYPES = new Set(['CALLS', 'CHANGED', 'TOGGLED', 'AFFECTS', 'ON_CALL_FOR', 'ROOT_CAUSE', 'REVERTS', 'ON']);
const TIGHT = new Set(['CHANGED', 'TOGGLED', 'AFFECTS', 'OWNS', 'ON_CALL_FOR', 'FOR', 'ON']);
const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
const $ = id => document.getElementById(id);
const idOf = x => typeof x === 'object' ? x.id : x;
let nodesById = new Map(), allLinks = [], visited = new Map(), latest = new Set(), health = {}, PIN = {}, HOME = {}, mode = 'graph', selectedStep = null;

// ---------- legend ----------
$('legend').innerHTML = Object.entries(TYPES).map(([t, c]) =>
  `<label><input type="checkbox" data-type="${t}" ${c.show ? 'checked' : ''}><i style="background:${c.color()}"></i>${t}</label>`).join('');
$('legend').addEventListener('change', e => { TYPES[e.target.dataset.type].show = e.target.checked; render(); });

// ---------- graph ----------
const el = $('graph');
const G = ForceGraph()(el)
  .nodeId('id').backgroundColor('rgba(0,0,0,0)')
  .linkColor(l => onPath(l) ? C.path : l.type === 'CALLS' ? C.muted : C.line)
  .linkWidth(l => onPath(l) ? 2.6 : l.type === 'CALLS' ? 1.4 : 0.6)
  .linkLineDash(l => l.optional ? [3, 3] : null)
  .linkDirectionalArrowLength(l => l.type === 'CALLS' ? 5 : 0).linkDirectionalArrowRelPos(1)
  .linkDirectionalParticles(l => l.type === 'CALLS' ? 2 : 0)
  .linkDirectionalParticleWidth(l => onPath(l) ? 3 : 1.6)
  .linkDirectionalParticleColor(l => failingLink(l) ? C.bad : C.accent)
  .linkDirectionalParticleSpeed(0.006)
  .nodeCanvasObject(draw)
  .nodePointerAreaPaint((n, color, ctx) => { ctx.fillStyle = color; ctx.beginPath(); ctx.arc(n.x, n.y, (TYPES[n.type]?.r || 5) + 3, 0, 7); ctx.fill(); })
  .onNodeClick(showDetail)
  .onBackgroundClick(() => { $('detail').hidden = true; })
  .onNodeHover(n => { el.style.cursor = n ? 'pointer' : null; })
  .autoPauseRedraw(false).cooldownTicks(160);
G.d3Force('charge').strength(-60).distanceMax(130);
G.d3Force('link').distance(l => l.type === 'CALLS' ? 140 : 32).strength(l => l.type === 'CALLS' ? 0 : TIGHT.has(l.type) ? 0.5 : 0.02);
G.d3Force('home', (() => { let ns = []; const f = a => ns.forEach(n => { const h = HOME[n.id]; if (!h || n.fx !== undefined) return; n.vx += (h.x - n.x) * 0.12 * a; n.vy += (h.y - n.y) * 0.12 * a; }); f.initialize = x => { ns = x; }; return f; })());
let fitted = false;
G.onEngineStop(() => { if (!fitted) { G.zoomToFit(500, 60); fitted = true; } });
new ResizeObserver(() => G.width(el.clientWidth).height(el.clientHeight)).observe(el);

function onPath(l) { return PATH_TYPES.has(l.type) && visited.has(idOf(l.source)) && visited.has(idOf(l.target)); }
function svcStatus(n) { return (health[n.name] && health[n.name].status) || n.props.status || 'idle'; }
function failingLink(l) { const t = nodesById.get(idOf(l.target)); return t && t.type === 'Service' && ['failing', 'down', 'degraded'].includes(svcStatus(t)); }

function draw(n, ctx, scale) {
  const r = TYPES[n.type]?.r || 4;
  const fill = n.type === 'Service' ? STATUS(svcStatus(n)) : (TYPES[n.type]?.color() || C.muted);
  if (visited.has(n.id)) {
    const pulse = latest.has(n.id) && !reduceMotion ? 2.5 + 2 * Math.sin(performance.now() / 170) : 2;
    ctx.beginPath(); ctx.arc(n.x, n.y, r + 3 + pulse, 0, 7); ctx.strokeStyle = C.path; ctx.lineWidth = 2.2 / Math.max(scale, .7); ctx.stroke();
  }
  ctx.beginPath();
  if (n.type === 'Change' || n.type === 'Flag') { const k = r + 1.4; ctx.moveTo(n.x, n.y - k); ctx.lineTo(n.x + k, n.y); ctx.lineTo(n.x, n.y + k); ctx.lineTo(n.x - k, n.y); ctx.closePath(); }
  else if (n.type === 'Team' || n.type === 'Runbook') ctx.rect(n.x - r, n.y - r, 2 * r, 2 * r);
  else ctx.arc(n.x, n.y, r, 0, 7);
  ctx.fillStyle = fill; ctx.fill();
  if (n.type === 'Service') { ctx.lineWidth = 1.5 / Math.max(scale, .7); ctx.strokeStyle = C.bg; ctx.stroke(); }
  if (n.type === 'Service' || visited.has(n.id) || scale > 1.6) {
    const fs = (n.type === 'Service' ? 12 : 9.5) / Math.max(scale, .85);
    ctx.font = `${n.type === 'Service' ? 600 : 500} ${fs}px "IBM Plex Sans", system-ui, sans-serif`;
    ctx.textAlign = 'center'; ctx.textBaseline = 'top';
    ctx.lineWidth = 3 / Math.max(scale, .85); ctx.strokeStyle = C.bg; ctx.strokeText(n.name, n.x, n.y + r + 3);
    ctx.fillStyle = n.type === 'Service' ? C.ink : C.muted; ctx.fillText(n.name, n.x, n.y + r + 3);
  }
  const steps = visited.get(n.id);
  if (steps) {
    const label = steps.length > 3 ? `${steps[0]}…${steps[steps.length - 1]}` : steps.join(',');
    ctx.font = `700 ${9 / Math.max(scale, .85)}px "IBM Plex Mono", monospace`;
    const w = ctx.measureText(label).width + 6 / scale, h = 11.5 / scale;
    ctx.fillStyle = C.path; ctx.fillRect(n.x + r, n.y - r - h, w, h);
    ctx.fillStyle = C['path-ink']; ctx.textAlign = 'left'; ctx.textBaseline = 'middle'; ctx.fillText(label, n.x + r + 3 / scale, n.y - r - h / 2);
  }
}

// Services pinned in call-depth columns (user-facing on the left); everything else pulled
// toward a home next to the service it belongs to; incidents in a memory band at the bottom.
function layout(nodes, links) {
  const svc = nodes.filter(n => n.type === 'Service').map(n => n.id);
  const calls = links.filter(l => l.type === 'CALLS');
  const called = new Set(calls.map(l => idOf(l.target)));
  const depth = {}; svc.filter(id => !called.has(id)).forEach(r => { depth[r] = 0; });
  for (let i = 0; i < svc.length; i++) calls.forEach(l => { const a = idOf(l.source), b = idOf(l.target); if (depth[a] !== undefined) depth[b] = Math.max(depth[b] ?? 0, depth[a] + 1); });
  const cols = {}; svc.forEach(id => { const d = depth[id] ?? 0; (cols[d] = cols[d] || []).push(id); });
  const maxD = Math.max(0, ...Object.keys(cols).map(Number));
  PIN = {};
  Object.entries(cols).forEach(([d, ids]) => { ids.sort(); ids.forEach((id, i) => { PIN[id] = { x: (Number(d) - maxD / 2) * 150, y: (i - (ids.length - 1) / 2) * 115 }; }); });
  HOME = {};
  const type = Object.fromEntries(nodes.map(n => [n.id, n.type]));
  const nbrs = {}; links.forEach(l => { const a = idOf(l.source), b = idOf(l.target); (nbrs[a] = nbrs[a] || []).push(b); (nbrs[b] = nbrs[b] || []).push(a); });
  const avg = ids => { const ps = ids.map(i => PIN[i] || HOME[i]).filter(Boolean); return ps.length ? { x: ps.reduce((a, p) => a + p.x, 0) / ps.length, y: ps.reduce((a, p) => a + p.y, 0) / ps.length } : null; };
  const off = { Change: [0, -62], Flag: [28, -92], Team: [0, 64], Person: [0, 102], Runbook: [-42, 50], Action: [38, -42] };
  for (let pass = 0; pass < 3; pass++) nodes.forEach(n => {
    if (PIN[n.id] || n.type === 'Incident') return;
    const s = (nbrs[n.id] || []).filter(i => PIN[i]);
    const base = avg(s.length ? s : (nbrs[n.id] || []).filter(i => HOME[i] && type[i] !== 'Incident'));
    if (base) { const [dx, dy] = off[n.type] || [0, 50]; HOME[n.id] = { x: base.x + dx, y: base.y + dy }; }
  });
  const xs = Object.values(PIN).map(p => p.x), ys = Object.values(PIN).map(p => p.y);
  const inc = nodes.filter(n => n.type === 'Incident').sort((a, b) => a.id.localeCompare(b.id));
  const minX = Math.min(...xs) - 40, maxX = Math.max(...xs) + 40;
  inc.forEach((n, i) => { HOME[n.id] = { x: minX + (maxX - minX) * (inc.length > 1 ? i / (inc.length - 1) : .5), y: Math.max(...ys) + 170 }; });
}

function render() {
  const nodes = [...nodesById.values()].filter(n => TYPES[n.type]?.show);
  const ids = new Set(nodes.map(n => n.id));
  G.graphData({ nodes, links: allLinks.filter(l => ids.has(idOf(l.source)) && ids.has(idOf(l.target))) });
}

async function loadGraph() {
  const data = await (await fetch('/api/graph')).json();
  layout(data.nodes, data.links);
  let changed = data.nodes.length !== nodesById.size;
  const fresh = new Map();
  for (const n of data.nodes) {
    const old = nodesById.get(n.id);
    if (old) { old.props = n.props; old.name = n.name; fresh.set(n.id, old); continue; }
    const copy = { ...n };
    if (HOME[n.id]) { copy.x = HOME[n.id].x + (Math.random() - .5) * 10; copy.y = HOME[n.id].y + (Math.random() - .5) * 10; }
    fresh.set(n.id, copy); changed = true;
  }
  fresh.forEach(n => { if (PIN[n.id]) { n.fx = PIN[n.id].x; n.fy = PIN[n.id].y; } });
  nodesById = fresh;
  const key = l => `${idOf(l.source)}|${l.type}|${idOf(l.target)}`;
  const before = new Set(allLinks.map(key));
  if (data.links.length !== allLinks.length || data.links.some(l => !before.has(key(l)))) { allLinks = data.links; changed = true; }
  if (changed) render();
}

function showDetail(n) {
  const box = $('detail');
  const skip = new Set(['at', 'health_at', 'opened_at', 'resolved_at', 'observed_at', 'started_at', 'updated_at', 'finished_at']);
  const rows = Object.entries(n.props).filter(([k, v]) => v !== null && v !== '' && !skip.has(k))
    .map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(typeof v === 'number' ? (Math.round(v * 1000) / 1000) : String(v))}</dd>`).join('');
  const steps = visited.get(n.id);
  box.innerHTML = `<button class="x" aria-label="Close">×</button><div class="type">${n.type}</div><h3>${esc(n.name)}</h3>
    ${steps ? `<div class="meta">looked at in step${steps.length > 1 ? 's' : ''} ${steps.join(', ')}</div>` : ''}<dl>${rows}</dl>`;
  box.querySelector('.x').onclick = () => { box.hidden = true; };
  box.hidden = false;
}

// ---------- health + status ----------
async function loadHealth() {
  const data = await (await fetch('/api/health')).json();
  health = data.services;
  $('busy').textContent = data.busy ? `● ${data.busy}` : '';
  ['run', 'reset', 'break-btn'].forEach(id => { $(id).disabled = !!data.busy; });
  const order = { down: 0, failing: 1, degraded: 2, healthy: 3, idle: 4, unknown: 5 };
  $('health').innerHTML = Object.entries(health).sort((a, b) => (order[a[1].status] - order[b[1].status]) || a[0].localeCompare(b[0])).map(([name, s]) => `
    <div class="svc" data-svc="${name}"><span class="dot" style="background:${STATUS(s.status)}"></span><span>${name} <span class="v">v${s.version || '?'}</span></span>
    <span class="n ${s.error_rate > .03 ? 'bad' : ''}">${s.status === 'down' ? 'DOWN' : Math.round((s.error_rate || 0) * 100) + '%'}</span>
    <span class="n ${(s.p95_ms || 0) > 1000 ? 'bad' : ''}">${Math.round(s.p95_ms || 0)} ms</span></div>`).join('');
  document.querySelectorAll('.svc').forEach(r => r.onclick = () => { const n = nodesById.get('Service:' + r.dataset.svc); if (n) { showDetail(n); G.centerAt(n.x, n.y, 400); } });
  const bad = Object.entries(health).filter(([, s]) => ['failing', 'down'].includes(s.status));
  $('status').classList.toggle('bad', bad.length > 0);
  $('status-text').textContent = bad.length ? `${bad.length} service${bad.length > 1 ? 's' : ''} failing: ${bad.map(([n]) => n).join(', ')}` : 'All services healthy';
}

// ---------- investigation ----------
async function loadInvestigation() {
  const data = await (await fetch('/api/investigation')).json();
  if (!data.investigation) return;
  const inv = data.investigation;
  visited = new Map();
  data.steps.forEach(s => s.targets.forEach(t => { if (!visited.has(t)) visited.set(t, []); visited.get(t).push(s.n); }));
  const last = data.steps[data.steps.length - 1];
  latest = new Set(selectedStep ? (data.steps.find(s => s.n === selectedStep)?.targets || []) : (last ? last.targets : []));
  const meta = [inv.agent, inv.mode && `${inv.mode} tools`, inv.alert, inv.duration_s && `${inv.duration_s}s`,
    inv.tool_calls && `${inv.tool_calls} tool calls`, inv.cost_usd ? `$${inv.cost_usd.toFixed(3)}` : null].filter(Boolean).join(' · ');
  $('inv-meta').textContent = `${inv.id}${meta ? ' · ' + meta : ''}`;
  $('steps').innerHTML = data.steps.length ? data.steps.map((s, i) => `<div class="step ${i === data.steps.length - 1 && !selectedStep ? 'on' : ''} ${selectedStep === s.n ? 'sel' : ''}" data-n="${s.n}">
    <div class="num">${s.n}</div><div><div class="tool ${ACTIONS.has(s.tool) && !/DRY RUN/.test(s.note) ? 'act' : ''}">${s.tool}</div><div class="note">${esc(s.note || '')}</div></div></div>`).join('')
    : '<p class="empty">The agent is reading the graph…</p>';
  document.querySelectorAll('.step').forEach(d => d.onclick = () => { const n = Number(d.dataset.n); selectedStep = selectedStep === n ? null : n; loadInvestigation(); });
  const rep = $('report');
  if (inv.report) { rep.hidden = false; rep.innerHTML = '<h4>Agent report</h4>' + md(inv.report); } else rep.hidden = true;
  const cap = $('caption');
  if (selectedStep) { const s = data.steps.find(x => x.n === selectedStep); if (s) cap.innerHTML = `<span class="chip step">step ${s.n}</span><span class="text"><code>${s.tool}</code> ${esc(s.note || '')}</span>`; }
  else if (last && !inv.finished_at) cap.innerHTML = `<span class="chip step">step ${last.n}</span><span class="text"><code>${last.tool}</code> ${esc(last.note || '')}</span>`;
  else if (inv.finished_at) cap.innerHTML = `<span class="chip ok">done</span><span class="text">${esc(inv.alert || inv.id)}: finished in ${inv.duration_s || '?'}s with ${inv.tool_calls || data.steps.length} tool calls. Click a step to see what it looked at.</span>`;
  $('kv-agent').innerHTML = inv.finished_at ? `last agent run <b>${inv.duration_s ?? '–'}s</b> · <b>${inv.tool_calls ?? data.steps.length}</b> calls` : `agent <b>working</b> · step <b>${last ? last.n : 0}</b>`;
}

async function loadIncidents() {
  const rows = await (await fetch('/api/incidents')).json();
  $('inc-count').textContent = rows.length;
  const open = rows.find(r => r.status === 'open');
  $('kv-inc').innerHTML = open ? `open <b>${open.id}</b> ${esc(open.severity || '')}` : '';
  const agent = rows.filter(r => r.source === 'agent' && r.mttr_min != null);
  const human = rows.filter(r => r.source !== 'agent' && r.mttr_min != null);
  const avg = a => a.reduce((s, r) => s + r.mttr_min, 0) / a.length;
  $('kv-mem').innerHTML = `memory <b>${rows.length}</b> incidents` + (agent.length && human.length ? ` · MTTR human <b>${avg(human).toFixed(0)} min</b>, agent <b>${avg(agent).toFixed(1)} min</b>` : '');
  $('panel-inc').innerHTML = rows.length ? rows.map(r => `<div class="inc"><div class="row"><span class="id">${r.id}</span>
    <span class="tag ${r.status === 'open' ? 'open' : ''}">${r.status}</span>${r.source === 'agent' ? '<span class="tag agent">agent</span>' : ''}
    ${r.severity ? `<span class="tag">${r.severity}</span>` : ''}</div><div class="title">${esc(r.title || '')}</div>
    <div class="sub">${r.root_cause.length ? 'root cause ' + esc(r.root_cause.join(', ')) : ''}${r.fixed_by.length ? ' · fixed by ' + r.fixed_by.map(a => `${esc(a.kind)} ${esc(a.target)} (${esc(a.by)})`).join(', ') : ''}${r.mttr_min != null ? ` · ${r.mttr_min} min` : ''}</div></div>`).join('')
    : '<p class="empty">No incidents yet.</p>';
}

async function loadChanges() {
  const rows = await (await fetch('/api/changes')).json();
  $('chg-count').textContent = rows.length;
  const ago = at => { const s = Date.now() / 1000 - at; return s < 120 ? `${Math.round(s)}s` : s < 7200 ? `${Math.round(s / 60)}m` : `${Math.round(s / 3600)}h`; };
  $('panel-chg').innerHTML = rows.length ? rows.map(c => `<div class="chg"><span class="when">${ago(c.at)} ago</span><div>
    ${c.kind === 'deploy' ? `<b>${esc(c.service)}</b> v${esc(c.prev_version || '?')} → v${esc(c.version)}` : `flag <b>${esc(c.flag)}</b> ${c.from} → ${c.to}`}
    <span class="kv"> by ${esc(c.author)}</span>${c.note ? `<div class="kv">${esc(c.note)}</div>` : ''}</div></div>`).join('')
    : '<p class="empty">No deploys or flag flips in the last 24 hours.</p>';
}

// ---------- controls ----------
let toastTimer;
function toast(msg) { let t = document.querySelector('.toast'); if (!t) { t = document.createElement('div'); t.className = 'toast'; t.setAttribute('role', 'status'); document.body.appendChild(t); } t.textContent = msg; clearTimeout(toastTimer); toastTimer = setTimeout(() => t.remove(), 3200); }
async function post(url, ok) {
  const r = await fetch(url, { method: 'POST' });
  if (!r.ok) { const d = await r.json().catch(() => ({})); toast(d.detail || `Request failed (${r.status})`); return; }
  toast(ok); setTimeout(() => { loadHealth(); loadGraph(); }, 600);
}
fetch('/api/scenarios').then(r => r.json()).then(sc => {
  $('break-menu').innerHTML = Object.entries(sc).map(([k, d]) => `<button data-s="${k}"><b>${k}</b><span>${esc(d)}</span></button>`).join('');
  $('break-menu').querySelectorAll('button').forEach(b => b.onclick = () => { closeMenu(); selectedStep = null; post(`/api/chaos/${b.dataset.s}`, `Injecting ${b.dataset.s}. Watch the health panel.`); });
});
function closeMenu() { $('break-menu').hidden = true; $('break-btn').setAttribute('aria-expanded', 'false'); }
$('break-btn').onclick = e => { e.stopPropagation(); const m = $('break-menu'); m.hidden = !m.hidden; $('break-btn').setAttribute('aria-expanded', String(!m.hidden)); };
document.addEventListener('click', e => { if (!e.target.closest('.menu-wrap')) closeMenu(); });
document.addEventListener('keydown', e => { if (e.key === 'Escape') { closeMenu(); $('detail').hidden = true; } });
const setMode = m => { mode = m; $('mode-graph').setAttribute('aria-pressed', m === 'graph'); $('mode-baseline').setAttribute('aria-pressed', m === 'baseline'); $('run').textContent = m === 'graph' ? 'Run agent' : 'Run baseline agent'; };
$('mode-graph').onclick = () => setMode('graph'); $('mode-baseline').onclick = () => setMode('baseline');
$('run').onclick = () => { selectedStep = null; post(`/api/agent?mode=${mode}`, `Agent started with ${mode} tools.`); };
$('reset').onclick = e => post(e.shiftKey ? '/api/reset?hard=true' : '/api/reset', e.shiftKey ? 'Resetting with a fresh graph.' : 'Restoring healthy state. Incident memory is kept.');
document.querySelectorAll('.tabs button').forEach(b => b.onclick = () => {
  document.querySelectorAll('.tabs button').forEach(x => x.setAttribute('aria-selected', x === b));
  ['inv', 'inc', 'chg'].forEach(p => { $('panel-' + p).hidden = p !== b.dataset.tab; });
});

function esc(s) { return String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }
function md(src) {
  let html = '', list = null, code = null;
  const inline = s => esc(s).replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>');
  const close = () => { if (list) { html += `</${list}>`; list = null; } };
  for (const line of src.split('\n')) {
    if (line.startsWith('```')) { if (code === null) { close(); code = ''; } else { html += `<pre><code>${esc(code)}</code></pre>`; code = null; } continue; }
    if (code !== null) { code += line + '\n'; continue; }
    let m;
    if ((m = line.match(/^#{1,4}\s+(.*)/))) { close(); html += `<h3>${inline(m[1])}</h3>`; }
    else if ((m = line.match(/^\s*[-*]\s+(.*)/))) { if (list !== 'ul') { close(); html += '<ul>'; list = 'ul'; } html += `<li>${inline(m[1])}</li>`; }
    else if ((m = line.match(/^\s*\d+\.\s+(.*)/))) { if (list !== 'ol') { close(); html += '<ol>'; list = 'ol'; } html += `<li>${inline(m[1])}</li>`; }
    else if (!line.trim()) close();
    else { close(); html += `<p>${inline(line)}</p>`; }
  }
  close(); return html;
}

function tick(fn, ms) { const run = () => fn().catch(() => {}).finally(() => setTimeout(run, ms)); run(); }
tick(loadGraph, 4000); tick(loadHealth, 2000); tick(loadInvestigation, 1500); tick(loadIncidents, 5000); tick(loadChanges, 5000);
})();
