"""MCP server exposing the on-call tools to any MCP host (Claude Code, Codex, Cursor, ...).

graph mode (default): graph reasoning tools + actions + incident memory, all on FalkorDB.
baseline mode: only what a plain observability stack gives you (health, logs, a flat change
               list) plus the same actions. Used to measure what the graph is worth."""

import logging
import os

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from . import tools

READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False)
ACTION = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False)

INSTRUCTIONS = """On-call incident tools backed by a FalkorDB graph of services, dependencies, owners,
deploys, feature flags, runbooks and past incidents.
Procedure: rank_suspects on the user-facing failing service first; confirm the top suspect with
get_logs and find_similar_incidents; open_incident; take the smallest safe fix (flag off >
rollback > restart), first with dry_run=true then dry_run=false; verify_recovery on the
user-facing service; notify_owner; resolve_incident with the root cause change id."""

BASELINE_INSTRUCTIONS = """On-call incident tools: live service health, logs, the change log, and
remediation actions (rollback, restart, feature flags). Find the root cause of the alert, fix it
with the smallest safe action, and verify recovery."""


def build(mode: str = "graph") -> FastMCP:
    mcp = FastMCP(
        "oncall", instructions=INSTRUCTIONS if mode == "graph" else BASELINE_INSTRUCTIONS, log_level="WARNING"
    )

    def add(fn, annotations):
        mcp.add_tool(fn, name=fn.__name__, description=fn.__doc__, annotations=annotations)

    add(tools.get_service_health, READ)
    add(tools.get_logs, READ)
    if mode == "graph":
        for fn in (
            tools.rank_suspects,
            tools.get_blast_radius,
            tools.recent_changes,
            tools.trace_dependency_path,
            tools.get_owner,
            tools.find_similar_incidents,
            tools.explain_incident,
            tools.query_graph,
        ):
            add(fn, READ)
        for fn in (tools.open_incident, tools.notify_owner, tools.resolve_incident):
            add(fn, WRITE)
        # Mapping: how Claude Code builds the graph of a real system (/rootpath:map).
        add(tools.map_overview, READ)
        for fn in (
            tools.upsert_service,
            tools.add_dependency,
            tools.set_team,
            tools.add_runbook,
            tools.register_flag,
            tools.record_change,
        ):
            add(fn, WRITE)
    else:
        add(tools.list_changes_flat, READ)
    for fn in (tools.rollback_service, tools.restart_service, tools.set_feature_flag):
        add(fn, ACTION)
    add(tools.verify_recovery, READ)
    return mcp


def main(mode: str | None = None) -> None:
    logging.getLogger("httpx").setLevel(logging.WARNING)
    mode = mode or os.environ.get("ONCALL_MODE", "graph")
    os.environ["ONCALL_MODE"] = mode
    build(mode).run("stdio")
