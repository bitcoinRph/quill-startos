// App shell, hash router, theme toggle.
import { api, onUnauthorized } from './api.js'
import { h, icon, clear, THEMES, currentTheme, applyTheme } from './util.js'
import { recorder } from './recorder.js'
import * as signin from './views/signin.js'
import * as library from './views/library.js'
import * as meeting from './views/meeting.js'
import * as newMeeting from './views/new.js'
import * as settings from './views/settings.js'
import { fmtClock } from './util.js'

const shell = document.getElementById('shell')
let cleanup = null
let navEls = {}
let main = null
let recPill = null

const ROUTES = { '': library, library, meeting, new: newMeeting, settings, signin }

export function parseHash() {
  const raw = location.hash.replace(/^#\/?/, '')
  const [path, qs = ''] = raw.split('?')
  const parts = path.split('/').filter(Boolean).map(decodeURIComponent)
  return { name: parts[0] || '', parts, query: Object.fromEntries(new URLSearchParams(qs)) }
}

function buildShell() {
  clear(shell)
  const themeBtn = h('button', { class: 'btn ghost icon-btn', type: 'button', id: 'theme-btn' })
  const syncTheme = () => {
    const t = currentTheme()
    themeBtn.replaceChildren(icon(t === 'dark' ? 'moon' : t === 'light' ? 'sun' : 'auto'))
    themeBtn.title = `Theme: ${t} (click to change)`
    themeBtn.setAttribute('aria-label', `Theme: ${t}. Click to change.`)
  }
  themeBtn.addEventListener('click', () => { applyTheme(THEMES[(THEMES.indexOf(currentTheme()) + 1) % 3]); syncTheme() })
  syncTheme()

  const link = (hash, key, label, ic) => (navEls[key] = h('a', { href: hash }, icon(ic), h('span', null, label)))
  recPill = h('a', { class: 'rec-pill', href: '#/new/recorder', hidden: true, 'aria-label': 'Recording in progress, open recorder' }, h('span', { class: 'dot pulse' }), h('span', { id: 'rec-time' }, '0:00'))
  const signout = h('button', { class: 'btn ghost icon-btn', type: 'button', title: 'Sign out', 'aria-label': 'Sign out', onclick: async () => {
    if (recorder.active && !confirm('A recording is running. Sign out anyway?')) return
    try { await api.post('/auth/logout', undefined, { quiet401: true }) } catch { /* cookie may already be gone */ }
    location.hash = '#/signin'
  } }, icon('logout'))

  main = h('main', { id: 'app', tabindex: '-1' })
  shell.append(
    h('header', { class: 'topbar', id: 'topbar' }, h('div', { class: 'wrap bar' },
      h('a', { class: 'brand', href: '#/' }, h('img', { src: 'favicon.svg', alt: '', width: 22, height: 22 }), 'Quill'),
      h('nav', { class: 'nav', 'aria-label': 'Main' },
        link('#/', 'library', 'Library', 'list'), link('#/new', 'new', 'New meeting', 'plus'), link('#/settings', 'settings', 'Settings', 'gear')),
      h('div', { class: 'bar-end' }, recPill, themeBtn, signout))),
    main)
}

function updateRecPill() {
  if (!recPill) return
  recPill.hidden = !recorder.active
  const t = document.getElementById('rec-time')
  if (t) t.textContent = fmtClock(recorder.elapsedMs())
}
recorder.addEventListener('change', updateRecPill)
setInterval(updateRecPill, 1000)

let redirecting = false
onUnauthorized(() => {
  if (location.hash.startsWith('#/signin') || redirecting) return
  redirecting = true
  try { sessionStorage.setItem('quill.returnTo', location.hash || '#/') } catch { /* private mode */ }
  location.hash = '#/signin'
  setTimeout(() => { redirecting = false }, 500)
})

const app = {
  setTitle(t) { document.title = t ? `${t} · Quill` : 'Quill' },
  go(hash) { location.hash = hash },
}

function route() {
  if (cleanup) { try { cleanup() } catch (e) { console.warn(e) } cleanup = null }
  const r = parseHash()
  const view = ROUTES[r.name] || library
  const isSignin = r.name === 'signin'
  if (isSignin) {
    clear(shell)
    main = h('main', { id: 'app', tabindex: '-1', style: 'padding:0' })
    shell.append(main)
    navEls = {}
  } else if (!document.getElementById('topbar')) {
    buildShell()
  }
  for (const [k, el] of Object.entries(navEls)) {
    if (k === (r.name || 'library')) el.setAttribute('aria-current', 'page'); else el.removeAttribute('aria-current')
  }
  clear(main)
  app.setTitle('')
  const holder = isSignin ? main : h('div', { class: 'wrap' })
  if (!isSignin) main.append(holder)
  cleanup = view.render(holder, r, app) || null
  window.scrollTo(0, 0)
  if (!isSignin) main.focus({ preventScroll: true })
  updateRecPill()
}

document.addEventListener('keydown', (e) => {
  if (e.key === '/' && !e.ctrlKey && !e.metaKey && !/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName) && !document.activeElement?.isContentEditable) {
    const q = document.getElementById('q')
    if (q) { e.preventDefault(); q.focus() }
  }
})

window.addEventListener('hashchange', route)
route()
