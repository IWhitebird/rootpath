/** Plays a recorded incident back in the hero console: health, graph, agent steps and captions
 * follow the recording's clock, with play/pause, speed, restart and a scrubbable timeline. */
import { byId, esc, prefersReducedMotion } from "../lib/html";
import { markdown } from "../lib/markdown";
import type { HealthFrame, HealthSample, Replay, ReplayEventKind, ReplayStep } from "../lib/types";
import type { ChipKind, Stage } from "./stage";

export interface Tab {
  key: string;
  title: string;
  sub: string;
}

export const RECORDINGS: Tab[] = [
  { key: "flag", title: "Flag flip, three hops down", sub: "fraud-check slows, frontend fails" },
  { key: "pool-after-deploy", title: "Old bug, new release", sub: "restart, not rollback" },
  { key: "bad-deploy", title: "Bad deploy, red herring", sub: "payment v2.4.1" },
];

export interface Recording extends Replay, Tab {
  /** When the failure was injected and when the pager fired, in recording seconds. */
  injectT: number;
  alertT: number | undefined;
}

export function prepare(tab: Tab, replay: Replay): Recording {
  return {
    ...replay,
    ...tab,
    nodes: replay.nodes.map((n) => ({ ...n, t: Math.max(0, n.t - 0.5) })),
    injectT: replay.events.find((e) => e.kind === "inject")?.t ?? 0,
    alertT: replay.events.find((e) => e.kind === "alert")?.t,
  };
}

const SPEEDS = [1, 2, 4];
const TRACK_LABELS: Partial<Record<ReplayEventKind, string>> = { inject: "failure", alert: "alert", done: "fixed" };
const PAUSE_ICON =
  '<svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><rect x="2" y="1.5" width="3.4" height="11" rx="1" fill="currentColor"/><rect x="8.6" y="1.5" width="3.4" height="11" rx="1" fill="currentColor"/></svg>';
const PLAY_ICON = '<svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><path d="M3 1.8v10.4L12 7z" fill="currentColor"/></svg>';

const pad = (n: number) => String(n).padStart(2, "0");

function healthAt(frames: HealthFrame[], t: number): Record<string, HealthSample> {
  let frame: HealthFrame | undefined = frames[0];
  for (const f of frames) {
    if (f.t > t) break;
    frame = f;
  }
  return frame?.s ?? {};
}

export class Player {
  private readonly stage: Stage;
  private readonly recordings: Map<string, Recording>;
  private readonly scrub = byId<HTMLInputElement>("scrub");
  private rec: Recording | null = null;
  private t = 0;
  private playing = false;
  private speed = 2;
  private lastFrame = 0;
  private health: Record<string, HealthSample> = {};

  constructor(stage: Stage, recordings: Recording[]) {
    this.stage = stage;
    this.recordings = new Map(recordings.map((r) => [r.key, r]));
    byId("play").onclick = () => {
      if (!this.rec) return;
      if (this.t >= this.rec.duration) this.t = 0;
      this.setPlaying(!this.playing);
    };
    byId("restart").onclick = () => {
      this.t = 0;
      this.rewind();
      this.stage.openReport(false);
      this.step(0);
      this.setPlaying(true);
    };
    byId("speed").onclick = () => this.setSpeed(SPEEDS[(SPEEDS.indexOf(this.speed) + 1) % SPEEDS.length]);
    this.scrub.oninput = () => {
      if (!this.rec) return;
      const target = (this.rec.duration * Number(this.scrub.value)) / 1000;
      if (target < this.t) this.rewind();
      this.t = target;
      this.step(0);
    };
    this.setPlaying(!prefersReducedMotion);
    requestAnimationFrame(this.loop);
  }

  load(key: string): void {
    const rec = this.recordings.get(key);
    if (!rec) return;
    this.rec = rec;
    this.t = 0;
    const { graph } = this.stage;
    graph.status = (name) => this.health[name]?.[0];
    graph.layout(rec.nodes, rec.links);
    this.rewind();
    this.buildTrack(rec);
    this.stage.report(
      markdown(rec.agent.report || ""),
      `${rec.agent.duration_s.toFixed(0)} s · ${rec.agent.tool_calls} tool calls · $${(rec.agent.cost_usd || 0).toFixed(2)} · recorded ${rec.recorded_at}`,
    );
    this.stage.openReport(false);
    this.step(0);
    setTimeout(() => graph.fit(400), 900);
  }

  setPlaying(playing: boolean): void {
    this.playing = playing;
    const button = byId("play");
    button.setAttribute("aria-label", playing ? "Pause replay" : "Play replay");
    button.innerHTML = playing ? PAUSE_ICON : PLAY_ICON;
  }

  setSpeed(speed: number): void {
    this.speed = speed;
    byId("speed").textContent = `${speed}×`;
  }

  /** Pause and jump to `t` seconds into the recording. */
  seek(t: number): void {
    this.setPlaying(false);
    this.t = 0;
    this.step(t);
  }

  private readonly loop = (ts: number): void => {
    const dt = this.lastFrame ? Math.min(0.1, (ts - this.lastFrame) / 1000) : 0;
    this.lastFrame = ts;
    if (this.playing) this.step(dt * this.speed);
    requestAnimationFrame(this.loop);
  };

  /** Going back in time: nodes are placed afresh as they reappear. */
  private rewind(): void {
    this.stage.graph.clear();
    this.stage.resetSteps();
  }

  private step(dt: number): void {
    const rec = this.rec;
    if (!rec) return;
    const t = (this.t = Math.min(rec.duration, this.t + dt));
    this.health = healthAt(rec.health, t);
    const nodes = rec.nodes.filter((n) => n.t <= t);
    const ids = new Set(nodes.map((n) => n.id));
    const { graph } = this.stage;
    graph.show(nodes, rec.links.filter((l) => l.t <= t && ids.has(l.source) && ids.has(l.target)));
    const steps = rec.steps.filter((s) => s.t <= t);
    graph.setTrail(steps);
    this.stage.health(this.health, "");
    this.stage.steps(steps, t < (rec.alertT ?? -Infinity) ? "Waiting for the alert." : "Agent is reading the graph.");
    this.caption(rec, steps);
    const rel = t - rec.injectT;
    const s = Math.abs(Math.floor(rel));
    this.stage.clock(`T${rel < 0 ? "−" : "+"}${pad(Math.floor(s / 60))}:${pad(s % 60)}`);
    byId("fill").style.width = `${(100 * t) / rec.duration}%`;
    this.scrub.value = String(Math.round((1000 * t) / rec.duration));
    if (t >= rec.duration && this.playing) {
      this.setPlaying(false);
      this.stage.openReport(true);
    }
  }

  private caption(rec: Recording, steps: ReplayStep[]): void {
    // The "agent" event marks the agent's start and shares the alert's moment; it has no caption of its own.
    const ev = rec.events.filter((e) => e.t <= this.t && e.kind !== "agent").at(-1);
    const last = steps.at(-1);
    let chip = "healthy";
    let kind: ChipKind = "";
    let text = "Seven services under steady traffic. Every node is in FalkorDB.";
    if (ev?.kind === "inject") {
      chip = "failure";
      kind = "alert";
      text = esc(ev.text);
    }
    if (ev?.kind === "alert") {
      chip = "paged";
      kind = "alert";
      text = `${esc(ev.text)}. A headless agent starts with only the Rootpath tools.`;
    }
    if (last && (ev?.kind !== "done" || last.t > ev.t)) {
      chip = `step ${last.n}`;
      kind = "step";
      text = `<code>${esc(last.tool)}</code> ${esc(last.note || "")}`;
    }
    if (ev?.kind === "done" && (!last || ev.t >= last.t)) {
      chip = "resolved";
      kind = "ok";
      text = `Fixed and verified in ${rec.agent.duration_s.toFixed(0)} s with ${rec.agent.tool_calls} tool calls. The incident is stored with its root cause.`;
    }
    this.stage.caption(chip, kind, text);
  }

  private buildTrack(rec: Recording): void {
    const track = byId("track");
    for (const old of track.querySelectorAll(".tick, .ev")) old.remove();
    const pct = (x: number) => `${((100 * x) / rec.duration).toFixed(2)}%`;
    const mark = (cls: string, at: number) => {
      const d = document.createElement("div");
      d.className = cls;
      d.style.left = pct(at);
      track.insertBefore(d, this.scrub);
      return d;
    };
    for (const s of rec.steps) mark("tick", s.t).title = `${s.n}. ${s.tool}`;
    for (const e of rec.events) {
      const label = TRACK_LABELS[e.kind];
      if (label) mark(`ev ${e.kind}`, e.t).textContent = label;
    }
  }
}
