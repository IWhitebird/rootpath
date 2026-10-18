# AGENTS.md: on-call playbook for Codex and other MCP agents

This repo ships an MCP server (`uv run rootpath mcp`) with on-call tools backed by a FalkorDB
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

To map a new system into the graph (what `/rootpath:map` does in Claude Code): call `map_overview`
first, then read compose files, Kubernetes manifests, CODEOWNERS, runbooks, flag definitions and git
history, and write through `upsert_service`, `add_dependency` (always with `evidence`: file and key),
`set_team`, `add_runbook`, `register_flag` and `record_change`. Never write an edge you can't point to.

Before a risky change, use `get_blast_radius`, `get_owner` and `recent_changes` for a pre-change check.
For postmortems, use `explain_incident`.

Development: `uv run rootpath demo up | reset | down`, `uv run rootpath chaos <scenario>`,
`uv run rootpath call <tool> key=value ...` to call any tool directly.
