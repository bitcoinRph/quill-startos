// AudioWorklet: downmix to mono, resample to 16 kHz by box averaging, emit PCM16 frames.
const OUT_RATE = 16000
const FRAME = 1600 // 100 ms

class Pcm16kProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super()
    this.id = (options.processorOptions && options.processorOptions.id) || ''
    this.ratio = sampleRate / OUT_RATE
    this.need = this.ratio
    this.acc = 0
    this.frame = new Int16Array(FRAME)
    this.n = 0
    this.sumSq = 0
    this.peak = 0
    this.port.onmessage = (e) => { if (e.data === 'flush') this.emit(true) }
  }

  push(v) {
    const c = v > 1 ? 1 : v < -1 ? -1 : v
    this.frame[this.n++] = c < 0 ? c * 0x8000 : c * 0x7fff
    this.sumSq += c * c
    const a = c < 0 ? -c : c
    if (a > this.peak) this.peak = a
    if (this.n === FRAME) this.emit(false)
  }

  emit(flush) {
    if (this.n === 0) return
    const len = flush ? Math.max(this.n, 320) : this.n // pad a final partial frame to 20 ms
    const out = new Int16Array(len)
    out.set(this.frame.subarray(0, this.n))
    const rms = Math.sqrt(this.sumSq / Math.max(1, this.n))
    this.port.postMessage({ id: this.id, pcm: out.buffer, rms, peak: this.peak }, [out.buffer])
    this.n = 0
    this.sumSq = 0
    this.peak = 0
  }

  process(inputs) {
    const input = inputs[0]
    if (!input || input.length === 0) return true
    const ch = input.length
    const len = input[0].length
    for (let i = 0; i < len; i++) {
      let x = 0
      for (let c = 0; c < ch; c++) x += input[c][i]
      x /= ch
      let w = 1
      while (w > 1e-9) {
        const take = w < this.need ? w : this.need
        this.acc += x * take
        this.need -= take
        w -= take
        if (this.need <= 1e-9) {
          this.push(this.acc / this.ratio)
          this.acc = 0
          this.need = this.ratio
        }
      }
    }
    return true
  }
}

registerProcessor('pcm16k', Pcm16kProcessor)
