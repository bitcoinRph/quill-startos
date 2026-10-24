import { api, listOf } from '../api.js'
import { h, icon, clear, fmtDate, toast, copyText, confirmDialog, notice, field, busy, tabList, getPref, setPref, currentTheme, applyTheme } from '../util.js'
import { statusBadge } from './library.js'

const TABS = [
  { id: 'general', label: 'General' }, { id: 'agents', label: 'Connect agents' }, { id: 'keys', label: 'API keys' },
  { id: 'webhooks', label: 'Webhooks' }, { id: 'templates', label: 'Templates' }, { id: 'status', label: 'Status' },
]
const EVENTS = ['meeting.started', 'meeting.ended', 'transcript.ready', 'notes.ready']
let rememberedKey = '' // a key created in this page session, only used to fill snippets

export function render(root, route, app) {
  app.setTitle('Settings')
  const tab = TABS.some((t) => t.id === route.parts[1]) ? route.parts[1] : 'general'
  const panel = h('div', { role: 'tabpanel', 'aria-label': TABS.find((t) => t.id === tab).label })
  const tabs = tabList({ label: 'Settings sections', tabs: TABS, selected: tab, onSelect: (id) => { location.hash = `#/settings/${id}` } })
  root.append(h('h1', { style: 'margin-bottom:14px' }, 'Settings'), tabs.el, panel)
  const disposeRef = { fn: null }
  const view = { general, agents, keys, webhooks, templates, status }[tab]
  disposeRef.fn = view(panel)
  return () => disposeRef.fn && disposeRef.fn()
}

function errBox() { return h('div', { 'aria-live': 'assertive' }) }
function showErr(box, e) { clear(box).append(notice('error', e.message || String(e))) }
function loading() { return h('p', { class: 'muted' }, h('span', { class: 'spinner' }), ' Loading…') }

// ---------------------------------------------------------------- General
function general(panel) {
  const me = h('input', { class: 'input', value: getPref('me', 'Me'), maxlength: 60, style: 'max-width:320px' })
  me.addEventListener('change', () => { setPref('me', me.value.trim() || 'Me'); toast('Saved', 'ok', 1500) })
  const theme = h('select', { class: 'select', 'aria-label': 'Theme' }, ['system', 'light', 'dark'].map((t) => h('option', { value: t, selected: currentTheme() === t }, t[0].toUpperCase() + t.slice(1))))
  theme.addEventListener('change', () => applyTheme(theme.value))
  panel.append(h('div', { class: 'card' }, h('h2', null, 'This browser'),
    field('Your name in recordings', me, 'Used as the label of your microphone track when you record from this browser. Stored only in this browser.'),
    field('Theme', theme)))
  return null
}

// ---------------------------------------------------------------- Agents
function agents(panel) {
  const origin = location.origin
  const keyIn = h('input', { class: 'input', type: 'password', autocomplete: 'off', placeholder: 'qk_…  (optional)', value: rememberedKey, style: 'max-width:420px' })
  const snippets = h('div', { class: 'stack' })
  const snippet = (label, text) => h('div', null, h('div', { class: 'label', style: 'margin-bottom:6px' }, label),
    h('div', { class: 'snippet' }, h('pre', { tabindex: '0' }, text), h('button', { class: 'btn sm', type: 'button', onclick: () => copyText(text, 'Snippet copied') }, icon('copy'), 'Copy')))
  function paint() {
    const key = keyIn.value.trim() || '<your-api-key>'
    const cmd = `claude mcp add --transport http quill ${origin}/mcp --header "Authorization: Bearer ${key}"`
    const json = JSON.stringify({ mcpServers: { quill: { type: 'http', url: `${origin}/mcp`, headers: { Authorization: `Bearer ${key}` } } } }, null, 2)
    clear(snippets).append(snippet('Claude Code', cmd), snippet('Any MCP client (JSON config)', json))
  }
  keyIn.addEventListener('input', paint)
  paint()
  panel.append(h('div', { class: 'card' },
    h('h2', null, 'Connect your agents'),
    h('p', { class: 'muted' }, 'Quill speaks MCP over HTTP at ', h('code', null, `${origin}/mcp`), '. Agents such as Claude Code, Codex, Hermes Agent and OpenClaw can then list meetings, read transcripts live, send the bot to a call and save the notes they write.'),
    h('ol', { style: 'padding-left:1.2em' }, h('li', null, 'Create an API key under ', h('a', { href: '#/settings/keys' }, 'API keys'), '.'),
      h('li', null, 'Paste it below to fill in the snippets (it stays in this page only), or replace ', h('code', null, '<your-api-key>'), ' yourself.'),
      h('li', null, 'Run the command or add the JSON to your agent\'s MCP configuration.')),
    field('API key', keyIn, 'Only used to fill in the snippets on this page. It is never sent anywhere or stored.'),
    snippets,
    notice('info', 'The agent must be able to reach this address. If it runs on another machine, use the same address you open this page with (HTTPS or your .local address).')))
  return null
}

// ---------------------------------------------------------------- API keys
function keys(panel) {
  let alive = true
  const box = h('div'), err = errBox(), created = h('div', { 'aria-live': 'polite' })
  const name = h('input', { class: 'input', placeholder: 'e.g. Claude Code on laptop', maxlength: 80, required: true })
  const btn = h('button', { class: 'btn primary', type: 'submit' }, 'Create key')
  const form = h('form', { class: 'row', style: 'align-items:flex-end;margin-bottom:12px', onsubmit: async (e) => {
    e.preventDefault()
    busy(btn, true, 'Creating')
    try {
      const k = await api.post('/keys', { name: name.value.trim() })
      const secret = k.key || k.secret || k.token || k.api_key
      rememberedKey = secret || ''
      name.value = ''
      clear(created).append(h('div', { class: 'notice ok', style: 'display:block' },
        h('strong', null, 'Copy your new key now. It is shown only once.'),
        h('div', { class: 'secret', style: 'margin-top:8px' }, h('code', null, secret || '(the server did not return the secret)'),
          h('button', { class: 'btn sm', type: 'button', onclick: () => copyText(secret, 'Key copied') }, icon('copy'), 'Copy')),
        h('p', { class: 'small', style: 'margin:8px 0 0' }, h('a', { href: '#/settings/agents' }, 'Use it to connect an agent'))))
      load()
    } catch (ex) { showErr(err, ex) } finally { busy(btn, false) }
  } }, h('div', { class: 'field', style: 'margin:0;flex:1;min-width:220px' }, h('label', { for: 'key-name' }, 'New API key name'), Object.assign(name, { id: 'key-name' })), btn)

  async function load() {
    try {
      const list = listOf(await api.get('/keys'), 'keys')
      if (!alive) return
      clear(box).append(list.length ? h('div', { class: 'tablewrap' }, h('table', { class: 't' },
        h('thead', null, h('tr', null, ['Name', 'Key', 'Created', 'Last used', ''].map((x) => h('th', { scope: 'col' }, x)))),
        h('tbody', null, list.map((k) => h('tr', null,
          h('td', null, k.name, k.revoked_at ? h('span', { class: 'badge failed', style: 'margin-left:6px' }, 'revoked') : null),
          h('td', { class: 'mono' }, k.prefix ? `${k.prefix}…` : ''),
          h('td', null, fmtDate(k.created_at)), h('td', null, k.last_used_at ? fmtDate(k.last_used_at) : 'never'),
          h('td', null, k.revoked_at ? null : h('button', { class: 'btn danger sm', type: 'button', 'aria-label': `Revoke key ${k.name}`, onclick: async () => {
            if (!await confirmDialog({ title: 'Revoke this key?', body: `Anything using "${k.name}" will stop working immediately.`, confirm: 'Revoke', danger: true })) return
            try { await api.del(`/keys/${encodeURIComponent(k.id)}`); toast('Key revoked', 'ok', 2000); load() } catch (ex) { showErr(err, ex) }
          } }, 'Revoke'))))))) : h('p', { class: 'muted' }, 'No API keys yet.'))
    } catch (ex) { if (alive) showErr(err, ex) }
  }
  panel.append(h('div', { class: 'card' }, h('h2', null, 'API keys'),
    h('p', { class: 'muted' }, 'Keys let agents and scripts call the REST API and MCP. Treat them like passwords.'), form, err, created),
    h('div', { class: 'card' }, box))
  box.append(loading())
  load()
  return () => { alive = false }
}

// ---------------------------------------------------------------- Webhooks
function webhooks(panel) {
  let alive = true
  const box = h('div'), err = errBox(), created = h('div', { 'aria-live': 'polite' })
  const url = h('input', { class: 'input', type: 'url', required: true, placeholder: 'https://example.com/hooks/quill' })
  const secret = h('input', { class: 'input', autocomplete: 'off', placeholder: 'Leave empty to let Quill generate one' })
  const boxes = EVENTS.map((ev) => [ev, h('input', { type: 'checkbox', id: `ev-${ev}`, checked: true })])
  const btn = h('button', { class: 'btn primary', type: 'submit' }, 'Add webhook')
  const form = h('form', { onsubmit: async (e) => {
    e.preventDefault()
    clear(err)
    const events = boxes.filter(([, b]) => b.checked).map(([ev]) => ev)
    if (!events.length) return showErr(err, new Error('Choose at least one event.'))
    busy(btn, true, 'Adding')
    try {
      const body = { url: url.value.trim(), events, active: true }
      if (secret.value.trim()) body.secret = secret.value.trim()
      const w = await api.post('/webhooks', body)
      url.value = ''; secret.value = ''
      clear(created)
      if (w && w.secret) created.append(h('div', { class: 'notice ok', style: 'display:block' }, h('strong', null, 'Signing secret (shown once):'),
        h('div', { class: 'secret', style: 'margin-top:8px' }, h('code', null, w.secret), h('button', { class: 'btn sm', type: 'button', onclick: () => copyText(w.secret, 'Secret copied') }, icon('copy'), 'Copy'))))
      load()
    } catch (ex) { showErr(err, ex) } finally { busy(btn, false) }
  } },
  field('Endpoint URL', url), field('Signing secret', secret, 'Deliveries carry a Quill-Signature header (HMAC-SHA256 of "<timestamp>.<body>").'),
  h('fieldset', { style: 'border:0;padding:0;margin:0 0 12px' }, h('legend', { class: 'label', style: 'padding:0;margin-bottom:6px' }, 'Events'),
    h('div', { class: 'events-grid' }, boxes.map(([ev, b]) => h('div', { class: 'check' }, b, h('label', { for: `ev-${ev}`, class: 'mono small' }, ev))))),
  btn)

  async function load() {
    try {
      const list = listOf(await api.get('/webhooks'), 'webhooks')
      if (!alive) return
      clear(box).append(list.length ? h('div', { class: 'tablewrap' }, h('table', { class: 't' },
        h('thead', null, h('tr', null, ['URL', 'Events', ''].map((x) => h('th', { scope: 'col' }, x)))),
        h('tbody', null, list.map((w) => h('tr', null,
          h('td', { class: 'mono small' }, w.url),
          h('td', null, h('div', { class: 'chips' }, (w.events || []).map((ev) => h('span', { class: 'chip' }, ev)))),
          h('td', null, h('button', { class: 'btn danger sm', type: 'button', 'aria-label': `Delete webhook ${w.url}`, onclick: async () => {
            if (!await confirmDialog({ title: 'Delete this webhook?', body: w.url, confirm: 'Delete', danger: true })) return
            try { await api.del(`/webhooks/${encodeURIComponent(w.id)}`); toast('Webhook deleted', 'ok', 2000); load() } catch (ex) { showErr(err, ex) }
          } }, 'Delete'))))))) : h('p', { class: 'muted' }, 'No webhooks yet.'))
    } catch (ex) { if (alive) showErr(err, ex) }
  }
  panel.append(h('div', { class: 'card' }, h('h2', null, 'Add a webhook'), form, err, created),
    h('div', { class: 'card' }, h('h2', null, 'Webhooks'), box))
  box.append(loading())
  load()
  return () => { alive = false }
}

// ---------------------------------------------------------------- Templates
function templates(panel) {
  let alive = true
  let editing = null
  const box = h('div'), err = errBox(), formBox = h('div')

  function paintForm() {
    clear(formBox)
    const name = h('input', { class: 'input', required: true, maxlength: 80, value: editing ? editing.name : '' })
    const prompt = h('textarea', { class: 'textarea', required: true, rows: 7, value: editing ? editing.prompt : '', placeholder: 'Instructions for the model, e.g. "Write concise notes with: Summary, Decisions, Action items (owner, due date)."' })
    const save = h('button', { class: 'btn primary', type: 'submit' }, editing ? 'Save template' : 'Add template')
    formBox.append(h('form', { onsubmit: async (e) => {
      e.preventDefault()
      busy(save, true, 'Saving')
      try {
        const body = { name: name.value.trim(), prompt: prompt.value }
        if (editing) await api.put(`/templates/${encodeURIComponent(editing.id)}`, body)
        else await api.post('/templates', body)
        editing = null
        toast('Template saved', 'ok', 1800)
        paintForm(); load()
      } catch (ex) { showErr(err, ex); busy(save, false) }
    } }, h('h2', null, editing ? 'Edit template' : 'Add a custom template'), field('Name', name), field('Prompt', prompt),
    h('div', { class: 'row' }, save, editing ? h('button', { class: 'btn', type: 'button', onclick: () => { editing = null; paintForm() } }, 'Cancel') : null)))
  }

  async function load() {
    try {
      const list = listOf(await api.get('/templates'), 'templates')
      if (!alive) return
      clear(box).append(h('ul', { class: 'list' }, list.map((t) => h('li', { style: 'padding:12px 14px' },
        h('div', { class: 'row between' },
          h('div', { class: 'row' }, h('strong', null, t.name), t.builtin ? h('span', { class: 'badge' }, 'built-in') : h('span', { class: 'badge ready' }, 'custom')),
          t.builtin ? null : h('div', { class: 'row' },
            h('button', { class: 'btn sm', type: 'button', 'aria-label': `Edit ${t.name}`, onclick: () => { editing = t; paintForm(); formBox.scrollIntoView({ block: 'nearest' }) } }, 'Edit'),
            h('button', { class: 'btn danger sm', type: 'button', 'aria-label': `Delete ${t.name}`, onclick: async () => {
              if (!await confirmDialog({ title: 'Delete this template?', body: t.name, confirm: 'Delete', danger: true })) return
              try { await api.del(`/templates/${encodeURIComponent(t.id)}`); toast('Template deleted', 'ok', 1800); load() } catch (ex) { showErr(err, ex) }
            } }, 'Delete'))),
        h('details', { style: 'margin-top:6px' }, h('summary', { class: 'small muted', style: 'cursor:pointer' }, 'Show prompt'),
          h('pre', { class: 'small', style: 'white-space:pre-wrap;margin:6px 0 0;font-family:var(--font)' }, t.prompt || ''))))))
    } catch (ex) { if (alive) showErr(err, ex) }
  }
  panel.append(h('div', { class: 'card' }, h('h2', null, 'Note templates'),
    h('p', { class: 'muted' }, 'Templates tell the notes LLM how to write up a meeting. Built-in templates are read-only; add your own below.'), err, box),
  h('div', { class: 'card' }, formBox))
  box.append(loading())
  paintForm()
  load()
  return () => { alive = false }
}

// ---------------------------------------------------------------- Status
function modelProgress(hl) {
  const raw = hl.model_progress ?? hl.model_download_progress ?? (hl.model_download && (hl.model_download.progress ?? hl.model_download.percent))
  if (typeof raw === 'number') return raw > 1 ? raw / 100 : raw
  const d = hl.model_download
  if (d && d.total) return Math.min(1, (d.bytes || 0) / d.total)
  if (d && d.total_bytes) return (d.downloaded_bytes || 0) / d.total_bytes
  return null
}

function status(panel) {
  let alive = true
  let timer = null
  const box = h('div')
  const botsBox = h('div')
  async function load() {
    try {
      const hl = await api.get('/health')
      if (!alive) return
      const p = modelProgress(hl)
      clear(box).append(h('dl', { class: 'kv' },
        h('dt', null, 'Server'), h('dd', null, h('span', { class: `badge ${hl.status === 'ok' ? 'ready' : 'processing'}` }, hl.status || 'unknown')),
        h('dt', null, 'Transcription engine'), h('dd', null, hl.engine || 'unknown'),
        h('dt', null, 'Model'), h('dd', null, hl.model || 'none'),
        h('dt', null, 'Model state'), h('dd', null,
          hl.model_ready ? h('span', { class: 'badge ready' }, 'ready')
            : h('div', null, h('span', { class: 'badge processing' }, 'downloading or loading'),
              p !== null ? h('div', { style: 'margin-top:6px;max-width:320px' }, h('progress', { value: p, max: 1, 'aria-label': 'Model download progress' }), h('span', { class: 'small muted' }, ` ${Math.round(p * 100)}%`)) : null,
              h('p', { class: 'small muted', style: 'margin:6px 0 0' }, 'The first start downloads the speech model (about 0.5 GB). Live and offline transcription start when it is ready.'))),
        h('dt', null, 'Transcription queue'), h('dd', null, `${hl.queue_depth ?? 0} waiting`),
        h('dt', null, 'Live meetings'), h('dd', null, String(hl.live_meetings ?? 0))))
    } catch (ex) { if (alive) clear(box).append(notice('error', ex.message)) }
    try {
      const bots = listOf(await api.get('/bot'), 'bots')
      if (!alive) return
      clear(botsBox).append(bots.length ? h('ul', { class: 'list' }, bots.map((b) => h('li', { style: 'padding:10px 14px' }, h('div', { class: 'row between' },
        h('a', { href: `#/meeting/${encodeURIComponent(b.meeting_id || b.id)}` }, b.title || b.url || b.meeting_id || b.id), statusBadge(b.state || b.status))))) : h('p', { class: 'muted' }, 'No bots are running.'))
    } catch { clear(botsBox) }
    if (alive) timer = setTimeout(load, 3000)
  }
  panel.append(h('div', { class: 'card' }, h('h2', null, 'Server status'), box), h('div', { class: 'card' }, h('h2', null, 'Active bots'), botsBox))
  box.append(loading())
  load()
  return () => { alive = false; clearTimeout(timer) }
}
