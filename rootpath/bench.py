"""Graph vs baseline benchmark: the same injected failure, the same model, the same actions;
the only difference is whether the agent gets the graph tools. Writes bench/RESULTS.md."""
import json
import time

from . import config, store
from .cli import SCENARIOS, inject, reset
from .responder import detect, run_agent

EXPECTED = {"bad-deploy": ("rollback", "payment"), "flag": ("toggle_flag", "fraud_model_v2"), "pool": ("restart", "inventory"),
            "deploy-noise": ("rollback", "payment"), "flag-collision": ("toggle_flag", "fraud_model_v2"),
            "pool-after-deploy": ("restart", "inventory")}
OUT = config.ROOT / "bench"


def _wait(cond, timeout: float, every: float = 3.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(every)
    return False


def one(scenario: str, mode: str) -> dict:
    reset()
    _wait(lambda: detect() is None, 60)
    inject(scenario)
    injected = time.time()
    hits = []
    _wait(lambda: hits.append(detect()) or (len(hits) >= 2 and hits[-1] and hits[-2]), 90, every=4)
    alert = hits[-1]
    if not alert:
        return {"scenario": scenario, "mode": mode, "error": "alert never fired"}
    summary = run_agent(alert[0], alert[1], mode=mode)
    recovered = _wait(lambda: detect() is None, 30)
    actions = store.read("MATCH (a:Action {investigation: $inv, dry_run: false}) RETURN a.kind AS kind, a.target AS target",
                         {"inv": summary["investigation"]})
    taken = [(a["kind"], a["target"]) for a in actions]
    expected = EXPECTED[scenario]
    return {
        "scenario": scenario, "mode": mode, "investigation": summary["investigation"],
        "fixed": recovered and expected in taken, "correct_action": expected in taken,
        "wrong_actions": [f"{k} {t}" for k, t in taken if (k, t) != expected],
        "time_to_resolve_s": round(time.time() - injected, 1), "agent_s": summary["duration_s"],
        "tool_calls": summary["tool_calls"], "tools": summary["tools"],
        "input_tokens": summary["input_tokens"], "output_tokens": summary["output_tokens"], "cost_usd": summary["cost_usd"],
    }


def run(scenarios: list[str], modes: list[str], repeats: int = 1) -> None:
    OUT.mkdir(exist_ok=True)
    results_file = OUT / f"results-{config.TELEMETRY}.json"
    results = []
    for scenario in scenarios:
        for mode in modes:
            for _ in range(repeats):
                print(f"\n=== {scenario} / {mode} ===", flush=True)
                results.append(one(scenario, mode))
                print(json.dumps(results[-1], indent=1), flush=True)
                results_file.write_text(json.dumps(results, indent=2))
    write_report(results)
    reset()


ACTIONS = {"rollback_service", "restart_service", "set_feature_flag"}


def diagnosis_calls(tools: list[str]) -> int:
    """Tool calls made before the first remediation action (i.e. to find the root cause)."""
    return next((i for i, t in enumerate(tools) if t in ACTIONS), len(tools))


def write_report(results: list[dict]) -> None:
    for r in results:
        if "tools" in r:
            r["diagnosis_calls"] = diagnosis_calls(r["tools"])
            r["logs_read"] = r["tools"][:r["diagnosis_calls"]].count("get_logs")
        r["clean"] = bool(r.get("correct_action")) and not r.get("wrong_actions")
    lines = ["# Benchmark: graph tools vs baseline tools", "",
             "Same injected failure, same model (headless Claude Code), same remediation actions. "
             "**graph** = FalkorDB reasoning tools; **baseline** = health + logs + flat change log only.", "",
             "*Correct fix* = the expected action on the expected target was taken. *Clean* = that, and no other "
             "(wrong) remediation action.", "",
             "| scenario | mode | correct fix | wrong actions | calls to root cause | logs read | total calls | agent time | input tokens | cost |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        if "error" in r:
            lines.append(f"| {r['scenario']} | {r['mode']} | error: {r['error']} | | | | | | | |")
            continue
        lines.append(f"| {r['scenario']} | {r['mode']} | {'✅' if r['correct_action'] else '❌'} | {', '.join(r['wrong_actions']) or '-'} | "
                     f"{r['diagnosis_calls']} | {r['logs_read']} | {r['tool_calls']} | {r['agent_s']}s | {r['input_tokens']:,} | ${(r['cost_usd'] or 0):.3f} |")
    lines += ["", "## Totals", "", "| mode | correct fix | clean fix | calls to root cause | logs read | total calls | agent time | input tokens | cost |",
              "|---|---|---|---|---|---|---|---|---|"]
    for mode in ("graph", "baseline"):
        rs = [r for r in results if r.get("mode") == mode and "error" not in r]
        if rs:
            lines.append(f"| {mode} | {sum(r['correct_action'] for r in rs)}/{len(rs)} | {sum(r['clean'] for r in rs)}/{len(rs)} | "
                         f"{sum(r['diagnosis_calls'] for r in rs)} | "
                         f"{sum(r['logs_read'] for r in rs)} | {sum(r['tool_calls'] for r in rs)} | "
                         f"{sum(r['agent_s'] for r in rs):.0f}s | {sum(r['input_tokens'] for r in rs):,} | "
                         f"${sum((r['cost_usd'] or 0) for r in rs):.2f} |")
    lines += ["", "*Graph-mode totals include incident bookkeeping (open_incident, notify_owner, resolve_incident) "
              "that baseline has no tools for; \"calls to root cause\" counts only the calls before the first fix.*",
              "", "Tool sequences:", ""] + [f"- **{r['scenario']} / {r['mode']}**: {' → '.join(r.get('tools', []))}" for r in results]
    lines.insert(1, f"\n**Telemetry: `{config.TELEMETRY}`** "
                 + ("(error rate, latency and where errors originate: what distributed tracing gives you)" if config.TELEMETRY == "full"
                    else "(error rate and latency only: no tracing, the agent has to work out how failures propagate)"))
    (OUT / f"RESULTS-{config.TELEMETRY}.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
