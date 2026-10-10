/**
 * StoneSage Harness Studio & Continuous Capacity Engine (v3.0)
 * LM Studio Parity: Dynamic fleet polling, node-specific GGUF discovery,
 * real-time VRAM/KV math, architectural context bounds, and 1-click model deployment.
 */

import { State } from './state.js';

let currentNodes = [];
let currentModels = [];
let selectedNodeId = 'node1_primary';
let selectedModelKey = '';

export function initHarness() {
  const ctxSlider = document.getElementById('capacity-ctx-slider');
  const ctxInput = document.getElementById('capacity-ctx-input');
  const slotsSlider = document.getElementById('capacity-slots-slider');
  const quantSelect = document.getElementById('capacity-quant-select');
  const vramSelect = document.getElementById('capacity-vram-select');
  const nglSlider = document.getElementById('capacity-ngl-slider');

  if (ctxSlider && ctxInput) {
    ctxSlider.addEventListener('input', () => {
      syncContextSliderToInput();
      updateCapacityCalculation();
    });

    ctxInput.addEventListener('input', () => {
      syncContextInputToSlider(ctxInput.value);
      updateCapacityCalculation();
    });
  }

  if (slotsSlider) {
    slotsSlider.addEventListener('input', () => {
      const valEl = document.getElementById('capacity-slots-val');
      if (valEl) valEl.textContent = `${slotsSlider.value} slot${slotsSlider.value > 1 ? 's' : ''}`;
      updateCapacityCalculation();
    });
  }

  if (nglSlider) {
    nglSlider.addEventListener('input', () => {
      updateCapacityCalculation();
    });
  }

  [quantSelect, vramSelect].forEach(el => {
    if (el) el.addEventListener('change', updateCapacityCalculation);
  });

  // Enter to send, Shift+Enter for newline in Playground prompt
  const playgroundPrompt = document.getElementById('playground-prompt-input');
  if (playgroundPrompt) {
    playgroundPrompt.addEventListener('keydown', (e) => {
      if ((e.key === 'Enter' || e.keyCode === 13) && !e.shiftKey && !e.ctrlKey && !e.altKey && !e.isComposing) {
        e.preventDefault();
        if (typeof window.runPlaygroundInference === 'function') {
          window.runPlaygroundInference();
        }
      }
    });
  }

  // Calculate capacity on initial render and probe fleet
  refreshHarnessNodes();

  // Listen for tab switch to sync 24/7 server running logs & reasoning state
  window.addEventListener('stonesage:tab-switched', (e) => {
    if (e.detail && e.detail.tab === 'harness') {
      refreshHarnessNodes();
      syncHarnessServerState();
    }
  });

  // Initial sync on page load
  syncHarnessServerState();
}

export function syncContextSliderToInput() {
  const slider = document.getElementById('capacity-ctx-slider');
  const input = document.getElementById('capacity-ctx-input');
  if (slider && input) {
    let val = parseInt(slider.value, 10);
    if (val < 4096) val = 4096;
    input.value = val;
    updateContextDisplay(val);
  }
}

export function syncContextInputToSlider(rawVal) {
  const slider = document.getElementById('capacity-ctx-slider');
  const input = document.getElementById('capacity-ctx-input');
  let val = parseInt(rawVal, 10);
  if (isNaN(val) || val < 4096) val = 4096;

  if (slider) {
    if (val > parseInt(slider.max, 10)) {
      slider.max = Math.max(val, 1048576);
    }
    slider.value = val;
  }
  updateContextDisplay(val);
}

function updateContextDisplay(ctx) {
  const label = document.getElementById('capacity-ctx-val');
  if (!label) return;

  if (ctx >= 1048576) {
    label.textContent = `(${(ctx / 1048576).toFixed(2)}M tokens)`;
  } else if (ctx >= 1024) {
    label.textContent = `(${(ctx / 1024).toFixed(0)}k tokens)`;
  } else {
    label.textContent = `(${ctx.toLocaleString()} tokens)`;
  }
}

export async function refreshHarnessNodes() {
  const badge = document.getElementById('harness-node-status-badge');
  const meta = document.getElementById('harness-node-meta');
  const select = document.getElementById('harness-node-select');
  if (badge) {
    badge.textContent = '[PROBING...]';
    badge.className = 'status-badge online';
    badge.style.background = '#854d0e';
  }

  try {
    const res = await fetch('/api/harness/nodes');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    currentNodes = data.nodes || [];
    const activeId = data.active_node_id || selectedNodeId || 'node1_primary';
    selectedNodeId = activeId;

    if (select) {
      select.innerHTML = currentNodes.map(n => {
        const statusIco = n.is_online ? '🟢' : '🔴';
        const activeTag = (n.id === activeId) ? ' [ACTIVE TARGET]' : '';
        return `<option value="${n.id}" ${n.id === activeId ? 'selected' : ''}>${statusIco} ${n.name}${activeTag}</option>`;
      }).join('');
    }

    onHarnessNodeSelected(selectedNodeId, false);
    updatePlaygroundModelList();
  } catch (err) {
    console.warn('Failed to probe nodes:', err);
    if (badge) {
      badge.textContent = '[PROBE ERROR]';
      badge.style.background = '#991b1b';
    }
  }
}
window.refreshHarnessNodes = refreshHarnessNodes;

export function onHarnessNodeSelected(nodeId, triggerRescan = true) {
  selectedNodeId = nodeId;
  const node = currentNodes.find(n => n.id === nodeId);
  const badge = document.getElementById('harness-node-status-badge');
  const meta = document.getElementById('harness-node-meta');
  const dualSplitGroup = document.getElementById('harness-dual-split-group');
  const vramSelect = document.getElementById('capacity-vram-select');

  if (node) {
    if (badge) {
      badge.textContent = node.is_online ? `[🟢 ONLINE]` : `[🔴 OFFLINE]`;
      badge.style.background = node.is_online ? '#166534' : '#991b1b';
    }
    if (meta) {
      const activeM = node.active_model ? `Loaded: ${node.active_model} (${node.active_context.toLocaleString()} ctx)` : 'No model loaded';
      meta.textContent = `${activeM} | Device: ${node.device_name} | Role: ${node.role}`;
    }

    // Auto-align VRAM select
    if (vramSelect) {
      if (nodeId === 'node1_primary') vramSelect.value = '12.0';
      else if (nodeId === 'node1_secondary') vramSelect.value = '8.0';
      else if (nodeId === 'vm102_dual') vramSelect.value = '20.0';
      else if (nodeId.includes('ally')) vramSelect.value = '16.0';
      else if (nodeId === 'local_workstation') vramSelect.value = '32.0';
    }
  }

  // Dual-GPU Split row visibility
  if (dualSplitGroup) {
    dualSplitGroup.style.display = (nodeId === 'vm102_dual') ? 'block' : 'none';
  }

  if (triggerRescan) {
    refreshNodeModels();
  }
}
window.onHarnessNodeSelected = onHarnessNodeSelected;

export function sortModelList(models, sortMode) {
  const mode = sortMode || (function() {
    try { return localStorage.getItem('stonesage_model_sort'); } catch (e) { return null; }
  })() || 'date-desc';
  const sorted = [...models];

  sorted.sort((a, b) => {
    if (mode === 'date-desc') {
      const ta = a.modified_time || 0;
      const tb = b.modified_time || 0;
      if (tb !== ta) return tb - ta;
      return (b.size_bytes || 0) - (a.size_bytes || 0);
    }
    if (mode === 'date-asc') {
      const ta = a.modified_time || 0;
      const tb = b.modified_time || 0;
      if (ta !== tb) return ta - tb;
      return (a.size_bytes || 0) - (b.size_bytes || 0);
    }
    if (mode === 'size-desc') {
      const sa = (a.size_bytes !== undefined ? a.size_bytes : (a.size_gb || 0) * 1e9);
      const sb = (b.size_bytes !== undefined ? b.size_bytes : (b.size_gb || 0) * 1e9);
      if (sb !== sa) return sb - sa;
      return (a.name || a.key || '').localeCompare(b.name || b.key || '');
    }
    if (mode === 'size-asc') {
      const sa = (a.size_bytes !== undefined ? a.size_bytes : (a.size_gb || 0) * 1e9);
      const sb = (b.size_bytes !== undefined ? b.size_bytes : (b.size_gb || 0) * 1e9);
      if (sa !== sb) return sa - sb;
      return (a.name || a.key || '').localeCompare(b.name || b.key || '');
    }
    if (mode === 'name-asc') {
      return (a.name || a.key || '').localeCompare(b.name || b.key || '');
    }
    if (mode === 'name-desc') {
      return (b.name || b.key || '').localeCompare(a.name || a.key || '');
    }
    return 0;
  });

  return sorted;
}

export function onModelSortChanged(newSortMode) {
  if (newSortMode) {
    try { localStorage.setItem('stonesage_model_sort', newSortMode); } catch (e) {}
  }
  renderModelOptions();
}
window.onModelSortChanged = onModelSortChanged;

export function renderModelOptions() {
  const select = document.getElementById('harness-model-select');
  const sortSelect = document.getElementById('harness-model-sort');
  if (!select) return;

  const currentSort = (sortSelect && sortSelect.value) || (function() {
    try { return localStorage.getItem('stonesage_model_sort'); } catch (e) { return null; }
  })() || 'date-desc';

  if (sortSelect && sortSelect.value !== currentSort) {
    sortSelect.value = currentSort;
  }

  if (!currentModels || currentModels.length === 0) {
    select.innerHTML = '<option value="">(No models found on target node)</option>';
    return;
  }

  const previousVal = select.value || selectedModelKey;
  const sorted = sortModelList(currentModels, currentSort);

  select.innerHTML = sorted.map(m => {
    const loadedTag = m.is_loaded ? ' ⚡ [LOADED]' : '';
    const maxCtxK = m.max_context ? ` (${Math.round(m.max_context / 1024)}k ctx)` : '';
    const dateStr = m.modified_time ? ` [${new Date(m.modified_time * 1000).toISOString().slice(0, 10)}]` : '';
    return `<option value="${m.key}">${m.name || m.key} - ${m.size_gb}GB [${m.quant || 'GGUF'}]${maxCtxK}${dateStr}${loadedTag}</option>`;
  }).join('');

  const match = sorted.find(m => m.key === previousVal);
  if (match) {
    select.value = match.key;
  } else {
    const defaultModel = sorted.find(m => m.is_loaded) || sorted[0];
    if (defaultModel) {
      select.value = defaultModel.key;
      onHarnessModelSelected(defaultModel.key);
    }
  }
}
window.renderModelOptions = renderModelOptions;

export async function refreshNodeModels() {
  const select = document.getElementById('harness-model-select');
  const meta = document.getElementById('harness-model-meta');
  if (select) {
    select.innerHTML = '<option value="">-- Scanning models on node... --</option>';
  }
  if (meta) {
    meta.textContent = 'Querying node filesystem and model manifest...';
  }

  try {
    const res = await fetch(`/api/harness/models?node=${encodeURIComponent(selectedNodeId)}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    currentModels = data.models || [];

    if (select) {
      if (currentModels.length === 0) {
        select.innerHTML = '<option value="">(No models found on target node)</option>';
        if (meta) meta.textContent = 'Zero GGUF models discovered.';
        return;
      }

      renderModelOptions();

      if (select.value) {
        onHarnessModelSelected(select.value);
      }
    }
  } catch (err) {
    console.warn('Failed to scan models:', err);
    if (meta) meta.textContent = `Error scanning models: ${err.message}`;
  }
}
window.refreshNodeModels = refreshNodeModels;

export function onHarnessModelSelected(modelKey) {
  selectedModelKey = modelKey;
  const m = currentModels.find(x => x.key === modelKey || x.name === modelKey);
  window.selectedModelInfo = m;
  const meta = document.getElementById('harness-model-meta');
  const ctxSlider = document.getElementById('capacity-ctx-slider');
  const ctxInput = document.getElementById('capacity-ctx-input');

  if (!m) return;

  // Auto-detect model parameter size from model filename or metadata
  const paramMatch = (m.name || m.key || '').match(/(\d+(?:\.\d+)?)\s*[bB]/);
  const detectedParams = paramMatch ? parseFloat(paramMatch[1]) : (m.params ? parseFloat(m.params) : null);
  const paramsInput = document.getElementById('capacity-model-params');
  const paramBadge = document.getElementById('capacity-param-badge');
  if (paramsInput && detectedParams) {
    paramsInput.value = detectedParams;
    if (paramBadge) {
      paramBadge.textContent = `⚡ AUTO: ${detectedParams}B`;
      paramBadge.style.color = 'var(--term-success)';
    }
  }

  // Update Model Info Banner elements
  const infoName = document.getElementById('info-model-name');
  const infoSize = document.getElementById('info-model-size');
  const infoQuant = document.getElementById('info-model-quant');
  const infoArch = document.getElementById('info-model-arch');
  const infoCtx = document.getElementById('info-model-ctx');
  const infoStatus = document.getElementById('info-model-status');

  const maxCtx = m.max_context || 131072;
  if (infoName) infoName.textContent = m.name || m.key;
  if (infoSize) infoSize.textContent = `${m.size_gb} GB [DISK]`;
  if (infoQuant) infoQuant.textContent = m.quant || 'GGUF';
  if (infoArch) infoArch.textContent = `${detectedParams ? detectedParams + 'B ' : ''}${m.architecture || 'GGUF'}`;
  if (infoCtx) infoCtx.textContent = `${maxCtx.toLocaleString()} tokens`;
  if (infoStatus) {
    infoStatus.textContent = m.is_loaded ? '🟢 ACTIVE ON NODE' : '⚪ AVAILABLE ON DISK';
    infoStatus.className = `status-badge ${m.is_loaded ? 'online' : ''}`;
  }

  // Auto-align hardware VRAM limit to target node
  const vramSelect = document.getElementById('capacity-vram-select');
  if (vramSelect) {
    if (selectedNodeId === 'node1_secondary') {
      vramSelect.value = '8.0';
    } else if (selectedNodeId === 'vm102_dual') {
      vramSelect.value = '20.0';
    } else {
      vramSelect.value = '12.0';
    }
  }

  if (meta) {
    meta.textContent = `Context Ceiling: ${maxCtx.toLocaleString()} tokens | Actual File: ${m.size_gb} GB | Quant: ${m.quant || 'GGUF'} | Architecture: ${detectedParams ? detectedParams + 'B' : (m.architecture || 'Auto')}`;
  }

  // Allow context bounds to scale up to 1M+ tokens without arbitrary clamping
  if (ctxSlider) {
    ctxSlider.max = Math.max(maxCtx, 1048576);
    let currentVal = parseInt(ctxSlider.value, 10) || 16384;
    ctxSlider.value = currentVal;
    if (ctxInput) ctxInput.value = currentVal;
    updateContextDisplay(currentVal);
  }

  if (typeof window.recalculateHardwareCapacity === 'function') {
    window.recalculateHardwareCapacity();
  } else {
    updateCapacityCalculation();
  }
}
window.onHarnessModelSelected = onHarnessModelSelected;

export async function deploySelectedModelToNode() {
  const node = currentNodes.find(n => n.id === selectedNodeId);
  const model = currentModels.find(m => m.key === selectedModelKey);
  const nodeName = node ? node.name : selectedNodeId;
  const modelName = model ? (model.name || model.key) : selectedModelKey;

  if (!selectedModelKey) {
    alert('Please select a model to deploy onto the node.');
    return;
  }

  const ctxInput = document.getElementById('capacity-ctx-input');
  const slotsSlider = document.getElementById('capacity-slots-slider');
  const dualSplitInput = document.getElementById('harness-dual-split-input');

  const numCtxEl = document.getElementById('num-ctx');
  const contextLength = parseInt(ctxInput?.value || numCtxEl?.value || '16384', 10);
  const parallelSlots = parseInt(slotsSlider?.value || document.getElementById('num-slots')?.value || '1', 10);
  const cacheTypeK = document.getElementById('capacity-k-quant')?.value || document.getElementById('capacity-kv-quant')?.value || 'f16';
  const cacheTypeV = document.getElementById('capacity-v-quant')?.value || document.getElementById('capacity-kv-quant')?.value || 'f16';
  const dualSplit = (selectedNodeId === 'vm102_dual') ? (dualSplitInput?.value || '12,8') : null;

  const mlock = !!document.getElementById('harness-mlock')?.checked;
  const mmap = document.getElementById('harness-mmap')?.checked !== false;
  const gpuKv = document.getElementById('harness-gpu-kv')?.checked !== false;
  const seed = document.getElementById('num-seed')?.value || '-1';
  const customFlags = document.getElementById('harness-custom-flags')?.value || '';

  const payload = {
    node_id: selectedNodeId,
    model_key: selectedModelKey,
    context_length: contextLength,
    parallel_slots: parallelSlots,
    cache_type_k: cacheTypeK,
    cache_type_v: cacheTypeV,
    kv_quant: cacheTypeK,
    dual_gpu_split: dualSplit,
    flash_attn: document.getElementById('harness-flash-attn')?.value || 'on',
    n_gpu_layers: parseInt(document.getElementById('num-ngl')?.value || '99', 10),
    batch_size: parseInt(document.getElementById('num-nbatch')?.value || '2048', 10),
    ubatch_size: parseInt(document.getElementById('num-nubatch')?.value || '512', 10),
    threads: parseInt(document.getElementById('num-threads')?.value || '8', 10),
    threads_batch: parseInt(document.getElementById('num-threads-batch')?.value) || undefined,
    mlock: mlock,
    mmap: mmap,
    offload_kv_cache_to_gpu: gpuKv,
    no_kv_offload: !gpuKv,
    seed: seed,
    custom_flags: customFlags,
    cpu_mask: document.getElementById('harness-cpu-mask')?.value || undefined,
    cpu_strict: document.getElementById('harness-cpu-strict')?.value || undefined,
    prio: document.getElementById('harness-prio')?.value || undefined,
    poll: parseInt(document.getElementById('num-poll')?.value) || undefined,
    numa: document.getElementById('harness-numa')?.value || undefined,
    split_mode: document.getElementById('harness-split-mode')?.value || undefined,
    tensor_split: document.getElementById('harness-tensor-split')?.value || dualSplit || undefined,
    main_gpu: parseInt(document.getElementById('num-main-gpu')?.value) || undefined,
    cpu_moe: document.getElementById('harness-cpu-moe')?.checked || undefined,
    n_cpu_moe: parseInt(document.getElementById('num-nc-moe')?.value) || undefined,
    n_cpu_ffn: parseInt(document.getElementById('num-nc-ffn')?.value) || undefined,
    fit: document.getElementById('harness-fit')?.value || undefined,
    fit_target: parseInt(document.getElementById('num-fit-target')?.value) || undefined,
    load_mode: document.getElementById('harness-load-mode')?.value || undefined,
    lazy_mode: document.getElementById('harness-lazy-mode')?.checked || undefined,
    kv_unified: document.getElementById('harness-kv-unified')?.checked || undefined,
    kv_unified_per_slot: document.getElementById('harness-kv-unified-slot')?.checked || undefined,
    context_shift: document.getElementById('harness-context-shift')?.checked !== false,
    cache_ram: parseInt(document.getElementById('num-cache-ram')?.value) || undefined,
    cache_idle_slots: document.getElementById('harness-cache-idle')?.value || undefined,
    cache_reuse: parseInt(document.getElementById('num-cache-reuse')?.value) || undefined,
    ctx_checkpoints: parseInt(document.getElementById('num-ctx-cp')?.value) || undefined,
    checkpoint_min_step: parseInt(document.getElementById('num-cms')?.value) || undefined,
    defrag_thold: parseFloat(document.getElementById('num-defrag')?.value) || undefined,
    cont_batching: document.getElementById('harness-cont-batching')?.checked || undefined,
    reasoning: document.getElementById('harness-reasoning')?.value || undefined,
    reasoning_format: document.getElementById('harness-reasoning-format')?.value || undefined,
    reasoning_effort: parseFloat(document.getElementById('num-reasoning-effort')?.value) || undefined,
    reasoning_budget: parseInt(document.getElementById('num-reasoning-budget')?.value) || undefined,
    reasoning_preserve: document.getElementById('harness-reasoning-preserve')?.checked || undefined,
    rope_scaling: document.getElementById('harness-rope-scaling')?.value || undefined,
    rope_scale: parseFloat(document.getElementById('num-rope-scale')?.value) || undefined,
    rope_freq_base: parseInt(document.getElementById('num-rope-base')?.value) || undefined,
    rope_freq_scale: parseFloat(document.getElementById('num-rope-scale')?.value) || undefined
  };

  const confirmMsg = `Deploy Model onto Compute Node?\n\nTarget Node: ${nodeName}\nModel: ${modelName}\nContext Window: ${contextLength === 0 ? 'Model Default' : contextLength.toLocaleString() + ' tokens'}\nParallel Slots: ${parallelSlots}\nK Cache: ${cacheTypeK.toUpperCase()}\nV Cache: ${cacheTypeV.toUpperCase()}` + (dualSplit ? `\nDual-GPU Split: ${dualSplit}` : '');

  if (!confirm(confirmMsg)) return;

  const btn = document.getElementById('btn-deploy-calculated-stack');
  const deployText = document.getElementById('deploy-summary-text');
  if (btn) btn.disabled = true;
  if (deployText) deployText.textContent = `⏳ Loading ${modelName} onto ${nodeName}... (Reconfiguring service and verifying health)`;

  try {
    const res = await fetch('/api/harness/load', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });

    const data = await res.json();
    if (data.ok) {
      if (deployText) deployText.textContent = `✔ Successfully loaded ${modelName} onto ${nodeName}! (${contextLength.toLocaleString()} context)`;
      alert(`✔ Model Loaded Successfully!\n\n${modelName} is now live on ${nodeName}.`);
      await refreshHarnessNodes();
    } else {
      if (deployText) deployText.textContent = `❌ Load failed: ${data.error}`;
      alert(`Load failed: ${data.error}`);
    }
  } catch (err) {
    if (deployText) deployText.textContent = `❌ Error: ${err.message}`;
    alert(`Error: ${err.message}`);
  } finally {
    if (btn) btn.disabled = false;
  }
}
window.deploySelectedModelToNode = deploySelectedModelToNode;
window.deployCalculatedStack = deploySelectedModelToNode;

export async function unloadActiveModel() {
  const node = currentNodes.find(n => n.id === selectedNodeId);
  const nodeName = node ? node.name : selectedNodeId;
  if (!confirm(`Unload active model and free GPU VRAM on ${nodeName}?`)) return;

  const btn = document.getElementById('btn-unload-model');
  const deployText = document.getElementById('deploy-summary-text');
  if (btn) btn.disabled = true;
  if (deployText) deployText.textContent = `⏳ Unloading model from ${nodeName}...`;

  try {
    const res = await fetch('/api/harness/unload', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ node_id: selectedNodeId })
    });
    const data = await res.json();
    if (data.ok) {
      if (deployText) deployText.textContent = `✔ Successfully unloaded model from ${nodeName}! GPU VRAM released.`;
      alert(`✔ Model Unloaded Successfully!\n\nGPU VRAM on ${nodeName} is now released.`);
      await refreshHarnessNodes();
    } else {
      if (deployText) deployText.textContent = `❌ Unload failed: ${data.error || data.message}`;
      alert(`Unload failed: ${data.error || data.message}`);
    }
  } catch (err) {
    if (deployText) deployText.textContent = `❌ Error: ${err.message}`;
    alert(`Error: ${err.message}`);
  } finally {
    if (btn) btn.disabled = false;
  }
}
window.unloadActiveModel = unloadActiveModel;

function updatePlaygroundModelList() {
  const playSelect = document.getElementById('playground-model-select');
  if (!playSelect || currentNodes.length === 0) return;

  const activeNodes = currentNodes.filter(n => n.is_online && n.active_model);
  if (activeNodes.length === 0) return;

  playSelect.innerHTML = activeNodes.map(n => {
    return `<option value="${n.id}">${n.name}: ${n.active_model} (${n.active_context.toLocaleString()} ctx)</option>`;
  }).join('');
}

export async function updateCapacityCalculation() {
  const ctxInput = document.getElementById('capacity-ctx-input');
  const ctxSlider = document.getElementById('capacity-ctx-slider');
  const slotsSlider = document.getElementById('capacity-slots-slider');
  const quantSel = document.getElementById('capacity-quant-select');
  const vramSel = document.getElementById('capacity-vram-select');
  const nglSlider = document.getElementById('capacity-ngl-slider');
  const nglVal = document.getElementById('capacity-ngl-val');

  let ctx = 16384;
  if (ctxInput && ctxInput.value) {
    ctx = parseInt(ctxInput.value, 10);
  } else if (ctxSlider) {
    ctx = parseInt(ctxSlider.value, 10);
  }

  // Strict agent context floor
  if (isNaN(ctx) || ctx < 4096) ctx = 4096;

  const slots = parseInt(slotsSlider?.value || '1', 10);
  const quant = quantSel?.value || 'q8_0';
  const vram = parseFloat(vramSel?.value || '12.0');

  // Infer architecture and layer parameters from selected model
  const model = currentModels.find(m => m.key === selectedModelKey);
  let arch = '9b';
  let totalLayers = 42;
  if (model && Number.isInteger(model.layers)) {
    totalLayers = model.layers;  // real layer count from the GGUF header
  } else if (model) {
    const fn = (model.name || model.key || '').toLowerCase();
    if (fn.includes('27b')) { arch = '27b'; totalLayers = 64; }
    else if (fn.includes('35b')) { arch = '35b_moe'; totalLayers = 40; }
    else if (fn.includes('14b') || fn.includes('12b')) { arch = '14b'; totalLayers = 48; }
    else if (fn.includes('3b')) { arch = '3b'; totalLayers = 32; }
    else if (fn.includes('70b')) { arch = '70b'; totalLayers = 80; }
    else { arch = '9b'; totalLayers = 42; }
  }

  if (nglSlider) {
    nglSlider.max = totalLayers;
    if (parseInt(nglSlider.value, 10) > totalLayers) nglSlider.value = totalLayers;
    if (nglVal) {
      const v = parseInt(nglSlider.value, 10);
      nglVal.textContent = (v >= totalLayers) ? `All (${totalLayers}/${totalLayers})` : `${v}/${totalLayers}`;
    }
  }

  State.harness.contextLength = ctx;
  State.harness.slots = slots;
  State.harness.arch = arch;
  State.harness.quant = quant;
  State.harness.vram = vram;

  try {
    const res = await fetch('/api/harness/capacity', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        arch_type: arch,
        model_file: model ? (model.filename || model.key) : undefined,
        quant: quant,
        context_length: ctx,
        parallel_slots: slots,
        target_vram_gb: vram
      })
    });

    const data = await res.json();
    if (!data.ok || !data.estimate) return;

    const est = data.estimate;
    State.harness.estimate = est;

    // Render readouts
    const wEl = document.getElementById('cap-weights-gb');
    const kvEl = document.getElementById('cap-kv-gb');
    const actEl = document.getElementById('cap-act-gb');
    const totEl = document.getElementById('cap-total-gb');
    const headEl = document.getElementById('cap-headroom-gb');
    const badgeEl = document.getElementById('cap-verdict-badge');

    if (wEl) wEl.textContent = `${est.base_weights_gb.toFixed(2)} GB`;
    if (kvEl) kvEl.textContent = `${est.kv_cache_gb.toFixed(2)} GB`;
    if (actEl) actEl.textContent = `${est.activation_gb.toFixed(2)} GB`;
    if (totEl) totEl.textContent = `${est.total_required_gb.toFixed(2)} GB`;
    if (headEl) {
      headEl.textContent = `${est.vram_headroom_gb.toFixed(2)} GB`;
      headEl.style.color = est.vram_headroom_gb >= 0 ? 'var(--term-success)' : '#ef4444';
    }
    if (badgeEl) {
      if (est.fits_in_vram) {
        badgeEl.textContent = `[FITS 100% IN VRAM (${est.gpu_layers_offload}/${est.total_layers} LAYERS)]`;
        badgeEl.style.background = '#166534';
      } else {
        badgeEl.textContent = `[PARTIAL OFFLOAD: ${est.gpu_layers_offload}/${est.total_layers} LAYERS (${est.cpu_ram_spillover_gb.toFixed(1)}GB RAM)]`;
        badgeEl.style.background = '#9a3412';
      }
    }

  } catch (err) {
    console.warn('Capacity calculation error:', err);
  }
}

/**
 * 1-Click Fleet Operational Presets
 */
window.applyCapacityPreset = function(presetName) {
  const ctxSlider = document.getElementById('capacity-ctx-slider');
  const ctxInput = document.getElementById('capacity-ctx-input');
  const slotsSlider = document.getElementById('capacity-slots-slider');
  const quantSel = document.getElementById('capacity-quant-select');
  const vramSel = document.getElementById('capacity-vram-select');

  const setCtx = (val) => {
    if (ctxSlider) {
      if (val > parseInt(ctxSlider.max, 10)) ctxSlider.max = Math.max(val, 1048576);
      ctxSlider.value = val;
    }
    if (ctxInput) ctxInput.value = val;
    updateContextDisplay(val);
  };

  if (presetName === 'ally_matrix') {
    setCtx(8192);
    if (slotsSlider) slotsSlider.value = 4;
    if (quantSel) quantSel.value = 'q4_0';
    if (vramSel) vramSel.value = '16.0';
  } else if (presetName === 'speculative_daily') {
    setCtx(16384);
    if (slotsSlider) slotsSlider.value = 1;
    if (quantSel) quantSel.value = 'q8_0';
    if (vramSel) vramSel.value = '12.0';
  } else if (presetName === 'deep_monolith') {
    setCtx(32768);
    if (slotsSlider) slotsSlider.value = 1;
    if (quantSel) quantSel.value = 'q8_0';
    if (vramSel) vramSel.value = '20.0';
  } else if (presetName === 'frontier_128k') {
    setCtx(131072);
    if (slotsSlider) slotsSlider.value = 1;
    if (quantSel) quantSel.value = 'q4_0';
    if (vramSel) vramSel.value = '20.0';
  } else if (presetName === 'ultra_256k') {
    setCtx(262144);
    if (slotsSlider) slotsSlider.value = 1;
    if (quantSel) quantSel.value = 'q4_0';
    if (vramSel) vramSel.value = '24.0';
  } else if (presetName === 'needle_1m') {
    setCtx(1048576);
    if (slotsSlider) slotsSlider.value = 1;
    if (quantSel) quantSel.value = 'q4_0';
    if (vramSel) vramSel.value = '32.0';
  }

  const valEl = document.getElementById('capacity-slots-val');
  if (valEl && slotsSlider) valEl.textContent = `${slotsSlider.value} slot${slotsSlider.value > 1 ? 's' : ''}`;

  updateCapacityCalculation();
};

/**
 * LM Studio-Grade Interactive Model Playground & Streaming Probe
 */
let playgroundAbortController = null;

window.setPlaygroundPreset = function(preset) {
  const tempInput = document.getElementById('harness-temp-input');
  const minpInput = document.getElementById('harness-minp-input');
  const presInput = document.getElementById('harness-pres-input');

  document.querySelectorAll('#harness-playground-card .preset-btn').forEach(btn => btn.classList.remove('active'));

  if (preset === 'code') {
    if (tempInput) tempInput.value = '0.65';
    if (minpInput) minpInput.value = '0.08';
    if (presInput) presInput.value = '0.20';
    document.getElementById('preset-btn-code')?.classList.add('active');
  } else if (preset === 'creative') {
    if (tempInput) tempInput.value = '0.78';
    if (minpInput) minpInput.value = '0.05';
    if (presInput) presInput.value = '0.30';
    document.getElementById('preset-btn-creative')?.classList.add('active');
  } else if (preset === 'fast') {
    if (tempInput) tempInput.value = '0.70';
    if (minpInput) minpInput.value = '0.06';
    if (presInput) presInput.value = '0.15';
    document.getElementById('preset-btn-fast')?.classList.add('active');
  }
};

window.togglePlaygroundReasoning = function() {
  const rPane = document.getElementById('playground-reasoning-pane');
  const oPane = document.getElementById('playground-output-pane');
  const btn = document.getElementById('playground-toggle-reasoning-btn');
  if (!rPane) return;
  const isHidden = rPane.style.display === 'none';
  rPane.style.display = isHidden ? 'flex' : 'none';
  if (oPane) {
    oPane.style.gridColumn = isHidden ? '' : '1 / -1';
  }
  if (btn) {
    btn.textContent = isHidden ? '[🧠 HIDE REASONING]' : '[🧠 SHOW REASONING]';
  }
};

window.togglePlaygroundParallelView = function() {
  const grid = document.getElementById('playground-streams-grid');
  const btn = document.getElementById('playground-parallel-view-btn');
  if (!grid) return;
  const isParallel = grid.style.gridTemplateColumns !== '1fr';
  if (isParallel) {
    grid.style.gridTemplateColumns = '1fr';
    if (btn) {
      btn.textContent = '[⚡ PARALLEL VIEW: OFF]';
      btn.style.color = 'var(--term-text-muted)';
    }
  } else {
    grid.style.gridTemplateColumns = 'minmax(0, 1fr) minmax(0, 1.1fr)';
    if (btn) {
      btn.textContent = '[⚡ PARALLEL VIEW: ON]';
      btn.style.color = 'var(--term-accent-green)';
    }
  }
};

window.clearPlayground = function() {
  const promptEl = document.getElementById('playground-prompt-input');
  const outputEl = document.getElementById('playground-output-stream');
  const reasoningEl = document.getElementById('playground-reasoning-content');
  const countEl = document.getElementById('playground-reasoning-count');
  const debugEl = document.getElementById('playground-debug-content');

  if (promptEl) promptEl.value = '';
  if (outputEl) outputEl.textContent = 'Awaiting prompt dispatch...';
  if (reasoningEl) reasoningEl.textContent = 'No active reasoning thoughts extracted yet.';
  if (countEl) countEl.textContent = '0 chars';
  if (debugEl) debugEl.textContent = '{"status": "cleared", "timestamp": ' + Date.now() + '}';

  document.getElementById('m-ttft').textContent = '-- ms';
  document.getElementById('m-tps').textContent = '-- tok/s';
  document.getElementById('m-tokens').textContent = '0';
  document.getElementById('m-elapsed').textContent = '0.0s';
};


window.stopPlaygroundInference = async function() {
  if (playgroundAbortController) {
    playgroundAbortController.abort();
    playgroundAbortController = null;
  }
  try {
    await fetch('/api/harness/session/stop', { method: 'POST' });
  } catch (_) {}

  const runBtn = document.getElementById('playground-run-btn');
  const stopBtn = document.getElementById('playground-stop-btn');
  const statusBadge = document.getElementById('playground-status-badge');

  if (runBtn) runBtn.style.display = 'inline-block';
  if (stopBtn) stopBtn.style.display = 'none';
  if (statusBadge) {
    statusBadge.textContent = '⏹ STOPPED';
    statusBadge.className = 'status-badge offline';
  }
};

window.runPlaygroundInference = async function() {
  const promptInput = document.getElementById('playground-prompt-input');
  const prompt = promptInput ? promptInput.value.trim() : '';
  if (!prompt) {
    alert('Please enter a prompt to run inference.');
    return;
  }

  const modelSelect = document.getElementById('playground-model-select');
  const targetModel = modelSelect ? modelSelect.value : 'coordinator';

  const temp = parseFloat(document.getElementById('harness-temp-input')?.value || '0.70');
  const minp = parseFloat(document.getElementById('harness-minp-input')?.value || '0.06');
  const pres = parseFloat(document.getElementById('harness-pres-input')?.value || '0.20');

  const runBtn = document.getElementById('playground-run-btn');
  const stopBtn = document.getElementById('playground-stop-btn');
  const statusBadge = document.getElementById('playground-status-badge');
  const outputEl = document.getElementById('playground-output-stream');
  const reasoningEl = document.getElementById('playground-reasoning-content');
  const reasoningCount = document.getElementById('playground-reasoning-count');
  const reasoningAccordion = document.getElementById('playground-reasoning-accordion');
  const debugEl = document.getElementById('playground-debug-content');
  const debugStatus = document.getElementById('playground-debug-status');

  const ttftEl = document.getElementById('m-ttft');
  const tpsEl = document.getElementById('m-tps');
  const tokensEl = document.getElementById('m-tokens');
  const elapsedEl = document.getElementById('m-elapsed');

  if (runBtn) runBtn.style.display = 'none';
  if (stopBtn) stopBtn.style.display = 'inline-block';
  if (statusBadge) {
    statusBadge.textContent = '⚡ STREAMING...';
    statusBadge.className = 'status-badge online';
  }
  if (outputEl) outputEl.textContent = '';
  if (reasoningEl) reasoningEl.textContent = '';
  if (reasoningCount) reasoningCount.textContent = '0 chars';
  if (debugStatus) debugStatus.textContent = 'Connecting...';

  playgroundAbortController = new AbortController();
  const startTime = performance.now();
  let firstTokenTime = null;
  let tokenCount = 0;
  let outputBuffer = '';
  let reasoningBuffer = '';
  let inThinkTag = false;

  const debugPayload = {
    model: targetModel,
    prompt: prompt,
    sampling: { temperature: temp, min_p: minp, presence_penalty: pres },
    started_at: new Date().toISOString()
  };
  if (debugEl) debugEl.textContent = JSON.stringify(debugPayload, null, 2);

  try {
    const response = await fetch('/api/cluster/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        target: targetModel,
        session_id: `playground_${Date.now()}`,
        messages: [{ role: 'user', content: prompt }],
        params: {
          temperature: temp,
          min_p: minp,
          presence_penalty: pres
        }
      }),
      signal: playgroundAbortController.signal
    });

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }

    if (debugStatus) debugStatus.textContent = 'Receiving stream...';

    const reader = response.body.getReader();
    const decoder = new TextDecoder();

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      if (!firstTokenTime) {
        firstTokenTime = performance.now();
        const ttftMs = Math.round(firstTokenTime - startTime);
        if (ttftEl) ttftEl.textContent = `${ttftMs} ms`;
      }

      const chunk = decoder.decode(value, { stream: true });
      const lines = chunk.split('\n');

      for (const line of lines) {
        if (!line.startsWith('data: ') || line === 'data: [DONE]') continue;

        try {
          const parsed = JSON.parse(line.slice(6));
          const delta = parsed.choices?.[0]?.delta || {};

          // 1. Check reasoning_content from model
          if (delta.reasoning_content) {
            reasoningBuffer += delta.reasoning_content;
            tokenCount++;
            if (reasoningEl) reasoningEl.textContent = reasoningBuffer;
            if (reasoningCount) reasoningCount.textContent = `${reasoningBuffer.length} chars`;
            if (reasoningAccordion) reasoningAccordion.open = true;
          }

          // 2. Check standard content and inspect for <think>...</think>
          if (delta.content) {
            const txt = delta.content;
            tokenCount++;

            if (txt.includes('<think>')) {
              inThinkTag = true;
              if (reasoningAccordion) reasoningAccordion.open = true;
            }

            if (inThinkTag) {
              if (txt.includes('</think>')) {
                inThinkTag = false;
                const parts = txt.split('</think>');
                reasoningBuffer += parts[0].replace('<think>', '');
                outputBuffer += parts[1] || '';
              } else {
                reasoningBuffer += txt.replace('<think>', '');
              }
              if (reasoningEl) {
                reasoningEl.textContent = reasoningBuffer;
                reasoningEl.scrollTop = reasoningEl.scrollHeight;
              }
              if (reasoningCount) reasoningCount.textContent = `${reasoningBuffer.length} chars`;
            } else {
              outputBuffer += txt;
              if (outputEl) {
                outputEl.textContent = outputBuffer;
                outputEl.scrollTop = outputEl.scrollHeight;
              }
            }
          }

          // Update real-time performance ribbon
          const now = performance.now();
          const elapsedSec = Math.max((now - (firstTokenTime || startTime)) / 1000, 0.05);
          const tps = Math.round(tokenCount / elapsedSec);

          if (tpsEl) tpsEl.textContent = `${tps} tok/s`;
          if (tokensEl) tokensEl.textContent = tokenCount;
          if (elapsedEl) elapsedEl.textContent = `${elapsedSec.toFixed(1)}s`;

        } catch (_) {}
      }
    }

    const totalDuration = ((performance.now() - startTime) / 1000).toFixed(2);
    if (debugStatus) debugStatus.textContent = 'Completed';
    if (debugEl) {
      debugEl.textContent = JSON.stringify({
        ...debugPayload,
        completed_at: new Date().toISOString(),
        total_tokens: tokenCount,
        ttft_ms: Math.round((firstTokenTime || startTime) - startTime),
        tokens_per_second: Math.round(tokenCount / Math.max(parseFloat(totalDuration), 0.1)),
        duration_seconds: totalDuration,
        status: 'success'
      }, null, 2);
    }

    if (statusBadge) {
      statusBadge.textContent = '🟢 FINISHED';
      statusBadge.className = 'status-badge online';
    }

  } catch (err) {
    if (err.name === 'AbortError') {
      if (outputEl) outputEl.textContent += '\n\n[INFERENCE ABORTED BY USER]';
      if (debugStatus) debugStatus.textContent = 'Aborted';
    } else {
      if (outputEl) outputEl.textContent += `\n\n[ERROR: ${err.message}]`;
      if (debugStatus) debugStatus.textContent = 'Error';
    }
  } finally {
    if (runBtn) runBtn.style.display = 'inline-block';
    if (stopBtn) stopBtn.style.display = 'none';
    playgroundAbortController = null;
  }
};

let harnessPollTimer = null;

export async function syncHarnessServerState() {
  try {
    // 1. Fetch server logs
    const logRes = await fetch('/api/harness/logs?limit=80');
    if (logRes.ok) {
      const logData = await logRes.json();
      const logs = logData.logs || [];
      const debugEl = document.getElementById('playground-debug-content');
      const debugStatus = document.getElementById('playground-debug-status');
      if (debugEl && logs.length > 0) {
        debugEl.textContent = logs.map(l => `[${l.time}] [${l.level}] [${l.logger}] ${l.message}`).join('\n');
      }
      if (debugStatus) {
        debugStatus.textContent = `${logs.length} entries`;
      }
    }

    // 2. Fetch active session state
    const sessRes = await fetch('/api/harness/active-session');
    if (sessRes.ok) {
      const sessData = await sessRes.json();
      const s = sessData.session;
      if (s) {
        const statusBadge = document.getElementById('playground-status-badge');
        const runBtn = document.getElementById('playground-run-btn');
        const stopBtn = document.getElementById('playground-stop-btn');
        const promptInput = document.getElementById('playground-prompt-input');
        const outputEl = document.getElementById('playground-output-stream');
        const reasoningEl = document.getElementById('playground-reasoning-content');
        const reasoningCount = document.getElementById('playground-reasoning-count');
        const reasoningAccordion = document.getElementById('playground-reasoning-accordion');
        const ttftEl = document.getElementById('m-ttft');
        const tpsEl = document.getElementById('m-tps');
        const tokensEl = document.getElementById('m-tokens');
        const elapsedEl = document.getElementById('m-elapsed');

        // Populate prompt if empty
        if (promptInput && !promptInput.value.trim() && s.prompt) {
          promptInput.value = s.prompt;
        }

        // Metrics
        if (s.metrics) {
          if (ttftEl && s.metrics.ttft_ms) ttftEl.textContent = `${s.metrics.ttft_ms} ms`;
          if (tpsEl && s.metrics.tps) tpsEl.textContent = `${s.metrics.tps} tok/s`;
          if (tokensEl && s.metrics.tokens !== undefined) tokensEl.textContent = s.metrics.tokens;
          if (elapsedEl && s.metrics.elapsed_sec) elapsedEl.textContent = `${s.metrics.elapsed_sec}s`;
        }

        // Output & Reasoning
        if (s.output && outputEl && (!playgroundAbortController || outputEl.textContent === 'Awaiting prompt dispatch...')) {
          outputEl.textContent = s.output;
        }
        if (s.reasoning_trace && reasoningEl) {
          reasoningEl.textContent = s.reasoning_trace;
          if (reasoningCount) reasoningCount.textContent = `${s.reasoning_trace.length} chars`;
          if (reasoningAccordion) reasoningAccordion.open = true;
        }

        // Status badge & buttons
        if (s.status === 'generating') {
          if (statusBadge) {
            statusBadge.textContent = '⚡ RUNNING (24/7)';
            statusBadge.className = 'status-badge online';
          }
          if (runBtn) runBtn.style.display = 'none';
          if (stopBtn) stopBtn.style.display = 'inline-block';

          // Schedule next poll while active
          clearTimeout(harnessPollTimer);
          harnessPollTimer = setTimeout(syncHarnessServerState, 1500);
        } else if (s.status === 'tool_calling') {
          if (statusBadge) {
            statusBadge.textContent = `🛠️ TOOL: ${s.active_tool || 'executing'}`;
            statusBadge.className = 'status-badge online';
          }
          if (runBtn) runBtn.style.display = 'none';
          if (stopBtn) stopBtn.style.display = 'inline-block';

          clearTimeout(harnessPollTimer);
          harnessPollTimer = setTimeout(syncHarnessServerState, 1500);
        } else if (s.status === 'stuck_loop') {
          if (statusBadge) {
            statusBadge.textContent = '⚠️ REASONING LOOP';
            statusBadge.className = 'status-badge offline';
          }
          if (runBtn) runBtn.style.display = 'inline-block';
          if (stopBtn) stopBtn.style.display = 'none';
        } else if (s.status === 'completed') {
          if (statusBadge && statusBadge.textContent.includes('RUNNING')) {
            statusBadge.textContent = '🟢 FINISHED';
            statusBadge.className = 'status-badge online';
          }
          if (runBtn) runBtn.style.display = 'inline-block';
          if (stopBtn) stopBtn.style.display = 'none';
        }
      }
    }
  } catch (err) {
    console.debug('Harness server state sync skipped:', err);
  }
}
window.syncHarnessServerState = syncHarnessServerState;
