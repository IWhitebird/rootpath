const ENTITIES: Record<string, string> = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" };

/** Escape a value for HTML text and double-quoted attributes. */
export function esc(value: unknown): string {
  return String(value).replace(/[&<>"]/g, (c) => ENTITIES[c]);
}

/** The element with this id. The pages ship their own markup, so a missing id is a bug. */
export function byId<T extends HTMLElement = HTMLElement>(id: string): T {
  const el = document.getElementById(id);
  if (!el) throw new Error(`#${id} is missing from the page`);
  return el as T;
}

export const prefersReducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
