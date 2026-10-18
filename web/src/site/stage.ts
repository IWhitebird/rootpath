/** The hero console's panels: graph, caption, health, steps, report and the timeline row.
 * The recorded replays and the live view both draw into it. */
import { GraphView, statusTone, type GraphStyle } from "../lib/graph";
import { byId, esc } from "../lib/html";
import { sortByHealth, stepItem } from "../lib/panels";
import type { HealthSample, Step } from "../lib/types";

const STYLE: GraphStyle = {
  radius: { Service: 8, Change: 4.5, Flag: 4.5, Incident: 5.5, Action: 4.5, Team: 4.5, Person: 3.6, Runbook: 3.6 },
  font: { service: 11.5, other: 9, badge: 8.5, badgeHeight: 11 },
  serviceOutline: "surface",
  incidentAlpha: 0.8,
  labelAllFrom: Infinity,
  callWidth: 1.3,
  arrowLength: 4.5,
  charge: -55,
  chargeRange: 120,
  callDistance: 130,
  linkDistance: 30,
  cooldownTicks: 140,
  column: 135,
  row: 105,
  offsets: { Change: [0, -58], Flag: [26, -86], Team: [0, 62], Person: [0, 98], Runbook: [-40, 48], Action: [36, -40] },
  bandPad: 0,
  bandDrop: 150,
  zoom: false,
  fitPadding: 64,
  fitOnce: false,
};

export type ChipKind = "" | "alert" | "step" | "ok";

export class Stage {
  readonly graph = new GraphView(byId("graph"), { style: STYLE });
  private readonly replayOnly = ["play", "speed", "restart", "track"].map((id) => byId(id));
  private readonly liveStatusEl = byId("live-status");
  private readonly offlineEl = byId("offline");
  private shownSteps = -1;
  private shownEmpty = "";

  /** Switch the chrome between a recording (transport, scrubber) and the live graph (status line). */
  setMode(live: boolean): void {
    for (const el of this.replayOnly) el.hidden = live;
    this.liveStatusEl.hidden = !live;
    byId("health-title").textContent = live ? "Live health · from FalkorDB" : "Live health · last 8s";
    if (!live) this.offline(null);
  }

  /** `html` is trusted markup; callers escape what they interpolate. */
  caption(chip: string, kind: ChipKind, html: string): void {
    byId("caption").innerHTML = `<span class="chip ${kind}">${chip}</span><span class="text">${html}</span>`;
  }

  clock(text: string): void {
    byId("clock").textContent = text;
  }

  health(services: Record<string, HealthSample>, empty: string): void {
    const rows = sortByHealth(Object.entries(services), ([status]) => status);
    byId("health").innerHTML = rows.length
      ? rows.map(([name, [st, er, p95]]) => `
    <div class="svc" title="${esc(name)}: ${esc(st)}"><span class="dot" style="background:var(--${statusTone(st)})"></span><span>${esc(name)}</span>
      <span class="n ${er > 0.03 ? "bad" : ""}">${st === "down" ? "down" : Math.round(er * 100) + "%"}</span>
      <span class="n ${p95 > 1000 ? "bad" : ""}">${Math.round(p95)} ms</span></div>`).join("")
      : `<p class="empty">${empty}</p>`;
  }

  /** Re-render the step list when it changed length, or the empty list when its message changed;
   * a step that just arrived slides in. */
  steps(steps: readonly Step[], empty: string): void {
    const box = byId("steps");
    if (!steps.length) {
      if (this.shownSteps === 0 && this.shownEmpty === empty) return;
      box.innerHTML = `<p class="empty">${empty}</p>`;
      this.shownSteps = 0;
      this.shownEmpty = empty;
      return;
    }
    if (steps.length === this.shownSteps) return;
    const grew = steps.length > this.shownSteps && this.shownSteps >= 0;
    const last = steps.length - 1;
    box.innerHTML = steps.map((s, i) => stepItem(s, i === last ? `on ${grew ? "enter" : ""}` : "")).join("");
    const scroller = box.parentElement;
    if (scroller) scroller.scrollTop = scroller.scrollHeight;
    this.shownSteps = steps.length;
  }

  /** Forget the rendered step list, so the next steps() redraws it without the slide-in. */
  resetSteps(): void {
    this.shownSteps = -1;
  }

  report(html: string, meta: string): void {
    byId("report-body").innerHTML = html;
    byId("report-meta").textContent = meta;
  }

  openReport(open: boolean): void {
    byId<HTMLDetailsElement>("report").open = open;
  }

  /** The live status line that takes the timeline's place; each part is trusted markup. */
  liveStatus(parts: string[], bad: boolean): void {
    this.liveStatusEl.classList.toggle("bad", bad);
    this.liveStatusEl.innerHTML = parts.map((p) => `<span>${p}</span>`).join("");
  }

  /** Show the offline state over the graph area, or hide it with `null`. */
  offline(error: string | null): void {
    this.offlineEl.hidden = error === null;
    if (error !== null) byId("offline-error").textContent = error;
  }
}
