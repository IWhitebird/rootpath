---
name: blast-radius
description: Before changing or deploying a service, show what depends on it, who to notify, what changed around it recently and what broke last time. Use when the user is about to ship, change, restart or deprecate a service.
argument-hint: "<service>"
allowed-tools: mcp__plugin_rootpath_oncall__get_blast_radius mcp__plugin_rootpath_oncall__get_owner mcp__plugin_rootpath_oncall__recent_changes mcp__plugin_rootpath_oncall__find_similar_incidents mcp__plugin_rootpath_oncall__get_service_health
---
Service: $ARGUMENTS

Pre-change risk check using the oncall graph:
1. `get_blast_radius` for the service: list upstream callers (who breaks if it breaks, user-facing or not)
   and downstream dependencies.
2. `get_owner` for the service and for each tier-1 upstream caller: teams and on-call people to notify.
3. `recent_changes` for the service (last 24h = 1440 minutes): other changes already in flight nearby.
4. `find_similar_incidents` with the service name: what went wrong the last times it changed and how it was fixed.
5. `get_service_health` for the service and its callers right now.

Answer with a short risk summary: blast radius, people to notify, in-flight changes, past failure
modes, and a go / wait recommendation with the reason.
