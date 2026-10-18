"""Alert watcher + headless agent launcher.

Polls live service health; when a user-facing service keeps failing, it starts a headless
Claude Code session (`claude -p`) wired to the oncall MCP server, streams the agent's tool
calls to the console, and stores the run (tokens, cost, duration) on the Investigation node."""
import json
import os
import shutil
import subprocess
import time
import uuid

from . import config, infra, store

RUNS = config.ROOT / ".oncall" / "runs"


def mcp_config(mode: str, investigation_id: str, autonomy: str = "auto") -> str:
    path = config.ROOT / ".oncall" / f"mcp-{mode}-{investigation_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    env = {"ONCALL_MODE": mode, "ONCALL_INVESTIGATION_ID": investigation_id, "ONCALL_AGENT": f"headless-{mode}",
           "ONCALL_TELEMETRY": config.TELEMETRY,
           "FALKORDB_URL": os.environ.get("FALKORDB_URL", f"redis://{config.FALKORDB_HOST}:{config.FALKORDB_PORT}")}
    if config.NOTIFY_WEBHOOK:
        env["ONCALL_NOTIFY_WEBHOOK"] = config.NOTIFY_WEBHOOK
    if autonomy == "propose" or not config.ALLOW_ACTIONS:
        env["ONCALL_ALLOW_ACTIONS"] = "0"  # enforced by the server, not just asked of the model
    path.write_text(json.dumps({"mcpServers": {"oncall": {
        "command": shutil.which("uv") or "uv",
        "args": ["run", "--quiet", "--directory", str(config.ROOT), "rootpath", "mcp", "--mode", mode],
        "env": env}}}, indent=2))
    return str(path)


def detect(window: float = 10) -> tuple[str, dict] | None:
    """Something is failing -> alert on the most user-facing unhealthy service (that's where the
    impact is felt), not on whichever internal service crossed the threshold first."""
    snap = infra.all_stats(window)
    if not any(s["status"] in ("failing", "down") for s in snap.values()):
        return None
    svcs = infra.services()
    unhealthy = [n for n, s in snap.items() if s["status"] in ("failing", "down", "degraded")]
    unhealthy.sort(key=lambda n: (not svcs[n]["internet_facing"], svcs[n]["tier"], snap[n].get("error_rate", 1) * -1))
    return unhealthy[0], snap[unhealthy[0]]


def run_agent(alert_service: str, alert: dict, mode: str = "graph", autonomy: str = "auto",
              quiet: bool = False, max_turns: int = 40) -> dict:
    inv = f"inv-{time.strftime('%H%M%S')}-{mode}-{uuid.uuid4().hex[:4]}"
    store.query("MERGE (v:Investigation {id: $id}) SET v.started_at = $now, v.agent = $agent, v.mode = $mode, "
                "v.alert = $alert, v.steps = 0",
                {"id": inv, "now": time.time(), "agent": f"headless-{mode}", "mode": mode,
                 "alert": f"{alert_service} error rate {alert.get('error_rate', 1):.0%}"})
    prompt = (f"ALERT {time.strftime('%H:%M:%S')}: {alert_service} is {alert['status']}: error rate "
              f"{alert.get('error_rate', 1):.0%}, p95 {alert.get('p95_ms', 0):.0f}ms over the last 10s. "
              "Investigate and resolve it.")
    if autonomy == "propose":
        prompt += " You are in PROPOSE mode: only call actions with dry_run=true and recommend the fix."
    system = config.ROOT / "prompts" / ("oncall-agent.md" if mode == "graph" else "oncall-agent-baseline.md")
    cmd = ["claude", "-p", prompt, "--mcp-config", mcp_config(mode, inv, autonomy), "--strict-mcp-config",
           "--tools", "", "--allowedTools", "mcp__oncall__*", "--permission-mode", "dontAsk",
           "--max-turns", str(max_turns), "--output-format", "stream-json", "--verbose",
           "--append-system-prompt-file", str(system)]
    if os.environ.get("ONCALL_AGENT_MODEL"):
        cmd += ["--model", os.environ["ONCALL_AGENT_MODEL"]]
    RUNS.mkdir(parents=True, exist_ok=True)
    transcript = RUNS / f"{inv}.jsonl"
    started = time.time()
    tool_calls, result = [], {}
    say = (lambda *a: None) if quiet else (lambda *a: print(*a, flush=True))
    say(f"\n🚨 {prompt}\n   investigation {inv} ({mode} mode) -> headless Claude Code")
    with transcript.open("w") as out, subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                                       cwd=str(config.ROOT)) as proc:
        for line in proc.stdout:
            out.write(line)
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "assistant":
                for block in ev["message"].get("content", []):
                    if block.get("type") == "tool_use":
                        name = block["name"].replace("mcp__oncall__", "")
                        tool_calls.append(name)
                        args = ", ".join(f"{k}={json.dumps(v)[:60]}" for k, v in block.get("input", {}).items())
                        say(f"   [{time.time() - started:5.1f}s] ● {name}({args})")
            elif ev.get("type") == "result":
                result = ev
        stderr = proc.stderr.read()
    usage = result.get("usage", {})
    summary = {
        "investigation": inv, "mode": mode, "alert_service": alert_service,
        "duration_s": round(time.time() - started, 1), "turns": result.get("num_turns"),
        "tool_calls": len(tool_calls), "tools": tool_calls, "cost_usd": result.get("total_cost_usd"),
        "input_tokens": (usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0)
                         + usage.get("cache_creation_input_tokens", 0)),
        "output_tokens": usage.get("output_tokens", 0), "is_error": result.get("is_error", not result),
        "report": result.get("result", "") or stderr[-800:],
    }
    store.query("MATCH (v:Investigation {id: $id}) SET v.finished_at = $now, v.duration_s = $d, v.tool_calls = $t, "
                "v.cost_usd = $c, v.input_tokens = $i, v.output_tokens = $o, v.report = $r",
                {"id": inv, "now": time.time(), "d": summary["duration_s"], "t": summary["tool_calls"],
                 "c": summary["cost_usd"] or 0.0, "i": summary["input_tokens"], "o": summary["output_tokens"],
                 "r": summary["report"][:4000]})
    (RUNS / f"{inv}.summary.json").write_text(json.dumps(summary, indent=2))
    say(f"\n✅ agent finished in {summary['duration_s']}s, {summary['tool_calls']} tool calls"
        + (f", ${summary['cost_usd']:.3f}" if summary["cost_usd"] else "") + f"\n\n{summary['report']}\n")
    return summary


def watch(mode: str = "graph", autonomy: str = "auto", once: bool = False, poll: float = 5.0) -> None:
    print(f"👀 watching {len(infra.services())} services every {poll:.0f}s (mode={mode}, autonomy={autonomy}) ...", flush=True)
    strikes = 0
    while True:
        hit = detect()
        strikes = strikes + 1 if hit else 0
        if hit and strikes >= 2:
            run_agent(hit[0], hit[1], mode=mode, autonomy=autonomy)
            if once:
                return
            strikes = 0
            time.sleep(30)  # cool down while metrics settle
        time.sleep(poll)
