// Pure-function tests for the page scripts, run by test_bot_js_units.py with Node.
'use strict';
const assert = require('assert');
const path = require('path');
const page = path.resolve(__dirname, '../../quill_server/bot/page');
const { encodeFrame, decodeFrame, trackMessage } = require(path.join(page, 'protocol.js'));
const { Resampler, toInt16 } = require(path.join(page, 'pcm-worklet.js'));
const { rewriteCandidate, rewriteSdp, install } = require(path.join(page, 'ice-map.js'));

const results = [];
function test(name, fn) {
  try { fn(); results.push({ name, ok: true }); } catch (e) { results.push({ name, ok: false, error: String(e && e.stack || e) }); }
}

function sine(freq, rate, seconds, amp = 0.5) {
  const out = new Float32Array(Math.round(rate * seconds));
  for (let i = 0; i < out.length; i++) out[i] = amp * Math.sin((2 * Math.PI * freq * i) / rate);
  return out;
}

// Goertzel magnitude of `freq` in x (sampled at rate), normalised so a unit sine gives ~0.5.
function tone(x, freq, rate) {
  const w = (2 * Math.PI * freq) / rate;
  let re = 0, im = 0;
  for (let i = 0; i < x.length; i++) { re += x[i] * Math.cos(w * i); im += x[i] * Math.sin(w * i); }
  return Math.hypot(re, im) / x.length;
}

function run(inRate, freq, seconds = 1, block = 128) {
  const r = new Resampler(inRate, 16000);
  const input = sine(freq, inRate, seconds);
  const chunks = [];
  for (let i = 0; i < input.length; i += block) chunks.push(r.process(input.subarray(i, i + block)));
  const n = chunks.reduce((a, c) => a + c.length, 0);
  const out = new Float32Array(n);
  let o = 0;
  for (const c of chunks) { out.set(c, o); o += c.length; }
  return out;
}

// ---- framing ----------------------------------------------------------------

test('frame header: id length, id, PCM16LE', () => {
  const f = encodeFrame('abc12345', Int16Array.from([1, -1, 256, -32768, 32767]));
  assert.strictEqual(f[0], 8);
  assert.strictEqual(Buffer.from(f.subarray(1, 9)).toString('utf8'), 'abc12345');
  assert.deepStrictEqual([...f.subarray(9)], [1, 0, 0xff, 0xff, 0x00, 0x01, 0x00, 0x80, 0xff, 0x7f]);
  assert.strictEqual(f.length, 1 + 8 + 10);
});

test('frame length byte counts UTF-8 bytes, not characters', () => {
  const id = 'zoë-1'; // ë is 2 bytes
  const f = encodeFrame(id, Int16Array.from([7]));
  assert.strictEqual(f[0], Buffer.byteLength(id));
  const d = decodeFrame(f);
  assert.strictEqual(d.trackId, id);
  assert.deepStrictEqual([...d.pcm], [7]);
});

test('frame round trip of a 40 ms chunk', () => {
  const pcm = new Int16Array(640).map((_, i) => ((i * 97) % 65536) - 32768);
  const d = decodeFrame(encodeFrame('p', pcm));
  assert.strictEqual(d.trackId, 'p');
  assert.deepStrictEqual(d.pcm, pcm);
});

test('frame rejects empty and over-long ids', () => {
  assert.throws(() => encodeFrame('', new Int16Array(1)), RangeError);
  assert.throws(() => encodeFrame('x'.repeat(256), new Int16Array(1)), RangeError);
  assert.strictEqual(encodeFrame('x'.repeat(255), new Int16Array(1))[0], 255);
});

test('track registration message follows the spec', () => {
  assert.deepStrictEqual(trackMessage({ trackId: 'p1', speaker: 'Alice' }), {
    type: 'track', track_id: 'p1', kind: 'participant', speaker: 'Alice', label: 'Alice',
    participant_id: 'p1', sample_rate: 16000,
  });
});

// ---- JVB media routing ------------------------------------------------------

test('rewrites only the exact advertised ICE candidate address', () => {
  const map = { '97.119.152.68': '10.0.3.1' };
  const candidate = 'candidate:3 1 udp 1677724415 97.119.152.68 10000 typ srflx raddr 0.0.0.0 rport 9';
  assert.strictEqual(
    rewriteCandidate(candidate, map),
    'candidate:3 1 udp 1677724415 10.0.3.1 10000 typ srflx raddr 0.0.0.0 rport 9',
  );
  assert.strictEqual(
    rewriteCandidate('candidate:4 1 udp 1677724415 97.119.152.69 10000 typ srflx', map),
    'candidate:4 1 udp 1677724415 97.119.152.69 10000 typ srflx',
  );
});

test('rewrites JVB candidates inside SDP without changing other addresses', () => {
  const map = { '97.119.152.68': '10.0.3.1' };
  const sdp = [
    'v=0',
    'c=IN IP4 0.0.0.0',
    'a=candidate:3 1 udp 1677724415 97.119.152.68 10000 typ srflx raddr 0.0.0.0 rport 9',
    'a=candidate:4 1 udp 1677724415 203.0.113.8 443 typ relay',
    '',
  ].join('\r\n');
  const out = rewriteSdp(sdp, map);
  assert.ok(out.includes('a=candidate:3 1 udp 1677724415 10.0.3.1 10000 typ srflx'));
  assert.ok(out.includes('c=IN IP4 0.0.0.0'));
  assert.ok(out.includes('203.0.113.8 443 typ relay'));
});

test('installs wrappers, rewrites candidates, and preserves callback arguments', () => {
  class FakePeerConnection {
    setRemoteDescription(...args) { this.remoteArgs = args; return 'remote'; }
    addIceCandidate(...args) { this.candidateArgs = args; return 'candidate'; }
  }
  const oldPeer = global.RTCPeerConnection;
  const oldDescription = global.RTCSessionDescription;
  const oldCandidate = global.RTCIceCandidate;
  global.RTCPeerConnection = FakePeerConnection;
  global.RTCSessionDescription = function (init) { Object.assign(this, init); };
  global.RTCIceCandidate = function (init) { Object.assign(this, init); };
  try {
    install({ '97.119.152.68': '10.0.3.1' });
    const pc = new FakePeerConnection();
    const ok = () => {};
    const fail = () => {};
    assert.strictEqual(pc.setRemoteDescription({ type: 'offer', sdp: 'a=candidate:3 1 udp 1 97.119.152.68 10000 typ host' }, ok, fail), 'remote');
    assert.ok(pc.remoteArgs[0].sdp.includes('10.0.3.1 10000'));
    assert.strictEqual(pc.remoteArgs[1], ok);
    assert.strictEqual(pc.remoteArgs[2], fail);
    assert.strictEqual(pc.addIceCandidate({ candidate: 'candidate:3 1 udp 1 97.119.152.68 10000 typ host', sdpMid: '0' }, ok, fail), 'candidate');
    assert.ok(pc.candidateArgs[0].candidate.includes('10.0.3.1 10000'));
    assert.strictEqual(pc.candidateArgs[1], ok);
    assert.strictEqual(pc.candidateArgs[2], fail);
    pc.addIceCandidate(null);
    assert.strictEqual(pc.candidateArgs[0], null);
  } finally {
    global.RTCPeerConnection = oldPeer;
    global.RTCSessionDescription = oldDescription;
    global.RTCIceCandidate = oldCandidate;
  }
});

// ---- int16 ------------------------------------------------------------------

test('float to int16 clamps and rounds', () => {
  const out = new Int16Array(5);
  toInt16(Float32Array.from([0, 1, -1, 2, -2]), out, 0);
  assert.deepStrictEqual([...out], [0, 32767, -32768, 32767, -32768]);
});

// ---- resampler --------------------------------------------------------------

for (const rate of [48000, 44100, 32000, 24000, 22050, 8000]) {
  test(`resample ${rate} -> 16000 keeps a 1 kHz tone and the right sample count`, () => {
    const out = run(rate, 1000, 1);
    // The kernel looks ahead by its half-width, so the tail (< 1.5 ms) is still pending.
    assert.ok(out.length <= 16000 && 16000 - out.length <= 24, `got ${out.length} samples`);
    const mid = out.subarray(2000, 14000);
    assert.ok(Math.abs(tone(mid, 1000, 16000) - 0.25) < 0.01, `1 kHz level ${tone(mid, 1000, 16000)}`);
  });
}

test('passband is flat to 6 kHz at 48 kHz', () => {
  for (const f of [100, 1000, 3000, 6000]) {
    const out = run(48000, f, 1).subarray(2000, 14000);
    const level = tone(out, f, 16000);
    assert.ok(Math.abs(level - 0.25) < 0.015, `${f} Hz level ${level}`);
  }
});

test('content above 8 kHz is suppressed, not folded back', () => {
  for (const f of [9500, 12000, 15000, 20000]) {
    const out = run(48000, f, 1).subarray(2000, 14000);
    let rms = 0;
    for (const v of out) rms += v * v;
    rms = Math.sqrt(rms / out.length);
    assert.ok(rms < 0.02, `${f} Hz leaked with rms ${rms}`);
  }
});

test('block size does not change the result', () => {
  const a = run(44100, 700, 0.5, 128);
  const b = run(44100, 700, 0.5, 441);
  const n = Math.min(a.length, b.length);
  for (let i = 0; i < n; i++) assert.ok(Math.abs(a[i] - b[i]) < 1e-5, `sample ${i}`);
});

test('16 kHz input is passed through untouched', () => {
  const x = sine(500, 16000, 0.1);
  const r = new Resampler(16000, 16000);
  assert.strictEqual(r.process(x), x);
});

test('upsampling 8 kHz to 16 kHz keeps the tone', () => {
  const r = new Resampler(8000, 16000);
  const input = sine(1000, 8000, 1);
  const out = [];
  for (let i = 0; i < input.length; i += 128) out.push(...r.process(input.subarray(i, i + 128)));
  const x = Float32Array.from(out).subarray(2000, 14000);
  assert.ok(out.length <= 16000 && 16000 - out.length <= 24, `got ${out.length}`);
  assert.ok(Math.abs(tone(x, 1000, 16000) - 0.25) < 0.015);
});

test('silence in, silence out; memory stays bounded over a long stream', () => {
  const r = new Resampler(48000, 16000);
  const z = new Float32Array(128);
  let total = 0;
  for (let i = 0; i < 48000 * 60 / 128; i++) total += r.process(z).length;
  // No drift: the shortfall is the fixed look-ahead, not something that grows with time.
  assert.ok(total <= 16000 * 60 && 16000 * 60 - total <= 24, `total ${total}`);
  assert.ok(r.buf.length <= 8192, `buffer grew to ${r.buf.length}`);
});

console.log(JSON.stringify(results));
process.exit(results.every((r) => r.ok) ? 0 : 1);
