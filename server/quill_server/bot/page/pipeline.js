/*
 * Remote MediaStream -> 16 kHz PCM16 chunks, one AudioWorklet per track.
 *
 * Chromium quirk (crbug 121673 / 933677): audio of a *remote* WebRTC stream
 * only reaches Web Audio (`createMediaStreamSource`) once the stream is also
 * attached to a media element. The element is muted, so nothing is played, and
 * the worklet output goes to a zero-gain node so the graph is still pulled.
 */
(function (root) {
  'use strict';

  class AudioPipeline {
    /**
     * @param {(trackId: string, pcm: Int16Array) => void} onPcm
     * @param {{log?: (msg: string) => void, chunkMs?: number, sampleRate?: number}} [opts]
     *   `sampleRate` pins the context rate (tests); by default the device rate is used.
     */
    constructor(onPcm, opts) {
      this.onPcm = onPcm;
      this.opts = opts || {};
      this.ctx = null;
      this.sink = null;
      this.tracks = new Map(); // trackId -> {source, node, el, stream}
    }

    async init(workletUrl) {
      const Ctx = root.AudioContext || root.webkitAudioContext;
      const ctxOptions = { latencyHint: 'playback' };
      if (this.opts.sampleRate) ctxOptions.sampleRate = this.opts.sampleRate;
      this.ctx = new Ctx(ctxOptions);
      await this.ctx.audioWorklet.addModule(workletUrl);
      // Zero gain: the graph needs a path to the destination to be rendered.
      this.sink = this.ctx.createGain();
      this.sink.gain.value = 0;
      this.sink.connect(this.ctx.destination);
      if (this.ctx.state !== 'running') await this.ctx.resume();
      this._log('audio context ' + this.ctx.state + ' at ' + this.ctx.sampleRate + ' Hz');
    }

    get sampleRate() {
      return this.ctx ? this.ctx.sampleRate : 0;
    }

    /** Start pulling audio from `stream` as `trackId`. Replaces an existing entry. */
    addStream(trackId, stream) {
      this.removeTrack(trackId);
      if (this.ctx.state !== 'running') this.ctx.resume().catch(() => {});
      const el = new Audio();
      el.muted = true;
      el.autoplay = true;
      el.srcObject = stream;
      const p = el.play();
      if (p && p.catch) p.catch((e) => this._log('muted audio element play(): ' + e));

      const source = this.ctx.createMediaStreamSource(stream);
      const node = new AudioWorkletNode(this.ctx, 'quill-pcm16k', {
        numberOfInputs: 1,
        numberOfOutputs: 1,
        outputChannelCount: [1],
        processorOptions: { chunkMs: this.opts.chunkMs || 40 },
      });
      node.port.onmessage = (ev) => this.onPcm(trackId, new Int16Array(ev.data));
      source.connect(node);
      node.connect(this.sink);
      this.tracks.set(trackId, { source, node, el, stream });
    }

    has(trackId) {
      return this.tracks.has(trackId);
    }

    /** Stop a track. Resolves once its last partial chunk has been delivered. */
    removeTrack(trackId) {
      const t = this.tracks.get(trackId);
      if (!t) return Promise.resolve();
      this.tracks.delete(trackId);
      try {
        t.node.port.postMessage('flush');
      } catch (e) {
        /* port closed */
      }
      return new Promise((resolve) =>
        setTimeout(() => {
          try {
            t.source.disconnect();
            t.node.disconnect();
          } catch (e) {
            /* already disconnected */
          }
          t.el.srcObject = null;
          resolve();
        }, 60),
      );
    }

    async close() {
      await Promise.all([...this.tracks.keys()].map((id) => this.removeTrack(id)));
      if (this.ctx && this.ctx.state !== 'closed') await this.ctx.close().catch(() => {});
    }

    _log(msg) {
      if (this.opts.log) this.opts.log(msg);
    }
  }

  root.QuillPipeline = { AudioPipeline };
})(typeof self !== 'undefined' ? self : this);
