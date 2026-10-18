/** The results: the same agent with and without the graph, per incident, from the benchmark. */
import { byId } from "../lib/html";
import type { Bench, BenchMode, BenchRow } from "../lib/types";

interface Side {
  value: string;
  /** Bar length in percent, for measured values; yes/no answers have no bar. */
  bar?: number;
  tone?: "good" | "bad";
}

function card(big: string, what: string, us: Side, them: Side): string {
  const row = (label: string, s: Side, cls: string) => `
    <div class="row ${cls}"><span class="who">${label}</span>
      <span class="bar">${s.bar === undefined ? "" : `<i style="width:${s.bar.toFixed(1)}%"></i>`}</span>
      <span class="val ${s.tone ?? ""}">${s.value}</span></div>`;
  return `<div class="card"><b>${big}</b><span class="what">${what}</span>
    <div class="pair">${row("Rootpath", us, "us")}${row("Without", them, "")}</div></div>`;
}

export function renderComparison(bench: Bench): void {
  const all = [...(bench.full ?? []), ...(bench.basic ?? [])].filter((r) => !r.error);
  const runs = (m: BenchMode) => all.filter((r) => r.mode === m);
  const mean = (m: BenchMode, pick: (r: BenchRow) => number | undefined) =>
    runs(m).reduce((a, r) => a + (pick(r) || 0), 0) / Math.max(runs(m).length, 1);
  const wrong = (m: BenchMode) => runs(m).some((r) => r.wrong_actions?.length);
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
  const yes = (v: boolean, goodWhen: boolean): Side => ({ value: v ? "Yes" : "No", tone: v === goodWhen ? "good" : "bad" });

  byId("compare").innerHTML = [
    card(`${logs.fewer}% fewer`, "logs read per incident", logs.us, logs.them),
    card(`${time.fewer}% less`, "time per incident", time.us, time.them),
    card("No wrong fixes", "a fix aimed at the wrong service", yes(wrong("graph"), false), yes(wrong("baseline"), false)),
    card("Remembers", "each incident, for next time", yes(true, true), yes(false, true)),
  ].join("");
  byId("runs").textContent = String(all.length);
}
