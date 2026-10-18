/** GET /api/live: the read-only live view of the Rootpath graph for the website's Live tab. */

import { graphName, withGraph } from "./_lib/db.js";
import { graphView, incidents, lastHealthSync, latestInvestigation, serviceHealth } from "./_lib/queries.js";
import { jsonRoute } from "./_lib/route.js";

export default jsonRoute(() =>
  withGraph(async (graph) => {
    const [view, investigation, incidentList] = await Promise.all([
      graphView(graph),
      latestInvestigation(graph),
      incidents(graph),
    ]);
    return {
      ok: true,
      graph_name: graphName(),
      updated_at: lastHealthSync(view.nodes),
      graph: view,
      services: serviceHealth(view.nodes),
      investigation,
      incidents: incidentList,
    };
  }),
);
