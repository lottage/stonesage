/**
 * 🧠 Courage's memories (Engine Studio → Engine Console → 🧠 MEMORIES): what Courage has learned about you from
 * conversations (backend courage/memory.py), newest first, each with a Forget button. Notes live only on LXC 120.
 * Backend: GET /api/courage/memories, POST /api/courage/memories/forget {id}
 */

const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const S = { notes: [], bound: false, confirming: null };
const when = (at) => new Date(at * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });

function render() {
  const root = $('#courage-memories-root');
  if (!root) return;
  const rows = S.notes.map((n) => `
    <div style="display:flex; gap:8px; align-items:baseline; padding:4px 0; border-bottom:1px solid var(--term-border-dim); font-size:0.76rem;">
      <span style="color:var(--term-text-muted); min-width:120px;">${when(n.at)}</span>
      <span style="flex:1;">${esc(n.text)}${n.times > 1 ? ` <small style="color:var(--term-text-muted)">· heard ${n.times}×, last ${when(n.seen)}</small>` : ''}</span>
      ${S.confirming === n.id
        ? `<span style="font-size:0.7rem; color:var(--term-accent-gold)">forget it?</span>
           <button type="button" class="preset-btn" data-forget-yes="${esc(n.id)}" style="padding:1px 6px; font-size:0.7rem;">✓ Yes</button>
           <button type="button" class="preset-btn" data-forget-no="1" style="padding:1px 6px; font-size:0.7rem;">✗ No</button>`
        : `<button type="button" class="preset-btn" data-forget="${esc(n.id)}" style="padding:1px 6px; font-size:0.7rem;" title="Computer forgets this">🗑 Forget</button>`}
    </div>`).join('');
  root.innerHTML = `
    <div class="harness-card-title" style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
      <span>🧠 COMPUTER'S MEMORIES — learned from your conversations, recalled when they fit (${S.notes.length})</span>
      <button type="button" class="term-cmd-btn" id="cmem-refresh" style="font-size:0.72rem;">[🔄 REFRESH]</button>
    </div>
    <div style="font-size:0.72rem; color:var(--term-text-muted); margin-bottom:6px;">
      Kept on LXC 120 only. Home tasks and small talk are not remembered; Computer's own stories never are.
    </div>
    ${rows || '<div style="color:var(--term-text-muted); padding:6px 0;">Nothing yet. Talk to him.</div>'}`;
}

async function refresh() {
  try {
    const j = await (await fetch('/api/courage/memories')).json();
    S.notes = j.ok ? j.memories : [];
  } catch (e) { S.notes = []; }
  render();
}

function bind() {
  if (S.bound) return;
  const root = $('#courage-memories-root');
  if (!root) return;
  S.bound = true;
  root.addEventListener('click', async (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    if (b.id === 'cmem-refresh') { refresh(); return; }
    if (b.dataset.forget) { S.confirming = b.dataset.forget; render(); return; }   // confirm in place, no confirm()
    if (b.dataset.forgetNo) { S.confirming = null; render(); return; }
    if (b.dataset.forgetYes) {
      await fetch('/api/courage/memories/forget', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                                    body: JSON.stringify({ id: b.dataset.forgetYes }) });
      S.confirming = null;
      refresh();
    }
  });
}

export async function openCourageMemories() {
  bind();
  await refresh();
}

window.openCourageMemories = openCourageMemories;
