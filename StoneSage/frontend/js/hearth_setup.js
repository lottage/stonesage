/**
 * 🏠 Hearth setup (F5: Smart Home & CCTV, top of the page): the browser version of `python -m stonesage.first_run` for the Home half
 * of config.json. Four steps: 1) Home Assistant address + token (Test, save), 2) Discover people / phones / speakers from Home
 * Assistant and edit them, 3) cameras, 4) Save. A banner shows while GET /api/hearth/setup says the setup is incomplete.
 * Backend: backend/hearth/setup_wizard.py -- GET /api/hearth/setup, POST /api/hearth/setup/test-ha,
 * GET /api/hearth/setup/discover, POST /api/hearth/setup/save. The token is only ever sent to the server, never shown again.
 */

const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const keyOf = (name) => String(name || '').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');

const S = {
  status: null,       // GET /api/hearth/setup, null until read, false when the Hearth app is not installed
  open: false,
  url: '', token: '',
  test: null,         // {ok, text} from the last Test
  busy: '',
  people: [], pronouns: 'they/them', pets: [], phones: [], speakers: [], descriptions: {},
  cameras: [],        // {entity, name, checked, configured}
  discovered: null, hints: { services: [] },
  result: null,       // the last save's answer
  bound: false,
};

async function api(path, body) {
  try {
    const r = await fetch(path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const j = await r.json().catch(() => ({}));
    return { http: r.status, ...j };
  } catch (e) {
    return { ok: false, http: 0, error: 'Could not reach StoneSage.' };
  }
}

// ── form <-> status ────────────────────────────────────────────────────────
function fillFromStatus(st) {
  const hh = st.household || {};
  S.url = st.homeassistant.url || S.url;
  S.people = (hh.people || []).slice();
  S.pronouns = (hh.owner_pronouns || ['they', 'them']).join('/');
  S.pets = Object.entries(hh.pets || {}).map(([name, species]) => ({ name, species }));
  S.phones = Object.entries(hh.phones || {}).map(([key, service]) => ({ person: S.people.find((p) => keyOf(p) === key) || key, service }));
  S.speakers = Object.entries(hh.speakers || {}).map(([room, service]) => ({ room, service }));
  S.descriptions = { ...(hh.descriptions || {}) };
  const known = new Set(S.cameras.map((c) => c.entity));
  for (const c of st.cameras || []) {
    if (!known.has(c.entity)) S.cameras.push({ entity: c.entity, name: c.name, checked: true, configured: true });
  }
}

function applyDiscovery(d) {
  S.discovered = d;
  S.hints.services = [...d.phones.map((p) => p.service), ...d.speakers.map((s) => s.service)];
  for (const p of d.people) if (!S.people.some((n) => n.toLowerCase() === p.name.toLowerCase())) S.people.push(p.name);
  for (const ph of d.phones) {
    const who = S.people.find((n) => keyOf(n) === ph.person) || '';
    if (who && !S.phones.some((x) => x.service === ph.service || keyOf(x.person) === keyOf(who))) S.phones.push({ person: who, service: ph.service });
  }
  for (const sp of d.speakers) if (!S.speakers.some((x) => x.service === sp.service)) S.speakers.push({ room: sp.room, service: sp.service });
  for (const c of d.cameras) {
    const mine = S.cameras.find((x) => x.entity === c.entity || c.variants.includes(x.entity));
    if (!mine) S.cameras.push({ entity: c.entity, name: c.name, checked: false, configured: false });
  }
}

function buildSave(which) {
  const ha = {};
  if (S.url.trim()) ha.url = S.url.trim();
  if (S.token.trim()) ha.token = S.token.trim();
  if (which === 'connection') return { homeassistant: ha };
  const people = S.people.map((n) => n.trim()).filter(Boolean);
  const [first, second] = S.pronouns.split(/[\/,\s]+/).filter(Boolean);
  const household = {
    people,
    pets: S.pets.filter((p) => p.name.trim()).map((p) => ({ name: p.name.trim(), species: p.species.trim() })),
    phones: Object.fromEntries(S.phones.filter((p) => p.person && p.service.trim()).map((p) => [keyOf(p.person), p.service.trim()])),
    speakers: Object.fromEntries(S.speakers.filter((s) => s.room.trim() && s.service.trim()).map((s) => [keyOf(s.room), s.service.trim()])),
    descriptions: Object.fromEntries(Object.entries(S.descriptions).filter(([n, t]) => t.trim() && (people.includes(n) || S.pets.some((p) => p.name === n)))),
  };
  if (people.length) household.owner_pronouns = [first, second].filter(Boolean).length === 2 ? [first, second] : S.pronouns;
  const camera_ui = {};
  const camera_ui_remove = [];
  for (const c of S.cameras) {
    if (c.checked) camera_ui[c.entity] = { name: c.name.trim() };
    else if (c.configured) camera_ui_remove.push(c.entity);
  }
  return Object.keys(ha).length ? { homeassistant: ha, household, camera_ui, camera_ui_remove } : { household, camera_ui, camera_ui_remove };
}

// ── actions ────────────────────────────────────────────────────────────────
async function refreshStatus(fill) {
  const st = await api('/api/hearth/setup');
  S.status = st.ok ? st : false;
  if (st.ok && fill) fillFromStatus(st);
  render();
}

async function doTest() {
  S.busy = 'test'; S.test = null; render();
  const r = await api('/api/hearth/setup/test-ha', { url: S.url, token: S.token });
  S.test = r.ok ? { ok: true, text: `Connected${r.version ? ` to Home Assistant ${r.version}` : ''}${r.location ? ` ("${r.location}")` : ''}.` }
                : { ok: false, text: r.error || 'No answer.' };
  S.busy = ''; render();
}

async function doSave(which) {
  S.busy = 'save'; S.result = null; render();
  const r = await api('/api/hearth/setup/save', buildSave(which));
  S.result = r;
  S.busy = '';
  if (r.ok) {
    S.token = '';
    if (r.status) { S.status = r.status; if (which === 'connection') S.test = null; }
  }
  render();
}

async function doDiscover() {
  S.busy = 'discover'; render();
  const d = await api('/api/hearth/setup/discover');
  S.busy = '';
  if (d.ok) { applyDiscovery(d); S.result = { ok: true, message: `Found ${d.people.length} people, ${d.phones.length} phones, ${d.speakers.length} speakers, ${d.cameras.length} cameras. Check the lists below.` }; }
  else S.result = { ok: false, errors: [d.error || 'Discovery failed.'] };
  render();
}

// ── rendering ──────────────────────────────────────────────────────────────
const row = 'display:flex; gap:6px; align-items:center; margin-bottom:4px; flex-wrap:wrap;';
const inp = (bind, val, ph, w = 160, extra = '') =>
  `<input type="text" class="form-input" data-bind="${bind}" value="${esc(val)}" placeholder="${esc(ph)}" style="width:${w}px; font-size:0.78rem; padding:2px 6px;" ${extra}>`;
const del = (list, i) => `<button type="button" class="preset-btn" data-del="${list}:${i}" style="padding:1px 6px; font-size:0.7rem;" title="Remove">✗</button>`;
const add = (list, label) => `<button type="button" class="preset-btn" data-add="${list}" style="padding:1px 8px; font-size:0.72rem;">${label}</button>`;
const card = (n, title, body) => `
  <div class="harness-card" style="margin-bottom:8px; border:1px solid var(--term-border-dim);">
    <div style="font-weight:bold; font-size:0.8rem; margin-bottom:6px; color:var(--term-text-bright);">${n}) ${title}</div>${body}
  </div>`;

function banner() {
  const st = S.status;
  if (st === null || st === false) return '';
  if (!st.complete && !S.open) {
    return `<div style="border:1px solid var(--term-accent-gold); background:rgba(217,119,6,0.08); padding:6px 10px; margin-bottom:8px; font-size:0.8rem; display:flex; gap:8px; align-items:center; flex-wrap:wrap;">
      <span>🏠 <b>Hearth is not set up yet</b> — missing: ${esc(st.missing.join(', '))}.</span>
      <button type="button" class="term-cmd-btn" data-act="open" style="font-size:0.75rem;">[⚙ SET UP NOW]</button></div>`;
  }
  return S.open ? '' : `<div style="margin-bottom:6px; text-align:right;"><button type="button" class="term-cmd-btn" data-act="open" style="font-size:0.72rem;">[⚙ HEARTH SETUP]</button></div>`;
}

function resultBox() {
  const r = S.result;
  if (!r) return '';
  const ok = r.ok;
  const lines = ok
    ? [r.message, r.changed && r.changed.length ? `Changed: ${r.changed.join(', ')}.` : '', r.backup ? `Previous config kept as ${r.backup}.` : '',
       r.restart_needed ? 'Restart StoneSage for phone approvals to pick up the change.' : '', ...(r.notes || [])].filter(Boolean)
    : (r.errors || [r.error || 'Something went wrong.']);
  return `<div style="border:1px solid ${ok ? 'var(--term-text-bright)' : 'var(--term-accent-gold)'}; padding:6px 10px; margin:6px 0; font-size:0.78rem;">
    ${ok ? '✓' : '✗'} ${lines.map(esc).join('<br>')}</div>`;
}

function render() {
  const root = $('#hearth-setup-root');
  if (!root) return;
  if (S.status === false) { root.innerHTML = ''; return; }     // no Hearth app: nothing to set up
  if (!S.open) { root.innerHTML = banner(); return; }
  const tokenSet = S.status && S.status.homeassistant.token_set;
  const names = S.people.filter((n) => n.trim());
  const svcList = `<datalist id="hearth-services">${S.hints.services.map((s) => `<option value="${esc(s)}">`).join('')}</datalist>`;
  const peopleRows = S.people.map((n, i) => `<div style="${row}">${inp(`people.${i}`, n, 'Name', 160)}${i === 0 ? '<small style="color:var(--term-text-muted)">owner (approves actions)</small>' : ''}${del('people', i)}</div>`).join('');
  const petRows = S.pets.map((p, i) => `<div style="${row}">${inp(`pets.${i}.name`, p.name, 'Name', 140)}${inp(`pets.${i}.species`, p.species, 'cat, dog...', 100)}${del('pets', i)}</div>`).join('');
  const phoneRows = S.phones.map((p, i) => `<div style="${row}">
      <select class="form-select" data-bind="phones.${i}.person" style="font-size:0.78rem;">${[...new Set([...names, p.person].filter(Boolean))].map((n) => `<option ${n === p.person ? 'selected' : ''}>${esc(n)}</option>`).join('')}</select>
      ${inp(`phones.${i}.service`, p.service, 'mobile_app_..._phone', 240, 'list="hearth-services"')}${del('phones', i)}</div>`).join('');
  const speakerRows = S.speakers.map((s, i) => `<div style="${row}">${inp(`speakers.${i}.room`, s.room, 'room (or everywhere)', 150)}${inp(`speakers.${i}.service`, s.service, 'alexa_media_...', 240, 'list="hearth-services"')}${del('speakers', i)}</div>`).join('');
  const who = [...names, ...S.pets.map((p) => p.name.trim()).filter(Boolean)];
  const descRows = who.map((n) => `<div style="${row}"><span style="min-width:110px; font-size:0.78rem;">${esc(n)}</span>${inp(`desc.${n}`, S.descriptions[n] || '', 'e.g. dark hair, grey tabby (helps the camera name them)', 380)}</div>`).join('');
  const camRows = S.cameras.map((c, i) => `<div style="${row}">
      <label style="display:flex; gap:4px; align-items:center; font-size:0.76rem; min-width:260px;"><input type="checkbox" data-check="${i}" ${c.checked ? 'checked' : ''}><code>${esc(c.entity)}</code></label>
      ${inp(`cameras.${i}.name`, c.name, 'Display name', 180)}</div>`).join('');

  root.innerHTML = `
    <div class="harness-card" style="margin-bottom:12px; border:1px solid var(--term-accent-gold);">
      <div class="harness-card-title" style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
        <span>🏠 HEARTH SETUP — connect Home Assistant and tell Computer who lives here</span>
        <button type="button" class="term-cmd-btn" data-act="close" style="font-size:0.72rem;">[✕ CLOSE]</button>
      </div>
      ${card(1, 'Home Assistant', `
        <div style="${row}">${inp('url', S.url, 'http://homeassistant.local:8123', 280)}
          <input type="password" class="form-input" data-bind="token" value="${esc(S.token)}" autocomplete="off" placeholder="${tokenSet ? 'token saved: leave blank to keep it' : 'long-lived access token'}" style="width:280px; font-size:0.78rem; padding:2px 6px;"></div>
        <div style="${row}">
          <button type="button" class="term-cmd-btn" data-act="test" style="font-size:0.74rem;" ${S.busy ? 'disabled' : ''}>[${S.busy === 'test' ? '… TESTING' : '🔌 TEST'}]</button>
          <button type="button" class="term-cmd-btn" data-act="save-connection" style="font-size:0.74rem;" ${S.busy ? 'disabled' : ''}>[💾 SAVE CONNECTION]</button>
          ${S.test ? `<span style="font-size:0.76rem; color:${S.test.ok ? 'var(--term-text-bright)' : 'var(--term-accent-gold)'};">${S.test.ok ? '✓' : '✗'} ${esc(S.test.text)}</span>` : ''}
        </div>
        <small style="color:var(--term-text-muted)">Make the token on your Home Assistant profile page (bottom: Long-lived access tokens). It is stored in config.json on this machine only and never shown again.</small>`)}
      ${card(2, 'People, pets, phones, speakers', `
        <div style="${row}"><button type="button" class="term-cmd-btn" data-act="discover" style="font-size:0.74rem;" ${S.busy || !(S.status && S.status.homeassistant.token_set) ? 'disabled' : ''}>[🔎 DISCOVER FROM HOME ASSISTANT]</button>
          <small style="color:var(--term-text-muted)">${S.status && S.status.homeassistant.token_set ? 'Read-only: it only suggests.' : 'Save the connection (step 1) first.'}</small></div>
        <div style="font-weight:bold; font-size:0.76rem; margin:6px 0 2px;">People (the first approves actions)</div>${peopleRows}${add('people', '[+ person]')}
        <div style="${row} margin-top:4px;"><span style="font-size:0.76rem;">Owner's pronouns</span>${inp('pronouns', S.pronouns, 'she/her', 100)}</div>
        <div style="font-weight:bold; font-size:0.76rem; margin:6px 0 2px;">Pets (Home Assistant doesn't know them)</div>${petRows}${add('pets', '[+ pet]')}
        <div style="font-weight:bold; font-size:0.76rem; margin:6px 0 2px;">Phones that get approvals (Home Assistant notify service)</div>${phoneRows}${add('phones', '[+ phone]')}
        <div style="font-weight:bold; font-size:0.76rem; margin:6px 0 2px;">Speakers Computer can talk through</div>${speakerRows}${add('speakers', '[+ speaker]')}
        <details style="margin-top:6px;"><summary style="font-size:0.76rem; cursor:pointer;">Descriptions (optional)</summary>${descRows || '<small>Add people or pets first.</small>'}</details>
        ${svcList}`)}
      ${card(3, 'Cameras', `${camRows || '<small style="color:var(--term-text-muted)">No cameras yet. Discover from Home Assistant (step 2) lists them.</small>'}
        <small style="color:var(--term-text-muted)">Ticked cameras appear in the LIVE CAMERAS tab and Computer can look through them.</small>`)}
      ${card(4, 'Save', `<div style="${row}"><button type="button" class="term-cmd-btn" data-act="save" style="font-size:0.78rem; background:var(--term-accent-gold); color:#000; font-weight:bold;" ${S.busy ? 'disabled' : ''}>[${S.busy === 'save' ? '… SAVING' : '💾 SAVE EVERYTHING'}]</button>
        <small style="color:var(--term-text-muted)">Keeps every other setting; the previous config.json is backed up first.</small></div>`)}
      ${resultBox()}
    </div>`;
}

// ── events (delegated; typing updates the model without re-rendering, so focus stays) ──
function setBound(path, value) {
  const p = path.split('.');
  if (p[0] === 'url' || p[0] === 'token' || p[0] === 'pronouns') { S[p[0]] = value; return; }
  if (p[0] === 'desc') { S.descriptions[p.slice(1).join('.')] = value; return; }
  if (p[0] === 'people') { S.people[+p[1]] = value; return; }
  const item = S[p[0]][+p[1]];
  if (item) item[p[2]] = value;
}

function bind() {
  if (S.bound) return;
  const root = $('#hearth-setup-root');
  if (!root) return;
  S.bound = true;
  root.addEventListener('input', (e) => { const b = e.target.dataset && e.target.dataset.bind; if (b) setBound(b, e.target.value); });
  root.addEventListener('change', (e) => {
    const t = e.target;
    if (t.dataset.bind) setBound(t.dataset.bind, t.value);
    if (t.dataset.check !== undefined) S.cameras[+t.dataset.check].checked = t.checked;
  });
  root.addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    if (b.dataset.add) {
      const l = b.dataset.add;
      if (l === 'people') S.people.push('');
      else if (l === 'pets') S.pets.push({ name: '', species: '' });
      else if (l === 'phones') S.phones.push({ person: S.people.find((n) => n.trim()) || '', service: '' });
      else if (l === 'speakers') S.speakers.push({ room: '', service: '' });
      render();
    } else if (b.dataset.del) {
      const [l, i] = b.dataset.del.split(':');
      S[l].splice(+i, 1);
      render();
    } else if (b.dataset.act === 'open') { S.open = true; refreshStatus(true); }
    else if (b.dataset.act === 'close') { S.open = false; S.result = null; refreshStatus(false); }
    else if (b.dataset.act === 'test') doTest();
    else if (b.dataset.act === 'save-connection') doSave('connection');
    else if (b.dataset.act === 'save') doSave('all');
    else if (b.dataset.act === 'discover') doDiscover();
  });
}

export async function openHearthSetup() {
  bind();
  await refreshStatus(false);
}

window.openHearthSetup = openHearthSetup;
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => { openHearthSetup(); });
else openHearthSetup();
