(() => {
const REPLAYS = JSON.parse(document.getElementById('replays').textContent);
const BENCH = JSON.parse(document.getElementById('bench').textContent);
const CONFIG = JSON.parse(document.getElementById('site-config').textContent);
const ORDER = [
  { key: 'flag', title: 'Flag flip, three hops down', sub: 'fraud-check slows, frontend fails' },
  { key: 'pool-after-deploy', title: 'Old bug, new release', sub: 'restart, not rollback' },
  { key: 'bad-deploy', title: 'Bad deploy, red herring', sub: 'payment v2.4.1' },
].filter(o => REPLAYS[o.key]);
const ACTIONS = new Set(['rollback_service', 'restart_service', 'set_feature_flag']);
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
let C = {};
const readColors = () => { C = { ok: css('--ok'), warn: css('--warn'), bad: css('--bad'), idle: css('--idle'), ink: css('--ink'), muted: css('--muted'),
  line: css('--line'), path: css('--path'), pathInk: css('--path-ink'), surface: css('--surface'), accent: css('--accent'), bg: css('--bg') }; };
readColors();
const STATUS = s => ({ healthy: C.ok, degraded: C.warn, failing: C.bad, down: C.bad, idle: C.idle }[s] || C.idle);
const TYPE = {
  Service: { r: 8 }, Change: { r: 4.5 }, Flag: { r: 4.5 }, Incident: { r: 5.5 }, Action: { r: 4.5 }, Team: { r: 4.5 }, Person: { r: 3.6 }, Runbook: { r: 3.6 },
};
const typeColor = t => ({ Change: C.warn, Flag: C.accent, Incident: C.bad, Action: C.ok, Team: '#8d6ad8', Person: '#d0609b', Runbook: C.muted }[t] || C.muted);
const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

// ---------- replay state ----------
let R = null, t = 0, playing = !reduceMotion, speed = 2, lastFrame = 0, visibleKey = '', visited = new Map(), latest = new Set(), shownSteps = 0;
let nodeById = new Map();
const el = document.getElementById('graph');
const G = ForceGraph()(el)
  .nodeId('id').backgroundColor('rgba(0,0,0,0)')
  .linkColor(l => onPath(l) ? C.path : (l.type === 'CALLS' ? C.muted : C.line))
  .linkWidth(l => onPath(l) ? 2.6 : l.type === 'CALLS' ? 1.3 : 0.6)
  .linkLineDash(l => l.optional ? [3, 3] : null)
  .linkDirectionalArrowLength(l => l.type === 'CALLS' ? 4.5 : 0).linkDirectionalArrowRelPos(1)
  .linkDirectionalParticles(l => l.type === 'CALLS' ? 2 : 0)
  .linkDirectionalParticleWidth(l => onPath(l) ? 3 : 1.6)
  .linkDirectionalParticleColor(l => failing(l) ? C.bad : C.accent)
  .linkDirectionalParticleSpeed(0.006)
  .nodeCanvasObject(draw)
  .nodePointerAreaPaint((n, color, ctx) => { ctx.fillStyle = color; ctx.beginPath(); ctx.arc(n.x, n.y, (TYPE[n.type]?.r || 4) + 3, 0, 7); ctx.fill(); })
  .enableZoomInteraction(false)
  .autoPauseRedraw(false)
  .cooldownTicks(140)
  .onEngineStop(() => G.zoomToFit(500, 64));
G.d3Force('charge').strength(-55).distanceMax(120);
const TIGHT = new Set(['CHANGED', 'TOGGLED', 'AFFECTS', 'OWNS', 'ON_CALL_FOR', 'FOR', 'ON']);
G.d3Force('link').distance(l => l.type === 'CALLS' ? 130 : 30).strength(l => l.type === 'CALLS' ? 0 : TIGHT.has(l.type) ? 0.5 : 0.02);
new ResizeObserver(() => { G.width(el.clientWidth).height(el.clientHeight); }).observe(el);

const idOf = x => typeof x === 'object' ? x.id : x;
const healthAt = () => { let f = R.health[0]; for (const h of R.health) { if (h.t <= t) f = h; else break; } return f.s; };
let H = {};
const svcStatus = n => (H[n.name] || [n.props.status])[0];
function onPath(l) { return ['CALLS', 'CHANGED', 'TOGGLED', 'AFFECTS', 'ON_CALL_FOR', 'ROOT_CAUSE', 'REVERTS', 'ON'].includes(l.type) && visited.has(idOf(l.source)) && visited.has(idOf(l.target)); }
function failing(l) { const n = nodeById.get(idOf(l.target)); return n && n.type === 'Service' && ['failing', 'down', 'degraded'].includes(svcStatus(n)); }

function draw(n, ctx, scale) {
  const r = TYPE[n.type]?.r || 4;
  const fill = n.type === 'Service' ? STATUS(svcStatus(n)) : typeColor(n.type);
  if (visited.has(n.id)) {
    const pulse = latest.has(n.id) && !reduceMotion ? 2.5 + 2 * Math.sin(performance.now() / 170) : 2;
    ctx.beginPath(); ctx.arc(n.x, n.y, r + 3 + pulse, 0, 7); ctx.strokeStyle = C.path; ctx.lineWidth = 2.2 / Math.max(scale, .7); ctx.stroke();
  }
  ctx.beginPath();
  if (n.type === 'Change' || n.type === 'Flag') { const k = r + 1.4; ctx.moveTo(n.x, n.y - k); ctx.lineTo(n.x + k, n.y); ctx.lineTo(n.x, n.y + k); ctx.lineTo(n.x - k, n.y); ctx.closePath(); }
  else if (n.type === 'Team' || n.type === 'Runbook') ctx.rect(n.x - r, n.y - r, 2 * r, 2 * r);
  else ctx.arc(n.x, n.y, r, 0, 7);
  ctx.globalAlpha = n.type === 'Incident' ? 0.8 : 1; ctx.fillStyle = fill; ctx.fill(); ctx.globalAlpha = 1;
  if (n.type === 'Service') { ctx.lineWidth = 1.5 / Math.max(scale, .7); ctx.strokeStyle = C.surface; ctx.stroke(); }
  const fs = (n.type === 'Service' ? 11.5 : 9) / Math.max(scale, .85);
  if (n.type === 'Service' || visited.has(n.id)) {
    ctx.font = `${n.type === 'Service' ? 600 : 500} ${fs}px "IBM Plex Sans", system-ui, sans-serif`;
    ctx.textAlign = 'center'; ctx.textBaseline = 'top';
    ctx.lineWidth = 3 / Math.max(scale, .85); ctx.strokeStyle = C.bg; ctx.strokeText(n.name, n.x, n.y + r + 3);
    ctx.fillStyle = n.type === 'Service' ? C.ink : C.muted; ctx.fillText(n.name, n.x, n.y + r + 3);
  }
  const steps = visited.get(n.id);
  if (steps) {
    const label = steps.length > 3 ? `${steps[0]}…${steps[steps.length - 1]}` : steps.join(',');
    ctx.font = `700 ${8.5 / Math.max(scale, .85)}px "IBM Plex Mono", monospace`;
    const w = ctx.measureText(label).width + 6 / scale, h = 11 / scale;
    ctx.fillStyle = C.path; ctx.fillRect(n.x + r, n.y - r - h, w, h);
    ctx.fillStyle = C.pathInk; ctx.textAlign = 'left'; ctx.textBaseline = 'middle'; ctx.fillText(label, n.x + r + 3 / scale, n.y - r - h / 2);
  }
}

// Services are pinned in call-depth columns: the user-facing service on the left, the deepest
// dependencies on the right, so a failure visibly travels across the page.
let PIN = {};
function layout(rec) {
  const svc = rec.nodes.filter(n => n.type === 'Service').map(n => n.id);
  const calls = rec.links.filter(l => l.type === 'CALLS');
  const callers = new Set(calls.map(l => idOf(l.target)));
  const roots = svc.filter(id => !callers.has(id));
  const depth = {}; roots.forEach(r => { depth[r] = 0; });
  for (let i = 0; i < svc.length; i++) calls.forEach(l => { const a = idOf(l.source), b = idOf(l.target); if (depth[a] !== undefined) depth[b] = Math.max(depth[b] ?? 0, depth[a] + 1); });
  const cols = {}; svc.forEach(id => { const d = depth[id] ?? 0; (cols[d] = cols[d] || []).push(id); });
  const maxD = Math.max(...Object.keys(cols).map(Number));
  PIN = {};
  Object.entries(cols).forEach(([d, ids]) => {
    ids.sort();
    ids.forEach((id, i) => { PIN[id] = { x: (Number(d) - maxD / 2) * 135, y: (i - (ids.length - 1) / 2) * 105 }; });
  });
}

// Every other node is pulled toward a "home" near the service it belongs to: changes and flags
// above their service, owners below, incidents in a memory band along the bottom.
let HOME = {};
function homes(rec) {
  HOME = {};
  const type = Object.fromEntries(rec.nodes.map(n => [n.id, n.type]));
  const nbrs = {};
  rec.links.forEach(l => { const a = idOf(l.source), b = idOf(l.target);
    (nbrs[a] = nbrs[a] || []).push(b); (nbrs[b] = nbrs[b] || []).push(a); });
  const avg = ids => { const ps = ids.map(i => PIN[i] || HOME[i]).filter(Boolean); if (!ps.length) return null;
    return { x: ps.reduce((a, p) => a + p.x, 0) / ps.length, y: ps.reduce((a, p) => a + p.y, 0) / ps.length }; };
  const off = { Change: [0, -58], Flag: [26, -86], Team: [0, 62], Person: [0, 98], Runbook: [-40, 48], Action: [36, -40] };
  for (let pass = 0; pass < 3; pass++) {
    rec.nodes.forEach(n => {
      if (PIN[n.id] || n.type === 'Incident') return;
      const svcNbrs = (nbrs[n.id] || []).filter(i => PIN[i]);
      const base = avg(svcNbrs.length ? svcNbrs : (nbrs[n.id] || []).filter(i => HOME[i] && type[i] !== 'Incident'));
      if (base) { const [dx, dy] = off[n.type] || [0, 50]; HOME[n.id] = { x: base.x + dx, y: base.y + dy }; }
    });
  }
  const ys = Object.values(PIN).map(p => p.y);
  const xs = Object.values(PIN).map(p => p.x);
  const incidents = rec.nodes.filter(n => n.type === 'Incident').sort((a, b) => a.id.localeCompare(b.id));
  const minX = Math.min(...xs), maxX = Math.max(...xs);
  incidents.forEach((n, i) => { HOME[n.id] = { x: minX + (maxX - minX) * (incidents.length > 1 ? i / (incidents.length - 1) : 0.5), y: Math.max(...ys) + 150 }; });
}
function homeForce() {
  let nodes = [];
  const f = alpha => { nodes.forEach(n => { const h = HOME[n.id]; if (!h || n.fx !== undefined) return;
    n.vx += (h.x - n.x) * 0.12 * alpha; n.vy += (h.y - n.y) * 0.12 * alpha; }); };
  f.initialize = ns => { nodes = ns; };
  return f;
}
G.d3Force('home', homeForce());

function load(key) {
  R = REPLAYS[key]; t = 0; visibleKey = ''; shownSteps = -1; nodeById = new Map();
  layout(R); homes(R);
  R.injectT = (R.events.find(e => e.kind === 'inject') || { t: 0 }).t;
  R.nodes.forEach(n => { n.t = Math.max(0, n.t - 0.5); });
  document.querySelectorAll('.tab').forEach(b => b.setAttribute('aria-selected', b.dataset.key === key));
  buildTrack(); renderReport(); step(0, true);
  setTimeout(() => G.zoomToFit(400, 64), 900);
}

function step(dt, force) {
  if (!R) return;
  t = Math.min(R.duration, t + dt);
  H = healthAt();
  const nodes = R.nodes.filter(n => n.t <= t);
  const key = nodes.length + ':' + R.links.filter(l => l.t <= t).length;
  if (key !== visibleKey || force) {
    visibleKey = key;
    const ids = new Set(nodes.map(n => n.id));
    nodes.forEach(n => {
      if (nodeById.has(n.id)) return;
      const copy = { ...n, props: { ...n.props } };
      if (PIN[n.id]) { copy.fx = PIN[n.id].x; copy.fy = PIN[n.id].y; }
      else if (HOME[n.id]) { copy.x = HOME[n.id].x + (Math.random() - 0.5) * 10; copy.y = HOME[n.id].y + (Math.random() - 0.5) * 10; }
      nodeById.set(n.id, copy);
    });
    const live = [...nodeById.values()].filter(n => ids.has(n.id));
    const links = R.links.filter(l => l.t <= t && ids.has(idOf(l.source)) && ids.has(idOf(l.target))).map(l => ({ ...l, source: idOf(l.source), target: idOf(l.target) }));
    G.graphData({ nodes: live, links });
  }
  const steps = R.steps.filter(s => s.t <= t);
  visited = new Map();
  steps.forEach(s => s.targets.forEach(id => { if (!visited.has(id)) visited.set(id, []); visited.get(id).push(s.n); }));
  latest = new Set(steps.length ? steps[steps.length - 1].targets : []);
  renderHealth(); renderSteps(steps); renderCaption(steps); renderClock();
  document.getElementById('fill').style.width = (100 * t / R.duration) + '%';
  document.getElementById('scrub').value = Math.round(1000 * t / R.duration);
  if (t >= R.duration && playing) { setPlaying(false); document.getElementById('report').open = true; }
}

function renderHealth() {
  const order = { down: 0, failing: 1, degraded: 2, healthy: 3, idle: 4 };
  const rows = Object.entries(H).sort((a, b) => (order[a[1][0]] - order[b[1][0]]) || a[0].localeCompare(b[0]));
  document.getElementById('health').innerHTML = rows.map(([name, [st, er, p95]]) => `
    <div class="svc" title="${name}: ${st}"><span class="dot" style="background:${STATUS(st)}"></span><span>${name}</span>
      <span class="n ${er > 0.03 ? 'bad' : ''}">${st === 'down' ? 'down' : Math.round(er * 100) + '%'}</span>
      <span class="n ${p95 > 1000 ? 'bad' : ''}">${Math.round(p95)} ms</span></div>`).join('');
}

function renderSteps(steps) {
  if (steps.length === shownSteps) return;
  const box = document.getElementById('steps');
  if (!steps.length) { box.innerHTML = `<p class="empty">${t < (R.events.find(e => e.kind === 'alert') || {}).t ? 'Waiting for the alert.' : 'Agent is reading the graph.'}</p>`; shownSteps = 0; return; }
  const grew = steps.length > shownSteps && shownSteps >= 0;
  box.innerHTML = steps.map((s, i) => `<div class="step ${i === steps.length - 1 ? 'on' : ''} ${grew && i === steps.length - 1 ? 'enter' : ''}">
    <div class="num">${s.n}</div><div><div class="tool ${ACTIONS.has(s.tool) && !/DRY RUN/.test(s.note) ? 'act' : ''}">${s.tool}</div>
    <div class="note">${esc(s.note || '')}</div></div></div>`).join('');
  const sc = box.parentElement; sc.scrollTop = sc.scrollHeight;
  shownSteps = steps.length;
}

function renderCaption(steps) {
  const evs = R.events.filter(e => e.t <= t);
  const ev = evs[evs.length - 1];
  const last = steps[steps.length - 1];
  let chip = 'healthy', cls = '', text = 'Seven services under steady traffic. Every node is in FalkorDB.';
  if (ev && ev.kind === 'inject') { chip = 'failure'; cls = 'alert'; text = esc(ev.text); }
  if (ev && ev.kind === 'alert') { chip = 'paged'; cls = 'alert'; text = esc(ev.text) + '. The headless agent starts with only the oncall tools.'; }
  if (last && (!ev || ev.kind !== 'done' || last.t > ev.t)) { chip = `step ${last.n}`; cls = 'step'; text = `<code>${last.tool}</code> ${esc(last.note || '')}`; }
  if (ev && ev.kind === 'done' && (!last || ev.t >= last.t)) {
    chip = 'resolved'; cls = 'ok';
    text = `Fixed and verified in ${R.agent.duration_s.toFixed(0)} s with ${R.agent.tool_calls} tool calls. The incident and its root cause are now memory.`;
  }
  document.getElementById('caption').innerHTML = `<span class="chip ${cls}">${chip}</span><span class="text">${text}</span>`;
}

function renderClock() {
  const rel = t - R.injectT;
  const s = Math.abs(Math.floor(rel));
  document.getElementById('clock').textContent = `T${rel < 0 ? '−' : '+'}${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
}

function buildTrack() {
  const track = document.getElementById('track');
  track.querySelectorAll('.tick, .ev').forEach(x => x.remove());
  const pct = x => (100 * x / R.duration).toFixed(2) + '%';
  R.steps.forEach(s => { const d = document.createElement('div'); d.className = 'tick'; d.style.left = pct(s.t); d.title = `${s.n}. ${s.tool}`; track.insertBefore(d, track.querySelector('input')); });
  const labels = { inject: 'failure', alert: 'alert', done: 'fixed' };
  R.events.filter(e => labels[e.kind]).forEach(e => {
    const d = document.createElement('div'); d.className = 'ev ' + e.kind; d.style.left = pct(e.t); d.textContent = labels[e.kind];
    track.insertBefore(d, track.querySelector('input'));
  });
}

function renderReport() {
  document.getElementById('report-body').innerHTML = md(R.agent.report || '');
  document.getElementById('report-meta').textContent = `${R.agent.duration_s.toFixed(0)} s · ${R.agent.tool_calls} tool calls · $${(R.agent.cost_usd || 0).toFixed(2)} · recorded ${R.recorded_at}`;
  document.getElementById('report').open = false;
}

function setPlaying(p) {
  playing = p;
  const b = document.getElementById('play');
  b.setAttribute('aria-label', p ? 'Pause replay' : 'Play replay');
  b.innerHTML = p ? '<svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><rect x="2" y="1.5" width="3.4" height="11" rx="1" fill="currentColor"/><rect x="8.6" y="1.5" width="3.4" height="11" rx="1" fill="currentColor"/></svg>'
                  : '<svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><path d="M3 1.8v10.4L12 7z" fill="currentColor"/></svg>';
}

function loop(ts) {
  const dt = lastFrame ? Math.min(0.1, (ts - lastFrame) / 1000) : 0;
  lastFrame = ts;
  if (playing) step(dt * speed);
  requestAnimationFrame(loop);
}

// tabs + transport
const tabs = document.getElementById('tabs');
ORDER.forEach(o => {
  const b = document.createElement('button'); b.className = 'tab'; b.setAttribute('role', 'tab'); b.dataset.key = o.key;
  b.innerHTML = `${o.title}<small>${o.sub}</small>`;
  b.onclick = () => { load(o.key); setPlaying(true); };
  tabs.appendChild(b);
});
document.getElementById('play').onclick = () => { if (t >= R.duration) t = 0; setPlaying(!playing); };
document.getElementById('restart').onclick = () => { t = 0; shownSteps = -1; visibleKey = ''; nodeById = new Map(); document.getElementById('report').open = false; step(0, true); setPlaying(true); };
const speeds = [1, 2, 4];
document.getElementById('speed').onclick = e => { speed = speeds[(speeds.indexOf(speed) + 1) % speeds.length]; e.currentTarget.textContent = speed + '×'; };
document.getElementById('scrub').oninput = e => { const target = R.duration * e.target.value / 1000; if (target < t) { nodeById = new Map(); visibleKey = ''; shownSteps = -1; } t = target; step(0, true); };

// ---------- facts under the console ----------
const recs = Object.values(REPLAYS);
const durs = recs.map(r => r.agent.duration_s);
document.getElementById('facts').innerHTML = [
  `<span>Recorded agent runs: <b>${recs.length}</b>, fixed and verified in <b>${Math.min(...durs).toFixed(0)}–${Math.max(...durs).toFixed(0)} s</b> each</span>`,
  `<span>Tools: <b>17</b> over MCP, <b>3</b> of them change infrastructure</span>`,
  `<span>Hosts: <b>Claude Code</b> (plugin), <b>Codex</b>, <b>Cursor</b>, headless</span>`,
].join('');

// ---------- benchmark ----------
function totals(rows) {
  const ok = rows.filter(r => !r.error);
  const sum = k => ok.reduce((a, r) => a + (r[k] || 0), 0);
  return { n: ok.length, fixed: ok.filter(r => r.correct_action ?? r.fixed).length, clean: ok.filter(r => r.clean).length,
           wrong: ok.reduce((a, r) => a + (r.wrong_actions || []).length, 0), logs: sum('logs_read'), calls: sum('tool_calls'),
           diag: sum('diagnosis_calls'), cost: sum('cost_usd'), time: sum('agent_s') };
}
function benchSection() {
  const tel = [['full', 'With tracing'], ['basic', 'Without tracing']].filter(([k]) => BENCH[k] && BENCH[k].length);
  if (!tel.length) { document.getElementById('evidence').hidden = true; return; }
  const T = {};
  tel.forEach(([k]) => { T[k] = { graph: totals(BENCH[k].filter(r => r.mode === 'graph')), baseline: totals(BENCH[k].filter(r => r.mode === 'baseline')) }; });
  const metric = (title, key, fmt, unit) => {
    const max = Math.max(...tel.flatMap(([k]) => [T[k].graph[key], T[k].baseline[key]]), 1e-9);
    const rows = tel.map(([k, label]) => ['graph', 'baseline'].map(m => `
      <div class="bar-row"><span class="lbl">${label}, ${m}</span>
      <span class="bar"><span class="${m === 'graph' ? 'g' : 'b'}" style="width:${(100 * T[k][m][key] / max).toFixed(1)}%"></span></span>
      <span class="v">${fmt(T[k][m][key])}${unit}</span></div>`).join('')).join('');
    return `<div><h3>${title}</h3>${rows}</div>`;
  };
  const per = k => T[k].graph.n;
  document.getElementById('bars').innerHTML =
    `<div class="key"><span><i style="background:var(--ink)"></i>graph tools</span><span><i style="background:var(--idle)"></i>baseline tools</span></div>` +
    metric('Clean fixes: the right action and nothing else', 'clean', v => v, ` / ${per(tel[0][0])}`) +
    metric('Service logs read before the fix', 'logs', v => v, '') +
    metric('Agent time, all runs', 'time', v => Math.round(v), ' s') +
    metric('Cost of all runs', 'cost', v => '$' + v.toFixed(2), '');
  const f = [];
  const full = T.full, basic = T.basic;
  const all = tel.flatMap(([k]) => BENCH[k].filter(r => !r.error));
  const sumOf = (mode, key) => tel.reduce((a, [k]) => a + T[k][mode][key], 0);
  const wrongList = mode => all.filter(r => r.mode === mode && (r.wrong_actions || []).length)
    .map(r => `${r.wrong_actions.join(', ')} during ${r.scenario}`);
  if (full) f.push(['', 'With tracing, a frontier model finds the cause either way.',
    `${full.graph.fixed === full.graph.n && full.baseline.fixed === full.baseline.n ? `Both modes took the right action in all ${full.graph.n} runs.` : `Right action: graph ${full.graph.fixed} of ${full.graph.n}, baseline ${full.baseline.fixed} of ${full.baseline.n}.`} When every service reports where its errors originate, the answer is close to the health table.`]);
  f.push(['key-f', 'The graph sends the agent to where the failure starts.',
    `Across all runs it read ${sumOf('graph', 'logs')} service logs before fixing, against ${sumOf('baseline', 'logs')} for baseline. The traversal names the origin instead of walking the call chain one hop at a time.`]);
  const gw = wrongList('graph'), bw = wrongList('baseline');
  f.push(['key-f', 'Wrong actions happen on coincidences.',
    (bw.length ? `Baseline took ${bw.length} wrong remediation action${bw.length > 1 ? 's' : ''} (${bw.join('; ')}).` : 'Baseline took no wrong actions.') + ' ' +
    (gw.length ? `The graph agent took ${gw.length} (${gw.join('; ')}).` : 'The graph agent took none.') +
    ' The trap is a healthy release that happens to land on the failing service. The graph hands the agent that service\'s runbook and past incidents with the ranking, and they say what actually fixes this failure.']);
  const gc = sumOf('graph', 'cost'), bc = sumOf('baseline', 'cost'), gt = sumOf('graph', 'time'), bt = sumOf('baseline', 'time');
  f.push(['', gc > bc ? 'It costs a little more per incident.' : 'It costs no more per incident.',
    `All graph runs cost $${gc.toFixed(2)} against $${bc.toFixed(2)} for baseline, and took ${Math.round(gt)} s of agent time against ${Math.round(bt)} s. The graph runs include work baseline cannot do: opening and resolving the incident, paging the on-call owner, recording why each action was taken.`]);
  document.getElementById('findings').innerHTML = f.map(([c, b, p]) => `<div class="finding ${c}"><b>${b}</b><p>${p}</p></div>`).join('');
  const scen = [...new Set(tel.flatMap(([k]) => BENCH[k].map(r => r.scenario)))];
  const cell = (k, s, m) => {
    const rs = BENCH[k].filter(r => r.scenario === s && r.mode === m && !r.error);
    if (!rs.length) return '<td>–</td>';
    const ok = rs.filter(r => r.clean).length;
    return `<td class="${ok === rs.length ? 'hit' : 'miss'}">${ok}/${rs.length}</td>`;
  };
  document.getElementById('bench-table').innerHTML =
    `<thead><tr><th>Scenario</th>${tel.map(([, l]) => `<th>${l}: graph</th><th>${l}: baseline</th>`).join('')}</tr></thead><tbody>` +
    scen.map(s => `<tr><td class="mono">${s}</td>${tel.map(([k]) => cell(k, s, 'graph') + cell(k, s, 'baseline')).join('')}</tr>`).join('') + '</tbody>';
  const runs = tel.reduce((a, [k]) => a + BENCH[k].length, 0);
  document.getElementById('bench-note').textContent = `Clean fix = the expected action on the expected target (rollback payment, flag off, restart inventory) and no other remediation action. ${runs} runs in total, model Claude Opus 5.5 through Claude Code headless. Small samples: read these as directional.`;
}
benchSection();

// ---------- install ----------
const repo = CONFIG.repo || '';
const repoSlug = esc(repo.replace(/^https:\/\/github\.com\//, '').replace(/\.git$/, '') || 'IWhitebird/rootpath');
if (repo) { const a = document.getElementById('repo-link'); a.href = repo; a.textContent = 'GitHub'; }
const HOSTS = [
  { key: 'claude', title: 'Claude Code', sub: 'Plugin: MCP server, /oncall, /blast-radius, /postmortem, safety hook',
    code: `<span class="c"># inside Claude Code</span>\n/plugin marketplace add ${repoSlug}\n/plugin install rootpath@rootpath\n\n<span class="c"># then</span>\n/rootpath:oncall checkout is throwing 500s`,
    note: 'Read tools are pre-approved. Real actions go through Claude Code\'s permission prompt, and the guard hook refuses any action that was not dry-run first, or anything during a change freeze.' },
  { key: 'codex', title: 'Codex CLI', sub: 'stdio MCP server in config.toml, playbook in AGENTS.md',
    code: `<span class="c"># ~/.codex/config.toml</span>\n[mcp_servers.oncall]\ncommand = <span class="s">"uv"</span>\nargs = [<span class="s">"run"</span>, <span class="s">"--quiet"</span>, <span class="s">"--directory"</span>, <span class="s">"/abs/path/to/rootpath"</span>, <span class="s">"rootpath"</span>, <span class="s">"mcp"</span>]\n\n[mcp_servers.oncall.env]\nFALKORDB_URL = <span class="s">"redis://localhost:6380"</span>`,
    note: 'The same 17 tools. The repository\'s AGENTS.md carries the incident playbook for Codex and other agents that read it.' },
  { key: 'cursor', title: 'Cursor, VS Code, any MCP host', sub: 'one stdio command',
    code: `{\n  <span class="s">"mcpServers"</span>: {\n    <span class="s">"oncall"</span>: {\n      <span class="s">"command"</span>: <span class="s">"uv"</span>,\n      <span class="s">"args"</span>: [<span class="s">"run"</span>, <span class="s">"--directory"</span>, <span class="s">"/abs/path/to/rootpath"</span>, <span class="s">"rootpath"</span>, <span class="s">"mcp"</span>]\n    }\n  }\n}`,
    note: 'Tools carry MCP annotations: read-only tools are marked readOnlyHint, the three remediation tools destructiveHint, so hosts can ask before acting.' },
  { key: 'headless', title: 'Headless responder', sub: 'an alert starts the agent with no human present',
    code: `git clone ${esc(repo || '<repo-url>')} rootpath && cd rootpath\nuv sync\nuv run rootpath demo up      <span class="c"># FalkorDB, 7 services, traffic, history</span>\nuv run rootpath ui           <span class="c"># dashboard on localhost:7100</span>\n\nuv run rootpath chaos flag   <span class="c"># break something</span>\nuv run rootpath watch        <span class="c"># alert -> claude -p with only the oncall tools</span>`,
    note: 'The watcher pages on the most user-facing failing service, launches Claude Code headless with no shell, file or web tools, and stores the run on an Investigation node. --autonomy propose keeps it to dry runs.' },
];
const hostsEl = document.getElementById('hosts');
let currentHost = HOSTS[0];
function showHost(h) {
  currentHost = h;
  document.querySelectorAll('.host').forEach(b => b.setAttribute('aria-selected', b.dataset.key === h.key));
  document.getElementById('snippet').innerHTML = h.code;
  document.getElementById('snippet-note').textContent = h.note;
}
HOSTS.forEach(h => {
  const b = document.createElement('button'); b.className = 'host'; b.setAttribute('role', 'tab'); b.dataset.key = h.key;
  b.innerHTML = `<b>${h.title}</b><span>${h.sub}</span>`; b.onclick = () => showHost(h); hostsEl.appendChild(b);
});
showHost(HOSTS[0]);
document.getElementById('copy').onclick = async e => {
  const text = document.getElementById('snippet').textContent;
  const btn = e.currentTarget;
  try { await navigator.clipboard.writeText(text); btn.textContent = 'Copied'; }
  catch { const r = document.createRange(); r.selectNodeContents(document.getElementById('snippet')); const s = getSelection(); s.removeAllRanges(); s.addRange(r); btn.textContent = 'Selected'; }
  setTimeout(() => { btn.textContent = 'Copy'; }, 1600);
};

// ---------- helpers ----------
function esc(s) { return String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }
function md(src) {
  const lines = src.split('\n'); let html = '', list = null, code = null;
  const inline = s => esc(s).replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>').replace(/(^|\W)\*([^*\s][^*]*)\*(?=\W|$)/g, '$1<i>$2</i>');
  const close = () => { if (list) { html += `</${list}>`; list = null; } };
  for (const line of lines) {
    if (line.startsWith('```')) { if (code === null) { close(); code = ''; } else { html += `<pre><code>${esc(code)}</code></pre>`; code = null; } continue; }
    if (code !== null) { code += line + '\n'; continue; }
    let m;
    if ((m = line.match(/^#{1,4}\s+(.*)/))) { close(); html += `<h3>${inline(m[1])}</h3>`; }
    else if ((m = line.match(/^\s*[-*]\s+(.*)/))) { if (list !== 'ul') { close(); html += '<ul>'; list = 'ul'; } html += `<li>${inline(m[1])}</li>`; }
    else if ((m = line.match(/^\s*\d+\.\s+(.*)/))) { if (list !== 'ol') { close(); html += '<ol>'; list = 'ol'; } html += `<li>${inline(m[1])}</li>`; }
    else if (line.trim() === '') { close(); }
    else if (line.startsWith('|')) { close(); html += `<p class="mono" style="font-size:.78rem;white-space:pre;overflow-x:auto">${esc(line)}</p>`; }
    else { close(); html += `<p>${inline(line)}</p>`; }
  }
  close(); return html;
}

window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => { readColors(); });
new MutationObserver(readColors).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });

const params = new URLSearchParams(location.search);
setPlaying(playing);
load(params.get('scenario') && REPLAYS[params.get('scenario')] ? params.get('scenario') : ORDER[0].key);
if (params.get('t')) { setPlaying(false); step(Number(params.get('t')), true); }
if (params.get('speed')) { speed = Number(params.get('speed')) || 2; document.getElementById('speed').textContent = speed + '×'; }
requestAnimationFrame(loop);
})();
