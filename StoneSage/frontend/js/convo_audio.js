// Conversation mode with barge-in: raw microphone capture (16 kHz PCM with a pre-roll), and the logic that decides whether
// a sound while Computer is speaking is YOU or Computer's own voice coming back through the mic.
//
// Everything except attachMic() is pure and tested headlessly (tests/js/convo_audio.test.mjs). attachMic() is the only part
// that touches the browser's audio and has not been tried on a real phone yet.

/** Per-frame loudness of a PCM16 WAV (the clips the voice server returns): {stepMs, rms:Float32Array} or null. */
export function wavEnvelope(bytes, stepMs = 40) {
  if (!bytes || bytes.length < 44) return null;
  const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const tag = (o) => String.fromCharCode(bytes[o], bytes[o + 1], bytes[o + 2], bytes[o + 3]);
  if (tag(0) !== 'RIFF' || tag(8) !== 'WAVE') return null;
  let pos = 12, rate = 0, channels = 1, bits = 16, dataStart = -1, dataLen = 0;
  while (pos + 8 <= bytes.length) {
    const id = tag(pos), size = dv.getUint32(pos + 4, true);
    if (id === 'fmt ') {
      channels = dv.getUint16(pos + 10, true); rate = dv.getUint32(pos + 12, true); bits = dv.getUint16(pos + 22, true);
    } else if (id === 'data') {
      dataStart = pos + 8;
      dataLen = Math.min(size, bytes.length - dataStart);       // a streamed WAV may claim 0xFFFFFFFF
      break;
    }
    pos += 8 + size + (size & 1);
  }
  if (dataStart < 0 || bits !== 16 || !rate) return null;
  const samples = Math.floor(dataLen / 2 / channels);
  const per = Math.max(1, Math.round(rate * stepMs / 1000));
  const out = new Float32Array(Math.ceil(samples / per));
  for (let f = 0; f < out.length; f++) {
    let sum = 0, n = 0;
    for (let i = f * per; i < Math.min(samples, (f + 1) * per); i++) {
      const v = dv.getInt16(dataStart + i * 2 * channels, true) / 32768;
      sum += v * v; n++;
    }
    out[f] = n ? Math.sqrt(sum / n) : 0;
  }
  return { stepMs, rms: out };
}

/** Loudest level of the envelope in [tMs - back, tMs + ahead]: the mic hears the speaker a little late. */
export function envelopeAt(env, tMs, backMs = 150, aheadMs = 50) {
  if (!env) return 0;
  const a = Math.max(0, Math.floor((tMs - backMs) / env.stepMs));
  const b = Math.min(env.rms.length - 1, Math.ceil((tMs + aheadMs) / env.stepMs));
  let m = 0;
  for (let i = a; i <= b; i++) if (env.rms[i] > m) m = env.rms[i];
  return m;
}

/** Int16 chunks -> a mono PCM16 WAV file (Uint8Array). */
export function pcmToWav(chunks, sampleRate) {
  let n = 0;
  for (const c of chunks) n += c.length;
  const buf = new Uint8Array(44 + n * 2);
  const dv = new DataView(buf.buffer);
  const put = (o, s) => { for (let i = 0; i < s.length; i++) buf[o + i] = s.charCodeAt(i); };
  put(0, 'RIFF'); dv.setUint32(4, 36 + n * 2, true); put(8, 'WAVE'); put(12, 'fmt ');
  dv.setUint32(16, 16, true); dv.setUint16(20, 1, true); dv.setUint16(22, 1, true);
  dv.setUint32(24, sampleRate, true); dv.setUint32(28, sampleRate * 2, true); dv.setUint16(32, 2, true); dv.setUint16(34, 16, true);
  put(36, 'data'); dv.setUint32(40, n * 2, true);
  let o = 44;
  for (const c of chunks) for (let i = 0; i < c.length; i++, o += 2) dv.setInt16(o, c[i], true);
  return buf;
}

/** Streaming linear resampler, Float32 in -> Int16 out (44.1/48 kHz mic -> 16 kHz for the recognizer). */
export function createResampler(inRate, outRate) {
  const ratio = inRate / outRate;
  let carry = new Float32Array(0), pos = 0;
  return {
    process(x) {
      const buf = new Float32Array(carry.length + x.length);
      buf.set(carry); buf.set(x, carry.length);
      const out = [];
      let p = pos;
      while (p + 1 < buf.length) {
        const i = Math.floor(p), f = p - i;
        const v = buf[i] * (1 - f) + buf[i + 1] * f;
        out.push(Math.max(-32768, Math.min(32767, Math.round(v * 32767))));
        p += ratio;
      }
      const used = Math.min(Math.floor(p), buf.length);     // p can already be past this chunk: the rest is the next chunk's
      carry = buf.slice(used);
      pos = p - used;
      return Int16Array.from(out);
    }
  };
}

/**
 * Is a sound heard while Computer is speaking really someone talking over it?
 * push(micRms, playbackRms, tMs) once per frame -> 'idle' | 'barge'.
 * The bar is max(minOn, echoFactor * coupling * playbackRms): the louder Computer is at that instant, the louder you must be.
 * `coupling` is how much of the speaker reaches the mic. It starts pessimistic (0.5) and learns from frames that look like
 * pure leakage, ignoring spikes (you), so with good echo cancellation the bar settles near minOn. You must stay above the
 * bar for onMs, and the first holdoffMs of a reply is ignored while the phone's echo canceller settles.
 */
export function createBargeGate(opts) {
  const cfg = Object.assign({ minOn: 0.05, onMs: 250, holdoffMs: 500, echoFactor: 2.0, coupling0: 0.5,
                              couplingMin: 0.02, refFloor: 0.01 }, opts || {});
  let coupling = cfg.coupling0, t0 = null, aboveSince = null;
  const learn = (mic, ref) => {
    if (ref <= cfg.refFloor) return;
    const r = mic / ref;
    if (r < coupling * 2) coupling = Math.max(cfg.couplingMin, coupling * 0.9 + r * 0.1);
  };
  return {
    coupling: () => coupling,
    push(mic, ref, t) {
      if (t0 === null) t0 = t;
      if (t - t0 < cfg.holdoffMs) { learn(mic, ref); return 'idle'; }
      const bar = Math.max(cfg.minOn, cfg.echoFactor * coupling * ref);
      if (mic > bar) {
        if (aboveSince === null) aboveSince = t;
        return t - aboveSince >= cfg.onMs ? 'barge' : 'idle';
      }
      aboveSince = null;
      learn(mic, ref);
      return 'idle';
    }
  };
}

const wordsOf = (t) => (t || '').toLowerCase().replace(/[^a-z0-9' ]+/g, ' ').split(/\s+/).filter(Boolean);

/** True when `heard` is mostly Computer's own words coming back through the mic (>= 3 words, >= 60 % found in `spoken`). */
export function looksLikeEcho(spoken, heard, threshold = 0.6) {
  const h = wordsOf(heard);
  if (h.length < 3) return false;
  const s = new Set(wordsOf(spoken));
  return h.filter((w) => s.has(w)).length / h.length >= threshold;
}

/**
 * The capture state machine. pushFrame({rms, pcm:Int16Array, t}) once per audio frame (~40 ms).
 *   'idle'      nothing happening            'listening' waiting for / recording an utterance (endpointer decides the end)
 *   'busy'      Computer is thinking         'speaking'  Computer is talking: the barge gate watches for you
 * A ring buffer keeps the last ringMs of audio so an utterance (or a barge-in) starts with the word you were already saying.
 * o: { makeEndpointer, getReference(t), onUtterance(wavBytes, {barge}), onBarge(), onState(mode), gate?, ringMs?, sampleRate? }
 */
export function createListenerCore(o) {
  const ringMs = o.ringMs || 700, rate = o.sampleRate || 16000;
  let mode = 'idle', ep = null, gate = null, take = null, bargeTake = false;
  const ring = [];
  const setMode = (m) => { mode = m; if (o.onState) o.onState(m); };
  const emit = () => {
    const chunks = take, barge = bargeTake;
    take = null; bargeTake = false;
    setMode('busy');
    o.onUtterance(pcmToWav(chunks, rate), { barge });
  };
  return {
    get mode() { return mode; },
    listen() { ep = o.makeEndpointer(); take = null; bargeTake = false; setMode('listening'); },
    busy() { take = null; bargeTake = false; setMode('busy'); },
    speaking(on) {
      if (on && mode !== 'speaking') { gate = createBargeGate(o.gate); take = null; setMode('speaking'); }
      else if (!on && mode === 'speaking') setMode('busy');
    },
    idle() { take = null; bargeTake = false; setMode('idle'); },
    pushFrame(f) {
      ring.push(f);
      while (ring.length && f.t - ring[0].t > ringMs) ring.shift();
      if (take) take.push(f.pcm);
      if (mode === 'listening') {
        const r = ep.push(f.rms, f.t);
        if (r === 'speech' && !take) take = ring.map((x) => x.pcm);           // includes the pre-roll
        else if (r === 'end' && take) emit();
        else if (r === 'noise') { take = null; bargeTake = false; }
        else if (r === 'noSpeech') { ep = o.makeEndpointer(); take = null; }   // start a fresh floor measurement
      } else if (mode === 'speaking') {
        if (gate.push(f.rms, o.getReference(f.t), f.t) === 'barge') {
          o.onBarge();
          ep = o.makeEndpointer();
          for (const fr of ring) ep.push(fr.rms, fr.t);                        // the endpointer learns the room from the pre-roll
          take = ring.map((x) => x.pcm);
          bargeTake = true;
          setMode('listening');
        }
      }
    }
  };
}

/** The only browser-facing part: mic stream -> ~43 ms frames of {rms, 16 kHz PCM} for the core. Returns {close()}. */
export function attachMic(stream, core) {
  const AC = window.AudioContext || window.webkitAudioContext;
  if (!AC || !stream) return null;
  const ctx = new AC();
  try { ctx.resume(); } catch (_) {}
  const src = ctx.createMediaStreamSource(stream);
  const proc = ctx.createScriptProcessor(2048, 1, 1);
  const rs = createResampler(ctx.sampleRate, 16000);
  proc.onaudioprocess = (e) => {
    const x = e.inputBuffer.getChannelData(0);
    let sum = 0;
    for (let i = 0; i < x.length; i++) sum += x[i] * x[i];
    core.pushFrame({ rms: Math.sqrt(sum / x.length), pcm: rs.process(x), t: performance.now() });
  };
  src.connect(proc);
  proc.connect(ctx.destination);              // some browsers only run the node when it is connected; its output is silence
  return { close() { try { proc.onaudioprocess = null; proc.disconnect(); src.disconnect(); ctx.close(); } catch (_) {} } };
}
