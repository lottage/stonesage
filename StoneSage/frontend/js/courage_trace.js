/**
 * 🧭 Trace (Engine Studio → Engine Console → 🧭 COURAGE TRACE), backend courage/trace.py. One row per:
 *   Courage turn  how it was handled, how it ended, model and tool steps
 *   Boost call    surface, egress class, which free source answered (metadata only: no message text is kept)
 *   patrol sweep  camera, scheduled/manual, frames and pans, where the camera went back to, vision calls
 * plus the escalation triggers each would have fired (recorded only; nothing acts on them yet). Click a row for details.
 * Backend: GET /api/courage/trace?limit=&kind=, GET /api/courage/trace/summary?hours=&kind=
 */

const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const S = { turns: [], summary: null, open: new Set(), onlyFlagged: false, hours: 24, kind: '', timer: null, bound: false };
const KINDS = [['', 'All'], ['courage', '🖥️ Computer'], ['memory', '🧠 Memory'], ['boost', '⚡ Boost'], ['patrol', '🛡️ Patrol'], ['commentary', '💬 Remarks'], ['escalation', '🪜 Escalations'], ['lease', '🔑 Leases']];

const OUTCOME = {
  answered: ['✓', 'var(--term-success, #22c55e)'],
  asked_approval: ['?', 'var(--term-accent-gold, #eab308)'],
  step_cap: ['⟳', 'var(--term-error, #ef4444)'],
  llm_error: ['✗', 'var(--term-error, #ef4444)'],
  error: ['✗', 'var(--term-error, #ef4444)'],
  abandoned: ['…', 'var(--term-text-muted)'],
  failed: ['✗', 'var(--term-error, #ef4444)'],
  stored: ['🧠', 'var(--term-success, #22c55e)'],
  refreshed: ['↻', 'var(--term-text-muted)'],
  nothing: ['·', 'var(--term-text-muted)'],
  done: ['✓', 'var(--term-success, #22c55e)'],
  interrupted: ['⏸', 'var(--term-accent-gold, #eab308)'],
  no_frames: ['✗', 'var(--term-error, #ef4444)'],
};
const TRIGGER_HELP = {
  tool_error: 'a tool returned an error',
  invalid_args: 'a tool call was refused before running (bad or unsafe arguments)',
  repeated_call: 'the same tool with the same arguments twice in one turn',
  nudged: 'the model described a tool call instead of making it',
  step_cap: 'ran out of steps without answering',
  llm_error: 'the coordinator on :8001 did not answer',
  empty_answer: 'the model ended with no text',
  all_failed: 'Boost: no free source answered',
  fell_through: 'Boost: an earlier source failed before one answered',
  local_fallback: 'Boost: answered by the local coordinator, not a free source',
  no_frames: 'patrol: the sweep got no frames',
  frame_error: 'patrol: a frame could not be fetched',
  vision_error: 'patrol: the vision model failed on a frame',
  return_failed: 'patrol: could not go back to the starting position',
  interrupted: 'patrol: someone took the camera mid-sweep',
  move_error: 'patrol: a PTZ move was refused (retried once)',
  save_failed: 'patrol: could not remember the starting position',
  memory_error: 'memory: learning from a conversation failed',
};

const secs = (ms) => (ms == null ? '–' : ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`);
const when = (at) => new Date(at * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', second: '2-digit' });
const chip = (text, color, title = '') =>
  `<span title="${esc(title)}" style="display:inline-block;padding:0 5px;margin:1px 2px;border:1px solid ${color};color:${color};border-radius:3px;font-size:0.68rem;">${esc(text)}</span>`;

function renderSummary() {
  const s = S.summary;
  if (!s) return '<div style="color:var(--term-text-muted)">Loading…</div>';
  if (!s.turns) return `<div style="color:var(--term-text-muted)">Nothing recorded in the last ${s.hours} h yet.</div>`;
  const counts = (obj, colorFn, help = {}) => Object.entries(obj || {}).sort((a, b) => b[1] - a[1])
    .map(([k, n]) => chip(`${k} ${n}`, colorFn(k), help[k] || '')).join('') || '<small>none</small>';
  return `
    <div style="display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:8px; font-size:0.74rem;">
      <div><b>${s.turns}</b> records · last ${s.hours} h<br><small>p50 ${secs(s.ms_p50)} · p95 ${secs(s.ms_p95)}</small></div>
      <div>Outcomes<br>${counts(s.outcomes, (k) => (OUTCOME[k] || [0, 'var(--term-text-muted)'])[1])}</div>
      <div>Handled by<br>${counts(s.paths, () => 'var(--term-text-muted)')}</div>
      <div>Triggers (recorded only)<br>${counts(s.triggers, () => 'var(--term-error, #ef4444)', TRIGGER_HELP)}</div>
      <div>Tool errors<br>${counts(s.tool_errors, () => 'var(--term-error, #ef4444)')}</div>
      <div>Kinds<br>${counts(s.kinds, () => 'var(--term-text-muted)')}</div>
      ${Object.keys(s.providers || {}).length ? `<div>Boost sources<br>${counts(s.providers, () => 'var(--term-accent-gold, #eab308)')}</div>` : ''}
      ${Object.keys(s.cameras || {}).length ? `<div>Patrol cameras<br>${counts(s.cameras, () => 'var(--term-text-muted)')}</div>` : ''}
    </div>`;
}

function renderBoost(t) {
  return `<div style="margin:4px 0 6px 18px; font-size:0.7rem; line-height:1.5;">
      <div>⚡ ${t.outcome === 'answered' ? `answered by <b>${esc(t.provider)}</b> (${esc(t.model)}, tier ${esc(t.tier)})` : 'no free source answered'}
        · ${secs(t.ms)}${t.stream ? ' to first byte (stream)' : ''}${t.tokens ? ` · ${t.tokens} tokens` : ''}</div>
      <div>Declared <b>${esc(t.declared)}</b> → class <b>${esc(t.class)}</b>${(t.found || []).length ? ` (found: ${esc(t.found.join(', '))})` : ''}${t.images ? ' · with pictures' : ''}</div>
      ${(t.tried || []).map((x) => `<div style="color:var(--term-error,#ef4444)">failed first: ${esc(x)}</div>`).join('')}
      <div style="color:var(--term-text-muted)">${t.skipped || 0} source(s) skipped (off, no key, over quota or not allowed for this class)</div>
    </div>`;
}

function renderPatrol(t) {
  const seen = (t.seen || []).map((x) => `${esc(x.seen.join(', '))} at ${x.pan}°`).join(' · ');
  return `<div style="margin:4px 0 6px 18px; font-size:0.7rem; line-height:1.5;">
      <div>🛡️ ${t.frames} frame(s) at ${esc((t.pans || []).join('°, '))}${(t.pans || []).length ? '°' : ''} · stopped: ${esc(t.stopped)}</div>
      <div>Went back to: <b>${esc({ start: 'where it was', home: 'home preset', failed: 'FAILED', none: '– (not moved back)' }[t.returned] || t.returned)}</b></div>
      <div>Vision: ${t.vision?.calls ?? 0} call(s), ${secs(t.vision?.ms)}${t.vision?.errors ? `, <span style="color:var(--term-error,#ef4444)">${t.vision.errors} failed</span>` : ''}</div>
      <div>Seen: ${seen || 'nobody known'}</div>
      ${t.error ? `<div style="color:var(--term-error,#ef4444)">error: ${esc(t.error)}</div>` : ''}
      ${t.save_error ? `<div style="color:var(--term-error,#ef4444)">saving the start: ${esc(t.save_error)}</div>` : ''}
      ${t.return_error ? `<div style="color:var(--term-error,#ef4444)">going back: ${esc(t.return_error)}</div>` : ''}
      ${t.home_error ? `<div style="color:var(--term-error,#ef4444)">home preset: ${esc(t.home_error)}</div>` : ''}
      ${t.move_error ? `<div style="color:var(--term-error,#ef4444)">PTZ move refused: ${esc(t.move_error)}</div>` : ''}
    </div>`;
}

function rowText(t) {
  if (t.kind === 'boost') return `⚡ ${esc(t.surface)} · ${esc(t.class)}${t.provider ? ` → ${esc(t.provider)}` : ''}`;
  if (t.kind === 'memory') return `🧠 ${t.stored?.length ? esc(t.stored.join(' · ')) : t.refreshed ? 'confirmed a note it already had' : 'nothing worth keeping'}`;
  if (t.kind === 'patrol') return `🛡️ ${esc(t.camera)} · ${t.frames} frame(s)${(t.seen || []).length ? ` · saw ${esc([...new Set(t.seen.flatMap((x) => x.seen))].join(', '))}` : ''}`;
  if (t.kind === 'lease') return `🔑 ${esc(t.event)} · ${esc(t.layout || '')}${t.why ? ` · ${esc(t.why)}` : ''}${t.held_min != null ? ` · held ${t.held_min} min` : ''}${(t.failed || []).length ? ` · FAILED: ${esc(t.failed.join('; '))}` : ''}`;
  if (t.kind === 'escalation') return `🪜 ${t.reason === 'tool_failing' ? `${esc(t.tool)} keeps failing (${t.errors_1h}/h)` : `${esc(t.user)} · ${esc(t.reason)}`} → ${esc(t.answered_by || t.outcome)}`;
  if (t.kind === 'commentary') return `💬 ${esc(t.trigger)} · ${t.remark ? esc(t.remark) : `held: ${esc(t.held || t.error || t.outcome)}`}`;
  return esc(t.user);
}

function renderSteps(t) {
  if (t.kind === 'memory') {
    return `<div style="margin:4px 0 6px 18px; font-size:0.7rem; line-height:1.5;">
      ${(t.stored || []).map((x) => `<div>🧠 stored: ${esc(x)}</div>`).join('') || '<div>nothing stored</div>'}
      ${t.refreshed ? `<div>↻ ${t.refreshed} note(s) it already had</div>` : ''}
      ${t.error ? `<div style="color:var(--term-error,#ef4444)">error: ${esc(t.error)}</div>` : ''}
      <div style="color:var(--term-text-muted)">${secs(t.ms)} · session ${esc(t.session)}</div></div>`;
  }
  if (t.kind === 'boost') return renderBoost(t);
  if (t.kind === 'patrol') return renderPatrol(t);
  if (t.kind === 'escalation') {
    return `<div style="margin:4px 0 6px 18px; font-size:0.7rem; line-height:1.5;">
      ${(t.rungs || []).map((r) => `<div>🪜 <b>${esc(r.rung)}</b>: ${esc(r.outcome)}${r.why ? ` (${esc(r.why)})` : ''}${r.source ? ` · ${esc(r.source)}` : ''}${r.ms != null ? ` · ${secs(r.ms)}` : ''}${r.error ? ` · <span style="color:var(--term-error,#ef4444)">${esc(r.error)}</span>` : ''}</div>`).join('')}
      ${t.local ? `<div style="color:var(--term-text-muted)">Local: ${esc(t.local)}</div>` : ''}
      ${t.answer ? `<div>Answer: ${esc(t.answer)}</div>` : ''}
      ${t.latest_error ? `<div style="color:var(--term-error,#ef4444)">${esc(t.latest_error)}</div>` : ''}</div>`;
  }
  if (t.kind === 'commentary') {
    const ms = [['vision', t.vision_ms], ['remark', t.llm_ms], ['Echo', t.speak_ms]].filter(([, v]) => v != null);
    return `<div style="margin:4px 0 6px 18px; font-size:0.7rem; line-height:1.5;">
      <div>${esc(t.facts)}</div>
      <div>${esc(t.outcome)}${t.held ? ` (${esc(t.held)})` : ''} · ${esc(t.room)}${(t.who || []).length ? ` · ${esc(t.who.join(', '))}` : ''}</div>
      ${t.remark ? `<div>💬 “${esc(t.remark)}”</div>` : ''}
      ${t.error ? `<div style="color:var(--term-error,#ef4444)">error: ${esc(t.error)}</div>` : ''}
      ${ms.length ? `<div style="color:var(--term-text-muted)">${ms.map(([k, v]) => `${k} ${secs(v)}`).join(' · ')}</div>` : ''}</div>`;
  }
  const rows = (t.steps || []).map((s) => {
    if ('llm' in s) {
      return `<div>🧠 model ${secs(s.llm)} · ${s.tokens} tokens · ${s.calls ? `asked for ${s.calls} tool call${s.calls > 1 ? 's' : ''}` : 'answered'}</div>`;
    }
    const mark = s.ok ? '<span style="color:var(--term-success,#22c55e)">ok</span>'
      : `<span style="color:var(--term-error,#ef4444)">${s.refused ? 'refused' : 'error'}: ${esc(s.error)}</span>`;
    return `<div>🔧 <b>${esc(s.tool)}</b> <code style="font-size:0.66rem">${esc(s.args)}</code> · ${mark}${s.ms != null ? ` · ${secs(s.ms)}` : ''}</div>`;
  }).join('');
  return `<div style="margin:4px 0 6px 18px; font-size:0.7rem; line-height:1.5;">
      ${rows || '<div style="color:var(--term-text-muted)">no model or tool calls</div>'}
      <div style="margin-top:3px; color:var(--term-text-muted)">Answer: ${esc(t.final) || '–'}</div>
      ${(t.recalled || []).length ? `<div>🧠 recalled ${t.recalled.length} memory note(s) (scores ${t.recalled.map((r) => r.score).join(', ')})</div>` : ''}
      <div style="color:var(--term-text-muted)">Model total ${secs(t.llm?.ms)} over ${t.llm?.calls ?? 0} call(s), ${t.llm?.tokens ?? 0} tokens · session ${esc(t.session)}</div>
    </div>`;
}

function renderTurns() {
  const turns = S.onlyFlagged ? S.turns.filter((t) => (t.triggers || []).length) : S.turns;
  if (!turns.length) return `<div style="color:var(--term-text-muted); padding:6px 0;">${S.onlyFlagged ? 'Nothing with triggers.' : 'Nothing recorded yet.'}</div>`;
  return turns.map((t) => {
    const key = `${t.kind || 'courage'}|${t.at}|${t.session || t.entity || t.surface || ''}`;
    const [icon, color] = OUTCOME[t.outcome] || ['?', 'var(--term-text-muted)'];
    const trig = (t.triggers || []).map((x) => chip(x, 'var(--term-error, #ef4444)', TRIGGER_HELP[x] || '')).join('');
    const open = S.open.has(key);
    return `<div style="border-bottom:1px solid var(--term-border-dim); padding:3px 0;">
        <div data-turn="${esc(key)}" style="cursor:pointer; display:flex; gap:8px; align-items:baseline; font-size:0.74rem;">
          <span style="color:${color}; font-weight:bold; width:1em;" title="${esc(t.outcome)}">${icon}</span>
          <span style="color:var(--term-text-muted); min-width:120px;">${when(t.at)}</span>
          <span style="min-width:62px;">${secs(t.ms)}</span>
          <span style="color:var(--term-text-muted); min-width:70px;">${esc(t.path || t.reason || (t.stream ? 'stream' : ''))}</span>
          <span style="flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${open ? '▾' : '▸'} ${rowText(t)}</span>
          <span>${trig}</span>
        </div>
        ${open ? renderSteps(t) : ''}
      </div>`;
  }).join('');
}

function render() {
  const root = $('#courage-trace-root');
  if (!root) return;
  root.innerHTML = `
    <div class="harness-card-title" style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
      <span>🧭 TRACE — Computer turns, Boost calls and patrol sweeps, with escalation triggers</span>
      <span style="display:flex; gap:8px; align-items:center; font-size:0.72rem; flex-wrap:wrap;">
        <span>${KINDS.map(([k, label]) => `<button type="button" class="preset-btn${S.kind === k ? ' active' : ''}" data-kind="${k}"
          style="padding:1px 6px; font-size:0.7rem;${S.kind === k ? ' outline:1px solid var(--term-accent-gold);' : ''}">${label}</button>`).join('')}</span>
        <label><input type="checkbox" id="ctrace-flagged" ${S.onlyFlagged ? 'checked' : ''}> only turns with triggers</label>
        <select class="form-input" id="ctrace-hours" style="font-size:0.72rem; padding:1px 4px;">
          ${[1, 24, 168].map((h) => `<option value="${h}" ${S.hours === h ? 'selected' : ''}>${h === 168 ? '7 days' : `${h} h`}</option>`).join('')}
        </select>
        <button type="button" class="term-cmd-btn" id="ctrace-refresh" style="font-size:0.72rem;">[🔄 REFRESH]</button>
      </span>
    </div>
    <div style="margin-bottom:10px;">${renderSummary()}</div>
    <div>${renderTurns()}</div>`;
}

async function refresh() {
  try {
    const [t, s] = await Promise.all([
      fetch(`/api/courage/trace?limit=100&kind=${S.kind}`).then((r) => r.json()),
      fetch(`/api/courage/trace/summary?hours=${S.hours}&kind=${S.kind}`).then((r) => r.json()),
    ]);
    S.turns = t.ok ? t.turns : [];
    S.summary = s.ok ? s.summary : null;
  } catch (e) {
    S.summary = { turns: 0, hours: S.hours };
  }
  render();
}

function bind() {
  if (S.bound) return;
  const root = $('#courage-trace-root');
  if (!root) return;
  S.bound = true;
  root.addEventListener('click', (e) => {
    const row = e.target.closest('[data-turn]');
    if (row) {
      const k = row.dataset.turn;
      if (S.open.has(k)) S.open.delete(k); else S.open.add(k);
      render();
      return;
    }
    const kb = e.target.closest('[data-kind]');
    if (kb) { S.kind = kb.dataset.kind; S.open.clear(); refresh(); return; }
    if (e.target.id === 'ctrace-refresh') refresh();
  });
  root.addEventListener('change', (e) => {
    if (e.target.id === 'ctrace-flagged') { S.onlyFlagged = e.target.checked; render(); }
    if (e.target.id === 'ctrace-hours') { S.hours = +e.target.value; refresh(); }
  });
}

export async function openCourageTrace() {
  bind();
  await refresh();
  if (S.timer) clearInterval(S.timer);
  S.timer = setInterval(() => { if ($('#econsole-layer-trace')?.style.display !== 'none') refresh(); }, 15000);
}

window.openCourageTrace = openCourageTrace;
