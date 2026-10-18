/** GET /api/status: whether FalkorDB is reachable, with the graph's size. */

import { graphName, withGraph } from "./_lib/db.js";
import { counts } from "./_lib/queries.js";
import { jsonRoute } from "./_lib/route.js";

export default jsonRoute(() =>
  withGraph(async (graph) => ({ ok: true, graph_name: graphName(), ...(await counts(graph)) })),
);
