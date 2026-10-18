"""PreToolUse hook for the Claude Code plugin. Policy, checked against the graph:
1. During a change freeze (ONCALL_CHANGE_FREEZE=1 or a .oncall/FREEZE file) every real action is denied.
2. A real action (dry_run=false) is denied unless the same action on the same target was
   dry-run in the last 15 minutes, i.e. the agent has looked at the plan first."""

import json
import os
import sys
import time

# A broken config (bad TOML, wrong URL scheme) must block real actions, not crash the hook: Claude Code
# treats a crashed hook as non-blocking, which would let the action through.
try:
    from . import config, store
except Exception as import_error:  # noqa: BLE001
    config = store = None
    _BROKEN = f"{type(import_error).__name__}: {import_error}"
else:
    _BROKEN = None

KIND = {
    "rollback_service": ("rollback", "service"),
    "restart_service": ("restart", "service"),
    "set_feature_flag": ("toggle_flag", "flag"),
}


def decide(event: dict) -> tuple[str, str] | None:
    tool = event.get("tool_name", "").rsplit("__", 1)[-1]
    args = event.get("tool_input") or {}
    if tool not in KIND or args.get("dry_run", True) is not False:
        return None  # dry runs are always fine
    if os.environ.get("ONCALL_CHANGE_FREEZE") == "1" or (config.ROOT / ".oncall" / "FREEZE").exists():
        return "deny", "Change freeze is active: real remediation actions are blocked. Escalate to a human."
    kind, key = KIND[tool]
    rows = store.read(
        "MATCH (a:Action {kind: $kind, target: $target, dry_run: true}) WHERE a.at >= $since RETURN count(a) AS n",
        {"kind": kind, "target": args.get(key), "since": time.time() - 900},
    )
    if not rows or rows[0]["n"] == 0:
        return "deny", (
            f"rootpath guard: run {tool} with dry_run=true on '{args.get(key)}' first and review "
            "the plan; real actions need a recorded dry run from the last 15 minutes."
        )
    return None


def main() -> None:
    try:
        event = json.load(sys.stdin)
        if _BROKEN:
            raise RuntimeError(_BROKEN)
        verdict = decide(event)
    except Exception as e:  # unreadable event, broken config or graph unreachable: fail closed
        verdict = ("deny", f"rootpath guard could not check this action ({type(e).__name__}); blocking it.")
    if verdict:
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": verdict[0],
                        "permissionDecisionReason": verdict[1],
                    }
                }
            )
        )
    sys.exit(0)


if __name__ == "__main__":
    main()
