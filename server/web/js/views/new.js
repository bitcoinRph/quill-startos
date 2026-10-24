import { api, listOf, upload } from '../api.js'
import { h, icon, clear, fmtClock, fmtBytes, toast, notice, field, busy, tabList, getPref } from '../util.js'
import { recorder } from '../recorder.js'
import { createTranscript } from '../transcript.js'

const TABS = [{ id: 'bot', label: 'Jitsi bot' }, { id: 'recorder', label: 'Browser recorder' }, { id: 'upload', label: 'Upload' }]
const defaultTitle = () => `Meeting ${new Date().toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}`

export function render(root, route, app) {
  app.setTitle('New meeting')
  const tab = TABS.some((t) => t.id === route.parts[1]) ? route.parts[1] : (recorder.active ? 'recorder' : 'bot')
  const panel = h('div', { role: 'tabpanel', 'aria-label': TABS.find((t) => t.id === tab).label })
  const tabs = tabList({ label: 'Ways to start a meeting', tabs: TABS, selected: tab, onSelect: (id) => { location.hash = `#/new/${id}` } })
  root.append(h('h1', { style: 'margin-bottom:14px' }, 'New meeting'), tabs.el, panel)
  const dispose = { bot: botPanel, recorder: recorderPanel, upload: uploadPanel }[tab](panel)
  return () => dispose && dispose()
}

// ---------------------------------------------------------------- Jitsi bot
function botPanel(panel) {
  let alive = true
  let timer = null
  const url = h('input', { class: 'input', type: 'url', name: 'url', required: true, placeholder: 'https://jitsi.example.com/TeamSync', autocomplete: 'off' })
  const title = h('input', { class: 'input', name: 'title', placeholder: 'Weekly sync (optional)', maxlength: 200 })
  const pw = h('input', { class: 'input', type: 'password', name: 'password', autocomplete: 'off', placeholder: 'Only if the room has one' })
  const err = h('div', { 'aria-live': 'assertive' })
  const btn = h('button', { class: 'btn primary', type: 'submit' }, 'Send the bot')
  const stateBox = h('div', { 'aria-live': 'polite' })

  const form = h('form', { class: 'card', onsubmit: async (e) => {
    e.preventDefault()
    clear(err)
    busy(btn, true, 'Sending')
    try {
      const body = { url: url.value.trim() }
      if (title.value.trim()) body.title = title.value.trim()
      if (pw.value) body.password = pw.value
      const res = await api.post('/bot/join', body)
      const m = res.meeting || res
      watch(m.id || m.meeting_id)
      form.hidden = true
    } catch (ex) { err.append(notice('error', ex.message)) } finally { busy(btn, false) }
  } },
  h('h2', null, 'Send the notetaker bot to a Jitsi meeting'),
  h('p', { class: 'muted' }, 'The bot joins as a silent participant, posts a recording notice in the chat, and transcribes everyone by name. Only the Jitsi servers you allowed in StartOS are accepted.'),
  field('Meeting URL', url), field('Title', title), field('Room password', pw), err, btn)

  function watch(meetingId) {
    const step = ['starting', 'lobby', 'joined']
    async function poll() {
      try {
        const bots = listOf(await api.get('/bot'), 'bots')
        const b = bots.find((x) => (x.meeting_id || x.id) === meetingId)
        const state = (b && (b.state || b.status)) || 'starting'
        clear(stateBox).append(h('div', { class: 'card' },
          h('div', { class: 'row between' }, h('h2', { style: 'margin:0' }, 'Bot status'), h('span', { class: `badge ${state}` }, state)),
          h('div', { class: 'steps', style: 'margin:12px 0' }, step.map((s) => h('span', { class: `badge ${step.indexOf(s) <= step.indexOf(state) ? 'ready' : ''}` }, s))),
          state === 'lobby' ? h('p', { class: 'muted' }, 'The bot is waiting in the lobby. Admit "Quill Notetaker" from your Jitsi meeting.') : null,
          state === 'joined' ? h('p', { class: 'muted' }, 'The bot is in the meeting and transcribing.') : null,
          b && b.error ? notice('error', b.error) : null,
          h('div', { class: 'row' },
            h('a', { class: 'btn primary', href: `#/meeting/${encodeURIComponent(meetingId)}` }, 'Open meeting'),
            ['ended', 'failed'].includes(state) ? null : h('button', { class: 'btn', type: 'button', onclick: async () => {
              try { await api.post(`/bot/${encodeURIComponent(meetingId)}/leave`); toast('The bot is leaving', 'ok') } catch (ex) { toast(ex.message, 'error') }
            } }, 'Make bot leave'))))
        if (['ended', 'failed'].includes(state)) return
      } catch (ex) { if (ex.status === 401) return }
      if (alive) timer = setTimeout(poll, 2500)
    }
    poll()
  }

  const info = h('div', { 'aria-live': 'polite' })
  panel.append(info, form, stateBox)
  api.get('/bot').then((r) => {
    if (!alive || !r || r.enabled === undefined) return
    if (r.enabled === false) {
      btn.disabled = true
      info.append(notice('warn', h('strong', null, 'The Jitsi bot is turned off. '), 'Set the Jitsi servers it may join with the "Configure Jitsi" action of the Quill service in StartOS. Until then, use the browser recorder or upload audio.'))
    } else if (r.domains && r.domains.length) {
      info.append(h('p', { class: 'hint' }, `Allowed Jitsi servers: ${r.domains.join(', ')}`))
    }
  }).catch(() => {})
  return () => { alive = false; clearTimeout(timer) }
}

// ---------------------------------------------------------------- Browser recorder
function recorderPanel(panel) {
  const secure = window.isSecureContext
  const canTab = !!(navigator.mediaDevices && navigator.mediaDevices.getDisplayMedia)
  const canMic = !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia)
  let mode = 'mic'
  let view = null // 'idle' | 'live'
  let tr = null
  const els = {}
  let tick = null

  const nameOf = (s) => (/^me$/i.test(s.speaker) ? (getPref('me', 'Me') || 'Me') : /^them$/i.test(s.speaker) ? 'Them' : s.speaker || 'Speaker')

  function idleUI() {
    const title = h('input', { class: 'input', value: defaultTitle(), maxlength: 200 })
    const err = h('div', { 'aria-live': 'assertive' })
    const start = h('button', { class: 'btn primary lg', type: 'button', disabled: !secure || !canMic }, icon('mic'), 'Start recording')
    const radio = (value, t, d, disabled) => h('label', { class: `radio-card${disabled ? ' disabled' : ''}` },
      h('input', { type: 'radio', name: 'mode', value, checked: mode === value, disabled, onchange: () => { mode = value; tabNote.hidden = value !== 'tab' } }),
      h('span', null, h('span', { class: 't' }, t), h('span', { class: 'muted small' }, d)))
    const tabNote = h('div', { class: 'notice', hidden: mode !== 'tab' }, icon('info'), h('div', null,
      h('strong', null, 'Tab audio needs Chrome, Edge or another Chromium browser on a computer. '),
      'When the share dialog opens, pick the browser tab with your meeting and tick ', h('strong', null, '"Share tab audio"'),
      '. Only the audio is kept; the video is discarded immediately. Safari and Firefox can record the microphone only.'))
    start.addEventListener('click', async () => {
      clear(err)
      busy(start, true, 'Starting')
      try {
        await recorder.start({ title: title.value.trim() || defaultTitle(), mode })
      } catch (ex) { err.append(notice('error', explain(ex))) } finally { busy(start, false); start.prepend(icon('mic')) }
    })
    return h('div', { class: 'card' },
      h('h2', null, 'Record in this browser'),
      secure ? null : notice('warn', h('strong', null, 'Recording needs a secure (HTTPS) connection. '),
        'Your browser blocks microphone access on plain http:// pages. Open Quill from its HTTPS address instead: in StartOS go to your Quill service, open Interfaces, and use the https:// (or .local) address. If the browser warns about the certificate, install your server\'s root CA on this device first.'),
      canMic ? null : notice('warn', 'This browser does not provide microphone capture.'),
      h('div', { class: 'radio-cards', role: 'radiogroup', 'aria-label': 'What to record', style: 'margin-bottom:12px' },
        radio('mic', 'Microphone only', 'Just your voice. Works in every current browser.', false),
        radio('tab', 'Microphone + tab audio', canTab ? 'You and the other side of a call in a browser tab.' : 'Not available in this browser.', !canTab)),
      tabNote, field('Title', title), err, start)
  }

  function explain(ex) {
    if (ex.name === 'NotAllowedError' || ex.name === 'SecurityError') return 'Permission was denied or the picker was cancelled. Allow microphone access in the address bar. For tab audio, choose a tab and tick "Share tab audio".'
    if (ex.name === 'NotFoundError') return 'No microphone was found on this device.'
    if (ex.name === 'NotReadableError') return 'The microphone is in use by another application.'
    return ex.message || String(ex)
  }

  function liveUI() {
    els.status = h('span', { class: 'row', style: 'gap:6px;font-weight:600' })
    els.timer = h('time', { class: 'timer', role: 'timer' }, '0:00')
    els.meters = h('div', { class: 'meters' })
    els.fills = {}
    for (const t of recorder.tracks) {
      const fill = h('div', { class: 'bar-fill' })
      els.fills[t.track_id] = fill
      els.meters.append(h('div', { class: 'meter' }, h('span', { class: 'small muted', style: 'min-width:54px' }, t.kind === 'mic' ? 'Mic' : 'Tab audio'),
        h('div', { class: 'bar-track', 'aria-hidden': 'true' }, fill)))
    }
    els.notice = h('div', { 'aria-live': 'polite' })
    els.stop = h('button', { class: 'btn danger solid lg stop', type: 'button', onclick: async () => {
      busy(els.stop, true, 'Stopping')
      try { const id = await recorder.stop(); location.hash = `#/meeting/${encodeURIComponent(id)}` } catch (ex) { toast(ex.message, 'error'); busy(els.stop, false) }
    } }, icon('stop'), 'Stop recording')
    tr = createTranscript({ live: true, nameOf, emptyText: 'Listening… the transcript appears here a few seconds after people speak.' })
    tr.setAll(recorder.segments)
    els.scroll = h('div', { class: 'rec-scroll', tabindex: '0', 'aria-label': 'Live transcript' }, tr.el)
    els.notes = h('textarea', { class: 'textarea', 'aria-label': 'Your notes', placeholder: 'Take notes while you record. They are saved with the meeting.', value: recorder.notes,
      oninput: (e) => recorder.setNotes(e.target.value) })
    return h('div', null,
      h('div', { class: 'row between', style: 'margin-bottom:8px' }, h('h2', null, recorder.title || 'Recording')),
      h('div', { class: 'card rec-bar' }, els.status, els.timer, els.meters, els.stop),
      els.notice,
      h('div', { class: 'rec-grid' },
        h('section', { class: 'card flat', 'aria-label': 'Live transcript' }, h('div', { class: 'pane-head' }, h('h2', null, 'Live transcript')), els.scroll),
        h('section', { class: 'card flat', 'aria-label': 'Notes' }, h('div', { class: 'pane-head' }, h('h2', null, 'My notes')), els.notes)))
  }

  function paintStatus() {
    if (!els.status) return
    const c = recorder.conn
    const label = recorder.phase === 'stopping' ? 'Finishing' : c === 'open' ? 'Recording' : c === 'reconnecting' ? `Reconnecting (attempt ${recorder.attempt})` : 'Connecting'
    const bad = c !== 'open' && recorder.phase !== 'stopping'
    clear(els.status).append(h('span', { class: `dot ${bad ? '' : 'pulse'}`, style: `color:var(${bad ? '--warn' : '--danger'})` }), label)
    clear(els.notice)
    if (recorder.attempt >= 3 && c !== 'open') els.notice.append(notice('warn', 'Still trying to reach the server. Audio is being kept in memory and will be sent when the connection returns.'))
    if (recorder.notice) els.notice.append(notice('warn', recorder.notice))
    els.timer.textContent = fmtClock(recorder.elapsedMs())
  }

  function paint() {
    const want = recorder.active && recorder.phase !== 'starting' ? 'live' : 'idle'
    if (want !== view) {
      view = want
      clear(panel)
      panel.append(want === 'live' ? liveUI() : idleUI())
    }
    paintStatus()
  }

  const onChange = () => paint()
  const onSeg = (e) => {
    if (!tr) return
    const el = els.scroll
    const near = el.scrollHeight - el.scrollTop - el.clientHeight < 80
    tr.add(e.detail)
    if (near) el.scrollTop = el.scrollHeight
  }
  const onLevel = () => {
    for (const [id, fill] of Object.entries(els.fills || {})) {
      const v = recorder.levels[id] || 0
      fill.style.width = `${Math.round(v * 100)}%`
      fill.classList.toggle('hot', v > 0.85)
    }
  }
  recorder.addEventListener('change', onChange)
  recorder.addEventListener('segment', onSeg)
  recorder.addEventListener('level', onLevel)
  tick = setInterval(() => { if (els.timer && recorder.active) els.timer.textContent = fmtClock(recorder.elapsedMs()) }, 500)
  paint()

  return () => {
    clearInterval(tick)
    recorder.removeEventListener('change', onChange)
    recorder.removeEventListener('segment', onSeg)
    recorder.removeEventListener('level', onLevel)
  }
}

// ---------------------------------------------------------------- Upload
function uploadPanel(panel) {
  let files = [] // {file, speaker, status, progress, error}
  let meetingId = null
  let busyUpload = false

  const input = h('input', { type: 'file', multiple: true, accept: 'audio/*,video/*,.m4a,.mp3,.wav,.ogg,.opus,.flac,.webm,.mp4', class: 'sr-only', id: 'file-input', onchange: () => { addFiles(input.files); input.value = '' } })
  const title = h('input', { class: 'input', value: defaultTitle(), maxlength: 200 })
  const list = h('div', { 'aria-live': 'polite' })
  const err = h('div', { 'aria-live': 'assertive' })
  const go = h('button', { class: 'btn primary', type: 'button', disabled: true, onclick: run }, 'Upload and transcribe')
  const drop = h('label', { class: 'dropzone', for: 'file-input', style: 'display:block;cursor:pointer' },
    h('strong', { style: 'color:var(--text)' }, 'Choose audio files'), ' or drop them here', h('div', { class: 'small' }, 'Anything ffmpeg can read: mp3, m4a, wav, ogg, flac, webm, mp4'))
  drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('over') })
  drop.addEventListener('dragleave', () => drop.classList.remove('over'))
  drop.addEventListener('drop', (e) => { e.preventDefault(); drop.classList.remove('over'); addFiles(e.dataTransfer.files) })

  function addFiles(fl) {
    for (const f of fl) files.push({ file: f, speaker: `Speaker ${files.length + 1}`, status: 'ready', progress: 0 })
    paint()
  }

  function paint() {
    clear(list)
    files.forEach((f, i) => {
      const sp = h('input', { class: 'input', value: f.speaker, 'aria-label': `Speaker label for ${f.file.name}`, disabled: busyUpload || f.status === 'done', oninput: (e) => { f.speaker = e.target.value } })
      list.append(h('div', { class: 'filerow' },
        h('div', null, h('div', { class: 'fname', title: f.file.name }, f.file.name), h('div', { class: 'small muted' }, fmtBytes(f.file.size),
          f.status === 'uploading' ? h('progress', { value: f.progress, max: 1, 'aria-label': `Uploading ${f.file.name}` }) : null,
          f.status === 'done' ? ' · uploaded' : null, f.status === 'error' ? h('span', { style: 'color:var(--danger)' }, ` · ${f.error}`) : null)),
        sp,
        h('button', { class: 'btn ghost sm', type: 'button', disabled: busyUpload || f.status === 'done', 'aria-label': `Remove ${f.file.name}`, onclick: () => { files.splice(i, 1); paint() } }, 'Remove')))
    })
    go.disabled = busyUpload || !files.length || files.every((f) => f.status === 'done')
  }

  async function run() {
    clear(err)
    busyUpload = true
    busy(go, true, 'Uploading')
    paint()
    try {
      if (!meetingId) { const m = await api.post('/meetings', { title: title.value.trim() || defaultTitle(), source: 'upload' }); meetingId = m.id }
      for (const f of files) {
        if (f.status === 'done') continue
        f.status = 'uploading'; f.error = ''; paint()
        const fd = new FormData()
        fd.append('file', f.file, f.file.name)
        fd.append('speaker', (f.speaker || '').trim() || 'Speaker')
        try {
          await upload(`/meetings/${encodeURIComponent(meetingId)}/audio`, fd, (p) => { f.progress = p; const pr = list.querySelectorAll('progress')[files.indexOf(f)]; if (pr) pr.value = p })
          f.status = 'done'
        } catch (ex) { f.status = 'error'; f.error = ex.message; throw ex }
      }
      toast('Uploaded. Transcription started.', 'ok')
      location.hash = `#/meeting/${encodeURIComponent(meetingId)}`
    } catch (ex) {
      err.append(notice('error', ex.message))
    } finally { busyUpload = false; busy(go, false); paint() }
  }

  panel.append(h('div', { class: 'card' },
    h('h2', null, 'Upload recordings'),
    h('p', { class: 'muted' }, 'Add one file per speaker or channel (for example your mic and the other side separately) and give each a speaker label. A single mixed file works too.'),
    field('Title', title), input, drop, list, err, h('div', { class: 'row', style: 'margin-top:12px' }, go)))
  paint()
}
