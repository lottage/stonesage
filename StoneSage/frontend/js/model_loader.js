/**
 * Model Loader: pick a node/engine, pick a model from that node's library, size it against the real GPU or
 * unified memory, review the exact change, apply it with live progress and automatic rollback.
 * Backend: /api/loader/* (backend/model_loader.py). All names and numbers come from the live stack.
 */

const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const gb = (v) => (v == null ? '?' : `${Number(v).toFixed(v >= 10 ? 1 : 2)} GB`);
const kfmt = (n) => (n >= 1024 ? `${Math.round(n / 1024)}k` : String(n));
const KV_TYPES = ['f16', 'q8_0', 'q5_1', 'q5_0', 'q4_1', 'q4_0'];

const S = {
  state: null, target: null, node: null, library: [], libNode: null,
  sel: null, params: {}, plan: null, filter: 'llm', query: '', sort: 'fit',
  review: null, job: null, jobTimer: null, planTimer: null, loading: false,
  profiles: null, profJob: null, profTimer: null,
};

async function api(path, body) {
  const res = await fetch(path, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {});
  return res.json();
}

// ------------------------------------------------------------- targets ----
function allTargets() {
  const out = [];
  for (const n of S.state?.nodes || []) {
    if (n.kind === 'llama-host') n.targets.forEach((t) => out.push({ ...t, node: n }));
    else out.push({ id: n.id, role: 'lmstudio', node: n, editable: n.online, model: n.loaded[0]?.name, lm: n });
  }
  return out;
}

function nodeIdFor(target) {
  return target.startsWith('engine:') ? 'host:inference' : target;
}

function renderTopology() {
  const nodes = S.state?.nodes || [];
  return nodes.map((n) => {
    if (n.kind === 'llama-host') {
      const gpus = n.gpus.map((g) => {
        const segs = n.targets.filter((t) => t.gpu_index === g.index);
        const used = segs.reduce((a, t) => a + (t.vram_gb || 0), 0);
        const other = Math.max(0, g.used_gb - used);
        const bar = segs.map((t) => `<span class="ml-seg ml-seg-${esc(t.role)} ${S.target === t.id ? 'on' : ''}" style="width:${(t.vram_gb / g.total_gb) * 100}%" title="${esc(t.role)}: ${esc(t.model)} ${gb(t.vram_gb)}"></span>`).join('')
          + `<span class="ml-seg ml-seg-other" style="width:${(other / g.total_gb) * 100}%" title="other ${gb(other)}"></span>`;
        return `<div class="ml-gpu"><div class="ml-gpu-head"><b>${esc(g.short)}</b><span>${gb(g.used_gb)} / ${gb(g.total_gb)}</span></div><div class="ml-bar">${bar}</div></div>`;
      }).join('');
      const chips = n.targets.map((t) => `
        <button type="button" class="ml-chip ${S.target === t.id ? 'active' : ''} ${t.editable ? '' : 'locked'}" data-target="${esc(t.id)}" ${t.editable ? '' : 'title="Managed with memory search; not swappable here"'}>
          <span class="ml-dot ${t.online ? 'up' : 'down'}"></span><b>${esc(t.role)}</b> :${esc(t.port)}
          <small>${esc(t.model || 'no model')}${t.ctx_per_slot ? ` · ${kfmt(t.ctx_per_slot)}×${t.slots || 1}` : ''}${t.draft_model ? ' · +draft' : ''}</small>
        </button>`).join('');
      return `<div class="ml-node"><div class="ml-node-head">🖥️ <b>${esc(n.name)}</b> <small>${esc(n.cpu || '')}${n.ram_gb ? ` · ${n.ram_gb} GB RAM` : ''}</small></div>${gpus}<div class="ml-chips">${chips}</div></div>`;
    }
    const hw = n.hardware || {};
    const loaded = n.loaded.map((m) => esc(m.name)).join(', ');
    return `<div class="ml-node ml-edge"><div class="ml-node-head">🎮 <b>${esc(n.name)}</b> <small>${esc([hw.cpu, hw.ram_gb && `${hw.ram_gb} GB unified`].filter(Boolean).join(' · '))}</small></div>
      <div class="ml-chips"><button type="button" class="ml-chip ${S.target === n.id ? 'active' : ''} ${n.online ? '' : 'locked'}" data-target="${esc(n.id)}">
        <span class="ml-dot ${n.online ? 'up' : 'down'}"></span><b>LM Studio</b><small>${n.online ? (loaded || `idle · ${n.model_count} models`) : 'offline'}</small></button></div></div>`;
  }).join('');
}

// -------------------------------------------------------------- library ----
function quickFit(m) {
  // client-side first guess for list dots; the server plan is authoritative for the selected model
  const t = allTargets().find((x) => x.id === S.target);
  if (!t || !m.size_gb) return 'unknown';
  let cap;
  if (t.lm) cap = t.lm.budget_gb;
  else {
    const sel = S.params.gpu_indexes?.length ? S.params.gpu_indexes : [t.gpu_index];
    const gs = t.node.gpus.filter((x) => sel.includes(x.index));
    if (!gs.length) return 'unknown';
    const others = t.node.targets.filter((x) => x.id !== t.id && sel.includes(x.gpu_index)).reduce((a, x) => a + x.vram_gb, 0);
    cap = gs.reduce((a, g) => a + g.total_gb, 0) - others - 0.35 * gs.length;
  }
  const need = m.size_gb * 1.08 + 0.5;
  return need <= cap * 0.92 ? 'fits' : need <= cap ? 'tight' : 'too_big';
}

function renderLibrary() {
  if (S.loading) return '<div class="ml-empty">Reading model headers…</div>';
  const q = S.query.toLowerCase();
  let items = S.library.filter((m) => (S.filter === 'all' || (S.filter === 'loaded' ? m.is_loaded : S.filter === 'fits' ? quickFit(m) !== 'too_big' && m.kind === 'llm' : m.kind === S.filter)));
  if (q) items = items.filter((m) => `${m.name} ${m.file} ${m.arch} ${m.params} ${m.quant}`.toLowerCase().includes(q));
  const order = { fits: 0, tight: 1, unknown: 2, too_big: 3 };
  items.sort((a, b) => S.sort === 'size' ? b.size_gb - a.size_gb : S.sort === 'name' ? a.name.localeCompare(b.name)
    : (order[quickFit(a)] - order[quickFit(b)]) || (b.is_loaded - a.is_loaded) || b.size_gb - a.size_gb);
  if (!items.length) return '<div class="ml-empty">No models match.</div>';
  // provenance (Phase 6): which llama-server builds can load each model; tag the ones not every build can run
  const builds = new Set(S.library.flatMap((m) => m.loadable_by || []));
  return items.map((m) => {
    const fit = quickFit(m);
    const needs = m.loadable_by && m.loadable_by.length < builds.size
      ? `<i class="ml-bad-text" title="Only these llama-server builds can load it (tensor types / architecture)">needs ${esc(m.loadable_by.join(' / ') || 'a build not installed')}</i>` : '';
    return `<button type="button" class="ml-model ${S.sel?.key === m.key ? 'active' : ''}" data-model="${esc(m.key)}">
      <span class="ml-fit ml-fit-${fit}" title="${fit.replace('_', ' ')}"></span>
      <span class="ml-model-name">${esc(m.name)}${m.is_loaded ? ' <em>loaded</em>' : ''}</span>
      <span class="ml-tags">${[m.params, m.quant, m.arch, m.trained_ctx && `${kfmt(m.trained_ctx)} ctx`, gb(m.size_gb)].filter(Boolean).map((x) => `<i>${esc(x)}</i>`).join('')}${m.vision ? '<i>vision</i>' : ''}${m.tools ? '<i>tools</i>' : ''}${needs}</span>
    </button>`;
  }).join('');
}

// ----------------------------------------------------------------- plan ----
// Provenance from the GGUF header (backend/provenance.py): base model, who quantized it, source, which builds run it
function lineageLine(m) {
  const l = m.lineage || {};
  const bits = [
    (l.base_models || []).length && `from ${l.base_models.map((b) => b.name || b.repo_url).join(', ')}`,
    l.finetune && `finetune: ${l.finetune}`, l.quantized_by && `quantized by ${l.quantized_by}`,
    l.source && `source ${l.source}`, m.loadable_by && `runs on ${m.loadable_by.join(' / ') || 'no installed build'}`,
    m.sha256 && `sha256 ${m.sha256.slice(0, 12)}`,
  ].filter(Boolean);
  return bits.length ? `<div class="ml-lineage"><small>${esc(bits.join(' · '))}</small></div>` : '';
}

function draftOptions() {
  if (!S.sel || !S.target?.startsWith('engine:')) return [];
  const p = parseFloat(S.sel.params) || 99;
  return S.library.filter((m) => m.kind === 'llm' && m.arch === S.sel.arch && m.key !== S.sel.key && (parseFloat(m.params) || 99) <= Math.max(2, p / 6));
}

function renderPlan() {
  const t = allTargets().find((x) => x.id === S.target);
  if (!t) return '<div class="ml-empty">Pick an engine or device above.</div>';
  if (!S.sel) return `<div class="ml-empty">Pick a model for <b>${esc(t.role)}</b> from the library.</div>`;
  const P = S.params;
  const isEngine = S.target.startsWith('engine:');
  const m = S.sel;
  const plan = S.plan;
  const mem = plan?.memory;
  const trained = m.trained_ctx || 131072;
  // slider spans what is useful here (twice what fits, at least 32k), the number box allows anything up to trained
  const sliderMax = Math.min(trained, Math.max(32768, Math.ceil(((plan?.max_ctx_per_slot || 0) * 2) / 1024) * 1024, P.ctx_per_slot));
  const cap = mem?.capacity_gb || 1;
  const pct = (v) => `${Math.max(0, Math.min(100, (v / Math.max(cap + (mem?.others_gb || 0), mem?.total_gb + (mem?.others_gb || 0), 0.01)) * 100))}%`;
  const verdict = plan ? { fits: ['ok', 'Fits'], tight: ['warn', 'Tight fit'], too_big: ['bad', 'Does not fit'] }[plan.verdict] : ['', 'Sizing…'];
  const gpus = isEngine ? t.node.gpus : [];
  const drafts = draftOptions();
  return `
    <div class="ml-plan-head">
      <div><b>${esc(m.name)}</b> <small>${esc([m.params, m.quant, m.arch].filter(Boolean).join(' · '))}</small></div>
      <div class="ml-verdict ml-${verdict[0]}">${verdict[1]}${plan?.calibrated ? ' · calibrated' : ''}</div>
    </div>
    ${lineageLine(m)}
    <div class="ml-membar" title="${esc(plan?.budget_label || '')}">
      ${mem ? `<span class="ml-m ml-m-other" style="width:${pct(mem.others_gb)}" title="other engines ${gb(mem.others_gb)}"></span>
      <span class="ml-m ml-m-weights" style="width:${pct(mem.weights_gb)}" title="weights ${gb(mem.weights_gb)}"></span>
      <span class="ml-m ml-m-kv" style="width:${pct(mem.kv_gb)}" title="KV cache ${gb(mem.kv_gb)}"></span>
      <span class="ml-m ml-m-compute" style="width:${pct(mem.compute_gb + mem.draft_gb)}" title="compute + draft ${gb(mem.compute_gb + mem.draft_gb)}"></span>` : ''}
    </div>
    <div class="ml-legend">${mem ? `<span><i class="ml-m-weights"></i>weights ${gb(mem.weights_gb)}</span><span><i class="ml-m-kv"></i>KV ${gb(mem.kv_gb)}</span><span><i class="ml-m-compute"></i>compute${mem.draft_gb ? '+draft' : ''} ${gb(mem.compute_gb + mem.draft_gb)}</span><span><i class="ml-m-other"></i>others ${gb(mem.others_gb)}</span><span><b>${gb(mem.total_gb)}</b> of ${gb(mem.capacity_gb)}</span>` : ''}</div>
    <div class="ml-budget">${esc(plan?.budget_label || '')}</div>

    <div class="ml-form">
      <label class="ml-wide">Context per slot${plan?.max_ctx_per_slot ? ` <small>(fits up to ${plan.max_ctx_per_slot.toLocaleString()} · trained ${trained.toLocaleString()})</small>` : ''}
        <span class="ml-ctx"><input type="range" data-p="ctx_per_slot" min="1024" max="${sliderMax}" step="1024" value="${Math.min(P.ctx_per_slot, sliderMax)}">
        <input type="number" data-p="ctx_per_slot" min="512" max="${trained}" step="512" value="${P.ctx_per_slot}"></span></label>
      ${isEngine ? `<label>Slots <small>parallel requests</small>
        <span class="ml-ctx"><input type="range" data-p="slots" min="1" max="8" value="${P.slots}"><input type="number" data-p="slots" min="1" max="16" value="${P.slots}"></span></label>
      <label>GPU layers <small>of ${m.layers ? m.layers + 1 : '?'} (layers + output)</small>
        <span class="ml-ctx"><input type="range" data-p="ngl" min="0" max="${m.layers ? m.layers + 1 : 99}" value="${Math.min(P.ngl ?? 99, m.layers ? m.layers + 1 : 99)}"><input type="number" data-p="ngl" min="0" max="999" value="${P.ngl ?? 99}"></span></label>
      <label>K cache type <select data-p="kv_cache_k">${KV_TYPES.map((k) => `<option ${P.kv_cache_k === k ? 'selected' : ''}>${k}</option>`).join('')}</select></label>
      <label>V cache type <select data-p="kv_cache_v">${KV_TYPES.map((k) => `<option ${P.kv_cache_v === k ? 'selected' : ''}>${k}</option>`).join('')}</select></label>
      <label>Flash attention <select data-p="flash_attn">${['on', 'off', 'auto'].map((k) => `<option ${P.flash_attn === k ? 'selected' : ''}>${k}</option>`).join('')}</select>
        ${P.flash_attn === 'off' && (P.kv_cache_v !== 'f16' || P.kv_cache_k !== 'f16') ? '<small class="ml-bad-text">quantized KV needs flash attention</small>' : ''}</label>
      <fieldset class="ml-gpus"><legend>GPUs <small>${(P.gpu_indexes || []).length > 1 ? 'split across these' : 'pick one, or several to split'}</small></legend>
        ${gpus.map((g) => `<label class="ml-check"><input type="checkbox" data-gpu="${g.index}" ${(P.gpu_indexes || []).includes(g.index) ? 'checked' : ''}> ${esc(g.short)} <small>${g.total_gb} GB · ${gb(g.used_gb)} used</small></label>`).join('')}</fieldset>
      <label class="ml-wide">Speculative draft model <select data-p="draft"><option value="">none</option>${drafts.map((d) => `<option value="${esc(d.key)}" ${P.draft === d.key ? 'selected' : ''}>${esc(d.name)} ${esc(d.quant || '')} (${gb(d.size_gb)})</option>`).join('')}</select>
        ${drafts.length ? '' : `<small>No smaller ${esc(m.arch || '')} model on this host to draft with.</small>`}</label>` : ''}
    </div>
    <div class="ml-quick">
      ${plan?.max_ctx_per_slot ? `<button type="button" data-quick="max">Max context that fits</button>` : ''}
      ${isEngine ? '<button type="button" data-quick="current">Current settings</button>' : ''}
      ${isEngine ? '<button type="button" data-quick="two">2 slots, most context</button>' : ''}
    </div>
    ${(plan?.notes || []).map((n) => `<div class="ml-note">• ${esc(n)}</div>`).join('')}
    ${renderOptions()}
    <div class="ml-actions">
      <button type="button" class="ml-primary" data-act="review" ${plan && plan.verdict !== 'too_big' ? '' : 'disabled'}>Review change →</button>
      ${plan?.verdict === 'too_big' ? '<span class="ml-bad-text">Lower the context, slots or KV precision, or pick a smaller quant.</span>' : ''}
    </div>
    ${renderReview()}`;
}

// ------------------------------------------------------ all engine options ----
const SECTION_LABELS = { common: 'Model loading & hardware', sampling: 'Sampling defaults', speculative: 'Speculative decoding',
  'example-specific': 'Server behaviour', load: 'LM Studio load settings' };
// options that change the memory plan: re-plan when they change
const PLAN_FLAGS = new Set(['--ubatch-size', '--n-cpu-moe', '--cpu-moe', '--kv-offload', 'parallel', 'physical_batch_size', 'offload_kv_cache_to_gpu']);

function optValue(o) {
  const ch = S.params.options || {};
  if (Object.prototype.hasOwnProperty.call(ch, o.flag)) return ch[o.flag];
  return S.opts?.current?.[o.flag];
}

function floatRange(o) {
  const d = parseFloat(o.default_value);
  if (/p$|-p-|prob|min-p|top-p|typical|threshold/.test(o.flag)) return [0, 1, 0.01];
  if (/temp/.test(o.flag)) return [0, 2, 0.01];
  if (/penalty|multiplier|base|scale/.test(o.flag)) return [0, Math.max(2, (d || 1) * 2), 0.01];
  return null;
}

function optControl(o) {
  const v = optValue(o);
  const dis = o.hidden || (o.needs_spec && !S.params.draft && !(optValue({ flag: '--spec-type' }) || '').startsWith('ngram')) ? 'disabled' : '';
  const a = `data-opt="${esc(o.flag)}" ${dis}`;
  if (o.kind === 'switch') {
    return `<label class="ml-check"><input type="checkbox" ${a} ${v === true ? 'checked' : ''} ${o.required_on ? 'disabled title="StoneSage needs this"' : ''}> ${v === true ? 'on' : 'off'}</label>`;
  }
  if (o.kind === 'tristate') {
    const cur = v === true ? 'on' : v === false ? 'off' : '';
    return `<select ${a}><option value="" ${cur === '' ? 'selected' : ''}>default${o.default_value ? ` (${esc(o.default_value)})` : ''}</option><option value="on" ${cur === 'on' ? 'selected' : ''}>on</option><option value="off" ${cur === 'off' ? 'selected' : ''}>off</option></select>`;
  }
  if (o.kind === 'enum') {
    const labels = o.choice_labels || o.choices;
    return `<select ${a}><option value="">default${o.default_value ? ` (${esc(o.default_value)})` : ''}</option>${o.choices.map((c, i) => `<option value="${esc(c)}" ${String(v) === c ? 'selected' : ''}>${esc(labels[i])}</option>`).join('')}</select>`;
  }
  if (o.kind === 'int' || o.kind === 'float') {
    const range = o.kind === 'float' ? floatRange(o) : (o.min != null && o.max != null && o.max - o.min <= 100000 ? [o.min, o.max, 1] : null);
    const ph = o.default_value ? `default ${o.default_value}` : '';
    const num = `<input type="number" ${a} ${o.min != null ? `min="${o.min}"` : ''} ${o.max != null ? `max="${o.max}"` : ''} step="${o.kind === 'float' ? 'any' : 1}" placeholder="${esc(ph)}" value="${v ?? ''}">`;
    if (!range) return num;
    const rv = v ?? o.default_value ?? range[0];
    return `<span class="ml-ctx"><input type="range" ${a} data-range="1" min="${range[0]}" max="${range[1]}" step="${range[2]}" value="${esc(rv)}">${num}</span>`;
  }
  return `<input type="text" ${a} placeholder="${esc(o.default ? `default ${o.default}` : o.arg || '')}" value="${esc(v ?? '')}">`;
}

function renderOptions() {
  if (!S.opts) return S.sel ? '<div class="ml-note">Reading the engine\'s options…</div>' : '';
  const q = (S.optQuery || '').toLowerCase();
  const changed = S.params.options || {};
  const list = (S.opts.options || []).filter((o) => !o.managed)
    .filter((o) => S.optShowAll || !o.hidden)
    .filter((o) => !S.optOnlySet || optValue(o) !== undefined && optValue(o) !== null)
    .filter((o) => !q || `${o.names.join(' ')} ${o.label || ''} ${o.desc} ${o.env || ''}`.toLowerCase().includes(q));
  const groups = {};
  list.forEach((o) => { (groups[o.section] = groups[o.section] || []).push(o); });
  const nChanged = Object.keys(changed).length;
  const rows = (o) => {
    const isChanged = Object.prototype.hasOwnProperty.call(changed, o.flag);
    const isSet = optValue(o) !== undefined && optValue(o) !== null;
    return `<div class="ml-opt ${isChanged ? 'changed' : ''} ${o.hidden ? 'unavail' : ''}">
      <div class="ml-opt-name"><code>${esc(o.label || o.flag)}</code>${o.label ? ` <small>${esc(o.flag)}</small>` : ` <small>${esc(o.names.filter((n) => n !== o.flag).join(' '))}</small>`}
        ${isSet && !isChanged ? '<em>set</em>' : ''}${isChanged ? '<em class="chg">changed</em>' : ''}</div>
      <div class="ml-opt-ctl">${optControl(o)}${isChanged || isSet ? `<button type="button" class="ml-reset" data-reset="${esc(o.flag)}" title="${isChanged ? 'undo' : 'back to engine default'}">↺</button>` : ''}</div>
      <div class="ml-opt-desc">${esc(o.hidden_reason ? `Unavailable: ${o.hidden_reason}. ` : '')}${esc(o.hint ? `${o.hint}. ` : '')}${esc(o.desc)}${o.env ? ` <small>env ${esc(o.env)}</small>` : ''}</div>
    </div>`;
  };
  return `<details class="ml-options ml-wide" ${S.optOpen ? 'open' : ''} data-optpanel="1">
    <summary>All ${S.opts.engine === 'lmstudio' ? 'LM Studio load' : 'llama-server'} options <small>${(S.opts.options || []).filter((o) => !o.managed).length} available${S.opts.build ? ` · ${esc(S.opts.build.split('\n')[0])}` : ''}${nChanged ? ` · <b>${nChanged} changed</b>` : ''}</small></summary>
    <div class="ml-opt-tools">
      <input type="search" id="ml-opt-search" placeholder="Filter options (name, description, env var)" value="${esc(S.optQuery || '')}">
      <label class="ml-check"><input type="checkbox" data-optflag="optOnlySet" ${S.optOnlySet ? 'checked' : ''}> only set</label>
      <label class="ml-check"><input type="checkbox" data-optflag="optShowAll" ${S.optShowAll ? 'checked' : ''}> show unavailable</label>
    </div>
    ${Object.entries(groups).map(([sec, os]) => `<details class="ml-opt-group" ${q || S.optOnlySet || os.some((o) => Object.prototype.hasOwnProperty.call(changed, o.flag)) || S.opts.engine === 'lmstudio' ? 'open' : ''}>
      <summary>${esc(SECTION_LABELS[sec] || sec)} <small>${os.length}</small></summary>${os.map(rows).join('')}</details>`).join('') || '<div class="ml-empty">No options match.</div>'}
  </details>`;
}

async function loadOptions() {
  if (!S.target || !S.sel) { S.opts = null; return; }
  const gpus = (S.params.gpu_indexes || []).join(',');
  S.opts = await api(`/api/loader/options?target=${encodeURIComponent(S.target)}&model=${encodeURIComponent(S.sel.key)}&gpus=${gpus}`);
  paint();
}

function renderReview() {
  if (S.job) {
    const j = S.job;
    const cls = { done: 'ok', rolled_back: 'warn', failed: 'bad' }[j.status] || '';
    return `<div class="ml-review"><div class="ml-review-head">Applying… <span class="ml-verdict ml-${cls}">${esc(j.status || 'running')}</span></div>
      <ol class="ml-steps">${(j.steps || []).map((s) => `<li><small>${s.t}s</small> ${esc(s.text)}</li>`).join('')}</ol>
      ${j.log ? `<pre class="ml-diff">${esc(j.log)}</pre>` : ''}
      ${j.status && j.status !== 'running' ? '<button type="button" data-act="close">Close</button>' : ''}</div>`;
  }
  if (!S.review) return '';
  const r = S.review;
  if (!r.ok) return `<div class="ml-review"><div class="ml-bad-text">${esc(r.error)}</div></div>`;
  const body = r.kind === 'unit'
    ? `<pre class="ml-diff">${esc(r.diff).split('\n').map((l) => `<span class="${l.startsWith('+') && !l.startsWith('+++') ? 'add' : l.startsWith('-') && !l.startsWith('---') ? 'del' : ''}">${l}</span>`).join('\n')}</pre>`
    : `<pre class="ml-diff">${esc(JSON.stringify(r.request, null, 2))}</pre>`;
  return `<div class="ml-review"><div class="ml-review-head">${esc(r.summary)}</div>${body}
    <div class="ml-actions"><button type="button" class="ml-primary" data-act="apply" ${r.kind === 'unit' && !r.changed ? 'disabled' : ''}>Apply</button><button type="button" data-act="cancel">Cancel</button></div></div>`;
}

// ------------------------------------------------------------- profiles ----
// Engine Profiles (backend/engine_profiles.py, config.json "engine_profiles"): named "which model where" bundles.
const PROF_BADGE = { active: ['ml-ok', 'live'], available: ['', 'ready'], model_missing: ['ml-bad', 'missing'], unspecified: ['ml-warn', 'no model'] };

function renderProfiles() {
  const list = S.profiles?.profiles || [];
  if (!list.length) return '';
  const j = S.profJob;
  const chips = list.map((p) => {
    const t = Object.entries(p.targets);
    const live = t.length && t.every(([, v]) => v.status === 'active');
    const rows = t.map(([id, v]) => {
      const [cls, word] = PROF_BADGE[v.status] || ['ml-bad', v.status];
      return `<small title="${esc(v.model || '')}">${esc(id.split(':')[1])}: <span class="${cls}">${esc(word)}</span></small>`;
    }).join('');
    const blocked = !p.unlocked || live || (j && j.status === 'running');
    const why = !p.unlocked ? `Needs project phase ${p.min_phase} (now ${S.profiles.project_phase})` : live ? 'Already live' : p.description;
    return `<button type="button" class="ml-chip ${live ? 'active' : ''} ${blocked ? 'locked' : ''}" data-profile="${esc(p.name)}" title="${esc(why)}">
      <b>${!p.unlocked ? '🔒 ' : ''}${esc(p.name)}</b>${rows}</button>`;
  }).join('');
  let status = '';
  if (j) {
    const cls = { done: 'ok', partial: 'warn' }[j.status] || '';
    const res = Object.entries(j.results || {}).map(([id, r]) => `<li><b>${esc(id)}</b> ${esc(r.skipped || r.error || r.status || '')}${(r.steps || []).length ? ` <small>(${esc(r.steps[r.steps.length - 1].text)})</small>` : ''}</li>`).join('');
    status = `<div class="ml-review"><div class="ml-review-head">Profile ${esc(j.profile)}: ${esc(j.step || '')} <span class="ml-verdict ml-${cls}">${esc(j.status)}</span></div>
      <ol class="ml-steps">${res}</ol>${j.status !== 'running' ? '<button type="button" data-act="profclose">Close</button>' : ''}</div>`;
  }
  return `<div class="ml-node ml-profiles"><div class="ml-node-head">📋 <b>Profiles</b> <small>one click, every listed engine; unlisted engines are left alone</small></div>
    <div class="ml-chips">${chips}</div>${status}</div>`;
}

async function loadProfiles() {
  S.profiles = await api('/api/engine-profiles').catch(() => null);
  paint();
}

async function applyProfile(name) {
  const p = (S.profiles?.profiles || []).find((x) => x.name === name);
  if (!p || !p.unlocked || S.profJob?.status === 'running') return;
  const todo = Object.entries(p.targets).filter(([, v]) => v.status === 'available').map(([id, v]) => `  ${id} → ${(v.model || '').split('/').pop()}`);
  if (!todo.length) return;
  if (!confirm(`Apply profile "${name}"?\n\nThese engines restart with a new model (each rolls back on its own if it fails):\n${todo.join('\n')}`)) return;
  const res = await api('/api/engine-profiles/apply', { name });
  if (!res.ok) { S.profJob = { profile: name, status: 'failed', step: res.error }; return paint(); }
  S.profJob = { profile: name, status: 'running', step: 'Starting…' }; paint();
  clearInterval(S.profTimer);
  S.profTimer = setInterval(async () => {
    S.profJob = await api(`/api/engine-profiles/job?id=${res.job}`);
    paint();
    if (S.profJob.status && S.profJob.status !== 'running') {
      clearInterval(S.profTimer);
      if (window.refreshProfile) window.refreshProfile();
      S.state = await api('/api/loader/state?fresh=1');
      S.libNode = null;
      loadProfiles();
    }
  }, 2000);
}

// ------------------------------------------------------------- behaviour ----
function paint() {
  const root = $('#model-loader-root');
  if (!root) return;
  root.innerHTML = `
    ${renderProfiles()}
    <div class="ml-topology">${renderTopology()}</div>
    <div class="ml-main">
      <section class="ml-library">
        <div class="ml-lib-head">
          <input type="search" id="ml-search" placeholder="Search models  ( / )" value="${esc(S.query)}">
          <select id="ml-sort"><option value="fit" ${S.sort === 'fit' ? 'selected' : ''}>Best fit</option><option value="size" ${S.sort === 'size' ? 'selected' : ''}>Largest</option><option value="name" ${S.sort === 'name' ? 'selected' : ''}>Name</option></select>
          <button type="button" data-act="refresh" title="Re-read hardware and model headers">⟳</button>
        </div>
        <div class="ml-filters">${['llm', 'fits', 'loaded', 'all'].map((f) => `<button type="button" data-filter="${f}" class="${S.filter === f ? 'active' : ''}">${{ llm: 'Chat models', fits: 'Fits here', loaded: 'Loaded', all: 'Everything' }[f]}</button>`).join('')}</div>
        <div class="ml-list">${renderLibrary()}</div>
      </section>
      <section class="ml-plan">${renderPlan()}</section>
    </div>`;
  const panel = root.querySelector('[data-optpanel]');
  if (panel) panel.addEventListener('toggle', () => { S.optOpen = panel.open; });
  const osearch = $('#ml-opt-search', root);
  if (osearch && S.focusOptSearch) { osearch.focus(); osearch.setSelectionRange(osearch.value.length, osearch.value.length); S.focusOptSearch = false; }
  const search = $('#ml-search', root);
  if (search && S.focusSearch) { search.focus(); search.setSelectionRange(search.value.length, search.value.length); S.focusSearch = false; }
}

function defaultsFor(target, model) {
  const t = allTargets().find((x) => x.id === target);
  if (target.startsWith('lmstudio:')) {
    const inst = t?.lm?.loaded[0]?.instances?.[0];
    return { ctx_per_slot: inst?.context_length || 8192, options: {} };
  }
  const sameModel = model && t && model.file === t.model_file;
  const draft = sameModel && t.draft_model_file ? (S.library.find((m) => m.file === t.draft_model_file) || {}).key || '' : '';
  const kv = (sameModel && t.kv_cache) || 'q8_0';
  return {
    ctx_per_slot: sameModel ? t.ctx_per_slot || 4096 : Math.min(8192, model?.trained_ctx || 8192),
    slots: sameModel ? t.slots || 1 : 1,
    kv_cache_k: kv, kv_cache_v: kv,
    gpu_indexes: t?.gpu_index != null ? [t.gpu_index] : [0],
    ngl: 99,
    flash_attn: (sameModel && t.flash_attn) || 'on',
    draft,
    options: {},
  };
}

async function selectTarget(id) {
  const t = allTargets().find((x) => x.id === id);
  if (!t || !t.editable) return;
  S.target = id; S.review = null; S.plan = null;
  const node = nodeIdFor(id);
  if (S.libNode !== node) {
    S.loading = true; S.library = []; paint();
    const lib = await api(`/api/loader/library?node=${encodeURIComponent(node)}`);
    S.library = lib.models || []; S.libNode = node; S.loading = false;
  }
  const current = S.library.find((m) => (t.model_file && m.file === t.model_file) || (t.lm && t.lm.loaded[0] && m.key === t.lm.loaded[0].key));
  S.sel = current || null;
  S.params = defaultsFor(id, S.sel);
  S.opts = null;
  paint();
  if (S.sel) { schedulePlan(0); loadOptions(); }
}

function payload(extra = {}) {
  const P = S.params;
  return { target: S.target, model: S.sel.key, ...P, kv_cache: P.kv_cache_v, gpu_index: (P.gpu_indexes || [])[0], ...extra };
}

function schedulePlan(delay = 250) {
  clearTimeout(S.planTimer);
  S.planTimer = setTimeout(async () => {
    if (!S.sel || !S.target) return;
    S.plan = await api('/api/loader/plan', payload());
    S.review = null;
    paint();
  }, delay);
}

async function pollJob(id) {
  clearInterval(S.jobTimer);
  S.jobTimer = setInterval(async () => {
    S.job = await api(`/api/loader/job?id=${id}`);
    paint();
    if (S.job.status && S.job.status !== 'running') {
      clearInterval(S.jobTimer);
      if (window.refreshProfile) window.refreshProfile();
      S.state = await api('/api/loader/state?fresh=1');
      S.libNode = null;
    }
  }, 1500);
}

async function onClick(e) {
  const reset = e.target.closest('[data-reset]');
  if (reset) {
    const f = reset.dataset.reset;
    const ch = S.params.options || (S.params.options = {});
    if (Object.prototype.hasOwnProperty.call(ch, f)) delete ch[f];   // undo my change
    else ch[f] = null;                                              // drop the flag: engine default
    paint();
    return PLAN_FLAGS.has(f) ? schedulePlan(0) : undefined;
  }
  const el = e.target.closest('[data-target],[data-model],[data-filter],[data-act],[data-quick],[data-profile]');
  if (!el) return;
  if (el.dataset.profile) return applyProfile(el.dataset.profile);
  if (el.dataset.target) return selectTarget(el.dataset.target);
  if (el.dataset.model) {
    const keepGpus = S.params.gpu_indexes;
    const keepOpts = S.params.options;
    S.sel = S.library.find((m) => m.key === el.dataset.model);
    S.params = { ...defaultsFor(S.target, S.sel), ...(keepGpus ? { gpu_indexes: keepGpus } : {}), options: keepOpts || {} };
    S.plan = null; S.review = null; S.opts = null; paint(); loadOptions(); return schedulePlan(0);
  }
  if (el.dataset.filter) { S.filter = el.dataset.filter; return paint(); }
  if (el.dataset.quick) {
    if (el.dataset.quick === 'current') S.params = defaultsFor(S.target, S.sel);
    if (el.dataset.quick === 'max' && S.plan?.max_ctx_per_slot) S.params.ctx_per_slot = S.plan.max_ctx_per_slot;
    if (el.dataset.quick === 'two') { S.params.slots = 2; S.params.ctx_per_slot = 1024; S.plan = await api('/api/loader/plan', payload()); S.params.ctx_per_slot = S.plan.max_ctx_per_slot || 2048; }
    return schedulePlan(0);
  }
  const act = el.dataset.act;
  if (act === 'refresh') { S.state = await api('/api/loader/state?fresh=1'); S.libNode = null; if (S.target) return selectTarget(S.target); return paint(); }
  if (act === 'review') { S.review = await api('/api/loader/preview', payload()); return paint(); }
  if (act === 'cancel') { S.review = null; return paint(); }
  if (act === 'profclose') { S.profJob = null; return paint(); }
  if (act === 'close') { S.job = null; S.review = null; return selectTarget(S.target); }
  if (act === 'apply') {
    const res = await api('/api/loader/apply', payload({ token: S.review.token }));
    if (!res.ok) { S.review = { ok: false, error: res.error }; return paint(); }
    S.job = { status: 'running', steps: [{ t: 0, text: 'Starting…' }] }; paint();
    return pollJob(res.job);
  }
}

function setOption(flag, value) {
  const o = (S.opts?.options || []).find((x) => x.flag === flag);
  const cur = S.opts?.current?.[flag];
  const ch = S.params.options || (S.params.options = {});
  const same = (value === cur) || (value !== null && cur != null && String(value) === String(cur)) || (value === null && cur === undefined);
  if (same) delete ch[flag]; else ch[flag] = value;
  if (o && PLAN_FLAGS.has(flag)) schedulePlan(0);
}

function onInput(e) {
  const el = e.target;
  if (el.id === 'ml-opt-search') { S.optQuery = el.value; S.focusOptSearch = true; return paint(); }
  if (el.dataset.optflag) { S[el.dataset.optflag] = el.checked; return paint(); }
  if (el.dataset.gpu != null) {
    const idx = parseInt(el.dataset.gpu, 10);
    const set = new Set(S.params.gpu_indexes || []);
    if (el.checked) set.add(idx); else set.delete(idx);
    if (!set.size) set.add(idx);  // at least one GPU
    S.params.gpu_indexes = [...set].sort();
    loadOptions();                // split options appear with 2+ GPUs
    return schedulePlan(0);
  }
  if (el.dataset.opt) {
    const o = (S.opts?.options || []).find((x) => x.flag === el.dataset.opt);
    if (!o) return;
    let v;
    if (o.kind === 'switch') v = el.checked ? true : null;
    else if (o.kind === 'tristate') v = el.value === 'on' ? true : el.value === 'off' ? false : null;
    else v = el.value === '' ? null : el.value;
    if (el.dataset.range && e.type === 'input') {  // live slider: mirror into the number box, commit on change
      const box = el.closest('.ml-ctx')?.querySelector('input[type=number]');
      if (box) box.value = el.value;
      return;
    }
    setOption(o.flag, v);
    return paint();
  }
  if (el.id === 'ml-search') { S.query = el.value; S.focusSearch = true; return paint(); }
  if (el.id === 'ml-sort') { S.sort = el.value; return paint(); }
  if (el.dataset.p) {
    const k = el.dataset.p;
    let v = el.value;
    if (['ctx_per_slot', 'slots', 'ngl'].includes(k)) v = parseInt(v, 10);
    S.params[k] = v;
    if (['ctx_per_slot', 'slots', 'ngl'].includes(k)) {
      const other = el.closest('.ml-ctx')?.querySelector(el.type === 'range' ? 'input[type=number]' : 'input[type=range]');
      if (other) other.value = v;
    }
    if (k === 'draft' || k === 'flash_attn' || k.startsWith('kv_cache')) paint();
    return schedulePlan(e.type === 'input' ? 300 : 0);
  }

}

export async function openModelLoader() {
  const root = $('#model-loader-root');
  if (!root) return;
  if (!root.dataset.bound) {
    root.addEventListener('click', onClick);
    root.addEventListener('change', onInput);
    root.addEventListener('input', (e) => { if (e.target.type === 'range' || e.target.id === 'ml-search' || e.target.id === 'ml-opt-search') onInput(e); });
    document.addEventListener('keydown', (e) => {
      if (e.key === '/' && root.offsetParent && !['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName)) {
        e.preventDefault(); $('#ml-search')?.focus();
      }
    });
    root.dataset.bound = '1';
  }
  root.innerHTML = '<div class="ml-empty">Reading the cluster…</div>';
  S.state = await api('/api/loader/state');
  paint();
  loadProfiles();
  if (!S.target) {
    const first = allTargets().find((t) => t.editable);
    if (first) selectTarget(first.id);
  } else selectTarget(S.target);
}

window.openModelLoader = openModelLoader;
