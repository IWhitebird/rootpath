---
name: map
description: Map the user's system into the Rootpath graph (services, dependencies, owners, on-call, runbooks, feature flags, recent changes) by reading the repository and its configuration. Use when the user wants to set up Rootpath on their own system, refresh the graph, or asks "what does Rootpath know about my system".
argument-hint: "[path or area to focus on, optional]"
allowed-tools: Read Glob Grep Bash(git log:*) Bash(git tag:*) mcp__plugin_rootpath_oncall__map_overview mcp__plugin_rootpath_oncall__upsert_service mcp__plugin_rootpath_oncall__add_dependency mcp__plugin_rootpath_oncall__set_team mcp__plugin_rootpath_oncall__add_runbook mcp__plugin_rootpath_oncall__register_flag mcp__plugin_rootpath_oncall__record_change
---
Focus: $ARGUMENTS

You are mapping this system into the Rootpath graph so that later incident investigations can
reason over it. Every node and edge you write is tagged as mapped by Claude Code, so be accurate:
only write what you found evidence for, and say where.

1. **See what is already there.** Call `map_overview`. Update existing entries rather than
   creating near-duplicates (reuse the exact service names you see).
2. **Services.** Look for docker-compose / compose files, Kubernetes manifests (Deployment,
   StatefulSet, Service), Helm values, Procfiles and top-level service directories. For each
   service call `upsert_service` with its name, owning team (if known), tier (1 = user-facing or
   critical, 3 = background) and whether it is internet-facing.
3. **Dependencies.** For each service, find what it calls: environment variables such as
   `*_URL`, `*_HOST`, `CALLS`, Kubernetes service DNS names, HTTP / gRPC client base URLs and queue
   names in config or code. Call `add_dependency(caller, callee, optional, evidence)` with
   `evidence` naming the file and the line or key, for example `docker-compose.yml: checkout.environment.CALLS`.
   Mark a dependency optional only if the code tolerates its failure.
4. **Ownership and on-call.** Read CODEOWNERS, team or on-call files, and READMEs. Call `set_team`
   for each team (channel, members, who is on call, escalation team) and set `team` on its services.
5. **Runbooks.** Find runbook-like docs (runbooks/, docs/ops, RUNBOOK.md, incident notes). Call
   `add_runbook` per service with the steps in plain text.
6. **Feature flags.** Find flag definitions (LaunchDarkly / flagd / Unleash / JSON config) and the
   services that read each flag. Call `register_flag(flag, affects, team, description)`.
7. **Recent changes (optional).** From `git log` / tags, record releases of the last day with
   `record_change` (kind `deploy`, service, version, author, time). Skip this if there is no clear
   service-to-release mapping.

Finish with a short summary: what you added or updated per category, dependencies you were unsure
about (not written), and anything the user should confirm. Never write a dependency, owner or flag
you could not point to evidence for.
