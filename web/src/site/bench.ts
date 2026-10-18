/** The benchmark section: the same agent with and without the graph tools, under two telemetry levels. */
import { byId, esc } from "../lib/html";
import type { Bench, BenchMode, BenchRow } from "../lib/types";

interface Totals {
  n: number;
  fixed: number;
  clean: number;
  logs: number;
  cost: number;
  time: number;
}

/** The runs of one telemetry level. */
interface Group {
  key: keyof Bench;
  label: string;
  rows: BenchRow[];
  totals: Record<BenchMode, Totals>;
}

const TELEMETRY: [keyof Bench, string][] = [
  ["full", "With tracing"],
  ["basic", "Without tracing"],
];
const MODES: BenchMode[] = ["graph", "baseline"];

function totals(rows: BenchRow[]): Totals {
  const ok = rows.filter((r) => !r.error);
  const sum = (pick: (r: BenchRow) => number | undefined) => ok.reduce((a, r) => a + (pick(r) || 0), 0);
  return {
    n: ok.length,
    fixed: ok.filter((r) => r.correct_action ?? r.fixed).length,
    clean: ok.filter((r) => r.clean).length,
    logs: sum((r) => r.logs_read),
    cost: sum((r) => r.cost_usd),
    time: sum((r) => r.agent_s),
  };
}

export function renderBench(bench: Bench): void {
  const groups: Group[] = TELEMETRY.filter(([key]) => bench[key]?.length).map(([key, label]) => ({
    key,
    label,
    rows: bench[key],
    totals: {
      graph: totals(bench[key].filter((r) => r.mode === "graph")),
      baseline: totals(bench[key].filter((r) => r.mode === "baseline")),
    },
  }));
  if (!groups.length) {
    byId("evidence").hidden = true;
    return;
  }

  const metric = (title: string, key: keyof Totals, fmt: (v: number) => string | number, unit: string) => {
    const max = Math.max(...groups.flatMap((g) => MODES.map((m) => g.totals[m][key])), 1e-9);
    const rows = groups
      .map((g) => MODES.map((m) => `
      <div class="bar-row"><span class="lbl">${g.label}, ${m}</span>
      <span class="bar"><span class="${m === "graph" ? "g" : "b"}" style="width:${((100 * g.totals[m][key]) / max).toFixed(1)}%"></span></span>
      <span class="v">${fmt(g.totals[m][key])}${unit}</span></div>`).join(""))
      .join("");
    return `<div><h3>${title}</h3>${rows}</div>`;
  };
  byId("bars").innerHTML =
    `<div class="key"><span><i style="background:var(--ink)"></i>graph tools</span><span><i style="background:var(--idle)"></i>baseline tools</span></div>` +
    metric("Clean fixes: the right action and nothing else", "clean", (v) => v, ` / ${groups[0].totals.graph.n}`) +
    metric("Service logs read before the fix", "logs", (v) => v, "") +
    metric("Agent time, all runs", "time", (v) => Math.round(v), " s") +
    metric("Cost of all runs", "cost", (v) => "$" + v.toFixed(2), "");

  const findings: [cls: string, title: string, text: string][] = [];
  const full = groups.find((g) => g.key === "full")?.totals;
  const all = groups.flatMap((g) => g.rows.filter((r) => !r.error));
  const sumOf = (mode: BenchMode, key: keyof Totals) => groups.reduce((a, g) => a + g.totals[mode][key], 0);
  const wrongList = (mode: BenchMode) =>
    all
      .filter((r) => r.mode === mode && r.wrong_actions?.length)
      .map((r) => `${(r.wrong_actions ?? []).join(", ")} during ${r.scenario}`);
  if (full) {
    const both = full.graph.fixed === full.graph.n && full.baseline.fixed === full.baseline.n;
    findings.push(["", "With tracing, a frontier model finds the cause either way.",
      `${both ? `Both modes took the right action in all ${full.graph.n} runs.` : `Right action: graph ${full.graph.fixed} of ${full.graph.n}, baseline ${full.baseline.fixed} of ${full.baseline.n}.`} When every service reports where its errors originate, the answer is close to the health table.`]);
  }
  findings.push(["key-f", "The graph sends the agent to where the failure starts.",
    `Across all runs it read ${sumOf("graph", "logs")} service logs before fixing, against ${sumOf("baseline", "logs")} for baseline. The traversal names the origin instead of walking the call chain one hop at a time.`]);
  const gw = wrongList("graph");
  const bw = wrongList("baseline");
  findings.push(["key-f", "Wrong actions happen on coincidences.",
    (bw.length ? `Baseline took ${bw.length} wrong remediation action${bw.length > 1 ? "s" : ""} (${bw.join("; ")}).` : "Baseline took no wrong actions.") + " " +
    (gw.length ? `The graph agent took ${gw.length} (${gw.join("; ")}).` : "The graph agent took none.") +
    " The trap is a healthy release that happens to land on the failing service. The graph hands the agent that service's runbook and past incidents with the ranking, and they say what actually fixes this failure."]);
  const gc = sumOf("graph", "cost");
  const bc = sumOf("baseline", "cost");
  const gt = sumOf("graph", "time");
  const bt = sumOf("baseline", "time");
  findings.push(["", gc > bc ? "It costs a little more per incident." : "It costs no more per incident.",
    `All graph runs cost $${gc.toFixed(2)} against $${bc.toFixed(2)} for baseline, and took ${Math.round(gt)} s of agent time against ${Math.round(bt)} s. The graph runs include work baseline cannot do: opening and resolving the incident, paging the on-call owner, recording why each action was taken.`]);
  byId("findings").innerHTML = findings.map(([cls, title, text]) => `<div class="finding ${cls}"><b>${title}</b><p>${esc(text)}</p></div>`).join("");

  const scenarios = [...new Set(groups.flatMap((g) => g.rows.map((r) => r.scenario)))];
  const cell = (g: Group, scenario: string, mode: BenchMode) => {
    const rs = g.rows.filter((r) => r.scenario === scenario && r.mode === mode && !r.error);
    if (!rs.length) return "<td>–</td>";
    const clean = rs.filter((r) => r.clean).length;
    return `<td class="${clean === rs.length ? "hit" : "miss"}">${clean}/${rs.length}</td>`;
  };
  byId("bench-table").innerHTML =
    `<thead><tr><th>Scenario</th>${groups.map((g) => `<th>${g.label}: graph</th><th>${g.label}: baseline</th>`).join("")}</tr></thead><tbody>` +
    scenarios.map((s) => `<tr><td class="mono">${esc(s)}</td>${groups.map((g) => cell(g, s, "graph") + cell(g, s, "baseline")).join("")}</tr>`).join("") +
    "</tbody>";
  const runs = groups.reduce((a, g) => a + g.rows.length, 0);
  byId("bench-note").textContent = `Clean fix = the expected action on the expected target (rollback payment, flag off, restart inventory) and no other remediation action. ${runs} runs in total, model Claude Opus 5.5 through Claude Code headless. Small samples: read these as directional.`;
}
