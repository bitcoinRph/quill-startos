// A tiny, safe markdown renderer. All text is HTML-escaped first; only a fixed set of
// tags is ever produced, and links must be http(s).

const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;')

function emphasis(s) {
  return s
    .replace(/\*\*([^*\n]+?)\*\*/g, '<strong>$1</strong>')
    .replace(/__([^_\n]+?)__/g, '<strong>$1</strong>')
    .replace(/(^|[^\w*])\*([^*\s][^*\n]*?)\*(?!\w)/g, '$1<em>$2</em>')
    .replace(/(^|[^\w])_([^_\s][^_\n]*?)_(?!\w)/g, '$1<em>$2</em>')
    .replace(/~~([^~\n]+?)~~/g, '<del>$1</del>')
}

export function inline(src) {
  const stash = []
  const keep = (html) => { stash.push(html); return `\u0001${stash.length - 1}\u0001` }
  let s = esc(src.replace(/[\u0000\u0001]/g, ''))
  s = s.replace(/`([^`\n]+)`/g, (_, c) => keep(`<code>${c}</code>`))
  s = s.replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, (_, t, u) =>
    keep(`<a href="${u}" target="_blank" rel="noopener noreferrer">${emphasis(t)}</a>`))
  s = emphasis(s)
  return s.replace(/\u0001(\d+)\u0001/g, (_, i) => stash[Number(i)])
}

function renderList(items, st) {
  const base = items[st.i].indent
  const tag = items[st.i].ordered ? 'ol' : 'ul'
  let out = `<${tag}>`
  while (st.i < items.length && items[st.i].indent >= base) {
    const it = items[st.i++]
    const task = /^\[( |x|X)\]\s+(.*)$/.exec(it.text)
    let li = task
      ? `<li class="task"><input type="checkbox" disabled${task[1] !== ' ' ? ' checked' : ''} aria-label="${task[1] !== ' ' ? 'Done' : 'Not done'}">${inline(task[2])}`
      : `<li>${inline(it.text)}`
    if (st.i < items.length && items[st.i].indent > it.indent) li += renderList(items, st)
    out += `${li}</li>`
  }
  return `${out}</${tag}>`
}

const LIST_RE = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/

export function renderMarkdown(text) {
  const lines = String(text || '').replace(/\r\n?/g, '\n').split('\n')
  const out = []
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    if (!line.trim()) { i++; continue }
    const fence = /^\s*```/.exec(line)
    if (fence) {
      const buf = []
      i++
      while (i < lines.length && !/^\s*```/.test(lines[i])) buf.push(lines[i++])
      i++
      out.push(`<pre><code>${esc(buf.join('\n'))}</code></pre>`)
      continue
    }
    const head = /^(#{1,6})\s+(.*?)\s*#*\s*$/.exec(line)
    if (head) {
      const n = head[1].length
      out.push(`<h${Math.min(6, n + 2)}>${inline(head[2])}</h${Math.min(6, n + 2)}>`)
      i++
      continue
    }
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) { out.push('<hr>'); i++; continue }
    if (/^\s*>/.test(line)) {
      const buf = []
      while (i < lines.length && /^\s*>/.test(lines[i])) buf.push(lines[i++].replace(/^\s*>\s?/, ''))
      out.push(`<blockquote>${renderMarkdown(buf.join('\n'))}</blockquote>`)
      continue
    }
    if (LIST_RE.test(line)) {
      const items = []
      while (i < lines.length && lines[i].trim()) {
        const m = LIST_RE.exec(lines[i])
        if (m) items.push({ indent: m[1].replace(/\t/g, '    ').length, ordered: /\d/.test(m[2]), text: m[3] })
        else if (/^\s+\S/.test(lines[i]) && items.length) items[items.length - 1].text += ` ${lines[i].trim()}`
        else break
        i++
      }
      out.push(renderList(items, { i: 0 }))
      continue
    }
    const buf = []
    while (i < lines.length && lines[i].trim() && !/^(#{1,6}\s|\s*```|\s*>|\s*([-*_])(\s*\2){2,}\s*$)/.test(lines[i]) && !LIST_RE.test(lines[i])) buf.push(lines[i++].trim())
    if (!buf.length) { buf.push(lines[i++]) }
    out.push(`<p>${inline(buf.join(' '))}</p>`)
  }
  return out.join('\n')
}

export function mdElement(text, cls = 'md') {
  const el = document.createElement('div')
  el.className = cls
  el.innerHTML = renderMarkdown(text) // safe: escaped by renderMarkdown
  return el
}
