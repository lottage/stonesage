/**
 * StoneSage Sovereign Agent DNA Studio Controller.
 * Manages model-agnostic .agent.dna bundles, Soul/System Prompt editing, and A-MEM atomic fact cards.
 */

import { escapeHtml } from './state.js';

let activeAgents = [];
let selectedAgentId = null;
let currentAgentFull = null;

export async function fetchAgentDnaList() {
  try {
    const res = await fetch('/api/agent_dna/list');
    const json = await res.json();
    if (json.ok && json.agents) {
      activeAgents = json.agents;
      renderAgentDnaList();
      if (!selectedAgentId && activeAgents.length > 0) {
        selectAgent(activeAgents[0].agent_id);
      } else if (selectedAgentId) {
        selectAgent(selectedAgentId);
      }
    }
  } catch (e) {
    console.warn('Failed to fetch agent DNA list:', e);
  }
}

export function renderAgentDnaList() {
  const listEl = document.getElementById('agent-dna-list');
  if (!listEl) return;

  let html = '';
  activeAgents.forEach(a => {
    const isSel = a.agent_id === selectedAgentId;
    html += `
      <div class="agent-dna-item ${isSel ? 'active' : ''}" style="padding:8px 10px; margin-bottom:6px; background:${isSel ? 'var(--term-surface-hover)' : 'var(--term-bg)'}; border:1px solid ${isSel ? 'var(--term-accent-gold)' : 'var(--term-border-dim)'}; border-radius:4px; cursor:pointer;" onclick="window.selectAgent('${a.agent_id}')">
        <div style="display:flex; justify-content:space-between; align-items:center;">
          <strong style="color:var(--term-text-bright); font-size:0.85rem;">${a.name}</strong>
          <span style="font-size:0.68rem; font-family:monospace; background:rgba(255,255,255,0.08); padding:1px 5px; border-radius:2px;">${a.sha256}</span>
        </div>
        <div style="display:flex; justify-content:space-between; align-items:center; margin-top:4px; font-size:0.72rem; color:var(--term-text-muted);">
          <span>Archetype: ${a.archetype || 'standard'}</span>
          <span>🧠 ${a.fact_count} fact cards</span>
        </div>
      </div>
    `;
  });
  listEl.innerHTML = html;
}

export async function selectAgent(agentId) {
  selectedAgentId = agentId;
  renderAgentDnaList();
  await fetchAgentFull(agentId);
}

export async function fetchAgentFull(agentId) {
  const detailsEl = document.getElementById('agent-dna-details-panel');
  if (!detailsEl) return;

  try {
    const res = await fetch(`/api/agent_dna/get?agent_id=${encodeURIComponent(agentId)}`);
    const json = await res.json();
    if (json.ok && json.agent) {
      currentAgentFull = json.agent;
      renderAgentDnaDetails(json.agent);
    } else {
      detailsEl.innerHTML = `<div style="color:var(--term-accent-red); padding:1rem;">Agent not found: ${agentId}</div>`;
    }
  } catch (e) {
    detailsEl.innerHTML = `<div style="color:var(--term-accent-red); padding:1rem;">Error loading agent: ${e.message}</div>`;
  }
}

export function estimateTokens(text) {
  if (!text) return 0;
  return Math.ceil(text.trim().split(/\s+/).length * 1.3);
}

export function renderAgentDnaDetails(agent) {
  const detailsEl = document.getElementById('agent-dna-details-panel');
  if (!detailsEl || !agent) return;

  const manifest = agent.manifest || {};
  const soul = agent.soul || {};
  const facts = agent.amem_facts || [];

  let factsHtml = '';
  facts.forEach((fact, idx) => {
    const tok = estimateTokens(fact);
    const tokClass = tok <= 35 ? 'color:#22c55e;' : 'color:#f59e0b; font-weight:bold;';
    factsHtml += `
      <div style="background:var(--term-bg); border:1px solid var(--term-border-dim); border-radius:3px; padding:6px 10px; margin-bottom:6px; display:flex; justify-content:space-between; align-items:flex-start; gap:8px;">
        <div style="flex:1;">
          <div style="font-size:0.76rem; color:var(--term-text-bright); line-height:1.4;">${escapeHtml(fact)}</div>
          <div style="display:flex; gap:8px; align-items:center; margin-top:4px; font-size:0.68rem; color:var(--term-text-muted);">
            <span style="${tokClass}">⚡ ${tok} tokens</span>
            <span>• Trigger: Keyword &amp; Semantic Match (&ge;0.82 sim)</span>
          </div>
        </div>
        <button type="button" class="term-cmd-btn" style="padding:1px 6px; font-size:0.68rem; color:var(--term-accent-red);" onclick="window.deleteFactCard(${idx})" title="Delete Card">[🗑️ DEL]</button>
      </div>
    `;
  });

  detailsEl.innerHTML = `
    <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid var(--term-border-dim); padding-bottom:8px; margin-bottom:12px; flex-wrap:wrap; gap:8px;">
      <div>
        <h3 style="margin:0; font-size:1.1rem; color:var(--term-text-bright);">${escapeHtml(manifest.name || manifest.agent_id)}</h3>
        <div style="font-size:0.75rem; color:var(--term-text-muted); font-family:monospace; margin-top:2px;">
          ID: ${manifest.agent_id} | SHA-256: ${(manifest.sha256 || '').slice(0, 12)} | Model-Agnostic: YES
        </div>
      </div>
      <div style="display:flex; gap:6px; flex-wrap:wrap;">
        <button type="button" class="term-cmd-btn" onclick="window.saveAgentSoul()" style="background:var(--term-accent-gold); color:#000; font-weight:bold;">
          [💾 SAVE SOUL &amp; PROMPT]
        </button>
        <button type="button" class="term-cmd-btn" onclick="window.backupAgent('${manifest.agent_id}')" style="background:var(--term-accent-blue); color:#fff; font-weight:bold;">
          [💾 BACKUP RECOVERY]
        </button>
        <button type="button" class="term-cmd-btn" onclick="window.exportAgentBundle('${manifest.agent_id}')">
          [📥 EXPORT .AGENT.DNA]
        </button>
      </div>
    </div>

    <!-- Soul & Persona Live Editor -->
    <div style="background:var(--term-surface); border:1px solid var(--term-border-dim); border-radius:4px; padding:10px; margin-bottom:12px;">
      <div style="font-weight:bold; color:var(--term-accent-gold); font-size:0.84rem; margin-bottom:8px;">🎭 SOUL, PERSONA &amp; SYSTEM PROMPT</div>
      
      <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-bottom:8px;">
        <div>
          <label style="display:block; font-size:0.72rem; color:var(--term-text-muted); margin-bottom:2px;">Archetype:</label>
          <input type="text" id="dna-archetype-input" class="form-input" value="${escapeHtml(soul.archetype || '')}" style="width:100%; font-size:0.78rem;">
        </div>
        <div>
          <label style="display:block; font-size:0.72rem; color:var(--term-text-muted); margin-bottom:2px;">Voice Profile (Kokoro / Piper):</label>
          <div style="display:flex; gap:6px;">
            <input type="text" id="dna-voice-input" class="form-input" value="${escapeHtml(soul.voice || 'bm_george')}" style="flex:1; font-size:0.78rem;">
            <button type="button" class="preset-btn" onclick="window.testAgentVoice('${manifest.agent_id}', document.getElementById('dna-voice-input').value)">[🗣️ TEST]</button>
          </div>
        </div>
      </div>

      <div style="margin-bottom:8px;">
        <label style="display:block; font-size:0.72rem; color:var(--term-text-muted); margin-bottom:2px;">System Prompt / Core Directives:</label>
        <textarea id="dna-prompt-input" class="form-input" rows="4" style="width:100%; font-family:monospace; font-size:0.78rem; resize:vertical;">${escapeHtml(soul.core_prompt || '')}</textarea>
      </div>
    </div>

    <!-- A-MEM Atomic Fact Cards Manager -->
    <div style="background:var(--term-surface); border:1px solid var(--term-border-dim); border-radius:4px; padding:10px; margin-bottom:12px;">
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
        <div style="font-weight:bold; color:var(--term-accent-gold); font-size:0.84rem;">⚡ ATOMIC WORKING MEMORY (A-MEM CARDS &lt; 35 TOKENS)</div>
        <span style="font-size:0.72rem; color:var(--term-text-muted);">Valkey (:6379) + Qdrant (:6333)</span>
      </div>

      <!-- Add New Card Input Bar -->
      <div style="display:flex; gap:6px; margin-bottom:10px;">
        <input type="text" id="dna-new-fact-input" class="form-input" placeholder="Add atomic fact card (< 35 tokens)... e.g. Alex is the homeowner." style="flex:1; font-size:0.78rem;" oninput="window.updateNewCardTokenCount(this.value)">
        <span id="dna-new-fact-tok" style="font-size:0.72rem; font-family:monospace; align-self:center; color:#22c55e;">0 tok</span>
        <button type="button" class="term-cmd-btn" onclick="window.addNewFactCard()" style="background:var(--term-accent-green); color:#000; font-weight:bold;">[+ ADD CARD]</button>
      </div>

      <!-- Cards List -->
      <div style="max-height:260px; overflow-y:auto; border:1px solid var(--term-border-dim); border-radius:3px; padding:6px; background:rgba(0,0,0,0.2);">
        ${facts.length > 0 ? factsHtml : '<div style="color:var(--term-text-muted); font-size:0.75rem; text-align:center; padding:1rem;">No atomic fact cards saved yet.</div>'}
      </div>
    </div>
  `;
}

export function updateNewCardTokenCount(val) {
  const tokEl = document.getElementById('dna-new-fact-tok');
  if (!tokEl) return;
  const tok = estimateTokens(val);
  tokEl.textContent = `${tok} tok`;
  tokEl.style.color = tok <= 35 ? '#22c55e' : '#f59e0b';
}

export async function saveAgentSoul() {
  if (!selectedAgentId || !currentAgentFull) return;
  const promptVal = document.getElementById('dna-prompt-input')?.value || '';
  const archVal = document.getElementById('dna-archetype-input')?.value || '';
  const voiceVal = document.getElementById('dna-voice-input')?.value || '';

  try {
    const res = await fetch('/api/agent_dna/save_profile', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        agent_id: selectedAgentId,
        soul: {
          core_prompt: promptVal,
          archetype: archVal,
          voice: voiceVal
        }
      })
    });
    const json = await res.json();
    if (json.ok) {
      alert(`✅ Soul & System Prompt saved successfully for ${selectedAgentId}!`);
      await fetchAgentFull(selectedAgentId);
    } else {
      alert(`❌ Save failed: ${json.error || 'Unknown error'}`);
    }
  } catch (e) {
    alert(`❌ Save error: ${e.message}`);
  }
}

export async function addNewFactCard() {
  if (!selectedAgentId) return;
  const inputEl = document.getElementById('dna-new-fact-input');
  const text = inputEl ? inputEl.value.trim() : '';
  if (!text) {
    alert('Please enter fact card text.');
    return;
  }

  const tok = estimateTokens(text);
  if (tok > 50) {
    if (!confirm(`Warning: This card is ~${tok} tokens (exceeds recommended 35 token limit). Save anyway?`)) {
      return;
    }
  }

  try {
    const res = await fetch('/api/agent_dna/cards', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        agent_id: selectedAgentId,
        card_text: text
      })
    });
    const json = await res.json();
    if (json.ok) {
      inputEl.value = '';
      updateNewCardTokenCount('');
      await fetchAgentFull(selectedAgentId);
      await fetchAgentDnaList();
    } else {
      alert(`❌ Failed to add card: ${json.error || 'Unknown error'}`);
    }
  } catch (e) {
    alert(`❌ Error adding card: ${e.message}`);
  }
}

export async function deleteFactCard(index) {
  if (!selectedAgentId) return;
  if (!confirm(`Delete atomic fact card #${index + 1}?`)) return;

  try {
    const res = await fetch('/api/agent_dna/cards', {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        agent_id: selectedAgentId,
        index: index
      })
    });
    const json = await res.json();
    if (json.ok) {
      await fetchAgentFull(selectedAgentId);
      await fetchAgentDnaList();
    } else {
      alert(`❌ Failed to delete card: ${json.error || 'Unknown error'}`);
    }
  } catch (e) {
    alert(`❌ Error deleting card: ${e.message}`);
  }
}

export async function backupAgent(agentId) {
  try {
    const res = await fetch('/api/agent_dna/backup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ agent_id: agentId })
    });
    const json = await res.json();
    if (json.ok) {
      alert('✅ Backup Complete: Local NAS + Obsidian Vault + Cloud Staged!');
    } else {
      alert(`❌ Backup Error: ${json.error || 'Failed'}`);
    }
  } catch (e) {
    alert(`❌ Backup Error: ${e.message}`);
  }
}

export function exportAgentBundle(agentId) {
  window.open(`/api/agent_dna/export?agent_id=${encodeURIComponent(agentId)}`, '_blank');
}

export async function testAgentVoice(agentId, voice) {
  try {
    const text = agentId.includes('home') || agentId.includes('courage')
      ? "Yes, you loaf, what do you want? I have thousands of calculations to run, and you're interrupting my day!"
      : "Sovereign Agent DNA verified. All memory cards and architectural invariants active.";

    const res = await fetch('/api/presence/speak', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: text, voice: voice || 'bm_george', speed: 1.06 })
    });
    const json = await res.json();
    if (json.ok && json.audio_base64) {
      const audio = new Audio(`data:audio/wav;base64,${json.audio_base64}`);
      audio.play().catch(e => console.log('Audio play blocked:', e));
    }
  } catch (e) {
    alert(`Voice test error: ${e.message}`);
  }
}

// Bind to window for HTML onclick handlers
window.fetchAgentDnaList = fetchAgentDnaList;
window.selectAgent = selectAgent;
window.saveAgentSoul = saveAgentSoul;
window.addNewFactCard = addNewFactCard;
window.deleteFactCard = deleteFactCard;
window.updateNewCardTokenCount = updateNewCardTokenCount;
window.backupAgent = backupAgent;
window.exportAgentBundle = exportAgentBundle;
window.testAgentVoice = testAgentVoice;

