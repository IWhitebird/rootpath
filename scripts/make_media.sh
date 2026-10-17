#!/bin/sh
# Builds docs/replay.mp4 and docs/replay.gif from a recorded replay of the website.
set -e
cd "$(dirname "$0")/.."
FRAMES=${FRAMES:-/tmp/oncall-frames}
SCENARIO=${1:-flag}
uv run python site/build.py >/dev/null
python3 -m http.server 7201 --bind 127.0.0.1 --directory site >/dev/null 2>&1 &
SERVER=$!
trap 'kill $SERVER' EXIT
sleep 1
uv run --quiet --with websocket-client python scripts/capture_replay.py "http://127.0.0.1:7201/index.html?scenario=$SCENARIO&speed=3" "$FRAMES" 38 10
ffmpeg -y -loglevel error -framerate 10 -i "$FRAMES/f%05d.png" -c:v libx264 -pix_fmt yuv420p -crf 23 -vf "scale=1280:-2" docs/replay.mp4
ffmpeg -y -loglevel error -framerate 10 -i "$FRAMES/f%05d.png" -vf "fps=8,scale=880:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96[p];[b][p]paletteuse=dither=bayer:bayer_scale=4" docs/replay.gif
ls -la docs/replay.mp4 docs/replay.gif
