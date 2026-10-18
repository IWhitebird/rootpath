/** Markup the side panels of both front-ends share: agent steps and the health ordering. */
import { esc } from "./html";
import type { Step } from "./types";

const ACTIONS = new Set(["rollback_service", "restart_service", "set_feature_flag"]);
const SEVERITY: Record<string, number> = { down: 0, failing: 1, degraded: 2, healthy: 3, idle: 4, unknown: 5 };

/** One row of an agent's step list. `cls` carries state classes (on, sel, enter). A real
 * remediation, as opposed to a dry run, is marked with the `act` colour. */
export function stepItem(s: Step, cls: string): string {
  const note = s.note || "";
  const act = ACTIONS.has(s.tool) && !/DRY RUN/.test(note);
  return `<div class="step ${cls}" data-n="${s.n}"><div class="num">${s.n}</div><div>
    <div class="tool ${act ? "act" : ""}">${esc(s.tool)}</div><div class="note">${esc(note)}</div></div></div>`;
}

/** Worst status first, then by service name. */
export function sortByHealth<T>(entries: [string, T][], status: (value: T) => string): [string, T][] {
  return entries.sort(([a, x], [b, y]) => SEVERITY[status(x)] - SEVERITY[status(y)] || a.localeCompare(b));
}
