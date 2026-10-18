/** The install section (a snippet per agent host) and the copy buttons. */
import { byId, esc } from "../lib/html";

interface Host {
  key: string;
  title: string;
  /** Highlighted snippet markup (pre.code spans). */
  code: string;
  note: string;
}

function hosts(): Host[] {
  const site = esc(window.location.origin);
  return [
    { key: "cli", title: "Install",
      code: `curl -fsSL ${site}/install.sh | sh\nrootpath init`,
      note: "rootpath init asks where your FalkorDB is (local Docker, FalkorDB Cloud or any URL), tests the connection, and connects Claude Code, Codex and Cursor." },
    { key: "claude", title: "Claude Code",
      code: `/plugin marketplace add IWhitebird/rootpath\n/plugin install rootpath@rootpath\n\n/rootpath:map\n/rootpath:oncall checkout is throwing 500s`,
      note: "rootpath init installs the plugin for you. Real actions go through the permission prompt, and a hook refuses any that was not dry-run first." },
    { key: "codex", title: "Codex",
      code: `<span class="c"># ~/.codex/config.toml</span>\n[mcp_servers.rootpath]\ncommand = <span class="s">"rootpath"</span>\nargs = [<span class="s">"mcp"</span>]`,
      note: "rootpath init writes this. The incident playbook is in AGENTS.md." },
    { key: "cursor", title: "Cursor",
      code: `{ <span class="s">"mcpServers"</span>: { <span class="s">"rootpath"</span>: { <span class="s">"command"</span>: <span class="s">"rootpath"</span>, <span class="s">"args"</span>: [<span class="s">"mcp"</span>] } } }`,
      note: "The same entry works in any MCP host. Over HTTP, use http://localhost:7100/mcp while rootpath start is running." },
    { key: "demo", title: "Demo",
      code: `rootpath demo up\nrootpath ui\nrootpath chaos flag\nrootpath watch --once`,
      note: "chaos breaks a service, watch fires the alert, and a headless agent fixes it. The console is at localhost:7100." },
  ];
}

export function renderInstall(): void {
  const snippet = byId("snippet");
  const list = byId("hosts");
  const show = (h: Host) => {
    for (const b of list.querySelectorAll<HTMLElement>(".host")) b.setAttribute("aria-selected", String(b.dataset.key === h.key));
    snippet.innerHTML = h.code;
    byId("snippet-note").textContent = h.note;
  };
  const all = hosts();
  for (const h of all) {
    const b = document.createElement("button");
    b.className = "host";
    b.setAttribute("role", "tab");
    b.dataset.key = h.key;
    b.textContent = h.title;
    b.onclick = () => show(h);
    list.appendChild(b);
  }
  show(all[0]);

  for (const button of document.querySelectorAll<HTMLButtonElement>("[data-copy]")) {
    const source = byId(button.dataset.copy ?? "");
    button.onclick = () => void copyText(button, source);
  }
}

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
