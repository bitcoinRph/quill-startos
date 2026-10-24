#!/usr/bin/env node
// Dev-only mock of the Quill server (sections 5 and 6 of docs/server-spec.md).
// Zero dependencies: node:http plus a tiny RFC 6455 WebSocket implementation.
//   node mock_server.mjs [--port 8099]      password: MOCK_PASSWORD (default "quill")
//   MOCK_NO_LLM=1 makes POST .../notes/generate answer 409.
// Debug endpoints for tests: GET /__mock/state, POST /__mock/drop, POST /__mock/reset.
import http from 'node:http'
import crypto from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const WEB_ROOT = path.resolve(HERE, '..')
const GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'

const LINES_ME = ['Thanks everyone for joining.', 'Let me share where we are on the roadmap.', 'The first milestone is the browser recorder.', 'I think we can ship it next week.', 'Can you take the action item on the docs?']
const LINES_THEM = ['Sounds good to me.', 'What about the Jitsi bot?', 'I will write the setup guide today.', 'We should test on a phone as well.', 'Agreed, let us add that to the list.']

const ulid = () => (Date.now().toString(36) + crypto.randomBytes(8).toString('hex')).toUpperCase().slice(0, 26)
const iso = (offsetMs = 0) => new Date(Date.now() + offsetMs).toISOString()

function wavBuffer(seconds = 30, rate = 16000) {
  const n = seconds * rate
  const buf = Buffer.alloc(44 + n * 2)
  buf.write('RIFF', 0); buf.writeUInt32LE(36 + n * 2, 4); buf.write('WAVEfmt ', 8)
  buf.writeUInt32LE(16, 16); buf.writeUInt16LE(1, 20); buf.writeUInt16LE(1, 22)
  buf.writeUInt32LE(rate, 24); buf.writeUInt32LE(rate * 2, 28); buf.writeUInt16LE(2, 32); buf.writeUInt16LE(16, 34)
  buf.write('data', 36); buf.writeUInt32LE(n * 2, 40)
  for (let i = 0; i < n; i++) {
    const t = i / rate
    const v = Math.sin(2 * Math.PI * (220 + 40 * Math.floor(t)) * t) * 0.15 * (0.6 + 0.4 * Math.sin(t * 3))
    buf.writeInt16LE(Math.round(v * 32767), 44 + i * 2)
  }
  return buf
}

function seed() {
  const meetings = new Map()
  const add = (m) => { meetings.set(m.id, { user_notes_md: '', ai_notes_md: '', ai_notes_source: null, attendees: [], tracks: [], segments: [], error: null, ...m }); return meetings.get(m.id) }
  const seg = (m, track, speaker, start, text) => m.segments.push({ seq: m.segments.length, track_id: track, speaker, start_ms: start, end_ms: start + 2600 + text.length * 20, text })

  const a = add({ id: 'MOCKREADY0000000000000001', title: 'Product roadmap review', status: 'ready', source: 'browser', started_at: iso(-3 * 3600e3), created_at: iso(-3 * 3600e3), duration_ms: 1_845_000,
    user_notes_md: '- Ship recorder first\n- Ask Priya about docs\n', ai_notes_md: '## Summary\nThe team reviewed the **Q4 roadmap** and agreed the browser recorder ships first.\n\n## Decisions\n- Recorder ships next week\n- Jitsi bot follows in the next sprint\n\n## Action items\n- [ ] Priya: write the setup guide\n- [x] Sam: confirm the HTTPS address\n- [ ] Me: share the notes\n\nSee [the spec](https://example.com/spec) for details.', ai_notes_source: 'agent',
    tracks: [{ id: 'mic', kind: 'mic', speaker: 'me', label: 'Me', started_offset_ms: 0 }, { id: 'system', kind: 'system', speaker: 'them', label: 'Them', started_offset_ms: 400 }] })
  for (let i = 0; i < 14; i++) {
    const me = i % 2 === 0
    seg(a, me ? 'mic' : 'system', me ? 'me' : 'them', 1000 + i * 4200, (me ? LINES_ME : LINES_THEM)[i % 5] + (i === 6 ? ' This is a longer sentence to check how wrapping looks on a narrow phone screen with several words in a row.' : ''))
  }

  const b = add({ id: 'MOCKREADY0000000000000002', title: 'Weekly engineering sync', status: 'ready', source: 'bot', started_at: iso(-26 * 3600e3), created_at: iso(-26 * 3600e3), duration_ms: 3_420_000,
    attendees: ['Alice', 'Bob', 'Chen'], ai_notes_md: '### Highlights\n1. Latency is down 30%\n2. The release is on Thursday\n\n> Alice: "ship it"', ai_notes_source: 'llm',
    tracks: ['Alice', 'Bob', 'Chen'].map((n, i) => ({ id: `p${i + 1}`, kind: 'participant', speaker: n, label: n, started_offset_ms: i * 1500 })) })
  for (let i = 0; i < 12; i++) { const s = ['Alice', 'Bob', 'Chen'][i % 3]; seg(b, `p${(i % 3) + 1}`, s, 2000 + i * 5000, [...LINES_ME, ...LINES_THEM][i % 10]) }

  add({ id: 'MOCKLIVE00000000000000003', title: 'Design critique (live)', status: 'live', source: 'bot', started_at: iso(-12 * 60e3), created_at: iso(-12 * 60e3), duration_ms: 0,
    tracks: [{ id: 'p1', kind: 'participant', speaker: 'Dana', label: 'Dana', started_offset_ms: 0 }] })
  add({ id: 'MOCKPROC00000000000000004', title: 'Customer call recording', status: 'processing', source: 'upload', started_at: iso(-5 * 60e3), created_at: iso(-5 * 60e3), duration_ms: 0 })
  add({ id: 'MOCKFAIL00000000000000005', title: 'broken-file.m4a', status: 'failed', source: 'upload', created_at: iso(-50 * 3600e3), error: 'ffmpeg could not decode the file (moov atom not found).' })
  for (let i = 0; i < 4; i++) add({ id: `MOCKOLD${i}0000000000000000${i}`.slice(0, 26), title: ['Standup', '1:1 with Sam', 'Retro', 'Sales call: Acme'][i], status: 'ready', source: ['browser', 'bot', 'upload', 'bot'][i], created_at: iso(-(3 + i) * 86400e3), started_at: iso(-(3 + i) * 86400e3), duration_ms: (15 + i * 9) * 60e3 })
  return meetings
}

export async function startMock({ port = 0, password = process.env.MOCK_PASSWORD || 'quill', noLlm = !!process.env.MOCK_NO_LLM } = {}) {
  let meetings = seed()
  const keys = [{ id: 'k1', name: 'Claude Code on laptop', prefix: 'qk_a1b2c3', created_at: iso(-9 * 86400e3), last_used_at: iso(-3600e3), revoked_at: null }]
  const webhooks = [{ id: 'w1', url: 'https://example.com/hooks/quill', events: ['meeting.ended', 'notes.ready'], active: true }]
  const templates = [
    { id: 'general', name: 'General meeting', builtin: true, prompt: 'Summarize the meeting with Summary, Decisions and Action items.' },
    { id: 'standup', name: 'Standup', builtin: true, prompt: 'List what each person did, will do, and blockers.' },
    { id: 'one-on-one', name: '1:1', builtin: true, prompt: 'Capture topics, feedback and follow-ups.' },
    { id: 'custom1', name: 'Customer discovery', builtin: false, prompt: 'Extract pains, current tools and budget signals.' },
  ]
  const bots = new Map() // meeting_id -> {joinedAt, state}
  const sse = new Map()
  const wsConns = new Set()
  const wsStats = {} // meetingId -> trackId -> stats
  const wav = wavBuffer()
  const sessions = new Set()

  const send = (res, status, body, headers = {}) => {
    const data = body === undefined ? '' : JSON.stringify(body)
    res.writeHead(status, { 'Content-Type': 'application/json', ...headers })
    res.end(data)
  }
  const fail = (res, status, code, message) => send(res, status, { error: { code, message } })
  const readBody = (req) => new Promise((r) => { const c = []; req.on('data', (d) => c.push(d)); req.on('end', () => r(Buffer.concat(c))) })
  const readJson = async (req) => { const b = await readBody(req); try { return b.length ? JSON.parse(b.toString()) : {} } catch { return {} } }
  const cookie = (req) => Object.fromEntries((req.headers.cookie || '').split(';').map((c) => c.trim().split('=')).filter((p) => p[0]))
  const authed = (req, url) => {
    const c = cookie(req)
    if (c.quill_session && sessions.has(c.quill_session)) return true
    const a = req.headers.authorization || ''
    return /^Bearer (qk_|mock)/.test(a) || (url && url.searchParams.get('token'))
  }
  const view = (m, full) => {
    const { segments, tracks, ...rest } = m
    const out = { ...rest }
    if (full) { out.tracks = tracks; out.transcript = segments; out.has_audio = tracks.length > 0 }
    return out
  }
  const broadcast = (id, event, data) => { for (const res of sse.get(id) || []) res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`) }
  const setStatus = (m, status) => { m.status = status; broadcast(m.id, 'status', { status }) }

  function addSegment(m, track_id, speaker, text, startMs) {
    const s = { seq: m.segments.length, track_id, speaker, start_ms: startMs, end_ms: startMs + 1800 + text.length * 15, text }
    m.segments.push(s)
    broadcast(m.id, 'segment', { segment: s })
    return s
  }

  // live demo meeting: one new segment every 3 s while someone watches
  let liveTick = 0
  const liveTimer = setInterval(() => {
    const m = meetings.get('MOCKLIVE00000000000000003')
    if (!m || m.status !== 'live' || !(sse.get(m.id) || new Set()).size || m.segments.length > 60) return
    const sp = ['Dana', 'Eli', 'Fran'][liveTick % 3]
    if (!m.tracks.some((t) => t.speaker === sp)) m.tracks.push({ id: `p${liveTick % 3 + 1}`, kind: 'participant', speaker: sp, label: sp, started_offset_ms: 0 })
    addSegment(m, `p${liveTick % 3 + 1}`, sp, [...LINES_ME, ...LINES_THEM][liveTick++ % 10], 1000 + liveTick * 3000)
  }, 3000)

  function stopMeeting(m) {
    if (!['live', 'scheduled'].includes(m.status)) return
    setStatus(m, 'processing')
    setTimeout(() => { m.duration_ms = m.duration_ms || 60_000; m.ended_at = iso(); setStatus(m, 'ready') }, 1200)
  }

  const botState = (b) => { const age = Date.now() - b.joinedAt; return b.left ? 'ended' : age < 2000 ? 'starting' : age < 4000 ? 'lobby' : 'joined' }

  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://localhost')
    const p = url.pathname
    const method = req.method
    try {
      // ---- debug
      if (p === '/__mock/state') return send(res, 200, { ws: wsStats, sockets: wsConns.size, meetings: [...meetings.values()].map((m) => ({ id: m.id, status: m.status, segments: m.segments.length, user_notes_md: m.user_notes_md })) })
      if (p === '/__mock/drop' && method === 'POST') { for (const c of wsConns) c.close(4000); return send(res, 200, { dropped: wsConns.size }) }
      if (p === '/__mock/reset' && method === 'POST') { meetings = seed(); for (const k of Object.keys(wsStats)) delete wsStats[k]; return send(res, 200, { ok: true }) }

      if (p === '/health') return send(res, 200, { status: 'ok', model_ready: !process.env.MOCK_MODEL_LOADING, model_progress: process.env.MOCK_MODEL_LOADING ? 0.42 : undefined, engine: 'parakeet', model: 'parakeet-tdt-0.6b-v3-int8', queue_depth: [...meetings.values()].filter((m) => m.status === 'processing').length, live_meetings: [...meetings.values()].filter((m) => m.status === 'live').length })

      if (p.startsWith('/api/v1/')) {
        const r = p.slice('/api/v1'.length)
        if (r === '/auth/login' && method === 'POST') {
          const b = await readJson(req)
          if (b.password !== password) return fail(res, 401, 'invalid_credentials', 'Wrong password.')
          const sid = crypto.randomBytes(12).toString('hex'); sessions.add(sid)
          return send(res, 200, { ok: true }, { 'Set-Cookie': `quill_session=${sid}; Path=/; HttpOnly; SameSite=Strict` })
        }
        if (r === '/auth/logout' && method === 'POST') { sessions.delete(cookie(req).quill_session); return send(res, 200, { ok: true }, { 'Set-Cookie': 'quill_session=; Path=/; Max-Age=0' }) }
        if (!authed(req, url)) return fail(res, 401, 'unauthorized', 'Sign in required.')

        let m
        if (r === '/meetings' && method === 'GET') {
          const q = (url.searchParams.get('q') || '').toLowerCase(), st = url.searchParams.get('status'), so = url.searchParams.get('source')
          const limit = Number(url.searchParams.get('limit') || 20), off = Number(url.searchParams.get('cursor') || 0)
          const all = [...meetings.values()].filter((x) => (!q || x.title.toLowerCase().includes(q)) && (!st || x.status === st) && (!so || x.source === so))
            .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))
          const page = all.slice(off, off + limit)
          return send(res, 200, { items: page.map((x) => view(x)), has_more: off + limit < all.length, cursor: off + limit < all.length ? String(off + limit) : null })
        }
        if (r === '/meetings' && method === 'POST') {
          const b = await readJson(req)
          const id = ulid()
          meetings.set(id, { id, title: b.title || 'Untitled meeting', status: 'scheduled', source: b.source || 'api', created_at: iso(), started_at: iso(), duration_ms: 0, attendees: b.attendees || [], user_notes_md: '', ai_notes_md: '', ai_notes_source: null, tracks: [], segments: [], error: null })
          return send(res, 201, view(meetings.get(id)))
        }
        const mm = /^\/meetings\/([^/]+)(?:\/(.*))?$/.exec(r)
        if (mm) {
          m = meetings.get(decodeURIComponent(mm[1]))
          const sub = mm[2] || ''
          if (!m) return fail(res, 404, 'not_found', 'Meeting not found.')
          if (sub === '' && method === 'GET') return send(res, 200, view(m, true))
          if (sub === '' && method === 'PATCH') {
            const b = await readJson(req)
            if (typeof b.title === 'string') m.title = b.title
            if (typeof b.user_notes_md === 'string') m.user_notes_md = b.user_notes_md
            if (b.speakers) for (const s of m.segments) if (b.speakers[s.speaker]) s.speaker = b.speakers[s.speaker]
            if (b.speakers) for (const t of m.tracks) if (b.speakers[t.speaker]) { t.speaker = b.speakers[t.speaker]; t.label = t.speaker }
            return send(res, 200, view(m, true))
          }
          if (sub === '' && method === 'DELETE') { meetings.delete(m.id); return send(res, 204) }
          if (sub === 'stop' && method === 'POST') { stopMeeting(m); return send(res, 200, view(m)) }
          if (sub === 'retranscribe' && method === 'POST') { setStatus(m, 'processing'); setTimeout(() => setStatus(m, 'ready'), 1500); return send(res, 202, view(m)) }
          if (sub === 'audio' && method === 'POST') {
            const body = (await readBody(req)).toString('latin1')
            const sp = /name="speaker"\r\n\r\n([^\r]*)/.exec(body)?.[1] || 'Speaker'
            const fn = /filename="([^"]*)"/.exec(body)?.[1] || 'audio'
            const tid = `u${m.tracks.length + 1}`
            m.tracks.push({ id: tid, kind: 'upload', speaker: sp, label: sp, started_offset_ms: 0 })
            setStatus(m, 'processing')
            setTimeout(() => { addSegment(m, tid, sp, `Transcribed text from ${fn}.`, m.segments.length * 4000); m.duration_ms = 30_000; setStatus(m, 'ready') }, 1500)
            return send(res, 202, { job_id: ulid(), track_id: tid })
          }
          const au = /^audio\/(.+)$/.exec(sub)
          if (au && method === 'GET') {
            if (!m.tracks.length && !m.segments.length) return fail(res, 404, 'no_audio', 'No audio for this track.')
            const range = /bytes=(\d*)-(\d*)/.exec(req.headers.range || '')
            const common = { 'Content-Type': 'audio/wav', 'Accept-Ranges': 'bytes' }
            if (range) {
              const s = range[1] ? Number(range[1]) : 0, e = range[2] ? Number(range[2]) : wav.length - 1
              res.writeHead(206, { ...common, 'Content-Range': `bytes ${s}-${e}/${wav.length}`, 'Content-Length': e - s + 1 })
              return res.end(wav.subarray(s, e + 1))
            }
            res.writeHead(200, { ...common, 'Content-Length': wav.length })
            return res.end(wav)
          }
          if (sub === 'transcript' && method === 'GET') {
            const fmt = url.searchParams.get('format') || 'json'
            const t = (ms, sep = '.') => { const d = new Date(ms).toISOString(); return d.slice(11, 19) + sep + d.slice(20, 23) }
            const lines = {
              json: () => JSON.stringify({ meeting_id: m.id, segments: m.segments }),
              md: () => `# ${m.title}\n\n` + m.segments.map((s) => `**${s.speaker}** [${t(s.start_ms)}]: ${s.text}`).join('\n\n'),
              txt: () => m.segments.map((s) => `${s.speaker}: ${s.text}`).join('\n'),
              srt: () => m.segments.map((s, i) => `${i + 1}\n${t(s.start_ms, ',')} --> ${t(s.end_ms, ',')}\n${s.speaker}: ${s.text}\n`).join('\n'),
              vtt: () => 'WEBVTT\n\n' + m.segments.map((s) => `${t(s.start_ms)} --> ${t(s.end_ms)}\n<v ${s.speaker}>${s.text}\n`).join('\n'),
            }
            const f = lines[fmt] || lines.json
            res.writeHead(200, { 'Content-Type': fmt === 'json' ? 'application/json' : fmt === 'vtt' ? 'text/vtt' : 'text/plain; charset=utf-8' })
            return res.end(f())
          }
          if (sub === 'events' && method === 'GET') {
            res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache', Connection: 'keep-alive' })
            res.write(': connected\n\n')
            if (!sse.has(m.id)) sse.set(m.id, new Set())
            sse.get(m.id).add(res)
            const ka = setInterval(() => res.write(': ka\n\n'), 15000)
            req.on('close', () => { clearInterval(ka); sse.get(m.id)?.delete(res) })
            return
          }
          if (sub === 'notes/generate' && method === 'POST') {
            if (noLlm) return fail(res, 409, 'no_llm', 'No LLM configured. Ask your agent to write notes via MCP.')
            const b = await readJson(req)
            const t = templates.find((x) => x.id === b.template_id) || templates[0]
            await new Promise((r2) => setTimeout(r2, 700))
            m.ai_notes_md = `## ${t.name}\n\nGenerated from ${m.segments.length} transcript segments.\n\n- **Decision:** ship the recorder first\n- **Next:** write the guide\n- [ ] Follow up with the team`
            m.ai_notes_source = 'llm'
            broadcast(m.id, 'notes', { source: 'llm' })
            return send(res, 200, view(m, true))
          }
          if (sub === 'notes' && method === 'PUT') { const b = await readJson(req); m.ai_notes_md = b.markdown || ''; m.ai_notes_source = b.source || 'user'; return send(res, 200, view(m, true)) }
        }
        if (r === '/search' && method === 'GET') {
          const q = (url.searchParams.get('q') || '').toLowerCase()
          const hits = []
          if (q) for (const x of meetings.values()) for (const s of x.segments) {
            const i = s.text.toLowerCase().indexOf(q)
            if (i >= 0) hits.push({ meeting_id: x.id, title: x.title, seq: s.seq, start_ms: s.start_ms, speaker: s.speaker, snippet: `${s.text.slice(0, i)}<mark>${s.text.slice(i, i + q.length)}</mark>${s.text.slice(i + q.length)}` })
          }
          return send(res, 200, { hits: hits.slice(0, Number(url.searchParams.get('limit') || 10)) })
        }
        if (r === '/bot/join' && method === 'POST') {
          const b = await readJson(req)
          let host = ''
          try { host = new URL(b.url).hostname } catch { return fail(res, 400, 'bad_url', 'That is not a valid URL.') }
          if (!/jitsi/.test(host)) return fail(res, 400, 'domain_not_allowed', `Host ${host} is not in the allowed Jitsi domains.`)
          const id = ulid()
          meetings.set(id, { id, title: b.title || 'Jitsi meeting', status: 'scheduled', source: 'bot', created_at: iso(), started_at: iso(), duration_ms: 0, attendees: [], user_notes_md: '', ai_notes_md: '', ai_notes_source: null, tracks: [], segments: [], error: null })
          bots.set(id, { joinedAt: Date.now(), left: false })
          return send(res, 201, view(meetings.get(id)))
        }
        if (r === '/bot' && method === 'GET') return send(res, 200, { enabled: true, domains: ['jitsi.example.com'], bots: [...bots].filter(([, b]) => !b.left).map(([id, b]) => ({ meeting_id: id, state: botState(b) })) })
        const bl = /^\/bot\/([^/]+)\/leave$/.exec(r)
        if (bl && method === 'POST') { const b = bots.get(bl[1]); if (b) b.left = true; const x = meetings.get(bl[1]); if (x) stopMeeting(x); return send(res, 200, { ok: true }) }

        if (r === '/keys' && method === 'GET') return send(res, 200, { items: keys })
        if (r === '/keys' && method === 'POST') {
          const b = await readJson(req)
          const secret = `qk_${crypto.randomBytes(18).toString('hex')}`
          keys.push({ id: ulid(), name: b.name || 'key', prefix: secret.slice(0, 9), created_at: iso(), last_used_at: null, revoked_at: null })
          return send(res, 201, { ...keys.at(-1), key: secret })
        }
        const kd = /^\/keys\/([^/]+)$/.exec(r)
        if (kd && method === 'DELETE') { const k = keys.find((x) => x.id === kd[1]); if (k) k.revoked_at = iso(); return send(res, 204) }

        if (r === '/webhooks' && method === 'GET') return send(res, 200, { items: webhooks })
        if (r === '/webhooks' && method === 'POST') { const b = await readJson(req); webhooks.push({ id: ulid(), url: b.url, events: b.events || [], active: true }); return send(res, 201, { ...webhooks.at(-1), secret: b.secret || `whsec_${crypto.randomBytes(12).toString('hex')}` }) }
        const wd = /^\/webhooks\/([^/]+)$/.exec(r)
        if (wd && method === 'DELETE') { const i = webhooks.findIndex((x) => x.id === wd[1]); if (i >= 0) webhooks.splice(i, 1); return send(res, 204) }

        if (r === '/templates' && method === 'GET') return send(res, 200, { items: templates })
        if (r === '/templates' && method === 'POST') { const b = await readJson(req); templates.push({ id: ulid(), name: b.name, prompt: b.prompt, builtin: false }); return send(res, 201, templates.at(-1)) }
        const td = /^\/templates\/([^/]+)$/.exec(r)
        if (td) {
          const i = templates.findIndex((x) => x.id === td[1])
          if (i < 0) return fail(res, 404, 'not_found', 'Template not found.')
          if (templates[i].builtin) return fail(res, 403, 'read_only', 'Built-in templates are read-only.')
          if (method === 'PUT') { Object.assign(templates[i], await readJson(req)); return send(res, 200, templates[i]) }
          if (method === 'DELETE') { templates.splice(i, 1); return send(res, 204) }
        }
        return fail(res, 404, 'not_found', `No route for ${method} ${p}`)
      }

      // ---- static files with index.html fallback
      let file = path.join(WEB_ROOT, decodeURIComponent(p))
      if (!file.startsWith(WEB_ROOT) || file.startsWith(HERE)) file = path.join(WEB_ROOT, 'index.html')
      if (!fs.existsSync(file) || fs.statSync(file).isDirectory()) file = path.join(WEB_ROOT, 'index.html')
      const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.json': 'application/json' }
      res.writeHead(200, { 'Content-Type': `${types[path.extname(file)] || 'application/octet-stream'}; charset=utf-8`, 'Cache-Control': 'no-store' })
      fs.createReadStream(file).pipe(res)
    } catch (err) {
      console.error(err)
      if (!res.headersSent) fail(res, 500, 'internal', String(err.message || err))
    }
  })

  // ---------------------------------------------------------------- WebSocket (live ingest)
  server.on('upgrade', (req, socket) => {
    const url = new URL(req.url, 'http://localhost')
    const mm = /^\/api\/v1\/meetings\/([^/]+)\/live$/.exec(url.pathname)
    const m = mm && meetings.get(decodeURIComponent(mm[1]))
    if (!mm || !m || !authed(req, url)) { socket.write(`HTTP/1.1 ${m ? 401 : 404} Not Found\r\nConnection: close\r\n\r\n`); return socket.destroy() }
    const accept = crypto.createHash('sha1').update(req.headers['sec-websocket-key'] + GUID).digest('base64')
    socket.write(`HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ${accept}\r\n\r\n`)
    const conn = new WsConn(socket)
    wsConns.add(conn)
    const stats = (wsStats[m.id] ||= {})
    const tracks = new Map()
    conn.send({ type: 'ready', meeting_id: m.id, session_offset_ms: 0 })
    conn.onText = (raw) => {
      let msg
      try { msg = JSON.parse(raw) } catch { return conn.send({ type: 'error', code: 'bad_json', message: 'Invalid JSON.' }) }
      if (msg.type === 'track') {
        tracks.set(msg.track_id, msg)
        const st = (stats[msg.track_id] ||= { registrations: 0, frames: 0, bytes: 0, badFrames: 0, unknownTrack: 0, sampleRate: null, kind: null, speaker: null, label: null, samples: 0, ended: false })
        Object.assign(st, { registrations: st.registrations + 1, sampleRate: msg.sample_rate, kind: msg.kind, speaker: msg.speaker, label: msg.label, ended: false })
        if (!m.tracks.some((t) => t.id === msg.track_id)) m.tracks.push({ id: msg.track_id, kind: msg.kind, speaker: msg.speaker, label: msg.label, started_offset_ms: 0 })
        if (m.status === 'scheduled') { m.status = 'live'; m.started_at = iso(); conn.send({ type: 'status', status: 'live', lag_ms: 0 }) }
      } else if (msg.type === 'track_end') { if (stats[msg.track_id]) stats[msg.track_id].ended = true }
      else if (msg.type === 'note') m.user_notes_md = msg.markdown || ''
      else if (msg.type === 'ping') conn.send({ type: 'pong' })
      else if (msg.type === 'stop') {
        conn.send({ type: 'status', status: 'processing', lag_ms: 0 }); setStatus(m, 'processing')
        setTimeout(() => { m.duration_ms = Math.max(...Object.values(stats).map((s) => s.samples / 16), 1000); setStatus(m, 'ready'); conn.send({ type: 'status', status: 'ready', lag_ms: 0 }); conn.close(1000) }, 2500)
      }
    }
    conn.onBinary = (buf) => {
      const n = buf[0]
      const id = buf.subarray(1, 1 + n).toString('utf8')
      const st = stats[id]
      if (!st || !tracks.has(id)) { (stats._unknown ||= { count: 0 }).count++; return }
      const pcm = buf.length - 1 - n
      if (n === 0 || pcm <= 0 || pcm % 2 !== 0 || pcm < 640 || pcm > 16000) { st.badFrames++; return }
      st.frames++; st.bytes += pcm; st.samples += pcm / 2
      // emit a fake transcript segment for every ~1.6 s of audio on the track
      if (st.bytes - (st.emitted || 0) >= 51200) {
        st.emitted = st.bytes
        const t = tracks.get(id)
        const lines = t.kind === 'mic' ? LINES_ME : LINES_THEM
        const s = addSegment(m, id, t.speaker, lines[m.segments.length % lines.length], Math.round((st.samples / 16) - 1600))
        conn.send({ type: 'segment', segment: s })
      }
    }
    conn.onClose = () => wsConns.delete(conn)
  })

  await new Promise((r) => server.listen(port, '127.0.0.1', r))
  const actual = server.address().port
  return {
    url: `http://127.0.0.1:${actual}`, port: actual, password, wsStats, meetings: () => meetings,
    close: () => new Promise((r) => { clearInterval(liveTimer); for (const c of wsConns) c.socket.destroy(); for (const set of sse.values()) for (const x of set) x.end(); server.close(() => r()); server.closeAllConnections?.() }),
  }
}

class WsConn {
  constructor(socket) {
    this.socket = socket; this.buf = Buffer.alloc(0); this.frag = []; this.fragOp = 0
    socket.on('data', (d) => { this.buf = Buffer.concat([this.buf, d]); this.parse() })
    socket.on('close', () => this.onClose && this.onClose())
    socket.on('error', () => {})
  }
  parse() {
    for (;;) {
      const b = this.buf
      if (b.length < 2) return
      const fin = !!(b[0] & 0x80), op = b[0] & 0x0f, masked = !!(b[1] & 0x80)
      let len = b[1] & 0x7f, off = 2
      if (len === 126) { if (b.length < 4) return; len = b.readUInt16BE(2); off = 4 }
      else if (len === 127) { if (b.length < 10) return; len = Number(b.readBigUInt64BE(2)); off = 10 }
      if (b.length < off + (masked ? 4 : 0) + len) return
      let payload
      if (masked) { const mask = b.subarray(off, off + 4); off += 4; payload = Buffer.from(b.subarray(off, off + len)); for (let i = 0; i < len; i++) payload[i] ^= mask[i & 3] }
      else payload = b.subarray(off, off + len)
      this.buf = b.subarray(off + len)
      if (op === 8) { this.close(1000); return }
      if (op === 9) { this.frame(10, payload); continue }
      if (op === 10) continue
      if (op === 1 || op === 2) { this.fragOp = op; this.frag = [payload] } else if (op === 0) this.frag.push(payload)
      if (fin) {
        const full = Buffer.concat(this.frag); this.frag = []
        if (this.fragOp === 1) this.onText && this.onText(full.toString('utf8')); else this.onBinary && this.onBinary(full)
      }
    }
  }
  frame(op, payload) {
    if (this.socket.destroyed) return
    const n = payload.length
    const head = n < 126 ? Buffer.from([0x80 | op, n]) : n < 65536 ? Buffer.from([0x80 | op, 126, n >> 8, n & 255]) : (() => { const h = Buffer.alloc(10); h[0] = 0x80 | op; h[1] = 127; h.writeBigUInt64BE(BigInt(n), 2); return h })()
    this.socket.write(Buffer.concat([head, payload]))
  }
  send(obj) { this.frame(1, Buffer.from(JSON.stringify(obj))) }
  close(code = 1000) {
    if (this.closing) return
    this.closing = true
    const p = Buffer.alloc(2); p.writeUInt16BE(code); this.frame(8, p); setTimeout(() => this.socket.end(), 50)
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const i = process.argv.indexOf('--port')
  const port = i > 0 ? Number(process.argv[i + 1]) : 8099
  const mock = await startMock({ port })
  console.log(`Quill mock server on ${mock.url}  (password: ${mock.password})`)
}
