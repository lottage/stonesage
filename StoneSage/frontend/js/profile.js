/**
 * Live system profile (GET /api/system/profile): the only source for hardware, engine and model labels.
 *
 * Static HTML:   <span data-profile="engines.coordinator.label"></span>
 *                <option data-profile-tpl="{engines.worker.model} · {engines.worker.gpu.short} :{engines.worker.port}">
 * Dynamic JS:    engineLabel('worker'), engineModel('coordinator'), gpuLabel('vision'), then re-render on the
 *                'stonesage:profile' window event.
 * Reloaded on page load, every minute, and by refreshProfile() after any engine or parameter change.
 */

export const Profile = { data: null, loading: null };

const REFRESH_MS = 60000;

function lookup(path, obj = Profile.data) {
  return path.split('.').reduce((o, k) => (o == null ? undefined : o[k]), obj);
}

function fill(tpl) {
  let missing = false;
  const out = tpl.replace(/\{([\w.]+)\}/g, (_, path) => {
    const v = lookup(path);
    if (v == null || v === '') { missing = true; return ''; }
    return String(v);
  });
  // drop separators left dangling by a missing value ("Qwen3 14B ·  :8001" -> "Qwen3 14B :8001")
  return missing ? out.replace(/\s*·\s*(?=\s|:|$)/g, ' ').replace(/\s{2,}/g, ' ').trim() : out;
}

export function applyProfile(root = document) {
  if (!Profile.data) return;
  root.querySelectorAll('[data-profile]').forEach((el) => {
    const v = lookup(el.dataset.profile);
    if (v != null && v !== '') el.textContent = String(v);
  });
  root.querySelectorAll('[data-profile-tpl]').forEach((el) => {
    const v = fill(el.dataset.profileTpl);
    if (v) el.textContent = v;
  });
  root.querySelectorAll('[data-profile-title]').forEach((el) => {
    const v = fill(el.dataset.profileTitle);
    if (v) el.title = v;
  });
}

export function engine(role) {
  return (Profile.data && Profile.data.engines && Profile.data.engines[role]) || null;
}

/** 'Qwen3 14B' (falls back to the role name while loading or when the engine is unknown) */
export function engineModel(role) {
  const e = engine(role);
  return (e && e.model) || role;
}

/** 'Qwen3 14B · RX 6750 XT' */
export function engineLabel(role) {
  const e = engine(role);
  return (e && e.label) || role;
}

/** 'RX 6750 XT' / 'CPU' / '' */
export function gpuLabel(role) {
  const e = engine(role);
  if (!e) return '';
  return e.gpu ? e.gpu.short : (e.offloaded ? 'CPU' : '');
}

/** params badge for tight spaces: '14B', else the model name */
export function engineShort(role) {
  const e = engine(role);
  return (e && (e.params || e.model)) || role;
}

export async function loadProfile(fresh = false) {
  if (Profile.loading && !fresh) return Profile.loading;
  Profile.loading = (async () => {
    try {
      const res = await fetch(`/api/system/profile${fresh ? '?fresh=1' : ''}`, { cache: 'no-store' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      Profile.data = await res.json();
      applyProfile();
      window.dispatchEvent(new CustomEvent('stonesage:profile', { detail: Profile.data }));
    } catch (e) {
      console.warn('[profile] could not load /api/system/profile:', e);
    } finally {
      Profile.loading = null;
    }
    return Profile.data;
  })();
  return Profile.loading;
}

// Requests that change an engine's model, context, slots or GPU: refresh the profile once the engine is back.
const ENGINE_CHANGES = /\/api\/(engine\/(reload|restart)|harness\/(load|unload)|speculative\/toggle|cluster\/mode\/switch)\b/;

function watchEngineChanges() {
  const orig = window.fetch.bind(window);
  window.fetch = async (input, init) => {
    const res = await orig(input, init);
    const url = typeof input === 'string' ? input : (input && input.url) || '';
    const method = ((init && init.method) || (input && input.method) || 'GET').toUpperCase();
    if (method === 'POST' && ENGINE_CHANGES.test(url)) {
      [3000, 15000, 45000].forEach((ms) => setTimeout(() => loadProfile(true), ms));  // engines take a while to reload
    }
    return res;
  };
}

export function initProfile() {
  loadProfile();
  setInterval(() => loadProfile(), REFRESH_MS);
  watchEngineChanges();
}

window.refreshProfile = () => loadProfile(true);
window.StoneSageProfile = { Profile, engine, engineModel, engineLabel, engineShort, gpuLabel, applyProfile };
