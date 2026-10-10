// Headless checks of StoneSage/frontend/js/convo_audio.js (barge-in). Run by tests/test_voice_js.py; needs node.
// Usage: node convo_audio.test.mjs <path to convo_audio.js> <path to chat.js>
import fs from 'fs';
import vm from 'vm';
import assert from 'assert';
import { pathToFileURL } from 'url';

const mod = await import(pathToFileURL(process.argv[2]).href);
const { wavEnvelope, envelopeAt, pcmToWav, createResampler, createBargeGate, looksLikeEcho, createListenerCore } = mod;

// the utterance end-of-speech detector lives in chat.js: load the real one
const chatSrc = fs.readFileSync(process.argv[3], 'utf8');
const grab = (name) => {
  const start = chatSrc.indexOf(`function ${name}(`);
  let i = chatSrc.indexOf('{', start), depth = 0;
  for (; i < chatSrc.length; i++) { if (chatSrc[i] === '{') depth++; if (chatSrc[i] === '}' && --depth === 0) break; }
  return chatSrc.slice(start, i + 1);
};
const c0 = vm.createContext({});
vm.runInContext(grab('createEndpointer') + '\nthis.make = createEndpointer;', c0);
const makeEndpointer = () => c0.make();

// ---- WAV in and out ------------------------------------------------------------------------------------------------------
const sine = (rate, seconds, amp, hz = 300) => Int16Array.from({ length: Math.round(rate * seconds) },
  (_, i) => Math.round(amp * 32767 * Math.sin(2 * Math.PI * hz * i / rate)));

let wav = pcmToWav([sine(24000, 0.4, 0.2), new Int16Array(24000 * 0.4), sine(24000, 0.4, 0.5)], 24000);
assert.equal(String.fromCharCode(...wav.slice(0, 4)), 'RIFF');
assert.equal(new DataView(wav.buffer).getUint32(24, true), 24000);
assert.equal(new DataView(wav.buffer).getUint32(40, true), 24000 * 1.2 * 2);
let env = wavEnvelope(wav);
assert.ok(env && env.stepMs === 40 && env.rms.length === 30, `envelope frames ${env && env.rms.length}`);
assert.ok(Math.abs(env.rms[2] - 0.2 * Math.SQRT1_2) < 0.01, `quiet part ${env.rms[2]}`);
assert.ok(env.rms[13] < 0.001, 'silent part');
assert.ok(Math.abs(env.rms[25] - 0.5 * Math.SQRT1_2) < 0.01, `loud part ${env.rms[25]}`);
// a WAV with an extra LIST chunk before the data (soundfile writes one) and a streamed one with an unknown length
const list = new Uint8Array([...'LIST'].map((c) => c.charCodeAt(0)).concat([4, 0, 0, 0, 1, 2, 3, 4]));
const withList = new Uint8Array(wav.length + list.length);
withList.set(wav.slice(0, 36)); withList.set(list, 36); withList.set(wav.slice(36), 36 + list.length);
assert.equal(wavEnvelope(withList).rms.length, 30, 'LIST chunk skipped');
const streamed = wav.slice(); new DataView(streamed.buffer).setUint32(40, 0xffffffff, true);
assert.equal(wavEnvelope(streamed).rms.length, 30, 'unknown data length');
assert.equal(wavEnvelope(new Uint8Array(100)), null);
assert.equal(wavEnvelope(null), null);
console.log('1 ok: WAV write / envelope read');

// the reference level the gate compares against
assert.ok(envelopeAt(env, 500) < 0.001 || envelopeAt(env, 500) > 0, 'defined');
assert.ok(envelopeAt(env, 200) > 0.1, 'inside the first tone');
assert.ok(envelopeAt(env, 620, 40, 0) < 0.001, 'inside the silence');
assert.ok(envelopeAt(env, 500, 150, 50) > 0.1, 'just after the tone ends: the mic still hears its tail');
assert.equal(envelopeAt(null, 100), 0);
console.log('2 ok: playback reference');

// ---- resampling -----------------------------------------------------------------------------------------------------------
for (const inRate of [48000, 44100]) {
  const full = Float32Array.from({ length: inRate * 2 }, (_, i) => 0.3 * Math.sin(2 * Math.PI * 440 * i / inRate));
  const rs = createResampler(inRate, 16000);
  const parts = [];
  for (let i = 0; i < full.length; i += 2048) parts.push(rs.process(full.slice(i, i + 2048)));
  const out = Int16Array.from(parts.flatMap((p) => Array.from(p)));
  assert.ok(Math.abs(out.length - 32000) <= 2, `${inRate}: ${out.length} samples for 2 s`);
  const rms = Math.sqrt(out.reduce((a, v) => a + (v / 32767) ** 2, 0) / out.length);
  assert.ok(Math.abs(rms - 0.3 * Math.SQRT1_2) < 0.01, `${inRate}: level ${rms}`);
  let maxJump = 0;
  for (let i = 1; i < out.length; i++) maxJump = Math.max(maxJump, Math.abs(out[i] - out[i - 1]));
  assert.ok(maxJump < 0.3 * 32767 * 2 * Math.PI * 440 / 16000 * 1.3, `${inRate}: a click at a chunk boundary (${maxJump})`);
}
console.log('3 ok: resampler length, level, no clicks at chunk boundaries');

// ---- the barge gate -------------------------------------------------------------------------------------------------------
/** [mic, ref, ms] segments at 40 ms per frame; returns the time of the first 'barge' or null. */
function gateRun(segments, opts) {
  const g = createBargeGate(opts);
  let t = 0;
  for (const [mic, ref, ms] of segments) for (let k = 0; k < ms; k += 40, t += 40) {
    const wob = 1 + 0.1 * Math.sin(t / 31);
    if (g.push(mic * wob, ref * wob, t) === 'barge') return { at: t, gate: g };
  }
  return { at: null, gate: g };
}
// Computer talks at a steady 0.10; the mic hears 0.03 of it (good echo cancellation): never a barge
assert.equal(gateRun([[0.03, 0.10, 6000]]).at, null);
// bad cancellation: the speaker is loud (0.30) and the mic hears 0.08, well above the plain 0.05 floor: still no barge
let r = gateRun([[0.08, 0.30, 8000]]);
assert.equal(r.at, null, 'loud echo counted as a person');
assert.ok(r.gate.coupling() < 0.4, `coupling learned down to ${r.gate.coupling()}`);
// you speak over it (0.4 while Computer is at 0.3): caught 250 ms after the hold-off ends
r = gateRun([[0.08, 0.30, 3000], [0.40, 0.30, 1000]]);
assert.ok(r.at !== null && r.at >= 3000 + 240 && r.at <= 3000 + 400, `barge at ${r.at}`);
// a cough (150 ms) over it does not stop it
assert.equal(gateRun([[0.03, 0.10, 2000], [0.4, 0.10, 150], [0.03, 0.10, 2000]]).at, null);
// speech in the first 500 ms of a reply is ignored (the echo canceller has not settled)
assert.equal(gateRun([[0.5, 0.2, 480]]).at, null);
r = gateRun([[0.5, 0.2, 1200]]);
assert.ok(r.at >= 500 + 240, `barge at ${r.at}`);
// between clips Computer is silent (ref 0): you only need to beat the plain floor
r = gateRun([[0.01, 0, 1000], [0.09, 0, 600]]);
assert.ok(r.at !== null && r.at <= 1000 + 400, `barge at ${r.at}`);
// the bar rises with Computer's own loudness: the same 0.12 voice barges in a quiet passage but not in a loud one
assert.ok(gateRun([[0.03, 0.05, 1000], [0.12, 0.05, 700]]).at !== null);
assert.equal(gateRun([[0.08, 0.30, 3000], [0.12, 0.30, 700]]).at, null);
console.log('4 ok: barge gate (echo ignored, you caught, cough ignored, hold-off, loudness-aware)');

// ---- echo detection -------------------------------------------------------------------------------------------------------
assert.ok(looksLikeEcho('The living room lights are now off and the thermostat is set to seventy two.', 'the living room lights are now off'));
assert.ok(!looksLikeEcho('The living room lights are now off.', 'Actually turn on the porch light'));
assert.ok(!looksLikeEcho('The living room lights are now off.', 'stop'));
assert.ok(!looksLikeEcho('', 'turn off the tv'));
console.log('5 ok: own words coming back are recognised');

// ---- the capture state machine, with synthetic frames -----------------------------------------------------------------------
function harness({ ref = 0 } = {}) {
  const events = { utterances: [], barges: 0, modes: [] };
  const core = createListenerCore({
    makeEndpointer, getReference: () => ref.v ?? 0, gate: {},
    onUtterance: (wavBytes, info) => events.utterances.push({ wavBytes, ...info }),
    onBarge: () => { events.barges++; }, onState: (m) => events.modes.push(m),
  });
  let t = 0;
  const frames = (level, ms, marker, refLevel) => {
    for (let k = 0; k < ms; k += 40, t += 40) {
      if (refLevel !== undefined && ref) ref.v = refLevel;
      core.pushFrame({ rms: level * (1 + 0.1 * Math.sin(t / 29)), pcm: new Int16Array(640).fill(marker), t });
    }
  };
  return { core, events, frames };
}
const dataSamples = (wavBytes) => new Int16Array(wavBytes.buffer.slice(wavBytes.byteOffset + 44, wavBytes.byteOffset + wavBytes.byteLength));
const countOf = (samples, v) => samples.reduce((a, s) => a + (s === v), 0);
const SPEECH = 1000, QUIET = 10;

// a) normal turn: quiet, speak 1.2 s, quiet -> one utterance, with the pre-roll, then 'busy'
let h = harness();
h.core.listen();
h.frames(0.005, 400, QUIET); h.frames(0.09, 1200, SPEECH); h.frames(0.005, 1500, QUIET);
assert.equal(h.events.utterances.length, 1);
assert.equal(h.events.utterances[0].barge, false);
assert.equal(countOf(dataSamples(h.events.utterances[0].wavBytes), SPEECH), 30 * 640, 'every speech frame is in the take, including the first');
assert.equal(h.core.mode, 'busy');
console.log('6 ok: a normal turn gives one utterance with all of the speech');

// b) Computer is speaking, its voice leaks into the mic; then you barge in: onBarge once, the utterance starts BEFORE detection
const ref = { v: 0.2 };
h = harness({ ref });
h.core.listen(); h.core.busy(); h.core.speaking(true);
h.frames(0.04, 3000, QUIET, 0.2);                                  // pure leakage for 3 s: nothing happens
assert.equal(h.events.barges, 0);
assert.equal(h.events.utterances.length, 0);
h.frames(0.25, 1500, SPEECH, 0.2);                                 // you talk over it
h.frames(0.005, 1600, QUIET, 0);                                   // and stop (Computer was cut off, so the room goes quiet)
assert.equal(h.events.barges, 1, 'onBarge once');
assert.equal(h.events.utterances.length, 1);
assert.equal(h.events.utterances[0].barge, true);
const speechFrames = countOf(dataSamples(h.events.utterances[0].wavBytes), SPEECH) / 640;
assert.equal(speechFrames, Math.round(1500 / 40) + 0, `the first word was clipped: ${speechFrames} of ${Math.round(1500 / 40)} speech frames kept`);
console.log('7 ok: barge-in stops Computer once and keeps your whole first word');

// c) speech while Computer is only thinking is ignored (no second, overlapping turn)
h = harness();
h.core.listen(); h.core.busy();
h.frames(0.005, 400, QUIET); h.frames(0.1, 1500, SPEECH); h.frames(0.005, 1500, QUIET);
assert.equal(h.events.utterances.length, 0);
console.log('8 ok: nothing recorded while Computer is thinking');

// d) a cough while listening is not an utterance
h = harness();
h.core.listen();
h.frames(0.005, 400, QUIET); h.frames(0.1, 160, SPEECH); h.frames(0.005, 2500, QUIET);
assert.equal(h.events.utterances.length, 0);
console.log('9 ok: a cough is not an utterance');

// e) after Computer stops speaking the state moves on, and listen() starts fresh
h = harness({ ref: { v: 0.1 } });
h.core.listen(); h.core.busy(); h.core.speaking(true);
assert.equal(h.core.mode, 'speaking');
h.core.speaking(false);
assert.equal(h.core.mode, 'busy');
h.core.listen();
assert.equal(h.core.mode, 'listening');
console.log('10 ok: state changes');
