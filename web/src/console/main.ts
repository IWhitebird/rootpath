/** The local operator console: polls the FastAPI console API (rootpath/web.py) and shows the live
 * graph, service health, the agent's latest investigation, incident memory and recent changes. */
import "./console.css";
import { statusTone } from "../lib/graph";
import { byId, esc } from "../lib/html";
import { getJson } from "../lib/http";
import { markdown } from "../lib/markdown";
import { sortByHealth, stepItem } from "../lib/panels";
import type { Change, GraphData, HealthResponse, Incident, InvestigationResponse, ServiceStats, Step } from "../lib/types";
import { initControls } from "./controls";
import { createGraph, showDetail } from "./view";

let health: Record<string, ServiceStats> = {};
let selectedStep: number | null = null;
/** Services in the graph with the health last synced onto them (the fallback when the demo isn't running). */
let graphServices: { name: string; status?: string }[] = [];
let demoRunning = true;
let hasInvestigation = false;
const graph = createGraph();
graph.status = (name) => health[name]?.status;

async function loadGraph(): Promise<void> {
  let data: GraphData;
  try {
    data = await getJson<GraphData>("/api/graph");
  } catch (err) {
    // A graph that has never been written to does not exist yet, so the API errors instead of returning nothing.
    graphServices = [];
    byId("empty-graph").hidden = false;
    throw err;
  }
  graphServices = data.nodes
    .filter((n) => n.type === "Service")
    .map((n) => ({ name: n.name, status: typeof n.props.status === "string" ? n.props.status : undefined }));
  byId("empty-graph").hidden = graphServices.length > 0;
  graph.layout(data.nodes, data.links);
  graph.show(data.nodes, data.links);
}

async function loadHealth(): Promise<void> {
  let data: HealthResponse;
  try {
    data = await getJson<HealthResponse>("/api/health");
  } catch {
    data = { services: {}, busy: null, allow_actions: false }; // no demo stack to read health from
  }
  // The demo stack is running when at least one of its services answers its stats endpoint.
  demoRunning = Object.values(data.services).some((s) => s.status !== "down" && s.status !== "unknown");
  document.body.classList.toggle("no-demo", !demoRunning);
  byId("env").textContent = demoRunning ? "demo · FalkorDB" : "your graph · FalkorDB";
  byId("health-title").textContent = demoRunning ? "Live health · last 10s" : "Services · last synced health";
  if (!demoRunning) {
    showGraphServices();
    if (!hasInvestigation) {
      byId("caption").innerHTML =
        '<span class="chip">ready</span><span class="text">Ask your agent, for example <code>/rootpath:oncall checkout is failing</code>. Every step it takes appears here, on the nodes it looked at.</span>';
    }
    return;
  }
  health = data.services;
  byId("busy").textContent = data.busy ? `● ${data.busy}` : "";
  for (const id of ["run", "reset", "break-btn"]) byId<HTMLButtonElement>(id).disabled = !!data.busy;
  const box = byId("health");
  box.innerHTML = sortByHealth(Object.entries(health), (s) => s.status)
    .map(([name, s]) => `
    <div class="svc" data-svc="${esc(name)}"><span class="dot" style="background:var(--${statusTone(s.status)})"></span><span>${esc(name)} <span class="v">v${esc(s.version || "?")}</span></span>
    <span class="n ${(s.error_rate || 0) > 0.03 ? "bad" : ""}">${s.status === "down" ? "DOWN" : Math.round((s.error_rate || 0) * 100) + "%"}</span>
    <span class="n ${(s.p95_ms || 0) > 1000 ? "bad" : ""}">${Math.round(s.p95_ms || 0)} ms</span></div>`)
    .join("");
  for (const row of box.querySelectorAll<HTMLElement>(".svc")) {
    row.onclick = () => {
      const n = graph.node(`Service:${row.dataset.svc}`);
      if (!n) return;
      showDetail(graph, n);
      graph.center(n, 400);
    };
  }
  const bad = Object.entries(health)
    .filter(([, s]) => s.status === "failing" || s.status === "down")
    .map(([name]) => name);
  byId("status").classList.toggle("bad", bad.length > 0);
  byId("status-text").textContent = bad.length ? `${bad.length} service${bad.length > 1 ? "s" : ""} failing: ${bad.join(", ")}` : "All services healthy";
}

/** Without the demo, list the services in the graph with whatever health was last synced onto them. */
function showGraphServices(): void {
  health = Object.fromEntries(graphServices.filter((s) => s.status).map((s) => [s.name, { status: s.status } as ServiceStats]));
  byId("health").innerHTML = graphServices.length
    ? sortByHealth(graphServices.map((s) => [s.name, s] as [string, { status?: string }]), (s) => s.status ?? "unknown")
        .map(([name, s]) => `<div class="svc"><span class="dot" style="background:var(--${statusTone(s.status ?? "unknown")})"></span><span>${esc(name)}</span>
          <span class="n">${esc(s.status ?? "no data")}</span><span class="n"></span></div>`)
        .join("")
    : '<p class="empty">No services yet.</p>';
  byId("status").classList.remove("bad");
  byId("status-text").textContent = graphServices.length ? `${graphServices.length} services in your graph` : "Graph is empty";
}

const stepCaption = (s: Step) => `<span class="chip step">step ${s.n}</span><span class="text"><code>${esc(s.tool)}</code> ${esc(s.note || "")}</span>`;

async function loadInvestigation(): Promise<void> {
  const { investigation: inv, steps } = await getJson<InvestigationResponse>("/api/investigation");
  if (!inv) return;
  hasInvestigation = true;
  const last = steps.at(-1);
  const selected = steps.find((s) => s.n === selectedStep);
  graph.setTrail(steps, (selectedStep === null ? last : selected)?.targets ?? []);
  const meta = [
    inv.agent,
    inv.mode && `${inv.mode} tools`,
    inv.alert,
    inv.duration_s && `${inv.duration_s}s`,
    inv.tool_calls && `${inv.tool_calls} tool calls`,
    inv.cost_usd ? `$${inv.cost_usd.toFixed(3)}` : null,
  ]
    .filter(Boolean)
    .join(" · ");
  byId("inv-meta").textContent = `${inv.id}${meta ? " · " + meta : ""}`;

  const box = byId("steps");
  box.innerHTML = steps.length
    ? steps.map((s, i) => stepItem(s, `${i === steps.length - 1 && selectedStep === null ? "on" : ""} ${selectedStep === s.n ? "sel" : ""}`)).join("")
    : '<p class="empty">The agent is reading the graph…</p>';
  for (const row of box.querySelectorAll<HTMLElement>(".step")) {
    row.onclick = () => {
      const n = Number(row.dataset.n);
      selectedStep = selectedStep === n ? null : n;
      loadInvestigation().catch(() => {});
    };
  }

  const report = byId("report");
  report.hidden = !inv.report;
  if (inv.report) report.innerHTML = "<h4>Agent report</h4>" + markdown(inv.report);

  const caption = byId("caption");
  if (selectedStep !== null) {
    if (selected) caption.innerHTML = stepCaption(selected);
  } else if (last && !inv.finished_at) caption.innerHTML = stepCaption(last);
  else if (inv.finished_at) {
    caption.innerHTML = `<span class="chip ok">done</span><span class="text">${esc(inv.alert || inv.id)}: finished in ${inv.duration_s || "?"}s with ${inv.tool_calls || steps.length} tool calls. Click a step to see what it looked at.</span>`;
  }
  byId("kv-agent").innerHTML = inv.finished_at
    ? `last agent run <b>${inv.duration_s ?? "–"}s</b> · <b>${inv.tool_calls ?? steps.length}</b> calls`
    : `agent <b>working</b> · step <b>${last ? last.n : 0}</b>`;
}

function incidentItem(r: Incident): string {
  const rootCause = r.root_cause.length ? "root cause " + esc(r.root_cause.join(", ")) : "";
  const fixedBy = r.fixed_by.length ? " · fixed by " + r.fixed_by.map((a) => `${esc(a.kind)} ${esc(a.target)} (${esc(a.by)})`).join(", ") : "";
  return `<div class="inc"><div class="row"><span class="id">${esc(r.id)}</span>
    <span class="tag ${r.status === "open" ? "open" : ""}">${esc(r.status)}</span>${r.source === "agent" ? '<span class="tag agent">agent</span>' : ""}
    ${r.severity ? `<span class="tag">${esc(r.severity)}</span>` : ""}</div><div class="title">${esc(r.title || "")}</div>
    <div class="sub">${rootCause}${fixedBy}${r.mttr_min != null ? ` · ${r.mttr_min} min` : ""}</div></div>`;
}

async function loadIncidents(): Promise<void> {
  const rows = await getJson<Incident[]>("/api/incidents");
  byId("inc-count").textContent = String(rows.length);
  const open = rows.find((r) => r.status === "open");
  byId("kv-inc").innerHTML = open ? `open <b>${esc(open.id)}</b> ${esc(open.severity || "")}` : "";
  const timed = rows.filter((r) => r.mttr_min != null);
  const agent = timed.filter((r) => r.source === "agent");
  const human = timed.filter((r) => r.source !== "agent");
  const avg = (rs: Incident[]) => rs.reduce((a, r) => a + (r.mttr_min ?? 0), 0) / rs.length;
  byId("kv-mem").innerHTML =
    `memory <b>${rows.length}</b> incidents` +
    (agent.length && human.length ? ` · MTTR human <b>${avg(human).toFixed(0)} min</b>, agent <b>${avg(agent).toFixed(1)} min</b>` : "");
  byId("panel-inc").innerHTML = rows.length ? rows.map(incidentItem).join("") : '<p class="empty">No incidents yet.</p>';
}

function ago(at: number): string {
  const s = Date.now() / 1000 - at;
  return s < 120 ? `${Math.round(s)}s` : s < 7200 ? `${Math.round(s / 60)}m` : `${Math.round(s / 3600)}h`;
}

function changeItem(c: Change): string {
  const what =
    c.kind === "deploy"
      ? `<b>${esc(c.service)}</b> v${esc(c.prev_version || "?")} → v${esc(c.version)}`
      : `flag <b>${esc(c.flag)}</b> ${esc(c.from)} → ${esc(c.to)}`;
  return `<div class="chg"><span class="when">${ago(c.at)} ago</span><div>
    ${what}
    <span class="kv"> by ${esc(c.author)}</span>${c.note ? `<div class="kv">${esc(c.note)}</div>` : ""}</div></div>`;
}

async function loadChanges(): Promise<void> {
  const rows = await getJson<Change[]>("/api/changes");
  byId("chg-count").textContent = String(rows.length);
  byId("panel-chg").innerHTML = rows.length ? rows.map(changeItem).join("") : '<p class="empty">No deploys or flag flips in the last 24 hours.</p>';
}

/** Run `load` now and again `ms` after each run settles. A failed poll is retried on the next one. */
function every(ms: number, load: () => Promise<void>): void {
  const run = () => {
    load()
      .catch(() => {})
      .finally(() => setTimeout(run, ms));
  };
  run();
}

initControls({
  refresh: () => {
    for (const load of [loadHealth, loadGraph]) load().catch(() => {});
  },
  deselect: () => {
    selectedStep = null;
  },
});
every(4000, loadGraph);
every(2000, loadHealth);
every(1500, loadInvestigation);
every(5000, loadIncidents);
every(5000, loadChanges);
