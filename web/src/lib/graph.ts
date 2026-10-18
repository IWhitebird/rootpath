/** The graph renderer the website and the operator console share.
 *
 * Services are pinned in call-depth columns, the user-facing service on the left and the deepest
 * dependencies on the right, so a failure visibly travels across the canvas. Every other node is
 * pulled toward a "home" near the service it belongs to: changes and flags above it, owners below,
 * incidents in a memory band along the bottom. Nodes the agent looked at get a ring and a badge
 * with their step numbers; edges between them are drawn as the evidence path. */
import ForceGraph, { type NodeObject } from "force-graph";
import { prefersReducedMotion } from "./html";
import type { GraphLink, GraphNode, Step } from "./types";

const TONES = [
  "ok", "warn", "bad", "idle", "ink", "muted", "line", "path", "path-ink", "surface", "accent", "bg", "team", "person",
] as const;

/** A colour of the design, defined as a CSS custom property (--ok, --path, ...) on :root. */
export type Tone = (typeof TONES)[number];

const STATUS_TONE: Record<string, Tone> = { healthy: "ok", degraded: "warn", failing: "bad", down: "bad", idle: "idle" };

export const statusTone = (status: string): Tone => STATUS_TONE[status] ?? "idle";

/** Fill of every node type except Service, which is coloured by its health. */
export const TYPE_TONE: Record<string, Tone> = {
  Change: "warn", Flag: "accent", Incident: "bad", Action: "ok", Team: "team", Person: "person", Runbook: "muted",
};

const PATH_TYPES = new Set(["CALLS", "CHANGED", "TOGGLED", "AFFECTS", "ON_CALL_FOR", "ROOT_CAUSE", "REVERTS", "ON"]);
const TIGHT = new Set(["CHANGED", "TOGGLED", "AFFECTS", "OWNS", "ON_CALL_FOR", "FOR", "ON"]);
const TROUBLED = new Set(["failing", "down", "degraded"]);
const FALLBACK_RADIUS = 4;
const FULL_TURN = 2 * Math.PI;

/** Sizes, spacing and forces. The site and the console are tuned differently. */
export interface GraphStyle {
  radius: Record<string, number>;
  font: { service: number; other: number; badge: number; badgeHeight: number };
  serviceOutline: Tone;
  incidentAlpha: number;
  /** Zoom level above which every node is labelled, not only services and visited nodes. */
  labelAllFrom: number;
  callWidth: number;
  arrowLength: number;
  charge: number;
  chargeRange: number;
  callDistance: number;
  linkDistance: number;
  cooldownTicks: number;
  /** Pinned service grid: distance between call-depth columns and between services in a column. */
  column: number;
  row: number;
  /** Where each satellite type sits relative to the services it links to. */
  offsets: Record<string, [dx: number, dy: number]>;
  /** The incident band reaches `bandPad` past the outer columns, `bandDrop` below the lowest service. */
  bandPad: number;
  bandDrop: number;
  zoom: boolean;
  fitPadding: number;
  /** Fit the view only when the first layout settles, instead of after every one. */
  fitOnce: boolean;
}

export interface GraphOptions {
  style: GraphStyle;
  /** Which nodes to draw (the console's legend toggles node types). */
  visible?: (n: GraphNode) => boolean;
  /** Which links to draw between visible nodes. */
  linkVisible?: (l: GraphLink) => boolean;
  onNodeClick?: (n: SimNode) => void;
  onBackgroundClick?: () => void;
}

/** A node as the force simulation holds it: the simulation writes positions onto it. */
export type SimNode = GraphNode & NodeObject;

interface SimLink extends Omit<GraphLink, "source" | "target"> {
  source: string | SimNode;
  target: string | SimNode;
}

interface Point {
  x: number;
  y: number;
}

const idOf = (end: string | SimNode) => (typeof end === "string" ? end : end.id);
const linkKey = (l: GraphLink) => `${l.source}|${l.type}|${l.target}`;

function readPalette(): Record<Tone, string> {
  const css = getComputedStyle(document.documentElement);
  return Object.fromEntries(TONES.map((t) => [t, css.getPropertyValue(`--${t}`).trim()])) as Record<Tone, string>;
}

export class GraphView {
  /** Current status of a service by name; when it has none, the node's stored status is used. */
  status: (service: string) => string | undefined = () => undefined;

  private readonly fg: ForceGraph<SimNode, SimLink>;
  private readonly style: GraphStyle;
  private readonly visible: (n: GraphNode) => boolean;
  private readonly linkVisible: (l: GraphLink) => boolean;
  private colors = readPalette();
  private nodes = new Map<string, SimNode>();
  private links: GraphLink[] = [];
  private pins = new Map<string, Point>();
  private homes = new Map<string, Point>();
  private visited = new Map<string, number[]>();
  private latest = new Set<string>();
  private fitted = false;

  constructor(el: HTMLElement, { style, visible = () => true, linkVisible = () => true, onNodeClick, onBackgroundClick }: GraphOptions) {
    this.style = style;
    this.visible = visible;
    this.linkVisible = linkVisible;
    const s = style;
    this.fg = new ForceGraph<SimNode, SimLink>(el)
      .nodeId("id")
      .backgroundColor("rgba(0,0,0,0)")
      .linkColor((l) => (this.onPath(l) ? this.colors.path : l.type === "CALLS" ? this.colors.muted : this.colors.line))
      .linkWidth((l) => (this.onPath(l) ? 2.6 : l.type === "CALLS" ? s.callWidth : 0.6))
      .linkLineDash((l) => (l.optional ? [3, 3] : null))
      .linkDirectionalArrowLength((l) => (l.type === "CALLS" ? s.arrowLength : 0))
      .linkDirectionalArrowRelPos(1)
      .linkDirectionalParticles((l) => (l.type === "CALLS" ? 2 : 0))
      .linkDirectionalParticleWidth((l) => (this.onPath(l) ? 3 : 1.6))
      .linkDirectionalParticleColor((l) => (this.troubled(l) ? this.colors.bad : this.colors.accent))
      .linkDirectionalParticleSpeed(0.006)
      .nodeCanvasObject((n, ctx, scale) => this.draw(n, ctx, scale))
      .nodePointerAreaPaint((n, color, ctx) => {
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.arc(n.x ?? 0, n.y ?? 0, this.radius(n) + 3, 0, FULL_TURN);
        ctx.fill();
      })
      .enableZoomInteraction(s.zoom)
      .autoPauseRedraw(false)
      .cooldownTicks(s.cooldownTicks)
      .onEngineStop(() => {
        if (s.fitOnce && this.fitted) return;
        this.fit(500);
        this.fitted = true;
      });
    if (onNodeClick) {
      this.fg.onNodeClick(onNodeClick).onNodeHover((n) => {
        el.style.cursor = n ? "pointer" : "";
      });
    }
    if (onBackgroundClick) this.fg.onBackgroundClick(onBackgroundClick);
    this.fg.d3Force("charge")?.strength(s.charge).distanceMax(s.chargeRange);
    this.fg
      .d3Force("link")
      ?.distance((l: SimLink) => (l.type === "CALLS" ? s.callDistance : s.linkDistance))
      .strength((l: SimLink) => (l.type === "CALLS" ? 0 : TIGHT.has(l.type) ? 0.5 : 0.02));
    this.fg.d3Force("home", this.homeForce());

    new ResizeObserver(() => this.fg.width(el.clientWidth).height(el.clientHeight)).observe(el);
    const repaint = () => {
      this.colors = readPalette();
    };
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", repaint);
    new MutationObserver(repaint).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  }

  /** Compute service pins and satellite homes. Pass the whole graph, including nodes not shown yet,
   * so positions stay put as nodes appear. */
  layout(nodes: readonly GraphNode[], links: readonly GraphLink[]): void {
    const { column, row, offsets, bandPad, bandDrop } = this.style;
    const services = nodes.filter((n) => n.type === "Service").map((n) => n.id);
    const calls = links.filter((l) => l.type === "CALLS");
    const called = new Set(calls.map((l) => l.target));
    const depth = new Map(services.filter((id) => !called.has(id)).map((id) => [id, 0]));
    for (let i = 0; i < services.length; i++) {
      for (const l of calls) {
        const d = depth.get(l.source);
        if (d !== undefined) depth.set(l.target, Math.max(depth.get(l.target) ?? 0, d + 1));
      }
    }
    const columns = new Map<number, string[]>();
    for (const id of services) {
      const d = depth.get(id) ?? 0;
      columns.set(d, [...(columns.get(d) ?? []), id]);
    }
    const deepest = Math.max(0, ...columns.keys());
    const pins = new Map<string, Point>();
    for (const [d, ids] of columns) {
      ids.sort();
      ids.forEach((id, i) => pins.set(id, { x: (d - deepest / 2) * column, y: (i - (ids.length - 1) / 2) * row }));
    }

    const homes = new Map<string, Point>();
    const typeOf = new Map(nodes.map((n) => [n.id, n.type]));
    const neighbours = new Map<string, string[]>();
    const connect = (a: string, b: string) => neighbours.set(a, [...(neighbours.get(a) ?? []), b]);
    for (const l of links) {
      connect(l.source, l.target);
      connect(l.target, l.source);
    }
    const centre = (ids: string[]): Point | null => {
      const ps = ids.map((id) => pins.get(id) ?? homes.get(id)).filter((p) => p !== undefined);
      if (!ps.length) return null;
      return { x: ps.reduce((a, p) => a + p.x, 0) / ps.length, y: ps.reduce((a, p) => a + p.y, 0) / ps.length };
    };
    // Three passes, so a node attached only to other satellites (a person on call for a team)
    // finds its home after theirs.
    for (let pass = 0; pass < 3; pass++) {
      for (const n of nodes) {
        if (pins.has(n.id) || n.type === "Incident") continue;
        const around = neighbours.get(n.id) ?? [];
        const owners = around.filter((id) => pins.has(id));
        const base = centre(owners.length ? owners : around.filter((id) => homes.has(id) && typeOf.get(id) !== "Incident"));
        if (!base) continue;
        const [dx, dy] = offsets[n.type] ?? [0, 50];
        homes.set(n.id, { x: base.x + dx, y: base.y + dy });
      }
    }
    if (pins.size) {
      const xs = [...pins.values()].map((p) => p.x);
      const bandY = Math.max(...[...pins.values()].map((p) => p.y)) + bandDrop;
      const minX = Math.min(...xs) - bandPad;
      const maxX = Math.max(...xs) + bandPad;
      const incidents = nodes.filter((n) => n.type === "Incident").sort((a, b) => a.id.localeCompare(b.id));
      incidents.forEach((n, i) => {
        const at = incidents.length > 1 ? i / (incidents.length - 1) : 0.5;
        homes.set(n.id, { x: minX + (maxX - minX) * at, y: bandY });
      });
    }
    this.pins = pins;
    this.homes = homes;
  }

  /** Show exactly these nodes and links. Known nodes keep their positions; new ones start at their
   * pin or home. The simulation is only restarted when the set of nodes or links changed. */
  show(nodes: readonly GraphNode[], links: readonly GraphLink[]): void {
    let changed = nodes.length !== this.nodes.size;
    const next = new Map<string, SimNode>();
    for (const n of nodes) {
      const known = this.nodes.get(n.id);
      if (known) {
        known.name = n.name;
        known.props = n.props;
        next.set(n.id, known);
      } else {
        next.set(n.id, this.place(n));
        changed = true;
      }
    }
    for (const n of next.values()) {
      const pin = this.pins.get(n.id);
      if (pin) {
        n.fx = pin.x;
        n.fy = pin.y;
      }
    }
    this.nodes = next;
    const before = new Set(this.links.map(linkKey));
    if (links.length !== this.links.length || links.some((l) => !before.has(linkKey(l)))) {
      this.links = [...links];
      changed = true;
    }
    if (changed) this.render();
  }

  /** Drop everything, so the next show() places every node afresh. */
  clear(): void {
    this.nodes = new Map();
    this.links = [];
    this.render();
  }

  /** Hand the current nodes, through the visibility filter, to the simulation. */
  render(): void {
    const nodes = [...this.nodes.values()].filter((n) => this.visible(n));
    const ids = new Set(nodes.map((n) => n.id));
    // force-graph swaps link ends for node objects, so it gets copies of the id-based links.
    const links = this.links
      .filter((l) => ids.has(l.source) && ids.has(l.target) && this.linkVisible(l))
      .map((l): SimLink => ({ ...l }));
    this.fg.graphData({ nodes, links });
  }

  /** Mark what the agent looked at: a ring and step badge on each visited node, a pulse on `focus`. */
  setTrail(steps: readonly Step[], focus: readonly string[] = steps.at(-1)?.targets ?? []): void {
    this.visited = new Map();
    for (const s of steps) {
      for (const id of s.targets) {
        const seen = this.visited.get(id);
        if (seen) seen.push(s.n);
        else this.visited.set(id, [s.n]);
      }
    }
    this.latest = new Set(focus);
  }

  /** The step numbers that looked at a node. */
  stepsAt(id: string): number[] | undefined {
    return this.visited.get(id);
  }

  node(id: string): SimNode | undefined {
    return this.nodes.get(id);
  }

  fit(ms: number): void {
    this.fg.zoomToFit(ms, this.style.fitPadding);
  }

  center(n: SimNode, ms: number): void {
    this.fg.centerAt(n.x, n.y, ms);
  }

  private place(n: GraphNode): SimNode {
    const home = this.homes.get(n.id);
    if (!home || this.pins.has(n.id)) return { ...n };
    return { ...n, x: home.x + (Math.random() - 0.5) * 10, y: home.y + (Math.random() - 0.5) * 10 };
  }

  private homeForce() {
    let nodes: SimNode[] = [];
    const force = (alpha: number) => {
      for (const n of nodes) {
        const home = this.homes.get(n.id);
        if (!home || n.fx !== undefined) continue;
        n.vx = (n.vx ?? 0) + (home.x - (n.x ?? 0)) * 0.12 * alpha;
        n.vy = (n.vy ?? 0) + (home.y - (n.y ?? 0)) * 0.12 * alpha;
      }
    };
    return Object.assign(force, {
      initialize: (all: SimNode[]) => {
        nodes = all;
      },
    });
  }

  private radius(n: GraphNode): number {
    return this.style.radius[n.type] ?? FALLBACK_RADIUS;
  }

  private statusOf(n: GraphNode): string {
    const stored = n.props.status;
    return this.status(n.name) || (typeof stored === "string" ? stored : "idle");
  }

  private onPath(l: SimLink): boolean {
    return PATH_TYPES.has(l.type) && this.visited.has(idOf(l.source)) && this.visited.has(idOf(l.target));
  }

  private troubled(l: SimLink): boolean {
    const target = this.nodes.get(idOf(l.target));
    return target?.type === "Service" && TROUBLED.has(this.statusOf(target));
  }

  private draw(n: SimNode, ctx: CanvasRenderingContext2D, scale: number): void {
    const { colors: C, style: s } = this;
    const x = n.x ?? 0;
    const y = n.y ?? 0;
    const r = this.radius(n);
    const service = n.type === "Service";
    const steps = this.visited.get(n.id);
    if (steps) {
      const pulse = this.latest.has(n.id) && !prefersReducedMotion ? 2.5 + 2 * Math.sin(performance.now() / 170) : 2;
      ctx.beginPath();
      ctx.arc(x, y, r + 3 + pulse, 0, FULL_TURN);
      ctx.strokeStyle = C.path;
      ctx.lineWidth = 2.2 / Math.max(scale, 0.7);
      ctx.stroke();
    }

    ctx.beginPath();
    if (n.type === "Change" || n.type === "Flag") {
      const k = r + 1.4;
      ctx.moveTo(x, y - k);
      ctx.lineTo(x + k, y);
      ctx.lineTo(x, y + k);
      ctx.lineTo(x - k, y);
      ctx.closePath();
    } else if (n.type === "Team" || n.type === "Runbook") ctx.rect(x - r, y - r, 2 * r, 2 * r);
    else ctx.arc(x, y, r, 0, FULL_TURN);
    ctx.globalAlpha = n.type === "Incident" ? s.incidentAlpha : 1;
    ctx.fillStyle = service ? C[statusTone(this.statusOf(n))] : C[TYPE_TONE[n.type] ?? "muted"];
    ctx.fill();
    ctx.globalAlpha = 1;
    if (service) {
      ctx.lineWidth = 1.5 / Math.max(scale, 0.7);
      ctx.strokeStyle = C[s.serviceOutline];
      ctx.stroke();
    }

    if (service || steps || scale > s.labelAllFrom) {
      const size = (service ? s.font.service : s.font.other) / Math.max(scale, 0.85);
      ctx.font = `${service ? 600 : 500} ${size}px "DM Sans", system-ui, sans-serif`;
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      ctx.lineWidth = 3 / Math.max(scale, 0.85);
      ctx.strokeStyle = C.bg;
      ctx.strokeText(n.name, x, y + r + 3);
      ctx.fillStyle = service ? C.ink : C.muted;
      ctx.fillText(n.name, x, y + r + 3);
    }

    if (steps) {
      const label = steps.length > 3 ? `${steps[0]}…${steps[steps.length - 1]}` : steps.join(",");
      ctx.font = `700 ${s.font.badge / Math.max(scale, 0.85)}px "IBM Plex Mono", monospace`;
      const w = ctx.measureText(label).width + 6 / scale;
      const h = s.font.badgeHeight / scale;
      ctx.fillStyle = C.path;
      ctx.fillRect(x + r, y - r - h, w, h);
      ctx.fillStyle = C["path-ink"];
      ctx.textAlign = "left";
      ctx.textBaseline = "middle";
      ctx.fillText(label, x + r + 3 / scale, y - r - h / 2);
    }
  }
}
