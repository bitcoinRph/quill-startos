// Transcript list used by the meeting view and the live recorder.
import { h, clear, fmtClock } from './util.js'

export function createTranscript({ live = false, onSeek = null, nameOf = (s) => s.speaker || 'Speaker', emptyText: initialEmpty = 'No transcript yet.' } = {}) {
  let emptyText = initialEmpty
  const el = h('div', {
    class: 'tr',
    role: live ? 'log' : 'group',
    'aria-label': 'Transcript',
    'aria-live': live ? 'polite' : null,
    'aria-relevant': live ? 'additions' : null,
  })
  const colors = new Map()
  const rows = new Map() // seq -> element
  let lastSpeaker = null
  let seekEnabled = !!onSeek
  let emptyEl = null

  const colorOf = (sp) => {
    if (!colors.has(sp)) colors.set(sp, colors.size % 8)
    return colors.get(sp)
  }

  function fill(row, seg) {
    clear(row)
    const first = row.classList.contains('first')
    const t = fmtClock(seg.start_ms)
    const ts = seekEnabled
      ? h('button', { class: 'ts', type: 'button', 'aria-label': `Play from ${t}`, onclick: () => onSeek(seg) }, t)
      : h('span', { class: 'ts', style: 'padding:3px 0' }, t)
    const body = h('div', null)
    if (first) body.append(h('span', { class: 'spk', style: `--c: var(--sp${colorOf(seg.speaker)})` }, nameOf(seg)))
    body.append(h('span', { class: 'txt' }, seg.text || ''))
    row.append(ts, body)
  }

  function removeEmpty() { if (emptyEl) { emptyEl.remove(); emptyEl = null } }

  function add(seg) {
    removeEmpty()
    if (seg.seq !== undefined && rows.has(seg.seq)) {
      const old = rows.get(seg.seq)
      old._seg = seg
      fill(old, seg)
      return old
    }
    const first = seg.speaker !== lastSpeaker
    lastSpeaker = seg.speaker
    const row = h('div', { class: `seg${first ? ' first' : ''}`, dataset: { seq: seg.seq ?? '' } })
    row._seg = seg
    fill(row, seg)
    el.append(row)
    if (seg.seq !== undefined) rows.set(seg.seq, row)
    return row
  }

  function showEmpty() {
    clear(el)
    emptyEl = h('div', { class: 'tr-empty' }, emptyText)
    el.append(emptyEl)
  }

  function setAll(list) {
    clear(el); rows.clear(); colors.clear(); lastSpeaker = null; emptyEl = null
    if (!list.length) return showEmpty()
    for (const seg of list) add(seg)
  }

  return {
    el,
    add,
    setAll,
    setEmptyText(t) { emptyText = t; if (emptyEl) emptyEl.textContent = t },
    showEmpty,
    setSeekEnabled(on) { seekEnabled = !!onSeek && on },
    rowFor: (seq) => rows.get(seq),
    setActive(seq, scroll = false) {
      el.querySelectorAll('.seg.active').forEach((n) => n.classList.remove('active'))
      const row = rows.get(seq)
      if (row) { row.classList.add('active'); if (scroll) row.scrollIntoView({ block: 'nearest' }) }
    },
    flash(seq) {
      const row = rows.get(seq)
      if (!row) return
      row.scrollIntoView({ block: 'center' })
      row.classList.add('flash')
      setTimeout(() => row.classList.remove('flash'), 2400)
    },
    get size() { return rows.size },
  }
}
