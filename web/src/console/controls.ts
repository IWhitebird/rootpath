/** Header controls (break something, graph/baseline mode, run the agent, reset), the right rail's
 * tabs, and the toast that reports what a control did. */
import { byId, esc } from "../lib/html";
import { getJson } from "../lib/http";
import { hideDetail } from "./view";

type Mode = "graph" | "baseline";

export interface ControlHooks {
  /** Reload what a control just changed. */
  refresh: () => void;
  /** Forget the selected step: a new incident or agent run is about to replace it. */
  deselect: () => void;
}

let toastTimer = 0;

function toast(message: string): void {
  let el = document.querySelector<HTMLElement>(".toast");
  if (!el) {
    el = document.createElement("div");
    el.className = "toast";
    el.setAttribute("role", "status");
    document.body.appendChild(el);
  }
  el.textContent = message;
  clearTimeout(toastTimer);
  const shown = el;
  toastTimer = window.setTimeout(() => shown.remove(), 3200);
}

export function initControls({ refresh, deselect }: ControlHooks): void {
  const post = async (url: string, done: string) => {
    let res: Response;
    try {
      res = await fetch(url, { method: "POST" });
    } catch {
      toast("The console API is unreachable.");
      return;
    }
    if (!res.ok) {
      const body = (await res.json().catch(() => ({}))) as { detail?: string };
      toast(body.detail || `Request failed (${res.status})`);
      return;
    }
    toast(done);
    setTimeout(refresh, 600);
  };

  const menu = byId("break-menu");
  const breakBtn = byId("break-btn");
  const closeMenu = () => {
    menu.hidden = true;
    breakBtn.setAttribute("aria-expanded", "false");
  };
  getJson<Record<string, string>>("/api/scenarios").then(
    (scenarios) => {
      menu.innerHTML = Object.entries(scenarios)
        .map(([key, about]) => `<button data-s="${esc(key)}"><b>${esc(key)}</b><span>${esc(about)}</span></button>`)
        .join("");
      for (const b of menu.querySelectorAll<HTMLButtonElement>("button")) {
        const scenario = b.dataset.s ?? "";
        b.onclick = () => {
          closeMenu();
          deselect();
          void post(`/api/chaos/${encodeURIComponent(scenario)}`, `Injecting ${scenario}. Watch the health panel.`);
        };
      }
    },
    () => toast("Could not load the failure scenarios."),
  );
  breakBtn.onclick = (e) => {
    e.stopPropagation();
    menu.hidden = !menu.hidden;
    breakBtn.setAttribute("aria-expanded", String(!menu.hidden));
  };
  document.addEventListener("click", (e) => {
    if (!(e.target instanceof Element && e.target.closest(".menu-wrap"))) closeMenu();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    closeMenu();
    hideDetail();
  });

  let mode: Mode = "graph";
  const setMode = (m: Mode) => {
    mode = m;
    byId("mode-graph").setAttribute("aria-pressed", String(m === "graph"));
    byId("mode-baseline").setAttribute("aria-pressed", String(m === "baseline"));
    byId("run").textContent = m === "graph" ? "Run agent" : "Run baseline agent";
  };
  byId("mode-graph").onclick = () => setMode("graph");
  byId("mode-baseline").onclick = () => setMode("baseline");
  byId("run").onclick = () => {
    deselect();
    void post(`/api/agent?mode=${mode}`, `Agent started with ${mode} tools.`);
  };
  byId("reset").onclick = (e) => {
    const hard = e.shiftKey;
    void post(hard ? "/api/reset?hard=true" : "/api/reset", hard ? "Resetting with a fresh graph." : "Restoring healthy state. Incident memory is kept.");
  };

  const tabs = document.querySelectorAll<HTMLButtonElement>(".tabs button");
  for (const tab of tabs) {
    tab.onclick = () => {
      for (const t of tabs) t.setAttribute("aria-selected", String(t === tab));
      for (const panel of ["inv", "inc", "chg"]) byId(`panel-${panel}`).hidden = panel !== tab.dataset.tab;
    };
  }
}
