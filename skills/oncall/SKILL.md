---
name: oncall
description: Investigate and resolve a production incident using the rootpath tools (dependency graph, recent changes, incident memory, safe remediation). Use when the user reports an outage, errors, latency, a failing service or an alert.
argument-hint: "<symptom, e.g. 'checkout is throwing 500s'>"
allowed-tools: mcp__plugin_rootpath_oncall__get_service_health mcp__plugin_rootpath_oncall__rank_suspects mcp__plugin_rootpath_oncall__get_logs mcp__plugin_rootpath_oncall__find_similar_incidents mcp__plugin_rootpath_oncall__get_blast_radius mcp__plugin_rootpath_oncall__recent_changes mcp__plugin_rootpath_oncall__trace_dependency_path mcp__plugin_rootpath_oncall__get_owner mcp__plugin_rootpath_oncall__explain_incident mcp__plugin_rootpath_oncall__query_graph mcp__plugin_rootpath_oncall__verify_recovery
---
The user reports: $ARGUMENTS

You are acting as the on-call engineer. Use the `oncall` MCP tools and follow this playbook.

1. **Locate.** If the user didn't name a service, call `get_service_health` and take the most
   upstream failing service (internet-facing first). Call `rank_suspects` on it. It walks the
   FalkorDB dependency graph and combines live health, recent deploys/flag flips and centrality.
   Let the graph scope the search; don't read logs of every service.
2. **Confirm.** `get_logs` on the top suspect only, and `find_similar_incidents` with the symptom
   and the suspect. A past incident with the same root cause tells you what fixed it.
3. **Show the evidence path** to the user: alerting service → ... → root cause (service + change),
   and which changes were ruled out because they are not in the dependency graph.
4. **Open the incident** (`open_incident`) with the impacted services from the path.
5. **Fix with the smallest safe action**: flag flip → `set_feature_flag` back; recent deploy →
   `rollback_service`; stuck state with no change → `restart_service`. `rank_suspects` returns the
   top suspect's runbooks and past incidents: when they say what fixes this failure mode, follow
   them over a coincidental recent change. Always call the action with `dry_run=true` first and show the plan;
   then call it with `dry_run=false`. The user approves the real action through the permission prompt.
6. **Verify** with `verify_recovery` on the service that alerted. If it didn't recover, re-rank.
7. **Close the loop**: `notify_owner` for the root-cause service, then `resolve_incident` with the
   root-cause service, change id and a short summary, so the incident is remembered.

End with: root cause, evidence path, action taken, verification, who was paged.
