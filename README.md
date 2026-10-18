# Rootpath

**The alert is never where the bug is.** Rootpath is an incident agent that reasons over a
FalkorDB graph of your services, deploys, feature flags, owners and past incidents. It follows the
failure from the alert to where it starts, fixes it, checks the fix, and remembers. It plugs into the
coding agent you already use (Claude Code, Codex, Cursor) over MCP, or runs on its own when an alert fires.

> Graph Hacks: Context for AI Agents (WeMakeDevs × FalkorDB, Oct 2026)
> **Tracks:** 01 Agents That Act on Connected Data (primary) · 02 Agent Memory and Coordination
>
> **Website with live replays:** https://iwhitebird.github.io/rootpath/ · **Demo video:** _link_

![A recorded incident: the agent follows the graph from the alerting frontend three hops down to a feature flag, turns it off, verifies and remembers](docs/replay.gif)

*A real recorded run, replayed by the project website: fraud-check gets slow after a flag flip,
frontend alerts, and the agent follows the graph three hops down. It rules out a coincidental
deploy, recalls the matching past incident, turns the flag off, verifies recovery, pages the owner
and stores the incident. The website is [`site/index.html`](site/index.html): self-contained, it runs
from disk and is published to GitHub Pages by `.github/workflows/pages.yml`.
[`docs/replay.mp4`](docs/replay.mp4) is the same clip as video.*

![The operator console mid-incident](docs/screenshot.png)

## The problem

When production breaks, the first 20–40 minutes of an incident go to working out **what changed,
what's affected and who owns it**. That means jumping between dashboards, logs, deploy history and
Slack. AI agents mostly don't fix this. They either grep every log (slow, expensive, easy to fool
with a coincidental deploy) or only *diagnose* without acting, verifying or learning.

The answer depends on **how things connect**:

- the service that alerts is rarely the one that's broken;
- a deploy only matters if it sits on the failing path;
- a feature flag affects services it is never deployed to;
- the right fix was usually already found the last time.

That is a graph problem.

## What it does

```
ALERT frontend 50% errors
  ● rank_suspects(frontend)      graph: frontend → checkout → payment → fraud-check
                                 fraud-check: slow, own deps healthy, flag fraud_model_v2 flipped 88s ago
                                 ruled out: notify v1.2.1 deploy (not on the failing path)
  ● get_logs(fraud-check)        latency 15ms → 1.9s right after the flip
  ● find_similar_incidents       INC-027: same flag, same symptom, fixed by turning it off
  ● open_incident                INC-043
  ● set_feature_flag(dry_run)    plan: fraud_model_v2 true → false, affects fraud-check
  ● set_feature_flag             executed
  ● verify_recovery(frontend)    0% errors after 7s ✓
  ● notify_owner(fraud-check)    paged ananya (#team-risk)
  ● resolve_incident             root cause → change chg-847f70af, stored in memory
done in 45s · 9 tool calls
```

1. **Locate:** multi-hop traversal from the alerting service through `CALLS` edges, combined with
   live health, recent `Deploy`/`FlagChange` nodes and `algo.betweenness`. The deepest unhealthy
   node on the path with a fresh change wins. Changes outside the dependency graph are explicitly
   ruled out.
2. **Confirm:** logs of the top suspect only, plus **incident memory**: a full-text and graph-overlap
   search over past `Incident` nodes, their `ROOT_CAUSE` and what `RESOLVED_BY` them.
3. **Act:** roll back, restart or flip a flag. Every action is dry-run first and recorded as
   `(:Action)-[:BECAUSE]->(:Evidence)`, linked to the change it reverts.
4. **Verify:** poll the user-facing service until it's healthy twice in a row.
5. **Remember:** the resolved incident, its root-cause change and the fix go back into the graph.
   The next similar alert is solved faster.
6. **Explain:** every tool call is a `STEP` of an `Investigation` node. The UI replays exactly which
   nodes the agent looked at, in order.

## Architecture

```
┌──────────────────────────── AGENT HOSTS (the brain) ─────────────────────────────┐
│ Claude Code + plugin      Codex CLI        Cursor / VS Code     Headless responder │
│ /oncall /blast-radius     config.toml      mcp.json             `rootpath watch`│
│ /postmortem + guard hook  + AGENTS.md                           → claude -p         │
└────────────┬─────────────────┬────────────────┬──────────────────────┬────────────┘
             └─────────────────┴──── MCP (stdio) ┴──────────────────────┘
┌──────────────────────────── rootpath MCP server ─────────────────────────────┐
│ READ        get_service_health · get_logs · get_blast_radius · recent_changes      │
│             get_owner · find_similar_incidents · explain_incident · query_graph    │
│ REASON      rank_suspects (traversal + betweenness) · trace_dependency_path (SPpaths)│
│ ACT         rollback_service · restart_service · set_feature_flag  (dry-run first)  │
│ LIFECYCLE   open_incident · verify_recovery · notify_owner · resolve_incident       │
└──────────┬────────────────────────────┬──────────────────────────────┬────────────┘
           │ Cypher (RO for reasoning)   │ ingest (idempotent MERGE)    │ docker compose
┌──────────▼───────────┐   ┌────────────┴─────────────┐   ┌────────────▼────────────┐
│ FalkorDB             │◄──│ compose labels + env      │   │ 7 microservices + load  │
│ services, calls,     │   │ live /_stats (observed    │   │ feature flags           │
│ teams, people, flags,│   │   calls, health)          │   │ change log (CI/CD+flags)│
│ deploys, incidents,  │   │ teams.json · runbooks     │   │ chaos scenarios         │
│ actions, evidence,   │   │ change log                │   └─────────────────────────┘
│ investigations       │   └───────────────────────────┘
└──────────▲───────────┘
           └── Web UI (localhost:7100): live graph + numbered agent path + report
```

## Quickstart (about 3 minutes)

Requirements:

- Docker
- [uv](https://docs.astral.sh/uv/)
- Claude Code, logged in, for the headless agent. Any MCP host can use the tools.

There's no hosted copy of the stack, because the agent controls real Docker containers. The website
is the hosted part: it replays recorded real incidents in the browser.

```bash
git clone <this repo> rootpath && cd rootpath
uv sync
uv run rootpath demo up        # FalkorDB :6380 (browser :3005), 7 services, load generator, seeded history
uv run rootpath ui             # http://localhost:7100
```

Break something and let the agent fix it:

```bash
uv run rootpath chaos flag     # or: bad-deploy | pool
uv run rootpath watch --once   # alert fires → headless Claude Code investigates, fixes, verifies
```

Or do it all from the console at http://localhost:7100:

- **Break something** picks any of the six scenarios.
- **graph / baseline** switches which toolset the agent gets.
- **Run agent** starts the headless agent.

While it runs, the path lights up step by step. Click a step to see what it looked at, or click any
node for its properties. The **Incident memory** tab shows every incident with its root cause and fix,
and compares mean time to resolve for humans and the agent. **Changes** lists recent deploys and flag flips.

**Reset** restores a healthy state but keeps the graph, so the next incident can find the memory
the last one wrote. Shift-click Reset for a fresh graph, or run `uv run rootpath demo reset`.

| Scenario | What happens | Correct fix | Red herring |
|---|---|---|---|
| `bad-deploy` | payment v2.4.1 ships a tokenizer bug → checkout and frontend 5xx | roll back payment | catalog deploy at the same moment |
| `flag` | `fraud_model_v2` makes fraud-check slow → payment times out 3 hops below the alert | flag off | notify deploy at the same moment |
| `pool` | inventory's DB pool is exhausted, with **no change at all** → checkout and catalog fail | restart inventory | none; recent-change heuristics alone fail here |

## Use it from your agent

**Claude Code (plugin):** MCP server, `/rootpath:oncall`, `/rootpath:blast-radius`, `/rootpath:postmortem`, plus the safety hook.

```bash
/plugin marketplace add IWhitebird/rootpath
/plugin install rootpath@rootpath
# or, from a clone:
claude --plugin-dir /path/to/rootpath
```

Then type `/rootpath:oncall checkout is throwing 500s`. Read tools are pre-approved. Real actions
go through Claude Code's permission prompt (human in the loop), and the guard hook denies any
real action that wasn't dry-run first, or anything at all during a change freeze (`touch .oncall/FREEZE`).

**Codex CLI** (`~/.codex/config.toml`); the playbook lives in [`AGENTS.md`](AGENTS.md):

```toml
[mcp_servers.oncall]
command = "uv"
args = ["run", "--quiet", "--directory", "/abs/path/to/rootpath", "rootpath", "mcp"]
[mcp_servers.oncall.env]
FALKORDB_URL = "redis://localhost:6380"
```

**Cursor / VS Code / any MCP host:** the same server, as a stdio command:
`uv run --directory /abs/path/to/rootpath rootpath mcp`.

**Headless / autonomous:** `uv run rootpath watch` polls health. When a user-facing service fails
twice in a row, it launches `claude -p` with only the oncall MCP tools (`--strict-mcp-config --tools ""`)
and stores the run (tool calls, tokens, cost, report) on the `Investigation` node.
Use `--autonomy propose` for dry-run-only recommendations.

## Does the graph matter? (benchmark)

`uv run rootpath bench` injects each failure into the live stack and runs the same headless
agent (Claude Opus 5.5 through Claude Code) twice, with the same remediation actions both times:

- **graph** gets the FalkorDB reasoning and memory tools.
- **baseline** gets what a plain observability stack gives you: health, logs and a flat change log.

Each of the 6 scenarios runs twice per mode at two telemetry levels: **with tracing** (stats say where
errors originate) and **without** (`ONCALL_TELEMETRY=basic`: error rate and latency only). That's 48
runs on the final code.

| telemetry | mode | clean fix¹ | logs read before the fix | calls to root cause | agent time | cost |
|---|---|---|---|---|---|---|
| with tracing | graph | **12/12** | **12** | 48 | **519 s** | $1.68 |
| with tracing | baseline | 11/12 | 21 | 46 | 687 s | $1.55 |
| without tracing | graph | **12/12** | **12** | **48** | **532 s** | **$1.62** |
| without tracing | baseline | 12/12 | 42 | 68 | 675 s | $1.80 |

¹ The expected action on the expected target (roll back payment, flag off, restart inventory), and no
other remediation action. Per-scenario tables and every tool sequence:
[`bench/RESULTS-full.md`](bench/RESULTS-full.md), [`bench/RESULTS-basic.md`](bench/RESULTS-basic.md).

**What we learned:**

- **A frontier model with good telemetry usually finds the cause without a graph.** Baseline took the
  right action in all 24 runs. On a 7-service system the answer is often readable from the health table.
- **The graph sends the agent straight to where the failure starts.** It read 24 service logs before
  fixing, against 63 for baseline (62% fewer). Without tracing the gap widens to 12 vs 42, because
  baseline has to walk the call chain one log at a time.
- **That makes it faster, and without tracing cheaper.** Agent time was 1051 s vs 1362 s in total.
  Cost was $3.30 vs $3.35, even though the graph agent also opens and resolves the incident, pages
  the owner and writes memory.
- **Wrong actions come from coincidences.** In one flag-collision run, baseline first rolled back the
  healthy fraud-check release that happened to land minutes before the flag flip. That fixed nothing
  and cost 4 minutes before it found the flag. The graph agent made no wrong actions in 24 runs.
- **What changed between versions, honestly.** The first version of `rank_suspects` pointed at the
  right service but didn't pass along what the graph knew about it. In that run the graph agent
  itself once rolled back a healthy inventory release (the `pool-after-deploy` trap) without reading
  the runbook. `rank_suspects` now returns the top suspect's runbooks and past incidents with the
  ranking. The v1 results are kept in [`bench/archive/`](bench/archive/).
- **Caveat:** 2 runs per cell is a small sample, so read these as directional.

## Website and incident replays

`uv run rootpath record <scenario> --out site/data/<scenario>.json` records a real incident end
to end: live health every second, every graph node and edge as it appears, and every agent step
with its timestamp and the nodes it touched. `python3 site/build.py` inlines the recordings and the
benchmark results into one self-contained `site/index.html`. The page replays the incidents in the
browser with a scrubbable timeline, so anyone can watch the agent work without Docker or an API key.
Pushing to GitHub publishes it through GitHub Pages; set *Settings → Pages → Source* to *GitHub Actions* once.

## Tests

```bash
uv run pytest tests/test_unit.py          # 17 fast tests, no Docker needed (also run in CI)
uv run pytest tests/test_integration.py   # 14 end-to-end tests against the live demo stack (~7 min)
```

The integration suite does three things against the live stack:

- It injects every scenario and checks that `rank_suspects` finds the service where the failure
  starts, with red herrings scored low.
- It takes the full fix loop through the real action tools: dry run, execute, verify, page the owner,
  resolve, explain, recall from memory.
- It checks the guarantees: rollback never redeploys a release that was itself rolled back,
  `ONCALL_ALLOW_ACTIONS=0` forces dry runs, the agent's Cypher is read-only, and bad input returns
  an error instead of crashing.

## Graph data model

```
(:Team)-[:OWNS]->(:Service)-[:CALLS {declared, optional, observed}]->(:Service)
(:Person)-[:MEMBER_OF]->(:Team)   (:Person)-[:ON_CALL_FOR]->(:Team)-[:ESCALATES_TO]->(:Team)
(:Runbook)-[:FOR]->(:Service)
(:Team)-[:OWNS]->(:Flag)-[:AFFECTS]->(:Service)

(:Change:Deploy {version, prev_version, at})-[:CHANGED]->(:Service)
(:Change:FlagChange {flag, from, to, at})-[:TOGGLED]->(:Flag)
(:Person)-[:AUTHORED]->(:Change)      (:Deploy)-[:ROLLED_BACK]->(:Deploy)

(:Incident)-[:IMPACTED]->(:Service)
(:Incident)-[:ROOT_CAUSE]->(:Change | :Service)
(:Incident)-[:HAS_ACTION | RESOLVED_BY]->(:Action)-[:BECAUSE]->(:Evidence)
(:Action)-[:REVERTS]->(:Change)   (:Action)-[:ON]->(:Service | :Flag)   (:Action)-[:PRODUCED]->(:Change)
(:Incident)-[:NOTIFIED]->(:Notification)-[:TO]->(:Person)

(:Investigation)-[:FOR]->(:Incident)
(:Investigation)-[:STEP {n, tool, note, at}]->(any node the agent looked at)
```

**Where the data comes from:** services, owners and declared calls come from the compose file's
labels and env. *Observed* calls and live health come from each service's `/_stats`. Teams and
on-call come from `demo/teams.json`, runbooks from Markdown front-matter, and deploys and flag flips
from the change log (the CI/CD + flag-service audit trail). Past incidents are seeded synthetic
history. All ingestion is idempotent `MERGE`. The agent's reasoning runs as read-only `GRAPH.RO_QUERY`
with a 2s timeout; only deterministic code writes.

## The Cypher the product depends on

Candidate suspects: everything downstream of the alerting service, with distance:

```cypher
MATCH p = (s:Service {name: $s})-[:CALLS*0..6]->(d:Service)
RETURN d.name AS service, min(length(p)) AS hops
```

Changes on those candidates, including flags that reach a service only through `AFFECTS`:

```cypher
MATCH (c:Deploy)-[:CHANGED]->(d:Service) WHERE d.name IN $names AND c.at >= $since RETURN d.name, c
MATCH (x:FlagChange)-[:TOGGLED]->(f:Flag)-[:AFFECTS]->(d:Service) WHERE d.name IN $names AND x.at >= $since RETURN d.name, x
```

Graph algorithms used as agent tools:

```cypher
CALL algo.betweenness({nodeLabels: ['Service'], relationshipTypes: ['CALLS']}) YIELD node, score
MATCH (a:Service {name: $a}), (b:Service {name: $b})
CALL algo.SPpaths({sourceNode: a, targetNode: b, relTypes: ['CALLS'], pathCount: 1}) YIELD path
```

Changes ruled out because they aren't in the dependency graph of the symptom:

```cypher
MATCH (c:Change) WHERE c.at >= $since
OPTIONAL MATCH (c)-[:CHANGED]->(s:Service)
OPTIONAL MATCH (c)-[:TOGGLED]->(:Flag)-[:AFFECTS]->(fs:Service)
WITH c, coalesce(s.name, fs.name) AS target WHERE NOT target IN $names
RETURN c, target
```

Blast radius (who is affected upstream) and ownership with escalation and runbooks:

```cypher
MATCH p = (u:Service)-[:CALLS*1..6]->(s:Service {name: $s})
WITH u, min(length(p)) AS hops MATCH (t:Team)-[:OWNS]->(u) RETURN u.name, hops, u.internet_facing, t.name

MATCH (t:Team)-[:OWNS]->(s:Service {name: $s})
OPTIONAL MATCH (p:Person)-[:ON_CALL_FOR]->(t)
OPTIONAL MATCH (t)-[:ESCALATES_TO]->(e:Team)<-[:ON_CALL_FOR]-(ep:Person)
OPTIONAL MATCH (r:Runbook)-[:FOR]->(s)
RETURN t.name, p.handle, e.name, ep.handle, collect(r)
```

Incident memory: a full-text index plus graph overlap, then root cause and fix:

```cypher
CALL db.idx.fulltext.createNodeIndex('Incident', 'title', 'symptom', 'summary')
CALL db.idx.fulltext.queryNodes('Incident', 'checkout|timeout|fraud') YIELD node, score
MATCH (i:Incident)-[:IMPACTED|ROOT_CAUSE]->(:Service {name: $s}) RETURN i.id
MATCH (i:Incident) WHERE i.id IN $ids
OPTIONAL MATCH (i)-[:ROOT_CAUSE]->(rc) OPTIONAL MATCH (i)-[:RESOLVED_BY]->(a:Action)
RETURN i, collect(rc), collect(a)
```

The safety guard (`rootpath/guard.py`) is itself a graph query: no real action unless a matching dry run exists.

```cypher
MATCH (a:Action {kind: $kind, target: $target, dry_run: true}) WHERE a.at >= $since RETURN count(a)
```

## Safety

- Reasoning is read-only (`GRAPH.RO_QUERY`, timeout, row cap). The agent can't write arbitrary Cypher.
- Actions default to `dry_run=true`, and the plugin hook enforces "dry run before real run" against the graph.
- `ONCALL_ALLOW_ACTIONS=0` forces every action to be a dry run, for read-only deployments.
- Change freeze: `touch .oncall/FREEZE` or `ONCALL_CHANGE_FREEZE=1`.
- The headless agent gets no shell, file or web tools, only the oncall MCP tools.
- Every action is attributable: who, why, what it reverted, which investigation.

## Related work

- **[woodpecker-mcp](https://github.com/sspcodeflix/woodpecker-mcp):** also stores a service dependency
  graph in FalkorDB and exposes topology, root-cause and blast-radius tools over MCP. It is analysis-only:
  no change correlation (deploys/flags), no remediation or verification, no incident memory.
- **AWS DevOps Agent, Datadog Bits AI SRE, Dynatrace Smartscape/Davis, Causely:** graph- or topology-based
  commercial AI SRE. They are closed and tied to their own platform.
- **Resolve.ai, Cleric, Traversal:** AI SRE startups.

Rootpath is open, self-hosted, uses FalkorDB as the reasoning substrate rather than as storage,
acts and verifies, remembers, and runs inside whichever agent you already use.

## Project layout

```
rootpath/   tools.py (agent tools) · mcp_server.py · ingest.py · seed.py · infra.py · responder.py
                bench.py · record.py · web.py · guard.py · store.py · cli.py
demo/           docker-compose.yml · service/ (one configurable microservice) · teams.json · runbooks/ · flags/
skills/         oncall · blast-radius · postmortem (Claude Code plugin skills)
hooks/          hooks.json (PreToolUse guard)
.claude-plugin/ plugin.json (MCP server inline) · marketplace.json
prompts/        headless agent playbooks
ui/             index.html (operator console)
site/           src/index.html (website) · data/ (recorded incidents) · build.py
bench/          results-*.json and RESULTS-*.md (graph vs baseline)
tests/          test_unit.py · test_integration.py
docs/           DEMO_SCRIPT.md · BLOG.md · SUBMISSION.md · screenshots
```

### CLI

| command | what it does |
|---|---|
| `rootpath demo up / down / reset [--soft]` | run, stop or reset the demo stack |
| `rootpath chaos <scenario>` | inject a failure (`bad-deploy`, `flag`, `pool`, `deploy-noise`, `flag-collision`, `pool-after-deploy`) |
| `rootpath status` | live health table |
| `rootpath ui` | operator console on :7100 |
| `rootpath mcp [--mode graph\|baseline]` | MCP server on stdio |
| `rootpath watch [--once] [--autonomy propose]` | alert watcher that launches the headless agent |
| `rootpath bench [--scenario ...] [--repeats N]` | graph vs baseline benchmark |
| `rootpath record <scenario> --out file.json` | record an incident for the website |
| `rootpath call <tool> key=value ...` | call any agent tool directly |
| `rootpath sync` | rebuild the graph from its sources |

## AI assistance disclosure

Built during the hackathon with Claude Code (Claude Opus 5.5) as a pair programmer. It wrote
substantial parts of the code, the UI and this README under my direction. Architecture, scope and
product decisions, review, and testing against the running system were done by me. At runtime the
agent itself is Claude (via Claude Code headless or any MCP host). There is no model training or
fine-tuning anywhere in the project.

## License

Apache-2.0. FalkorDB itself is licensed under SSPL.
