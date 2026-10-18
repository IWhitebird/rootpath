/** The benchmark section: the same agent with and without the graph tools, under two telemetry levels. */
import { byId, esc } from "../lib/html";
import type { Bench, BenchMode, BenchRow } from "../lib/types";

interface Totals {
  n: number;
  clean: number;
  logs: number;
  cost: number;
  time: number;
  wrong: number;
}

/** The runs of one telemetry level. */
interface Group {
  label: string;
  rows: BenchRow[];
  totals: Record<BenchMode, Totals>;
}

const TELEMETRY: [keyof Bench, string][] = [
  ["full", "With tracing"],
  ["basic", "Without tracing"],
];
const MODES: BenchMode[] = ["graph", "baseline"];
const RESULTS = "https://github.com/IWhitebird/rootpath/tree/main/bench";

function totals(rows: BenchRow[]): Totals {
  const ok = rows.filter((r) => !r.error);
  const sum = (pick: (r: BenchRow) => number | undefined) => ok.reduce((a, r) => a + (pick(r) || 0), 0);
  return {
    n: ok.length,
    clean: ok.filter((r) => r.clean).length,
    logs: sum((r) => r.logs_read),
    cost: sum((r) => r.cost_usd),
    time: sum((r) => r.agent_s),
    wrong: sum((r) => r.wrong_actions?.length),
  };
}

/** What went wrong in a run that was not a clean fix, in a few words. */
function miss(r: BenchRow): string {
  const extra = (r.wrong_actions ?? []).map((a) => a.replace(/^rollback /, "rolled back ").replace(/^restart /, "restarted "));
  if (!(r.correct_action ?? r.fixed)) return "did not fix it";
  return `also ${extra.join(" and ")} before the right fix`;
}

export function renderBench(bench: Bench): void {
  const groups: Group[] = TELEMETRY.filter(([key]) => bench[key]?.length).map(([key, label]) => ({
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

  const metric = (title: string, key: keyof Totals, fmt: (v: number) => string) => {
    const max = Math.max(...groups.flatMap((g) => MODES.map((m) => g.totals[m][key])), 1e-9);
    const rows = groups
      .map((g) => MODES.map((m) => `
      <div class="bar-row"><span class="lbl">${g.label.toLowerCase()}, ${m}</span>
      <span class="bar"><span class="${m === "graph" ? "g" : "b"}" style="width:${((100 * g.totals[m][key]) / max).toFixed(1)}%"></span></span>
      <span class="v">${fmt(g.totals[m][key])}</span></div>`).join(""))
      .join("");
    return `<div><h3>${title}</h3>${rows}</div>`;
  };
  byId("bars").innerHTML =
    `<div class="key"><span><i style="background:var(--ink)"></i>graph</span><span><i style="background:var(--idle)"></i>baseline</span></div>` +
    metric("Service logs read before the fix", "logs", String) +
    metric("Agent time", "time", (v) => `${Math.round(v)} s`) +
    metric("Cost", "cost", (v) => `$${v.toFixed(2)}`);

  const all = (mode: BenchMode, key: keyof Totals) => groups.reduce((a, g) => a + g.totals[mode][key], 0);
  const pct = (a: number, b: number) => Math.round(100 * (1 - a / b));
  const stats: [big: string, text: string][] = [
    [`${pct(all("graph", "logs"), all("baseline", "logs"))}% fewer`, `service logs read before the fix (${all("graph", "logs")} vs ${all("baseline", "logs")})`],
    [`${all("graph", "wrong")} vs ${all("baseline", "wrong")}`, "wrong remediation actions"],
    [`${pct(all("graph", "time"), all("baseline", "time"))}% faster`, `at the same cost ($${all("graph", "cost").toFixed(2)} vs $${all("baseline", "cost").toFixed(2)})`],
  ];
  byId("findings").innerHTML = stats.map(([big, text]) => `<div class="stat"><b>${big}</b><span>${esc(text)}</span></div>`).join("");

  const scenarios = [...new Set(groups.flatMap((g) => g.rows.map((r) => r.scenario)))];
  const notes: string[] = [];
  const cell = (rs: BenchRow[], where: string) => {
    rs = rs.filter((r) => !r.error);
    if (!rs.length) return "<td>–</td>";
    const clean = rs.filter((r) => r.clean).length;
    let mark = "";
    for (const r of rs.filter((r) => !r.clean)) {
      notes.push(`${where}: one run ${miss(r)}.`);
      mark += "*".repeat(notes.length);
    }
    return `<td class="${clean === rs.length ? "hit" : "miss"}">${clean}/${rs.length}${mark}</td>`;
  };
  const body = scenarios.map((s) => `<tr><td class="mono">${esc(s)}</td>${groups.map((g) => MODES.map((m) =>
    cell(g.rows.filter((r) => r.scenario === s && r.mode === m), `${s}, ${g.label.toLowerCase()}, ${m}`)).join("")).join("")}</tr>`);
  const total = `<tr class="total"><td>all</td>${groups.map((g) => MODES.map((m) =>
    `<td>${g.totals[m].clean}/${g.totals[m].n}</td>`).join("")).join("")}</tr>`;
  byId("bench-table").innerHTML =
    `<thead><tr><th rowspan="2">Clean fixes</th>${groups.map((g) => `<th colspan="2">${g.label}</th>`).join("")}</tr>` +
    `<tr>${groups.map(() => MODES.map((m) => `<th>${m}</th>`).join("")).join("")}</tr></thead>` +
    `<tbody>${body.join("")}${total}</tbody>`;

  const runs = groups.reduce((a, g) => a + g.rows.length, 0);
  byId("bench-note").innerHTML =
    notes.map((n, i) => `${"*".repeat(i + 1)} ${esc(n)}<br>`).join("") +
    `A clean fix is the expected action on the expected target and nothing else. ${runs} runs, Claude Opus 5.5 through Claude Code headless, two per cell. <a href="${RESULTS}">Every run</a>.`;
}
