/** The install section: one MCP server, a snippet per agent host, and a copy button. */
import { byId, esc } from "../lib/html";

interface Host {
  key: string;
  title: string;
  sub: string;
  /** Highlighted snippet markup (pre.code spans). */
  code: string;
  note: string;
}

function hosts(repo: string): Host[] {
  const slug = esc(repo.replace(/^https:\/\/github\.com\//, "").replace(/\.git$/, ""));
  const site = esc(window.location.origin);
  return [
    { key: "cli", title: "Install", sub: "One command, then a short setup that connects your agents",
      code: `curl -fsSL ${site}/install.sh | sh     <span class="c"># the rootpath binary, into ~/.local/bin</span>\n\nrootpath init     <span class="c"># where your graph lives, a connection test, and your agents</span>\nrootpath start    <span class="c"># optional: background server with the console, ingest and MCP over HTTP</span>`,
      note: "rootpath init asks where your FalkorDB is (local Docker, FalkorDB Cloud, or a URL), tests the connection, saves it to ~/.config/rootpath/config.toml readable only by you, and installs Rootpath into Claude Code, Codex and Cursor. Nothing goes into chat, and no Rootpath server is involved." },
    { key: "claude", title: "Claude Code", sub: "Plugin: /map, /oncall, /blast-radius, /postmortem, safety hook",
      code: `<span class="c"># rootpath init does this for you; by hand:</span>\n/plugin marketplace add ${slug}\n/plugin install rootpath@rootpath\n\n/rootpath:map                                <span class="c"># Claude maps your system into the graph</span>\n/rootpath:oncall checkout is throwing 500s   <span class="c"># and investigates over it</span>`,
      note: "Read tools are pre-approved. Real actions go through Claude Code's permission prompt, and the guard hook refuses any action that was not dry-run first, or anything during a change freeze." },
    { key: "codex", title: "Codex CLI", sub: "stdio MCP server in config.toml, playbook in AGENTS.md",
      code: `<span class="c"># ~/.codex/config.toml (rootpath init writes this)</span>\n[mcp_servers.rootpath]\ncommand = <span class="s">"rootpath"</span>\nargs = [<span class="s">"mcp"</span>]`,
      note: "The same 24 tools. The repository's AGENTS.md carries the incident playbook for Codex and other agents that read it." },
    { key: "cursor", title: "Cursor, VS Code, any MCP host", sub: "stdio, or HTTP when rootpath start is running",
      code: `<span class="c"># stdio</span>\n{ <span class="s">"mcpServers"</span>: { <span class="s">"rootpath"</span>: { <span class="s">"command"</span>: <span class="s">"rootpath"</span>, <span class="s">"args"</span>: [<span class="s">"mcp"</span>] } } }\n\n<span class="c"># or over HTTP, with the background server running</span>\nclaude mcp add --transport http rootpath http://localhost:7100/mcp`,
      note: "Tools carry MCP annotations: read-only tools are marked readOnlyHint, the three remediation tools destructiveHint, so hosts can ask before acting." },
    { key: "headless", title: "Try the demo", sub: "seven services to break, and an agent that fixes them",
      code: `rootpath demo up        <span class="c"># FalkorDB, 7 services, traffic and history in Docker</span>\nrootpath ui             <span class="c"># console on localhost:7100</span>\n\nrootpath chaos flag     <span class="c"># break something</span>\nrootpath watch --once   <span class="c"># an alert starts Claude Code headless with only the Rootpath tools</span>`,
      note: "The watcher pages on the most user-facing failing service and launches Claude Code headless with no shell, file or web tools. --autonomy propose makes the server refuse real actions. From a clone of the repository, prefix the commands with uv run." },
  ];
}

export function renderInstall(repo: string): void {
  const link = byId<HTMLAnchorElement>("repo-link");
  link.href = repo;
  link.textContent = "GitHub";

  const snippet = byId("snippet");
  const list = byId("hosts");
  const show = (h: Host) => {
    for (const b of list.querySelectorAll<HTMLElement>(".host")) b.setAttribute("aria-selected", String(b.dataset.key === h.key));
    snippet.innerHTML = h.code;
    byId("snippet-note").textContent = h.note;
  };
  const all = hosts(repo);
  for (const h of all) {
    const b = document.createElement("button");
    b.className = "host";
    b.setAttribute("role", "tab");
    b.dataset.key = h.key;
    b.innerHTML = `<b>${h.title}</b><span>${h.sub}</span>`;
    b.onclick = () => show(h);
    list.appendChild(b);
  }
  show(all[0]);

  const copy = byId("copy");
  copy.onclick = async () => {
    try {
      await navigator.clipboard.writeText(snippet.textContent ?? "");
      copy.textContent = "Copied";
    } catch {
      const range = document.createRange();
      range.selectNodeContents(snippet);
      const selection = getSelection();
      selection?.removeAllRanges();
      selection?.addRange(range);
      copy.textContent = "Selected";
    }
    setTimeout(() => {
      copy.textContent = "Copy";
    }, 1600);
  };
}
