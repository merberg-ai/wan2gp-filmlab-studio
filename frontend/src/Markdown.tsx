import { Fragment, type ReactNode } from "react";

/** A small Markdown renderer for the director's replies: headings, paragraphs, bold/italic/code, links,
 *  bullet and numbered lists, tables, quotes, code blocks and rules. It builds React elements only (no HTML is
 *  ever injected), so text from the model can't add markup or scripts. Anything it doesn't know stays as text. */

function inline(text: string, key = "i"): ReactNode[] {
  const out: ReactNode[] = [];
  // `code`, **bold**, __bold__, *italic*, _italic_, [label](https://…)
  const re = /(`[^`]+`)|(\*\*[^*]+\*\*|__[^_]+__)|(\*[^*\s][^*]*\*|_[^_\s][^_]*_)|(\[[^\]]+\]\((https?:\/\/[^\s)]+)\))/g;
  let last = 0, m: RegExpExecArray | null, n = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const k = `${key}-${n++}`;
    if (m[1]) out.push(<code key={k}>{m[1].slice(1, -1)}</code>);
    else if (m[2]) out.push(<strong key={k}>{inline(m[2].slice(2, -2), k)}</strong>);
    else if (m[3]) out.push(<em key={k}>{inline(m[3].slice(1, -1), k)}</em>);
    else if (m[4]) out.push(<a key={k} href={m[5]} target="_blank" rel="noreferrer noopener">{m[4].slice(1, m[4].indexOf("]"))}</a>);
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

const cells = (row: string) => row.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
const isRule = (row: string) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(row);

export function Markdown({ text }: { text: string }) {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const k = `b${i}`;
    if (!line.trim()) { i++; continue; }
    if (/^```/.test(line)) {  // fenced code
      const body: string[] = [];
      for (i++; i < lines.length && !/^```/.test(lines[i]); i++) body.push(lines[i]);
      i++;
      blocks.push(<pre key={k}><code>{body.join("\n")}</code></pre>);
      continue;
    }
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) {
      const level = Math.min(h[1].length + 2, 6);  // ### → h5: headings stay small inside a chat bubble
      const Tag = `h${level}` as "h3";
      blocks.push(<Tag key={k} className="md-h">{inline(h[2], k)}</Tag>);
      i++; continue;
    }
    if (/^\s*(?:-\s*){3,}$|^\s*(?:\*\s*){3,}$|^\s*(?:_\s*){3,}$/.test(line)) { blocks.push(<hr key={k} />); i++; continue; }
    if (line.includes("|") && i + 1 < lines.length && isRule(lines[i + 1])) {  // table
      const head = cells(line);
      const rows: string[][] = [];
      for (i += 2; i < lines.length && lines[i].includes("|") && lines[i].trim(); i++) rows.push(cells(lines[i]));
      blocks.push(
        <div key={k} className="md-table"><table>
          <thead><tr>{head.map((c, j) => <th key={j}>{inline(c, `${k}h${j}`)}</th>)}</tr></thead>
          <tbody>{rows.map((r, ri) => <tr key={ri}>{head.map((_, j) => <td key={j}>{inline(r[j] ?? "", `${k}${ri}-${j}`)}</td>)}</tr>)}</tbody>
        </table></div>);
      continue;
    }
    const list = line.match(/^\s*([-*+]|\d+[.)])\s+/);
    if (list) {
      const ordered = /\d/.test(list[1]);
      const items: string[] = [];
      while (i < lines.length) {
        const m = lines[i].match(/^\s*([-*+]|\d+[.)])\s+(.*)$/);
        if (m && /\d/.test(m[1]) === ordered) { items.push(m[2]); i++; }
        else if (items.length && /^\s{2,}\S/.test(lines[i])) { items[items.length - 1] += " " + lines[i].trim(); i++; }
        else break;
      }
      const L = ordered ? "ol" : "ul";
      blocks.push(<L key={k}>{items.map((t, j) => <li key={j}>{inline(t, `${k}${j}`)}</li>)}</L>);
      continue;
    }
    if (/^>\s?/.test(line)) {
      const q: string[] = [];
      for (; i < lines.length && /^>\s?/.test(lines[i]); i++) q.push(lines[i].replace(/^>\s?/, ""));
      blocks.push(<blockquote key={k}>{inline(q.join(" "), k)}</blockquote>);
      continue;
    }
    const para: string[] = [];  // a paragraph runs until a blank line or another block starts
    for (; i < lines.length && lines[i].trim() && !/^(#{1,6}\s|```|>|\s*([-*+]|\d+[.)])\s)/.test(lines[i])
           && !(lines[i].includes("|") && i + 1 < lines.length && isRule(lines[i + 1])); i++) para.push(lines[i]);
    if (!para.length) { para.push(line); i++; }
    blocks.push(<p key={k}>{para.map((l, j) => <Fragment key={j}>{j > 0 && <br />}{inline(l, `${k}${j}`)}</Fragment>)}</p>);
  }
  return <div className="md">{blocks}</div>;
}
