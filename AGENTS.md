# AGENTS.md: on-call playbook for Codex and other MCP agents

This repo ships an MCP server (`uv run oncall-graph mcp`) with on-call tools backed by a FalkorDB
graph of services, dependencies, owners, deploys, feature flags, runbooks and past incidents.

When the user reports an outage, errors, latency or an alert:

1. `get_service_health`, then `rank_suspects` on the most upstream failing (internet-facing) service.
   Trust the graph to scope the search; don't read every service's logs.
2. `get_logs` on the top suspect only; `find_similar_incidents` with the symptom and suspect.
3. Show the evidence path: alerting service → ... → root cause (service + change), plus ruled-out changes.
4. `open_incident`.
5. Smallest safe fix: flag flip → `set_feature_flag` back; recent deploy → `rollback_service`;
   stuck state without a change → `restart_service`. Follow the top suspect's runbook and past
   incidents (returned by `rank_suspects`) over a coincidental recent change.
   Always `dry_run=true` first, then `dry_run=false`.
6. `verify_recovery` on the alerting service; re-rank if it didn't recover. At most 3 actions.
7. `notify_owner` (root-cause service), then `resolve_incident` with the root-cause change id.

Before a risky change, use `get_blast_radius`, `get_owner` and `recent_changes` for a pre-change check.
For postmortems, use `explain_incident`.

Development: `uv run oncall-graph demo up | reset | down`, `uv run oncall-graph chaos <scenario>`,
`uv run oncall-graph call <tool> key=value ...` to call any tool directly.
