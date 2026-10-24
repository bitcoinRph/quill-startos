// Browser recorder: captures mic (and optionally shared tab audio), turns it into 16 kHz PCM16
// with an AudioWorklet and streams it over the live WebSocket (server-spec section 6).
// One shared instance survives navigation so a recording is never tied to a view.
import { api, liveUrl } from './api.js'
import { debounce, getPref } from './util.js'

const MAX_QUEUE_BYTES = 1_500_000 // about 45 s of one track while the socket is down
const MAX_BUFFERED = 2_000_000

class Recorder extends EventTarget {
  constructor() {
    super()
    this.reset()
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible' && this.phase === 'recording') this.acquireWake()
    })
    window.addEventListener('beforeunload', (e) => {
      if (this.phase === 'recording') { e.preventDefault(); e.returnValue = '' }
    })
  }

  reset() {
    this.phase = 'idle' // idle | starting | recording | stopping
    this.meetingId = null
    this.title = ''
    this.mode = 'mic'
    this.segments = []
    this.levels = {}
    this.tracks = [] // {track_id, kind, speaker, label, sample_rate, active}
    this.conn = 'closed' // closed | connecting | open | reconnecting
    this.attempt = 0
    this.notes = ''
    this.notice = ''
    this.startedAt = 0
    this.ws = null
    this.queue = []
    this.queueBytes = 0
    this.ctx = null
    this.nodes = []
    this.streams = []
    this.wake = null
    this.stopping = false
    this.reconnectTimer = null
    this.pingTimer = null
    this.serverDone = false
  }

  get active() { return this.phase === 'starting' || this.phase === 'recording' || this.phase === 'stopping' }
  elapsedMs() { return this.startedAt ? Date.now() - this.startedAt : 0 }
  emit(type, detail) { this.dispatchEvent(new CustomEvent(type, { detail })) }
  change() { this.emit('change') }

  async start({ title, mode }) {
    if (this.active) throw new Error('A recording is already running.')
    this.reset()
    this.phase = 'starting'
    this.title = title
    this.mode = mode
    this.change()
    try {
      // getDisplayMedia needs a fresh user gesture, so ask for it before anything else awaits.
      let display = null
      if (mode === 'tab') {
        display = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: true })
        display.getVideoTracks().forEach((t) => t.stop()) // we only want the audio
        if (!display.getAudioTracks().length) {
          display.getTracks().forEach((t) => t.stop())
          throw new Error('No tab audio was shared. In the share dialog choose a Chrome tab and tick "Share tab audio".')
        }
      }
      let mic
      try {
        mic = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
        })
      } catch (err) {
        if (display) display.getTracks().forEach((t) => t.stop())
        throw err
      }
      this.streams = [mic, display].filter(Boolean)
      const me = (getPref('me', 'Me') || 'Me').trim()
      this.tracks = [{ track_id: 'mic', kind: 'mic', speaker: 'me', label: me, sample_rate: 16000, active: true, stream: mic }]
      if (display) this.tracks.push({ track_id: 'system', kind: 'system', speaker: 'them', label: 'Them', sample_rate: 16000, active: true, stream: new MediaStream(display.getAudioTracks()) })

      const meeting = await api.post('/meetings', { title, source: 'browser' })
      this.meetingId = meeting.id

      const Ctx = window.AudioContext || window.webkitAudioContext
      this.ctx = new Ctx({ latencyHint: 'interactive' })
      await this.ctx.resume()
      await this.ctx.audioWorklet.addModule(new URL('./worklet.js', import.meta.url).href)
      for (const t of this.tracks) {
        const src = this.ctx.createMediaStreamSource(t.stream)
        const node = new AudioWorkletNode(this.ctx, 'pcm16k', { numberOfInputs: 1, numberOfOutputs: 1, channelCount: 1, channelCountMode: 'explicit', processorOptions: { id: t.track_id } })
        const mute = this.ctx.createGain()
        mute.gain.value = 0
        src.connect(node).connect(mute).connect(this.ctx.destination)
        node.port.onmessage = (e) => this.onFrame(e.data)
        this.nodes.push({ id: t.track_id, src, node, mute })
        t.stream.getAudioTracks().forEach((mt) => mt.addEventListener('ended', () => this.onTrackEnded(t)))
      }
      this.startedAt = Date.now()
      this.phase = 'recording'
      this.connect()
      this.acquireWake()
      this.change()
      return this.meetingId
    } catch (err) {
      await this.teardownCapture()
      const id = this.meetingId
      if (id) api.del(`/meetings/${encodeURIComponent(id)}`).catch(() => {}) // do not leave an empty meeting behind
      this.reset()
      this.change()
      throw err
    }
  }

  onTrackEnded(t) {
    if (!t.active || this.phase !== 'recording') return
    t.active = false
    this.sendJSON({ type: 'track_end', track_id: t.track_id })
    this.notice = t.kind === 'system'
      ? 'Tab sharing stopped, so the other side is no longer recorded. Your microphone is still recording.'
      : 'The microphone stopped (was it unplugged?). Recording continues with the remaining tracks.'
    if (this.tracks.every((x) => !x.active)) this.stop()
    this.change()
  }

  onFrame({ id, pcm, rms }) {
    const level = Math.min(1, Math.sqrt(rms) * 1.6)
    this.levels[id] = level
    this.emit('level')
    const idBytes = new TextEncoder().encode(id)
    const buf = new Uint8Array(1 + idBytes.length + pcm.byteLength)
    buf[0] = idBytes.length
    buf.set(idBytes, 1)
    buf.set(new Uint8Array(pcm), 1 + idBytes.length)
    this.sendBinary(buf)
  }

  sendBinary(buf) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN && this.conn === 'open') {
      if (this.ws.bufferedAmount > MAX_BUFFERED) return // network cannot keep up: drop rather than lag
      this.ws.send(buf)
      return
    }
    this.queue.push(buf)
    this.queueBytes += buf.byteLength
    while (this.queueBytes > MAX_QUEUE_BYTES && this.queue.length) this.queueBytes -= this.queue.shift().byteLength
  }

  sendJSON(obj) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) { this.ws.send(JSON.stringify(obj)); return true }
    return false
  }

  connect() {
    if (this.serverDone || this.stopping) return
    this.conn = this.attempt ? 'reconnecting' : 'connecting'
    this.change()
    const ws = new WebSocket(liveUrl(this.meetingId))
    ws.binaryType = 'arraybuffer'
    this.ws = ws
    ws.onopen = () => {
      // Re-register every live track, then flush what was captured while offline.
      for (const t of this.tracks) {
        if (!t.active) continue
        ws.send(JSON.stringify({ type: 'track', track_id: t.track_id, kind: t.kind, speaker: t.speaker, label: t.label, sample_rate: 16000 }))
      }
      this.conn = 'open'
      this.attempt = 0
      const q = this.queue
      this.queue = []
      this.queueBytes = 0
      for (const b of q) ws.send(b)
      clearInterval(this.pingTimer)
      this.pingTimer = setInterval(() => this.sendJSON({ type: 'ping' }), 20000)
      if (this.notes) this.sendJSON({ type: 'note', markdown: this.notes })
      this.change()
    }
    ws.onmessage = (ev) => {
      if (typeof ev.data !== 'string') return
      let msg
      try { msg = JSON.parse(ev.data) } catch { return }
      if (msg.type === 'segment' && msg.segment) {
        const s = msg.segment
        const i = this.segments.findIndex((x) => x.seq === s.seq)
        if (i >= 0) this.segments[i] = s
        else this.segments.push(s)
        this.emit('segment', s)
      } else if (msg.type === 'status') {
        this.lastStatus = msg
        if (msg.status === 'processing' || msg.status === 'ready' || msg.status === 'failed') this.serverDone = true
        this.emit('status', msg)
      } else if (msg.type === 'error') {
        this.notice = msg.message || 'The server reported an error.'
        this.change()
      }
    }
    ws.onclose = () => {
      clearInterval(this.pingTimer)
      if (this.ws !== ws) return
      this.conn = 'closed'
      if (this.phase === 'recording' && !this.serverDone) this.scheduleReconnect()
      this.change()
    }
    ws.onerror = () => { /* onclose follows */ }
  }

  scheduleReconnect() {
    this.attempt += 1
    this.conn = 'reconnecting'
    const delay = Math.min(15000, 1000 * 2 ** Math.min(this.attempt - 1, 4)) + Math.random() * 300
    clearTimeout(this.reconnectTimer)
    this.reconnectTimer = setTimeout(() => this.connect(), delay)
  }

  setNotes(markdown) {
    this.notes = markdown
    if (!this._saveNotes) {
      this._saveNotes = debounce(() => {
        this.sendJSON({ type: 'note', markdown: this.notes })
        if (this.meetingId) api.patch(`/meetings/${encodeURIComponent(this.meetingId)}`, { user_notes_md: this.notes }).catch(() => {})
      }, 900)
    }
    this._saveNotes()
  }

  async acquireWake() {
    try {
      if (!('wakeLock' in navigator) || (this.wake && !this.wake.released)) return
      this.wake = await navigator.wakeLock.request('screen')
    } catch { /* not allowed or unsupported: the page just may sleep */ }
  }

  async teardownCapture() {
    for (const n of this.nodes) {
      try { n.node.port.postMessage('flush') } catch { /* closed */ }
    }
    await new Promise((r) => setTimeout(r, 80)) // let the final partial frames arrive
    for (const n of this.nodes) {
      try { n.src.disconnect(); n.node.disconnect(); n.mute.disconnect() } catch { /* already gone */ }
    }
    this.nodes = []
    this.streams.forEach((s) => s.getTracks().forEach((t) => t.stop()))
    this.streams = []
    if (this.ctx) { try { await this.ctx.close() } catch { /* closed */ } this.ctx = null }
    if (this.wake) { try { await this.wake.release() } catch { /* released */ } this.wake = null }
  }

  async stop() {
    if (this.phase !== 'recording' && this.phase !== 'starting') return this.meetingId
    this.stopping = true
    this.phase = 'stopping'
    this.change()
    clearTimeout(this.reconnectTimer)
    if (this._saveNotes) this._saveNotes.flush()
    await this.teardownCapture()
    const id = this.meetingId
    // The socket may be down; give it a moment to come back, otherwise use the REST stop.
    let stopped = false
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      for (const t of this.tracks) if (t.active) this.sendJSON({ type: 'track_end', track_id: t.track_id })
      if (this.notes) this.sendJSON({ type: 'note', markdown: this.notes })
      for (const b of this.queue) this.ws.send(b)
      this.queue = []
      this.sendJSON({ type: 'stop' })
      stopped = await new Promise((resolve) => {
        const done = () => { this.removeEventListener('status', onStatus); clearTimeout(timer); resolve(true) }
        const onStatus = (e) => { if (['processing', 'ready', 'failed'].includes(e.detail.status)) done() }
        const timer = setTimeout(() => { this.removeEventListener('status', onStatus); resolve(false) }, 5000)
        this.addEventListener('status', onStatus)
      })
    }
    if (!stopped) {
      try {
        await api.patch(`/meetings/${encodeURIComponent(id)}`, { user_notes_md: this.notes })
      } catch { /* notes are best effort here */ }
      await api.post(`/meetings/${encodeURIComponent(id)}/stop`).catch(() => {})
    }
    if (this.ws) { this.ws.onclose = null; try { this.ws.close() } catch { /* closed */ } }
    clearInterval(this.pingTimer)
    this.reset()
    this.meetingId = null
    this.change()
    return id
  }

  // test hook for the dev mock: force the socket to drop so reconnect can be exercised
  _dropSocket() { if (this.ws) this.ws.close(4000, 'test drop') }
}

export const recorder = new Recorder()
window.__quillRecorder = recorder // handy for debugging in the console
