/*
 * Live ingest protocol (docs/server-spec.md section 6): frame encoding and a
 * small WebSocket client.
 *
 * Binary frame: byte 0 = N (length of the track id in UTF-8), bytes 1..N =
 * track id, the rest = PCM signed 16-bit little-endian, mono, 16 kHz.
 *
 * Loaded as a classic script in the bot page and in tests; also `require`-able
 * from Node for the pure functions.
 */
(function (root) {
  'use strict';

  const SAMPLE_RATE = 16000;
  const MAX_PENDING_MS = 3000; // audio held back while a track waits to be registered
  const MAX_BUFFERED_BYTES = 4 * 1024 * 1024; // drop frames rather than grow memory
  const PING_MS = 20000;

  const utf8 = new TextEncoder();

  /** Encode one binary frame. `pcm` is an Int16Array of samples. */
  function encodeFrame(trackId, pcm) {
    const id = utf8.encode(trackId);
    if (id.length === 0 || id.length > 255) {
      throw new RangeError('track id must be 1..255 bytes in UTF-8');
    }
    const out = new Uint8Array(1 + id.length + pcm.length * 2);
    out[0] = id.length;
    out.set(id, 1);
    const view = new DataView(out.buffer);
    const base = 1 + id.length;
    for (let i = 0; i < pcm.length; i++) view.setInt16(base + i * 2, pcm[i], true);
    return out;
  }

  /** Decode a frame (tests and diagnostics). */
  function decodeFrame(bytes) {
    const n = bytes[0];
    const id = new TextDecoder().decode(bytes.subarray(1, 1 + n));
    const body = bytes.subarray(1 + n);
    const view = new DataView(body.buffer, body.byteOffset, body.byteLength);
    const pcm = new Int16Array(body.byteLength >> 1);
    for (let i = 0; i < pcm.length; i++) pcm[i] = view.getInt16(i * 2, true);
    return { trackId: id, pcm };
  }

  function trackMessage(t) {
    return {
      type: 'track',
      track_id: t.trackId,
      kind: t.kind || 'participant',
      speaker: t.speaker,
      label: t.label || t.speaker,
      participant_id: t.participantId || t.trackId,
      sample_rate: SAMPLE_RATE,
    };
  }

  /**
   * One WebSocket, many tracks. Frames for a track that is not registered yet
   * are held (bounded) and flushed on registration, so the bot can wait a
   * moment for a participant's display name before it names the track.
   */
  class LiveIngest {
    constructor(url, hooks) {
      this.url = url;
      this.hooks = hooks || {};
      this.ws = null;
      this.ready = false;
      this.closedByUs = false;
      this.registered = new Set();
      this.pending = new Map(); // trackId -> {chunks: Int16Array[], samples}
      this.queue = []; // text/binary sends waiting for 'open'
      this.pingTimer = null;
      this.framesSent = 0;
      this.droppedFrames = 0;
    }

    open() {
      return new Promise((resolve, reject) => {
        const ws = new WebSocket(this.url);
        ws.binaryType = 'arraybuffer';
        this.ws = ws;
        ws.onopen = () => {
          this.pingTimer = setInterval(() => this._text({ type: 'ping' }), PING_MS);
          for (const item of this.queue.splice(0)) ws.send(item);
          resolve();
        };
        ws.onmessage = (ev) => this._onMessage(ev);
        ws.onerror = () => {
          if (!this.ready) reject(new Error('live ingest websocket failed to open'));
        };
        ws.onclose = (ev) => {
          clearInterval(this.pingTimer);
          this.ready = false;
          if (!this.closedByUs && this.hooks.onClose) this.hooks.onClose(ev.code, ev.reason);
        };
      });
    }

    _onMessage(ev) {
      if (typeof ev.data !== 'string') return;
      let msg;
      try {
        msg = JSON.parse(ev.data);
      } catch (e) {
        return;
      }
      if (msg.type === 'ready') this.ready = true;
      if (this.hooks.onMessage) this.hooks.onMessage(msg);
    }

    _text(obj) {
      const s = JSON.stringify(obj);
      if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(s);
      else if (this.ws && this.ws.readyState === WebSocket.CONNECTING) this.queue.push(s);
    }

    registerTrack(meta) {
      this.registered.add(meta.trackId);
      this._text(trackMessage(meta));
      const held = this.pending.get(meta.trackId);
      if (held) {
        this.pending.delete(meta.trackId);
        for (const chunk of held.chunks) this._binary(meta.trackId, chunk);
      }
    }

    /** Send PCM for a track; held back while the track is not registered. */
    pcm(trackId, samples) {
      if (this.registered.has(trackId)) return this._binary(trackId, samples);
      let held = this.pending.get(trackId);
      if (!held) this.pending.set(trackId, (held = { chunks: [], samples: 0 }));
      held.chunks.push(samples);
      held.samples += samples.length;
      while (held.samples > (SAMPLE_RATE * MAX_PENDING_MS) / 1000 && held.chunks.length > 1) {
        held.samples -= held.chunks.shift().length;
      }
    }

    _binary(trackId, samples) {
      const ws = this.ws;
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      if (ws.bufferedAmount > MAX_BUFFERED_BYTES) {
        this.droppedFrames++;
        return;
      }
      ws.send(encodeFrame(trackId, samples));
      this.framesSent++;
    }

    isRegistered(trackId) {
      return this.registered.has(trackId);
    }

    hasPending(trackId) {
      return this.pending.has(trackId);
    }

    endTrack(trackId) {
      this.pending.delete(trackId);
      if (!this.registered.delete(trackId)) return;
      this._text({ type: 'track_end', track_id: trackId });
    }

    /** Tell the server the meeting is over (it flushes and finalizes). */
    stop() {
      this._text({ type: 'stop' });
    }

    close() {
      this.closedByUs = true;
      clearInterval(this.pingTimer);
      if (this.ws && this.ws.readyState <= WebSocket.OPEN) {
        try {
          this.ws.close(1000, 'bot done');
        } catch (e) {
          /* already closing */
        }
      }
    }
  }

  const api = { SAMPLE_RATE, encodeFrame, decodeFrame, trackMessage, LiveIngest };
  root.QuillProtocol = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof self !== 'undefined' ? self : this);
