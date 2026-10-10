/**
 * StoneSage Fleet Matrix, Memory & Workstation Controller (v3.0)
 * Multi-Workstation Continuum, Valkey A-MEM with core-memory tags, and Obsidian 3-file reader.
 */

import { State, escapeHtml } from './state.js';
import { switchSession } from './chat.js';
import { openFileInTerminal } from './terminal.js';
import { Profile, engine, engineShort } from './profile.js';

export function initFleet() {
  loadFleetInstances();
  loadAmemCards();
  loadObsidianNotes();
  loadWorkspaces();
  loadHaData();
  loadTrainerWatchdogData();

  // Periodic background telemetry polling (every 8s)
  setInterval(() => {
    loadFleetInstances(false);
  }, 8000);

  // Tab switch listener to refresh data on demand
  window.addEventListener('stonesage:tab-switched', (e) => {
    if (e.detail.tab === 'fleet') loadFleetInstances(true);
    if (e.detail.tab === 'memory') loadAmemCards();
    if (e.detail.tab === 'obsidian') loadObsidianNotes();
    if (e.detail.tab === 'ha') loadHaData();
    if (e.detail.tab === 'trainer') loadTrainerWatchdogData();
    if (e.detail.tab === 'workstation') {
      loadWorkspaces();
      loadWorkstationTree();
    }
  });

  // Workstation UI Event Listeners
  const wsSelect = document.getElementById('workstation-workspace-select');
  if (wsSelect) {
    wsSelect.addEventListener('change', async (e) => {
      const selectedPath = e.target.value;
      const found = State.workspaces.find(w => w.path === selectedPath);
      State.activeWorkspace = found || (selectedPath ? { path: selectedPath, name: selectedPath.split('/').pop() } : null);
      updateWorkspaceBadge();
      await syncWorkspaceAgent(selectedPath);
      loadWorkstationTree(selectedPath);
    });
  }

  const autonomySelect = document.getElementById('workstation-autonomy-select');
  if (autonomySelect) {
    autonomySelect.addEventListener('change', (e) => {
      State.autonomyLevel = e.target.value;
    });
  }

  const refreshTreeBtn = document.getElementById('ws-tree-refresh-btn');
  if (refreshTreeBtn) {
    refreshTreeBtn.addEventListener('click', () => {
      const activePath = State.activeWorkspace ? State.activeWorkspace.path : '';
      loadWorkstationTree(activePath);
    });
  }

  // Modal: mkdir Project
  const mkdirBtn = document.getElementById('ws-mkdir-btn');
  const modalMkdir = document.getElementById('modal-mkdir');
  const mkdirCancel = document.getElementById('mkdir-cancel-btn');
  const mkdirSubmit = document.getElementById('mkdir-submit-btn');

  if (mkdirBtn && modalMkdir) {
    mkdirBtn.addEventListener('click', () => {
      modalMkdir.style.display = 'flex';
      const nameInput = document.getElementById('mkdir-name-input');
      if (nameInput) {
        nameInput.value = '';
        nameInput.focus();
      }
    });
  }

  if (mkdirCancel && modalMkdir) {
    mkdirCancel.addEventListener('click', () => {
      modalMkdir.style.display = 'none';
    });
  }

  if (mkdirSubmit && modalMkdir) {
    mkdirSubmit.addEventListener('click', handleCreateProject);
  }

  // Modal: mkdir Project Agent Checkbox toggle
  const mkdirAgentCheck = document.getElementById('mkdir-init-agent-check');
  const mkdirAgentFields = document.getElementById('mkdir-agent-fields');
  if (mkdirAgentCheck && mkdirAgentFields) {
    mkdirAgentCheck.addEventListener('change', (e) => {
      mkdirAgentFields.style.display = e.target.checked ? 'block' : 'none';
    });
  }

  // Modal: Add Custom Root
  const addRootBtn = document.getElementById('ws-add-root-btn');
  const modalAddRoot = document.getElementById('modal-add-root');
  const addRootCancel = document.getElementById('add-root-cancel-btn');
  const addRootSubmit = document.getElementById('add-root-submit-btn');

  if (addRootBtn && modalAddRoot) {
    addRootBtn.addEventListener('click', () => {
      modalAddRoot.style.display = 'flex';
      const labelInput = document.getElementById('add-root-label-input');
      if (labelInput) {
        labelInput.value = '';
        labelInput.focus();
      }
    });
  }

  if (addRootCancel && modalAddRoot) {
    addRootCancel.addEventListener('click', () => {
      modalAddRoot.style.display = 'none';
    });
  }

  if (addRootSubmit && modalAddRoot) {
    addRootSubmit.addEventListener('click', handleAddRoot);
  }

  // Modal: Agent DNA & Invariants Contract Editor
  const agentBadgeBtn = document.getElementById('workstation-agent-badge');
  const modalAgentDna = document.getElementById('modal-agent-dna');
  const agentDnaCancel = document.getElementById('agent-dna-cancel-btn');
  const agentDnaSave = document.getElementById('agent-dna-save-btn');

  if (agentBadgeBtn) {
    agentBadgeBtn.addEventListener('click', openAgentDnaModal);
  }

  if (agentDnaCancel && modalAgentDna) {
    agentDnaCancel.addEventListener('click', () => {
      modalAgentDna.style.display = 'none';
    });
  }

  if (agentDnaSave) {
    agentDnaSave.addEventListener('click', handleSaveAgentDna);
  }

  // DNA Tabs listeners
  ['invariants', 'identity', 'soul', 'handover'].forEach(tab => {
    const btn = document.getElementById(`tab-dna-${tab}`);
    if (btn) {
      btn.addEventListener('click', () => switchDnaTab(tab));
    }
  });

  // Close modals on Escape key or backdrop click
  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      if (modalMkdir) modalMkdir.style.display = 'none';
      if (modalAddRoot) modalAddRoot.style.display = 'none';
      if (modalAgentDna) modalAgentDna.style.display = 'none';
    }
  });

  [modalMkdir, modalAddRoot, modalAgentDna].forEach(m => {
    if (m) {
      m.addEventListener('click', (e) => {
        if (e.target === m) m.style.display = 'none';
      });
    }
  });
}

/**
 * Multi-Workstation Continuum & Dynamic Infrastructure Polling
 */
let cachedClusterHealth = null;
let cachedProxmoxNodes = null;
let cachedModelsInfo = null;
let cachedClusterModes = null;
window._coordShort = '';

function hostOf(url) {
  try { return new URL(url).hostname; } catch (_) { return ''; }
}


export async function loadFleetInstances(forceRefresh = false) {
  const container = document.getElementById('fleet-nodes-container');
  const topbarNodeSelect = document.getElementById('topbar-node-select');
  const drawerNodeSelect = document.getElementById('drawer-node-select');
  const topbarPingText = document.getElementById('topbar-ping-text');
  const mobilePingText = document.getElementById('mobile-ping-text');
  const pingBadge = document.getElementById('topbar-ping-badge');
  const mobilePingBtn = document.getElementById('mobile-ping-btn');
  const topbarModelSelect = document.getElementById('topbar-model-select');
  const mobileModelText = document.getElementById('mobile-model-text');

  try {
    const [instRes, healthRes, proxRes, modelsRes, modesRes] = await Promise.all([
      fetch('/api/harness/instances').then(r => r.json()).catch(() => ({ ok: false })),
      fetch('/api/cluster/health').then(r => r.json()).catch(() => ({ ok: false })),
      fetch('/api/proxmox/nodes').then(r => r.json()).catch(() => ({ ok: false })),
      fetch('/api/cluster/models').then(r => r.json()).catch(() => ({ ok: false })),
      fetch('/api/cluster/modes').then(r => r.json()).catch(() => ({ ok: false }))
    ]);

    if (modelsRes && modelsRes.ok) {
      cachedModelsInfo = modelsRes;
    }

    if (modesRes && modesRes.ok) {
      cachedClusterModes = modesRes;
      try {
        renderClusterModesList(modesRes);
      } catch (me) {
        console.warn('renderClusterModesList error:', me);
      }
    }

    if (healthRes && healthRes.ok && healthRes.cluster) {
      cachedClusterHealth = healthRes.cluster;
      updateClusterHealthBadge(healthRes.cluster, pingBadge, mobilePingBtn, topbarPingText, mobilePingText);
      updateModelSelectors(healthRes.cluster, topbarModelSelect, mobileModelText, cachedModelsInfo, cachedClusterModes);
      renderClusterHealthPopover(healthRes.cluster, proxRes?.nodes);
    } else {
      updateClusterHealthBadge({}, pingBadge, mobilePingBtn, topbarPingText, mobilePingText);
    }

    // Real per-service health overrides the badge: it is the first thing on screen.
    const svcHealth = await fetch(`/api/health/all${forceRefresh ? '?fresh=1' : ''}`).then(r => r.json()).catch(() => null);
    renderServiceHealth(svcHealth, pingBadge, mobilePingBtn, topbarPingText, mobilePingText);

    if (proxRes && proxRes.ok && proxRes.nodes) {
      cachedProxmoxNodes = proxRes.nodes;
    }

    if (instRes && instRes.ok && Array.isArray(instRes.instances)) {
      const instances = instRes.instances;
      const activeInst = instances.find(i => i.is_active) || instances[0];

      const nodeOptions = instances.map(inst => `
        <option value="${inst.id}" data-host="${escapeHtml(hostOf(inst.url))}" ${inst.is_active ? 'selected' : ''}>
          ${inst.reachable ? '🟢' : '🔴'} ${escapeHtml(inst.name)}
        </option>
      `).join('');

      if (topbarNodeSelect && topbarNodeSelect.innerHTML !== nodeOptions) {
        topbarNodeSelect.innerHTML = nodeOptions;
      }
      if (drawerNodeSelect && drawerNodeSelect.innerHTML !== nodeOptions) {
        drawerNodeSelect.innerHTML = nodeOptions;
      }
      const rootNodeSelect = document.getElementById('add-root-node-select');
      if (rootNodeSelect) {
        const rootOptions = '<option value="local">This machine</option>' + instances.filter(i => !i.is_base).map(inst =>
          `<option value="${inst.id}" data-host="${escapeHtml(hostOf(inst.url))}">${escapeHtml(inst.name)}</option>`).join('');
        if (rootNodeSelect.innerHTML !== rootOptions) rootNodeSelect.innerHTML = rootOptions;
      }

      if (container) {
        renderFleetGrid(container, instances, cachedProxmoxNodes, cachedClusterHealth);
      }
    }

  } catch (err) {
    console.warn('Error loading fleet instances:', err);
    updateClusterHealthBadge({}, pingBadge, mobilePingBtn, topbarPingText, mobilePingText);
  }
}

function updateClusterHealthBadge(cluster, pingBadge, mobilePingBtn, topbarPingText, mobilePingText) {
  cluster = cluster || {};
  const coord = cluster.coordinator;
  const embed = cluster.embedder_bge || cluster.embedder;
  const qdrant = cluster.qdrant_brain || cluster.qdrant;
  const worker = cluster.worker;

  let bestLatency = coord?.online ? coord.latency_ms : (embed?.online ? embed.latency_ms : (qdrant?.online ? qdrant.latency_ms : null));
  const isOnline = Boolean(coord?.online || embed?.online || qdrant?.online);

  const text = isOnline ? `🟢 ${bestLatency ? bestLatency + 'ms' : 'ONLINE'}` : `🔴 OFFLINE`;
  const cls = isOnline ? 'topbar-badge-btn online' : 'topbar-badge-btn offline';

  if (topbarPingText) topbarPingText.textContent = text;
  if (mobilePingText) mobilePingText.textContent = isOnline ? `${bestLatency ? bestLatency + 'ms' : 'ON'}` : 'OFF';
  if (pingBadge) pingBadge.className = cls;
  if (mobilePingBtn) mobilePingBtn.className = cls;
}

/**
 * /api/health/all: one row per service with its real state (engines report model, ctx per slot, slots).
 */
function renderServiceHealth(res, pingBadge, mobilePingBtn, topbarPingText, mobilePingText) {
  const list = document.getElementById('service-health-list');
  if (!res || !Array.isArray(res.services)) {
    if (topbarPingText) topbarPingText.textContent = '🔴 HEALTH API DOWN';
    if (mobilePingText) mobilePingText.textContent = 'ERR';
    if (pingBadge) pingBadge.className = 'topbar-badge-btn offline';
    if (mobilePingBtn) mobilePingBtn.className = 'topbar-badge-btn offline';
    if (list) list.innerHTML = '<div style="color:#ef4444;">/api/health/all unreachable</div>';
    return;
  }
  const up = res.services.filter(s => s.ok).length;
  const total = res.services.length;
  const cls = res.ok ? 'topbar-badge-btn online' : 'topbar-badge-btn offline';
  if (topbarPingText) topbarPingText.textContent = `${res.ok ? '🟢' : '🔴'} ${up}/${total} UP`;
  if (mobilePingText) mobilePingText.textContent = `${up}/${total}`;
  if (pingBadge) { pingBadge.className = cls; pingBadge.title = res.summary; }
  if (mobilePingBtn) mobilePingBtn.className = cls;
  if (!list) return;

  list.innerHTML = res.services.map(s => {
    const d = s.detail || {};
    const info = d.model
      ? `${escapeHtml(d.model)} · ${d.slots ?? '?'}×${d.ctx_per_slot ?? '?'} ctx`
      : (s.ok ? (d.check === 'tcp' ? 'port open' : '') : escapeHtml(String(d.error || '')).slice(0, 90));
    const color = s.ok ? '#22c55e' : (s.status === 'loading' ? '#f59e0b' : '#ef4444');
    return `
      <div style="display:flex; gap:8px; align-items:baseline;">
        <span style="color:${color}; min-width:62px;">${s.ok ? '● OK' : '● ' + escapeHtml(s.status.toUpperCase())}</span>
        <span style="min-width:150px; color:var(--term-text-bright);">${escapeHtml(s.name)}</span>
        <span style="color:var(--term-text-muted); overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${info}</span>
        <span style="margin-left:auto; color:var(--term-text-muted);">${s.latency_ms != null ? s.latency_ms + 'ms' : ''}</span>
      </div>`;
  }).join('');
}

function updateModelSelectors(cluster, topbarModelSelect, mobileModelText, modelsInfo = null, modesInfo = null) {
  const coordData = cluster.coordinator || {};
  const workerData = cluster.worker || {};
  const coordOnline = Boolean(coordData.online);
  const workerOnline = Boolean(workerData.online);

  // names, ports and context come from /api/system/profile (live /props + hardware probe)
  const describe = (role, data, online) => {
    const e = engine(role);
    const port = e && e.port ? ` :${e.port}` : '';
    const name = e && e.model ? `${e.label}${port}` : `${role}${port}`;
    const ctx = e && e.ctx_per_slot ? `${e.ctx_per_slot}×${e.slots || 1} ctx` : '';
    const desc = online ? [ctx, data.latency_ms ? `${data.latency_ms}ms` : ''].filter(Boolean).join(' • ') : 'Offline / Standby';
    return { name, desc, short: engineShort(role) };
  };
  const c = describe('coordinator', coordData, coordOnline);
  const w = describe('worker', workerData, workerOnline);
  const coordName = c.name, coordDesc = c.desc, coordShort = c.short;
  const workerName = w.name, workerDesc = w.desc, workerShort = w.short;
  window._coordShort = coordShort;

  const models = [
    { id: 'antigravity', name: '🌌 Antigravity Frontier Director (Tier-1 Hybrid)', short: 'AGY Director', desc: 'Tier-1 Meta-Verifier directing cluster to minimize cloud tokens', online: true, latency: null },
    { id: 'coordinator', name: coordName, short: coordShort, desc: coordDesc, online: coordOnline, latency: coordData.latency_ms },
    { id: 'worker', name: workerName, short: workerShort, desc: workerDesc, online: workerOnline, latency: workerData.latency_ms },
    { id: 'gemini', name: 'Gemini Tier-1 (Frontier Cloud)', short: 'Gemini', desc: 'Supreme Arbiter & Meta-Verifier', online: true, latency: null },
    ...(window.boostModelEntries || [])  // js/boost.js: free pool entries while Boost chat is on
  ];

  if (!State.activeModel) State.activeModel = 'coordinator';

  if (topbarModelSelect) {
    const optionsHtml = models.map(m => `
      <option value="${m.id}" ${State.activeModel === m.id ? 'selected' : ''}>
        ${m.online ? '🟢' : '🔴'} ${escapeHtml(m.name)}
      </option>
    `).join('');
    if (topbarModelSelect.innerHTML !== optionsHtml) {
      topbarModelSelect.innerHTML = optionsHtml;
      topbarModelSelect.onchange = (e) => selectModel(e.target.value);
    }
  }

  if (mobileModelText) {
    const curr = models.find(m => m.id === State.activeModel) || models[0];
    const shortName = curr.id === 'antigravity' ? 'AGY' : (curr.id === 'gemini' ? 'Frontier' : curr.short);
    mobileModelText.textContent = `🤖 ${shortName}`;
  }

  const popoverList = document.getElementById('model-popover-list');
  if (popoverList) {
    popoverList.innerHTML = models.map(m => `
      <div class="mobile-popover-item ${State.activeModel === m.id ? 'active' : ''}" onclick="selectModel('${m.id}')">
        <div>
          <div style="font-weight:bold; color:var(--term-text-bright); display:flex; align-items:center; gap:6px;">
            <span>${m.online ? '🟢' : '🔴'}</span>
            <span>${escapeHtml(m.name)}</span>
            ${State.activeModel === m.id ? '<span style="font-size:0.7rem; color:var(--term-accent-gold);">[ACTIVE]</span>' : ''}
          </div>
          <div style="font-size:0.74rem; color:var(--term-text-muted); margin-top:2px;">
            ${escapeHtml(m.desc)} ${m.latency ? `(${m.latency}ms)` : ''}
          </div>
        </div>
        <div style="font-size:1.1rem; color:var(--term-border-bright);">
          ${State.activeModel === m.id ? '🔘' : '⚪'}
        </div>
      </div>
    `).join('');
  }
}

function renderClusterHealthPopover(cluster, nodes) {
  const body = document.getElementById('cluster-health-popover-body');
  if (!body) return;

  const mcp = cluster.mcp_bridge;
  const qdrant = cluster.qdrant_brain;

  let nodesHtml = '';
  if (nodes) {
    nodesHtml = `
      <div style="border-top:1px dashed var(--term-border-dim); padding-top:6px; margin-top:4px;">
        <div style="font-weight:bold; color:var(--term-accent-gold); margin-bottom:4px;">PROXMOX HYPERVISORS:</div>
        ${Object.values(nodes).map(n => `
          <div style="display:flex; justify-content:space-between; margin-bottom:2px;">
            <span>${n.online ? '🟢' : '🔴'} <strong>${escapeHtml(n.node)}</strong> ${n.ip ? `(${escapeHtml(n.ip)})` : ''}</span>
            <span>CPU: <strong>${n.cpu_pct || 0}%</strong> • RAM: <strong>${n.memory_pct || 0}%</strong> (${n.memory_used_gb || 0}G)</span>
          </div>
        `).join('')}
      </div>
    `;
  }

  body.innerHTML = `
    <div style="display:flex; flex-direction:column; gap:5px;">
      ${Object.values((Profile.data && Profile.data.engines) || {}).map(e => `
      <div style="display:flex; justify-content:space-between;">
        <span>${e.online ? '🟢' : '🔴'} ${escapeHtml(e.label || e.role)} (:${e.port})</span>
        <span>${e.online ? (e.ctx_per_slot ? `${e.ctx_per_slot}×${e.slots || 1} ctx` : 'online') : 'Offline'}</span>
      </div>`).join('')}
      <div style="display:flex; justify-content:space-between;">
        <span>${mcp?.online ? '🟢' : '🔴'} Cluster MCP Bridge (:8765)</span>
        <span>${mcp?.online ? `${mcp.latency_ms}ms` : 'Offline'}</span>
      </div>
      <div style="display:flex; justify-content:space-between;">
        <span>${qdrant?.online ? '🟢' : '🔴'} Qdrant Vector Brain (:6333)</span>
        <span>${qdrant?.online ? `${qdrant.latency_ms}ms` : 'Offline'}</span>
      </div>
    </div>
    ${nodesHtml}
  `;
}

function renderFleetGrid(container, instances, nodes, cluster) {
  let nodesCards = '';
  if (nodes) {
    nodesCards = Object.values(nodes).map(n => `
      <div class="node-card active-node">
        <div class="card-header">
          <span>🖥️ Proxmox Node: <strong>${escapeHtml(n.node.toUpperCase())}</strong></span>
          <span class="status-badge ${n.online ? 'online' : 'offline'}">${n.online ? '🟢 ONLINE' : '🔴 OFFLINE'}</span>
        </div>
        <div style="font-size:0.78rem; color:var(--term-text-muted); display:flex; flex-direction:column; gap:3px;">
          ${n.ip ? `<div>IP: <code>${escapeHtml(n.ip)}</code></div>` : ''}
          <div>CPU Usage: <strong>${n.cpu_pct || 0}%</strong></div>
          <div>Memory: <strong>${n.memory_used_gb || 0} GB / ${n.memory_total_gb || 0} GB (${n.memory_pct || 0}%)</strong></div>
          <div>Disk: <strong>${n.disk_used_gb || 0} GB / ${n.disk_total_gb || 0} GB (${n.disk_pct || 0}%)</strong></div>
          <div>Uptime: <strong>${Math.floor((n.uptime_sec || 0)/3600)}h ${Math.floor(((n.uptime_sec || 0)%3600)/60)}m</strong></div>
        </div>
      </div>
    `).join('');
  }

  const instCards = instances.map(inst => `
    <div class="node-card ${inst.is_active ? 'active-node' : ''}">
      <div class="card-header">
        <span>${inst.is_active ? '⭐' : '🖥️'} ${escapeHtml(inst.name)}</span>
        <span class="status-badge ${inst.reachable ? 'online' : 'offline'}">
          ${inst.reachable ? `🟢 ONLINE (${inst.latency_ms}ms)` : '🔴 UNREACHABLE'}
        </span>
      </div>
      <div style="font-size: 0.78rem; color: var(--term-text-muted);">
        <div>Endpoint: <code>${escapeHtml(inst.url || inst.host)}</code></div>
        <div>Role: <strong>${escapeHtml(inst.role || inst.description || 'Cluster Endpoint')}</strong></div>
      </div>
      <div style="display:flex; gap:6px; margin-top:4px;">
        <button class="term-cmd-btn" onclick="switchActiveNode('${inst.id}')" ${inst.is_active ? 'disabled' : ''}>
          ${inst.is_active ? '[CURRENT ACTIVE]' : '[SELECT ENDPOINT]'}
        </button>
        ${inst.id !== 'base_server' && inst.id !== 'vm102_compute' ? `
          <button class="term-cmd-btn" onclick="deleteNode('${inst.id}')" style="color:var(--term-accent-red);">
            [REMOVE]
          </button>
        ` : ''}
      </div>
    </div>
  `).join('');

  container.innerHTML = nodesCards + instCards;
}

window.openAddNodeModal = function() {
  const m = document.getElementById('modal-add-node');
  if (m) m.style.display = 'flex';
  const nameInp = document.getElementById('add-node-name');
  if (nameInp) nameInp.focus();
};

window.closeAddNodeModal = function() {
  const m = document.getElementById('modal-add-node');
  if (m) m.style.display = 'none';
  const st = document.getElementById('add-node-status');
  if (st) st.style.display = 'none';
};

window.submitAddNode = async function() {
  const name = document.getElementById('add-node-name')?.value.trim();
  const url = document.getElementById('add-node-url')?.value.trim();
  const desc = document.getElementById('add-node-desc')?.value.trim();
  const st = document.getElementById('add-node-status');
  const btn = document.getElementById('btn-submit-add-node');

  if (!url) {
    if (st) {
      st.style.display = 'block';
      st.style.color = 'var(--term-accent-red)';
      st.textContent = '❌ Endpoint Base URL is required.';
    }
    return;
  }

  if (btn) btn.disabled = true;
  if (st) {
    st.style.display = 'block';
    st.style.color = 'var(--term-accent-gold)';
    st.textContent = '⏳ Testing reachability and registering node...';
  }

  try {
    const res = await fetch('/api/harness/instances/add', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: name || url, url: url, description: desc || 'Custom Model Host' })
    });
    const data = await res.json();
    if (data.ok) {
      if (st) {
        st.style.color = 'var(--term-accent-green)';
        st.textContent = '✔ Node registered successfully!';
      }
      setTimeout(() => {
        closeAddNodeModal();
        loadFleetInstances(true);
      }, 700);
    } else {
      if (st) {
        st.style.color = 'var(--term-accent-red)';
        st.textContent = `❌ ${data.error || 'Failed to add node'}`;
      }
    }
  } catch (err) {
    if (st) {
      st.style.color = 'var(--term-accent-red)';
      st.textContent = `❌ Error: ${err.message}`;
    }
  } finally {
    if (btn) btn.disabled = false;
  }
};

window.deleteNode = async function(instId) {
  if (!confirm(`Are you sure you want to remove compute node '${instId}' from your continuum?`)) return;
  try {
    const res = await fetch('/api/harness/instances/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: instId })
    });
    const data = await res.json();
    if (data.ok) {
      loadFleetInstances(true);
    } else {
      alert(`Failed to remove node: ${data.error}`);
    }
  } catch (err) {
    alert(`Error removing node: ${err.message}`);
  }
};

// ==========================================
// Cluster Hardware Stack Switching Controller
// ==========================================
export function renderClusterModesList(modesData) {
  const container = document.getElementById('cluster-modes-list') || document.getElementById('cluster-modes-container');
  if (!container || !modesData) return;

  const rawProfiles = modesData.profiles || modesData.modes || modesData.available_modes;
  if (!rawProfiles) return;

  const activeMode = modesData.active_mode || modesData.current_mode;
  const profilesList = Array.isArray(rawProfiles) ? rawProfiles : Object.entries(rawProfiles).map(([k, v]) => ({ id: k, ...v }));

  container.innerHTML = profilesList.map(p => {
    const isAct = p.id === activeMode;
    return `
      <div style="background:var(--term-bg); border:1px solid ${isAct ? 'var(--term-accent-gold)' : 'var(--term-border-dim)'}; border-radius:3px; padding:8px 10px; display:flex; justify-content:space-between; align-items:center; gap:8px;">
        <div style="flex:1; min-width:0;">
          <div style="font-weight:bold; color:var(--term-text-bright); font-size:0.8rem; display:flex; align-items:center; gap:6px;">
            <span>${isAct ? '⚡' : '💾'}</span>
            <span>${escapeHtml(p.name)}</span>
            ${p.recommended ? '<span style="font-size:0.68rem; color:var(--term-accent-green); background:rgba(34,197,94,0.1); padding:1px 4px; border-radius:2px;">[RECOMMENDED]</span>' : ''}
            ${isAct ? '<span style="font-size:0.7rem; color:var(--term-accent-gold);">[ACTIVE]</span>' : ''}
          </div>
          <div style="font-size:0.72rem; color:var(--term-text-muted); margin-top:2px;">
            ${p.speed ? `Speed: <strong style="color:var(--term-accent-gold);">${escapeHtml(p.speed)}</strong> • ` : ''}${p.context ? `Context: ${escapeHtml(p.context)} • ` : ''}VRAM: ${escapeHtml(p.vram || '')}
          </div>
        </div>
        ${modesData.switching === false ? '' : `<button type="button" class="term-cmd-btn" onclick="switchClusterMode('${p.id}')" ${isAct ? 'disabled' : ''} style="font-size:0.74rem; white-space:nowrap; ${isAct ? 'opacity:0.6;' : 'background:var(--term-accent-blue); color:#fff; font-weight:bold;'}">
          ${isAct ? '✔ LOADED' : '[⚡ ACTIVATE]'}
        </button>`}
      </div>
    `;
  }).join('');
}

window.switchClusterMode = async function(modeId) {
  const statusEl = document.getElementById('stack-switch-status');
  if (statusEl) statusEl.textContent = `⏳ Switching to ${modeId}... (30-60s)`;
  try {
    const res = await fetch('/api/cluster/mode/switch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode: modeId })
    });
    const data = await res.json();
    if (data.ok) {
      if (statusEl) statusEl.textContent = `✔ Deployed ${modeId}!`;
      setTimeout(() => {
        if (statusEl) statusEl.textContent = '';
        loadFleetInstances(true);
      }, 1500);
    } else {
      if (statusEl) statusEl.textContent = `❌ Failed: ${data.error}`;
      alert(`Failed to switch cluster mode: ${data.error}`);
    }
  } catch (err) {
    if (statusEl) statusEl.textContent = `❌ Error: ${err.message}`;
    alert(`Error: ${err.message}`);
  }
};

window.deployCalculatedStack = async function() {
  if (typeof window.deploySelectedModelToNode === 'function') {
    return window.deploySelectedModelToNode();
  }
  alert('Harness model deployment engine initializing. Please try again.');
};


// ==========================================
// Agent Directory & Persona Controller
// ==========================================
let cachedAgentsList = null;

window.openAgentDirectoryModal = async function() {
  const m = document.getElementById('modal-agent-directory');
  if (m) m.style.display = 'flex';
  await loadAgentDirectory();
};

window.closeAgentDirectoryModal = function() {
  const m = document.getElementById('modal-agent-directory');
  if (m) m.style.display = 'none';
};

window.toggleCreateAgentForm = function() {
  const f = document.getElementById('create-agent-form');
  if (f) f.style.display = f.style.display === 'none' ? 'block' : 'none';
};

window.loadAgentDirectory = async function() {
  const bannerName = document.getElementById('agent-active-name');
  const builtinGrid = document.getElementById('builtin-agents-grid');
  const customGrid = document.getElementById('custom-agents-grid');
  const wsSection = document.getElementById('workspace-agent-section');
  const wsCard = document.getElementById('workspace-agent-card');

  if (bannerName) {
    if (State.activeAgent && State.activeAgent.name) {
      bannerName.textContent = `${State.activeAgent.icon || '🤖'} ${State.activeAgent.name} (${State.activeAgent.role || 'Active'})`;
    } else {
      bannerName.textContent = 'Default Homelab Copilot';
    }
  }

  try {
    const res = await fetch('/api/agents/list');
    const data = await res.json();
    if (!data.ok) return;
    cachedAgentsList = data;

    if (data.workspace_agent && wsSection && wsCard) {
      wsSection.style.display = 'block';
      const wsa = data.workspace_agent;
      const isAct = State.activeAgent?.id === wsa.id;
      wsCard.innerHTML = `
        <div style="background:var(--term-bg); border:1px solid ${isAct ? 'var(--term-accent-gold)' : 'var(--term-border-dim)'}; border-radius:3px; padding:10px; display:flex; justify-content:space-between; align-items:flex-start;">
          <div>
            <div style="font-weight:bold; color:var(--term-text-bright); font-size:0.84rem;">
              ${wsa.icon || '📁'} ${escapeHtml(wsa.name)} <span style="font-size:0.72rem; color:var(--term-text-muted);">(${escapeHtml(wsa.role)})</span>
              ${isAct ? '<span style="font-size:0.7rem; color:var(--term-accent-gold); margin-left:6px;">[ACTIVE]</span>' : ''}
            </div>
            <div style="font-size:0.74rem; color:var(--term-text-muted); margin-top:3px; font-family:monospace;">
              ${escapeHtml(wsa.system_prompt ? wsa.system_prompt.slice(0, 160) + '...' : 'Bound to in-repository .stonesage/agent.json')}
            </div>
          </div>
          <button type="button" class="term-cmd-btn" onclick="selectAgent('${wsa.id}', 'workspace')" style="font-size:0.74rem;">
            ${isAct ? '✔ ACTIVE' : '[ACTIVATE]'}
          </button>
        </div>
      `;
    } else if (wsSection) {
      wsSection.style.display = 'none';
    }

    if (builtinGrid && Array.isArray(data.builtin_agents)) {
      builtinGrid.innerHTML = data.builtin_agents.map(ag => {
        const isAct = State.activeAgent?.id === ag.id;
        return `
          <div style="background:var(--term-bg); border:1px solid ${isAct ? 'var(--term-accent-gold)' : 'var(--term-border-dim)'}; border-radius:3px; padding:8px 10px; display:flex; justify-content:space-between; align-items:center; gap:8px;">
            <div style="flex:1; min-width:0;">
              <div style="font-weight:bold; color:var(--term-text-bright); font-size:0.82rem; display:flex; align-items:center; gap:6px;">
                <span>${ag.icon || '⚡'}</span>
                <span>${escapeHtml(ag.name)}</span>
                <span style="font-size:0.7rem; color:var(--term-text-muted);">• ${escapeHtml(ag.role)}</span>
                ${isAct ? '<span style="font-size:0.7rem; color:var(--term-accent-gold);">[ACTIVE]</span>' : ''}
              </div>
              <div style="font-size:0.74rem; color:var(--term-text-muted); margin-top:2px;">
                ${escapeHtml(ag.description)}
              </div>
            </div>
            <div style="display:flex; gap:6px;">
              <button type="button" class="term-cmd-btn" onclick="editAgentModal('${ag.id}')" style="font-size:0.74rem; color:var(--term-accent-gold);" title="Edit System Prompt & Directives">
                ✏️ EDIT
              </button>
              <button type="button" class="term-cmd-btn" onclick="selectAgent('${ag.id}', 'builtin')" style="font-size:0.74rem; white-space:nowrap;">
                ${isAct ? '✔ ACTIVE' : '[ACTIVATE]'}
              </button>
            </div>
          </div>
        `;
      }).join('');
    }

    if (customGrid) {
      if (Array.isArray(data.custom_agents) && data.custom_agents.length > 0) {
        customGrid.innerHTML = data.custom_agents.map(ag => {
          const isAct = State.activeAgent?.id === ag.id;
          return `
            <div style="background:var(--term-bg); border:1px solid ${isAct ? 'var(--term-accent-gold)' : 'var(--term-border-dim)'}; border-radius:3px; padding:8px 10px; display:flex; justify-content:space-between; align-items:center; gap:8px;">
              <div style="flex:1; min-width:0;">
                <div style="font-weight:bold; color:var(--term-text-bright); font-size:0.82rem; display:flex; align-items:center; gap:6px;">
                  <span>${ag.icon || '🤖'}</span>
                  <span>${escapeHtml(ag.name)}</span>
                  <span style="font-size:0.7rem; color:var(--term-text-muted);">• ${escapeHtml(ag.role)}</span>
                  ${isAct ? '<span style="font-size:0.7rem; color:var(--term-accent-gold);">[ACTIVE]</span>' : ''}
                </div>
                <div style="font-size:0.74rem; color:var(--term-text-muted); margin-top:2px; font-family:monospace;">
                  ${escapeHtml(ag.system_prompt ? ag.system_prompt.slice(0, 140) + '...' : '')}
                </div>
              </div>
              <div style="display:flex; gap:6px;">
                <button type="button" class="term-cmd-btn" onclick="editAgentModal('${ag.id}')" style="font-size:0.74rem; color:var(--term-accent-gold);" title="Edit System Prompt & Directives">
                  ✏️ EDIT
                </button>
                <button type="button" class="term-cmd-btn" onclick="selectAgent('${ag.id}', 'custom')" style="font-size:0.74rem;">
                  ${isAct ? '✔ ACTIVE' : '[ACTIVATE]'}
                </button>
                <button type="button" class="term-cmd-btn" onclick="deleteCustomAgent('${ag.id}')" style="font-size:0.74rem; color:var(--term-accent-red);">
                  ✕
                </button>
              </div>
            </div>
          `;
        }).join('');
      } else {
        customGrid.innerHTML = `<div style="font-size:0.74rem; color:var(--term-text-muted); font-style:italic;">No custom agents created yet. Click [+ CREATE CUSTOM AGENT] to define one.</div>`;
      }
    }
  } catch (err) {
    console.warn('Error loading agents list:', err);
  }
};

window.selectAgent = function(agentId, type) {
  if (!cachedAgentsList) return;
  let target = null;
  if (type === 'workspace') target = cachedAgentsList.workspace_agent;
  else if (type === 'builtin') target = cachedAgentsList.builtin_agents?.find(a => a.id === agentId);
  else if (type === 'custom') target = cachedAgentsList.custom_agents?.find(a => a.id === agentId);

  if (target) {
    State.activeAgent = target;
    applyActiveAgentBadges(target);
    loadAgentDirectory();
  }
};

window.clearActiveAgent = function() {
  State.activeAgent = null;
  applyActiveAgentBadges(null);
  loadAgentDirectory();
};

function applyActiveAgentBadges(agent) {
  const chatBadge = document.getElementById('chat-agent-badge');
  const wsBadge = document.getElementById('workstation-agent-badge');
  if (agent) {
    const label = `🤖 [AGENT: ${agent.name}]`;
    if (chatBadge) {
      chatBadge.textContent = label;
      chatBadge.style.color = 'var(--term-accent-gold)';
      chatBadge.style.borderColor = 'var(--term-accent-gold)';
      chatBadge.title = `${agent.name} • ${agent.role} (Click to switch Agent)`;
    }
    if (wsBadge) {
      wsBadge.textContent = label;
      wsBadge.style.color = 'var(--term-accent-gold)';
      wsBadge.title = `${agent.name} • ${agent.role} (Click to switch Agent)`;
    }
  } else {
    const label = `🤖 [AGENT: (none)]`;
    if (chatBadge) {
      chatBadge.textContent = label;
      chatBadge.style.color = 'var(--term-text-muted)';
      chatBadge.style.borderColor = 'var(--term-border-dim)';
      chatBadge.title = 'No agent active. Click to select an Agent persona.';
    }
    if (wsBadge) {
      wsBadge.textContent = label;
      wsBadge.style.color = 'var(--term-text-muted)';
      wsBadge.title = 'No agent active. Click to select an Agent persona.';
    }
  }
}

window.submitCreateAgent = async function() {
  const name = document.getElementById('new-agent-name')?.value.trim();
  const role = document.getElementById('new-agent-role')?.value.trim();
  const prompt = document.getElementById('new-agent-prompt')?.value.trim();

  if (!name) {
    alert('Agent name is required.');
    return;
  }

  try {
    const res = await fetch('/api/agents/create', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, role, system_prompt: prompt, icon: '🤖' })
    });
    const data = await res.json();
    if (data.ok) {
      toggleCreateAgentForm();
      if (document.getElementById('new-agent-name')) document.getElementById('new-agent-name').value = '';
      if (document.getElementById('new-agent-role')) document.getElementById('new-agent-role').value = '';
      if (document.getElementById('new-agent-prompt')) document.getElementById('new-agent-prompt').value = '';
      await loadAgentDirectory();
      if (data.agent) selectAgent(data.agent.id, 'custom');
    } else {
      alert(`Failed to save agent: ${data.error}`);
    }
  } catch (err) {
    alert(`Error creating agent: ${err.message}`);
  }
};

window.deleteCustomAgent = async function(agentId) {
  if (!confirm(`Delete custom agent?`)) return;
  try {
    const res = await fetch('/api/agents/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: agentId })
    });
    const data = await res.json();
    if (data.ok) {
      if (State.activeAgent?.id === agentId) {
        clearActiveAgent();
      }
      await loadAgentDirectory();
    } else {
      alert(`Error deleting agent: ${data.error}`);
    }
  } catch (err) {
    alert(`Error deleting agent: ${err.message}`);
  }
};

window.editAgentModal = function(agentId) {
  if (!cachedAgentsList) return;
  const allAgents = [
    ...(cachedAgentsList.builtin_agents || []),
    ...(cachedAgentsList.custom_agents || [])
  ];
  if (cachedAgentsList.workspace_agent) allAgents.push(cachedAgentsList.workspace_agent);

  const ag = allAgents.find(a => a.id === agentId);
  if (!ag) return;

  const form = document.getElementById('edit-agent-form');
  if (!form) return;

  // Hide create form if open
  const createForm = document.getElementById('create-agent-form');
  if (createForm) createForm.style.display = 'none';

  document.getElementById('edit-agent-id').value = ag.id;
  const badge = document.getElementById('edit-agent-id-badge');
  if (badge) badge.textContent = `[ID: ${ag.id}]`;
  document.getElementById('edit-agent-name').value = ag.name || '';
  document.getElementById('edit-agent-role').value = ag.role || '';
  document.getElementById('edit-agent-icon').value = ag.icon || '🤖';
  document.getElementById('edit-agent-prompt').value = ag.system_prompt || '';

  const modelSelect = document.getElementById('edit-agent-model');
  if (modelSelect) {
    modelSelect.value = ag.preferred_model || 'coordinator';
  }

  form.style.display = 'block';
  form.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
};

window.closeEditAgentForm = function() {
  const form = document.getElementById('edit-agent-form');
  if (form) form.style.display = 'none';
};

window.submitUpdateAgent = async function() {
  const id = document.getElementById('edit-agent-id')?.value;
  const name = document.getElementById('edit-agent-name')?.value.trim();
  const role = document.getElementById('edit-agent-role')?.value.trim();
  const icon = document.getElementById('edit-agent-icon')?.value.trim();
  const prompt = document.getElementById('edit-agent-prompt')?.value.trim();
  const preferredModel = document.getElementById('edit-agent-model')?.value || 'coordinator';

  if (!id || !name) {
    alert('Agent Name is required.');
    return;
  }

  try {
    const res = await fetch('/api/agents/update', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        id: id,
        name: name,
        role: role,
        icon: icon,
        system_prompt: prompt,
        preferred_model: preferredModel
      })
    });
    const data = await res.json();
    if (data.ok) {
      closeEditAgentForm();
      await loadAgentDirectory();
      if (State.activeAgent && State.activeAgent.id === id) {
        State.activeAgent = data.agent;
        applyActiveAgentBadges(data.agent);
      }
      alert(`✔ Agent '${name}' updated successfully!`);
    } else {
      alert(`Failed to update agent: ${data.error}`);
    }
  } catch (err) {
    alert(`Error updating agent: ${err.message}`);
  }
};

window.switchActiveNode = async function(instanceId) {
  try {
    const res = await fetch('/api/harness/instances/select', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: instanceId })
    });
    const data = await res.json();
    if (data.ok) {
      State.activeNode = instanceId;
      loadFleetInstances(true);
    }
  } catch (e) {
    alert(`Failed to switch active node: ${e.message}`);
  }
};

window.openModelModal = function() {
  const m = document.getElementById('modal-model-picker');
  if (m) m.classList.add('open');
};

window.closeModelModal = function() {
  const m = document.getElementById('modal-model-picker');
  if (m) m.classList.remove('open');
};

window.openClusterModal = function() {
  const m = document.getElementById('modal-cluster-health');
  if (m) m.classList.add('open');
};

window.closeClusterModal = function() {
  const m = document.getElementById('modal-cluster-health');
  if (m) m.classList.remove('open');
};

window.openSettingsDrawer = function() {
  const m = document.getElementById('modal-settings-drawer');
  if (m) m.classList.add('open');
};

window.closeSettingsDrawer = function() {
  const m = document.getElementById('modal-settings-drawer');
  if (m) m.classList.remove('open');
};

window.selectModel = function(modelId) {
  State.activeModel = modelId;
  const sel = document.getElementById('topbar-model-select');
  if (sel) sel.value = modelId;
  const mobText = document.getElementById('mobile-model-text');
  if (mobText) {
    mobText.textContent = modelId === 'antigravity' ? '🌌 AGY' : (modelId === 'gemini' ? '⚡ Frontier' : `🤖 ${engineShort(modelId)}`);
  }
  closeModelModal();
  if (cachedClusterHealth) {
    updateModelSelectors(cachedClusterHealth, sel, mobText, cachedModelsInfo);
  }
};

window.loadFleetInstances = loadFleetInstances;

window.toggleHaSwitch = async function(entityId) {
  try {
    const btn = document.querySelector(`[data-switch-id="${entityId}"]`);
    if (btn) btn.textContent = '...';
    await fetch('/api/ha/service', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        domain: 'switch',
        service: 'toggle',
        service_data: { entity_id: entityId }
      })
    });
    setTimeout(loadHaData, 500);
  } catch (err) {
    console.warn('Error toggling switch:', err);
  }
};

window.adjustNestTemp = async function(delta) {
  try {
    const dashRes = await fetch('/api/ha/dashboard');
    const dash = await dashRes.json();
    const climate = (dash.climate || [])[0];
    if (!climate) return;
    const currentTarget = (climate.attributes && climate.attributes.temperature) || 69;
    const newTarget = Math.min(85, Math.max(60, Math.round(currentTarget + delta)));
    await fetch('/api/ha/service', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        domain: 'climate',
        service: 'set_temperature',
        service_data: { entity_id: climate.entity_id, temperature: newTarget }
      })
    });
    setTimeout(loadHaData, 600);
  } catch (err) {
    console.warn('Error adjusting Nest temp:', err);
  }
};

export async function loadHaData() {
  const container = document.getElementById('view-ha');
  if (!container) return;

  try {
    const [haStatus, haDash] = await Promise.all([
      fetch('/api/ha/status').then(r => r.json()).catch(() => ({ online: false })),
      fetch('/api/ha/dashboard').then(r => r.json()).catch(() => ({ climate: [], switches: [], lights: [], sensors: [] }))
    ]);

    const connBadge = document.getElementById('ha-connection-badge');
    if (connBadge) {
      connBadge.className = `status-badge ${haStatus.online ? 'online' : 'offline'}`;
      const haHost = haStatus.url ? haStatus.url.replace(/^https?:\/\//, '') : 'Home Assistant';
      connBadge.textContent = haStatus.online ? `HA: ${haHost} (ONLINE ${haStatus.latency_ms ?? '?'}ms)` : `HA: ${haHost} (OFFLINE)`;
    }

    const climate = (haDash.climate || [])[0] || {};
    const climateAttrs = climate.attributes || {};
    const currentTemp = climateAttrs.current_temperature !== undefined ? Math.round(climateAttrs.current_temperature) : 72;
    const targetTemp = climateAttrs.temperature !== undefined ? Math.round(climateAttrs.temperature) : 71;
    const hvacMode = (climate.state || 'cool').toUpperCase();

    const curElem = document.getElementById('thermostat-current-temp');
    const tarElem = document.getElementById('thermostat-target-temp');
    const stateElem = document.getElementById('thermostat-hvac-state');
    const humElem = document.getElementById('thermostat-humidity');
    const eidElem = document.getElementById('thermostat-entity-id');
    const slider = document.getElementById('temp-slider');

    if (curElem) curElem.textContent = currentTemp;
    if (tarElem) tarElem.textContent = targetTemp;
    if (stateElem) stateElem.textContent = `[${hvacMode} ACTIVE]`;
    if (humElem && climateAttrs.current_humidity) humElem.textContent = `${climateAttrs.current_humidity}%`;
    if (eidElem && climate.entity_id) eidElem.textContent = climate.entity_id;
    if (slider) slider.value = targetTemp;

    // Attach temp button listeners if not already bound
    const downBtn = document.getElementById('temp-down-btn');
    const upBtn = document.getElementById('temp-up-btn');
    if (downBtn && !downBtn._bound) {
      downBtn._bound = true;
      downBtn.addEventListener('click', () => window.adjustNestTemp(-1));
    }
    if (upBtn && !upBtn._bound) {
      upBtn._bound = true;
      upBtn.addEventListener('click', () => window.adjustNestTemp(1));
    }
    if (slider && !slider._bound) {
      slider._bound = true;
      slider.addEventListener('change', async () => {
        const val = parseInt(slider.value, 10);
        if (climate.entity_id) {
          await fetch('/api/ha/service', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              domain: 'climate',
              service: 'set_temperature',
              service_data: { entity_id: climate.entity_id, temperature: val }
            })
          });
          setTimeout(loadHaData, 600);
        }
      });
    }

    // Attach HVAC mode buttons
    document.querySelectorAll('.thermostat-modes-row button').forEach(b => {
      const mode = b.dataset.hvacMode;
      b.classList.toggle('active', mode === (climate.state || 'cool'));
      if (!b._bound) {
        b._bound = true;
        b.addEventListener('click', async () => {
          if (climate.entity_id && mode) {
            await fetch('/api/ha/service', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                domain: 'climate',
                service: 'set_hvac_mode',
                service_data: { entity_id: climate.entity_id, hvac_mode: mode }
              })
            });
            setTimeout(loadHaData, 600);
          }
        });
      }
    });

    // Populate entities
    State.ha.all = [
      ...(haDash.switches || []),
      ...(haDash.lights || []),
      ...(haDash.climate || [])
    ];
    State.ha.customizations = haDash.customizations || {};
    renderHaFilteredDevices();

  } catch (err) {
    console.warn('Error loading HA data:', err);
  }
}

function isNoisyEntity(entity_id, name) {
  const str = `${entity_id} ${name || ''}`.toLowerCase();
  const noisePatterns = [
    'trigger_alarm_on_', 'alarm_tamper', 'tamper_detection', 'tamper_', '_tamper',
    'auto_update', 'firmware_update', 'status_led', 'indicator_led', '_led',
    'night_vision', 'cloud_storage', 'siren', 'doorbell_call', 'privacy_mode',
    'privacy_zone', '_privacy', 'motion_detection_switch', 'smart_track_',
    'microphone_mute', 'auto_off_enabled', 'communications', 'do_not_disturb', '_flip'
  ];
  return noisePatterns.some(p => str.includes(p));
}

function cleanEntityDisplayName(item) {
  const custom = (State.ha && State.ha.customizations && State.ha.customizations[item.entity_id]) || {};
  if (custom.custom_name) return custom.custom_name;
  if (item.name && item.name !== 'None' && item.name.trim()) return item.name.trim();
  const eid = item.entity_id || '';
  const raw = eid.includes('.') ? eid.split('.')[1] : eid;
  let text = raw.replace(/_/g, ' ');
  let s = text.trim().replace(/\w\S*/g, (w) => w.charAt(0).toUpperCase() + w.substr(1).toLowerCase());
  return s.replace(/\bTv\b/g, 'TV').replace(/\bLed\b/g, 'LED');
}

export function filterHaDevices(category) {
  State.ha.filter = category;
  ['curated', 'lights', 'switches', 'cameras', 'all'].forEach(cat => {
    const btn = document.getElementById(`ha-filter-${cat}`);
    if (btn) btn.classList.toggle('active', cat === category);
  });
  renderHaFilteredDevices();
}
window.filterHaDevices = filterHaDevices;

export function onHaSearchInput() {
  const input = document.getElementById('ha-search-input');
  State.ha.searchQuery = input ? input.value.trim().toLowerCase() : '';
  renderHaFilteredDevices();
}
window.onHaSearchInput = onHaSearchInput;
window.fetchHaDashboard = loadHaData;

function renderHaFilteredDevices() {
  const table = document.getElementById('ha-entities-table');
  const count = document.getElementById('ha-devices-count');
  if (!table) return;

  let list = [...(State.ha.all || [])];
  const query = State.ha.searchQuery;
  const filter = State.ha.filter || 'curated';

  if (filter === 'lights') {
    list = list.filter(item => item.entity_id.startsWith('light.'));
  } else if (filter === 'switches') {
    list = list.filter(item => item.entity_id.startsWith('switch.') && !isNoisyEntity(item.entity_id, item.name));
  } else if (filter === 'cameras') {
    list = list.filter(item => {
      const s = `${item.entity_id} ${item.name || ''}`.toLowerCase();
      return s.includes('camera') || s.includes('doorbell') || s.includes('floodlight') || isNoisyEntity(item.entity_id, item.name);
    });
  } else if (filter === 'curated') {
    list = list.filter(item => {
      const custom = (State.ha && State.ha.customizations && State.ha.customizations[item.entity_id]) || {};
      if (custom.hidden) return false;
      return !isNoisyEntity(item.entity_id, item.name);
    });
  }

  if (query) {
    list = list.filter(item => {
      const s = `${item.entity_id} ${item.name || ''}`.toLowerCase();
      return s.includes(query);
    });
  }

  if (count) count.textContent = `${list.length} entities displayed (${State.ha.all?.length || 0} total)`;

  if (list.length === 0) {
    table.innerHTML = '<div style="color:var(--term-text-muted); padding:0.75rem;">No matching smart home devices found.</div>';
    return;
  }

  table.innerHTML = `
    <table style="width: 100%; border-collapse: collapse; font-size: 0.78rem;">
      <thead>
        <tr style="border-bottom: 1px solid var(--term-border); color: var(--term-text-bright); text-align: left;">
          <th style="padding: 4px;">ENTITY</th>
          <th style="padding: 4px;">STATE</th>
          <th style="padding: 4px; text-align: right;">ACTION</th>
        </tr>
      </thead>
      <tbody>
        ${list.map(item => {
          const isLight = item.entity_id.startsWith('light.');
          const isSwitch = item.entity_id.startsWith('switch.');
          const cleanName = cleanEntityDisplayName(item);
          return `
            <tr style="border-bottom: 1px dashed var(--term-border-dim);">
              <td style="padding: 4px;">
                <span style="margin-right: 4px;">${isLight ? '💡' : (isSwitch ? '🔌' : '⚙️')}</span>
                <span style="font-weight: 700; color: var(--term-text-bright);">${escapeHtml(cleanName)}</span>
                <div style="font-size: 0.7rem; color: var(--term-text-dim);">${escapeHtml(item.entity_id)}</div>
              </td>
              <td style="padding: 4px; font-weight: 700; color: ${item.state === 'on' ? 'var(--term-accent-green)' : 'var(--term-text-muted)'};">
                [${(item.state || 'off').toUpperCase()}]
              </td>
              <td style="padding: 4px; text-align: right;">
                ${(isLight || isSwitch) ? `
                  <button type="button" class="term-cmd-btn" onclick="toggleHaSwitch('${escapeHtml(item.entity_id)}')" style="font-size:0.72rem; padding:2px 8px; border-color:${item.state === 'on' ? 'var(--term-accent-green)' : 'var(--term-border-dim)'}; color:${item.state === 'on' ? 'var(--term-accent-green)' : 'var(--term-text-muted)'};">
                    ${item.state === 'on' ? 'TOGGLE OFF' : 'TOGGLE ON'}
                  </button>
                ` : ''}
              </td>
            </tr>
          `;
        }).join('')}
      </tbody>
    </table>
  `;
}

export async function loadTrainerWatchdogData() {
  const container = document.getElementById('engine-layer-trainer') || document.getElementById('view-trainer');
  if (!container) return;

  try {
    const [watchdogRes, trainerRes, datasetRes] = await Promise.all([
      fetch('/api/watchdog/status').then(r => r.json()).catch(() => ({})),
      fetch('/api/trainer/status').then(r => r.json()).catch(() => ({})),
      fetch('/api/dataset/status').then(r => r.json()).catch(() => ({}))
    ]);

    const vram = trainerRes.vram_specs || {};
    const golden = trainerRes.golden_invariants || { passed_count: 0, total_count: 0, details: [] };
    const ds = datasetRes.status || {};
    const dsManifest = ds.last_manifest || {};

    container.innerHTML = `
      <div class="harness-container">
        <div class="harness-card-title">
          <span>🎯 PVE HARDWARE WATCHDOG &amp; VRAM TRAINING PIPELINE</span>
          <span class="status-badge ${watchdogRes.armed !== false ? 'online' : 'offline'}">
            ${watchdogRes.armed !== false ? '🟢 WATCHDOG ARMED' : '🔴 DISARMED'}
          </span>
        </div>

        <div class="harness-grid" style="display:grid; grid-template-columns:repeat(auto-fit, minmax(280px, 1fr)); gap:12px; margin-top:12px;">
          <!-- Watchdog Telemetry -->
          <div class="harness-card">
            <div class="harness-card-title">
              <span>🛡️ HARDWARE FENCING WATCHDOG</span>
              <span class="status-badge online">${watchdogRes.intercept_count || 0} CYCLES</span>
            </div>
            <div style="font-size:0.8rem; color:var(--term-text); line-height:1.6; margin-top:6px;">
              <div>• <strong>Status:</strong> ${watchdogRes.armed ? 'armed' : 'disarmed'}</div>
              <div>• <strong>Last power cycle:</strong> ${watchdogRes.last_intercept ? escapeHtml(String(watchdogRes.last_intercept)) : 'never'}</div>
            </div>
          </div>

          <!-- GPU VRAM from the live hardware probe -->
          <div class="harness-card">
            <div class="harness-card-title">
              <span>⚡ GPU VRAM</span>
              <span style="font-size:0.75rem; color:var(--term-accent-gold);">${((Profile.data && Profile.data.gpus) || []).reduce((t, g) => t + g.vram_total_gb, 0).toFixed(1)} GB VRAM</span>
            </div>
            <div style="font-size:0.8rem; color:var(--term-text); line-height:1.6; margin-top:6px;">
              ${((Profile.data && Profile.data.gpus) || []).map(g => `
                <div>• <strong>${escapeHtml(g.name)}:</strong> ${g.vram_used_gb} / ${g.vram_total_gb} GB used
                  ${g.engines && g.engines.length ? `(${g.engines.map(r => escapeHtml(engine(r) ? engine(r).model || r : r)).join(', ')})` : ''}</div>`).join('')
                || '<div style="color:var(--term-text-muted);">Hardware probe unavailable</div>'}
            </div>
          </div>
        </div>

        <!-- Golden Invariants Verification -->
        <div class="harness-card" style="margin-top:12px;">
          <div class="harness-card-title">
            <span>🧪 GOLDEN INVARIANTS EVALUATION SUITE</span>
            <span class="status-badge ${golden.pass_rate >= 0.7 ? 'online' : 'offline'}">
              ${golden.passed_count}/${golden.total_count} PASSED (${Math.round((golden.pass_rate || 0) * 100)}%)
            </span>
          </div>
          <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(280px, 1fr)); gap:8px; margin-top:10px;">
            ${(golden.details || []).map(inv => `
              <div style="padding:8px 10px; background:var(--term-bg); border:1px solid ${inv.passed ? 'var(--term-border-dim)' : 'rgba(255,100,100,0.4)'}; border-radius:4px; font-size:0.76rem;">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
                  <strong style="color:var(--term-text-bright);">${escapeHtml(inv.id)}</strong>
                  <span class="status-badge ${inv.passed ? 'online' : 'offline'}">${inv.passed ? 'PASSED' : 'FAILED'}</span>
                </div>
                <div style="color:var(--term-text-muted); font-size:0.72rem; margin-bottom:4px;">${escapeHtml(inv.reason)}</div>
                ${inv.response_snippet ? `<div style="color:var(--term-text); font-family:monospace; background:rgba(0,0,0,0.3); padding:3px 6px; border-radius:2px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">Snippet: ${escapeHtml(inv.response_snippet)}</div>` : ''}
              </div>
            `).join('')}
          </div>
        </div>

        <!-- Curated Training Dataset Pipeline -->
        <div class="harness-card" style="margin-top:12px;">
          <div class="harness-card-title">
            <span>📚 HOMELAB CURATED TRAINING DATASET</span>
            <span class="status-badge online">${ds.accepted || 15}/${ds.total || 15} ACCEPTED SAMPLES</span>
          </div>
          <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px; margin-top:8px;">
            <div style="font-size:0.8rem; color:var(--term-text); line-height:1.6;">
              <div>• <strong>Curation Ratio:</strong> 70% Real Transcripts &amp; Code Diffs / 30% Audited Ruminations</div>
              <div>• <strong>Rejection Rate:</strong> ${dsManifest.rejection_rate_pct || 0}% (Zero unreviewed synthetic hallucinations)</div>
              <div>• <strong>Curated Artifacts:</strong> ShareGPT format &amp; Alpaca instruction formats</div>
            </div>
            <div style="display:flex; gap:8px;">
              <a href="/api/dataset/download?type=sharegpt" download class="term-cmd-btn" style="padding:6px 12px; text-decoration:none; display:inline-flex; align-items:center;">
                ⬇ DOWNLOAD SHAREGPT
              </a>
              <a href="/api/dataset/download?type=alpaca" download class="term-cmd-btn" style="padding:6px 12px; text-decoration:none; display:inline-flex; align-items:center;">
                ⬇ DOWNLOAD ALPACA
              </a>
            </div>
          </div>
        </div>
      </div>
    `;
  } catch (err) {
    console.warn('Error loading Watchdog/Trainer data:', err);
  }
}

/**
 * Valkey A-MEM Atomic Memory Cards with core-memory tags & full 189+ card browser
 */
let cachedAmemCards = [];
let activeAmemCategory = 'all';

export async function loadAmemCards() {
  const container = document.getElementById('memory-cards-container');
  const totalBadge = document.getElementById('amem-total-badge');
  const avgBadge = document.getElementById('amem-avg-badge');
  if (!container) return;

  try {
    const res = await fetch(`/api/memory/amem?category=${encodeURIComponent(activeAmemCategory)}`);
    const data = await res.json();
    if (!data.ok || !Array.isArray(data.cards)) return;

    cachedAmemCards = data.cards;

    if (totalBadge) totalBadge.textContent = `🟢 ${data.total_cards || data.cards.length} CARDS`;
    if (avgBadge) avgBadge.textContent = `(${data.average_tokens || 20} tok/card avg)`;

    const countAll = document.getElementById('cat-all-count');
    const countAgent = document.getElementById('cat-agent-count');
    if (countAll) countAll.textContent = data.total_cards || data.cards.length;
    if (countAgent && data.categories) countAgent.textContent = data.categories['agent_contract'] || 0;

    renderAmemCardsList(cachedAmemCards);
  } catch (e) {
    console.warn('Error loading AMEM cards:', e);
  }
}

function renderAmemCardsList(cards) {
  const container = document.getElementById('memory-cards-container');
  if (!container) return;

  if (cards.length === 0) {
    container.innerHTML = '<div style="color:var(--term-text-muted); padding:1rem; grid-column:1/-1;">No matching memory cards found in Valkey RAM.</div>';
    return;
  }

  container.innerHTML = cards.map(c => {
    const cardId = c.id || '';
    const safeId = escapeHtml(cardId);
    const atomText = c.atom || c.text || '';
    const cat = c.category || 'general';
    const tok = c.token_count || Math.max(1, Math.round(atomText.split(/\s+/).length * 1.25));
    const isCore = Boolean(c.is_core_memory);

    return `
      <div class="amem-card ${isCore ? 'core-memory' : ''}" style="background:var(--term-surface); border:1px solid var(--term-border-dim); border-radius:4px; padding:10px; display:flex; flex-direction:column; justify-content:space-between; cursor:pointer; transition:border-color 0.15s;" onclick="openAmemEditModal('${encodeURIComponent(cardId)}')">
        <div>
          <div class="card-header" style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:6px; gap:6px;">
            <span style="font-weight:bold; font-size:0.75rem; color:var(--term-accent-gold); word-break:break-all;">🧠 ${safeId}</span>
            <span class="status-badge" style="font-size:0.65rem; padding:1px 4px;">${escapeHtml(cat)}</span>
          </div>
          <div style="font-size:0.82rem; color:var(--term-text); line-height:1.5; margin-bottom:8px; display:-webkit-box; -webkit-line-clamp:4; -webkit-box-orient:vertical; overflow:hidden;">
            ${escapeHtml(atomText)}
          </div>
        </div>
        <div style="border-top:1px dashed var(--term-border-dim); padding-top:6px; margin-top:6px; display:flex; justify-content:space-between; align-items:center;">
          <div style="font-size:0.68rem; color:var(--term-text-muted);">
            <span>${tok} tokens</span>
            ${isCore ? '<span style="color:var(--term-accent-gold); margin-left:4px;">★ CORE</span>' : ''}
          </div>
          <div style="display:flex; gap:4px;" onclick="event.stopPropagation();">
            <button type="button" class="term-cmd-btn" onclick="openAmemEditModal('${encodeURIComponent(cardId)}')" style="font-size:0.68rem; padding:1px 6px;">[EDIT]</button>
            <button type="button" class="term-cmd-btn" onclick="quickDeleteAmemCard('${encodeURIComponent(cardId)}')" style="font-size:0.68rem; padding:1px 6px; color:var(--term-accent-red);">[DEL]</button>
          </div>
        </div>
      </div>
    `;
  }).join('');
}

window.filterAmemCategory = function(cat) {
  activeAmemCategory = cat;
  document.querySelectorAll('#amem-category-chips .preset-btn').forEach(btn => btn.classList.remove('active'));
  const activeBtn = document.getElementById(cat === 'all' ? 'amem-cat-all' : (cat === 'agent_contract' ? 'amem-cat-agent' : (cat === 'systems_architecture' ? 'amem-cat-arch' : 'amem-cat-hw')));
  if (activeBtn) activeBtn.classList.add('active');
  loadAmemCards();
};

window.searchAmemCards = function() {
  const input = document.getElementById('amem-search-input');
  const q = (input ? input.value : '').trim().toLowerCase();
  if (!q) {
    renderAmemCardsList(cachedAmemCards);
    return;
  }
  const filtered = cachedAmemCards.filter(c => 
    (c.atom || '').toLowerCase().includes(q) ||
    (c.id || '').toLowerCase().includes(q) ||
    (c.category || '').toLowerCase().includes(q) ||
    (c.keywords || []).some(k => k.toLowerCase().includes(q))
  );
  renderAmemCardsList(filtered);
};

window.openNewAmemModal = function() {
  const modal = document.getElementById('modal-amem-card');
  if (!modal) return;
  document.getElementById('amem-modal-title').textContent = '🧠 CREATE NEW ATOMIC CARD';
  document.getElementById('modal-amem-id').value = '';
  document.getElementById('modal-amem-id').disabled = false;
  document.getElementById('modal-amem-category').value = 'general';
  document.getElementById('modal-amem-atom').value = '';
  document.getElementById('modal-amem-keywords').value = '';
  document.getElementById('modal-amem-delete-btn').style.display = 'none';
  window.updateAmemModalTokenCount();
  modal.style.display = 'flex';
};

window.openAmemEditModal = function(encCardId) {
  const cardId = decodeURIComponent(encCardId);
  const card = cachedAmemCards.find(c => c.id === cardId);
  if (!card) return;
  const modal = document.getElementById('modal-amem-card');
  if (!modal) return;
  document.getElementById('amem-modal-title').textContent = `🧠 EDIT ATOMIC CARD: ${cardId}`;
  document.getElementById('modal-amem-id').value = card.id || '';
  document.getElementById('modal-amem-id').disabled = true;
  document.getElementById('modal-amem-category').value = card.category || 'general';
  document.getElementById('modal-amem-atom').value = card.atom || card.text || '';
  document.getElementById('modal-amem-keywords').value = (card.keywords || []).join(', ');
  document.getElementById('modal-amem-delete-btn').style.display = 'block';
  window.updateAmemModalTokenCount();
  modal.style.display = 'flex';
};

window.closeAmemModal = function() {
  const modal = document.getElementById('modal-amem-card');
  if (modal) modal.style.display = 'none';
};

window.updateAmemModalTokenCount = function() {
  const text = document.getElementById('modal-amem-atom')?.value || '';
  const tokens = Math.max(0, Math.round(text.trim().split(/\s+/).filter(Boolean).length * 1.25));
  const el = document.getElementById('modal-amem-tok-count');
  if (el) {
    el.textContent = tokens;
    el.style.color = tokens > 35 ? 'var(--term-accent-red)' : 'var(--term-accent-gold)';
  }
};

window.saveActiveAmemCard = async function() {
  const id = document.getElementById('modal-amem-id')?.value.trim();
  const atom = document.getElementById('modal-amem-atom')?.value.trim();
  const category = document.getElementById('modal-amem-category')?.value;
  const kwStr = document.getElementById('modal-amem-keywords')?.value;
  const keywords = (kwStr || '').split(',').map(s => s.trim()).filter(Boolean);

  if (!id || !atom) {
    alert('Please provide both a Card ID and Atom content.');
    return;
  }

  try {
    const res = await fetch('/api/memory/amem/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id, atom, category, keywords })
    });
    const data = await res.json();
    if (data.ok) {
      window.closeAmemModal();
      loadAmemCards();
    } else {
      alert('Error saving card: ' + (data.error || 'Unknown error'));
    }
  } catch (e) {
    alert('Failed to save atomic memory card: ' + e);
  }
};

window.deleteActiveAmemCard = async function() {
  const id = document.getElementById('modal-amem-id')?.value.trim();
  if (!id) return;
  if (!confirm(`Are you sure you want to delete atomic card '${id}' from Valkey RAM?`)) return;

  try {
    const res = await fetch('/api/memory/amem/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id })
    });
    const data = await res.json();
    if (data.ok) {
      window.closeAmemModal();
      loadAmemCards();
    }
  } catch (e) {
    alert('Failed to delete card: ' + e);
  }
};

window.quickDeleteAmemCard = async function(encCardId) {
  const id = decodeURIComponent(encCardId);
  if (!confirm(`Delete atomic card '${id}' from Valkey RAM?`)) return;
  try {
    const res = await fetch('/api/memory/amem/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id })
    });
    const data = await res.json();
    if (data.ok) loadAmemCards();
  } catch (e) {
    alert('Delete failed: ' + e);
  }
};

/**
 * Obsidian Full 331-Note Vault Explorer & Reader
 */
let cachedObsidianNotes = [];
let activeObsidianNote = null;
let obsidianRenderMode = 'raw';

export async function loadObsidianNotes(forceRefresh = false) {
  const list = document.getElementById('obsidian-notes-list');
  const countBadge = document.getElementById('obsidian-notes-count-badge');
  if (!list) return;

  const dbSelect = document.getElementById('obsidian-database-select');
  const activeDb = dbSelect ? dbSelect.value : 'ai_obsidian';

  try {
    const res = await fetch(`/api/obsidian/notes?database=${encodeURIComponent(activeDb)}`);
    const data = await res.json();
    if (!data.ok || !Array.isArray(data.notes)) {
      list.innerHTML = `<div style="color:var(--term-accent-red); padding:8px;">Failed loading vault: ${data.error || 'Server error'}</div>`;
      return;
    }

    cachedObsidianNotes = data.notes;
    if (countBadge) countBadge.textContent = `🟢 ${data.total_count || data.notes.length} NOTES (${activeDb})`;

    renderObsidianNotesList(cachedObsidianNotes);

    if (cachedObsidianNotes.length > 0) {
      if (!activeObsidianNote || !cachedObsidianNotes.some(n => n.path === activeObsidianNote)) {
        window.openObsidianNote(encodeURIComponent(cachedObsidianNotes[0].path));
      }
    } else {
      activeObsidianNote = null;
      const editor = document.getElementById('obsidian-note-editor');
      const preview = document.getElementById('obsidian-note-preview');
      const pathLabel = document.getElementById('obsidian-active-note-path');
      if (editor) editor.value = '';
      if (preview) preview.innerHTML = '<em style="color:var(--term-text-muted);">No notes in this vault. Use [📥 QUICK CAPTURE] to ingest URLs, thoughts, or photos.</em>';
      if (pathLabel) pathLabel.textContent = '(Empty vault)';
    }
  } catch (e) {
    console.warn('Error loading Obsidian notes:', e);
  }
}

window.onObsidianDatabaseChange = function() {
  activeObsidianNote = null;
  loadObsidianNotes(true);
};

function renderObsidianNotesList(notes) {
  const list = document.getElementById('obsidian-notes-list');
  if (!list) return;

  if (notes.length === 0) {
    list.innerHTML = '<div style="color:var(--term-text-muted); padding:8px;">No matching notes found.</div>';
    return;
  }

  list.innerHTML = notes.map(n => {
    const isAct = activeObsidianNote === n.path;
    const sizeKb = Math.round((n.size_bytes || 0) / 1024);
    return `
      <div class="obsidian-note-item ${isAct ? 'active' : ''}" style="padding:6px 8px; border:1px solid ${isAct ? 'var(--term-accent-blue)' : 'var(--term-border-dim)'}; border-radius:3px; background:${isAct ? 'var(--term-surface-dim)' : 'var(--term-bg)'}; cursor:pointer;" onclick="openObsidianNote('${encodeURIComponent(n.path)}')">
        <div style="font-weight:bold; color:${isAct ? 'var(--term-accent-gold)' : 'var(--term-text-bright)'}; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">
          📄 ${escapeHtml(n.name || n.path)}
        </div>
        <div style="font-size:0.7rem; color:var(--term-text-muted); display:flex; justify-content:space-between; margin-top:2px;">
          <span style="overflow:hidden; text-overflow:ellipsis; max-width:180px; white-space:nowrap;">${escapeHtml(n.path)}</span>
          <span>${sizeKb}KB</span>
        </div>
      </div>
    `;
  }).join('');
}

window.filterObsidianNotesList = function() {
  const input = document.getElementById('obsidian-search-input');
  const q = (input ? input.value : '').trim().toLowerCase();
  if (!q) {
    renderObsidianNotesList(cachedObsidianNotes);
    return;
  }
  const filtered = cachedObsidianNotes.filter(n =>
    (n.name || '').toLowerCase().includes(q) ||
    (n.path || '').toLowerCase().includes(q)
  );
  renderObsidianNotesList(filtered);
};

window.setObsidianMobileMode = function(mode) {
  const obsViewport = document.getElementById('obsidian-vault-viewport');
  const btnList = document.getElementById('obsidian-mobile-btn-list');
  const btnReader = document.getElementById('obsidian-mobile-btn-reader');
  if (obsViewport) {
    obsViewport.setAttribute('data-mobile-mode', mode);
  }
  if (btnList) btnList.classList.toggle('active', mode === 'list');
  if (btnReader) btnReader.classList.toggle('active', mode === 'reader');
};

window.openObsidianNote = async function(encPath) {
  const notePath = decodeURIComponent(encPath);
  activeObsidianNote = notePath;

  const dbSelect = document.getElementById('obsidian-database-select');
  const activeDb = dbSelect ? dbSelect.value : 'ai_obsidian';

  const pathLabel = document.getElementById('obsidian-active-note-path');
  const editor = document.getElementById('obsidian-note-editor');
  const preview = document.getElementById('obsidian-note-preview');
  const sourceBadge = document.getElementById('obsidian-note-source-badge');
  const mobileNoteTitle = document.getElementById('obsidian-mobile-note-title');

  if (pathLabel) pathLabel.textContent = notePath;
  if (mobileNoteTitle) mobileNoteTitle.textContent = notePath.split('/').pop() || notePath;

  renderObsidianNotesList(cachedObsidianNotes);

  if (window.innerWidth <= 768) {
    window.setObsidianMobileMode('reader');
  }

  try {
    const res = await fetch(`/api/obsidian/note?path=${encodeURIComponent(notePath)}&database=${encodeURIComponent(activeDb)}`);
    const data = await res.json();
    if (data.ok) {
      const content = data.content || '';
      if (editor) editor.value = content;
      if (preview) preview.innerHTML = renderMarkdownToHtml(content);
      if (sourceBadge) {
        sourceBadge.style.display = 'inline-block';
        if (data.source === 'local_vault' || data.source === 'user_vault') {
          sourceBadge.textContent = 'LOCAL VAULT';
          sourceBadge.className = 'status-badge online';
        } else if (data.source === 'qdrant_memory') {
          sourceBadge.textContent = `QDRANT (${data.chunks_count || 1} CHUNKS)`;
          sourceBadge.className = 'status-badge online';
        } else if (data.source === 'couchdb' || data.source === 'couchdb_plain') {
          sourceBadge.textContent = `COUCHDB (${data.database || activeDb})`;
          sourceBadge.className = 'status-badge online';
        } else {
          sourceBadge.textContent = 'COUCHDB E2EE';
          sourceBadge.className = 'status-badge';
        }
      }
    }
  } catch (e) {
    console.warn('Failed loading note:', e);
  }
};

window.saveObsidianNote = async function() {
  if (!activeObsidianNote) return;
  const dbSelect = document.getElementById('obsidian-database-select');
  const activeDb = dbSelect ? dbSelect.value : 'ai_obsidian';
  const editor = document.getElementById('obsidian-note-editor');
  const content = editor ? editor.value : '';
  const saveBtn = document.getElementById('obsidian-save-note-btn');

  if (saveBtn) saveBtn.textContent = 'SAVING...';

  try {
    const res = await fetch('/api/obsidian/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: activeObsidianNote, content: content, database: activeDb })
    });
    const data = await res.json();
    if (data.ok) {
      if (saveBtn) {
        saveBtn.textContent = '✓ SAVED!';
        setTimeout(() => { saveBtn.textContent = '[💾 SAVE NOTE]'; }, 1800);
      }
      const preview = document.getElementById('obsidian-note-preview');
      if (preview) preview.innerHTML = renderMarkdownToHtml(content);
    } else {
      alert('Error saving note: ' + (data.error || 'Unknown error'));
      if (saveBtn) saveBtn.textContent = '[💾 SAVE NOTE]';
    }
  } catch (e) {
    alert('Failed saving note: ' + e);
    if (saveBtn) saveBtn.textContent = '[💾 SAVE NOTE]';
  }
};

window.toggleObsidianRenderMode = function() {
  const editor = document.getElementById('obsidian-note-editor');
  const preview = document.getElementById('obsidian-note-preview');
  const btn = document.getElementById('obsidian-toggle-view-btn');

  if (obsidianRenderMode === 'raw') {
    obsidianRenderMode = 'preview';
    if (editor) editor.style.display = 'none';
    if (preview) {
      preview.style.display = 'block';
      preview.innerHTML = renderMarkdownToHtml(editor ? editor.value : '');
    }
    if (btn) btn.textContent = '[VIEW: PREVIEW]';
  } else {
    obsidianRenderMode = 'raw';
    if (preview) preview.style.display = 'none';
    if (editor) editor.style.display = 'block';
    if (btn) btn.textContent = '[VIEW: RAW]';
  }
};

function renderMarkdownToHtml(md) {
  if (!md) return '<em style="color:var(--term-text-muted);">Empty note.</em>';
  let html = escapeHtml(md);

  // Obsidian embedded images ![[filename.jpg]]
  html = html.replace(/!\[\[(.*?\.(?:png|jpe?g|gif|webp|svg))\]\]/gi, (match, p1) => {
    const cleanName = p1.trim();
    return `<div style="margin:10px 0; text-align:center;">
      <img src="/uploads/captures/${encodeURIComponent(cleanName)}" style="max-width:100%; max-height:420px; border:1px solid var(--term-border-dim); border-radius:4px; box-shadow:0 2px 8px rgba(0,0,0,0.5);" alt="${escapeHtml(cleanName)}" onerror="this.onerror=null; this.src='/uploads/${encodeURIComponent(cleanName)}';">
      <div style="font-size:0.72rem; color:var(--term-text-muted); margin-top:3px;">🖼️ ${escapeHtml(cleanName)}</div>
    </div>`;
  });

  // Standard markdown images ![alt](url)
  html = html.replace(/!\[(.*?)\]\((.*?)\)/gi, (match, alt, url) => {
    return `<div style="margin:10px 0; text-align:center;">
      <img src="${url}" style="max-width:100%; max-height:420px; border-radius:4px;" alt="${escapeHtml(alt)}">
      ${alt ? `<div style="font-size:0.72rem; color:var(--term-text-muted); margin-top:3px;">${escapeHtml(alt)}</div>` : ''}
    </div>`;
  });

  // Internal Obsidian links [[Note Title]]
  html = html.replace(/\[\[(.*?)\]\]/gi, (match, target) => {
    const cleanTarget = target.trim();
    return `<a href="#" onclick="event.preventDefault(); window.openObsidianNote(encodeURIComponent('${cleanTarget}'));" style="color:var(--term-accent-blue, #60a5fa); text-decoration:underline; font-weight:bold;">[[${escapeHtml(cleanTarget)}]]</a>`;
  });

  // Standard markdown links [text](url)
  html = html.replace(/\[(.*?)\]\((https?:\/\/[^\s\)]+)\)/gi, (match, txt, url) => {
    return `<a href="${url}" target="_blank" rel="noopener noreferrer" style="color:var(--term-accent-green, #22c55e); text-decoration:underline;">${escapeHtml(txt)} ↗</a>`;
  });

  html = html.replace(/^### (.*$)/gim, '<h3 style="color:var(--term-accent-gold); margin:10px 0 4px;">$1</h3>');
  html = html.replace(/^## (.*$)/gim, '<h2 style="color:var(--term-accent-gold); margin:12px 0 6px; border-bottom:1px solid var(--term-border-dim);">$1</h2>');
  html = html.replace(/^# (.*$)/gim, '<h1 style="color:var(--term-accent-gold); margin:14px 0 8px; border-bottom:2px solid var(--term-border-dim);">$1</h1>');
  html = html.replace(/\*\*(.*?)\*\*/gim, '<strong>$1</strong>');
  html = html.replace(/\*(.*?)\*/gim, '<em>$1</em>');
  html = html.replace(/`([^`]+)`/gim, '<code style="background:var(--term-surface); padding:2px 4px; border-radius:3px; font-family:monospace;">$1</code>');
  html = html.replace(/\n\n/gim, '<br><br>');
  return html;
}

/**
 * Quick Capture Modal Handlers
 */
let activeCapturePhotoData = null;

export function openQuickCaptureModal(prefill = {}) {
  const modal = document.getElementById('modal-quick-capture');
  if (!modal) return;

  const dbSelect = document.getElementById('capture-database-select');
  const obsDbSelect = document.getElementById('obsidian-database-select');
  if (dbSelect) {
    dbSelect.value = prefill.database || (obsDbSelect ? obsDbSelect.value : 'ai_obsidian');
  }

  const statusBox = document.getElementById('quick-capture-status');
  if (statusBox) {
    statusBox.style.display = 'none';
    statusBox.innerHTML = '';
  }

  if (prefill.url) {
    const urlInp = document.getElementById('capture-url-input');
    const titleInp = document.getElementById('capture-url-title');
    if (urlInp) urlInp.value = prefill.url;
    if (titleInp && prefill.title) titleInp.value = prefill.title;
    switchCaptureTab('url');
  } else if (prefill.text || prefill.content) {
    const contentInp = document.getElementById('capture-note-content');
    const titleInp = document.getElementById('capture-note-title');
    if (contentInp) contentInp.value = prefill.text || prefill.content;
    if (titleInp && prefill.title) titleInp.value = prefill.title;
    switchCaptureTab('note');
  } else {
    switchCaptureTab('url');
  }

  modal.classList.add('open');
  modal.style.display = 'flex';
}

export function closeQuickCaptureModal() {
  const modal = document.getElementById('modal-quick-capture');
  if (!modal) return;
  modal.classList.remove('open');
  modal.style.display = 'none';
}

export function switchCaptureTab(tab) {
  const tabs = ['url', 'note', 'photo'];
  tabs.forEach(t => {
    const btn = document.getElementById(`tab-btn-capture-${t}`);
    const content = document.getElementById(`capture-tab-content-${t}`);
    if (btn) btn.classList.toggle('active', t === tab);
    if (content) content.style.display = (t === tab) ? 'flex' : 'none';
  });
}

export function handleCapturePhotoSelect(event) {
  const file = event.target.files && event.target.files[0];
  if (!file) return;

  const reader = new FileReader();
  reader.onload = (e) => {
    activeCapturePhotoData = e.target.result;
    const previewBox = document.getElementById('capture-photo-preview-box');
    const previewImg = document.getElementById('capture-photo-img-preview');
    if (previewBox) previewBox.style.display = 'block';
    if (previewImg) previewImg.src = activeCapturePhotoData;
  };
  reader.readAsDataURL(file);
}

export async function submitQuickCapture(type) {
  const statusBox = document.getElementById('quick-capture-status');
  const dbSelect = document.getElementById('capture-database-select');
  const targetDb = dbSelect ? dbSelect.value : 'ai_obsidian';

  let payload = { type: type, database: targetDb };
  let submitBtn = null;

  if (type === 'url') {
    submitBtn = document.getElementById('capture-url-submit-btn');
    const url = (document.getElementById('capture-url-input')?.value || '').trim();
    const title = (document.getElementById('capture-url-title')?.value || '').trim();
    const tagsStr = (document.getElementById('capture-url-tags')?.value || '').trim();
    if (!url) {
      alert('Please enter a valid URL to ingest.');
      return;
    }
    payload.url = url;
    if (title) payload.title = title;
    if (tagsStr) payload.tags = tagsStr.split(',').map(t => t.trim()).filter(Boolean);
  } else if (type === 'note') {
    submitBtn = document.getElementById('capture-note-submit-btn');
    const title = (document.getElementById('capture-note-title')?.value || '').trim();
    const content = (document.getElementById('capture-note-content')?.value || '').trim();
    const tagsStr = (document.getElementById('capture-note-tags')?.value || '').trim();
    if (!content) {
      alert('Please enter note content.');
      return;
    }
    payload.title = title;
    payload.content = content;
    if (tagsStr) payload.tags = tagsStr.split(',').map(t => t.trim()).filter(Boolean);
  } else if (type === 'photo') {
    submitBtn = document.getElementById('capture-photo-submit-btn');
    const hint = (document.getElementById('capture-photo-hint')?.value || '').trim();
    const tagsStr = (document.getElementById('capture-photo-tags')?.value || '').trim();
    if (!activeCapturePhotoData) {
      alert('Please select or attach an image first.');
      return;
    }
    payload.image_data = activeCapturePhotoData;
    payload.hint = hint;
    if (tagsStr) payload.tags = tagsStr.split(',').map(t => t.trim()).filter(Boolean);
  }

  const origText = submitBtn ? submitBtn.textContent : 'SUBMIT';
  if (submitBtn) {
    submitBtn.disabled = true;
    submitBtn.textContent = '⏳ INGESTING & VECTORIZING...';
  }

  if (statusBox) {
    statusBox.style.display = 'block';
    statusBox.style.background = 'var(--term-surface-dim)';
    statusBox.style.color = 'var(--term-accent-gold)';
    statusBox.textContent = '⚡ Ingesting into Obsidian vault, computing BGE-large embeddings, and indexing Valkey A-MEM...';
  }

  try {
    const res = await fetch('/api/obsidian/capture', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await res.json();

    if (data.ok) {
      if (statusBox) {
        statusBox.style.background = 'rgba(34, 197, 94, 0.15)';
        statusBox.style.border = '1px solid #16a34a';
        statusBox.style.color = '#22c55e';
        statusBox.innerHTML = `✅ <strong>Successfully Captured!</strong><br>
          📄 Note: <code>${escapeHtml(data.path || data.title || 'Saved')}</code><br>
          🧠 Chunks Embedded: <strong>${data.chunks_embedded || 1}</strong> to Qdrant<br>
          💾 Valkey Fact: <em>${escapeHtml(data.amem_fact || 'Stored in RAM')}</em>`;
      }

      const obsDbSelect = document.getElementById('obsidian-database-select');
      if (obsDbSelect && obsDbSelect.value === targetDb) {
        loadObsidianNotes(true);
      }

      setTimeout(() => {
        closeQuickCaptureModal();
      }, 2500);
    } else {
      if (statusBox) {
        statusBox.style.background = 'rgba(239, 68, 68, 0.15)';
        statusBox.style.border = '1px solid #dc2626';
        statusBox.style.color = '#ef4444';
        statusBox.textContent = `❌ Capture failed: ${data.error || 'Unknown error'}`;
      }
    }
  } catch (err) {
    if (statusBox) {
      statusBox.style.background = 'rgba(239, 68, 68, 0.15)';
      statusBox.style.border = '1px solid #dc2626';
      statusBox.style.color = '#ef4444';
      statusBox.textContent = `❌ Ingestion error: ${err.message}`;
    }
  } finally {
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.textContent = origText;
    }
  }
}

export async function syncObsidianToQdrant() {
  const syncBtn = document.getElementById('sync-obsidian-btn') || document.getElementById('sync-obsidian-qdrant-btn');
  const origText = syncBtn ? syncBtn.textContent : '[⚡ SYNC TO QDRANT]';
  if (syncBtn) {
    syncBtn.disabled = true;
    syncBtn.textContent = '[⏳ SYNCING VECTORS...]';
  }
  try {
    const res = await fetch('/api/obsidian/sync_to_memory', { method: 'POST' });
    const data = await res.json();
    if (data.ok && data.result) {
      const r = data.result;
      alert(`✅ [VAULT SYNC COMPLETE]: Synced ${r.total_notes || 0} notes, indexed ${r.new_chunks_indexed || 0} new vectors into Qdrant!`);
    } else {
      alert(`Vault sync result: ${data.error || 'OK'}`);
    }
  } catch (err) {
    alert(`Vault sync error: ${err.message}`);
  } finally {
    if (syncBtn) {
      syncBtn.disabled = false;
      syncBtn.textContent = origText;
    }
  }
}

window.openQuickCaptureModal = openQuickCaptureModal;
window.closeQuickCaptureModal = closeQuickCaptureModal;
window.switchCaptureTab = switchCaptureTab;
window.handleCapturePhotoSelect = handleCapturePhotoSelect;
window.submitQuickCapture = submitQuickCapture;
window.syncObsidianToQdrant = syncObsidianToQdrant;
window.loadObsidianNotes = loadObsidianNotes;

/**
 * Workspaces & Remote Directory Management
 */
export async function loadWorkspaces() {
  const wsSelect = document.getElementById('workstation-workspace-select');
  const parentSelect = document.getElementById('mkdir-parent-select');

  try {
    const res = await fetch('/api/workspaces/list');
    const data = await res.json();
    if (!data.ok) return;

    State.workspaces = data.workspaces || [];
    State.workspaceRoots = data.roots || [];

    // Populate Parent Root select in mkdir modal
    if (parentSelect && Array.isArray(data.roots)) {
      parentSelect.innerHTML = data.roots.map(r => `
        <option value="${escapeHtml(r.path)}">${escapeHtml(r.label || r.path)} (${escapeHtml(r.path)}) [${escapeHtml(r.node_id || 'local')}]</option>
      `).join('');
    }

    // Populate Workstation Workspace dropdown
    if (wsSelect && Array.isArray(data.workspaces)) {
      if (data.workspaces.length === 0) {
        wsSelect.innerHTML = '<option value="">(No workspaces found - click [+ mkdir PROJECT])</option>';
      } else {
        wsSelect.innerHTML = data.workspaces.map(w => {
          const agentTag = w.agent ? ` [🤖 ${w.agent.name}]` : '';
          return `
            <option value="${escapeHtml(w.path)}">
              ${w.node_id === 'local' ? '💻' : '🌐'} ${escapeHtml(w.name)}${escapeHtml(agentTag)} (${escapeHtml(w.path)})
            </option>
          `;
        }).join('');
      }

      // Restore active workspace
      const currentActivePath = State.activeWorkspace ? State.activeWorkspace.path : data.active_workspace;
      if (currentActivePath) {
        const found = data.workspaces.find(w => w.path === currentActivePath);
        if (found) {
          wsSelect.value = found.path;
          State.activeWorkspace = found;
        } else if (data.workspaces.length > 0) {
          wsSelect.value = data.workspaces[0].path;
          State.activeWorkspace = data.workspaces[0];
        }
      } else if (data.workspaces.length > 0) {
        wsSelect.value = data.workspaces[0].path;
        State.activeWorkspace = data.workspaces[0];
      }

      updateWorkspaceBadge();
      await syncWorkspaceAgent(State.activeWorkspace ? State.activeWorkspace.path : '');
      loadWorkstationTree(State.activeWorkspace ? State.activeWorkspace.path : '');
    }

  } catch (err) {
    console.warn('Error loading workspaces:', err);
  }
}

function updateWorkspaceBadge() {
  const badge = document.getElementById('chat-workspace-badge');
  if (badge) {
    const name = State.activeWorkspace ? (State.activeWorkspace.name || State.activeWorkspace.path.split('/').pop()) : 'Root';
    badge.textContent = `📁 [WS: ${name}]`;
  }
}

async function handleCreateProject() {
  const modalMkdir = document.getElementById('modal-mkdir');
  const parentRoot = document.getElementById('mkdir-parent-select')?.value || '';
  const nameInput = document.getElementById('mkdir-name-input');
  const projectName = nameInput ? nameInput.value.trim() : '';
  const autonomy = document.getElementById('mkdir-autonomy-select')?.value || 'tiered';
  const initAgent = document.getElementById('mkdir-init-agent-check')?.checked ?? true;
  const agentNameInput = document.getElementById('mkdir-agent-name');
  const agentRoleInput = document.getElementById('mkdir-agent-role');
  const agentName = agentNameInput ? agentNameInput.value.trim() : '';
  const agentRole = agentRoleInput ? agentRoleInput.value.trim() : '';

  if (!projectName) {
    alert('Please enter a project or folder name.');
    return;
  }

  try {
    const res = await fetch('/api/workspaces/create', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        project_name: projectName,
        parent_root: parentRoot,
        autonomy_level: autonomy,
        create_chat_session: true,
        init_agent: initAgent,
        agent_name: agentName,
        agent_role: agentRole
      })
    });
    const data = await res.json();
    if (!data.ok) {
      alert(`Failed to create project: ${data.error || 'Unknown error'}`);
      return;
    }

    if (modalMkdir) modalMkdir.style.display = 'none';
    if (nameInput) nameInput.value = '';
    if (agentNameInput) agentNameInput.value = '';
    if (agentRoleInput) agentRoleInput.value = '';

    await loadWorkspaces();

    // Select the new workspace
    const wsSelect = document.getElementById('workstation-workspace-select');
    if (wsSelect && data.workspace_path) {
      wsSelect.value = data.workspace_path;
      const found = State.workspaces.find(w => w.path === data.workspace_path);
      State.activeWorkspace = found || { path: data.workspace_path, name: data.project_name };
      updateWorkspaceBadge();
      await syncWorkspaceAgent(data.workspace_path);
      loadWorkstationTree(data.workspace_path);
    }

    // Switch to the newly created chat thread
    if (data.session_id) {
      await switchSession(data.session_id);
    }

  } catch (err) {
    alert(`Error creating project: ${err.message}`);
  }
}

async function handleAddRoot() {
  const modalAddRoot = document.getElementById('modal-add-root');
  const labelInput = document.getElementById('add-root-label-input');
  const pathInput = document.getElementById('add-root-path-input');
  const nodeSelect = document.getElementById('add-root-node-select');

  const label = labelInput ? labelInput.value.trim() : '';
  const rootPath = pathInput ? pathInput.value.trim() : '';
  const nodeId = nodeSelect ? nodeSelect.value : 'local';

  if (!rootPath) {
    alert('Please enter an absolute directory path for the workspace root.');
    return;
  }

  try {
    const res = await fetch('/api/workspaces/roots/add', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        label: label,
        path: rootPath,
        node_id: nodeId
      })
    });
    const data = await res.json();
    if (!data.ok) {
      alert(`Failed to add workspace root: ${data.error || 'Unknown error'}`);
      return;
    }

    if (modalAddRoot) modalAddRoot.style.display = 'none';
    if (labelInput) labelInput.value = '';
    if (pathInput) pathInput.value = '';

    await loadWorkspaces();

  } catch (err) {
    alert(`Error adding workspace root: ${err.message}`);
  }
}

/**
 * Workstation File Tree & Editor
 */
export async function loadWorkstationTree(dirPath) {
  const treeEl = document.getElementById('workstation-file-tree');
  const titleEl = document.getElementById('workstation-tree-title');
  if (!treeEl) return;

  const targetDir = dirPath || (State.activeWorkspace ? State.activeWorkspace.path : '');
  if (titleEl && targetDir) {
    const folderName = targetDir.split('/').filter(Boolean).pop() || targetDir;
    titleEl.textContent = `📁 ${folderName.toUpperCase()}`;
  }

  try {
    const url = targetDir ? `/api/workspace/tree?dir=${encodeURIComponent(targetDir)}` : '/api/workspace/tree';
    const res = await fetch(url);
    const data = await res.json();
    if (!data.ok || !Array.isArray(data.tree)) {
      treeEl.innerHTML = '<div style="color:var(--term-text-muted); padding:6px;">No items in workspace.</div>';
      return;
    }

    if (data.tree.length === 0) {
      treeEl.innerHTML = '<div style="color:var(--term-text-muted); padding:6px;">Empty folder. Click [+ mkdir PROJECT] or use shell.</div>';
      return;
    }

    treeEl.innerHTML = renderTreeNodes(data.tree, 0);

  } catch (e) {
    console.warn('Workstation tree error:', e);
    treeEl.innerHTML = `<div style="color:var(--term-accent-red); padding:6px;">Error loading tree: ${escapeHtml(e.message)}</div>`;
  }
}

function renderTreeNodes(nodes, depth = 0) {
  if (!nodes || !nodes.length) return '';
  const paddingLeft = depth * 14;

  return nodes.map(item => {
    const isDir = item.type === 'directory';
    const icon = isDir ? '📁' : getFileIcon(item.name);
    const sizeStr = !isDir && item.size ? ` <span style="font-size:0.7rem; color:var(--term-text-muted);">(${formatBytes(item.size)})</span>` : '';
    const safePath = escapeHtml(item.path || item.full_path || item.name);
    const safeName = escapeHtml(item.name);

    let html = `
      <div class="tree-item ${isDir ? 'tree-dir' : 'tree-file'}" 
           style="padding: 2px 6px; padding-left:${paddingLeft + 6}px; cursor: pointer; font-size: 0.78rem; user-select: none; border-radius: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;"
           data-path="${safePath}"
           data-type="${item.type}"
           onclick="handleTreeItemClick('${safePath}', '${item.type}')"
           ondblclick="handleTreeItemDblClick('${safePath}', '${item.type}')"
           title="${safePath}">
        <span>${icon} ${safeName}</span>${sizeStr}
      </div>
    `;

    if (isDir && item.children && item.children.length > 0) {
      html += renderTreeNodes(item.children, depth + 1);
    }
    return html;
  }).join('');
}

function getFileIcon(filename) {
  if (!filename) return '📄';
  const ext = filename.split('.').pop().toLowerCase();
  if (['py'].includes(ext)) return '🐍';
  if (['js', 'mjs', 'ts'].includes(ext)) return '📜';
  if (['json', 'yaml', 'yml', 'toml'].includes(ext)) return '⚙️';
  if (['md', 'txt', 'rst'].includes(ext)) return '📝';
  if (['html', 'htm'].includes(ext)) return '🌐';
  if (['css'].includes(ext)) return '🎨';
  if (['sh', 'bash', 'ps1', 'bat'].includes(ext)) return '📟';
  return '📄';
}

function formatBytes(bytes) {
  if (!bytes || bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

window.setWorkstationMobileMode = function(mode) {
  const wsViewport = document.getElementById('workstation-viewport');
  const btnTree = document.getElementById('ws-mobile-btn-tree');
  const btnEditor = document.getElementById('ws-mobile-btn-editor');
  if (wsViewport) {
    wsViewport.setAttribute('data-mobile-mode', mode);
  }
  if (btnTree) btnTree.classList.toggle('active', mode === 'tree');
  if (btnEditor) btnEditor.classList.toggle('active', mode === 'editor');

  if (mode === 'editor') {
    const editor = document.getElementById('workstation-editor-textarea');
    if (editor) editor.focus();
  }
};

window.handleTreeItemClick = async function(filePath, type) {
  if (type === 'directory') return;

  State.activeFile = filePath;
  const titleEl = document.getElementById('workstation-active-file-title');
  const statsEl = document.getElementById('workstation-file-stats');
  const mobileIndicator = document.getElementById('ws-mobile-file-indicator');

  if (titleEl) titleEl.textContent = filePath;
  if (mobileIndicator) mobileIndicator.textContent = filePath.split('/').pop() || filePath;

  // Highlight active tree item
  document.querySelectorAll('.tree-file').forEach(el => {
    if (el.getAttribute('data-path') === filePath) {
      el.style.backgroundColor = 'var(--term-surface-dim)';
      el.style.color = 'var(--term-text-bright)';
    } else {
      el.style.backgroundColor = '';
      el.style.color = '';
    }
  });

  // Switch to editor view automatically on mobile
  if (window.innerWidth <= 768) {
    window.setWorkstationMobileMode('editor');
  }

  // Ensure code editor is shown
  const editorContainer = document.getElementById('workstation-editor-container');
  const termScreen = document.getElementById('workstation-term-screen');
  const toggleBtn = document.getElementById('ws-toggle-view-btn');
  if (editorContainer) editorContainer.style.display = 'flex';
  if (termScreen) termScreen.style.display = 'none';
  if (toggleBtn) toggleBtn.textContent = '[TERMINAL EMBED]';

  // Load content into editor
  try {
    const res = await fetch(`/api/workspace/file?path=${encodeURIComponent(filePath)}`);
    const data = await res.json();
    if (data.ok) {
      const editor = document.getElementById('workstation-editor-textarea');
      const content = data.content || '';
      if (editor) {
        editor.value = content;
        editor.focus();
      }
      if (statsEl) {
        const lines = content.split('\n').length;
        const bytes = new Blob([content]).size;
        statsEl.textContent = `(${lines} lines, ${formatBytes(bytes)})`;
      }
    }
  } catch (e) {
    console.warn('Failed to preview file:', e);
  }
};

window.saveWorkstationFile = async function() {
  const filePath = State.activeFile;
  if (!filePath) {
    alert('No active file selected to save.');
    return;
  }
  const editor = document.getElementById('workstation-editor-textarea');
  const content = editor ? editor.value : '';
  const saveBtn = document.getElementById('ws-save-btn');
  const statsEl = document.getElementById('workstation-file-stats');

  if (saveBtn) saveBtn.textContent = 'SAVING...';

  try {
    const res = await fetch('/api/workspace/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: filePath, content: content })
    });
    const data = await res.json();
    if (data.ok) {
      if (saveBtn) {
        saveBtn.textContent = '✓ SAVED!';
        setTimeout(() => { saveBtn.textContent = '[💾 SAVE FILE]'; }, 1600);
      }
      if (statsEl) {
        const lines = content.split('\n').length;
        statsEl.textContent = `(${lines} lines, ${formatBytes(data.size || content.length)})`;
      }
    } else {
      alert('Failed to save file: ' + (data.error || 'Unknown error'));
      if (saveBtn) saveBtn.textContent = '[💾 SAVE FILE]';
    }
  } catch (e) {
    alert('Save request failed: ' + e);
    if (saveBtn) saveBtn.textContent = '[💾 SAVE FILE]';
  }
};

window.reloadWorkstationFile = function() {
  if (State.activeFile) {
    window.handleTreeItemClick(State.activeFile, 'file');
  }
};

window.toggleWorkstationDiff = async function() {
  const filePath = State.activeFile;
  if (!filePath) return;
  const editor = document.getElementById('workstation-editor-textarea');
  const content = editor ? editor.value : '';

  try {
    const res = await fetch('/api/workspace/diff', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: filePath, content: content })
    });
    const data = await res.json();
    if (data.ok && data.diff) {
      alert(data.diff || 'No differences found against saved file.');
    } else {
      alert(data.message || 'Clean: File matches disk.');
    }
  } catch (e) {
    console.warn('Diff failed:', e);
  }
};

let workstationViewMode = 'editor';
window.toggleWorkstationView = function() {
  const editorContainer = document.getElementById('workstation-editor-container');
  const termScreen = document.getElementById('workstation-term-screen');
  const toggleBtn = document.getElementById('ws-toggle-view-btn');

  if (workstationViewMode === 'editor') {
    workstationViewMode = 'terminal';
    if (editorContainer) editorContainer.style.display = 'none';
    if (termScreen) termScreen.style.display = 'block';
    if (toggleBtn) toggleBtn.textContent = '[CODE EDITOR]';
    setTimeout(() => {
      if (typeof window.sendTerminalResize === 'function') window.sendTerminalResize();
      if (typeof window.focusTerminal === 'function') window.focusTerminal();
    }, 50);
  } else {
    workstationViewMode = 'editor';
    if (termScreen) termScreen.style.display = 'none';
    if (editorContainer) editorContainer.style.display = 'flex';
    if (toggleBtn) toggleBtn.textContent = '[TERMINAL EMBED]';
  }
};

window.handleTreeItemDblClick = function(filePath, type) {
  if (type === 'directory') return;
  State.activeFile = filePath;
  openFileInTerminal(filePath, 'nano');
};

window.openWorkstationFile = async function(filePath) {
  return window.handleTreeItemClick(filePath, 'file');
};

/**
 * Sync Active Workspace Agent DNA & Badges
 */
export async function syncWorkspaceAgent(wsPath) {
  const wsBadge = document.getElementById('workstation-agent-badge');
  const chatBadge = document.getElementById('chat-agent-badge');

  if (!wsPath) {
    State.activeProjectAgent = null;
    if (wsBadge) {
      wsBadge.textContent = '🤖 [AGENT: (none)]';
      wsBadge.title = 'No active workspace selected.';
      wsBadge.style.color = 'var(--term-text-muted)';
      wsBadge.style.borderColor = 'var(--term-border-dim)';
    }
    if (chatBadge) {
      chatBadge.textContent = '🤖 [AGENT: (none)]';
      chatBadge.style.color = 'var(--term-text-muted)';
      chatBadge.style.borderColor = 'var(--term-border-dim)';
    }
    return;
  }

  try {
    const res = await fetch(`/api/workspaces/agent?path=${encodeURIComponent(wsPath)}`);
    const data = await res.json();
    if (data.ok && data.agent) {
      State.activeProjectAgent = data.agent;
      const agentName = data.agent.name || data.agent.identity?.name || 'Agent';
      const agentRole = data.agent.role || data.agent.identity?.role || 'Engineer';
      const label = `🤖 [AGENT: ${agentName}]`;
      if (wsBadge) {
        wsBadge.textContent = label;
        wsBadge.title = `${data.agent.agent_id} • ${agentRole} (Click to inspect and edit Agent DNA)`;
        wsBadge.style.color = 'var(--term-accent-gold)';
        wsBadge.style.borderColor = 'var(--term-accent-gold)';
      }
      if (chatBadge) {
        chatBadge.textContent = label;
        chatBadge.title = `${data.agent.agent_id} • ${agentRole}`;
        chatBadge.style.color = 'var(--term-accent-gold)';
        chatBadge.style.borderColor = 'var(--term-accent-gold)';
      }
    } else {
      State.activeProjectAgent = null;
      if (wsBadge) {
        wsBadge.textContent = '🤖 [AGENT: (none)]';
        wsBadge.title = 'No agent bound to this workspace. Click to initialize Agent DNA.';
        wsBadge.style.color = 'var(--term-text-muted)';
        wsBadge.style.borderColor = 'var(--term-border-dim)';
      }
      if (chatBadge) {
        chatBadge.textContent = '🤖 [AGENT: (none)]';
        chatBadge.style.color = 'var(--term-text-muted)';
        chatBadge.style.borderColor = 'var(--term-border-dim)';
      }
    }
  } catch (err) {
    console.warn('Failed to sync workspace agent:', err);
  }
}

/**
 * Agent DNA Modal Controller
 */
let currentDnaCache = {
  activeTab: 'invariants',
  invariants_md: '',
  identity_md: '',
  soul_md: '',
  handover_md: '',
  agent_id: '',
  name: '',
  role: ''
};

export async function openAgentDnaModal() {
  const modal = document.getElementById('modal-agent-dna');
  if (!modal) return;

  const activeWs = State.activeWorkspace;
  if (!activeWs || !activeWs.path) {
    alert("Please select an active project workspace in [F2: WORKSTATION] first.");
    return;
  }

  const titleEl = document.getElementById('agent-dna-modal-title');
  const badgeIdEl = document.getElementById('agent-dna-badge-id');
  const statusEl = document.getElementById('agent-dna-save-status');
  if (statusEl) statusEl.textContent = '';

  try {
    const res = await fetch(`/api/workspaces/agent?path=${encodeURIComponent(activeWs.path)}`);
    const data = await res.json();

    if (!data.ok || !data.agent) {
      const confirmInit = confirm(
        `No dedicated agent DNA found in this workspace (.stonesage/).\n\nWould you like to bind a dedicated agent to '${activeWs.name}' now?`
      );
      if (!confirmInit) return;

      const agentName = prompt("Enter Agent Name:", `${activeWs.name.replace(/[^a-zA-Z0-9]/g, ' ').trim()} Specialist`) || `${activeWs.name} Specialist`;
      const agentRole = prompt("Enter Agent Technical Focus:", "Dedicated Software & Systems Architect") || "Architect";

      const initRes = await fetch('/api/workspaces/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_name: activeWs.name,
          parent_root: activeWs.parent_root || '',
          autonomy_level: State.autonomyLevel || 'tiered',
          init_agent: true,
          agent_name: agentName,
          agent_role: agentRole,
          create_chat_session: false
        })
      });
      const initData = await initRes.json();
      if (!initData.ok) {
        alert(`Failed to initialize agent: ${initData.error || 'Unknown error'}`);
        return;
      }
      await syncWorkspaceAgent(activeWs.path);
      return openAgentDnaModal();
    }

    const agent = data.agent;
    currentDnaCache = {
      activeTab: 'invariants',
      invariants_md: agent.invariants_md || '',
      identity_md: agent.identity_md || '',
      soul_md: agent.soul_md || '',
      handover_md: agent.handover_md || '',
      agent_id: agent.agent_id || '',
      name: agent.name || 'Agent',
      role: agent.role || 'Architect'
    };

    if (titleEl) titleEl.textContent = `🤖 AGENT DNA: ${currentDnaCache.name.toUpperCase()}`;
    if (badgeIdEl) badgeIdEl.textContent = `${currentDnaCache.agent_id} • ${currentDnaCache.role}`;

    switchDnaTab('invariants');
    modal.style.display = 'flex';

  } catch (err) {
    alert(`Error loading agent DNA: ${err.message}`);
  }
}

export function switchDnaTab(tabName) {
  const textarea = document.getElementById('agent-dna-textarea');
  const descEl = document.getElementById('agent-dna-tab-desc');

  // Save current textarea content to previous tab cache
  if (textarea && currentDnaCache.activeTab) {
    const prevKey = `${currentDnaCache.activeTab}_md`;
    currentDnaCache[prevKey] = textarea.value;
  }

  currentDnaCache.activeTab = tabName;

  const tabs = {
    'invariants': {
      btnId: 'tab-dna-invariants',
      key: 'invariants_md',
      desc: 'Living architectural invariants and rules enforced across model generations (.stonesage/INVARIANTS.md):'
    },
    'identity': {
      btnId: 'tab-dna-identity',
      key: 'identity_md',
      desc: 'Agent identity contract, persona, tone, and role specifications (.stonesage/IDENTITY.md):'
    },
    'soul': {
      btnId: 'tab-dna-soul',
      key: 'soul_md',
      desc: 'Core heuristics, behavioral boundaries, and cognitive imperatives (.stonesage/SOUL.md):'
    },
    'handover': {
      btnId: 'tab-dna-handover',
      key: 'handover_md',
      desc: 'Rolling milestone status, completed objectives, and next steps (.stonesage/HANDOVER.md):'
    }
  };

  // Update button highlights
  Object.keys(tabs).forEach(t => {
    const btn = document.getElementById(tabs[t].btnId);
    if (btn) {
      if (t === tabName) {
        btn.style.borderColor = 'var(--term-accent-gold)';
        btn.style.color = 'var(--term-accent-gold)';
      } else {
        btn.style.borderColor = 'var(--term-border-dim)';
        btn.style.color = 'var(--term-text-muted)';
      }
    }
  });

  const activeConf = tabs[tabName];
  if (activeConf) {
    if (descEl) descEl.textContent = activeConf.desc;
    if (textarea) textarea.value = currentDnaCache[activeConf.key] || '';
  }
}

export async function handleSaveAgentDna() {
  const textarea = document.getElementById('agent-dna-textarea');
  const statusEl = document.getElementById('agent-dna-save-status');
  const activeWs = State.activeWorkspace;

  if (!activeWs || !activeWs.path) {
    alert("No active workspace selected.");
    return;
  }

  if (textarea && currentDnaCache.activeTab) {
    const key = `${currentDnaCache.activeTab}_md`;
    currentDnaCache[key] = textarea.value;
  }

  if (statusEl) statusEl.textContent = '⏳ Saving DNA contracts...';

  try {
    const res = await fetch('/api/workspaces/agent/update', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        workspace_path: activeWs.path,
        identity_md: currentDnaCache.identity_md,
        soul_md: currentDnaCache.soul_md,
        invariants_md: currentDnaCache.invariants_md,
        handover_md: currentDnaCache.handover_md
      })
    });
    const data = await res.json();
    if (data.ok) {
      if (statusEl) statusEl.textContent = '✔ Agent DNA contracts saved to repository (.stonesage/)';
      await syncWorkspaceAgent(activeWs.path);
      setTimeout(() => {
        if (statusEl) statusEl.textContent = '';
      }, 3000);
    } else {
      if (statusEl) statusEl.textContent = `❌ Save failed: ${data.error}`;
    }
  } catch (err) {
    if (statusEl) statusEl.textContent = `❌ Error: ${err.message}`;
  }
}


// Re-label the model picker and health popover whenever the live profile changes (engine swap, new context size...)
window.addEventListener('stonesage:profile', () => {
  if (!cachedClusterHealth) return;
  updateModelSelectors(cachedClusterHealth, document.getElementById('topbar-model-select'),
    document.getElementById('mobile-model-text'), cachedModelsInfo, cachedClusterModes);
  renderClusterHealthPopover(cachedClusterHealth, cachedProxmoxNodes);
});
