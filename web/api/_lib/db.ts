/** FalkorDB access for the serverless functions: one connection per warm instance, read-only queries. */

import { isIP } from "node:net";
import { FalkorDB, type FalkorDBOptions, type Graph } from "falkordb";

const CONNECT_TIMEOUT_MS = 3000;
const QUERY_TIMEOUT_MS = 5000;
const DEADLINE_MS = 8000;
const DEFAULT_PORT = 6379;
const TLS_BY_PROTOCOL: Record<string, boolean> = { "redis:": false, "rediss:": true };
const BAD_URL = "FALKORDB_URL must look like redis://[user:password@]host:port (rediss:// for TLS), URL-encoded";

/** An error whose message is safe to show to visitors. */
export class LiveError extends Error {}

export type Params = Record<string, string | number | boolean | null>;

// node-redis honours reconnectStrategy, but falkordb's narrower SocketOptions type does not list it.
type SocketOptions = NonNullable<FalkorDBOptions["socket"]> & { reconnectStrategy: false };

let pending: Promise<FalkorDB> | undefined;

export function graphName(): string {
  return process.env.FALKORDB_GRAPH || "oncall";
}

function decode(part: string): string | undefined {
  try {
    return decodeURIComponent(part) || undefined;
  } catch {
    throw new LiveError(BAD_URL);
  }
}

/** Client options from FALKORDB_URL: redis://[user:password@]host:port, or rediss:// for TLS. */
function connectOptions(): FalkorDBOptions {
  const raw = process.env.FALKORDB_URL;
  if (!raw) throw new LiveError("FALKORDB_URL is not set");
  const url = URL.parse(raw);
  const tls = url ? TLS_BY_PROTOCOL[url.protocol] : undefined;
  if (!url || tls === undefined || !url.hostname) throw new LiveError(BAD_URL);
  const host = url.hostname.replace(/^\[(.*)\]$/, "$1"); // IPv6 literals come bracketed
  const socket: SocketOptions = {
    tls,
    // Node sends TLS SNI only when given a servername, and hosted endpoints may route on it.
    servername: tls && !isIP(host) ? host : undefined,
    connectTimeout: CONNECT_TIMEOUT_MS,
    // A dropped connection is replaced on the next request instead of being retried in the background.
    reconnectStrategy: false,
  };
  return {
    host,
    port: url.port ? Number(url.port) : DEFAULT_PORT,
    username: decode(url.username),
    password: decode(url.password),
    clientInfoTag: "rootpath-web",
    socket,
  };
}

// FalkorDB.connect forwards socket errors to the instance it is still building, which has no 'error' listener yet,
// and an unheard 'error' throws from inside the socket's event handler. A server that drops the connection during
// the handshake (redis:// against a TLS-only port) would crash the function instead of failing connect. Until our
// listener is attached those errors go unheard; connect still rejects with the cause.
const emit = FalkorDB.prototype.emit;
FalkorDB.prototype.emit = function (this: FalkorDB, event: string | symbol, ...args: unknown[]): boolean {
  return event === "error" && this.listenerCount("error") === 0 ? false : emit.call(this, event, ...args);
};

function connect(): Promise<FalkorDB> {
  if (!pending) {
    const connection: Promise<FalkorDB> = FalkorDB.connect(connectOptions()).then(
      (db) => {
        db.on("error", () => discard(connection));
        return db;
      },
      (err: unknown) => {
        discard(connection);
        throw new LiveError("cannot reach FalkorDB", { cause: err });
      },
    );
    pending = connection;
  }
  return pending;
}

function discard(connection: Promise<FalkorDB>): void {
  if (pending !== connection) return;
  pending = undefined;
  connection.then((db) => db.close()).catch(() => undefined);
}

async function withDeadline<T>(work: Promise<T>): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const expired = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new LiveError("FalkorDB did not answer in time")), DEADLINE_MS);
  });
  try {
    return await Promise.race([work, expired]);
  } finally {
    clearTimeout(timer);
  }
}

async function attempt<T>(run: (graph: Graph) => Promise<T>, retry: boolean): Promise<T> {
  const connection = connect();
  try {
    return await withDeadline(connection.then((db) => run(db.selectGraph(graphName()))));
  } catch (err) {
    if (err instanceof LiveError) {
      discard(connection); // unreachable or unresponsive: the next request starts from a fresh connection
      throw err;
    }
    if (!retry || pending === connection) throw err; // the connection is healthy, the query itself failed
    // The connection dropped under this request (typically it idled out while the instance was frozen).
    return attempt(run, false);
  }
}

/** Run read-only work against the configured graph; LiveError when FalkorDB is unset, unreachable or too slow. */
export function withGraph<T>(run: (graph: Graph) => Promise<T>): Promise<T> {
  return attempt(run, true);
}

/** GRAPH.RO_QUERY: rows keyed by column name; the server rejects any write. */
export async function read<T>(graph: Graph, cypher: string, params?: Params): Promise<T[]> {
  const reply = await graph.roQuery<T>(cypher, { params, TIMEOUT: QUERY_TIMEOUT_MS });
  return reply.data ?? [];
}
