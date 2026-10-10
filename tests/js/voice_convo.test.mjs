// Headless checks of conversation mode's end-of-speech detector and speech queue (StoneSage/frontend/js/chat.js).
// Run by tests/test_voice_js.py; needs node. Usage: node voice_convo.test.mjs <path to chat.js>
import fs from 'fs';
import vm from 'vm';
import assert from 'assert';

import { pathToFileURL } from 'url';
const mod = await import(pathToFileURL(process.argv[3]).href);
const src = fs.readFileSync(process.argv[2], 'utf8');
const grab = (name) => {
  const start = src.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `function ${name} not found`);
  let i = src.indexOf('{', start), depth = 0;
  for (; i < src.length; i++) {
    if (src[i] === '{') depth++;
    if (src[i] === '}' && --depth === 0) break;
  }
  return src.slice(start, i + 1);
};

// ---- the end-of-speech detector, fed synthetic audio levels every 40 ms --------------------------------------------------
const ctx0 = vm.createContext({});
vm.runInContext(grab('createEndpointer') + '\nthis.make = createEndpointer;', ctx0);
const make = (opts) => ctx0.make(opts);

/** segments: [level, ms] pairs. Returns the events with the time they fired. */
function drive(segments, opts) {
  const ep = make(opts);
  const events = [];
  let t = 0, last = 'waiting';
  for (const [level, ms] of segments) {
    for (let k = 0; k < ms; k += 40, t += 40) {
      const wobble = level * (1 + 0.15 * Math.sin(t / 37));                       // a level is never perfectly flat
      const r = ep.push(wobble, t);
      if (r !== last && r !== 'waiting' || r === 'end' || r === 'noise' || r === 'noSpeech') {
        if (!events.length || events[events.length - 1][0] !== r) events.push([r, t]);
      }
      last = r;
    }
  }
  return events;
}
const names = (ev) => ev.map((e) => e[0]).join(',');
const QUIET = 0.005, TALK = 0.09;

// 1. quiet, speak, quiet: ends about 0.8 s after the speech stops, not before
let ev = drive([[QUIET, 400], [TALK, 1500], [QUIET, 2000]]);
assert.equal(names(ev), 'speech,end', names(ev));
const speechStopsAt = 400 + 1500;
const endAt = ev.find((e) => e[0] === 'end')[1];
assert.ok(endAt - speechStopsAt >= 760 && endAt - speechStopsAt <= 900, `ended ${endAt - speechStopsAt} ms after speech stopped`);
console.log('1 ok: ended', endAt - speechStopsAt, 'ms after the last word');

// 2. a 500 ms pause inside the sentence does not end it
ev = drive([[QUIET, 400], [TALK, 800], [QUIET, 500], [TALK, 900], [QUIET, 2000]]);
assert.equal(names(ev), 'speech,end', names(ev));
const stop2 = 400 + 800 + 500 + 900;
const end2 = ev.find((e) => e[0] === 'end')[1];
assert.ok(end2 - stop2 >= 760, `ended ${end2 - stop2} ms after the last word: the pause left a stale silence timer`);
console.log('2 ok: pause of 500 ms kept the take open');

// 3. a cough (150 ms) is ignored and listening goes on; the real sentence is still caught
ev = drive([[QUIET, 400], [TALK, 150], [QUIET, 1200], [TALK, 1200], [QUIET, 2000]]);
assert.equal(names(ev), 'speech,noise,speech,end', names(ev));
console.log('3 ok: blip ignored, sentence caught');

// 4. silence only: gives up after 12 s
ev = drive([[QUIET, 13000]]);
assert.equal(names(ev), 'noSpeech', names(ev));
assert.ok(ev[0][1] >= 12000 && ev[0][1] < 12100, String(ev[0][1]));
console.log('4 ok: silence dropped at', ev[0][1], 'ms');

// 5. a room with a TV on (steady 0.03) is not speech, and speech over it ends when it stops
ev = drive([[0.03, 600], [0.15, 1300], [0.03, 2500]]);
assert.equal(names(ev), 'speech,end', names(ev));
ev = drive([[0.03, 5000]]);
assert.equal(names(ev), '', 'steady TV noise alone counted as speech: ' + names(ev));
console.log('5 ok: steady room noise ignored');

// 6. talking straight away (during the noise-floor measurement) is still caught
ev = drive([[0.1, 1500], [QUIET, 2000]]);
assert.equal(names(ev), 'speech,end', names(ev));
console.log('6 ok: started talking at once');

// 7. quiet speech (0.045) in a quiet room is caught
ev = drive([[QUIET, 400], [0.045, 1500], [QUIET, 2000]]);
assert.equal(names(ev), 'speech,end', names(ev));
console.log('7 ok: soft speaker caught');

// the real delay before a filler speaks (the scenarios below shorten it): a wait under ~1 s is not worth a filler,
// and one over ~2.5 s leaves the user in silence
const delayMs = Number(/const FILLER_DELAY_MS = (\d+);/.exec(src)[1]);
assert.ok(delayMs >= 800 && delayMs <= 2500, `FILLER_DELAY_MS is ${delayMs}`);

// ---- the speech queue's filler --------------------------------------------------------------------------------------------
async function scenario(run, { delay = 60 } = {}) {
  const requested = [], played = [], speakingCalls = [];
  const audio = { set src(v) { this._src = v; }, get src() { return this._src; },
    play() { played.push(this._src); setTimeout(() => this.onended && this.onended(), 5); return Promise.resolve(); }, pause() {} };
  const c = {
    window: { _convoMode: true }, console, setTimeout, Promise, JSON, FILLER_DELAY_MS: delay,
    getConvoAudioPlayer: () => audio, updateConvoStatus: () => {}, convoStartRecording: () => {},
    convoSpeakingHook: (on) => speakingCalls.push(on), wavEnvelope: () => null, base64ToBytes: () => new Uint8Array(0),
    fetch: async (url, opts) => { const text = JSON.parse(opts.body).text; requested.push(text);
      return { json: async () => ({ ok: true, audio_base64: text }) }; },
  };
  vm.createContext(c);
  vm.runInContext(grab('speechText') + '\n' + grab('createConvoSpeech') + '\nthis.make = createConvoSpeech;', c);
  const speech = c.make();
  await run(speech);
  await new Promise((r) => setTimeout(r, 400));
  return { requested, speakingCalls, played: played.map((p) => p.replace('data:audio/wav;base64,', '')) };
}
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

// 8. a slow tool call: the filler is spoken first, then the real reply (whose first sentence still goes out on its own)
let r = await scenario(async (s) => {
  s.filler('Let me have a look.');
  await wait(200);                                            // still waiting: filler is due
  s.push('The driveway is empty. Nobody has been there for an hour.');
  s.end();
});
assert.deepEqual(r.played.slice(0, 2), ['Let me have a look.', 'The driveway is empty.'], r.played.join(' | '));
console.log('8 ok: filler then reply');

// 9. the reply arrives before the delay: no filler
r = await scenario(async (s) => {
  s.filler('One moment.');
  await wait(10);
  s.push('Bella is on the couch. She has been there all morning.');
  s.end();
});
assert.ok(!r.played.includes('One moment.'), r.played.join(' | '));
assert.equal(r.played[0], 'Bella is on the couch.');
console.log('9 ok: fast answer, no filler');

// 10. only one filler per turn
r = await scenario(async (s) => {
  s.filler('One moment.'); s.filler('Let me have a look.'); s.filler('One moment.');
  await wait(200);
  s.push('Done.'); s.end();
});
assert.equal(r.played.filter((p) => p === 'One moment.' || p === 'Let me have a look.').length, 1, r.played.join(' | '));
console.log('10 ok: one filler per turn');

// 11. a filler alone does not stand in for the reply: end() says nothing was spoken, so the page speaks the whole reply itself
let ended;
r = await scenario(async (s) => { s.filler('One moment.'); await wait(200); ended = s.end(); });
assert.equal(ended, false);
console.log('11 ok: filler is not the reply');

// 12. barge-in: stop() cuts Computer off: nothing queued is spoken, listening is NOT restarted by the queue, text() says what it was saying
r = await scenario(async (s) => {
  s.push('The driveway is empty. Nobody has been there for an hour. The gate is closed. ');
  await wait(30);
  s.stop();
  s.push('This must never be spoken.');
  s.end();
});
assert.ok(r.played.length <= 1, `spoke on after being stopped: ${r.played.join(' | ')}`);
assert.ok(!r.played.some((p) => p.includes('never be spoken')));
console.log('12 ok: stop() ends the speech');

let heardText;
r = await scenario(async (s) => {
  s.push('Bella is on the couch. '); s.push('She has been there all morning.');
  heardText = s.text();
});
assert.equal(heardText, 'Bella is on the couch. She has been there all morning.');
assert.ok(r.speakingCalls.includes(true), 'the listener is told when Computer starts talking');
console.log('13 ok: text() and the speaking hook');

// ---- the echo check in the real submit function ----------------------------------------------------------------------------
async function submit({ transcript, info, spoken, strikes = 0 }) {
  const submitted = [], restarted = [], status = [];
  const win = { _convoLastSpoken: spoken, _convoEchoStrikes: strikes, _convoT: null };
  const c = {
    window: win, console, setTimeout, JSON, performance: { now: () => 0 },
    document: { getElementById: () => ({ style: {}, value: '' }) },
    fetch: async () => ({ json: async () => ({ ok: true, text: transcript, engine: 'parakeet', ms: 120 }) }),
    updateConvoStatus: (t) => status.push(t), refreshBargeButton: () => {}, looksLikeEcho: echo,
    submitPrompt: () => submitted.push(transcript), convoStartRecording: () => restarted.push(1),
  };
  vm.createContext(c);
  vm.runInContext('async ' + grab('convoSubmitAudio') + '\nthis.go = convoSubmitAudio;', c);   // grab() starts at "function"
  await c.go('AAAA', 'audio/wav', info);
  await new Promise((r) => setTimeout(r, 700));
  return { submitted, restarted, win, status };
}
const { looksLikeEcho: echo } = mod;
const spokenText = 'The driveway is empty. Nobody has been there for an hour.';
let x = await submit({ transcript: 'the driveway is empty', info: { barge: true }, spoken: spokenText });
assert.equal(x.submitted.length, 0, 'Computer hearing itself was sent to the chat');
assert.equal(x.win._convoEchoStrikes, 1);
assert.equal(x.restarted.length, 1, 'keeps listening');
x = await submit({ transcript: 'the driveway is empty', info: { barge: true }, spoken: spokenText, strikes: 1 });
assert.equal(x.win._bargeDisabled, true, 'two strikes switch interrupt off');
x = await submit({ transcript: 'actually turn on the porch light', info: { barge: true }, spoken: spokenText });
assert.deepEqual(x.submitted, ['actually turn on the porch light']);
x = await submit({ transcript: 'the driveway is empty', info: null, spoken: spokenText });           // not a barge take: no echo check
assert.equal(x.submitted.length, 1);
console.log('14 ok: echo takes discarded, two strikes disable, real speech and normal takes go through');
