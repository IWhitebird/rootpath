---
name: postmortem
description: Write a blameless postmortem for an incident from the evidence stored in the oncall graph (timeline, root cause, actions, agent steps). Use when the user asks for a postmortem, incident report or RCA.
argument-hint: "<incident id, e.g. INC-043>"
allowed-tools: mcp__plugin_rootpath_oncall__explain_incident mcp__plugin_rootpath_oncall__find_similar_incidents mcp__plugin_rootpath_oncall__get_blast_radius mcp__plugin_rootpath_oncall__get_owner
---
Incident: $ARGUMENTS

1. `explain_incident` for the incident: impacted services, root cause change, actions with their
   reasons, and the investigation steps.
2. `find_similar_incidents` with the incident's symptom to find repeats of the same failure mode.
3. Write a blameless postmortem in Markdown: summary, impact (services + duration), timeline
   (from the investigation steps), root cause with the evidence path, what fixed it, what went well,
   and action items. If this failure mode happened before, say so and make preventing the repeat
   the top action item.
