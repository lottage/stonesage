// Headless checks for "a fresh install shows only what is real" (docs/LAYERS.md, M6 rc.2). Run by tests/test_ui_features.py:
//   node tests/js/fresh_install.test.mjs <frontend dir>
// 1. js/net.js: concurrent GETs of a status endpoint share one network call; nothing else is merged.
// 2. js/features.js: data-needs elements are hidden until the server says the install has them; the header, the token tally and
//    the agent name come only from the answer; a tab the install lacks sends the page back to chat.
// 3. js/presence_garden.js: appliance monitors, suggested questions, sightings and irrigation come only from data.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { pathToFileURL } from 'node:url';

const dir = process.argv[2];
let failed = 0;
async function test(name, fn) {
  try { await fn(); console.log('ok  ', name); } catch (e) { failed++; console.log('FAIL', name, '\n', e.stack || e); }
}

// ---------------------------------------------------------------------------------------------------- net.js ----
function netWindow(realFetch) {
  const win = { fetch: realFetch };
  win.window = win;
  const ctx = vm.createContext({ window: win, location: { href: 'http://box:8888/', origin: 'http://box:8888' }, URL, Date, Promise });
  vm.runInContext(fs.readFileSync(path.join(dir, 'js', 'net.js'), 'utf8'), ctx);
  return win;
}
const answer = (body) => () => Promise.resolve({ status: 200, clone() { return { json: async () => body }; } });
let clock = Date.now();

await test('three callers, one request', async () => {
  let n = 0;
  const win = netWindow(() => { n++; return answer({ ok: true })(); });
  const all = await Promise.all([win.fetch('/api/engine/state/all'), win.fetch('/api/engine/state/all'), win.fetch('/api/engine/state/all')]);
  assert.equal(n, 1);
  for (const r of all) assert.deepEqual(await r.json(), { ok: true });
  await win.fetch('/api/engine/state/all');           // answered a moment ago: still shared
  assert.equal(n, 1);
});

await test('different endpoints, fresh=1, POSTs and unlisted paths are not merged', async () => {
  let n = 0;
  const win = netWindow(() => { n++; return answer({})(); });
  await win.fetch('/api/health/all'); await win.fetch('/api/health/all?fresh=1');
  assert.equal(n, 2);
  await win.fetch('/api/engine/state/all', { method: 'POST' }); await win.fetch('/api/engine/state/all', { method: 'POST' });
  assert.equal(n, 4);
  await win.fetch('/api/chat/sessions'); await win.fetch('/api/chat/sessions');
  assert.equal(n, 6);
  await win.fetch('http://elsewhere/api/health/all'); await win.fetch('http://elsewhere/api/health/all');
  assert.equal(n, 8);
});

await test('a failed request is not remembered', async () => {
  let n = 0;
  const win = netWindow(() => { n++; return n === 1 ? Promise.reject(new Error('down')) : answer({ ok: 1 })(); });
  await assert.rejects(win.fetch('/api/cluster/health'));
  const r = await win.fetch('/api/cluster/health');
  assert.equal(n, 2);
  assert.deepEqual(await r.json(), { ok: 1 });
});

// --------------------------------------------------------------------------------------------- a fake page ----
class El {
  constructor(id, attrs = {}) { this.id = id; this.attrs = attrs; this.hidden = 'hidden' in attrs; this.textContent = ''; this.children = []; this.dataset = {};
    this.style = {}; this.classList = { toggle() {}, add() {}, remove() {} }; this.innerHTML = ''; this.value = ''; this.disabled = false; }
  getAttribute(k) { return this.attrs[k] ?? null; }
  addEventListener() {}
  appendChild(c) { this.children.push(c); }
  closest() { return null; }
  querySelector() { return null; }
  querySelectorAll() { return []; }
}
function page(elements) {
  const byId = Object.fromEntries(elements.map((e) => [e.id, e]));
  const tabs = elements.filter((e) => e.attrs['data-tab']);
  global.window = global;
  global.document = {
    title: '', hidden: false,
    getElementById: (id) => byId[id] || null,
    querySelectorAll: (sel) => (sel === '[data-needs]' ? elements.filter((e) => 'data-needs' in e.attrs) : []),
    querySelector: (sel) => { const m = sel.match(/data-tab="(.+?)"/); return m ? tabs.find((t) => t.attrs['data-tab'] === m[1]) || null : null; },
    createElement: (tag) => new El('', { tag }),
    addEventListener() {},
  };
  global.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
  global.CustomEvent = class { constructor(type, init) { this.type = type; this.detail = init && init.detail; } };
  global.dispatchEvent = () => true;
  global.addEventListener = () => {};
  return byId;
}
const fresh = () => page([
  new El('tab-ws', { 'data-tab': 'workstation', 'data-needs': 'file_editor', hidden: '' }),
  new El('tab-term', { 'data-tab': 'terminal', 'data-needs': 'web_terminal', hidden: '' }),
  new El('tab-ha', { 'data-tab': 'ha', 'data-needs': 'app:hearth', hidden: '' }),
  new El('tab-ob', { 'data-tab': 'obsidian', 'data-needs': 'obsidian', hidden: '' }),
  new El('tab-chat', { 'data-tab': 'chat' }),
  new El('topbar-citadel-link', { 'data-needs': 'shelved', hidden: '' }),
  new El('topbar-nudge-btn', { 'data-needs': 'shelved', hidden: '' }),
  new El('topbar-tokens-badge', { hidden: '' }), new El('topbar-tokens-text'),
  new El('brand-version', { hidden: '' }), new El('brand-app', { hidden: '' }),
  new El('agent-option-default'), new El('computer-card-title'), new El('courage-ask-btn'),
]);

const featuresUrl = pathToFileURL(path.join(dir, 'js', 'features.js')).href;
const stateUrl = pathToFileURL(path.join(dir, 'js', 'state.js')).href;
const FRESH = { ok: true, product: 'StoneSage', version: '0.1.0', apps: [{ id: 'hearth', name: 'Hearth' }], agent: { id: 'courage-computer', name: 'Computer' },
  features: { web_terminal: false, file_editor: false, code_tools: false, raw_ha_call: false, shelved: false }, configured: {}, obsidian: false,
  providers: { gemini: false }, usage: null, home: {} };
const FULL = { ...FRESH, features: { ...FRESH.features, file_editor: true, web_terminal: true, shelved: true }, obsidian: true,
  usage: { cloud_tokens_today: 21500 }, agent: { id: 'courage-computer', name: 'Atlas' }, configured: { worker: true } };

// state.js and features.js are loaded once, against whichever page is current (they only touch document inside functions)
global.window = global;
global.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
global.addEventListener = () => {};
const state = await import(stateUrl);
const feat = await import(featuresUrl);
const apply = (els, data) => { feat.Features.data = data; state.State.featuresLoaded = true; feat.applyFeatures(); return els; };

await test('a fresh install hides every tab and button it has no source for', () => {
  const els = fresh();
  apply(els, FRESH);
  assert.equal(els['tab-ws'].hidden, true);
  assert.equal(els['tab-term'].hidden, true);
  assert.equal(els['tab-ob'].hidden, true);
  assert.equal(els['topbar-citadel-link'].hidden, true);
  assert.equal(els['topbar-nudge-btn'].hidden, true);
  assert.equal(els['tab-ha'].hidden, false, 'the Home app is loaded here');
  assert.equal(els['tab-chat'].hidden, false);
});

await test('the header says the product, its real version and the app; no token tally without a count', () => {
  const els = fresh();
  apply(els, FRESH);
  assert.equal(els['brand-version'].textContent, 'v0.1.0');
  assert.equal(els['brand-version'].hidden, false);
  assert.equal(els['brand-app'].textContent, 'Hearth');
  assert.equal(els['topbar-tokens-badge'].hidden, true);
  assert.equal(document.title, 'StoneSage · Hearth');
});

await test('a configured install shows its tabs, its token tally and its agent by name', () => {
  const els = fresh();
  apply(els, FULL);
  for (const id of ['tab-ws', 'tab-term', 'tab-ob', 'topbar-citadel-link', 'topbar-nudge-btn']) assert.equal(els[id].hidden, false, id);
  assert.equal(els['topbar-tokens-badge'].hidden, false);
  assert.equal(els['topbar-tokens-text'].textContent, 'Cloud today: 21.5k tokens');
  assert.equal(els['computer-card-title'].textContent, '💻 ATLAS');
  assert.equal(els['agent-option-default'].textContent, '✨ Atlas');
  assert.equal(els['courage-ask-btn'].textContent, '[🗣️ ASK ATLAS]');
});

await test('has() answers false for everything while the server has not spoken', () => {
  feat.Features.data = null;
  for (const need of ['shelved', 'file_editor', 'obsidian', 'app:hearth', 'service:worker', 'provider:gemini']) assert.equal(feat.has(need), false, need);
});

await test('a saved tab this install lacks sends the page back to chat', () => {
  const els = fresh();
  let went = null;
  global.window.switchTab = (t) => { went = t; };
  state.State.activeTab = 'workstation';
  apply(els, FRESH);
  assert.equal(went, 'chat');
  went = null; state.State.activeTab = 'chat';
  apply(els, FRESH);
  assert.equal(went, null);
});

// ----------------------------------------------------------------------------------------- presence panels ----
const presence = await import(pathToFileURL(path.join(dir, 'js', 'presence_garden.js')).href);

await test('appliance monitors exist only for appliances the server found', () => {
  const els = page([new El('presence-appliances-wrap', { hidden: '' }), new El('presence-appliances-box')]);
  presence.renderAppliances({});
  assert.equal(els['presence-appliances-wrap'].hidden, true);
  presence.renderAppliances({ dryer: { name: 'Dryer', is_running: true, remaining_time: '12' } });
  assert.equal(els['presence-appliances-wrap'].hidden, false);
  assert.match(els['presence-appliances-box'].innerHTML, /DRYER/);
  assert.match(els['presence-appliances-box'].innerHTML, /RUNNING \(12\)/);
  assert.doesNotMatch(els['presence-appliances-box'].innerHTML, /WASHER|LG/);
});

await test('suggested questions: the household first, laundry and temperature only when Home Assistant has them', () => {
  feat.Features.data = { ...FRESH, home: { questions: ["Who's home?", 'Is Alex home?', 'Where is Bella?'] } };
  assert.deepEqual(presence.presetQuestions({ appliances: {}, smart_home: {} }), ["Who's home?", 'Is Alex home?', 'Where is Bella?']);
  const q = presence.presetQuestions({ appliances: { washer: {} }, smart_home: { thermostat_state: 'heat' } });
  assert.deepEqual(q.slice(3), ['Is the laundry done?', 'What is the temperature inside?']);
  assert.ok(!presence.presetQuestions({ appliances: {}, smart_home: { thermostat_state: 'unknown' } }).some((x) => /temperature/.test(x)));
  feat.Features.data = { ...FRESH, home: {} };
  assert.deepEqual(presence.presetQuestions({}), ['Who is home?']);
});

await test('the sightings card needs cameras, and shows counts that come from the data', () => {
  const els = page(['sightings-card', 'sightings-stat-cameras', 'sightings-stat-inview', 'sightings-stat-last', 'sightings-updated', 'sightings-log-box']
    .map((id) => new El(id, id === 'sightings-card' ? { hidden: '' } : {})));
  feat.Features.data = { ...FRESH, home: { cameras: [], frigate: false } };
  presence.renderSightings({ locations: {}, recent_sightings: [] });
  assert.equal(els['sightings-card'].hidden, true);
  feat.Features.data = { ...FRESH, home: { cameras: [{ key: 'k', name: 'Kitchen' }], frigate: false } };
  presence.renderSightings({ locations: { bella: { minutes_ago: 0 }, rex: { minutes_ago: 30 } },
    recent_sightings: [{ timestamp: '10:00', entity: 'Bella', camera: 'Kitchen' }] });
  assert.equal(els['sightings-card'].hidden, false);
  assert.equal(els['sightings-stat-cameras'].textContent, '1');
  assert.equal(els['sightings-stat-inview'].textContent, '1');
  assert.equal(els['sightings-stat-last'].textContent, 'now');
  assert.match(els['sightings-log-box'].innerHTML, /Bella/);
  presence.renderSightings({ locations: {}, recent_sightings: [] });
  assert.equal(els['sightings-stat-last'].textContent, '—');
  assert.equal(els['sightings-log-box'].innerHTML, 'No sightings yet.');
});

await test('irrigation: no zones in config, no card and no request for its status', async () => {
  const els = page([new El('irrigation-card'), new El('irrigation-zones-grid')]);
  const asked = [];
  global.fetch = async (url) => { asked.push(url); return { ok: true, json: async () => ({ ...FRESH, home: { irrigation: { zones: [] } } }) }; };
  await feat.loadFeatures(true);
  const r = await presence.fetchIrrigationState();
  assert.equal(r, null);
  assert.equal(els['irrigation-card'].hidden, true);
  assert.deepEqual(asked, ['/api/ui/features']);
  global.fetch = async (url) => { asked.push(url); return url === '/api/ui/features'
    ? { ok: true, json: async () => ({ ...FRESH, home: { irrigation: { zones: [{ key: 'a', name: 'A' }], battery: false, rain_delay: false } } }) }
    : { ok: true, json: async () => ({ ok: true, data: { battery_level: 'unknown', rain_delay_active: false, zones: { a: { name: 'A', description: '', is_open: false, last_watered: 'unknown' } } } }) }; };
  els['irrigation-battery-badge'] = new El('irrigation-battery-badge');
  await feat.loadFeatures(true);
  await presence.fetchIrrigationState();
  assert.equal(els['irrigation-card'].hidden, false);
  assert.ok(asked.includes('/api/garden/status'));
});

console.log(failed ? `\n${failed} FAILED` : '\nall passed');
process.exit(failed ? 1 : 0);
