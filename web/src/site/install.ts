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
  return [
    { key: "claude", title: "Claude Code", sub: "Plugin: setup dialog, /map, /oncall, /blast-radius, /postmortem, safety hook",
      code: `<span class="c"># inside Claude Code</span>\n/plugin marketplace add ${slug}\n/plugin install rootpath@rootpath\n<span class="c"># the setup dialog asks for your FalkorDB URL and keeps it in the system keychain</span>\n\n/rootpath:map                                <span class="c"># Claude maps your system into the graph</span>\n/rootpath:oncall checkout is throwing 500s   <span class="c"># and investigates over it</span>`,
      note: "Bring your own FalkorDB: local Docker (redis://localhost:6380) or FalkorDB Cloud (rediss://user:password@host:port). The URL is entered once in Claude Code's plugin dialog, never in chat. Real actions go through the permission prompt, and the guard hook refuses any action that was not dry-run first." },
    { key: "codex", title: "Codex CLI", sub: "stdio MCP server in config.toml, playbook in AGENTS.md",
      code: `<span class="c"># ~/.codex/config.toml</span>\n[mcp_servers.oncall]\ncommand = <span class="s">"uv"</span>\nargs = [<span class="s">"run"</span>, <span class="s">"--quiet"</span>, <span class="s">"--directory"</span>, <span class="s">"/abs/path/to/rootpath"</span>, <span class="s">"rootpath"</span>, <span class="s">"mcp"</span>]\n\n[mcp_servers.oncall.env]\nFALKORDB_URL = <span class="s">"redis://localhost:6380"</span>`,
      note: "The same 24 tools. The repository's AGENTS.md carries the incident playbook for Codex and other agents that read it." },
    { key: "cursor", title: "Cursor, VS Code, any MCP host", sub: "one stdio command",
      code: `{\n  <span class="s">"mcpServers"</span>: {\n    <span class="s">"oncall"</span>: {\n      <span class="s">"command"</span>: <span class="s">"uv"</span>,\n      <span class="s">"args"</span>: [<span class="s">"run"</span>, <span class="s">"--directory"</span>, <span class="s">"/abs/path/to/rootpath"</span>, <span class="s">"rootpath"</span>, <span class="s">"mcp"</span>]\n    }\n  }\n}`,
      note: "Tools carry MCP annotations: read-only tools are marked readOnlyHint, the three remediation tools destructiveHint, so hosts can ask before acting." },
    { key: "headless", title: "Headless responder", sub: "an alert starts the agent with no human present",
      code: `git clone ${esc(repo)} rootpath && cd rootpath\nuv sync\nuv run rootpath demo up      <span class="c"># FalkorDB, 7 services, traffic, history</span>\nuv run rootpath ui           <span class="c"># dashboard on localhost:7100</span>\n\nuv run rootpath chaos flag   <span class="c"># break something</span>\nuv run rootpath watch        <span class="c"># alert -> claude -p with only the oncall tools</span>`,
      note: "The watcher pages on the most user-facing failing service, launches Claude Code headless with no shell, file or web tools, and stores the run on an Investigation node. --autonomy propose keeps it to dry runs." },
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
