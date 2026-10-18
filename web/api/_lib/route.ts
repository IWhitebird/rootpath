import type { VercelApiHandler } from "@vercel/node";
import { LiveError } from "./db.js";

const CACHE = "s-maxage=3, stale-while-revalidate=15";

/** A GET endpoint answering 200 with the built body, or 503 {ok: false, error} when the graph is unavailable. */
export function jsonRoute(build: () => Promise<object>): VercelApiHandler {
  return async (_req, res) => {
    try {
      const body = await build();
      res.setHeader("Cache-Control", CACHE);
      res.status(200).json(body);
    } catch (err) {
      console.error(err);
      res.setHeader("Cache-Control", "no-store");
      res.status(503).json({ ok: false, error: err instanceof LiveError ? err.message : "graph query failed" });
    }
  };
}
