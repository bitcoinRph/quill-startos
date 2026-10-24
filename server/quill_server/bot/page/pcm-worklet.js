/*
 * AudioWorklet: any-rate float audio -> 16 kHz mono PCM16 chunks.
 *
 * Why resample here and not in an AudioContext({sampleRate: 16000}): the
 * server contract is 16 kHz PCM16 and this keeps the conversion in one place
 * that is unit-tested without a browser (`Resampler` is exported for Node).
 *
 * The resampler is a streaming windowed-sinc low-pass evaluated at fractional
 * positions, so 48 kHz, 44.1 kHz and 16 kHz contexts all work and aliasing
 * above 8 kHz is suppressed (ASR front ends are sensitive to it).
 *
 * Messages to the main thread: ArrayBuffer of Int16 samples (little-endian
 * platform order, converted to explicit LE when framed), `chunkMs` long.
 */
'use strict';

const TARGET_RATE = 16000;
const LOBES = 8; // sinc lobes on each side of the centre tap
const CUTOFF = 0.92; // fraction of the lower Nyquist kept
const OVERSAMPLE = 512; // kernel table entries per input sample
const MIN_FRAME = (TARGET_RATE * 20) / 1000; // 20 ms in samples

class Resampler {
  constructor(inRate, outRate = TARGET_RATE) {
    this.inRate = inRate;
    this.outRate = outRate;
    this.step = inRate / outRate; // input samples per output sample
    this.passthrough = inRate === outRate;
    const scale = Math.max(this.step, 1);
    this.half = LOBES * scale; // kernel half-width in input samples
    const size = Math.ceil(this.half * OVERSAMPLE) + 2;
    this.table = new Float32Array(size);
    for (let i = 0; i < size; i++) {
      const t = i / OVERSAMPLE;
      const x = (CUTOFF * t) / scale;
      const sinc = x === 0 ? 1 : Math.sin(Math.PI * x) / (Math.PI * x);
      const w = t >= this.half ? 0 : 0.5 + 0.5 * Math.cos((Math.PI * t) / this.half); // Hann
      this.table[i] = (CUTOFF / scale) * sinc * w;
    }
    this.buf = new Float32Array(8192);
    this.len = 0; // valid samples in buf
    this.base = 0; // absolute index of buf[0]
    this.pos = 0; // absolute (fractional) input position of the next output
  }

  /** Feed float samples; returns a Float32Array of 16 kHz samples. */
  process(input) {
    if (this.passthrough) return input;
    if (this.len + input.length > this.buf.length) {
      const bigger = new Float32Array(Math.max(this.buf.length * 2, this.len + input.length));
      bigger.set(this.buf.subarray(0, this.len));
      this.buf = bigger;
    }
    this.buf.set(input, this.len);
    this.len += input.length;

    const out = [];
    const end = this.base + this.len; // exclusive
    const { half, table, buf } = this;
    while (Math.floor(this.pos + half) < end) {
      const first = Math.ceil(this.pos - half);
      const last = Math.floor(this.pos + half);
      let acc = 0;
      for (let n = first; n <= last; n++) {
        const k = n - this.base;
        if (k < 0) continue; // before the stream start: silence
        const d = Math.abs(n - this.pos) * OVERSAMPLE;
        const i = d | 0;
        const frac = d - i;
        acc += buf[k] * (table[i] + (table[i + 1] - table[i]) * frac);
      }
      out.push(acc);
      this.pos += this.step;
    }
    const drop = Math.floor(this.pos - half) - this.base;
    if (drop > 0 && drop <= this.len) {
      buf.copyWithin(0, drop, this.len);
      this.len -= drop;
      this.base += drop;
    }
    return Float32Array.from(out);
  }
}

function toInt16(float, out, offset) {
  for (let i = 0; i < float.length; i++) {
    const s = Math.max(-1, Math.min(1, float[i]));
    out[offset + i] = Math.round(s < 0 ? s * 32768 : s * 32767);
  }
}

if (typeof registerProcessor === 'function') {
  class Pcm16kProcessor extends AudioWorkletProcessor {
    constructor(options) {
      super();
      const opts = (options && options.processorOptions) || {};
      this.resampler = new Resampler(sampleRate, TARGET_RATE); // `sampleRate` is the context rate
      this.chunkSize = Math.round(((opts.chunkMs || 40) * TARGET_RATE) / 1000);
      this.chunk = new Int16Array(this.chunkSize);
      this.filled = 0;
      this.silence = new Float32Array(128);
      this.stopped = false;
      this.port.onmessage = (ev) => {
        if (ev.data === 'flush') {
          this._emit(true);
          this.stopped = true; // the partial chunk stays the last one
        }
      };
    }

    _push(pcm16k) {
      let read = 0;
      while (read < pcm16k.length) {
        const n = Math.min(this.chunkSize - this.filled, pcm16k.length - read);
        toInt16(pcm16k.subarray(read, read + n), this.chunk, this.filled);
        this.filled += n;
        read += n;
        if (this.filled === this.chunkSize) this._emit(false);
      }
    }

    _emit(partial) {
      if (this.filled === 0) return;
      // The server expects frames of at least 20 ms; the zero fill is silence.
      const data = partial ? this.chunk.slice(0, Math.max(this.filled, MIN_FRAME)) : this.chunk;
      this.port.postMessage(data.buffer, [data.buffer]);
      this.chunk = new Int16Array(this.chunkSize); // the old buffer was transferred (detached)
      this.filled = 0;
    }

    process(inputs) {
      if (this.stopped) return true;
      const channels = inputs[0];
      let mono;
      if (!channels || channels.length === 0) {
        // No source connected (stream ended or not yet flowing): keep the
        // timeline moving with silence; the server never fills gaps itself.
        mono = this.silence;
      } else if (channels.length === 1) {
        mono = channels[0];
      } else {
        mono = new Float32Array(channels[0].length);
        for (const ch of channels) for (let i = 0; i < mono.length; i++) mono[i] += ch[i] / channels.length;
      }
      this._push(this.resampler.process(mono));
      return true;
    }
  }
  registerProcessor('quill-pcm16k', Pcm16kProcessor);
}

if (typeof module !== 'undefined' && module.exports) module.exports = { Resampler, toInt16, TARGET_RATE };
