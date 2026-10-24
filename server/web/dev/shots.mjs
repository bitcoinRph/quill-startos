#!/usr/bin/env node
// Dev helper: screenshots of every view at desktop and phone width, light and dark.
//   node shots.mjs [outDir]
import { createRequire } from 'node:module'
import fs from 'node:fs'
import { startMock } from './mock_server.mjs'
const { chromium } = createRequire(import.meta.url)('playwright')

const out = process.argv[2] || '/tmp/claude-0/-home-user/48c95ade-e295-507e-98f2-50d9064df418/scratchpad/ui-shots'
fs.mkdirSync(out, { recursive: true })
const mock = await startMock({ port: 0 })
const browser = await chromium.launch({ args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'] })
const sizes = { desktop: { width: 1280, height: 800 }, phone: { width: 390, height: 844 } }

for (const [sname, viewport] of Object.entries(sizes)) {
  for (const scheme of ['light', 'dark']) {
    const ctx = await browser.newContext({ viewport, colorScheme: scheme, permissions: ['microphone'], deviceScaleFactor: sname === 'phone' ? 2 : 1, hasTouch: sname === 'phone' })
    const page = await ctx.newPage()
    const shot = async (name, full = false) => { await page.waitForTimeout(250); await page.screenshot({ path: `${out}/${name}-${sname}-${scheme}.png`, fullPage: full }) }
    const go = async (hash, sel) => { await page.goto(`${mock.url}/${hash}`); if (sel) await page.waitForSelector(sel, { timeout: 8000, state: 'attached' }) }
    const overflow = async (name) => {
      const o = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
      if (o > 0) console.log(`HORIZONTAL OVERFLOW ${o}px on ${name} (${sname}/${scheme})`)
    }

    await page.goto(`${mock.url}/`)
    await page.waitForSelector('input[type=password]')
    await shot('01-signin')
    await page.fill('input[type=password]', 'quill'); await page.click('button[type=submit]')
    await page.waitForSelector('.mrow')
    await shot('02-library'); await overflow('library')
    await page.fill('#q', 'roadmap'); await page.waitForSelector('.hit'); await shot('03-library-search'); await page.fill('#q', '')

    await go('#/meeting/MOCKREADY0000000000000001', '.seg')
    await shot('04-meeting', true); await overflow('meeting')
    await page.click('[role=tab]:has-text("My notes") >> visible=true'); await shot('04b-meeting-mynotes')
    await page.click('[role=tab]:has-text("AI notes") >> visible=true'); await shot('05-meeting-ai'); await overflow('meeting-ai')
    if (sname === 'phone') { await page.click('[role=tab]:has-text("Transcript") >> visible=true'); await shot('05b-meeting-transcript') }
    await page.click('.seg .ts >> nth=3'); await page.waitForTimeout(600); await shot('06-meeting-playing'); await overflow('meeting-playing')
    await go('#/meeting/MOCKLIVE00000000000000003', '.badge.live'); if (sname === 'phone') await page.click('[role=tab]:has-text("Transcript") >> visible=true'); await page.waitForTimeout(7000); await shot('07-meeting-live'); await overflow('meeting-live')
    await go('#/meeting/MOCKFAIL00000000000000005', '.badge.failed'); await shot('08-meeting-failed')

    await go('#/new/bot', 'input[type=url]'); await shot('09-new-bot'); await overflow('new-bot')
    await go('#/new/recorder', 'text=Start recording'); await shot('10-new-recorder'); await overflow('new-recorder')
    await page.check('input[value=tab]').catch(() => {}); await shot('11-new-recorder-tab')
    await page.check('input[value=mic]')
    await page.click('button:has-text("Start recording")'); await page.waitForSelector('.rec-bar'); await page.fill('.rec-grid textarea', 'Ask about the budget\n- follow up with Priya'); await page.waitForTimeout(4500)
    await shot('12-recording'); await overflow('recording')
    await page.click('button:has-text("Stop recording")'); await page.waitForURL(/#\/meeting\//)
    await go('#/new/upload', '.dropzone'); await page.setInputFiles('#file-input', [{ name: 'mic-track.m4a', mimeType: 'audio/mp4', buffer: Buffer.alloc(2_400_000) }, { name: 'zoom-other-side-recording-final.wav', mimeType: 'audio/wav', buffer: Buffer.alloc(52_000_000 / 10) }]); await shot('13-new-upload'); await overflow('upload')

    for (const t of ['general', 'agents', 'keys', 'webhooks', 'templates', 'status']) {
      await go(`#/settings/${t}`, '[role=tabpanel] .card')
      if (t === 'keys') { await page.fill('#key-name', 'Codex'); await page.click('button:has-text("Create key")'); await page.waitForSelector('.secret') }
      await page.waitForTimeout(300)
      await shot(`14-settings-${t}`, true); await overflow(`settings-${t}`)
    }
    await ctx.close()
  }
}
// insecure-context notice
const ctx = await browser.newContext({ viewport: sizes.desktop })
await ctx.addInitScript(() => Object.defineProperty(window, 'isSecureContext', { value: false }))
const page = await ctx.newPage()
await page.goto(`${mock.url}/`); await page.fill('input[type=password]', 'quill'); await page.click('button[type=submit]'); await page.waitForSelector('.mrow')
await page.goto(`${mock.url}/#/new/recorder`); await page.waitForSelector('text=Start recording'); await page.waitForTimeout(300)
await page.screenshot({ path: `${out}/15-insecure-desktop-light.png` })
await browser.close(); await mock.close()
console.log('screenshots in', out)
