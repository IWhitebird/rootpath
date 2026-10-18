import { esc } from "./html";

type ListKind = "ul" | "ol";

/** Render the Markdown subset agent reports use: headings, lists, fenced code, inline code,
 * bold, italics, and table rows (kept as monospaced lines). */
export function markdown(src: string): string {
  let html = "";
  let list: ListKind | null = null;
  let code: string | null = null;
  const close = () => {
    if (list) html += `</${list}>`;
    list = null;
  };
  const item = (kind: ListKind, text: string) => {
    if (list !== kind) {
      close();
      html += `<${kind}>`;
      list = kind;
    }
    html += `<li>${inline(text)}</li>`;
  };
  for (const line of src.split("\n")) {
    if (line.startsWith("```")) {
      if (code === null) {
        close();
        code = "";
      } else {
        html += `<pre><code>${esc(code)}</code></pre>`;
        code = null;
      }
      continue;
    }
    if (code !== null) {
      code += line + "\n";
      continue;
    }
    let m: RegExpMatchArray | null;
    if ((m = line.match(/^#{1,4}\s+(.*)/))) {
      close();
      html += `<h3>${inline(m[1])}</h3>`;
    } else if ((m = line.match(/^\s*[-*]\s+(.*)/))) item("ul", m[1]);
    else if ((m = line.match(/^\s*\d+\.\s+(.*)/))) item("ol", m[1]);
    else if (line.trim() === "") close();
    else if (line.startsWith("|")) {
      close();
      html += `<p class="mono" style="font-size:.78rem;white-space:pre;overflow-x:auto">${esc(line)}</p>`;
    } else {
      close();
      html += `<p>${inline(line)}</p>`;
    }
  }
  close();
  return html;
}

function inline(text: string): string {
  return esc(text)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/(^|\W)\*([^*\s][^*]*)\*(?=\W|$)/g, "$1<i>$2</i>");
}
