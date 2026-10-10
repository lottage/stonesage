/**
 * StoneSage Engine Studio Controller
 * Consolidated 5-Layer Master Engine (Windows Media Player ergonomics):
 * Layer 1: Hardware & Dynamic Fleet Matrix
 * Layer 2: Profiles & Unrestricted Sampling (0-max sliders + numeric inputs, VRAM/RAM Sizer)
 * Layer 3: Agent DNA & Roles
 * Layer 4: Memory HUD & Embedder RAG
 * Layer 5: Autonomous Loop & Trainer
 */

import { State } from './state.js';

let activeLayer = 'fleet';
let isAdvancedMode = false;
let customProfiles = {};

export function initEngineStudio() {
  console.log('⚡ [EngineStudio] Initializing master unified studio...');

  // Sub-nav layer switching
  document.querySelectorAll('.engine-subnav-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const layer = btn.getAttribute('data-layer');
      if (layer) switchEngineLayer(layer);
    });
  });

  // Simple vs Advanced Mode Toggle
  const modeToggle = document.getElementById('engine-mode-toggle-btn');
  if (modeToggle) {
    modeToggle.addEventListener('click', toggleAdvancedMode);
  }

  // Setup Unrestricted Sliders & Sync with Numeric Inputs
  setupUnrestrictedSliders();

  // Load Saved Profiles
  loadProfiles();

  // Wire Sizer Calculation
  setupCapacitySizer();

  // Wire Speculative Toggle
  setupSpeculativeControls();

  console.log('⚡ [EngineStudio] Engine Studio initialized.');
}

export function switchEngineLayer(layerId) {
  activeLayer = layerId;
  document.querySelectorAll('.engine-subnav-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.engine-layer').forEach(l => l.style.display = 'none');

  const activeBtn = document.querySelector(`.engine-subnav-btn[data-layer="${layerId}"]`);
  const activePane = document.getElementById(`engine-layer-${layerId}`);

  if (activeBtn) activeBtn.classList.add('active');
  if (activePane) activePane.style.display = 'block';

  // Trigger layer-specific refreshes
  if (layerId === 'fleet' && typeof window.loadFleetInstances === 'function') {
    window.loadFleetInstances(false);
  } else if (layerId === 'memory' && typeof window.loadAmemCards === 'function') {
    window.loadAmemCards();
  } else if (layerId === 'agentdna' && typeof window.fetchAgentDnaList === 'function') {
    window.fetchAgentDnaList();
  } else if (layerId === 'gallery3d') {
    if (typeof window.switchTab === 'function') {
      window.switchTab('workstation');
    }
    if (typeof window.setWorkstationViewMode === 'function') {
      window.setWorkstationViewMode('3d');
    }
  } else if (layerId === 'console' && typeof window.pollAllEngines === 'function') {
    window.pollAllEngines();
  }
}

function toggleAdvancedMode() {
  isAdvancedMode = !isAdvancedMode;
  const toggleBtn = document.getElementById('engine-mode-toggle-btn');
  const advSections = document.querySelectorAll('.engine-advanced-only');

  if (toggleBtn) {
    toggleBtn.textContent = isAdvancedMode ? '[MODE: ADVANCED ⚡]' : '[MODE: SIMPLE 🟢]';
    toggleBtn.style.color = isAdvancedMode ? 'var(--term-accent-gold)' : 'var(--term-success)';
  }

  advSections.forEach(el => {
    el.style.display = isAdvancedMode ? 'block' : 'none';
  });
}

/**
 * Setup Unrestricted 0-Max Sliders with Synchronized Numeric Inputs
 */
function setupUnrestrictedSliders() {
  const bindings = [
    { slider: 'sl-temp', input: 'num-temp', def: 0.70 },
    { slider: 'sl-minp', input: 'num-minp', def: 0.06 },
    { slider: 'sl-topp', input: 'num-topp', def: 1.0 },
    { slider: 'sl-topk', input: 'num-topk', def: 40 },
    { slider: 'sl-typp', input: 'num-typp', def: 1.0 },
    { slider: 'sl-tfsz', input: 'num-tfsz', def: 1.0 },
    { slider: 'sl-pres', input: 'num-pres', def: 0.20 },
    { slider: 'sl-freq', input: 'num-freq', def: 0.0 },
    { slider: 'sl-rep', input: 'num-rep', def: 1.05 },
    { slider: 'sl-repeat-last-n', input: 'num-repeat-last-n', def: 64 },
    { slider: 'sl-ctx', input: 'num-ctx', def: 16384 },
    { slider: 'sl-npredict', input: 'num-npredict', def: 2048 },
    { slider: 'sl-dry-mult', input: 'num-dry-mult', def: 0.0 },
    { slider: 'sl-dry-base', input: 'num-dry-base', def: 1.75 },
    { slider: 'sl-dry-len', input: 'num-dry-len', def: 2 },
    { slider: 'sl-dry-pen', input: 'num-dry-pen', def: -1 },
    { slider: 'sl-xtc-prob', input: 'num-xtc-prob', def: 0.0 },
    { slider: 'sl-xtc-thresh', input: 'num-xtc-thresh', def: 0.10 },
    { slider: 'sl-dynatemp-range', input: 'num-dynatemp-range', def: 0.0 },
    { slider: 'sl-dynatemp-exp', input: 'num-dynatemp-exp', def: 1.0 },
    { slider: 'sl-mirostat-tau', input: 'num-mirostat-tau', def: 5.0 },
    { slider: 'sl-mirostat-eta', input: 'num-mirostat-eta', def: 0.10 },
    { slider: 'sl-nbatch', input: 'num-nbatch', def: 512 },
    { slider: 'sl-nubatch', input: 'num-nubatch', def: 512 },
    { slider: 'sl-ngl', input: 'num-ngl', def: 99 },
    { slider: 'sl-rope-base', input: 'num-rope-base', def: 1000000 },
    { slider: 'sl-rope-scale', input: 'num-rope-scale', def: 1.0 },
    { slider: 'sl-defrag', input: 'num-defrag', def: 0.10 },
    { slider: 'sl-draft-tokens', input: 'num-draft-tokens', def: 5 },
    { slider: 'sl-lookup-cache', input: 'num-lookup-cache', def: 1024 },
    { slider: 'sl-threads', input: 'num-threads', def: 8 },
    { slider: 'sl-threads-batch', input: 'num-threads-batch', def: 8 },
    { slider: 'sl-poll', input: 'num-poll', def: 0 },
    { slider: 'sl-nc-moe', input: 'num-nc-moe', def: 0 },
    { slider: 'sl-nc-ffn', input: 'num-nc-ffn', def: 0 },
    { slider: 'sl-fit-target', input: 'num-fit-target', def: 512 },
    { slider: 'sl-cache-ram', input: 'num-cache-ram', def: 0 },
    { slider: 'sl-cache-reuse', input: 'num-cache-reuse', def: 0 },
    { slider: 'sl-ctx-cp', input: 'num-ctx-cp', def: 0 },
    { slider: 'sl-cms', input: 'num-cms', def: 0 },
    { slider: 'sl-reasoning-effort', input: 'num-reasoning-effort', def: 0.50 },
    { slider: 'sl-reasoning-budget', input: 'num-reasoning-budget', def: 0 }
  ];

  bindings.forEach(b => {
    const sl = document.getElementById(b.slider);
    const num = document.getElementById(b.input);
    if (!sl || !num) return;

    sl.value = b.def;
    num.value = b.def;

    sl.addEventListener('input', () => {
      num.value = sl.value;
      recalculateHardwareCapacity();
    });

    num.addEventListener('input', () => {
      sl.value = num.value;
      recalculateHardwareCapacity();
    });
  });
}

/**
 * Mathematical VRAM & RAM Capacity Sizer
 */
function setupCapacitySizer() {
  const triggerIds = [
    'capacity-model-params', 'capacity-params-select', 'capacity-custom-params',
    'capacity-weight-quant', 'capacity-k-quant', 'capacity-v-quant', 'capacity-kv-quant',
    'num-ctx', 'sl-ctx',
    'capacity-slots-slider', 'num-slots',
    'num-ngl', 'sl-ngl', 'capacity-vram-select', 'capacity-custom-vram',
    'harness-mirostat', 'harness-split-mode', 'harness-model-select', 'harness-node-select'
  ];

  triggerIds.forEach(id => {
    const el = document.getElementById(id);
    if (el) {
      el.addEventListener('input', recalculateHardwareCapacity);
      el.addEventListener('change', recalculateHardwareCapacity);
    }
  });

  recalculateHardwareCapacity();
}

export function recalculateHardwareCapacity() {
  const modelParamsEl = document.getElementById('capacity-model-params');
  const paramsEl = document.getElementById('capacity-params-select');
  const customParamsEl = document.getElementById('capacity-custom-params');
  const weightQuantEl = document.getElementById('capacity-weight-quant');
  const kQuantEl = document.getElementById('capacity-k-quant') || document.getElementById('capacity-kv-quant');
  const vQuantEl = document.getElementById('capacity-v-quant') || document.getElementById('capacity-kv-quant');
  const ctxEl = document.getElementById('num-ctx') || document.getElementById('sl-ctx');
  const slotsEl = document.getElementById('num-slots') || document.getElementById('capacity-slots-slider');
  const vramLimitEl = document.getElementById('capacity-vram-select');
  const customVramEl = document.getElementById('capacity-custom-vram');
  const nglEl = document.getElementById('num-ngl') || document.getElementById('sl-ngl');

  if (!ctxEl) return;

  let paramsB = 9.0;
  if (modelParamsEl && !isNaN(parseFloat(modelParamsEl.value)) && parseFloat(modelParamsEl.value) > 0) {
    paramsB = parseFloat(modelParamsEl.value);
  } else if (customParamsEl && !isNaN(parseFloat(customParamsEl.value)) && parseFloat(customParamsEl.value) > 0) {
    paramsB = parseFloat(customParamsEl.value);
  } else if (paramsEl && !isNaN(parseFloat(paramsEl.value))) {
    paramsB = parseFloat(paramsEl.value);
  }

  const contextTokens = parseInt(ctxEl.value) || 16384;
  const slots = parseInt(slotsEl?.value) || 1;

  const customV = parseFloat(customVramEl?.value);
  const vramLimit = (!isNaN(customV) && customV > 0) ? customV : (vramLimitEl ? parseFloat(vramLimitEl.value) || 12.0 : 12.0);

  // Complete GGUF Bits per weight for theoretical fallback
  const bpwMap = {
    'iq1_s': 1.56, 'iq1_m': 1.75,
    'iq2_xxs': 2.06, 'iq2_xs': 2.31, 'iq2_s': 2.50, 'iq2_m': 2.70,
    'iq3_xxs': 3.06, 'iq3_xs': 3.30, 'iq3_s': 3.44, 'iq3_m': 3.66,
    'q2_k': 2.62, 'q3_k_s': 3.41, 'q3_k_m': 3.91, 'q3_k_l': 4.27,
    'iq4_nl': 4.50, 'iq4_xs': 4.25, 'q4_0': 4.55, 'q4_1': 5.00,
    'q4_k_s': 4.58, 'q4_k_m': 4.85,
    'q5_0': 5.54, 'q5_1': 6.00, 'q5_k_s': 5.54, 'q5_k_m': 5.69,
    'q6_k': 6.56, 'q8_0': 8.50, 'f16': 16.0, 'f32': 32.0
  };
  const weightBpw = weightQuantEl ? (bpwMap[weightQuantEl.value] || 4.85) : 4.85;

  // Complete KV token precisions (Default: F16 = 16.0 bpw = No Quantization)
  const kvBpwMap = {
    'f16': 16.0, 'none': 16.0, 'q8_0': 8.0, 'q5_1': 5.5, 'q5_0': 5.0,
    'q4_1': 4.5, 'iq4_nl': 4.5, 'q4_0': 4.0, 'f32': 32.0
  };
  const kBpw = kvBpwMap[kQuantEl?.value] || 16.0;
  const vBpw = kvBpwMap[vQuantEl?.value] || 16.0;
  const avgKvBpw = (kBpw + vBpw) / 2.0;

  // 1. GROUND TRUTH Model Weights VRAM:
  // If a real model file is selected, use its actual disk size (e.g. 6.71 GB for Bonsai 27B)
  let actualModelGB = null;
  if (window.selectedModelInfo && window.selectedModelInfo.size_gb) {
    actualModelGB = parseFloat(window.selectedModelInfo.size_gb);
  } else {
    const modelSelect = document.getElementById('harness-model-select');
    if (modelSelect && window.currentModels) {
      const m = window.currentModels.find(x => x.key === modelSelect.value || x.name === modelSelect.value);
      if (m && m.size_gb) actualModelGB = parseFloat(m.size_gb);
    }
  }

  let totalModelLayers = paramsB > 40 ? 80 : (paramsB > 20 ? 64 : (paramsB > 10 ? 48 : 28));
  if (window.selectedModelInfo && window.selectedModelInfo.layers) {
    totalModelLayers = parseInt(window.selectedModelInfo.layers, 10);
  }
  const ngl = parseInt(nglEl?.value || '99', 10);
  const offloadRatio = Math.min(1.0, Math.max(0.0, ngl >= 99 ? 1.0 : (ngl / totalModelLayers)));

  let weightGB = 0;
  let isActualFile = false;
  if (actualModelGB && !isNaN(actualModelGB) && actualModelGB > 0) {
    weightGB = actualModelGB * offloadRatio;
    isActualFile = true;
  } else {
    const weightBytes = (paramsB * 1e9) * (weightBpw / 8.0) * 1.15;
    weightGB = (weightBytes / 1e9) * offloadRatio;
  }

  // 2. GQA-AWARE KV Cache VRAM:
  // Modern models (Qwen, Llama 3, Gemma, Bonsai) use Grouped Query Attention:
  // kv_heads is 4 (for <=3.5B), 8 (for 7B-32B), 16 (for 70B+), with head_dim = 128
  const kvHeads = paramsB <= 3.5 ? 4 : (paramsB >= 60 ? 16 : 8);
  const headDim = 128;
  const kvDim = kvHeads * headDim;
  const kvBytesPerToken = 2 * totalModelLayers * kvDim * (avgKvBpw / 8.0);
  const kvBytes = kvBytesPerToken * contextTokens * slots;
  const kvGB = kvBytes / 1e9;

  // 3. Activations & Compute Buffers
  const actGB = Math.max(0.35, (contextTokens / 8192) * 0.30);

  const totalGB = weightGB + kvGB + actGB;
  const headroomGB = vramLimit - totalGB;

  // Update DOM readouts
  const wEl = document.getElementById('cap-weights-gb');
  const kEl = document.getElementById('cap-kv-gb');
  const aEl = document.getElementById('cap-act-gb');
  const tEl = document.getElementById('cap-total-gb');
  const hEl = document.getElementById('cap-headroom-gb');
  const vBadge = document.getElementById('cap-verdict-badge');
  const safeBadge = document.getElementById('capacity-safe-badge');

  if (wEl) {
    wEl.textContent = `${weightGB.toFixed(2)} GB${isActualFile ? ' [DISK]' : ''}`;
    wEl.title = isActualFile ? 'Ground-truth byte size from real GGUF file on node' : 'Estimated theoretical size from param formula';
  }
  if (kEl) {
    const isUnquant = (kBpw === 16.0 && vBpw === 16.0);
    kEl.textContent = `${kvGB.toFixed(2)} GB (${isUnquant ? 'F16' : `K:${kQuantEl?.value}/V:${vQuantEl?.value}`})`;
  }
  if (aEl) aEl.textContent = `${actGB.toFixed(2)} GB`;
  if (tEl) tEl.textContent = `${totalGB.toFixed(2)} GB`;

  if (hEl) {
    hEl.textContent = `${headroomGB >= 0 ? '+' : ''}${headroomGB.toFixed(2)} GB`;
    hEl.style.color = headroomGB >= 0 ? 'var(--term-success)' : 'var(--term-alert)';
  }

  if (vBadge) {
    if (headroomGB >= 0) {
      vBadge.textContent = '[FITS IN VRAM (SAFE - FULL F16 PRECISION)]';
      vBadge.style.background = '#166534';
    } else {
      vBadge.textContent = `[OVERFLOW: ${Math.abs(headroomGB).toFixed(2)} GB TO RAM]`;
      vBadge.style.background = '#991b1b';
    }
  }

  if (safeBadge) {
    if (headroomGB >= 0) {
      safeBadge.textContent = 'FITS IN VRAM (SAFE)';
      safeBadge.className = 'status-badge online';
    } else {
      safeBadge.textContent = 'SPILLS TO SYSTEM RAM';
      safeBadge.className = 'status-badge offline';
    }
  }
}

/**
 * Profile Management: Save, Load, Delete Custom User Profiles
 */
export async function loadProfiles() {
  try {
    const res = await fetch('/api/profiles/list');
    const data = await res.json();
    if (data.ok && data.profiles) {
      customProfiles = data.profiles;
    }
  } catch (e) {
    // Fallback to local storage
    try {
      const local = localStorage.getItem('stonesage_user_profiles');
      if (local) customProfiles = JSON.parse(local);
    } catch (_) {}
  }

  renderProfileDropdown();
}

function renderProfileDropdown() {
  const select = document.getElementById('engine-profile-select');
  if (!select) return;

  select.innerHTML = '<option value="">-- Select Saved Profile --</option>';
  Object.keys(customProfiles).forEach(id => {
    const p = customProfiles[id];
    const opt = document.createElement('option');
    opt.value = id;
    opt.textContent = `${p.name || id} (${p.model_params || '9'}B • ${p.ctx || '16k'})`;
    select.appendChild(opt);
  });
}

export async function saveCurrentProfile() {
  const name = prompt('Enter a name for this model profile (e.g. "Coding 64k Precision", "Voice Fast 3B"):', '');
  if (!name) return;

  const id = name.toLowerCase().replace(/[^a-z0-9]+/g, '_');
  const profileData = {
    id: id,
    name: name,
    temp: parseFloat(document.getElementById('num-temp')?.value) || 0.7,
    min_p: parseFloat(document.getElementById('num-minp')?.value) || 0.06,
    top_p: parseFloat(document.getElementById('num-topp')?.value) || 1.0,
    top_k: parseInt(document.getElementById('num-topk')?.value) || 40,
    typp: parseFloat(document.getElementById('num-typp')?.value) || 1.0,
    tfsz: parseFloat(document.getElementById('num-tfsz')?.value) || 1.0,
    pres_pen: parseFloat(document.getElementById('num-pres')?.value) || 0.2,
    freq_pen: parseFloat(document.getElementById('num-freq')?.value) || 0.0,
    rep_pen: parseFloat(document.getElementById('num-rep')?.value) || 1.05,
    repeat_last_n: parseInt(document.getElementById('num-repeat-last-n')?.value) || 64,
    ctx: parseInt(document.getElementById('num-ctx')?.value) || 16384,
    npredict: parseInt(document.getElementById('num-npredict')?.value) || 2048,
    dry_mult: parseFloat(document.getElementById('num-dry-mult')?.value) || 0.0,
    dry_base: parseFloat(document.getElementById('num-dry-base')?.value) || 1.75,
    dry_len: parseInt(document.getElementById('num-dry-len')?.value) || 2,
    dry_pen: parseInt(document.getElementById('num-dry-pen')?.value) || -1,
    xtc_prob: parseFloat(document.getElementById('num-xtc-prob')?.value) || 0.0,
    xtc_thresh: parseFloat(document.getElementById('num-xtc-thresh')?.value) || 0.10,
    dynatemp_range: parseFloat(document.getElementById('num-dynatemp-range')?.value) || 0.0,
    dynatemp_exp: parseFloat(document.getElementById('num-dynatemp-exp')?.value) || 1.0,
    mirostat: document.getElementById('harness-mirostat')?.value || '0',
    mirostat_tau: parseFloat(document.getElementById('num-mirostat-tau')?.value) || 5.0,
    mirostat_eta: parseFloat(document.getElementById('num-mirostat-eta')?.value) || 0.10,
    nbatch: parseInt(document.getElementById('num-nbatch')?.value) || 512,
    nubatch: parseInt(document.getElementById('num-nubatch')?.value) || 512,
    ngl: parseInt(document.getElementById('num-ngl')?.value) || 99,
    rope_base: parseInt(document.getElementById('num-rope-base')?.value) || 1000000,
    rope_scale: parseFloat(document.getElementById('num-rope-scale')?.value) || 1.0,
    split_mode: document.getElementById('harness-split-mode')?.value || 'none',
    defrag: parseFloat(document.getElementById('num-defrag')?.value) || 0.10,
    kv_quant: document.getElementById('capacity-k-quant')?.value || document.getElementById('capacity-kv-quant')?.value || 'f16',
    k_quant: document.getElementById('capacity-k-quant')?.value || 'f16',
    v_quant: document.getElementById('capacity-v-quant')?.value || 'f16',
    mlock: document.getElementById('harness-mlock')?.checked || false,
    mmap: document.getElementById('harness-mmap')?.checked !== false,
    gpu_kv: document.getElementById('harness-gpu-kv')?.checked !== false,
    seed: document.getElementById('num-seed')?.value || '-1',
    custom_flags: document.getElementById('harness-custom-flags')?.value || '',
    weight_quant: document.getElementById('capacity-weight-quant')?.value || 'q4_k_m',
    model_params: document.getElementById('capacity-model-params')?.value || document.getElementById('capacity-custom-params')?.value || document.getElementById('capacity-params-select')?.value || '9.0',
    slots: parseInt(document.getElementById('num-slots')?.value) || 1,
    flash_attn: document.getElementById('harness-flash-attn')?.value || 'on',
    draft_tokens: parseInt(document.getElementById('num-draft-tokens')?.value) || 5,
    lookup_cache: parseInt(document.getElementById('num-lookup-cache')?.value) || 1024,
    threads: parseInt(document.getElementById('num-threads')?.value) || 8,
    threads_batch: parseInt(document.getElementById('num-threads-batch')?.value) || 8,
    cpu_mask: document.getElementById('harness-cpu-mask')?.value || '',
    cpu_strict: document.getElementById('harness-cpu-strict')?.value || 'auto',
    prio: document.getElementById('harness-prio')?.value || 'auto',
    poll: parseInt(document.getElementById('num-poll')?.value) || 0,
    numa: document.getElementById('harness-numa')?.value || 'none',
    device: document.getElementById('harness-device')?.value || '',
    tensor_split: document.getElementById('harness-tensor-split')?.value || '',
    main_gpu: parseInt(document.getElementById('num-main-gpu')?.value) || 0,
    cpu_moe: document.getElementById('harness-cpu-moe')?.checked || false,
    nc_moe: parseInt(document.getElementById('num-nc-moe')?.value) || 0,
    nc_ffn: parseInt(document.getElementById('num-nc-ffn')?.value) || 0,
    fit: document.getElementById('harness-fit')?.value || 'off',
    fit_target: parseInt(document.getElementById('num-fit-target')?.value) || 512,
    load_mode: document.getElementById('harness-load-mode')?.value || 'auto',
    lazy_mode: document.getElementById('harness-lazy-mode')?.checked || false,
    kv_unified: document.getElementById('harness-kv-unified')?.checked || false,
    kv_unified_slot: document.getElementById('harness-kv-unified-slot')?.checked || false,
    cache_ram: parseInt(document.getElementById('num-cache-ram')?.value) || 0,
    cache_idle: document.getElementById('harness-cache-idle')?.value || 'auto',
    cache_reuse: parseInt(document.getElementById('num-cache-reuse')?.value) || 0,
    context_shift: document.getElementById('harness-context-shift')?.checked !== false,
    no_kv_offload: document.getElementById('harness-no-kv-offload')?.checked || false,
    ctx_cp: parseInt(document.getElementById('num-ctx-cp')?.value) || 0,
    cms: parseInt(document.getElementById('num-cms')?.value) || 0,
    cont_batching: document.getElementById('harness-cont-batching')?.checked || false,
    rope_scaling: document.getElementById('harness-rope-scaling')?.value || 'none',
    reasoning: document.getElementById('harness-reasoning')?.value || 'auto',
    reasoning_format: document.getElementById('harness-reasoning-format')?.value || 'none',
    reasoning_effort: parseFloat(document.getElementById('num-reasoning-effort')?.value) || 0.5,
    reasoning_budget: parseInt(document.getElementById('num-reasoning-budget')?.value) || 0,
    reasoning_preserve: document.getElementById('harness-reasoning-preserve')?.checked || false
  };

  customProfiles[id] = profileData;
  localStorage.setItem('stonesage_user_profiles', JSON.stringify(customProfiles));

  try {
    await fetch('/api/profiles/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(profileData)
    });
  } catch (e) {
    console.warn('Profile save remote sync skipped:', e);
  }

  renderProfileDropdown();
  const select = document.getElementById('engine-profile-select');
  if (select) select.value = id;
  alert(`✓ Profile '${name}' saved successfully!`);
}

export function loadSelectedProfile(id) {
  if (!id) id = document.getElementById('engine-profile-select')?.value;
  if (!id || !customProfiles[id]) return;

  const p = customProfiles[id];
  const setVal = (elId, val) => {
    const el = document.getElementById(elId);
    if (el && val !== undefined) el.value = val;
  };
  const setCheck = (elId, val) => {
    const el = document.getElementById(elId);
    if (el && val !== undefined) el.checked = !!val;
  };

  setVal('num-temp', p.temp); setVal('sl-temp', p.temp);
  setVal('num-minp', p.min_p); setVal('sl-minp', p.min_p);
  setVal('num-topp', p.top_p); setVal('sl-topp', p.top_p);
  setVal('num-topk', p.top_k); setVal('sl-topk', p.top_k);
  setVal('num-typp', p.typp); setVal('sl-typp', p.typp);
  setVal('num-tfsz', p.tfsz); setVal('sl-tfsz', p.tfsz);
  setVal('num-pres', p.pres_pen); setVal('sl-pres', p.pres_pen);
  setVal('num-freq', p.freq_pen); setVal('sl-freq', p.freq_pen);
  setVal('num-rep', p.rep_pen); setVal('sl-rep', p.rep_pen);
  setVal('num-repeat-last-n', p.repeat_last_n); setVal('sl-repeat-last-n', p.repeat_last_n);
  setVal('num-ctx', p.ctx); setVal('sl-ctx', p.ctx);
  setVal('num-npredict', p.npredict); setVal('sl-npredict', p.npredict);
  setVal('num-dry-mult', p.dry_mult); setVal('sl-dry-mult', p.dry_mult);
  setVal('num-dry-base', p.dry_base); setVal('sl-dry-base', p.dry_base);
  setVal('num-dry-len', p.dry_len); setVal('sl-dry-len', p.dry_len);
  setVal('num-dry-pen', p.dry_pen); setVal('sl-dry-pen', p.dry_pen);
  setVal('num-xtc-prob', p.xtc_prob); setVal('sl-xtc-prob', p.xtc_prob);
  setVal('num-xtc-thresh', p.xtc_thresh); setVal('sl-xtc-thresh', p.xtc_thresh);
  setVal('num-dynatemp-range', p.dynatemp_range); setVal('sl-dynatemp-range', p.dynatemp_range);
  setVal('num-dynatemp-exp', p.dynatemp_exp); setVal('sl-dynatemp-exp', p.dynatemp_exp);
  setVal('harness-mirostat', p.mirostat);
  setVal('num-mirostat-tau', p.mirostat_tau); setVal('sl-mirostat-tau', p.mirostat_tau);
  setVal('num-mirostat-eta', p.mirostat_eta); setVal('sl-mirostat-eta', p.mirostat_eta);
  setVal('num-nbatch', p.nbatch); setVal('sl-nbatch', p.nbatch);
  setVal('num-nubatch', p.nubatch); setVal('sl-nubatch', p.nubatch);
  setVal('num-ngl', p.ngl); setVal('sl-ngl', p.ngl);
  setVal('num-rope-base', p.rope_base); setVal('sl-rope-base', p.rope_base);
  setVal('num-rope-scale', p.rope_scale); setVal('sl-rope-scale', p.rope_scale);
  setVal('harness-split-mode', p.split_mode);
  setVal('capacity-kv-quant', p.k_quant || p.kv_quant || 'f16');
  setVal('capacity-k-quant', p.k_quant || p.kv_quant || 'f16');
  setVal('capacity-v-quant', p.v_quant || p.kv_quant || 'f16');
  setCheck('harness-mlock', p.mlock);
  setCheck('harness-mmap', p.mmap !== false);
  setCheck('harness-gpu-kv', p.gpu_kv !== false);
  setVal('num-seed', p.seed || '-1');
  setVal('harness-custom-flags', p.custom_flags || '');
  setVal('capacity-weight-quant', p.weight_quant);
  setVal('capacity-model-params', p.model_params);
  setVal('capacity-params-select', p.model_params);
  setVal('capacity-custom-params', p.model_params);
  setVal('num-slots', p.slots);
  setVal('harness-flash-attn', p.flash_attn);
  setVal('num-draft-tokens', p.draft_tokens); setVal('sl-draft-tokens', p.draft_tokens);
  setVal('num-lookup-cache', p.lookup_cache); setVal('sl-lookup-cache', p.lookup_cache);

  setVal('num-threads', p.threads); setVal('sl-threads', p.threads);
  setVal('num-threads-batch', p.threads_batch); setVal('sl-threads-batch', p.threads_batch);
  setVal('harness-cpu-mask', p.cpu_mask);
  setVal('harness-cpu-strict', p.cpu_strict);
  setVal('harness-prio', p.prio);
  setVal('num-poll', p.poll); setVal('sl-poll', p.poll);
  setVal('harness-numa', p.numa);
  setVal('harness-device', p.device);
  setVal('harness-tensor-split', p.tensor_split);
  setVal('num-main-gpu', p.main_gpu);
  setCheck('harness-cpu-moe', p.cpu_moe);
  setVal('num-nc-moe', p.nc_moe); setVal('sl-nc-moe', p.nc_moe);
  setVal('num-nc-ffn', p.nc_ffn); setVal('sl-nc-ffn', p.nc_ffn);
  setVal('harness-fit', p.fit);
  setVal('num-fit-target', p.fit_target); setVal('sl-fit-target', p.fit_target);
  setVal('harness-load-mode', p.load_mode);
  setCheck('harness-lazy-mode', p.lazy_mode);
  setCheck('harness-kv-unified', p.kv_unified);
  setCheck('harness-kv-unified-slot', p.kv_unified_slot);
  setVal('num-cache-ram', p.cache_ram); setVal('sl-cache-ram', p.cache_ram);
  setVal('harness-cache-idle', p.cache_idle);
  setVal('num-cache-reuse', p.cache_reuse); setVal('sl-cache-reuse', p.cache_reuse);
  setCheck('harness-context-shift', p.context_shift);
  setCheck('harness-no-kv-offload', p.no_kv_offload);
  setVal('num-ctx-cp', p.ctx_cp); setVal('sl-ctx-cp', p.ctx_cp);
  setVal('num-cms', p.cms); setVal('sl-cms', p.cms);
  setCheck('harness-cont-batching', p.cont_batching);
  setVal('harness-rope-scaling', p.rope_scaling);
  setVal('harness-reasoning', p.reasoning);
  setVal('harness-reasoning-format', p.reasoning_format);
  setVal('num-reasoning-effort', p.reasoning_effort); setVal('sl-reasoning-effort', p.reasoning_effort);
  setVal('num-reasoning-budget', p.reasoning_budget); setVal('sl-reasoning-budget', p.reasoning_budget);
  setCheck('harness-reasoning-preserve', p.reasoning_preserve);

  recalculateHardwareCapacity();
}

export async function deleteSelectedProfile() {
  const select = document.getElementById('engine-profile-select');
  const id = select?.value;
  if (!id || !customProfiles[id]) {
    alert('Select a profile to delete.');
    return;
  }

  const ok = confirm(`Delete profile '${customProfiles[id].name || id}'?`);
  if (!ok) return;

  delete customProfiles[id];
  localStorage.setItem('stonesage_user_profiles', JSON.stringify(customProfiles));

  try {
    await fetch('/api/profiles/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: id })
    });
  } catch (_) {}

  renderProfileDropdown();
}

function setupSpeculativeControls() {
  window.toggleEngineSpeculative = async function(enabled) {
    try {
      const res = await fetch('/api/speculative/toggle', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: enabled })
      });
      const data = await res.json();
      alert(`Speculative Decoding: ${data.enabled ? 'ENABLED ⚡' : 'DISABLED'}`);
    } catch (e) {
      alert(`Speculative toggle failed: ${e}`);
    }
  };
}

// ============================================================================
// Model Downloader Engine
// ============================================================================

let downloadPollInterval = null;

export function onModelDownloadUrlChange(rawUrl) {
  const url = (rawUrl || '').trim();
  const filenameInput = document.getElementById('model-dl-filename');
  if (!url || !filenameInput) return;

  // Auto-extract filename from URL
  try {
    const cleanUrl = url.split('?')[0].split('#')[0];
    const parts = cleanUrl.split('/');
    let candidateName = decodeURIComponent(parts[parts.length - 1] || '');
    if (candidateName) {
      if (!candidateName.toLowerCase().endsWith('.gguf')) {
        candidateName = `${candidateName}.gguf`;
      }
      filenameInput.value = candidateName;
    }
  } catch (e) {
    console.warn('Could not auto-extract filename:', e);
  }
}
window.onModelDownloadUrlChange = onModelDownloadUrlChange;

export async function startModelDownload() {
  const urlInput = document.getElementById('model-dl-url');
  const filenameInput = document.getElementById('model-dl-filename');
  const nodeSelect = document.getElementById('harness-node-select');
  const startBtn = document.getElementById('btn-start-download');
  const cancelBtn = document.getElementById('btn-cancel-download');
  const badge = document.getElementById('model-dl-status-badge');
  const progressBox = document.getElementById('model-dl-progress-box');
  const infoEl = document.getElementById('model-dl-info');
  const barEl = document.getElementById('model-dl-bar');
  const percentEl = document.getElementById('model-dl-percent');

  let rawUrl = (urlInput ? urlInput.value : '').trim();
  const filename = (filenameInput ? filenameInput.value : '').trim();
  const nodeId = (nodeSelect ? nodeSelect.value : 'node1_primary') || 'node1_primary';

  if (!rawUrl) {
    alert('Please enter or paste a valid model download URL.');
    return;
  }

  // Automatic Hugging Face /blob/ -> /resolve/ fix
  if ((rawUrl.includes('huggingface.co') || rawUrl.includes('hf.co')) && rawUrl.includes('/blob/')) {
    rawUrl = rawUrl.replace('/blob/', '/resolve/');
    if (urlInput) urlInput.value = rawUrl;
  }

  if (startBtn) startBtn.disabled = true;
  if (cancelBtn) cancelBtn.style.display = 'inline-block';
  if (progressBox) progressBox.style.display = 'block';
  if (badge) {
    badge.textContent = 'CONNECTING...';
    badge.style.color = 'var(--term-accent-gold, #ffd700)';
  }
  if (infoEl) infoEl.textContent = `Connecting to stream: ${filename || 'model.gguf'}...`;
  if (barEl) barEl.style.width = '0%';
  if (percentEl) percentEl.textContent = '0%';

  try {
    const res = await fetch('/api/harness/models/download', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: rawUrl, filename: filename, node_id: nodeId })
    });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      throw new Error(data.error || `HTTP ${res.status}`);
    }

    // Begin polling progress
    if (downloadPollInterval) clearInterval(downloadPollInterval);
    downloadPollInterval = setInterval(pollModelDownloadStatus, 1000);
    pollModelDownloadStatus();
  } catch (err) {
    if (badge) {
      badge.textContent = 'ERROR';
      badge.style.color = '#ff4d4d';
    }
    if (infoEl) infoEl.textContent = `Download launch failed: ${err.message}`;
    if (startBtn) startBtn.disabled = false;
    if (cancelBtn) cancelBtn.style.display = 'none';
  }
}
window.startModelDownload = startModelDownload;

export async function pollModelDownloadStatus() {
  const startBtn = document.getElementById('btn-start-download');
  const cancelBtn = document.getElementById('btn-cancel-download');
  const badge = document.getElementById('model-dl-status-badge');
  const progressBox = document.getElementById('model-dl-progress-box');
  const infoEl = document.getElementById('model-dl-info');
  const barEl = document.getElementById('model-dl-bar');
  const percentEl = document.getElementById('model-dl-percent');
  const sizesEl = document.getElementById('model-dl-sizes');
  const speedEl = document.getElementById('model-dl-speed');
  const etaEl = document.getElementById('model-dl-eta');

  try {
    const res = await fetch('/api/harness/models/download/status');
    if (!res.ok) return;
    const data = await res.json();
    const dl = data.download || {};

    if (!dl || dl.status === 'idle') {
      if (downloadPollInterval) {
        clearInterval(downloadPollInterval);
        downloadPollInterval = null;
      }
      if (startBtn) startBtn.disabled = false;
      if (cancelBtn) cancelBtn.style.display = 'none';
      if (badge) {
        badge.textContent = 'READY';
        badge.style.color = 'var(--term-text-muted)';
      }
      return;
    }

    if (progressBox) progressBox.style.display = 'block';

    const percent = Math.min(100, Math.max(0, dl.percent || 0));
    if (barEl) barEl.style.width = `${percent}%`;
    if (percentEl) percentEl.textContent = `${percent.toFixed(1)}%`;

    const downloadedGb = ((dl.bytes_downloaded || 0) / (1024 ** 3)).toFixed(2);
    const totalGb = dl.total_bytes > 0 ? ((dl.total_bytes || 0) / (1024 ** 3)).toFixed(2) : '--';
    if (sizesEl) sizesEl.textContent = `${downloadedGb} GB / ${totalGb} GB`;

    if (speedEl) speedEl.textContent = `${dl.speed_mb || 0} MB/s`;

    if (etaEl) {
      if (dl.eta_seconds > 0) {
        const mins = Math.floor(dl.eta_seconds / 60);
        const secs = dl.eta_seconds % 60;
        etaEl.textContent = `ETA: ${mins}m ${secs < 10 ? '0' : ''}${secs}s`;
      } else {
        etaEl.textContent = 'ETA: --:--';
      }
    }

    if (dl.status === 'connecting') {
      if (badge) {
        badge.textContent = 'CONNECTING...';
        badge.style.color = 'var(--term-accent-gold, #ffd700)';
      }
      if (infoEl) infoEl.textContent = `Connecting to URL stream for '${dl.filename}'...`;
    } else if (dl.status === 'downloading') {
      if (badge) {
        badge.textContent = `DOWNLOADING (${percent.toFixed(0)}%)`;
        badge.style.color = 'var(--term-accent-gold, #ffd700)';
      }
      if (infoEl) infoEl.textContent = `Downloading ${dl.filename}...`;
      if (startBtn) startBtn.disabled = true;
      if (cancelBtn) cancelBtn.style.display = 'inline-block';
    } else if (dl.status === 'completed') {
      if (downloadPollInterval) {
        clearInterval(downloadPollInterval);
        downloadPollInterval = null;
      }
      if (badge) {
        badge.textContent = 'COMPLETED';
        badge.style.color = 'var(--term-success, #28a745)';
      }
      if (infoEl) infoEl.textContent = `✅ Successfully downloaded '${dl.filename}' to ${dl.target_node || 'target node'}!`;
      if (barEl) barEl.style.width = '100%';
      if (percentEl) percentEl.textContent = '100%';
      if (startBtn) startBtn.disabled = false;
      if (cancelBtn) cancelBtn.style.display = 'none';

      // Automatically refresh model registry so new model appears in dropdown
      if (typeof window.refreshNodeModels === 'function') {
        window.refreshNodeModels();
      }
    } else if (dl.status === 'error') {
      if (downloadPollInterval) {
        clearInterval(downloadPollInterval);
        downloadPollInterval = null;
      }
      if (badge) {
        badge.textContent = 'ERROR';
        badge.style.color = '#ff4d4d';
      }
      if (infoEl) infoEl.textContent = `Download failed: ${dl.error || 'Network error'}`;
      if (startBtn) startBtn.disabled = false;
      if (cancelBtn) cancelBtn.style.display = 'none';
    } else if (dl.status === 'cancelled') {
      if (downloadPollInterval) {
        clearInterval(downloadPollInterval);
        downloadPollInterval = null;
      }
      if (badge) {
        badge.textContent = 'CANCELLED';
        badge.style.color = 'var(--term-text-muted)';
      }
      if (infoEl) infoEl.textContent = `Download cancelled by operator.`;
      if (startBtn) startBtn.disabled = false;
      if (cancelBtn) cancelBtn.style.display = 'none';
    }
  } catch (e) {
    console.warn('Failed to poll download status:', e);
  }
}
window.pollModelDownloadStatus = pollModelDownloadStatus;

export async function cancelModelDownload() {
  const cancelBtn = document.getElementById('btn-cancel-download');
  const badge = document.getElementById('model-dl-status-badge');
  const infoEl = document.getElementById('model-dl-info');

  if (badge) badge.textContent = 'CANCELLING...';
  if (infoEl) infoEl.textContent = 'Aborting download and removing partial file...';
  if (cancelBtn) cancelBtn.disabled = true;

  try {
    await fetch('/api/harness/models/download/cancel', { method: 'POST' });
    setTimeout(pollModelDownloadStatus, 500);
  } catch (err) {
    console.error('Cancel request error:', err);
  } finally {
    if (cancelBtn) cancelBtn.disabled = false;
  }
}
window.cancelModelDownload = cancelModelDownload;

// Poll active download status on boot
setTimeout(pollModelDownloadStatus, 1500);

// Global window bindings
window.switchEngineLayer = switchEngineLayer;
window.saveCurrentProfile = saveCurrentProfile;
window.loadSelectedProfile = loadSelectedProfile;
window.deleteSelectedProfile = deleteSelectedProfile;
window.recalculateHardwareCapacity = recalculateHardwareCapacity;
