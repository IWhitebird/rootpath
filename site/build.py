"""Build the project website: inline the recorded incident replays and the benchmark results
into one self-contained HTML file (site/index.html, ready for GitHub Pages).

    uv run python site/build.py [--repo https://github.com/<owner>/oncall-graph] [--artifact out.html]
"""
import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
ACTIONS = {"rollback_service", "restart_service", "set_feature_flag"}


def bench(telemetry: str) -> list[dict]:
    path = ROOT / "bench" / f"results-{telemetry}.json"
    if not path.exists():
        return []
    rows = json.loads(path.read_text())
    for r in rows:
        tools = r.get("tools", [])
        r["diagnosis_calls"] = next((i for i, t in enumerate(tools) if t in ACTIONS), len(tools))
        r["logs_read"] = tools[:r["diagnosis_calls"]].count("get_logs")
        r.pop("tools", None)
    return rows


def git_remote() -> str:
    """The GitHub URL of this checkout, if it has one (git@github.com:o/r.git -> https://github.com/o/r)."""
    import subprocess
    try:
        url = subprocess.run(["git", "-C", str(ROOT), "config", "--get", "remote.origin.url"],
                             capture_output=True, text=True).stdout.strip()
    except OSError:
        return ""
    url = re.sub(r"^git@github\.com:", "https://github.com/", url)
    return re.sub(r"\.git$", "", url) if url.startswith("https://github.com/") else ""


def embed(obj) -> str:
    return json.dumps(obj, separators=(",", ":")).replace("</", "<\\/")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="")
    ap.add_argument("--artifact", help="also write a variant without the document wrapper")
    args = ap.parse_args()
    cfg_file = SITE / "config.json"
    config = json.loads(cfg_file.read_text()) if cfg_file.exists() else {}
    if args.repo:
        config["repo"] = args.repo
    elif not config.get("repo"):
        config["repo"] = git_remote()
    replays = {p.stem: json.loads(p.read_text()) for p in sorted((SITE / "data").glob("*.json"))}
    html = (SITE / "src" / "index.html").read_text()
    html = html.replace("/*__REPLAYS__*/", embed(replays))
    html = html.replace("/*__BENCH__*/", embed({"full": bench("full"), "basic": bench("basic")}))
    html = html.replace("/*__CONFIG__*/", embed(config))
    (SITE / "index.html").write_text(html)
    print(f"site/index.html: {len(html) / 1024:.0f} KB, {len(replays)} replays")
    if args.artifact:
        body = re.sub(r"<!doctype html>\s*", "", html, flags=re.I)
        body = re.sub(r"</?html[^>]*>|</?head>|</?body>", "", body)
        body = re.sub(r'<meta charset="utf-8">\s*|<meta name="viewport"[^>]*>\s*', "", body)
        Path(args.artifact).write_text(body.strip() + "\n")
        print(f"{args.artifact}: artifact variant")


if __name__ == "__main__":
    main()
