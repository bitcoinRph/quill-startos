import { api, listOf, audioUrl, eventsUrl } from '../api.js'
import { h, icon, clear, fill, debounce, fmtDate, fmtDuration, fmtClock, toast, copyText, saveBlob, slug, confirmDialog, menu, notice, tabList, busy, getPref } from '../util.js'
import { mdElement } from '../md.js'
import { createTranscript } from '../transcript.js'
import { statusBadge } from './library.js'

const SOURCES = { bot: 'Jitsi bot', browser: 'Browser', upload: 'Upload', import: 'Import', api: 'API' }
const FORMATS = { md: 'Markdown', txt: 'Plain text', srt: 'SRT subtitles', vtt: 'WebVTT' }
const wide = window.matchMedia('(min-width: 900px)')

const segmentsOf = (t) => (Array.isArray(t) ? t : (t && Array.isArray(t.segments) ? t.segments : []))
const tid = (t) => t.id ?? t.track_id

export function render(root, route, app) {
  const id = route.parts[1]
  const enc = encodeURIComponent(id)
  let alive = true
  let m = null
  let tracks = new Map()
  let segs = []
  let es = null
  let esRetry = null
  let botTimer = null
  let templates = []
  let firstLoad = true
  let curTrack = null
  let trackSegs = []
  let flashSeq = route.query.seq !== undefined ? Number(route.query.seq) : null

  const nameOf = (s) => {
    const sp = s.speaker || 'Speaker'
    if (/^(me|them)$/i.test(sp)) {
      const t = tracks.get(s.track_id)
      if (t && t.label && !/^(me|them)$/i.test(t.label)) return t.label
      return /^me$/i.test(sp) ? (getPref('me', 'Me') || 'Me') : 'Them'
    }
    return sp
  }

  const tr = createTranscript({ live: false, onSeek: seek, nameOf, emptyText: 'No transcript yet.' })
  const audio = h('audio', { controls: true, preload: 'none', 'aria-label': 'Meeting audio' })
  const playerLabel = h('span', { class: 'small muted', style: 'white-space:nowrap' })
  const player = h('div', { class: 'player', hidden: true }, playerLabel, audio)

  const titleInput = h('input', { class: 'title-input', 'aria-label': 'Meeting title', maxlength: 200 })
  const metaline = h('div', { class: 'metaline' })
  const tools = h('div', { class: 'tools' })
  const banner = h('div', { 'aria-live': 'polite' })
  const count = h('span', { class: 'small muted' })

  const notesArea = h('textarea', { class: 'notes-area', 'aria-label': 'Your notes', placeholder: 'Type your own notes here. They are saved automatically and combined with the transcript when AI notes are written.' })
  const saveState = h('span', { class: 'savestate', 'aria-live': 'polite' })
  const aiHead = h('div', { class: 'pane-head' })
  const aiBody = h('div', { class: 'pane-body' })

  const grid = h('div', { class: 'mgrid', dataset: { pane: 'mine', note: 'mine' } })
  const selectPane = (p) => { grid.dataset.pane = p; if (p !== 'transcript') grid.dataset.note = p; syncTabs() }
  const narrowTabs = tabList({ label: 'Meeting sections', cls: 'panetabs tabs-narrow', selected: 'mine', onSelect: selectPane,
    tabs: [{ id: 'transcript', label: 'Transcript' }, { id: 'mine', label: 'My notes' }, { id: 'ai', label: 'AI notes' }] })
  const wideTabs = tabList({ label: 'Notes', cls: 'tabs-wide', selected: 'mine', onSelect: (p) => selectPane(p),
    tabs: [{ id: 'mine', label: 'My notes' }, { id: 'ai', label: 'AI notes' }] })
  function syncTabs() {
    narrowTabs.mark(grid.dataset.pane)
    wideTabs.mark(grid.dataset.note)
  }
  wide.addEventListener('change', syncTabs)

  grid.append(
    narrowTabs.el,
    h('section', { class: 'pane p-transcript', 'aria-label': 'Transcript' }, h('div', { class: 'card flat' },
      h('div', { class: 'pane-head' }, h('h2', null, 'Transcript'), count), tr.el, player)),
    h('div', { class: 'notes-col' },
      wideTabs.el,
      h('section', { class: 'pane p-mine', 'aria-label': 'My notes' }, h('div', { class: 'card flat' },
        h('div', { class: 'pane-head' }, h('h2', null, 'My notes'), saveState), h('div', { class: 'pane-body' }, notesArea))),
      h('section', { class: 'pane p-ai', 'aria-label': 'AI notes' }, h('div', { class: 'card flat' }, aiHead, aiBody))))

  root.append(h('div', { class: 'mhead' }, titleInput, metaline, tools), banner, grid)

  // ---------- user notes autosave ----------
  let notesDirty = false
  const saveNotes = debounce(async () => {
    saveState.textContent = 'Saving…'
    const value = notesArea.value
    try {
      await api.patch(`/meetings/${enc}`, { user_notes_md: value })
      notesDirty = notesArea.value !== value
      saveState.textContent = notesDirty ? '' : 'Saved'
    } catch (e) {
      saveState.textContent = 'Not saved'
      toast(`Could not save notes: ${e.message}`, 'error')
    }
  }, 800)
  notesArea.addEventListener('input', () => { notesDirty = true; saveState.textContent = 'Editing…'; saveNotes() })
  notesArea.addEventListener('blur', () => saveNotes.flush())

  titleInput.addEventListener('change', async () => {
    const t = titleInput.value.trim() || 'Untitled meeting'
    try { await api.patch(`/meetings/${enc}`, { title: t }); m.title = t; app.setTitle(t); toast('Title saved', 'ok', 1800) } catch (e) { toast(e.message, 'error'); titleInput.value = m.title }
  })

  // ---------- loading ----------
  async function load({ keepNotes = false } = {}) {
    try {
      const res = await api.get(`/meetings/${enc}`, { query: { include: 'transcript,notes,tracks' } })
      if (!alive) return
      m = res
      tracks = new Map(listOf(res.tracks).map((t) => [tid(t), t]))
      segs = segmentsOf(res.transcript)
      if (!res.transcript && res.status !== 'scheduled') {
        try { segs = segmentsOf(await api.get(`/meetings/${enc}/transcript`, { query: { format: 'json' } })) } catch { /* transcript not ready yet */ }
      }
      segs = [...segs].sort((a, b) => (a.start_ms - b.start_ms) || ((a.seq ?? 0) - (b.seq ?? 0)))
      paint({ keepNotes })
    } catch (e) {
      if (!alive || e.status === 401) return
      clear(banner)
      if (e.status === 404) {
        clear(root)
        root.append(h('div', { class: 'empty' }, h('h2', null, 'Meeting not found'), h('p', null, 'It may have been deleted.'), h('a', { class: 'btn', href: '#/' }, 'Back to library')))
      } else banner.append(notice('error', e.message, ' ', h('button', { class: 'btn sm', type: 'button', onclick: () => load() }, 'Retry')))
    }
  }

  function paint({ keepNotes }) {
    app.setTitle(m.title || 'Meeting')
    if (document.activeElement !== titleInput) titleInput.value = m.title || ''
    const dur = m.status === 'live' ? '' : fmtDuration(m.duration_ms)
    const attendees = listOf(m.attendees).map((a) => (typeof a === 'string' ? a : a.name || a.email)).filter(Boolean)
    fill(metaline, statusBadge(m.status),
      h('span', null, SOURCES[m.source] || m.source || ''),
      h('span', null, fmtDate(m.started_at || m.created_at)),
      dur ? h('span', null, dur) : null,
      attendees.length ? h('span', null, `${attendees.length} attendee${attendees.length > 1 ? 's' : ''}: ${attendees.slice(0, 4).join(', ')}${attendees.length > 4 ? '…' : ''}`) : null)
    paintTools()
    paintBanner()

    tr.setSeekEnabled(m.status !== 'live' && m.status !== 'scheduled' && tracks.size > 0)
    tr.el.setAttribute('aria-live', m.status === 'live' ? 'polite' : 'off')
    tr.el.setAttribute('role', m.status === 'live' ? 'log' : 'group')
    tr.setEmptyText(m.status === 'live' ? 'Waiting for speech…' : m.status === 'processing' ? 'Transcribing…' : m.status === 'scheduled' ? 'The meeting has not started yet.' : 'No transcript yet.')
    tr.setAll(segs)
    count.textContent = segs.length ? `${segs.length} segments` : ''

    if (!keepNotes && (document.activeElement !== notesArea || !notesDirty)) notesArea.value = m.user_notes_md || ''
    paintAi()

    if (firstLoad) {
      firstLoad = false
      selectPane(m.ai_notes_md ? 'ai' : 'mine')
      if (flashSeq !== null && !Number.isNaN(flashSeq)) {
        if (!wide.matches) selectPane('transcript')
        requestAnimationFrame(() => tr.flash(flashSeq))
      }
    }
    syncEvents()
    syncBot()
  }

  function paintTools() {
    clear(tools)
    const exportItems = Object.entries(FORMATS).map(([ext, label]) => ({ label: `Transcript, ${label} (.${ext})`, onclick: () => exportTranscript(ext) }))
    exportItems.push('-',
      { label: 'Copy transcript', onclick: async () => copyText(await fetchTranscript('txt'), 'Transcript copied') },
      { label: 'Copy my notes', onclick: () => copyText(notesArea.value, 'Notes copied') },
      { label: 'Copy AI notes', onclick: () => copyText(m.ai_notes_md || '', 'AI notes copied') })
    const more = [
      { label: 'Re-transcribe audio', onclick: retranscribe }, '-',
      { label: 'Delete meeting', danger: true, onclick: del },
    ]
    tools.append(
      h('button', { class: 'btn', type: 'button', onclick: renameSpeakers, disabled: !segs.length }, 'Speakers'),
      menu('Export', exportItems),
      menu('More', more))
  }

  function paintBanner() {
    clear(banner)
    if (m.status === 'processing') banner.append(notice('info', h('span', { class: 'spinner' }), ' Transcribing the recording. This page updates by itself when it is done.'))
    else if (m.status === 'failed') banner.append(notice('error', h('strong', null, 'Processing failed. '), m.error || 'Unknown error.', ' ', h('button', { class: 'btn sm', type: 'button', onclick: retranscribe }, 'Try again')))
    else if (m.status === 'live') {
      banner.append(notice('info', h('span', { class: 'dot pulse', style: 'color:var(--danger)' }), ' This meeting is live. The transcript grows as people speak.', ' ',
        m.source === 'bot'
          ? h('button', { class: 'btn sm', type: 'button', onclick: () => endLive(`/bot/${enc}/leave`, 'The bot will leave the meeting.') }, 'Make bot leave')
          : h('button', { class: 'btn sm', type: 'button', onclick: () => endLive(`/meetings/${enc}/stop`, 'The meeting will end and be transcribed.') }, 'Stop meeting')))
    }
    if (m.source === 'bot' && (m.status === 'scheduled' || m.status === 'live')) banner.append(botBox)
  }
  const botBox = h('div', { class: 'card', style: 'margin-bottom:12px', 'aria-live': 'polite' })

  async function endLive(path, msg) {
    if (!await confirmDialog({ title: 'End this meeting?', body: msg, confirm: 'End meeting', danger: true })) return
    try { await api.post(path); toast('Done', 'ok', 1800); load({ keepNotes: true }) } catch (e) { toast(e.message, 'error') }
  }

  // ---------- bot state ----------
  async function pollBot() {
    try {
      const res = await api.get('/bot')
      const bots = listOf(res, 'bots')
      const b = bots.find((x) => (x.meeting_id || x.id) === id)
      clear(botBox)
      if (!b) { botBox.append(h('span', { class: 'muted' }, 'Waiting for the bot to start…')); return }
      const state = b.state || b.status || 'starting'
      botBox.append(h('div', { class: 'row' }, h('strong', null, 'Notetaker bot'), h('span', { class: `badge ${state}` }, state),
        b.error ? h('span', { class: 'muted' }, b.error) : null,
        state === 'lobby' ? h('span', { class: 'muted' }, 'Waiting to be admitted from the lobby.') : null))
    } catch { /* keep the last state */ }
  }
  function syncBot() {
    clearInterval(botTimer)
    botTimer = null
    if (m && m.source === 'bot' && (m.status === 'scheduled' || m.status === 'live')) {
      pollBot()
      botTimer = setInterval(pollBot, 3000)
    }
  }

  // ---------- live events ----------
  let pollTimer = null
  function syncEvents() {
    clearInterval(pollTimer)
    // Fallback in case an SSE status event is missed (proxy buffering, reconnect gap).
    if (m && (m.status === 'processing' || m.status === 'scheduled')) pollTimer = setInterval(() => load({ keepNotes: true }), 5000)
    const needs = m && ['live', 'processing', 'scheduled'].includes(m.status)
    if (needs && !es) openEvents()
    else if (!needs && es) closeEvents()
  }
  function openEvents() {
    es = new EventSource(eventsUrl(id), { withCredentials: true })
    const handle = (type, raw) => {
      let d = null
      try { d = JSON.parse(raw) } catch { return }
      type = type === 'message' ? d.type || type : type
      if (type === 'segment') onSegment(d.segment || d)
      else if (type === 'status') {
        const st = d.status || (d.meeting && d.meeting.status)
        if (st && m && st !== m.status) load({ keepNotes: true })
      } else if (type === 'notes') load({ keepNotes: true })
    }
    for (const t of ['segment', 'status', 'notes', 'message']) es.addEventListener(t, (e) => handle(t, e.data))
    es.onerror = () => {
      // EventSource retries by itself; reload once so a 401 or a finished meeting is noticed.
      clearTimeout(esRetry)
      esRetry = setTimeout(() => { if (alive) load({ keepNotes: true }) }, 3000)
    }
  }
  function closeEvents() { if (es) es.close(); es = null; clearTimeout(esRetry) }

  function onSegment(s) {
    if (!s || s.text === undefined) return
    const near = window.innerHeight + window.scrollY >= document.body.scrollHeight - 140
    const i = segs.findIndex((x) => x.seq !== undefined && x.seq === s.seq)
    if (i >= 0) segs[i] = s; else segs.push(s)
    tr.add(s)
    count.textContent = `${segs.length} segments`
    if (near && wide.matches) window.scrollTo({ top: document.body.scrollHeight })
  }

  // ---------- audio ----------
  function seek(seg) {
    const trackId = seg.track_id ?? (tracks.size ? [...tracks.keys()][0] : null)
    if (trackId === null || trackId === undefined) return
    const t = tracks.get(trackId)
    const at = Math.max(0, (seg.start_ms - ((t && t.started_offset_ms) || 0)) / 1000)
    player.hidden = false
    playerLabel.textContent = t ? (t.label || t.speaker || trackId) : trackId
    const go = () => { audio.currentTime = at; audio.play().catch(() => {}) }
    if (curTrack !== trackId) {
      curTrack = trackId
      trackSegs = segs.filter((x) => x.track_id === trackId)
      audio.src = audioUrl(id, trackId)
      audio.addEventListener('loadedmetadata', go, { once: true })
      audio.load()
    } else go()
  }
  audio.addEventListener('error', () => {
    if (!audio.src) return
    toast('The audio for this track is not available (it may have been removed by the retention setting).', 'error')
    curTrack = null
  })
  audio.addEventListener('timeupdate', () => {
    const t = tracks.get(curTrack)
    const ms = audio.currentTime * 1000 + ((t && t.started_offset_ms) || 0)
    const cur = trackSegs.find((s) => ms >= s.start_ms && ms < s.end_ms)
    tr.setActive(cur ? cur.seq : -1, true)
  })

  // ---------- AI notes ----------
  function paintAi() {
    const ready = m.status === 'ready'
    const hasNotes = !!(m.ai_notes_md && m.ai_notes_md.trim())
    const sel = h('select', { class: 'select', 'aria-label': 'Notes template' }, templates.map((t) => h('option', { value: t.id }, t.name)))
    const btn = h('button', { class: 'btn primary', type: 'button', disabled: !ready || !templates.length || !segs.length, onclick: () => generate(sel, btn) }, hasNotes ? 'Regenerate' : 'Generate notes')
    fill(aiHead, h('h2', null, 'AI notes'),
      m.ai_notes_source ? h('span', { class: 'badge' }, `by ${m.ai_notes_source}`) : null,
      h('span', { class: 'grow' }), templates.length ? sel : null, btn,
      hasNotes ? h('button', { class: 'btn ghost sm icon-btn', type: 'button', title: 'Copy AI notes', 'aria-label': 'Copy AI notes', onclick: () => copyText(m.ai_notes_md, 'AI notes copied') }, icon('copy')) : null)
    clear(aiBody)
    if (hasNotes) aiBody.append(mdElement(m.ai_notes_md))
    else aiBody.append(h('p', { class: 'muted' }, ready
      ? 'No AI notes yet. Pick a template and generate them with the server\'s LLM, or ask your agent to write them through MCP.'
      : 'AI notes can be generated once the transcript is ready.'))
  }

  async function generate(sel, btn) {
    busy(btn, true, 'Generating')
    try {
      await api.post(`/meetings/${enc}/notes/generate`, { template_id: sel.value || undefined })
      toast('Notes generated', 'ok', 2200)
      await load({ keepNotes: true })
      selectPane('ai')
    } catch (e) {
      busy(btn, false)
      if (e.status === 409) {
        aiBody.prepend(notice('warn', h('strong', null, 'No LLM is configured on this server. '),
          'Ask your agent to write the notes through MCP (it can call ', h('code', null, 'save_notes'), '), or set up a notes LLM in StartOS. ',
          h('a', { href: '#/settings/agents' }, 'Connect an agent')))
      } else toast(e.message, 'error')
    }
  }

  // ---------- actions ----------
  async function fetchTranscript(fmt) {
    return api.get(`/meetings/${enc}/transcript`, { query: { format: fmt }, as: 'text' })
  }
  async function exportTranscript(ext) {
    try {
      const blob = await api.get(`/meetings/${enc}/transcript`, { query: { format: ext }, as: 'blob' })
      saveBlob(blob, `${slug(m.title)}.${ext}`)
    } catch (e) { toast(e.message, 'error') }
  }
  async function retranscribe() {
    if (!await confirmDialog({ title: 'Re-transcribe this meeting?', body: 'The stored audio is transcribed again and the current transcript is replaced.', confirm: 'Re-transcribe' })) return
    try { await api.post(`/meetings/${enc}/retranscribe`); toast('Re-transcription queued', 'ok'); load({ keepNotes: true }) } catch (e) { toast(e.message, 'error') }
  }
  async function del() {
    if (!await confirmDialog({ title: 'Delete this meeting?', body: 'The transcript, notes and audio are removed from the server. This cannot be undone.', confirm: 'Delete', danger: true })) return
    try { await api.del(`/meetings/${enc}`); toast('Meeting deleted', 'ok'); location.hash = '#/' } catch (e) { toast(e.message, 'error') }
  }

  function renameSpeakers() {
    const names = new Map()
    for (const s of segs) if (!names.has(s.speaker)) names.set(s.speaker, nameOf(s))
    const inputs = [...names].map(([raw, shown], i) => [raw, shown, h('input', { class: 'input', id: `spk${i}`, value: shown, maxlength: 80 })])
    const dlg = h('dialog', { 'aria-labelledby': 'spk-title' },
      h('form', { method: 'dialog' },
        h('h2', { id: 'spk-title' }, 'Rename speakers'),
        h('p', { class: 'muted' }, 'Changes apply to the whole transcript and to exports.'),
        h('div', { class: 'stack' }, inputs.map(([raw, shown, input], i) => h('div', { class: 'field', style: 'margin:0' }, h('label', { for: `spk${i}` }, `Speaker "${raw}"`), input))),
        h('div', { class: 'dlg-actions' },
          h('button', { class: 'btn', value: 'cancel', type: 'submit' }, 'Cancel'),
          h('button', { class: 'btn primary', value: 'ok', type: 'submit' }, 'Save'))))
    dlg.addEventListener('close', async () => {
      const ok = dlg.returnValue === 'ok'
      const map = {}
      if (ok) for (const [raw, shown, input] of inputs) { const v = input.value.trim(); if (v && v !== shown) map[raw] = v }
      dlg.remove()
      if (!Object.keys(map).length) return
      try { await api.patch(`/meetings/${enc}`, { speakers: map }); toast('Speakers renamed', 'ok', 2000); load({ keepNotes: true }) } catch (e) { toast(e.message, 'error') }
    })
    document.body.append(dlg)
    dlg.showModal()
  }

  load()
  api.get('/templates').then((r) => { templates = listOf(r, 'templates'); if (m) paintAi() }).catch(() => {})

  return () => {
    alive = false
    closeEvents()
    clearInterval(botTimer)
    clearInterval(pollTimer)
    saveNotes.flush()
    audio.pause()
    wide.removeEventListener('change', syncTabs)
  }
}
