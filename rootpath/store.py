"""Thin FalkorDB access layer: queries return plain dicts, never client objects."""

import contextlib
import re
from typing import Any

from falkordb import FalkorDB
from falkordb.edge import Edge
from falkordb.node import Node
from falkordb.path import Path

from . import config

_graph = None


def graph():
    global _graph
    if _graph is None:
        db = FalkorDB(host=config.FALKORDB_HOST, port=config.FALKORDB_PORT, password=config.FALKORDB_PASSWORD)
        _graph = db.select_graph(config.GRAPH)
    return _graph


def _plain(value: Any) -> Any:
    if isinstance(value, Node):
        return {"_id": value.id, "_labels": list(value.labels), **value.properties}
    if isinstance(value, Edge):
        return {"_type": value.relation, **value.properties}
    if isinstance(value, Path):
        return {"nodes": [_plain(n) for n in value.nodes()], "edges": [_plain(e) for e in value.edges()]}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    return value


def _rows(result) -> list[dict]:
    names = [h[1] for h in result.header]
    return [dict(zip(names, (_plain(v) for v in row), strict=True)) for row in result.result_set]


def query(cypher: str, params: dict | None = None) -> list[dict]:
    """Read/write query, used only by deterministic code (ingest, action recording)."""
    return _rows(graph().query(cypher, params))


def read(cypher: str, params: dict | None = None, timeout_ms: int | None = None) -> list[dict]:
    """Read-only query (GRAPH.RO_QUERY) with a timeout. Writes are rejected by the server."""
    return _rows(graph().ro_query(cypher, params, timeout=timeout_ms or config.QUERY_TIMEOUT_MS))


_LIMIT = re.compile(r"\blimit\s+\d+\s*$", re.IGNORECASE)


def safe_read(cypher: str, max_rows: int = 100) -> list[dict]:
    """For agent-written Cypher: read-only, time-boxed, row-capped."""
    cypher = cypher.strip().rstrip(";")
    if not _LIMIT.search(cypher):
        cypher = f"{cypher} LIMIT {max_rows}"
    return read(cypher)[:max_rows]


def delete_graph() -> None:
    with contextlib.suppress(Exception):  # graph may not exist yet
        graph().delete()
