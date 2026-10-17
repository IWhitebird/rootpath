"""Record the website's incident replay to video frames via the Chrome DevTools protocol.

    uv run --with websocket-client python scripts/capture_replay.py <url> <out_dir> [seconds] [fps]
Then encode with ffmpeg (see scripts/make_media.sh)."""
import base64
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import websocket

url, out = sys.argv[1], Path(sys.argv[2])
seconds = float(sys.argv[3]) if len(sys.argv) > 3 else 40
fps = float(sys.argv[4]) if len(sys.argv) > 4 else 10
out.mkdir(parents=True, exist_ok=True)
for f in out.glob("*.png"):
    f.unlink()

chrome = subprocess.Popen(["google-chrome", "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
                           "--remote-debugging-port=9333", "--remote-allow-origins=*", "--window-size=1440,1000", "--user-data-dir=/tmp/oncall-capture-profile",
                           "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(50):
        try:
            targets = json.load(urllib.request.urlopen("http://127.0.0.1:9333/json"))
            page = next(t for t in targets if t["type"] == "page")
            break
        except Exception:
            time.sleep(0.2)
    ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=30, suppress_origin=True)
    msg_id = 0

    def cdp(method, **params):
        global msg_id
        msg_id += 1
        ws.send(json.dumps({"id": msg_id, "method": method, "params": params}))
        while True:
            reply = json.loads(ws.recv())
            if reply.get("id") == msg_id:
                return reply.get("result", {})

    cdp("Emulation.setDeviceMetricsOverride", width=1440, height=1000, deviceScaleFactor=1, mobile=False)
    cdp("Page.navigate", url=url)
    time.sleep(2.5)
    rect = cdp("Runtime.evaluate", expression="JSON.stringify(document.getElementById('replay').getBoundingClientRect())",
               returnByValue=True)["result"]["value"]
    r = json.loads(rect)
    clip = {"x": r["x"], "y": r["y"], "width": r["width"], "height": r["height"], "scale": 1}
    cdp("Runtime.evaluate", expression="document.getElementById('restart').click()")
    start, n = time.time(), 0
    while time.time() - start < seconds:
        tick = time.time()
        shot = cdp("Page.captureScreenshot", format="png", clip=clip)
        (out / f"f{n:05d}.png").write_bytes(base64.b64decode(shot["data"]))
        n += 1
        time.sleep(max(0, 1 / fps - (time.time() - tick)))
    print(f"{n} frames in {time.time() - start:.1f}s ({n / (time.time() - start):.1f} fps) -> {out}")
finally:
    chrome.terminate()
