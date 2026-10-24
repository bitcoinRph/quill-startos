import { api, listOf } from '../api.js'
import { h, icon, clear, debounce, fmtDate, fmtDuration, fmtClock, notice } from '../util.js'

const saved = { q: '', status: '', source: '' } // survives navigation within the session
const SOURCES = { bot: 'Jitsi bot', browser: 'Browser', upload: 'Upload', import: 'Import', api: 'API' }
const PAGE = 25

// Search snippets may carry <mark> highlights; everything else is shown as text.
export function snippetNode(text) {
  const out = document.createDocumentFragment()
  const parts = String(text || '').split(/(<mark>|<\/mark>)/i)
  let inMark = false
  for (const p of parts) {
    if (/^<mark>$/i.test(p)) { inMark = true; continue }
    if (/^<\/mark>$/i.test(p)) { inMark = false; continue }
    if (!p) continue
    out.append(inMark ? h('mark', null, p) : document.createTextNode(p))
  }
  return out
}

export function statusBadge(status) {
  const label = { scheduled: 'Scheduled', live: 'Live', processing: 'Processing', ready: 'Ready', failed: 'Failed' }[status] || status || 'Unknown'
  return h('span', { class: `badge ${status || ''}` }, status === 'live' ? h('span', { class: 'dot pulse' }) : null, label)
}

export function render(root, route, app) {
  app.setTitle('Library')
  let alive = true
  let items = []
  let cursor = null
  let hasMore = false
  let loadingMore = false
  let timer = null

  const q = h('input', { class: 'input', id: 'q', type: 'search', value: saved.q, placeholder: 'Search meetings and transcripts', title: 'Press / to focus the search', 'aria-keyshortcuts': '/', autocomplete: 'off', 'aria-label': 'Search meetings and transcripts' })
  const status = h('select', { class: 'select', 'aria-label': 'Filter by status' },
    h('option', { value: '' }, 'All statuses'),
    ...['live', 'processing', 'ready', 'failed', 'scheduled'].map((s) => h('option', { value: s, selected: saved.status === s }, s[0].toUpperCase() + s.slice(1))))
  const source = h('select', { class: 'select', 'aria-label': 'Filter by source' },
    h('option', { value: '' }, 'All sources'),
    ...Object.entries(SOURCES).map(([k, v]) => h('option', { value: k, selected: saved.source === k }, v)))
  const hitsBox = h('div')
  const listBox = h('div', { 'aria-live': 'polite' })
  const moreBox = h('div', { class: 'row', style: 'justify-content:center;margin-top:12px' })

  const searchBox = h('div', { class: 'searchbox' }, icon('search'), q)
  root.append(
    h('div', { class: 'row between', style: 'margin-bottom:14px' }, h('h1', null, 'Library'),
      h('a', { class: 'btn primary', href: '#/new' }, icon('plus'), 'New meeting')),
    h('div', { class: 'toolbar', role: 'search' }, searchBox, status, source),
    hitsBox, listBox, moreBox)

  function row(m) {
    const when = fmtDate(m.started_at || m.created_at)
    const dur = m.status === 'live' ? 'In progress' : fmtDuration(m.duration_ms)
    return h('li', null, h('a', { class: 'mrow', href: `#/meeting/${encodeURIComponent(m.id)}` },
      h('span', { class: 'mtitle' }, m.title || 'Untitled meeting'),
      statusBadge(m.status),
      h('span', { class: 'mmeta' },
        h('span', null, SOURCES[m.source] || m.source || ''),
        when ? h('span', null, when) : null,
        dur ? h('span', null, dur) : null)))
  }

  function paintList(error) {
    clear(listBox)
    const filtering = saved.q || saved.status || saved.source
    if (error) { listBox.append(notice('error', error)); return }
    if (!items.length) {
      listBox.append(filtering
        ? h('div', { class: 'empty' }, h('h2', null, 'No meetings match'), h('p', null, 'Try a different search or clear the filters.'))
        : h('div', { class: 'empty' }, h('h2', null, 'No meetings yet'),
          h('p', null, 'Send the bot to a Jitsi call, record from this browser, or upload an audio file.'),
          h('div', { class: 'row' }, h('a', { class: 'btn primary', href: '#/new' }, icon('plus'), 'New meeting'))))
    } else {
      if (saved.q) listBox.append(h('div', { class: 'section-title' }, 'Meetings'))
      listBox.append(h('ul', { class: 'list' }, items.map(row)))
    }
    clear(moreBox)
    if (hasMore) {
      moreBox.append(h('button', { class: 'btn', type: 'button', disabled: loadingMore, onclick: () => load(false) }, loadingMore ? 'Loading' : 'Load more'))
    }
  }

  async function load(reset = true) {
    if (!reset) { loadingMore = true; paintList() }
    try {
      const res = await api.get('/meetings', {
        query: { q: saved.q, status: saved.status, source: saved.source, limit: PAGE, cursor: reset ? undefined : cursor },
      })
      if (!alive) return
      const got = listOf(res, 'meetings')
      items = reset ? got : items.concat(got)
      cursor = res && res.cursor
      hasMore = !!(res && res.has_more)
      loadingMore = false
      paintList()
      schedulePoll()
    } catch (e) {
      if (!alive || e.status === 401) return
      loadingMore = false
      paintList(e.message)
    }
  }

  async function loadHits() {
    clear(hitsBox)
    if (!saved.q || saved.q.length < 2) return
    try {
      const res = await api.get('/search', { query: { q: saved.q, limit: 15 } })
      if (!alive || saved.q !== q.value.trim()) return
      const hits = listOf(res, 'hits')
      clear(hitsBox)
      if (!hits.length) return
      hitsBox.append(h('div', { class: 'section-title' }, 'Transcript matches'),
        h('ul', { class: 'list' }, hits.map((x) => h('li', null,
          h('a', { class: 'hit', href: `#/meeting/${encodeURIComponent(x.meeting_id)}${x.seq !== undefined ? `?seq=${x.seq}` : ''}` },
            h('div', { class: 'mmeta row', style: 'gap:6px 10px' }, h('strong', { style: 'color:var(--text)' }, x.title || 'Meeting'),
              x.speaker ? h('span', null, x.speaker) : null, x.start_ms !== undefined ? h('span', { class: 'mono' }, fmtClock(x.start_ms)) : null),
            h('div', { class: 'snip' }, snippetNode(x.snippet)))))))
    } catch { /* hits are a bonus; the list still works */ }
  }

  function schedulePoll() {
    clearTimeout(timer)
    if (items.some((m) => ['live', 'processing', 'scheduled'].includes(m.status)) && !hasMore) {
      timer = setTimeout(() => { if (alive && !document.hidden) load(true); else schedulePoll() }, 8000)
    }
  }

  const apply = debounce(() => { saved.q = q.value.trim(); loadHits(); load(true) }, 300)
  q.addEventListener('input', apply)
  q.addEventListener('keydown', (e) => { if (e.key === 'Escape' && q.value) { q.value = ''; apply() } })
  status.addEventListener('change', () => { saved.status = status.value; load(true) })
  source.addEventListener('change', () => { saved.source = source.value; load(true) })

  load(true)
  loadHits()
  return () => { alive = false; clearTimeout(timer); apply.cancel() }
}
