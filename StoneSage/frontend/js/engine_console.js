/**
 * StoneSage Engine Console Controller (v1.0)
 * Unified LLM Engine Control & Debug Console
 *
 * 4 Sub-Layers:
 *  1. Live Engine Dashboard - Polls /slots, /metrics, /props from all engines
 *  2. Parameter Editor & Hot-Reload - Edit sampling params, trigger service restarts
 *  3. Debug Token Stream - Real-time SSE token-by-token with timing & probabilities
 *  4. System Log Viewer - Live journalctl streaming from systemd services
 */

import { State, escapeHtml } from './state.js';

// ─── State ───────────────────────────────────────────────────────────────
let engineStates = {};
let pollTimer = null;
let activeDebugStream = null;
let activeLogStream = null;
let selectedEngine = 'coordinator';
let consoleSublayer = 'dashboard';

// ─── Dynamic / Roaming Nodes ─────────────────────────────────────────────
// Filled from the configured harness instances (config.json) that serve a model API: LM Studio (:1234),
// Ollama (:11434) or any instance marked "engine": true. Nothing is hardcoded here.
let dynamicNodes = [];
const ENGINE_PORTS = new Set([1234, 11434]);

async function loadDynamicNodes() {
  try {
    const res = await fetch('/api/harness/instances').then(r => r.json());
    const found = (res.instances || []).flatMap(inst => {
      try {
        const u = new URL(inst.url);
        const port = parseInt(u.port, 10);
        if (!inst.engine && !ENGINE_PORTS.has(port)) return [];
        return [{ key: inst.id, name: inst.name, host: u.hostname, port, type: 'auto', is_roaming: true }];
      } catch (_) { return []; }
    });
    const manual = dynamicNodes.filter(n => n.type === 'auto' && !found.some(f => f.key === n.key) && n.manual);
    dynamicNodes = [...found, ...manual];
  } catch (err) {
    console.warn('[EngineConsole] could not load instances:', err);
  }
}

// ─── Initialization ──────────────────────────────────────────────────────
export function initEngineConsole() {
  console.log('⚡ [EngineConsole] Initializing unified engine console...');

  // Sub-layer navigation
  document.querySelectorAll('.econsole-subnav-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const layer = btn.getAttribute('data-layer');
      if (layer) switchConsoleSublayer(layer);
    });
  });

  // Engine selector tabs
  document.querySelectorAll('.econsole-engine-tab').forEach(btn => {
    btn.addEventListener('click', () => {
      const key = btn.getAttribute('data-engine');
      if (key) selectEngine(key);
    });
  });

  // Debug stream controls
  const debugSendBtn = document.getElementById('econsole-debug-send');
  if (debugSendBtn) debugSendBtn.addEventListener('click', sendDebugQuery);

  const debugStopBtn = document.getElementById('econsole-debug-stop');
  if (debugStopBtn) debugStopBtn.addEventListener('click', stopDebugStream);

  // Log viewer controls
  const logStartBtn = document.getElementById('econsole-log-start');
  if (logStartBtn) logStartBtn.addEventListener('click', startLogViewer);

  const logStopBtn = document.getElementById('econsole-log-stop');
  if (logStopBtn) logStopBtn.addEventListener('click', stopLogViewer);

  // Sampling apply button
  const applyBtn = document.getElementById('econsole-apply-sampling');
  if (applyBtn) applyBtn.addEventListener('click', applySamplingChanges);

  // Reload service button
  const reloadBtn = document.getElementById('econsole-reload-service');
  if (reloadBtn) reloadBtn.addEventListener('click', reloadSelectedService);

  // Dynamic node add
  const addNodeBtn = document.getElementById('econsole-add-node');
  if (addNodeBtn) addNodeBtn.addEventListener('click', addDynamicNode);

  // Initial poll
  pollAllEngines();
  pollTimer = setInterval(pollAllEngines, 3000);

  console.log('⚡ [EngineConsole] Engine Console ready.');
}

export function destroyEngineConsole() {
  if (pollTimer) clearInterval(pollTimer);
  stopDebugStream();
  stopLogViewer();
}

// ─── Sub-Layer Switching ─────────────────────────────────────────────────
function switchConsoleSublayer(layerId) {
  consoleSublayer = layerId;
  document.querySelectorAll('.econsole-subnav-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.econsole-layer').forEach(l => l.style.display = 'none');

  const btn = document.querySelector(`.econsole-subnav-btn[data-layer="${layerId}"]`);
  const pane = document.getElementById(`econsole-layer-${layerId}`);
  if (btn) btn.classList.add('active');
  if (pane) pane.style.display = 'block';

  const tabs = document.getElementById('econsole-engine-tabs');
  if (tabs) tabs.style.display = ['loader', 'boost', 'trace', 'memories'].includes(layerId) ? 'none' : 'flex';  // own pickers / not per engine
  if (layerId === 'loader' && typeof window.openModelLoader === 'function') window.openModelLoader();
  if (layerId === 'boost' && typeof window.openBoost === 'function') window.openBoost();
  if (layerId === 'trace' && typeof window.openCourageTrace === 'function') window.openCourageTrace();
  if (layerId === 'memories' && typeof window.openCourageMemories === 'function') window.openCourageMemories();
  if (layerId === 'dashboard') pollAllEngines();
  if (layerId === 'params') renderParameterEditor();
  if (layerId === 'sizer' && typeof window.recalculateHardwareCapacity === 'function') {
    window.recalculateHardwareCapacity();
  }
}

function selectEngine(key) {
  selectedEngine = key;
  document.querySelectorAll('.econsole-engine-tab').forEach(b => b.classList.remove('active'));
  const btn = document.querySelector(`.econsole-engine-tab[data-engine="${key}"]`);
  if (btn) btn.classList.add('active');

  // Sync with playground target model selector
  const pgSelect = document.getElementById('playground-model-select');
  if (pgSelect) {
    for (let opt of pgSelect.options) {
      if (opt.value === key) { pgSelect.value = key; break; }
    }
  }

  // Sync with harness scanned model selector
  const hmSelect = document.getElementById('harness-model-select');
  if (hmSelect) {
    for (let opt of hmSelect.options) {
      if (opt.value === key) { hmSelect.value = key; break; }
    }
  }

  // Sync with log viewer service selector
  const logSelect = document.getElementById('econsole-log-service');
  if (logSelect) {
    const serviceMap = {
      'coordinator': 'llama-coordinator',
      'worker': 'llama-worker',
      'embedder': 'llama-embedder',
      'vision': 'vision-server'
    };
    if (serviceMap[key]) logSelect.value = serviceMap[key];
  }

  if (consoleSublayer === 'params') renderParameterEditor();
}

// ─── Dashboard Polling ───────────────────────────────────────────────────
async function pollAllEngines() {
  if (!dynamicNodes.loaded) { await loadDynamicNodes(); dynamicNodes.loaded = true; }
  // Fetch fixed engines
  const fixedPromise = fetch('/api/engine/state/all')
    .then(r => r.json())
    .then(data => { if (data.ok && data.engines) Object.assign(engineStates, data.engines); })
    .catch(err => console.warn('[EngineConsole] Fixed poll error:', err));

  // Fetch all dynamic nodes in parallel
  const dynamicPromises = dynamicNodes.map(node =>
    fetch(`/api/engine/state/dynamic?host=${node.host}&port=${node.port}&name=${node.key}`)
      .then(r => r.json())
      .then(data => { if (data.ok) engineStates[node.key] = data.engine; })
      .catch(() => {
        engineStates[node.key] = { key: node.key, online: false, host: node.host, port: node.port, role: node.name, device: 'Roaming', is_dynamic: true };
      })
  );

  // Wait for ALL to settle, then render once
  await Promise.all([fixedPromise, ...dynamicPromises]);
  renderDashboard();
}

// ─── Dashboard Rendering ─────────────────────────────────────────────────
function renderDashboard() {
  const container = document.getElementById('econsole-dashboard-grid');
  if (!container) return;

  const cards = Object.entries(engineStates).map(([key, eng]) => renderEngineCard(key, eng));
  container.innerHTML = cards.join('');

  // Update engine selector tabs
  const tabBar = document.getElementById('econsole-engine-tabs');
  if (tabBar) {
    tabBar.innerHTML = Object.entries(engineStates).map(([key, eng]) => {
      const online = eng.online;
      const dot = online ? '🟢' : '🔴';
      const active = key === selectedEngine ? 'active' : '';
      const label = eng.model?.alias || eng.role || key;
      return `<button class="econsole-engine-tab ${active}" data-engine="${key}">${dot} ${escapeHtml(label)}</button>`;
    }).join('');

    // Re-wire click handlers
    tabBar.querySelectorAll('.econsole-engine-tab').forEach(btn => {
      btn.addEventListener('click', () => selectEngine(btn.getAttribute('data-engine')));
    });
  }
}

function renderEngineCard(key, eng) {
  const online = eng.online;
  const statusClass = online ? 'engine-online' : 'engine-offline';
  const isLmStudio = eng.api === 'lm-studio';
  const statusText = !online ? '🔴 OFFLINE' : (eng.status_note ? '🟡 NO MODEL LOADED'
    : (eng.slots?.[0]?.is_processing ? '⚡ PROCESSING' : '🟢 IDLE'));

  const model = eng.model || {};
  const metrics = eng.metrics || {};
  const slot0 = eng.slots?.[0] || {};
  const params = slot0.params || {};

  // Throughput gauges
  const promptTps = parseFloat(metrics['llamacpp:prompt_tokens_seconds'] || 0).toFixed(1);
  const genTps = parseFloat(metrics['llamacpp:predicted_tokens_seconds'] || 0).toFixed(1);
  const totalGenerated = parseInt(metrics['llamacpp:tokens_predicted_total'] || 0);
  const totalPrompt = parseInt(metrics['llamacpp:prompt_tokens_total'] || 0);
  const totalCached = parseInt(metrics['llamacpp:prompt_tokens_cached_total'] || 0);
  const maxSeqLen = parseInt(metrics['llamacpp:n_tokens_max'] || 0);
  const specDrafted = parseInt(metrics['llamacpp:spec_decode_num_draft_tokens_total'] || 0);
  const specAccepted = parseInt(metrics['llamacpp:spec_decode_num_accepted_tokens_total'] || 0);
  const specRate = specDrafted > 0 ? ((specAccepted / specDrafted) * 100).toFixed(1) : '—';

  const isDynamic = eng.is_dynamic;
  const dynamicBadge = isDynamic ? '<span class="econsole-badge-roaming">⚡ ROAMING</span>' : '';

  return `
    <div class="econsole-card ${statusClass}">
      <div class="econsole-card-header">
        <span class="econsole-card-title">${escapeHtml(key.toUpperCase())} ${dynamicBadge}</span>
        <span class="econsole-card-status">${statusText}</span>
      </div>
      <div class="econsole-card-body">
        <div class="econsole-card-row"><span class="econsole-label">Model:</span> <span class="econsole-value">${escapeHtml(model.alias || (eng.available_models ? `none loaded (${eng.available_models.length} available)` : '—'))}${model.params ? ` · ${escapeHtml(model.params)}` : ''}</span></div>
        <div class="econsole-card-row"><span class="econsole-label">File:</span> <span class="econsole-value econsole-mono">${escapeHtml((model.path || '').split('/').pop() || '—')}</span></div>
        <div class="econsole-card-row"><span class="econsole-label">Quantization:</span> <span class="econsole-value">${escapeHtml(model.quantization || '—')}</span></div>
        <div class="econsole-card-row"><span class="econsole-label">Device:</span> <span class="econsole-value">${escapeHtml(eng.device || '—')}</span></div>
        <div class="econsole-card-row"><span class="econsole-label">Context:</span> <span class="econsole-value">${slot0.n_ctx || model.context || '—'}${model.max_context ? ` / ${model.max_context} max` : ''}</span></div>
        <div class="econsole-card-row"><span class="econsole-label">Slots:</span> <span class="econsole-value">${eng.slots?.length || '—'}</span></div>
        ${online && isLmStudio ? `
        <div class="econsole-card-divider"></div>
        <div class="econsole-card-row"><span class="econsole-label">API:</span> <span class="econsole-value">LM Studio REST v1 (${eng.latency_ms} ms)</span></div>` : ''}
        ${online && !isLmStudio ? `
        <div class="econsole-card-divider"></div>
        <div class="econsole-card-row"><span class="econsole-label">Prompt tok/s:</span> <span class="econsole-value econsole-gauge">${promptTps}</span></div>
        <div class="econsole-card-row"><span class="econsole-label">Gen tok/s:</span> <span class="econsole-value econsole-gauge">${genTps}</span></div>
        <div class="econsole-card-row"><span class="econsole-label">Total Generated:</span> <span class="econsole-value">${totalGenerated.toLocaleString()}</span></div>
        <div class="econsole-card-row"><span class="econsole-label">Total Prompt:</span> <span class="econsole-value">${totalPrompt.toLocaleString()}</span></div>
        <div class="econsole-card-row"><span class="econsole-label">Cached Hits:</span> <span class="econsole-value">${totalCached.toLocaleString()}</span></div>
        <div class="econsole-card-row"><span class="econsole-label">Max Seq Length:</span> <span class="econsole-value">${maxSeqLen.toLocaleString()}</span></div>
        ${specDrafted > 0 ? `
        <div class="econsole-card-divider"></div>
        <div class="econsole-card-row"><span class="econsole-label">Spec Drafted:</span> <span class="econsole-value">${specDrafted.toLocaleString()}</span></div>
        <div class="econsole-card-row"><span class="econsole-label">Spec Accepted:</span> <span class="econsole-value">${specAccepted.toLocaleString()}</span></div>
        <div class="econsole-card-row"><span class="econsole-label">Accept Rate:</span> <span class="econsole-value econsole-gauge">${specRate}%</span></div>
        ` : ''}
        <div class="econsole-card-divider"></div>
        <div class="econsole-card-row"><span class="econsole-label">Reasoning:</span> <span class="econsole-value">${escapeHtml(params.reasoning_format || '—')}</span></div>
        <div class="econsole-card-row"><span class="econsole-label">Chat Format:</span> <span class="econsole-value">${escapeHtml(params.chat_format || '—')}</span></div>
        <div class="econsole-card-row"><span class="econsole-label">Sampler Chain:</span> <span class="econsole-value econsole-mono">${escapeHtml((params.samplers || []).join(' → '))}</span></div>
        ` : ''}
        <div class="econsole-card-row"><span class="econsole-label">Latency:</span> <span class="econsole-value">${eng.latency_ms ? eng.latency_ms.toFixed(1) + ' ms' : '—'}</span></div>
      </div>
    </div>
  `;
}

// ─── Parameter Editor ────────────────────────────────────────────────────
function renderParameterEditor() {
  const container = document.getElementById('econsole-param-editor');
  if (!container) return;

  const eng = engineStates[selectedEngine];
  if (!eng || !eng.online) {
    container.innerHTML = '<div class="econsole-empty">Selected engine is offline. No parameters to display.</div>';
    return;
  }

  const slot0 = eng.slots?.[0] || {};
  const params = slot0.params || {};
  const props = eng.props || {};

  // Sampling parameters (per-request, no restart needed)
  const samplingParams = [
    { key: 'temperature', label: 'Temperature', min: 0, max: 5, step: 0.01, val: params.temperature },
    { key: 'min_p', label: 'Min-P', min: 0, max: 1, step: 0.01, val: params.min_p },
    { key: 'top_p', label: 'Top-P', min: 0, max: 1, step: 0.01, val: params.top_p },
    { key: 'top_k', label: 'Top-K', min: 0, max: 500, step: 1, val: params.top_k },
    { key: 'typical_p', label: 'Typical-P', min: 0, max: 1, step: 0.01, val: params.typical_p },
    { key: 'repeat_penalty', label: 'Repeat Penalty', min: 0, max: 3, step: 0.01, val: params.repeat_penalty },
    { key: 'repeat_last_n', label: 'Repeat Last N', min: 0, max: 4096, step: 1, val: params.repeat_last_n },
    { key: 'presence_penalty', label: 'Presence Penalty', min: -2, max: 2, step: 0.01, val: params.presence_penalty },
    { key: 'frequency_penalty', label: 'Frequency Penalty', min: -2, max: 2, step: 0.01, val: params.frequency_penalty },
    { key: 'dry_multiplier', label: 'DRY Multiplier', min: 0, max: 10, step: 0.01, val: params.dry_multiplier },
    { key: 'dry_base', label: 'DRY Base', min: 1, max: 5, step: 0.01, val: params.dry_base },
    { key: 'dry_allowed_length', label: 'DRY Allowed Length', min: 0, max: 100, step: 1, val: params.dry_allowed_length },
    { key: 'xtc_probability', label: 'XTC Probability', min: 0, max: 1, step: 0.01, val: params.xtc_probability },
    { key: 'xtc_threshold', label: 'XTC Threshold', min: 0, max: 1, step: 0.01, val: params.xtc_threshold },
    { key: 'dynatemp_range', label: 'DynaTemp Range', min: 0, max: 5, step: 0.01, val: params.dynatemp_range },
    { key: 'dynatemp_exponent', label: 'DynaTemp Exponent', min: 0, max: 5, step: 0.01, val: params.dynatemp_exponent },
    { key: 'n_predict', label: 'Max Tokens (n_predict)', min: -1, max: 32768, step: 1, val: params.n_predict },
  ];

  // Server-level parameters (require restart)
  const serverParams = [
    { key: 'n_ctx', label: 'Context Window', val: slot0.n_ctx, restart: true },
    { key: 'total_slots', label: 'Parallel Slots', val: props.total_slots, restart: true },
  ];

  const samplingHtml = samplingParams.map(p => `
    <div class="econsole-param-row">
      <label class="econsole-param-label">${p.label}</label>
      <input type="range" class="econsole-param-slider" data-key="${p.key}" min="${p.min}" max="${p.max}" step="${p.step}" value="${p.val ?? 0}">
      <input type="number" class="econsole-param-num" data-key="${p.key}" min="${p.min}" max="${p.max}" step="${p.step}" value="${p.val ?? 0}">
    </div>
  `).join('');

  const serverHtml = serverParams.map(p => `
    <div class="econsole-param-row">
      <label class="econsole-param-label">${p.label} <span class="econsole-badge-restart">⚠ RESTART</span></label>
      <input type="number" class="econsole-param-num econsole-param-restart" data-key="${p.key}" value="${p.val ?? 0}">
    </div>
  `).join('');

  const samplerChain = (params.samplers || []).join(', ');

  container.innerHTML = `
    <div class="econsole-param-section">
      <div class="econsole-param-section-title">Sampling Parameters (Applied Per-Request — Zero Downtime)</div>
      ${samplingHtml}
      <div class="econsole-param-row">
        <label class="econsole-param-label">Sampler Chain Order</label>
        <input type="text" class="econsole-param-text" data-key="samplers" value="${escapeHtml(samplerChain)}" style="width:100%;">
      </div>
      <div class="econsole-param-row">
        <label class="econsole-param-label">Reasoning Format</label>
        <select class="econsole-param-select" data-key="reasoning_format">
          <option value="none" ${params.reasoning_format === 'none' ? 'selected' : ''}>none</option>
          <option value="deepseek" ${params.reasoning_format === 'deepseek' ? 'selected' : ''}>deepseek</option>
        </select>
      </div>
      <button id="econsole-apply-sampling" class="econsole-btn econsole-btn-primary">✓ Apply Sampling</button>
    </div>
    <div class="econsole-param-section econsole-param-section-danger">
      <div class="econsole-param-section-title">Server-Level Parameters (Requires Service Restart)</div>
      ${serverHtml}
      <button id="econsole-reload-service" class="econsole-btn econsole-btn-danger">⟲ Restart Service</button>
    </div>
  `;

  // Sync sliders ↔ numeric inputs
  container.querySelectorAll('.econsole-param-slider').forEach(sl => {
    const num = container.querySelector(`.econsole-param-num[data-key="${sl.dataset.key}"]`);
    if (!num) return;
    sl.addEventListener('input', () => { num.value = sl.value; });
    num.addEventListener('input', () => { sl.value = num.value; });
  });

  // Re-wire buttons
  const applyBtn2 = container.querySelector('#econsole-apply-sampling');
  if (applyBtn2) applyBtn2.addEventListener('click', applySamplingChanges);
  const reloadBtn2 = container.querySelector('#econsole-reload-service');
  if (reloadBtn2) reloadBtn2.addEventListener('click', reloadSelectedService);

  // Sync with unrestricted controls in the DOM
  syncUnrestrictedSlidersFromParams(params, slot0, props);
}

function syncUnrestrictedSlidersFromParams(params, slot0, props) {
  const setVal = (id, val) => {
    if (val === undefined || val === null || isNaN(val)) return;
    const el = document.getElementById(id);
    if (el) el.value = val;
  };

  setVal('num-temp', params.temperature);
  setVal('sl-temp', params.temperature);
  setVal('num-minp', params.min_p);
  setVal('sl-minp', params.min_p);
  setVal('num-topp', params.top_p);
  setVal('sl-topp', params.top_p);
  setVal('num-topk', params.top_k);
  setVal('sl-topk', params.top_k);
  setVal('num-typp', params.typical_p);
  setVal('sl-typp', params.typical_p);
  setVal('num-pres', params.presence_penalty);
  setVal('sl-pres', params.presence_penalty);
  setVal('num-freq', params.frequency_penalty);
  setVal('sl-freq', params.frequency_penalty);
  setVal('num-rep', params.repeat_penalty);
  setVal('sl-rep', params.repeat_penalty);
  setVal('num-repeat-last-n', params.repeat_last_n);
  setVal('sl-repeat-last-n', params.repeat_last_n);
  setVal('num-dry-mult', params.dry_multiplier);
  setVal('sl-dry-mult', params.dry_multiplier);
  setVal('num-dry-base', params.dry_base);
  setVal('sl-dry-base', params.dry_base);
  setVal('num-dry-len', params.dry_allowed_length);
  setVal('sl-dry-len', params.dry_allowed_length);
  setVal('num-xtc-prob', params.xtc_probability);
  setVal('sl-xtc-prob', params.xtc_probability);
  setVal('num-xtc-thresh', params.xtc_threshold);
  setVal('sl-xtc-thresh', params.xtc_threshold);
  setVal('num-dynatemp-range', params.dynatemp_range);
  setVal('sl-dynatemp-range', params.dynatemp_range);
  setVal('num-dynatemp-exp', params.dynatemp_exponent);
  setVal('sl-dynatemp-exp', params.dynatemp_exponent);
  setVal('num-npredict', params.n_predict);
  setVal('sl-npredict', params.n_predict);

  if (slot0?.n_ctx) {
    setVal('num-ctx', slot0.n_ctx);
    setVal('sl-ctx', slot0.n_ctx);
    setVal('capacity-ctx-slider', slot0.n_ctx);
    setVal('capacity-ctx-input', slot0.n_ctx);
  }
  if (props?.total_slots) {
    setVal('num-slots', props.total_slots);
    setVal('capacity-slots-slider', props.total_slots);
  }
  if (params?.reasoning_format) {
    const rf = document.getElementById('harness-reasoning-format');
    if (rf) rf.value = params.reasoning_format;
  }
}

function applySamplingChanges() {
  const container = document.getElementById('econsole-param-editor');
  if (!container) return;

  const params = {};
  container.querySelectorAll('.econsole-param-num:not(.econsole-param-restart)').forEach(el => {
    const key = el.dataset.key;
    const val = parseFloat(el.value);
    if (key && !isNaN(val)) params[key] = val;
  });

  // Also read values from unrestricted inputs if present
  const numTemp = document.getElementById('num-temp');
  if (numTemp && !isNaN(parseFloat(numTemp.value))) params.temperature = parseFloat(numTemp.value);
  const numMinp = document.getElementById('num-minp');
  if (numMinp && !isNaN(parseFloat(numMinp.value))) params.min_p = parseFloat(numMinp.value);
  const numTopp = document.getElementById('num-topp');
  if (numTopp && !isNaN(parseFloat(numTopp.value))) params.top_p = parseFloat(numTopp.value);
  const numTopk = document.getElementById('num-topk');
  if (numTopk && !isNaN(parseFloat(numTopk.value))) params.top_k = parseInt(numTopk.value, 10);
  const numPres = document.getElementById('num-pres');
  if (numPres && !isNaN(parseFloat(numPres.value))) params.presence_penalty = parseFloat(numPres.value);
  const numFreq = document.getElementById('num-freq');
  if (numFreq && !isNaN(parseFloat(numFreq.value))) params.frequency_penalty = parseFloat(numFreq.value);
  const numRep = document.getElementById('num-rep');
  if (numRep && !isNaN(parseFloat(numRep.value))) params.repeat_penalty = parseFloat(numRep.value);
  const numRepLastN = document.getElementById('num-repeat-last-n');
  if (numRepLastN && !isNaN(parseInt(numRepLastN.value, 10))) params.repeat_last_n = parseInt(numRepLastN.value, 10);
  const numDryMult = document.getElementById('num-dry-mult');
  if (numDryMult && !isNaN(parseFloat(numDryMult.value))) params.dry_multiplier = parseFloat(numDryMult.value);
  const numDryBase = document.getElementById('num-dry-base');
  if (numDryBase && !isNaN(parseFloat(numDryBase.value))) params.dry_base = parseFloat(numDryBase.value);
  const numDryLen = document.getElementById('num-dry-len');
  if (numDryLen && !isNaN(parseInt(numDryLen.value, 10))) params.dry_allowed_length = parseInt(numDryLen.value, 10);
  const numXtcProb = document.getElementById('num-xtc-prob');
  if (numXtcProb && !isNaN(parseFloat(numXtcProb.value))) params.xtc_probability = parseFloat(numXtcProb.value);
  const numXtcThresh = document.getElementById('num-xtc-thresh');
  if (numXtcThresh && !isNaN(parseFloat(numXtcThresh.value))) params.xtc_threshold = parseFloat(numXtcThresh.value);
  const numDynaRange = document.getElementById('num-dynatemp-range');
  if (numDynaRange && !isNaN(parseFloat(numDynaRange.value))) params.dynatemp_range = parseFloat(numDynaRange.value);
  const numDynaExp = document.getElementById('num-dynatemp-exp');
  if (numDynaExp && !isNaN(parseFloat(numDynaExp.value))) params.dynatemp_exponent = parseFloat(numDynaExp.value);
  const numNPredict = document.getElementById('num-npredict');
  if (numNPredict && !isNaN(parseInt(numNPredict.value, 10))) params.n_predict = parseInt(numNPredict.value, 10);

  // Sampler chain
  const samplerInput = container.querySelector('.econsole-param-text[data-key="samplers"]');
  if (samplerInput) {
    params.samplers = samplerInput.value.split(',').map(s => s.trim()).filter(Boolean);
  }

  // Reasoning format
  const reasoningSelect = container.querySelector('.econsole-param-select[data-key="reasoning_format"]') || document.getElementById('harness-reasoning-format');
  if (reasoningSelect) params.reasoning_format = reasoningSelect.value;

  // Store in global state for cluster_client to use
  if (!State.engineOverrides) State.engineOverrides = {};
  State.engineOverrides[selectedEngine] = params;

  // Notify user
  const btn = container.querySelector('#econsole-apply-sampling');
  if (btn) {
    const orig = btn.textContent;
    btn.textContent = '✓ Applied!';
    btn.style.background = 'var(--term-success, #0a0)';
    setTimeout(() => { btn.textContent = orig; btn.style.background = ''; }, 2000);
  }

  console.log(`[EngineConsole] Applied sampling overrides for ${selectedEngine}:`, params);
}

async function reloadSelectedService() {
  const eng = engineStates[selectedEngine];
  if (!eng?.service) {
    alert('Cannot reload: no service name known for this engine.');
    return;
  }

  const btn = document.getElementById('econsole-reload-service');
  if (btn) {
    btn.disabled = true;
    btn.textContent = '⟲ Restarting...';
  }

  try {
    const res = await fetch('/api/engine/reload', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ service: eng.service })
    });
    const data = await res.json();
    if (data.ok) {
      if (btn) { btn.textContent = '✓ Restarted!'; btn.style.background = 'var(--term-success, #0a0)'; }
      setTimeout(() => pollAllEngines(), 1000);
    } else {
      if (btn) btn.textContent = '✗ Failed: ' + (data.error || 'unknown');
    }
  } catch (err) {
    if (btn) btn.textContent = '✗ Error: ' + err.message;
  }

  setTimeout(() => {
    if (btn) { btn.disabled = false; btn.textContent = '⟲ Restart Service'; btn.style.background = ''; }
  }, 5000);
}

// ─── Debug Token Stream ──────────────────────────────────────────────────
async function sendDebugQuery() {
  const promptEl = document.getElementById('econsole-debug-prompt');
  const terminal = document.getElementById('econsole-debug-terminal');
  if (!promptEl || !terminal) return;

  const prompt = promptEl.value.trim();
  if (!prompt) return;

  const eng = engineStates[selectedEngine];
  if (!eng?.online) {
    terminal.innerHTML = '<div class="econsole-debug-error">Engine offline. Cannot send debug query.</div>';
    return;
  }

  terminal.innerHTML = '<div class="econsole-debug-info">Connecting to engine...</div>';
  stopDebugStream();

  try {
    const port = eng.port;
    const host = eng.host;
    const res = await fetch('/api/engine/debug-stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        host, port, prompt,
        params: State.engineOverrides?.[selectedEngine] || {}
      })
    });

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let tokenCount = 0;
    let totalMs = 0;

    terminal.innerHTML = '';
    activeDebugStream = { reader };

    const processChunk = async () => {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          const payload = line.slice(6).trim();
          if (payload === '[DONE]') {
            terminal.innerHTML += `\n<div class="econsole-debug-summary">── DONE ── ${tokenCount} tokens | ${totalMs.toFixed(0)} ms | ${tokenCount > 0 ? (tokenCount / (totalMs / 1000)).toFixed(1) : 0} tok/s ──</div>`;
            return;
          }

          try {
            const chunk = JSON.parse(payload);
            const content = chunk.content || '';
            const timings = chunk.timings || {};
            const probs = chunk.completion_probabilities || chunk.probs || [];
            const tokenMs = timings.predicted_per_token_ms || 0;

            tokenCount++;
            totalMs += tokenMs;

            // Determine confidence level for coloring
            let confClass = 'econsole-token-high';
            if (probs.length > 0) {
              const topProb = probs[0]?.prob || probs[0]?.probability || 1.0;
              if (topProb < 0.1) confClass = 'econsole-token-low';
              else if (topProb < 0.3) confClass = 'econsole-token-medium';
            }

            // Render token with metadata
            let tokenHtml = `<span class="${confClass}" title="`;
            if (probs.length > 0) {
              const probsList = probs.slice(0, 5).map(p => `${escapeHtml(p.tok_str || p.token || '?')}: ${((p.prob || p.probability || 0) * 100).toFixed(1)}%`).join(' | ');
              tokenHtml += escapeHtml(probsList);
            }
            tokenHtml += `\n${tokenMs.toFixed(1)}ms">${escapeHtml(content)}</span>`;

            terminal.innerHTML += tokenHtml;

            // Auto-scroll
            terminal.scrollTop = terminal.scrollHeight;
          } catch (parseErr) {
            // Skip unparseable chunks
          }
        }
      }
    };

    processChunk().catch(err => {
      terminal.innerHTML += `\n<div class="econsole-debug-error">Stream ended: ${err.message}</div>`;
    });

  } catch (err) {
    terminal.innerHTML = `<div class="econsole-debug-error">Failed to connect: ${err.message}</div>`;
  }
}

function stopDebugStream() {
  if (activeDebugStream?.reader) {
    try { activeDebugStream.reader.cancel(); } catch (e) {}
    activeDebugStream = null;
  }
}

// ─── System Log Viewer ───────────────────────────────────────────────────
async function startLogViewer() {
  const terminal = document.getElementById('econsole-log-terminal');
  const serviceSelect = document.getElementById('econsole-log-service');
  if (!terminal || !serviceSelect) return;

  const service = serviceSelect.value;
  if (!service) return;

  stopLogViewer();
  terminal.innerHTML = `<div class="econsole-debug-info">Fetching logs for ${escapeHtml(service)}...</div>`;

  try {
    const res = await fetch(`/api/engine/logs?service=${encodeURIComponent(service)}&lines=150`);
    const data = await res.json();
    if (data.ok && data.logs) {
      terminal.innerHTML = data.logs.map(line => `<div class="econsole-log-line">${escapeHtml(line)}</div>`).join('');
      terminal.scrollTop = terminal.scrollHeight;
    } else {
      terminal.innerHTML = `<div class="econsole-debug-error">Error: ${escapeHtml(data.error || 'Unknown error')}</div>`;
    }
  } catch (err) {
    terminal.innerHTML = `<div class="econsole-debug-error">Failed: ${err.message}</div>`;
  }
}

function stopLogViewer() {
  if (activeLogStream) {
    try { activeLogStream.abort(); } catch (e) {}
    activeLogStream = null;
  }
}

// ─── Dynamic Node Management ─────────────────────────────────────────────
function addDynamicNode() {
  const hostEl = document.getElementById('econsole-dyn-host');
  const portEl = document.getElementById('econsole-dyn-port');
  const nameEl = document.getElementById('econsole-dyn-name');
  if (!hostEl || !portEl || !nameEl) return;

  const host = hostEl.value.trim();
  const port = parseInt(portEl.value) || 1234;
  const name = nameEl.value.trim() || `dynamic_${host}`;

  if (!host) return;

  const key = name.replace(/[^a-zA-Z0-9_-]/g, '_').toLowerCase();
  dynamicNodes.push({ key, name, host, port, type: 'auto', is_roaming: true, manual: true });

  hostEl.value = '';
  portEl.value = '1234';
  nameEl.value = '';

  pollAllEngines();
  console.log(`[EngineConsole] Added dynamic node: ${key} at ${host}:${port}`);
}

// ─── Exports for global access ───────────────────────────────────────────
window.initEngineConsole = initEngineConsole;
window.pollAllEngines = pollAllEngines;
