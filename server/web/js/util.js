// Small DOM and formatting helpers shared by every view.

export function h(tag, attrs, ...kids) {
  const el = document.createElement(tag)
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v === undefined || v === null || v === false) continue
      if (k === 'class') el.className = v
      else if (k === 'dataset') Object.assign(el.dataset, v)
      else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v)
      else if (k in el && !k.includes('-') && k !== 'list') {
        try { el[k] = v } catch { el.setAttribute(k, v === true ? '' : v) }
      } else el.setAttribute(k, v === true ? '' : v)
    }
  }
  append(el, kids)
  return el
}

export function append(el, kids) {
  for (const k of kids.flat(Infinity)) {
    if (k === null || k === undefined || k === false) continue
    el.append(k instanceof Node ? k : document.createTextNode(String(k)))
  }
  return el
}

export function fill(el, ...kids) { clear(el); return append(el, kids) }

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild)
  return el
}

const ICONS = {
  list: '<svg viewBox="0 0 24 24"><path d="M8 6h13M8 12h13M8 18h13M3.5 6h.01M3.5 12h.01M3.5 18h.01"/></svg>',
  plus: '<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>',
  gear: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg>',
  search: '<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></svg>',
  sun: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
  moon: '<svg viewBox="0 0 24 24"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>',
  auto: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 3v18" /><path d="M12 3a9 9 0 0 1 0 18z" fill="currentColor"/></svg>',
  logout: '<svg viewBox="0 0 24 24"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/></svg>',
  info: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/></svg>',
  warn: '<svg viewBox="0 0 24 24"><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0zM12 9v4M12 17h.01"/></svg>',
  mic: '<svg viewBox="0 0 24 24"><rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v4"/></svg>',
  stop: '<svg viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor"/></svg>',
  copy: '<svg viewBox="0 0 24 24"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>',
  chevron: '<svg viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></svg>',
}
export function icon(name) {
  const s = document.createElement('span')
  s.className = 'ic'
  s.setAttribute('aria-hidden', 'true')
  s.innerHTML = ICONS[name] || '' // static strings above, never user data
  return s
}

export function debounce(fn, ms) {
  let t = null
  let lastArgs = []
  const d = (...args) => { lastArgs = args; clearTimeout(t); t = setTimeout(() => { t = null; fn(...lastArgs) }, ms) }
  d.flush = () => { if (t) { clearTimeout(t); t = null; fn(...lastArgs) } }
  d.cancel = () => { clearTimeout(t); t = null }
  d.pending = () => t !== null
  return d
}

export function toMs(v) {
  if (v === null || v === undefined || v === '') return null
  if (typeof v === 'number') return v < 1e11 ? v * 1000 : v
  const t = Date.parse(v)
  return Number.isNaN(t) ? null : t
}

export function fmtDate(v) {
  const ms = toMs(v)
  if (ms === null) return ''
  const d = new Date(ms)
  const now = new Date()
  const time = d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
  const y = new Date(now); y.setDate(y.getDate() - 1)
  if (d.toDateString() === now.toDateString()) return `Today, ${time}`
  if (d.toDateString() === y.toDateString()) return `Yesterday, ${time}`
  const opts = d.getFullYear() === now.getFullYear() ? { month: 'short', day: 'numeric' } : { year: 'numeric', month: 'short', day: 'numeric' }
  return `${d.toLocaleDateString([], opts)}, ${time}`
}

export function fmtDuration(ms) {
  if (!ms || ms < 0) return ''
  const s = Math.round(ms / 1000)
  if (s < 60) return `${s} s`
  const m = Math.round(s / 60)
  if (m < 60) return `${m} min`
  return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, '0')} min`
}

export function fmtClock(ms) {
  const s = Math.max(0, Math.floor((ms || 0) / 1000))
  const hh = Math.floor(s / 3600)
  const mm = Math.floor((s % 3600) / 60)
  const ss = s % 60
  return hh ? `${hh}:${String(mm).padStart(2, '0')}:${String(ss).padStart(2, '0')}` : `${mm}:${String(ss).padStart(2, '0')}`
}

export function fmtBytes(n) {
  if (!Number.isFinite(n)) return ''
  const u = ['B', 'KB', 'MB', 'GB']
  let i = 0
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++ }
  return `${n.toFixed(i ? 1 : 0)} ${u[i]}`
}

export function slug(s) {
  return (s || 'meeting').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 60) || 'meeting'
}

export function toast(msg, kind = 'info', ms = 5000) {
  const host = document.getElementById('toasts')
  if (!host) return
  const el = h('div', { class: `toast ${kind}` }, msg)
  host.append(el)
  setTimeout(() => el.remove(), kind === 'error' ? Math.max(ms, 7000) : ms)
}

export async function copyText(text, what = 'Copied') {
  try {
    await navigator.clipboard.writeText(text)
  } catch {
    const ta = h('textarea', { value: text, 'aria-hidden': 'true', style: 'position:fixed;opacity:0;top:0' })
    document.body.append(ta)
    ta.select()
    try { document.execCommand('copy') } catch { /* nothing else to try */ }
    ta.remove()
  }
  toast(`${what} to clipboard`, 'ok', 2200)
}

export function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob)
  const a = h('a', { href: url, download: filename })
  document.body.append(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 5000)
}

export function confirmDialog({ title, body, confirm = 'Confirm', danger = false }) {
  return new Promise((resolve) => {
    const dlg = h('dialog', { 'aria-labelledby': 'dlg-title' },
      h('form', { method: 'dialog' },
        h('h2', { id: 'dlg-title' }, title),
        h('p', { class: 'muted' }, body),
        h('div', { class: 'dlg-actions' },
          h('button', { class: 'btn', value: 'cancel', type: 'submit', autofocus: true }, 'Cancel'),
          h('button', { class: `btn ${danger ? 'danger solid' : 'primary'}`, value: 'ok', type: 'submit' }, confirm))))
    dlg.addEventListener('close', () => { resolve(dlg.returnValue === 'ok'); dlg.remove() })
    document.body.append(dlg)
    dlg.showModal()
  })
}

// A disclosure menu built on <details>: keyboard accessible, closes on Escape or outside click.
export function menu(label, items, { align = 'right', cls = 'btn' } = {}) {
  const summary = h('summary', { class: cls, 'aria-haspopup': 'true' }, label, icon('chevron'))
  const list = h('div', { class: 'menu-list', role: 'group' })
  const root = h('details', { class: 'menu' }, summary, list)
  if (align === 'left') list.style.right = 'auto'
  for (const it of items) {
    if (it === '-') { list.append(h('hr')); continue }
    list.append(h('button', { type: 'button', class: it.danger ? 'danger' : '', onclick: () => { root.open = false; it.onclick() } }, it.label))
  }
  const away = (e) => { if (!root.contains(e.target)) root.open = false }
  document.addEventListener('click', away)
  root.addEventListener('keydown', (e) => { if (e.key === 'Escape' && root.open) { root.open = false; summary.focus() } })
  root.addEventListener('toggle', () => { if (!root.isConnected) document.removeEventListener('click', away) })
  return root
}

export function notice(kind, ...kids) {
  return h('div', { class: `notice ${kind === 'info' ? '' : kind}`, role: kind === 'error' ? 'alert' : null },
    icon(kind === 'info' || kind === 'ok' ? 'info' : 'warn'), h('div', { class: 'grow' }, kids))
}

export function field(label, control, hint) {
  const id = control.id || `f${Math.random().toString(36).slice(2, 8)}`
  control.id = id
  return h('div', { class: 'field' }, h('label', { for: id }, label), control, hint ? h('div', { class: 'hint' }, hint) : null)
}

export function busy(btn, on, text) {
  if (on) { btn.dataset.label = btn.textContent; btn.disabled = true; btn.replaceChildren(h('span', { class: 'spinner' }), ' ', text || btn.dataset.label) }
  else { btn.disabled = false; btn.textContent = btn.dataset.label || btn.textContent }
}

export function getPref(key, fallback = '') {
  try { return localStorage.getItem(`quill.${key}`) ?? fallback } catch { return fallback }
}
export function setPref(key, value) {
  try { localStorage.setItem(`quill.${key}`, value) } catch { /* storage blocked */ }
}

// Accessible tab bar (roving tabindex, arrow keys). Panels are managed by the caller.
export function tabList({ label, tabs, selected, onSelect, cls = '' }) {
  const el = h('div', { class: `tabs ${cls}`, role: 'tablist', 'aria-label': label })
  const buttons = new Map()
  function mark(id) {
    for (const [k, b] of buttons) { b.setAttribute('aria-selected', String(k === id)); b.tabIndex = k === id ? 0 : -1 }
  }
  for (const t of tabs) {
    const b = h('button', { class: `tab ${t.cls || ''}`, type: 'button', role: 'tab', id: `tab-${label.replace(/\W+/g, '')}-${t.id}`,
      onclick: () => { mark(t.id); onSelect(t.id) } }, t.label)
    buttons.set(t.id, b)
    el.append(b)
  }
  el.addEventListener('keydown', (e) => {
    const vis = [...buttons.entries()].filter(([, b]) => b.offsetParent !== null)
    const i = vis.findIndex(([, b]) => b === document.activeElement)
    if (i < 0) return
    let n = null
    if (e.key === 'ArrowRight') n = (i + 1) % vis.length
    else if (e.key === 'ArrowLeft') n = (i - 1 + vis.length) % vis.length
    else if (e.key === 'Home') n = 0
    else if (e.key === 'End') n = vis.length - 1
    if (n === null) return
    e.preventDefault()
    const [id, b] = vis[n]
    b.focus(); mark(id); onSelect(id)
  })
  mark(selected)
  return { el, mark }
}

export const THEMES = ['system', 'light', 'dark']
export function currentTheme() { const t = getPref('theme', 'system'); return THEMES.includes(t) ? t : 'system' }
export function applyTheme(t) {
  if (t === 'system') document.documentElement.removeAttribute('data-theme')
  else document.documentElement.setAttribute('data-theme', t)
  setPref('theme', t)
}
