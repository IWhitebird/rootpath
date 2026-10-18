/** The "Live" tab: the real graph in FalkorDB Cloud, polled from GET /api/live and drawn with the
 * replay renderer, with the latest agent investigation's steps and report. */
import { esc } from "../lib/html";
import { markdown } from "../lib/markdown";
import type { HealthSample, InvestigationMeta, LiveResponse, LiveSnapshot } from "../lib/types";
import type { Stage } from "./stage";

const POLL_MS = 5000;
/** Longer than the function's own 8 s FalkorDB deadline, so its explanation arrives instead of a timeout. */
const TIMEOUT_MS = 12000;

async function fetchLive(): Promise<LiveResponse> {
  let res: Response;
  try {
    res = await fetch("/api/live", { headers: { Accept: "application/json" }, signal: AbortSignal.timeout(TIMEOUT_MS) });
  } catch (e) {
    const timedOut = e instanceof DOMException && e.name === "TimeoutError";
    return { ok: false, error: timedOut ? `no answer from the live API in ${TIMEOUT_MS / 1000} s` : "the live API is unreachable" };
  }
  const body = (await res.json().catch(() => null)) as LiveResponse | null;
  if (typeof body?.ok === "boolean") return body;
  return { ok: false, error: res.ok ? "the live API sent an unreadable answer" : `the live API answered ${res.status}` };
}

/** "12 s", "5 min", "3 h" since an epoch-milliseconds moment. */
function ago(ms: number): string {
  const s = Math.max(0, (Date.now() - ms) / 1000);
  return s < 120 ? `${Math.round(s)} s` : s < 7200 ? `${Math.round(s / 60)} min` : `${Math.round(s / 3600)} h`;
}

function stamp(epochSeconds: number): string {
  const d = new Date(epochSeconds * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export class LiveView {
  private readonly stage: Stage;
  private active = false;
  /** Bumped on every start and stop, so an answer to an earlier poll is dropped. */
  private run = 0;
  private timer = 0;
  private ticker = 0;
  private snapshot: LiveSnapshot | null = null;
  private error: string | null = null;
  private polledAt = 0;
  private investigation: string | null = null;
  private reportKey: string | null = null;

  constructor(stage: Stage) {
    this.stage = stage;
  }

  start(): void {
    if (this.active) return;
    this.active = true;
    const run = ++this.run;
    this.snapshot = null;
    this.error = null;
    this.investigation = null;
    this.reportKey = null;
    const { stage } = this;
    stage.setMode(true);
    stage.graph.clear();
    stage.graph.setTrail([]);
    stage.health({}, "Reading service health from the graph.");
    stage.resetSteps();
    stage.steps([], "Reading the latest investigation.");
    stage.report("", "");
    stage.openReport(false);
    this.tick();
    this.ticker = window.setInterval(() => this.tick(), 1000);
    void this.poll(run);
  }

  stop(): void {
    if (!this.active) return;
    this.active = false;
    this.run++;
    clearTimeout(this.timer);
    clearInterval(this.ticker);
    this.stage.setMode(false);
  }

  private async poll(run: number): Promise<void> {
    // A hidden tab skips its polls instead of querying the graph for nobody.
    if (!document.hidden) {
      const res = await fetchLive();
      if (run !== this.run) return;
      this.polledAt = Date.now();
      try {
        if (res.ok) this.show(res);
        else this.fail(res.error);
      } catch {
        // A snapshot that does not match the contract must not stop the polling.
        this.fail("the live API sent an unexpected answer");
      }
    }
    this.timer = window.setTimeout(() => void this.poll(run), POLL_MS);
  }

  private show(live: LiveSnapshot): void {
    const { stage } = this;
    const { graph } = stage;
    const first = this.snapshot === null;
    this.snapshot = live;
    this.error = null;
    stage.offline(null);
    graph.status = (name) => live.services[name]?.status;
    graph.layout(live.graph.nodes, live.graph.links);
    graph.show(live.graph.nodes, live.graph.links);
    if (first) setTimeout(() => graph.fit(400), 900);

    const inv = live.investigation;
    const steps = inv?.steps ?? [];
    graph.setTrail(steps);
    const health = Object.entries(live.services).map(
      ([name, s]): [string, HealthSample] => [name, [s.status, s.error_rate ?? 0, s.p95_ms ?? 0]],
    );
    stage.health(Object.fromEntries(health), "No services in the graph yet.");
    const id = inv?.meta.id ?? null;
    if (id !== this.investigation) {
      this.investigation = id;
      stage.resetSteps();
    }
    stage.steps(steps, "No agent investigation in the graph yet.");
    this.report(inv?.meta ?? null, steps.length);
    this.tick();
  }

  private fail(error: string): void {
    const { stage } = this;
    this.snapshot = null;
    this.error = error;
    this.investigation = null;
    this.reportKey = null;
    stage.offline(error);
    stage.graph.clear();
    stage.graph.setTrail([]);
    stage.health({}, "No live health while the graph is offline.");
    stage.resetSteps();
    stage.steps([], "No live investigation while the graph is offline.");
    stage.report('<p class="empty">No live report while the graph is offline.</p>', "offline");
    this.tick();
  }

  /** Re-render the report only when the investigation moved on, so an open report keeps its scroll. */
  private report(inv: InvestigationMeta | null, steps: number): void {
    const key = inv ? `${inv.id}|${inv.finished_at ?? ""}|${steps}` : "";
    if (key === this.reportKey) return;
    this.reportKey = key;
    if (!inv) {
      this.stage.report('<p class="empty">No agent investigation in the graph yet.</p>', "no agent run yet");
      return;
    }
    const meta = [
      inv.id,
      inv.finished_at ? null : `running · ${steps} steps so far`,
      inv.duration_s != null ? `${Math.round(inv.duration_s)} s` : null,
      inv.tool_calls != null ? `${inv.tool_calls} tool calls` : null,
      inv.cost_usd ? `$${inv.cost_usd.toFixed(2)}` : null,
      inv.started_at ? `ran ${stamp(inv.started_at)}` : null,
    ];
    const body = inv.report
      ? markdown(inv.report)
      : inv.finished_at
        ? '<p class="empty">This run left no written report.</p>'
        : '<p class="empty">The agent is still working. Its report lands here when it finishes.</p>';
    this.stage.report(body, meta.filter(Boolean).join(" · "));
  }

  /** Once a second: the clock, the caption and the status line, whose ages count up between polls. */
  private tick(): void {
    const { stage, snapshot: live } = this;
    stage.clock(new Date().toLocaleTimeString("en-GB"));
    if (this.error !== null) {
      stage.caption("offline", "alert", `Retrying every ${POLL_MS / 1000} s.`);
      stage.liveStatus(["Live graph offline", `last attempt <b>${ago(this.polledAt)}</b> ago`], true);
      return;
    }
    if (!live) {
      stage.caption("live", "", "Connecting to FalkorDB Cloud.");
      stage.liveStatus(["Connecting to the live graph"], false);
      return;
    }
    const failing = Object.entries(live.services)
      .filter(([, s]) => s.status === "failing" || s.status === "down")
      .map(([name]) => esc(name));
    const synced = live.updated_at === null ? "health not synced yet" : `health synced ${ago(live.updated_at * 1000)} ago`;
    stage.caption(
      "live",
      failing.length ? "alert" : "ok",
      `Live from FalkorDB Cloud · ${synced}${failing.length ? ` · <b>${failing.join(", ")}</b> failing` : ""}`,
    );
    const open = live.incidents.filter((i) => i.status === "open").length;
    const inv = live.investigation;
    stage.liveStatus(
      [
        `graph <b>${esc(live.graph_name)}</b>: <b>${live.graph.nodes.length}</b> nodes, <b>${live.graph.links.length}</b> edges`,
        `<b>${live.incidents.length}</b> incidents in memory${open ? `, <b>${open}</b> open` : ""}`,
        inv ? `latest agent run <b>${esc(inv.meta.id)}</b>, ${inv.steps.length} steps` : "no agent run yet",
        `refreshed <b>${ago(this.polledAt)}</b> ago, every ${POLL_MS / 1000} s`,
      ],
      failing.length > 0,
    );
  }
}
