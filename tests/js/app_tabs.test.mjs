// Headless check of the engine console's app tabs (StoneSage/frontend/js/engine_console.js: loadAppTabs / openAppTab).
// Run by tests/test_app_plugins.py; needs node. Usage: node app_tabs.test.mjs <path to engine_console.js>
// The block between the two section comments is cut out of the file and run against a fake DOM, so the real code is what is tested.
import fs from 'node:fs';
import assert from 'node:assert/strict';

const src = fs.readFileSync(process.argv[2], 'utf8');
const a = src.indexOf('// ─── Tabs that apps bring');
const b = src.indexOf('// ─── Sub-Layer Switching');
assert.ok(a > 0 && b > a, 'the app-tab block is in engine_console.js');
const block = src.slice(a, b).replace('await import(tab.script)', 'await __import(tab.script)');

class El {
  constructor(tag) { this.tag = tag; this.children = []; this.style = {}; this.dataset = {}; this.id = ''; this.className = ''; this.type = ''; this.textContent = ''; this.handlers = {}; }
  addEventListener(name, fn) { this.handlers[name] = fn; }
  appendChild(c) { this.children.push(c); return c; }
  insertBefore(c, ref) { const i = this.children.indexOf(ref); if (i < 0) this.children.push(c); else this.children.splice(i, 0, c); return c; }
  all() { return this.children.flatMap((c) => [c, ...c.all()]); }
  querySelector(sel) { const m = sel.match(/data-layer="([^"]+)"/); return m ? (this.children.find((c) => c.dataset.layer === m[1]) || null) : null; }
  set innerHTML(v) { throw new Error('innerHTML must not be used'); }
}

function world(apps) {
  const nav = new El('div'); nav.className = 'econsole-subnav';
  for (const layer of ['dashboard', 'trace', 'debug', 'logs']) { const btn = new El('button'); btn.className = 'econsole-subnav-btn'; btn.dataset.layer = layer; nav.appendChild(btn); }
  const host = new El('div'); host.id = 'engine-layer-console'; host.appendChild(nav);
  const doc = {
    querySelector(sel) {
      if (sel === '.econsole-subnav') return nav;
      const m = sel.match(/data-layer="([^"]+)"/);
      if (m) return nav.children.find((c) => c.dataset.layer === m[1]) || null;
      return null;
    },
    getElementById(id) { return id === 'engine-layer-console' ? host : (host.all().find((e) => e.id === id) || null); },
    createElement: (tag) => new El(tag),
  };
  const win = {};
  const opened = [];
  const warned = [];
  const fetchFn = async (url) => ({ ok: true, json: async () => ({ ok: true, apps }) });
  const imported = [];
  const factory = new Function('document', 'window', 'fetch', 'switchConsoleSublayer', 'console', '__import',
    block + '\nreturn { appTabs, loadAppTabs, openAppTab };');
  const api = factory(doc, win, fetchFn, (id) => opened.push(id), { warn: (...x) => warned.push(x.join(' ')) }, async (url) => { imported.push(url); });
  return { api, nav, host, win, opened, warned, imported };
}

const TAB = { id: 'fake', label: 'FAKE', script: '/apps/fakeapp/board.js', open: 'openFake', target: 'engine_console', hide_engine_tabs: true };

// 1. no apps: nothing is added
{
  const w = world([]);
  await w.api.loadAppTabs();
  assert.equal(w.nav.children.length, 4);
  assert.equal(w.host.children.length, 1);
}
// 2. an app with a tab: a button before "debug", a pane with the root, and nothing is imported until the tab is opened
{
  const w = world([{ id: 'fakeapp', tabs: [TAB] }]);
  await w.api.loadAppTabs();
  const labels = w.nav.children.map((c) => c.dataset.layer);
  assert.deepEqual(labels, ['dashboard', 'trace', 'fake', 'debug', 'logs']);
  const btn = w.nav.children[2];
  assert.equal(btn.textContent, '[FAKE]');
  const pane = w.host.children[1];
  assert.equal(pane.id, 'econsole-layer-fake');
  assert.equal(pane.className, 'econsole-layer');
  assert.equal(pane.style.display, 'none');
  assert.equal(pane.children[0].id, 'fake-root');
  assert.deepEqual(w.imported, []);
  btn.handlers.click();
  assert.deepEqual(w.opened, ['fake']);
  // opening: the script is imported, then window[open]() is called
  let called = 0;
  w.win.openFake = () => { called += 1; };
  await w.api.openAppTab(w.api.appTabs.fake);
  assert.deepEqual(w.imported, ['/apps/fakeapp/board.js']);
  assert.equal(called, 1);
}
// 3. a tab that is not allowed to load is ignored: wrong target, an id with markup, a script outside /apps/, a pane that exists already
{
  const evil = [
    { ...TAB, id: 'x', target: 'somewhere_else' },
    { ...TAB, id: '<img src=x onerror=alert(1)>' },
    { ...TAB, id: 'y', script: 'https://evil.example/x.js' },
    { ...TAB, id: 'z', script: '//evil.example/x.js' },
    { ...TAB, id: 'trace' },
  ];
  const w = world([{ id: 'bad', tabs: evil }]);
  const pre = w.host.children.length;
  w.host.appendChild(Object.assign(new El('div'), { id: 'econsole-layer-trace' }));
  await w.api.loadAppTabs();
  assert.equal(w.nav.children.length, 4, 'no button was added');
  assert.equal(w.host.children.length, pre + 1, 'no pane was added');
}
// 4. a label is text, never markup; a failing script is a warning, not an exception
{
  const w = world([{ id: 'fakeapp', tabs: [{ ...TAB, label: '<b onmouseover=x>FAKE</b>' }] }]);
  await w.api.loadAppTabs();
  assert.equal(w.nav.children[2].textContent, '[<b onmouseover=x>FAKE</b>]');
  const f = new Function('document', 'window', 'fetch', 'switchConsoleSublayer', 'console', '__import',
    block + '\nreturn { appTabs, openAppTab };')({}, {}, async () => ({ ok: false }), () => {}, { warn: (...x) => w.warned.push(x.join(' ')) },
    async () => { throw new Error('boom'); });
  await f.openAppTab({ id: 'q', script: '/apps/q/x.js', open: 'openQ' });
  assert.ok(w.warned.some((m) => m.includes('did not load')));
}
// 5. a server that answers an error leaves the page as it was
{
  const w = world([]);
  const f = new Function('document', 'window', 'fetch', 'switchConsoleSublayer', 'console', '__import',
    block + '\nreturn { loadAppTabs };')({ querySelector: () => { throw new Error('touched the page'); } }, {}, async () => ({ ok: false }), () => {}, console, async () => {});
  await f.loadAppTabs();
}
console.log('app tabs: all checks passed');
