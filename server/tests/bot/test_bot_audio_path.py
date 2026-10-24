"""The in-page audio path: remote WebRTC stream -> AudioWorklet -> frames on the live socket.

No Jitsi involved. A 'remote' stream is made by sending an oscillator through a
local RTCPeerConnection pair (so it is a genuine remote stream, which is what
triggers Chromium's need for a media element), then the real pipeline and
ingest client send it to the double of the live endpoint.
"""

from __future__ import annotations

import asyncio

import numpy as np
import pytest

from botkit import TOKEN

pytestmark = pytest.mark.browser

LOOPBACK = """
async function loopbackStream(freq, amp = 0.5) {
  const ctx = new AudioContext();
  const osc = ctx.createOscillator(); osc.frequency.value = freq;
  const gain = ctx.createGain(); gain.gain.value = amp;
  const dest = ctx.createMediaStreamDestination();
  osc.connect(gain); gain.connect(dest); osc.start();
  const a = new RTCPeerConnection(), b = new RTCPeerConnection();
  a.onicecandidate = (e) => e.candidate && b.addIceCandidate(e.candidate);
  b.onicecandidate = (e) => e.candidate && a.addIceCandidate(e.candidate);
  const got = new Promise((r) => (b.ontrack = (e) => r(e.streams[0])));
  dest.stream.getTracks().forEach((t) => a.addTrack(t, dest.stream));
  await a.setLocalDescription(await a.createOffer());
  await b.setRemoteDescription(a.localDescription);
  await b.setLocalDescription(await b.createAnswer());
  await a.setRemoteDescription(b.localDescription);
  return got;
}
"""

SEND = (
    """
async ({base, token, meeting, ids, freqs, seconds, ctxRate, attach}) => {
"""
    + LOOPBACK
    + """
  const qs = '?internal=' + encodeURIComponent(token);
  const ingest = new QuillProtocol.LiveIngest(base.replace('http', 'ws') + '/api/v1/meetings/' + meeting + '/live' + qs, {});
  await ingest.open();
  const pipeline = new QuillPipeline.AudioPipeline((id, pcm) => ingest.pcm(id, pcm), { sampleRate: ctxRate || undefined });
  await pipeline.init(base + '/bot/page/pcm-worklet.js' + qs);
  const info = { ctxRate: pipeline.sampleRate };
  for (let i = 0; i < ids.length; i++) {
    const stream = await loopbackStream(freqs[i]);
    ingest.registerTrack({ trackId: ids[i], kind: 'participant', speaker: 'Spk' + i, participantId: ids[i] });
    pipeline.addStream(ids[i], stream);
    info['muted_' + ids[i]] = pipeline.tracks.get(ids[i]).el.muted;
    info['attached_' + ids[i]] = pipeline.tracks.get(ids[i]).el.srcObject === stream;
  }
  await new Promise((r) => setTimeout(r, seconds * 1000));
  for (const id of ids) { await pipeline.removeTrack(id); ingest.endTrack(id); }
  ingest.stop();
  await new Promise((r) => setTimeout(r, 300));
  info.sent = ingest.framesSent; info.dropped = ingest.droppedFrames;
  ingest.close(); await pipeline.close();
  return info;
}
"""
)


async def run_send(browser, double, **kw):
    page = await browser.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    await page.goto(f"{double.base}/test/blank")
    for name in ("protocol.js", "pipeline.js"):
        await page.add_script_tag(url=f"{double.base}/bot/page/{name}?internal={TOKEN}")
    args = dict(base=double.base, token=TOKEN, meeting="m1", ids=["p1"], freqs=[440], seconds=3, ctxRate=0, attach=True)
    args.update(kw)
    info = await page.evaluate(SEND, args)
    await page.close()
    assert not errors, errors
    return info


def peak_hz(samples, skip_s=0.5):
    x = np.asarray(samples[int(16000 * skip_s):], dtype=float)
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return float(np.fft.rfftfreq(len(x), 1 / 16000)[spec.argmax()])


async def wait_closed(double, mid):
    await asyncio.wait_for(double.rec.closed[mid].wait(), 10)


@pytest.mark.parametrize("ctx_rate", [0, 48000, 16000])
async def test_frames_follow_the_wire_format(browser, quill_double, ctx_rate):
    info = await run_send(browser, quill_double, ctxRate=ctx_rate, seconds=3)
    await wait_closed(quill_double, "m1")
    rec = quill_double.rec
    if ctx_rate:
        assert info["ctxRate"] == ctx_rate

    # text frames: register, end, stop, in that order
    assert rec.types("m1") == ["track", "track_end", "stop"]
    track = rec.texts["m1"][0]
    assert track == {"type": "track", "track_id": "p1", "kind": "participant", "speaker": "Spk0",
                     "label": "Spk0", "participant_id": "p1", "sample_rate": 16000}

    # binary frames: 1 byte id length + id + PCM16LE
    frames = rec.frames["m1"]
    assert frames and len(frames) == info["sent"] and info["dropped"] == 0
    for f in frames:
        assert f[0] == 2 and f[1:3] == b"p1"
        assert (len(f) - 3) % 2 == 0
    sizes = [(len(f) - 3) // 2 for f in frames]
    assert max(sizes) <= 640 and sizes.count(640) >= len(sizes) - 2, "40 ms chunks (the last one may be partial)"
    assert all(20 * 16 <= s for s in sizes[:-1]), "frames of 20 ms to 500 ms"

    samples = rec.decoded("m1")["p1"]
    seconds = len(samples) / 16000
    assert 2.5 < seconds < 3.6, seconds
    assert abs(peak_hz(samples) - 440) < 6
    assert max(abs(s) for s in samples) > 8000, "audio must not be silent"
    assert max(abs(s) for s in samples) < 20000, "no clipping at half scale"


async def test_remote_stream_is_attached_to_a_muted_element(browser, quill_double):
    info = await run_send(browser, quill_double, seconds=1)
    assert info["muted_p1"] is True and info["attached_p1"] is True


async def test_two_tracks_stay_separate(browser, quill_double):
    await run_send(browser, quill_double, ids=["a1", "b2"], freqs=[440, 1000], seconds=3)
    await wait_closed(quill_double, "m1")
    pcm = quill_double.rec.decoded("m1")
    assert abs(peak_hz(pcm["a1"]) - 440) < 6
    assert abs(peak_hz(pcm["b2"]) - 1000) < 8
    assert quill_double.rec.types("m1").count("track") == 2


async def test_chromium_needs_the_element_for_remote_audio(browser, quill_double):
    """Pins down the behavior the pipeline works around (crbug 933677).

    Without a media element a remote WebRTC stream is silent in Web Audio. If a
    future Chromium fixes this, the workaround becomes redundant but harmless.
    """
    page = await browser.new_page()
    await page.goto(f"{quill_double.base}/test/blank")
    peaks = await page.evaluate(
        "async () => {"
        + LOOPBACK
        + """
          const out = {};
          for (const attach of [false, true]) {
            const stream = await loopbackStream(440);
            const ctx = new AudioContext();
            if (attach) { const el = new Audio(); el.muted = true; el.srcObject = stream; el.play().catch(() => {}); }
            const an = ctx.createAnalyser(); an.fftSize = 2048;
            const src = ctx.createMediaStreamSource(stream); src.connect(an);
            const g = ctx.createGain(); g.gain.value = 0; an.connect(g); g.connect(ctx.destination);
            await new Promise((r) => setTimeout(r, 1500));
            const buf = new Float32Array(an.fftSize); an.getFloatTimeDomainData(buf);
            out[attach ? 'attached' : 'bare'] = Math.max(...buf.map(Math.abs));
          }
          return out;
        }"""
    )
    await page.close()
    assert peaks["attached"] > 0.1
    assert peaks["bare"] < 0.01, "Chromium no longer needs the element: the workaround can go"
