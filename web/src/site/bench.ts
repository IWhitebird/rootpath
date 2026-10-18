/** The results: three numbers from the benchmark, the same agent with and without the graph. */
import { byId, esc } from "../lib/html";
import type { Bench, BenchMode, BenchRow } from "../lib/types";

export function renderStats(bench: Bench): void {
  const rows = [...(bench.full ?? []), ...(bench.basic ?? [])].filter((r) => !r.error);
  const sum = (mode: BenchMode, pick: (r: BenchRow) => number | undefined) =>
    rows.filter((r) => r.mode === mode).reduce((a, r) => a + (pick(r) || 0), 0);
  const logs = (m: BenchMode) => sum(m, (r) => r.logs_read);
  const wrong = (m: BenchMode) => sum(m, (r) => r.wrong_actions?.length);
  const time = (m: BenchMode) => sum(m, (r) => r.agent_s);
  const cost = (m: BenchMode) => sum(m, (r) => r.cost_usd);
  const fewer = (a: number, b: number) => Math.round(100 * (1 - a / b));
  if (!rows.length || !logs("baseline") || !time("baseline")) {
    byId("results").hidden = true;
    return;
  }

  const stats: [big: string, text: string][] = [
    [`${fewer(logs("graph"), logs("baseline"))}% fewer`, `logs read before the fix (${logs("graph")} vs ${logs("baseline")})`],
    [`${wrong("graph")} vs ${wrong("baseline")}`, "wrong remediation actions"],
    [`${fewer(time("graph"), time("baseline"))}% less`, `agent time, at the same cost ($${cost("graph").toFixed(2)} vs $${cost("baseline").toFixed(2)})`],
  ];
  byId("stats").innerHTML = stats.map(([big, text]) => `<div class="stat"><b>${big}</b><span>${esc(text)}</span></div>`).join("");
  byId("runs").textContent = String(rows.length);
}
