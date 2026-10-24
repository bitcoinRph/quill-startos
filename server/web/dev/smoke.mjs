#!/usr/bin/env node
// Playwright smoke test for the Quill web UI.
//   node smoke.mjs                          starts the mock server in-process
//   SMOKE_BASE_URL=http://127.0.0.1:8080 SMOKE_PASSWORD=secret node smoke.mjs     runs against a real server
// Needs `require('playwright')` to resolve (Chromium is pre-installed in this environment).
import { createRequire } from 'node:module'
import { startMock } from './mock_server.mjs'

const require = createRequire(import.meta.url)
const { chromium } = require('playwright')

const REAL = !!process.env.SMOKE_BASE_URL
const PASSWORD = process.env.SMOKE_PASSWORD || 'quill'
let mock = null
let base = process.env.SMOKE_BASE_URL
if (!REAL) { mock = await startMock({ port: 0, password: PASSWORD }); base = mock.url }

const results = []
const check = (name, ok, extra = '') => { results.push({ name, ok }); console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${extra ? `  (${extra})` : ''}`) }

const browser = await chromium.launch({ args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'] })
const context = await browser.newContext({ viewport: { width: 1280, height: 800 }, permissions: ['microphone'] })
const page = await context.newPage()

const consoleErrors = []
page.on('pageerror', (e) => consoleErrors.push(`pageerror: ${e.message}`))
page.on('console', (m) => {
  if (m.type() !== 'error') return
  const t = m.text()
  // Chromium logs every non-2xx response; 401 (sign-in) and 409 (no LLM) are expected in this test.
  if (/Failed to load resource.*\b(401|409|400)\b/.test(t)) return
  consoleErrors.push(`console.error: ${t}`)
})

const wsFrames = { binary: [], text: [] }
const sockets = []
page.on('websocket', (ws) => {
  sockets.push(ws.url())
  ws.on('framesent', (ev) => (typeof ev.payload === 'string' ? wsFrames.text.push(ev.payload) : wsFrames.binary.push(Buffer.from(ev.payload))))
})

const waitText = (sel, re, timeout = 8000) => page.waitForFunction(([s, r]) => [...document.querySelectorAll(s)].some((e) => new RegExp(r).test(e.textContent)), [sel, re.source], { timeout })

try {
  // ---- sign in
  await page.goto(`${base}/`)
  await page.waitForURL(/#\/signin/, { timeout: 8000 })
  check('unauthenticated visit lands on sign-in', true)
  await page.fill('input[type=password]', 'definitely-wrong')
  await page.click('button[type=submit]')
  await page.waitForSelector('.notice.error', { timeout: 5000 })
  check('wrong password shows an error', true)
  await page.fill('input[type=password]', PASSWORD)
  await page.click('button[type=submit]')
  await page.waitForSelector('.topbar', { timeout: 8000 })
  check('sign in succeeds', true)

  // ---- library
  await page.waitForSelector('.mrow, .empty', { timeout: 8000 })
  const rows = await page.locator('.mrow').count()
  if (REAL) check('library renders (rows or empty state)', true, `${rows} rows`)
  else {
    check('library lists meetings', rows >= 5, `${rows} rows`)
    await page.fill('#q', 'roadmap')
    await page.waitForSelector('.hit', { timeout: 5000 })
    check('transcript search shows highlighted hits', (await page.locator('.hit mark').count()) > 0)
    await page.fill('#q', '')
    await page.waitForFunction(() => document.querySelectorAll('.mrow').length >= 5)
  }

  // ---- meeting
  if (!REAL) {
    await page.click('.mrow:has-text("Product roadmap review")')
    await page.waitForSelector('.seg', { timeout: 8000 })
    check('meeting transcript renders', (await page.locator('.seg').count()) >= 10)
    check('AI notes markdown rendered', (await page.locator('.p-ai .md strong, .p-ai .md h3').count()) > 0)
    await page.click('.seg .ts >> nth=2')
    await page.waitForFunction(() => document.querySelector('.player audio')?.src.includes('/audio/'), null, { timeout: 5000 })
    check('timestamp click loads the track audio', true)
    await page.waitForFunction(() => document.querySelector('.player audio').readyState >= 1, null, { timeout: 5000 })
    const t = await page.evaluate(() => document.querySelector('.player audio').currentTime)
    check('audio seeks to the segment', t > 3, `currentTime=${t.toFixed(2)}`)
    // notes autosave
    await page.click('.mgrid .tab-wide, [role=tab]:has-text("My notes") >> visible=true').catch(() => {})
    await page.fill('textarea.notes-area', 'smoke test note')
    await page.waitForFunction(() => document.querySelector('.savestate')?.textContent === 'Saved', null, { timeout: 5000 })
    check('user notes autosave (PATCH)', true)
    // generate -> in mock, works unless MOCK_NO_LLM
    await page.click('[role=tab]:has-text("AI notes") >> visible=true')
    await page.click('button:has-text("Regenerate")')
    await page.waitForFunction(() => /Generated from/.test(document.querySelector('.p-ai .md')?.textContent || ''), null, { timeout: 8000 })
    check('generate notes with a template', true)
    // live meeting streams over SSE
    await page.goto(`${base}/#/meeting/MOCKLIVE00000000000000003`)
    await page.waitForSelector('.badge.live', { timeout: 5000 })
    const before = await page.locator('.seg').count()
    await page.waitForFunction((n) => document.querySelectorAll('.seg').length > n, before, { timeout: 9000 })
    check('live meeting appends segments via SSE', true)
    await page.goto(`${base}/#/`)
  }

  // ---- browser recorder with fake mic
  await page.goto(`${base}/#/new/recorder`)
  await page.waitForSelector('text=Start recording')
  check('page is a secure context', await page.evaluate(() => window.isSecureContext))
  await page.fill('.card input.input', 'Smoke recording')
  await page.click('button:has-text("Start recording")')
  await page.waitForSelector('.rec-bar', { timeout: 10000 })
  check('recorder started', true)
  await page.waitForTimeout(3200)
  const bin = wsFrames.binary
  const ok = bin.filter((b) => { const n = b[0]; return n === 3 && b.subarray(1, 1 + n).toString() === 'mic' && (b.length - 1 - n) % 2 === 0 && b.length - 1 - n >= 640 })
  check('PCM frames reach the WebSocket with the right header', ok.length >= 20 && ok.length === bin.length, `${ok.length}/${bin.length} frames`)
  const samples = ok.reduce((a, b) => a + (b.length - 4) / 2, 0)
  check('about 3 s of 16 kHz audio sent', samples > 16000 * 2.4 && samples < 16000 * 4.5, `${(samples / 16000).toFixed(2)} s`)
  const reg = wsFrames.text.map((t) => JSON.parse(t)).find((m) => m.type === 'track')
  check('track registration message is correct', reg && reg.track_id === 'mic' && reg.kind === 'mic' && reg.speaker === 'me' && reg.sample_rate === 16000, JSON.stringify(reg))
  const peak = await page.evaluate(() => Math.max(...Object.values(window.__quillRecorder.levels)))
  check('level meter receives audio level', peak > 0, `peak=${peak.toFixed(3)}`)
  if (!REAL) {
    await page.waitForFunction(() => document.querySelectorAll('.rec-scroll .seg').length > 0, null, { timeout: 8000 })
    check('live segments from the server show in the recorder', true)
    // reconnect: drop the socket and watch it re-register and keep streaming
    const before = wsFrames.binary.length
    await page.evaluate(() => fetch('/__mock/drop', { method: 'POST' }))
    await page.waitForFunction(() => /Reconnecting/.test(document.querySelector('.rec-bar')?.textContent || ''), null, { timeout: 3000 }).catch(() => {})
    await page.waitForFunction(() => window.__quillRecorder.conn === 'open', null, { timeout: 10000 })
    await page.waitForTimeout(800)
    const regs = wsFrames.text.map((t) => JSON.parse(t)).filter((m) => m.type === 'track').length
    check('socket drop: reconnects, re-registers the track and keeps sending', regs >= 2 && wsFrames.binary.length > before + 3, `registrations=${regs}`)
    await page.fill('.rec-grid textarea', 'notes typed during the recording')
    await page.waitForTimeout(1300)
    const st = await (await fetch(`${base}/__mock/state`)).json()
    const stats = Object.values(st.ws)[0]
    check('mock saw frames, no bad headers', stats.mic.frames > 20 && stats.mic.badFrames === 0 && stats.mic.sampleRate === 16000, JSON.stringify(stats.mic))
  }
  await page.click('button:has-text("Stop recording")')
  await page.waitForURL(/#\/meeting\//, { timeout: 10000 })
  check('stop navigates to the meeting page', true)
  if (!REAL) {
    await page.waitForSelector('.badge.ready', { timeout: 8000 })
    check('meeting becomes ready after stop', true)
    const st = await (await fetch(`${base}/__mock/state`)).json()
    const mine = st.meetings.find((m) => /smoke|notes typed/.test(m.user_notes_md)) 
    check('notes typed while recording were saved', !!mine)
  }

  // ---- upload tab, bot tab render
  await page.goto(`${base}/#/new/upload`)
  await page.waitForSelector('.dropzone')
  await page.setInputFiles('#file-input', { name: 'a.wav', mimeType: 'audio/wav', buffer: Buffer.alloc(4096) })
  await page.waitForSelector('.filerow')
  check('upload tab accepts a file and asks for a speaker label', true)
  if (!REAL) {
    await page.click('button:has-text("Upload and transcribe")')
    await page.waitForURL(/#\/meeting\//, { timeout: 8000 })
    check('upload creates a meeting and navigates to it', true)
  }
  await page.goto(`${base}/#/new/bot`)
  await page.waitForSelector('input[type=url]')
  if (!REAL) {
    await page.fill('input[type=url]', 'https://jitsi.example.com/Room')
    await page.click('button:has-text("Send the bot")')
    await page.waitForSelector('.badge.joined, .badge.lobby, .badge.starting', { timeout: 6000 })
    check('bot join shows bot state', true)
  }

  // ---- settings
  await page.goto(`${base}/#/settings/keys`)
  await page.fill('#key-name', 'smoke key')
  await page.click('button:has-text("Create key")')
  await page.waitForSelector('.secret code', { timeout: 5000 })
  const secret = await page.textContent('.secret code')
  check('API key secret shown once', /^qk_/.test(secret) || REAL, secret.slice(0, 8))
  await page.goto(`${base}/#/settings/agents`)
  const snip = await page.textContent('.snippet pre')
  check('agent snippet uses the page origin', snip.includes(`${new URL(base).origin}/mcp`) && snip.includes('Authorization: Bearer'))
  await page.goto(`${base}/#/settings/status`)
  await page.waitForSelector('.kv')
  check('status panel renders /health', /parakeet|engine|Transcription/i.test(await page.textContent('.kv')))
  await page.goto(`${base}/#/settings/templates`)
  await page.waitForSelector('.list li')
  check('templates list renders', true)
  await page.goto(`${base}/#/settings/webhooks`)
  await page.waitForSelector('form')
  check('webhooks panel renders', true)

  await page.waitForTimeout(300)
  check('no console errors', consoleErrors.length === 0, consoleErrors.join(' | '))
} catch (e) {
  check(`unexpected failure: ${e.message.split('\n')[0]}`, false)
  console.error(e)
  await page.screenshot({ path: process.env.SMOKE_FAIL_SHOT || '/tmp/smoke-fail.png' }).catch(() => {})
  if (consoleErrors.length) console.error('console errors:', consoleErrors)
} finally {
  await browser.close()
  if (mock) await mock.close()
}
const failed = results.filter((r) => !r.ok).length
console.log(`\n${results.length - failed}/${results.length} checks passed${REAL ? ' (real server)' : ' (mock server)'}`)
process.exit(failed ? 1 : 0)
