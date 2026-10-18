/** The console API reads of rootpath/web.py (graph, investigation, incidents), ported for the live view. */

import type { Graph } from "falkordb";
import { read } from "./db.js";

type Value = string | number | boolean | null | Value[] | { [key: string]: Value };
export type Props = Record<string, Value>;

/** A node as the falkordb client returns it. */
interface Entity {
  labels: string[];
  properties: Props;
}

export interface LiveNode {
  id: string;
  type: string;
  name: string;
  props: Props;
}

export interface LiveLink {
  source: string;
  target: string;
  type: string;
  optional: boolean;
}

export interface ServiceHealth {
  status: string;
  error_rate: number;
  p95_ms: number;
  version: string | null;
}

export interface Step {
  n: number;
  tool: string;
  note: string;
  at: number;
  targets: string[];
}

export interface Investigation {
  meta: Props;
  steps: Step[];
}

export interface Fix {
  kind: string;
  target: string | null;
  by: string | null;
}

export interface Incident {
  id: string;
  title: string;
  status: string;
  severity: string;
  opened_at: number;
  mttr_min: number | null;
  source: string;
  summary: string | null;
  root_cause: string[];
  fixed_by: Fix[];
}

const KEY: Record<string, string> = { Service: "name", Team: "name", Person: "handle", Flag: "name" };
const HIDDEN = ["body", "summary", "symptom"];
const WINDOW_S = 3 * 3600;

const NODES = `
  MATCH (n) WHERE (n:Service OR n:Team OR n:Flag OR n:Runbook)
     OR (n:Change AND (n.at >= $since OR (n)<-[:ROOT_CAUSE]-(:Incident)))
     OR (n:Incident) OR (n:Action AND n.dry_run = false)
     OR (n:Person AND (n)-[:ON_CALL_FOR]->())
  RETURN n`;

const LINKS = `
  MATCH (a)-[r]->(b) WHERE type(r) IN ['CALLS','OWNS','ON_CALL_FOR','FOR','AFFECTS','CHANGED','TOGGLED',
                                       'IMPACTED','ROOT_CAUSE','RESOLVED_BY','ON','REVERTS','ESCALATES_TO']
  RETURN a, type(r) AS type, b, r.optional AS optional`;

const LATEST_INVESTIGATION = `
  MATCH (v:Investigation) WHERE v.steps > 0 AND NOT v.id STARTS WITH 'cli-'
  RETURN v ORDER BY v.started_at DESC LIMIT 1`;

const STEPS = `
  MATCH (v:Investigation {id: $id})-[s:STEP]->(x)
  RETURN s.n AS n, s.tool AS tool, s.note AS note, s.at AS at, collect(x) AS targets ORDER BY n`;

const INCIDENTS = `
  MATCH (i:Incident)
  OPTIONAL MATCH (i)-[:ROOT_CAUSE]->(rc)
  OPTIONAL MATCH (i)-[:RESOLVED_BY]->(a:Action)
  RETURN i.id AS id, i.title AS title, i.status AS status, i.severity AS severity, i.opened_at AS opened_at,
         i.mttr_min AS mttr_min, i.source AS source, i.summary AS summary,
         collect(DISTINCT coalesce(rc.name, rc.version, rc.flag)) AS root_cause,
         collect(DISTINCT {kind: a.kind, target: a.target, by: a.by}) AS fixed_by
  ORDER BY opened_at DESC`;

/** str() of a value as Python prints it, so ids and labels match the Python console ("fraud_model_v2 → True"). */
function py(value: Value | undefined): string {
  if (value === null || value === undefined) return "None";
  if (typeof value === "boolean") return value ? "True" : "False";
  return String(value);
}

function visible(props: Props, hidden: readonly string[] = []): Props {
  return Object.fromEntries(Object.entries(props).filter(([key]) => !key.startsWith("_") && !hidden.includes(key)));
}

function labelOf({ labels }: Entity): string {
  return labels.includes("Change") ? "Change" : labels[0];
}

/** "<Label>:<key>", as rootpath/web.py _nid builds it. */
function nodeId(node: Entity): string {
  const label = labelOf(node);
  return `${label}:${py(node.properties[KEY[label] ?? "id"])}`;
}

function nodeName(label: string, p: Props): string {
  return py(
    p.name ||
      p.handle ||
      (p.kind === "deploy" ? `${py(p.service)} v${py(p.version)}` : null) ||
      (p.kind === "flag" ? `${py(p.flag)} → ${py(p.to)}` : null) ||
      (label === "Action" ? `${py(p.kind)} ${py(p.target)}` : null) ||
      p.id,
  );
}

/** Recent changes plus root causes, the services around them, and the links among them. */
export async function graphView(graph: Graph): Promise<{ nodes: LiveNode[]; links: LiveLink[] }> {
  const [nodeRows, linkRows] = await Promise.all([
    read<{ n: Entity }>(graph, NODES, { since: Date.now() / 1000 - WINDOW_S }),
    read<{ a: Entity; type: string; b: Entity; optional: Value }>(graph, LINKS),
  ]);
  const nodes = new Map<string, LiveNode>();
  for (const { n } of nodeRows) {
    const id = nodeId(n);
    const type = labelOf(n);
    nodes.set(id, { id, type, name: nodeName(type, n.properties), props: visible(n.properties, HIDDEN) });
  }
  const links = linkRows.flatMap(({ a, type, b, optional }) => {
    const source = nodeId(a);
    const target = nodeId(b);
    return nodes.has(source) && nodes.has(target) ? [{ source, target, type, optional: Boolean(optional) }] : [];
  });
  return { nodes: [...nodes.values()], links };
}

/** Live health the ingestor overlays on Service nodes, by service name. */
export function serviceHealth(nodes: LiveNode[]): Record<string, ServiceHealth> {
  const services = nodes.filter((node) => node.type === "Service");
  return Object.fromEntries(
    services.map(({ name, props }) => [
      name,
      {
        status: String(props.status ?? "unknown"),
        error_rate: Number(props.error_rate ?? 0),
        p95_ms: Number(props.p95_ms ?? 0),
        version: props.version == null ? null : String(props.version),
      },
    ]),
  );
}

/** When the ingestor last synced health (max Service.health_at), in epoch seconds. */
export function lastHealthSync(nodes: LiveNode[]): number | null {
  const stamps = nodes.flatMap(({ type, props }) =>
    type === "Service" && typeof props.health_at === "number" ? [props.health_at] : [],
  );
  return stamps.length ? Math.max(...stamps) : null;
}

async function investigationSteps(graph: Graph, id: string): Promise<Step[]> {
  const rows = await read<Omit<Step, "targets"> & { targets: Entity[] }>(graph, STEPS, { id });
  return rows.map(({ targets, ...step }) => ({
    ...step,
    targets: targets.filter((target) => !target.labels.includes("Investigation")).map(nodeId),
  }));
}

/** The most recent agent run with at least one step (CLI runs excluded). */
export async function latestInvestigation(graph: Graph): Promise<Investigation | null> {
  const [row] = await read<{ v: Entity }>(graph, LATEST_INVESTIGATION);
  if (!row) return null;
  const meta = visible(row.v.properties);
  return { meta, steps: await investigationSteps(graph, String(meta.id)) };
}

export async function incidents(graph: Graph): Promise<Incident[]> {
  type Row = Omit<Incident, "root_cause" | "fixed_by"> & {
    root_cause: (string | null)[];
    fixed_by: (Omit<Fix, "kind"> & { kind: string | null })[];
  };
  const rows = await read<Row>(graph, INCIDENTS);
  return rows.map((row) => ({
    ...row,
    root_cause: row.root_cause.filter((cause): cause is string => Boolean(cause)),
    fixed_by: row.fixed_by.filter((fix): fix is Fix => Boolean(fix.kind)),
  }));
}

export async function counts(graph: Graph): Promise<{ nodes: number; edges: number }> {
  const [[nodes], [edges]] = await Promise.all([
    read<{ nodes: number }>(graph, "MATCH (n) RETURN count(n) AS nodes"),
    read<{ edges: number }>(graph, "MATCH ()-[r]->() RETURN count(r) AS edges"),
  ]);
  return { nodes: nodes.nodes, edges: edges.edges };
}
