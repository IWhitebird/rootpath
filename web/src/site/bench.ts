/** The results: the same agent with and without the graph, per incident, from the benchmark. */
import { byId } from "../lib/html";
import type { Bench, BenchMode, BenchRow } from "../lib/types";

interface Side {
  value: string;
  /** Bar length, in percent of the larger of the two values. */
  bar: number;
}

function card(big: string, what: string, us: Side, them: Side): string {
  const row = (label: string, s: Side, cls: string) => `
    <div class="row ${cls}"><span class="who">${label}</span>
      <span class="bar"><i style="width:${s.bar.toFixed(1)}%"></i></span>
      <span class="val">${s.value}</span></div>`;
  return `<div class="card"><b>${big}</b><span class="what">${what}</span>
    <div class="pair">${row("Rootpath", us, "us")}${row("Without", them, "")}</div></div>`;
}

export function renderComparison(bench: Bench): void {
  const all = [...(bench.full ?? []), ...(bench.basic ?? [])].filter((r) => !r.error);
  const runs = (m: BenchMode) => all.filter((r) => r.mode === m);
  const mean = (m: BenchMode, pick: (r: BenchRow) => number | undefined) =>
    runs(m).reduce((a, r) => a + (pick(r) || 0), 0) / Math.max(runs(m).length, 1);
  if (!runs("graph").length || !runs("baseline").length) {
    byId("results").hidden = true;
    return;
  }

  const measured = (pick: (r: BenchRow) => number | undefined, fmt: (v: number) => string) => {
    const us = mean("graph", pick);
    const them = mean("baseline", pick);
    const max = Math.max(us, them, 1e-9);
    return {
      fewer: Math.round(100 * (1 - us / them)),
      us: { value: fmt(us), bar: (100 * us) / max },
      them: { value: fmt(them), bar: (100 * them) / max },
    };
  };
  const logs = measured((r) => r.logs_read, (v) => String(Number(v.toFixed(1))));
  const time = measured((r) => r.agent_s, (v) => `${Math.round(v)} s`);
  const wrongFixes = (m: BenchMode) => runs(m).reduce((a, r) => a + (r.wrong_actions?.length ?? 0), 0);
  const yesNo = (v: boolean) => `<span class="val ${v ? "good" : "bad"}">${v ? "Yes" : "No"}</span>`;
  const check = (label: string, us: boolean, them: boolean) =>
    `<div class="check"><span>${label}</span>${yesNo(us)}${yesNo(them)}</div>`;
  const fewerWrong = wrongFixes("graph") < wrongFixes("baseline");

  byId("compare").innerHTML = [
    card(`${logs.fewer}% fewer`, "logs read per incident", logs.us, logs.them),
    card(`${time.fewer}% less`, "time per incident", time.us, time.them),
    `<div class="card checks"><div class="check head"><span></span><span>Rootpath</span><span>Without</span></div>` +
      check("Fewer wrong fixes", fewerWrong, !fewerWrong) +
      check("Remembers past incidents", true, false) +
      "</div>",
  ].join("");
  byId("runs").textContent = String(all.length);
}
