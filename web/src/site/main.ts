import "./site.css";
import { byId } from "../lib/html";
import { getJson } from "../lib/http";
import type { Bench, Replay } from "../lib/types";
import { renderBench } from "./bench";
import { renderInstall } from "./install";
import { LiveView } from "./live";
import { Player, prepare, RECORDINGS, type Recording, type Tab } from "./replay";
import { Stage } from "./stage";

const REPO = "https://github.com/IWhitebird/rootpath";
const DATA = `${import.meta.env.BASE_URL}data/`;
const LIVE: Tab = { key: "live", title: "Live · FalkorDB Cloud", sub: "the real graph, right now" };

/** The recordings that loaded, in tab order; a missing one just has no tab. */
async function loadRecordings(): Promise<Recording[]> {
  const loaded = await Promise.all(
    RECORDINGS.map((tab) => getJson<Replay>(`${DATA}replays/${tab.key}.json`).then((r) => prepare(tab, r), () => null)),
  );
  return loaded.filter((r) => r !== null);
}

function renderFacts(recordings: Recording[]): void {
  const durations = recordings.map((r) => r.agent.duration_s);
  const runs = recordings.length
    ? [`<span>Recorded agent runs: <b>${recordings.length}</b>, fixed and verified in <b>${Math.min(...durations).toFixed(0)}–${Math.max(...durations).toFixed(0)} s</b> each</span>`]
    : [];
  byId("facts").innerHTML = [
    ...runs,
    "<span>Tools: <b>24</b> over MCP, <b>3</b> of them change infrastructure</span>",
    "<span>Hosts: <b>Claude Code</b> (plugin), <b>Codex</b>, <b>Cursor</b>, headless</span>",
  ].join("");
}

async function startConsole(): Promise<void> {
  const recordings = await loadRecordings();
  renderFacts(recordings);
  const stage = new Stage();
  const player = new Player(stage, recordings);
  const live = new LiveView(stage);
  const tabs = byId("tabs");
  const select = (key: string) => {
    for (const b of tabs.querySelectorAll<HTMLElement>(".tab")) b.setAttribute("aria-selected", String(b.dataset.key === key));
    if (key === LIVE.key) {
      player.setPlaying(false);
      live.start();
    } else {
      live.stop();
      player.load(key);
    }
  };
  for (const tab of [...recordings, LIVE]) {
    const b = document.createElement("button");
    b.className = "tab";
    b.setAttribute("role", "tab");
    b.dataset.key = tab.key;
    b.innerHTML = `${tab === LIVE ? '<span class="pulse" aria-hidden="true"></span>' : ""}${tab.title}<small>${tab.sub}</small>`;
    b.onclick = () => {
      select(tab.key);
      if (tab !== LIVE) player.setPlaying(true);
    };
    tabs.appendChild(b);
  }

  const params = new URLSearchParams(location.search);
  const wanted = params.get("scenario");
  const keys = [...recordings.map((r) => r.key), LIVE.key];
  const first = wanted && keys.includes(wanted) ? wanted : keys[0];
  select(first);
  const t = params.get("t");
  if (t && first !== LIVE.key) player.seek(Number(t) || 0);
  const speed = params.get("speed");
  if (speed) player.setSpeed(Number(speed) || 2);
}

renderInstall(REPO);
getJson<Bench>(`${DATA}bench.json`).then(renderBench, () => {
  byId("evidence").hidden = true;
});
void startConsole();
