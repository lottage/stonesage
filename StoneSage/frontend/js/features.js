/**
 * What this install has (GET /api/ui/features, backend stonesage/ui_features.py).
 *
 * The page starts bare. Anything that exists only on some installs carries a `data-needs` attribute and the `hidden` flag in
 * index.html; this module shows it when the server says the thing is there:
 *
 *   data-needs="shelved"        features.shelved is on (3D Citadel, the nudge button)
 *   data-needs="file_editor"    features.file_editor is on (F2 workstation)         (also web_terminal, code_tools, raw_ha_call)
 *   data-needs="obsidian"       a vault or the CouchDB it syncs through is configured (F6)
 *   data-needs="app:hearth"     the Hearth app is loaded (F5)
 *   data-needs="service:qdrant" config.json (or the environment) names that optional service
 *
 * It also writes the header (product + real version + the loaded apps), the token tally (only when there is a real count),
 * and the agent's display name from its soul. Everything else on the page that needs the data asks `loadFeatures()`.
 */

import { State } from './state.js';

export const Features = { data: null, loaded: false };
let pending = null;

export function loadFeatures(force = false) {
  if (pending && !force) return pending;
  pending = fetch('/api/ui/features', { cache: 'no-store' })
    .then((r) => (r.ok ? r.json() : null))
    .catch(() => null)
    .then((data) => {
      Features.data = data && data.ok ? data : null;
      Features.loaded = true;
      State.featuresLoaded = true;
      applyFeatures();
      window.dispatchEvent(new CustomEvent('stonesage:features', { detail: Features.data }));
      return Features.data;
    });
  return pending;
}

/** Resolves with the features (or null when the server did not answer) once they are known. */
export function featuresReady() { return loadFeatures(); }

/** The Home app's own block ({} until known / when no app gives one). */
export function homeInfo() { return (Features.data && Features.data.home) || {}; }

/** Does this install have what `need` names? (see the header comment) */
export function has(need) {
  const f = Features.data;
  if (!f || !need) return false;
  if (need === 'obsidian') return Boolean(f.obsidian);
  if (need.startsWith('app:')) return (f.apps || []).some((a) => a.id === need.slice(4));
  if (need.startsWith('service:')) return Boolean((f.configured || {})[need.slice(8)]);
  if (need.startsWith('provider:')) return Boolean((f.providers || {})[need.slice(9)]);
  return Boolean((f.features || {})[need]);
}

function formatTokens(n) {
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
}

export function applyFeatures() {
  const f = Features.data;
  document.querySelectorAll('[data-needs]').forEach((el) => { el.hidden = !has(el.getAttribute('data-needs')); });
  if (!f) return;

  // header: the product, its real version, the app that is running on it
  const ver = document.getElementById('brand-version');
  if (ver && f.version) { ver.textContent = `v${f.version}`; ver.hidden = false; }
  const app = document.getElementById('brand-app');
  if (app && (f.apps || []).length) { app.textContent = f.apps.map((a) => a.name).join(' · '); app.hidden = false; }
  document.title = (f.apps || []).length ? `StoneSage · ${f.apps[0].name}` : 'StoneSage';

  // token tally: only a real count
  const tokens = document.getElementById('topbar-tokens-badge');
  const text = document.getElementById('topbar-tokens-text');
  const cloud = f.usage && f.usage.cloud_tokens_today;
  if (tokens && text) {
    text.textContent = cloud ? `Cloud today: ${formatTokens(cloud)} tokens` : '';
    tokens.hidden = !cloud;
  }

  // the default agent, by the name its soul gives it
  const name = (f.agent && f.agent.name) || '';
  const opt = document.getElementById('agent-option-default');
  if (opt && name) opt.textContent = `✨ ${name}`;
  const title = document.getElementById('computer-card-title');
  if (title && name) title.textContent = `💻 ${name.toUpperCase()}`;
  const ask = document.getElementById('courage-ask-btn');
  if (ask && name) ask.textContent = `[🗣️ ASK ${name.toUpperCase()}]`;

  // a tab the person was on (hash or last visit) that this install does not have: back to chat
  const active = document.querySelector(`.nav-tab-btn[data-tab="${State.activeTab}"]`);
  if (active && active.hidden && typeof window.switchTab === 'function') window.switchTab('chat');
}
