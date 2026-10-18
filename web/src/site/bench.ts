/** The results: the same agent with and without the graph, per incident, from the benchmark. */
import { byId, esc } from "../lib/html";
import type { Bench, BenchMode, BenchRow } from "../lib/types";

/** "rollback fraud-check" -> "rolled back fraud-check, which was healthy" */
function describe(action: string): string {
  const [kind, target] = action.split(" ");
  const verb: Record<string, string> = { rollback: "rolled back", restart: "restarted", flag: "flipped" };
  return `${verb[kind] ?? kind} ${target}, which was healthy`;
}

export function renderComparison(bench: Bench): void {
  const all = [...(bench.full ?? []), ...(bench.basic ?? [])].filter((r) => !r.error);
  const runs = (m: BenchMode) => all.filter((r) => r.mode === m);
  const mean = (m: BenchMode, pick: (r: BenchRow) => number | undefined) =>
    runs(m).reduce((a, r) => a + (pick(r) || 0), 0) / Math.max(runs(m).length, 1);
  const wrong = (m: BenchMode) => runs(m).flatMap((r) => r.wrong_actions ?? []);
  if (!runs("graph").length || !runs("baseline").length) {
    byId("results").hidden = true;
    return;
  }

  const logs = (m: BenchMode) => String(Number(mean(m, (r) => r.logs_read).toFixed(1)));
  const time = (m: BenchMode) => `${Math.round(mean(m, (r) => r.agent_s))} s`;
  const cost = (m: BenchMode) => `$${mean(m, (r) => r.cost_usd).toFixed(2)}`;
  const fixes = (m: BenchMode) => {
    const w = wrong(m);
    return w.length ? `${w.length} <small>${esc(w.map(describe).join("; "))}</small>` : "0";
  };
  const rows: [label: string, graph: string, baseline: string][] = [
    ["Logs read per incident", logs("graph"), logs("baseline")],
    ["Time per incident", time("graph"), time("baseline")],
    ["Wrong fixes", fixes("graph"), fixes("baseline")],
    ["Cost per incident", cost("graph"), cost("baseline")],
    ["Remembers the incident", "yes", "no"],
  ];
  byId("compare").innerHTML =
    `<thead><tr><th></th><th class="us">With Rootpath</th><th>Without the graph</th></tr></thead><tbody>` +
    rows.map(([label, g, b]) => `<tr><th scope="row">${label}</th><td class="us">${g}</td><td>${b}</td></tr>`).join("") +
    "</tbody>";
  byId("runs").textContent = String(all.length);
}
