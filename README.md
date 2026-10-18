# Rootpath

**The alert is never where the bug is.**

Rootpath is an incident agent that reasons over a FalkorDB graph of your services, deploys, feature
flags, owners and past incidents. It follows a failure from the alert to where it starts, fixes it,
checks the fix, and remembers. It runs inside the agent you already use: Claude Code, Codex, Cursor
or any MCP host.

[Website](https://rootpath.iwhitebird.com) · [Demo video](__VIDEO_URL__)

![A recorded incident: the agent follows the graph from the alerting frontend three hops down to a feature flag, turns it off, verifies and remembers](docs/replay.gif)

## Quickstart

```bash
curl -fsSL https://rootpath.iwhitebird.com/install.sh | sh
rootpath init
```

`rootpath init` asks where your FalkorDB is (local Docker, FalkorDB Cloud or any URL), tests the
connection and connects your agents. Then, in Claude Code, inside your repository:

```
/rootpath:map
/rootpath:oncall checkout is throwing 500s
```

Or try the demo: seven services in Docker that you can break.

```bash
rootpath demo up
rootpath ui                # console at http://localhost:7100
rootpath chaos flag        # or bad-deploy, pool, deploy-noise, flag-collision, pool-after-deploy
rootpath watch --once      # the alert fires and a headless agent fixes it
```

## How it works

```mermaid
flowchart LR
  Team -- OWNS --> Service
  Service -- CALLS --> Service
  Person -- ON_CALL_FOR --> Team
  Runbook -- FOR --> Service
  Flag -- AFFECTS --> Service
  Deploy -- CHANGED --> Service
  FlagChange -- TOGGLED --> Flag
  Incident -- IMPACTED --> Service
  Incident -- ROOT_CAUSE --> Deploy
  Incident -- RESOLVED_BY --> Action
  Action -- BECAUSE --> Evidence
  Investigation -. STEP .-> Service
```

| The agent decides | From the graph | Tool |
|---|---|---|
| where the failure starts | `(alerting)-[:CALLS*0..6]->(d)`: the deepest unhealthy service whose dependencies are healthy | `rank_suspects` |
| which change matters | deploys via `CHANGED`, flag flips via `TOGGLED → Flag → AFFECTS`; anything off the failing path is ruled out | `rank_suspects` |
| what fixed it before | full-text index on incidents, then `ROOT_CAUSE` and `RESOLVED_BY` | `find_similar_incidents` |
| what a fix would break | upstream `CALLS` traversal | `get_blast_radius` |
| whether it may act | a matching dry-run `Action` from the last 15 minutes | safety hook |
| who to page | `OWNS`, `ON_CALL_FOR`, `ESCALATES_TO` | `notify_owner` |

Afterwards it writes the incident back with a `ROOT_CAUSE` edge to the exact change, and every tool
call as a `STEP` edge. The next agent, in a new session, finds it.

The graph is built three ways: `/rootpath:map` (Claude Code reads your compose files, manifests,
CODEOWNERS, runbooks and flags, and writes through validated tools that require evidence for every
dependency), `rootpath ingest --watch` (health, deploys and flag changes), and the agent itself.

```mermaid
flowchart LR
  A["Claude Code · Codex · Cursor<br/>or the headless watcher"] -- MCP --> M["Rootpath MCP server<br/>(local process)"]
  M -- read-only Cypher --> DB[("Your FalkorDB<br/>Docker or FalkorDB Cloud")]
  M -- rollback · restart · flag --> S["Your system"]
  S -- rootpath ingest --> DB
```

![The Rootpath console mid-incident](docs/screenshot.png)

## Benchmark

The same agent (Claude Opus 5.5, headless) and the same remediation tools on six injected failures,
with the graph and without it. Baseline gets only health, logs and a flat list of changes. Every
scenario ran twice per mode, with and without tracing: 48 runs.

| | graph | baseline |
|---|---|---|
| clean fixes | **24/24** | 23/24 |
| service logs read before the fix | **24** | 63 |
| agent time | **1051 s** | 1362 s |
| cost | $3.30 | $3.35 |

With tracing, both agents find the cause; the graph gets there reading fewer logs. The one baseline
miss rolled back a healthy fraud-check release before turning the flag off. Two runs per cell is a
small sample. Every run is in [`bench/`](bench/).

## Safety

- Agent queries are read-only: `GRAPH.RO_QUERY` with a timeout and a row cap.
- Actions are dry runs by default. A Claude Code hook refuses a real action without a recent dry run,
  or during a change freeze, and fails closed.
- `rootpath watch --autonomy propose` makes the server refuse real actions.
- The headless agent gets only the Rootpath tools: no shell, file or web access.

## Agents

- **Claude Code:** `rootpath init` installs the plugin, or run `/plugin marketplace add IWhitebird/rootpath`
  and `/plugin install rootpath@rootpath`. Skills: `/rootpath:map`, `/rootpath:oncall`,
  `/rootpath:blast-radius`, `/rootpath:postmortem`.
- **Codex, Cursor, any MCP host:** `rootpath mcp` over stdio, or `http://localhost:7100/mcp` while
  `rootpath start` is running. The playbook for Codex is in [`AGENTS.md`](AGENTS.md).

## CLI

| Command | |
|---|---|
| `rootpath init` | connect a FalkorDB and your agents |
| `rootpath start`, `stop`, `status` | background server: console, ingest and MCP over HTTP |
| `rootpath mcp` | MCP server on stdio |
| `rootpath demo up`, `down`, `reset` | the demo stack |
| `rootpath chaos <scenario>` | break something |
| `rootpath watch` | alert watcher that starts the headless agent |
| `rootpath bench` | the benchmark above |

## Development

```bash
uv sync
uv run pytest tests/test_unit.py           # no Docker needed
uv run pytest tests/test_integration.py    # against the demo stack
uv run ruff check . && uv run ruff format --check .
cd web && npm ci && npm run build          # website, console and the read-only API
```

## AI assistance

Built with Claude Code as a pair programmer: it wrote much of the code under my direction. The
design, decisions, review and testing are mine.

## License

Apache-2.0. FalkorDB is licensed separately.
