import "./site.css";
import { byId } from "../lib/html";
import { getJson } from "../lib/http";
import type { Bench, Replay } from "../lib/types";
import { renderStats } from "./bench";
import { Player } from "./replay";
import { Stage } from "./stage";

const DATA = `${import.meta.env.BASE_URL}data/`;

/** Copy an element's text, or select it where the clipboard is not available. */
async function copyText(button: HTMLElement, source: HTMLElement): Promise<void> {
  try {
    await navigator.clipboard.writeText(source.textContent ?? "");
    button.textContent = "Copied";
  } catch {
    const range = document.createRange();
    range.selectNodeContents(source);
    const selection = getSelection();
    selection?.removeAllRanges();
    selection?.addRange(range);
    button.textContent = "Selected";
  }
  setTimeout(() => {
    button.textContent = "Copy";
  }, 1600);
}

for (const button of document.querySelectorAll<HTMLButtonElement>("[data-copy]")) {
  const source = byId(button.dataset.copy ?? "");
  button.onclick = () => void copyText(button, source);
}

getJson<Bench>(`${DATA}bench.json`).then(renderStats, () => {
  byId("results").hidden = true;
});

// ?speed=3 and ?t=40 are for recording the README media (scripts/make_media.sh).
const params = new URLSearchParams(location.search);
getJson<Replay>(`${DATA}replays/flag.json`).then(
  (replay) => {
    const player = new Player(new Stage(), replay, Number(params.get("speed")) || 2);
    const t = params.get("t");
    if (t) player.seek(Number(t) || 0);
  },
  () => {
    byId("replay").hidden = true;
  },
);
