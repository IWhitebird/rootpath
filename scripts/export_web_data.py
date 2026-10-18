"""Write the website's benchmark data (web/public/data/bench.json) from bench/results-full.json and
bench/results-basic.json. `rootpath bench` does this after every run.

    uv run python scripts/export_web_data.py
"""

from rootpath.bench import export_web_data

if __name__ == "__main__":
    export_web_data()
