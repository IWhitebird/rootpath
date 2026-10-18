/** Data shapes shared by the website, the operator console and the live API. */

export type Props = Record<string, unknown>;

/** A graph node as rootpath/web.py and web/api/live.ts return it. `id` is "<Label>:<key>". */
export interface GraphNode {
  id: string;
  type: string;
  name: string;
  props: Props;
}

export interface GraphLink {
  source: string;
  target: string;
  type: string;
  optional: boolean;
}

export interface GraphData {
  nodes: GraphNode[];
  links: GraphLink[];
}

/** One agent tool call and the node ids it looked at. */
export interface Step {
  n: number;
  tool: string;
  note: string;
  targets: string[];
}

/** A step read from the graph; `at` is epoch seconds. */
export interface GraphStep extends Step {
  at: number;
}

// ---------- recorded replays: public/data/replays/<key>.json (t = seconds into the recording) ----------

export interface ReplayNode extends GraphNode {
  t: number;
}

export interface ReplayLink extends GraphLink {
  t: number;
}

export interface ReplayStep extends Step {
  t: number;
}

export type ReplayEventKind = "start" | "inject" | "alert" | "agent" | "done";

export interface ReplayEvent {
  t: number;
  kind: ReplayEventKind;
  text: string;
}

export type HealthSample = [status: string, errorRate: number, p95Ms: number];

export interface HealthFrame {
  t: number;
  s: Record<string, HealthSample>;
}

export interface ReplayAgent {
  duration_s: number;
  tool_calls: number;
  cost_usd: number | null;
  input_tokens: number;
  output_tokens: number;
  report: string;
}

export interface Replay {
  scenario: string;
  description: string;
  mode: string;
  recorded_at: string;
  duration: number;
  agent: ReplayAgent;
  events: ReplayEvent[];
  steps: ReplayStep[];
  health: HealthFrame[];
  nodes: ReplayNode[];
  links: ReplayLink[];
}

// ---------- benchmark: public/data/bench.json ----------

export type BenchMode = "graph" | "baseline";

export interface BenchRow {
  scenario: string;
  mode: BenchMode;
  investigation?: string;
  fixed?: boolean;
  correct_action?: boolean;
  wrong_actions?: string[];
  clean: boolean;
  logs_read: number;
  diagnosis_calls: number;
  tool_calls?: number;
  agent_s?: number;
  time_to_resolve_s?: number;
  cost_usd?: number;
  input_tokens?: number;
  output_tokens?: number;
  error?: string;
}

export interface Bench {
  full: BenchRow[];
  basic: BenchRow[];
}

// ---------- operator console API: rootpath/web.py ----------

export interface ServiceStats {
  status: string;
  version?: string | null;
  error_rate?: number;
  p95_ms?: number;
}

export interface HealthResponse {
  services: Record<string, ServiceStats>;
  busy: string | null;
  allow_actions: boolean;
}

/** The properties of an (:Investigation) node. */
export interface InvestigationMeta {
  id: string;
  agent?: string;
  mode?: string;
  alert?: string;
  started_at?: number;
  updated_at?: number;
  finished_at?: number;
  steps?: number;
  duration_s?: number;
  tool_calls?: number;
  cost_usd?: number;
  input_tokens?: number;
  output_tokens?: number;
  report?: string;
  outcome?: string;
}

export interface InvestigationResponse {
  investigation: InvestigationMeta | null;
  steps: GraphStep[];
}

export interface Incident {
  id: string;
  title: string | null;
  status: string;
  severity: string | null;
  opened_at: number | null;
  mttr_min: number | null;
  source: string | null;
  summary: string | null;
  root_cause: string[];
  fixed_by: { kind: string; target: string; by: string }[];
}

export interface Change {
  id: string;
  at: number;
  kind: "deploy" | "flag";
  author: string;
  note?: string;
  service: string | null;
  version?: string;
  prev_version?: string;
  flag?: string;
  from?: boolean | string;
  to?: boolean | string;
}

// ---------- live API: GET /api/live (web/api/live.ts) ----------

export interface LiveService {
  status: string;
  error_rate: number;
  p95_ms: number;
  version: string | null;
}

export interface LiveSnapshot {
  ok: true;
  graph_name: string;
  /** When the ingestor last synced service health, epoch seconds. */
  updated_at: number | null;
  graph: GraphData;
  services: Record<string, LiveService>;
  investigation: { meta: InvestigationMeta; steps: GraphStep[] } | null;
  incidents: Incident[];
}

export interface LiveFailure {
  ok: false;
  error: string;
}

export type LiveResponse = LiveSnapshot | LiveFailure;
