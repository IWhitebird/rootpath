/** The console's graph: the shared renderer, a legend that toggles node types, and a detail card. */
import { GraphView, TYPE_TONE, type GraphStyle, type SimNode, type Tone } from "../lib/graph";
import { byId, esc } from "../lib/html";

const STYLE: GraphStyle = {
  radius: { Service: 9, Change: 5, Flag: 5, Incident: 6, Action: 5, Team: 5, Person: 4, Runbook: 4 },
  font: { service: 12, other: 9.5, badge: 9, badgeHeight: 11.5 },
  serviceOutline: "bg",
  incidentAlpha: 1,
  labelAllFrom: 1.6,
  callWidth: 1.4,
  arrowLength: 5,
  charge: -60,
  chargeRange: 130,
  callDistance: 140,
  linkDistance: 32,
  cooldownTicks: 160,
  column: 150,
  row: 115,
  offsets: { Change: [0, -62], Flag: [28, -92], Team: [0, 64], Person: [0, 102], Runbook: [-42, 50], Action: [38, -42] },
  bandPad: 40,
  bandDrop: 170,
  zoom: true,
  fitPadding: 60,
  fitOnce: true,
};

/** Legend swatches in order; services are drawn in their health colour, the legend shows the accent. */
const LEGEND: Record<string, Tone> = { Service: "accent", ...TYPE_TONE };
const shown = new Set(Object.keys(LEGEND).filter((type) => type !== "Runbook"));
const TIMESTAMPS = new Set(["at", "health_at", "opened_at", "resolved_at", "observed_at", "started_at", "updated_at", "finished_at"]);

export function createGraph(): GraphView {
  const graph: GraphView = new GraphView(byId("graph"), {
    style: STYLE,
    visible: (n) => shown.has(n.type),
    onNodeClick: (n) => showDetail(graph, n),
    onBackgroundClick: hideDetail,
  });
  const legend = byId("legend");
  legend.innerHTML = Object.entries(LEGEND)
    .map(([type, tone]) => `<label><input type="checkbox" data-type="${type}" ${shown.has(type) ? "checked" : ""}><i style="background:var(--${tone})"></i>${type}</label>`)
    .join("");
  legend.addEventListener("change", (e) => {
    const box = e.target as HTMLInputElement;
    const type = box.dataset.type ?? "";
    if (box.checked) shown.add(type);
    else shown.delete(type);
    graph.render();
  });
  return graph;
}

export function showDetail(graph: GraphView, n: SimNode): void {
  const box = byId("detail");
  const rows = Object.entries(n.props)
    .filter(([k, v]) => v !== null && v !== "" && !TIMESTAMPS.has(k))
    .map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(typeof v === "number" ? Math.round(v * 1000) / 1000 : String(v))}</dd>`)
    .join("");
  const steps = graph.stepsAt(n.id);
  box.innerHTML = `<button class="x" aria-label="Close">×</button><div class="type">${esc(n.type)}</div><h3>${esc(n.name)}</h3>
    ${steps ? `<div class="meta">looked at in step${steps.length > 1 ? "s" : ""} ${steps.join(", ")}</div>` : ""}<dl>${rows}</dl>`;
  const close = box.querySelector<HTMLButtonElement>(".x");
  if (close) close.onclick = hideDetail;
  box.hidden = false;
}

export function hideDetail(): void {
  byId("detail").hidden = true;
}
