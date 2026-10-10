/**
 * ⚡ Boost: free extra inference (Engine Studio → Engine Console → ⚡ BOOST).
 * Per-surface switches, free sources with today's quota and tier, a test button per source, key entry
 * (write-only: keys are never sent back), and the frontier worker (Claude/Gemini CLI jobs on VM 102).
 * Backend: /api/boost/* (backend/boost). When chat is switched on, the topbar model menu gains "boost" entries.
 */

const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const SURFACES = [
  ['chat', 'Chat', 'the model menu gains ⚡ Boost entries'],
  ['courage', 'Computer', 'think_harder tool for hard questions'],
  ['loops', 'Harness loops', '/node use boost; max background share of each quota'],
  ['workspaces', 'Workspaces', 'workspace chat on Boost; frontier jobs'],
];
const TIER = {
  local: ['🏠', 'our hardware: anything but secrets'],
  no_training: ['🏠', 'not retained: may see home text, never pictures or secrets'],
  training: ['🔓', 'prompts may train their models: general and code only'],
};
const S = { status: null, frontier: null, jobs: [], busy: {}, tests: {}, timer: null };

async function api(path, body) {
  const res = await fetch(path, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {});
  return res.json();
}

function bar(used, limit) {
  if (!limit) return `<small>${used} today</small>`;
  const pct = Math.min(100, Math.round((used / limit) * 100));
  const color = pct > 85 ? 'var(--term-error, #ef4444)' : pct > 60 ? 'var(--term-accent-gold, #eab308)' : 'var(--term-success, #22c55e)';
  return `<div title="${used} / ${limit}" style="height:6px;background:rgba(127,127,127,.25);border-radius:3px;overflow:hidden;margin-top:3px">
      <div style="width:${pct}%;height:100%;background:${color}"></div></div><small>${used} / ${limit} requests today</small>`;
}

function providerRow(p) {
  const u = p.usage || {};
  const [icon, tierHelp] = TIER[p.tier] || ['?', p.tier];
  const test = S.tests[p.id];
  const state = !p.enabled ? '<span style="opacity:.6">disabled</span>'
    : !p.has_key ? `<span style="color:var(--term-accent-gold,#eab308)">no key</span>`
      : u.cooldown_s ? `<span style="color:var(--term-accent-gold,#eab308)">cooling ${u.cooldown_s}s</span>` : '<span style="color:var(--term-success,#22c55e)">ready</span>';
  const keyField = p.key_env
    ? `<input type="password" class="form-input bst-key" data-pid="${esc(p.id)}" placeholder="${p.has_key ? 'key set (type to replace)' : esc(p.key_env)}" style="width:170px;font-size:.72rem;padding:2px 6px" autocomplete="off">`
    : '';
  return `<div class="harness-card" style="padding:8px;margin-bottom:6px">
    <div style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap;align-items:center">
      <div><b>${esc(p.label)}</b> <span title="${esc(tierHelp)}">${icon} ${esc(p.tier.replace('_', '-'))}</span> · ${state}
        <br><small style="opacity:.75">${esc((p.models || []).slice(0, 3).join(', ') || (p.live_models || []).slice(0, 3).join(', ') || 'models from the live list')}</small></div>
      <div style="display:flex;gap:6px;align-items:center">
        ${keyField}
        <label style="font-size:.72rem"><input type="checkbox" class="bst-penabled" data-pid="${esc(p.id)}" ${p.enabled ? 'checked' : ''}> on</label>
        <button type="button" class="term-cmd-btn bst-test" data-pid="${esc(p.id)}" ${S.busy[p.id] ? 'disabled' : ''} style="font-size:.72rem">[TEST]</button>
      </div>
    </div>
    ${bar(u.requests_today || 0, u.rpd)}
    ${u.last_error ? `<div><small style="color:var(--term-error,#ef4444)">${esc(u.last_error)}</small></div>` : ''}
    ${test ? `<div><small>${test.ok ? '✅' : '❌'} ${esc(test.text)}</small></div>` : ''}
  </div>`;
}

function frontierBlock() {
  const f = S.frontier;
  if (!f) return '<small>Checking the frontier worker…</small>';
  const eng = f.engines || {};
  const line = (e) => {
    const x = eng[e];
    if (!x) return `<li>${e}: <small>worker unreachable</small></li>`;
    return `<li><b>${e}</b>: ${x.installed ? 'installed' : '<span style="color:var(--term-accent-gold,#eab308)">not installed / not logged in</span>'} · ${x.jobs_today}/${x.cap} jobs today${x.busy ? ' · busy' : ''}</li>`;
  };
  const jobs = (S.jobs || []).slice(0, 6).map((j) => `<li><a href="#" class="bst-job" data-id="${esc(j.id)}">${esc(j.id)}</a> ${esc(j.engine)} · ${esc(j.state)}${j.error ? ` · <small>${esc(j.error).slice(0, 80)}</small>` : ''}</li>`).join('');
  return `<div>${f.enabled ? '' : '<small style="opacity:.7">Off (config.json boost.frontier.enabled). </small>'}${f.error ? `<small>${esc(f.error)}</small>` : ''}
    <ul style="margin:4px 0 8px 18px;padding:0">${line('claude')}${line('gemini')}</ul>
    <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:flex-start">
      <select id="bst-fr-engine" class="form-select" style="font-size:.75rem"><option value="claude">Claude (Pro)</option><option value="gemini">Gemini (AI Pro)</option></select>
      <input id="bst-fr-repo" class="form-input" placeholder="git repo on VM 102 (optional)" style="width:220px;font-size:.75rem">
      <textarea id="bst-fr-task" class="form-input" rows="2" placeholder="Task (code or general only; home details are refused)" style="flex:1;min-width:220px;font-size:.75rem"></textarea>
      <button type="button" class="term-cmd-btn" id="bst-fr-send" ${f.enabled ? '' : 'disabled'} style="font-size:.72rem">[HAND OFF]</button>
    </div>
    <ul style="margin:6px 0 0 18px;padding:0">${jobs || '<li><small>No jobs yet.</small></li>'}</ul>
    <pre id="bst-fr-result" style="display:none;max-height:260px;overflow:auto;font-size:.7rem;white-space:pre-wrap"></pre></div>`;
}

function render() {
  const root = $('#boost-root');
  if (!root) return;
  const st = S.status;
  if (!st) { root.innerHTML = '<div style="padding:1rem">Loading Boost…</div>'; return; }
  if (st.ok === false) { root.innerHTML = `<div style="padding:1rem">${esc(st.error)}</div>`; return; }
  const surf = SURFACES.map(([id, label, help]) => `<label style="display:flex;gap:6px;align-items:center" title="${esc(help)}">
      <input type="checkbox" class="bst-surface" data-surface="${id}" ${st.surfaces?.[id] ? 'checked' : ''}> ${label} <small style="opacity:.65">${esc(help)}</small></label>`).join('');
  root.innerHTML = `
    <div class="harness-card-title" style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
      <span>⚡ BOOST — free extra inference (local stays the default and the last fallback)</span>
      <label><input type="checkbox" id="bst-enabled" ${st.enabled ? 'checked' : ''}> <b>Boost on</b></label>
    </div>
    <div class="harness-card" style="padding:8px;margin-bottom:10px;display:grid;gap:4px">${surf}
      <label style="font-size:.78rem">Background share (loops + workspaces) of each daily quota:
        <input type="number" id="bst-share" class="form-input" min="0" max="100" step="5" value="${Math.round((st.loop_share ?? 0.5) * 100)}" style="width:60px;font-size:.75rem;padding:2px 4px">%</label>
      <small style="opacity:.7">Order tried: ${esc((st.priority || []).join(' → '))} → local. Home content only goes to 🏠 sources; pictures and secrets never leave.</small>
    </div>
    <div>${(st.providers || []).map(providerRow).join('')}</div>
    <div style="display:flex;gap:6px;margin:6px 0 12px"><button type="button" class="term-cmd-btn" id="bst-save-keys" style="font-size:.72rem">[SAVE KEYS]</button>
      <small style="opacity:.7">Keys are stored in config.json on StoneSage (or set them in /etc/stonesage/secrets.env) and never shown again.</small></div>
    <div class="harness-card-title" style="margin-bottom:6px">🧠 FRONTIER WORKER — Claude Code / Gemini CLI on VM 102 (your subscriptions, scratch clones, diff back)</div>
    <div class="harness-card" style="padding:8px">${frontierBlock()}</div>`;
}

async function save(update) {
  S.status = await api('/api/boost/settings', update);
  render();
  syncModelMenu();
}

function bind() {
  const root = $('#boost-root');
  if (!root || root.dataset.bound) return;
  root.dataset.bound = '1';
  root.addEventListener('change', (e) => {
    const t = e.target;
    if (t.id === 'bst-enabled') save({ enabled: t.checked });
    else if (t.classList.contains('bst-surface')) save({ surfaces: { [t.dataset.surface]: t.checked } });
    else if (t.id === 'bst-share') save({ loop_share: Math.max(0, Math.min(100, Number(t.value) || 0)) / 100 });
    else if (t.classList.contains('bst-penabled')) save({ providers: { [t.dataset.pid]: { enabled: t.checked } } });
  });
  root.addEventListener('click', async (e) => {
    const t = e.target;
    if (t.classList.contains('bst-test')) {
      const pid = t.dataset.pid;
      S.busy[pid] = true; render();
      const r = await api('/api/boost/test', { provider: pid });
      S.tests[pid] = { ok: r.ok, text: r.ok ? `${r.model}: "${r.reply}"` : [...(r.skipped || []), ...(r.tried || [])].join('; ') || 'failed' };
      S.busy[pid] = false;
      await refresh();
    } else if (t.id === 'bst-save-keys') {
      const providers = {};
      root.querySelectorAll('.bst-key').forEach((inp) => { if (inp.value.trim()) providers[inp.dataset.pid] = { api_key: inp.value.trim() }; });
      if (Object.keys(providers).length) await save({ providers });
    } else if (t.id === 'bst-fr-send') {
      const task = $('#bst-fr-task').value.trim();
      if (!task) return;
      const r = await api('/api/boost/frontier/jobs', { engine: $('#bst-fr-engine').value, task, repo: $('#bst-fr-repo').value.trim() || null });
      showResult(r.ok ? `Job ${r.id} queued.` : `Refused: ${r.error}`);
      await refreshFrontier();
    } else if (t.classList.contains('bst-job')) {
      e.preventDefault();
      const j = await api(`/api/boost/frontier/jobs/${encodeURIComponent(t.dataset.id)}`);
      showResult(j.ok === false ? j.error : `${j.state}${j.error ? `: ${j.error}` : ''}\n\n${j.answer || ''}\n\n${j.diff || '(no diff)'}`);
    }
  });
}

function showResult(text) {
  const pre = $('#bst-fr-result');
  if (pre) { pre.style.display = 'block'; pre.textContent = text; }
}

// Topbar model menu (built by fleet.js updateModelSelectors): "⚡ Boost (auto)" plus one pinned entry per ready
// source, only while Boost chat is on. fleet.js re-renders the menu on its next poll.
function syncModelMenu() {
  if (!S.status) return;
  const on = S.status.enabled && S.status.surfaces?.chat;
  const entries = !on ? [] : [{ id: 'boost', name: '⚡ Boost (free pool, auto)', short: 'Boost', desc: 'free cloud sources, local fallback', online: true, latency: null }]
    .concat((S.status.providers || []).filter((p) => p.enabled && p.has_key && !p.id.startsWith('edge:'))
      .map((p) => ({ id: `boost:${p.id}`, name: `⚡ ${p.label}`, short: p.label, desc: p.tier, online: !(p.usage || {}).cooldown_s, latency: null })));
  window.boostModelEntries = entries;
}

async function refresh() {
  try { S.status = await api('/api/boost/status'); } catch (err) { S.status = { ok: false, error: String(err) }; }
  render();
  syncModelMenu();
}

async function refreshFrontier() {
  try {
    S.frontier = await api('/api/boost/frontier/health');
    const j = await api('/api/boost/frontier/jobs');
    S.jobs = j.jobs || [];
  } catch (err) { S.frontier = { error: String(err) }; }
  render();
}

export async function openBoost() {
  bind();
  await refresh();
  refreshFrontier();
  if (S.timer) clearInterval(S.timer);
  S.timer = setInterval(() => { if ($('#econsole-layer-boost')?.style.display !== 'none') refresh(); }, 15000);
}

window.openBoost = openBoost;
// fill the model menu on page load even if the Boost panel is never opened
setTimeout(refresh, 2500);
