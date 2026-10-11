/**
 * StoneSage Real Presence, Courage Computer Voice & Irrigation Cockpit.
 * Connects to /api/presence/* and /api/garden/* endpoints.
 */

export async function fetchPresenceState() {
  try {
    const res = await fetch('/api/presence/status');
    const json = await res.json();
    if (!json.ok) return null;
    renderPresence(json.data);
    return json.data;
  } catch (e) {
    console.warn('Presence fetch failed:', e);
    return null;
  }
}

// Identity key shared with the backend (frigate_presence.norm_name, wildlife_admin.prefix): 'Aunt May' -> 'aunt-may'
const normName = (n) => n.trim().toLowerCase().replace(/ /g, '-').replace(/'/g, '').replace(/[^a-z0-9-]/g, '');

async function postJSON(url, body) {
  const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  return res.json();
}

// "✗ Wrong" / "Correct as" on a card, and the "+ New profile" form. Corrections go back to where the sighting came
// from (Frigate event, sentry snapshot or patrol frame); correcting to a person also trains Frigate's face recognition.
// Confirmation happens inside the card, not with confirm(): the StoneSage Android app's WebView answers confirm()
// with "no" without showing anything, so the button looked dead there.
// After a correction the card fades out and comes back with that identity's next most recent sighting; on a
// relabel the other profile's card flashes with the moved frame.

// While a card waits for Yes/No, animates, or the new-profile form is open, presence refreshes must not rebuild
// the grid underneath it: the latest data is kept and drawn when the hold ends.
let holdRender = 0;
let heldData = null;
function hold() { holdRender += 1; }
function release() {
  holdRender = Math.max(0, holdRender - 1);
  if (!holdRender && heldData) { const d = heldData; heldData = null; renderPresence(d); }
}
const reducedMotion = () => { try { return matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (e) { return false; } };
const pause = (ms) => new Promise((r) => setTimeout(r, reducedMotion() ? 0 : ms));

function smallButton(label, title) {
  const b = document.createElement('button');
  b.type = 'button';
  b.className = 'preset-btn';
  b.style.cssText = 'padding:1px 8px; font-size:0.68rem;';
  b.textContent = label;
  b.title = title;
  return b;
}

// Replace the card's correction row with "question  ✓ Yes  ✗ No" until one is pressed.
function askInline(row, question, onYes, onNo) {
  const saved = [...row.childNodes];
  hold();
  const q = document.createElement('span');
  q.style.cssText = 'flex:1; font-size:0.68rem; color:var(--term-accent-gold); align-self:center;';
  q.textContent = question;
  const yes = smallButton('✓ Yes', 'Apply this correction');
  const no = smallButton('✗ No', 'Leave the sighting as it is');
  yes.onclick = (e) => { e.stopPropagation(); release(); onYes(); };
  no.onclick = (e) => { e.stopPropagation(); row.replaceChildren(...saved); if (onNo) onNo(); release(); };
  row.replaceChildren(q, yes, no);
  yes.focus();
}

// Card for an identity key, and the short note that tells the owner what they are looking at now.
const cardFor = (grid, key) => grid.querySelector(`[data-card="${CSS.escape(key)}"]`);
function arrive(grid, key, text) {
  const card = cardFor(grid, key);
  if (!card) return;
  card.classList.add('pg-arrive');
  card.addEventListener('animationend', () => card.classList.remove('pg-arrive'), { once: true });
  const note = document.createElement('div');
  note.className = 'pg-note';
  note.textContent = text;
  card.appendChild(note);
  setTimeout(() => { note.style.opacity = '0'; }, 6000);
}
function describeNext(loc) {
  if (!loc) return 'no earlier sighting';
  const ago = loc.minutes_ago < 90 ? `${Math.round(loc.minutes_ago)} min ago` : `${(loc.minutes_ago / 60).toFixed(1)} h ago`;
  const from = loc.source === 'frigate' ? 'Frigate' : loc.source === 'patrol' ? 'patrol' : 'camera sentry';
  return `↺ previous sighting · ${ago} · ${from}`;
}

// 2px box around the card's subject (backend sighting_boxes.py): Frigate's detector box, or where the vision model
// found that profile in a sentry/patrol picture. The image also shifts so the subject stays in the 90 px strip.
const boxCache = new Map();   // query -> box ([x, y, w, h] fractions) or null
function placeBox(img, box) {
  const wrap = img.parentElement;
  wrap.querySelector('.pg-box')?.remove();
  const cw = img.clientWidth, ch = img.clientHeight, iw = img.naturalWidth, ih = img.naturalHeight;
  if (!box || !iw || !cw) return;                              // no box, or the grid is hidden right now
  const px = box[0] + box[2] / 2, py = box[1] + box[3] / 2;
  img.style.objectPosition = `${(px * 100).toFixed(1)}% ${(py * 100).toFixed(1)}%`;
  const s = Math.max(cw / iw, ch / ih);                          // object-fit: cover
  const ox = (cw - iw * s) * px, oy = (ch - ih * s) * py;        // object-position px% py%
  const d = document.createElement('div');
  d.className = 'pg-box';
  Object.assign(d.style, { left: `${ox + box[0] * iw * s}px`, top: `${oy + box[1] * ih * s}px`,
                           width: `${box[2] * iw * s}px`, height: `${box[3] * ih * s}px` });
  wrap.appendChild(d);
}
function drawBoxes(grid) {
  grid.querySelectorAll('img[data-box-src]').forEach((img) => {
    const { boxSrc, boxRef, boxName, boxAt } = img.dataset;
    const q = `source=${encodeURIComponent(boxSrc)}&ref=${encodeURIComponent(boxRef)}&name=${encodeURIComponent(boxName)}&at=${boxAt}`;
    const show = (box) => (img.complete && img.naturalWidth ? placeBox(img, box)
      : img.addEventListener('load', () => placeBox(img, box), { once: true }));
    if (boxCache.has(q)) { show(boxCache.get(q)); return; }
    fetch(`/api/presence/box?${q}`).then((r) => r.json())
      .then((j) => { if (j.ok) { boxCache.set(q, j.box); show(j.box); } })
      .catch(() => { /* no box: the picture still shows */ });
  });
}
let boxResizeTimer = null;
window.addEventListener('resize', () => {
  clearTimeout(boxResizeTimer);
  boxResizeTimer = setTimeout(() => { const g = document.getElementById('presence-locations-grid'); if (g) drawBoxes(g); }, 200);
});

function bindCorrections(grid) {
  const form = document.getElementById('presence-new-profile');
  let pending = null;  // the card waiting for a new profile to be created

  const apply = async (row, action, name) => {
    const card = row.closest('[data-card]');
    const fromKey = card.dataset.card;
    const toKey = action === 'relabel' ? normName(name) : null;
    const note = document.createElement('span');
    note.style.cssText = 'font-size:0.68rem; color:var(--term-accent-gold);';
    note.textContent = action === 'reject' ? 'hiding…' : `moving to ${name}…`;
    row.replaceChildren(note);
    hold();
    card.classList.add('pg-leaving');
    const [res] = await Promise.all([
      postJSON('/api/presence/correct', { source: row.dataset.src, ref: row.dataset.ref, shown: row.dataset.shown, action, name })
        .catch((e) => ({ ok: false, error: e.message })),
      pause(350),                                   // let the fade finish even when the server is quick
    ]);
    if (!res.ok) {
      card.classList.remove('pg-leaving');
      note.textContent = `error: ${res.error}`;
      release();
      return;
    }
    note.textContent = 'finding the previous sighting…';
    heldData = null;                                // stale: the correction changed it
    release();
    const data = await fetchPresenceState();        // redraws the grid with each identity's newest remaining sighting
    const locs = (data && data.locations) || {};
    arrive(grid, fromKey, describeNext(locs[fromKey]));
    if (toKey && toKey !== fromKey) {
      const trained = res.faces_trained ? ` · ${res.faces_trained} face(s) learned` : res.face_registered === true ? ' · face learned' : '';
      arrive(grid, toKey, `✓ corrected to ${name}${trained}`);
    }
  };

  grid.onchange = (e) => {
    const sel = e.target.closest('select[data-correct]');
    if (!sel || !sel.value) return;
    const row = sel.closest('[data-src]');
    const choice = sel.value;
    sel.value = '';
    if (choice === '__new__') {
      pending = row;
      hold();                                       // keep this card while the form is open
      form.style.display = 'block';
      document.getElementById('np-name').focus();
      return;
    }
    askInline(row, `Really ${choice}?`, () => apply(row, 'relabel', choice));
  };
  grid.onclick = async (e) => {
    const rej = e.target.closest('button[data-correct="reject"]');
    if (rej) {
      const row = rej.closest('[data-src]');
      askInline(row, `Not ${row.dataset.shown}? Hide it`, () => apply(row, 'reject', ''));
      return;
    }
    if (e.target.id === 'np-cancel') { form.style.display = 'none'; pending = null; release(); return; }
    if (e.target.id !== 'np-save') return;
    const kind = document.getElementById('np-kind').value;
    const extra = document.getElementById('np-extra').value.trim();
    const body = { name: document.getElementById('np-name').value.trim(), kind,
                   traits: document.getElementById('np-traits').value.trim(),
                   [kind === 'person' ? 'role' : 'species']: extra };
    const status = document.getElementById('np-status');
    status.textContent = 'saving…';
    const res = await postJSON('/api/presence/profiles', body);
    if (!res.ok) { status.textContent = `error: ${res.error}`; return; }
    form.style.display = 'none';
    const row = pending;
    pending = null;
    release();
    if (row) await apply(row, 'relabel', res.profile.name);
    else fetchPresenceState();
  };
}

export function renderPresence(data) {
  if (!data) return;
  if (holdRender) { heldData = data; return; }   // a card is mid-correction: draw when it is done

  // Render resident & pet locations
  const locContainer = document.getElementById('presence-locations-grid');
  if (locContainer && data.locations) {
    let html = '';
    const locs = data.locations;
    const known = data.known_entities || {};
    const allEntities = [...(known.people || []), ...(known.pets || [])];
    if (locs.someone) allEntities.push({ name: 'Someone', role: 'Person Frigate could not identify' });

    const profileNames = [...(known.people || []), ...(known.pets || [])].map((e) => e.name);

    allEntities.forEach(ent => {
      const key = normName(ent.name);
      const loc = locs[key];
      const isOnline = loc && loc.minutes_ago < 60;
      const statusBadge = loc && loc.minutes_ago === 0
        ? `<span style="color:#22c55e; font-weight:bold;">🟢 In view now</span>`
        : isOnline
        ? `<span style="color:#22c55e; font-weight:bold;">🟢 Active (${Math.round(loc.minutes_ago)}m ago)</span>`
        : `<span style="color:var(--term-text-muted); font-weight:bold;">⚪ Inactive (${loc ? Math.round(loc.minutes_ago) + 'm ago' : 'No recent sighting'})</span>`;

      // Correction controls and the subject box: only for a sighting that has an image to judge
      const src = loc && loc.source === 'frigate' && loc.event_id ? ['frigate', loc.event_id]
        : loc && loc.source === 'patrol' && loc.patrol_ref ? ['patrol', loc.patrol_ref]
        : loc && loc.snapshot ? ['sentry', loc.snapshot] : null;
      // Frigate sightings carry an event id (snapshot proxied by StoneSage); sentry sightings a snapshot filename
      const snapSrc = loc && loc.source === 'frigate' && loc.event_id ? `/api/frigate/snapshot/${loc.event_id}`
        : loc && loc.source === 'patrol' ? loc.snapshot_url
        : loc && loc.snapshot ? `/api/presence/snapshot/${loc.snapshot}` : null;
      const snapshotImg = snapSrc
        ? `<div style="position:relative; margin-top:6px; border:1px solid var(--term-border-dim); border-radius:3px; overflow:hidden; max-height:90px; background:#000;">
             <img src="${snapSrc}" alt="${ent.name}" style="display:block; width:100%; height:90px; object-fit:cover;" loading="lazy"
               ${src ? `data-box-src="${src[0]}" data-box-ref="${src[1]}" data-box-name="${ent.name}" data-box-at="${loc.mtime}"` : ''}>
           </div>`
        : `<div style="margin-top:6px; height:45px; background:rgba(0,0,0,0.3); display:flex; align-items:center; justify-content:center; font-size:0.7rem; color:var(--term-text-muted);">No snapshot</div>`;
      const lastSeen = !loc ? 'Unknown'
        : loc.last_seen || new Date(loc.mtime * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
      const via = loc && loc.source === 'frigate' ? ' · Frigate' : loc && loc.source === 'patrol' ? ' · patrol' : '';
      // GPS home status for residents (HA person: Life360 / phone)
      const hs = (data.home_status || {})[key];
      const gps = !hs || ['unknown', 'unavailable', null].includes(hs.state) ? ''
        : `<div style="font-size:0.72rem; margin-top:2px; font-weight:bold; color:${hs.state === 'home' ? '#22c55e' : 'var(--term-accent-gold)'};">`
          + `${hs.state === 'home' ? '🏠 Home' : hs.state === 'not_home' ? '🚗 Away' : `📍 ${hs.state}`} · ${hs.source_label}`
          + `${hs.since_min != null ? ` · ${hs.since_min < 90 ? Math.round(hs.since_min) + ' min' : (hs.since_min / 60).toFixed(1) + ' h'}` : ''}</div>`;
      const correct = !src ? '' : `
          <div style="display:flex; gap:4px; margin-top:4px;" data-src="${src[0]}" data-ref="${src[1]}" data-shown="${ent.name}">
            <button type="button" class="preset-btn" style="padding:1px 6px; font-size:0.68rem;" data-correct="reject" title="Not a real sighting of ${ent.name}: hide it">✗ Wrong</button>
            <select class="form-input" style="flex:1; font-size:0.68rem; padding:1px 2px;" data-correct="relabel">
              <option value="">Correct as…</option>
              ${profileNames.filter((n) => n !== ent.name).map((n) => `<option value="${n}">${n}</option>`).join('')}
              <option value="__new__">+ New profile…</option>
            </select>
          </div>`;

      html += `
        <div class="pg-card" data-card="${key}" style="background:var(--term-bg); border:1px solid var(--term-border-dim); border-radius:4px; padding:8px;">
          <div style="display:flex; justify-content:space-between; align-items:center;">
            <span style="font-weight:bold; color:var(--term-text-bright);">${ent.name}</span>
            <span style="font-size:0.7rem; font-family:monospace;">${statusBadge}</span>
          </div>
          <div style="font-size:0.72rem; color:var(--term-text-muted); margin-top:2px;">${ent.role || ent.breed || ent.species}</div>${gps}
          ${snapshotImg}
          <div style="font-size:0.68rem; color:var(--term-text-muted); margin-top:4px;">Last: ${lastSeen}${loc && loc.camera ? ` · ${loc.camera}` : ''}${via}</div>${correct}
        </div>
      `;
    });
    locContainer.innerHTML = html + `
      <div id="presence-new-profile" style="display:none; grid-column:1/-1; background:var(--term-bg); border:1px dashed var(--term-accent-gold); border-radius:4px; padding:8px; font-size:0.74rem;">
        <b>New recognition profile</b>
        <div style="display:flex; gap:6px; flex-wrap:wrap; margin-top:6px;">
          <input class="form-input" id="np-name" placeholder="Name" maxlength="30" style="flex:1; min-width:110px;">
          <select class="form-input" id="np-kind"><option value="person">Person</option><option value="pet">Pet</option></select>
          <input class="form-input" id="np-extra" placeholder="Role (e.g. Visitor) / species (e.g. cat)" style="flex:1; min-width:140px;">
        </div>
        <input class="form-input" id="np-traits" placeholder="Looks like… (helps the vision model, e.g. tall, grey beard, red jacket)" maxlength="300" style="width:100%; margin-top:6px;">
        <div style="display:flex; gap:6px; margin-top:6px;">
          <button type="button" class="preset-btn" id="np-save">[Save &amp; apply]</button>
          <button type="button" class="preset-btn" id="np-cancel">[Cancel]</button>
          <span id="np-status" style="color:var(--term-text-muted); align-self:center;"></span>
        </div>
      </div>`;
    bindCorrections(locContainer);
    drawBoxes(locContainer);
  }

  // Render Appliance Status
  const appContainer = document.getElementById('presence-appliances-box');
  if (appContainer && data.appliances) {
    const d = data.appliances.dryer || {};
    const w = data.appliances.washer || {};
    const dBadge = d.is_running
      ? `<span style="color:#22c55e; font-weight:bold;">RUNNING (${d.remaining_time || 'active'})</span>`
      : (d.finished_recently ? `<span style="color:#eab308; font-weight:bold;">FINISHED (${Math.round(d.last_cycle_minutes_ago || 0)}m ago)</span>` : `<span style="color:var(--term-text-muted);">IDLE / OFF</span>`);
    const wBadge = w.is_running
      ? `<span style="color:#22c55e; font-weight:bold;">RUNNING (${w.remaining_time || 'active'})</span>`
      : (w.finished_recently ? `<span style="color:#eab308; font-weight:bold;">FINISHED (${Math.round(w.last_cycle_minutes_ago || 0)}m ago)</span>` : `<span style="color:var(--term-text-muted);">IDLE / OFF</span>`);

    appContainer.innerHTML = `
      <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px;">
        <div style="background:var(--term-bg); padding:6px; border:1px solid var(--term-border-dim); border-radius:3px;">
          <div style="font-size:0.72rem; color:var(--term-text-muted);">🧺 LG DRYER</div>
          <div style="font-size:0.82rem; font-weight:bold; margin-top:2px;">${dBadge}</div>
        </div>
        <div style="background:var(--term-bg); padding:6px; border:1px solid var(--term-border-dim); border-radius:3px;">
          <div style="font-size:0.72rem; color:var(--term-text-muted);">🫧 LG WASHER</div>
          <div style="font-size:0.82rem; font-weight:bold; margin-top:2px;">${wBadge}</div>
        </div>
      </div>
    `;
  }
}

// ------------------------------------------------------------ live cameras ----
// One tile per camera in config.json camera_ui (backend/camera_ui.py):
//   webrtc   go2rtc (Frigate cameras, and the solar driveway via tapo://). StoneSage only relays the SDP
//            offer/answer; video flows go2rtc -> browser. MP4 relayed by StoneSage when WebRTC is blocked.
//   snapshot still frame with a refresh button; the server keeps battery cameras to one wake per min_refresh_s.
// PTZ cameras get arrows and their HA presets.
const livePeers = [];
const liveVideos = [];
const liveTimers = [];  // WebRTC -> MP4 fallback timers: must not fire after the tiles are gone
let patrolTimer = null;

function stopLive() {
  clearInterval(patrolTimer);
  while (liveTimers.length) clearTimeout(liveTimers.pop());
  while (livePeers.length) {
    const pc = livePeers.pop();
    try { pc.close(); } catch (e) { /* already closed */ }
  }
  while (liveVideos.length) {  // MP4: stop fetching so StoneSage's relay and go2rtc's stream end
    const v = liveVideos.pop();
    v.pause(); v.removeAttribute('src'); v.load();
  }
  const grid = document.getElementById('presence-live-grid');
  if (grid) grid.innerHTML = '';
}

function iceGathered(pc, ms = 1500) {
  if (pc.iceGatheringState === 'complete') return Promise.resolve();
  return new Promise((resolve) => {
    const done = () => { if (pc.iceGatheringState === 'complete') resolve(); };
    pc.addEventListener('icegatheringstatechange', done);
    setTimeout(resolve, ms);  // host candidates are enough on the LAN; don't wait for slow STUN
  });
}

function setStatus(status, text, live = false) {
  status.textContent = text;
  status.style.color = live ? '#22c55e' : 'var(--term-text-muted)';
}

// When a stream fails, send the browser's own ICE view to StoneSage's log (candidate types and pair states):
// the only place that shows why one device connects and another does not.
async function reportIceFailure(pc, stream, status) {
  try {
    const all = [];
    (await pc.getStats()).forEach((s) => all.push(s));
    const byId = Object.fromEntries(all.map((s) => [s.id, s]));
    const cand = (c) => (c ? `${c.candidateType}/${c.protocol} ${c.address || c.ip || '?'}:${c.port}` : '?');
    const report = {
      stream, ua: navigator.userAgent, page: location.origin,
      local: all.filter((s) => s.type === 'local-candidate').map(cand),
      remote: all.filter((s) => s.type === 'remote-candidate').map(cand),
      pairs: all.filter((s) => s.type === 'candidate-pair').map((p) =>
        `${cand(byId[p.localCandidateId])} -> ${cand(byId[p.remoteCandidateId])}: ${p.state} req=${p.requestsSent || 0} resp=${p.responsesReceived || 0}`),
    };
    status.title = report.pairs.join('\n') || 'no candidate pairs';
    await fetch('/api/cameras/webrtc-report', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(report),
    });
  } catch (e) { /* diagnostics only */ }
}

// MP4 fallback: go2rtc's fragmented MP4 (H.264 as-is, video only) relayed by StoneSage over the page's own
// HTTP(S) connection. Used when this browser cannot reach go2rtc directly (Firefox on Android 16 without the
// local-network permission never sends an ICE check). Remembered per browser so later visits start on MP4.
const MP4_PREF = 'stonesage.live.mp4';
const prefersMP4 = () => { try { return localStorage.getItem(MP4_PREF) === '1'; } catch (e) { return false; } };

function playMP4(video, status, stream, audio = false, label = 'MP4') {
  video.srcObject = null;
  liveVideos.push(video);
  const tag = `● LIVE (${label}${audio ? '' : ', no sound'})`;
  video.addEventListener('playing', () => setStatus(status, tag, true), { once: true });
  video.addEventListener('error', () => setStatus(status, 'stream ended'), { once: true });
  setStatus(status, `connecting (${label})…`);
  video.src = `/api/cameras/mp4?stream=${encodeURIComponent(stream)}${audio ? '&audio=1' : ''}`;
  video.play().catch(() => { /* muted autoplay starts on its own */ });
}

// Per-camera stream choice (the tile's quality menu), remembered in this browser.
const MODES = {
  auto: 'Auto (WebRTC, MP4 if blocked)',
  webrtc: 'WebRTC · sound · fastest',
  mp4: 'MP4 · no sound',
  mp4a: 'MP4 + sound (FLAC)',
  low: 'Low data 720p (MP4)',
  hd: 'HD 1080p (MP4)',
};
const modeKey = (entity) => `stonesage.live.mode.${entity}`;
function getMode(entity) { try { return localStorage.getItem(modeKey(entity)) || 'auto'; } catch (e) { return 'auto'; } }
function setMode(entity, mode) { try { localStorage.setItem(modeKey(entity), mode); } catch (e) { /* private mode */ } }

function playTile(c, video, status) {
  const mode = getMode(c.entity);
  const v = c.variants || {};
  if (mode === 'mp4') return playMP4(video, status, c.stream);
  if (mode === 'mp4a') return playMP4(video, status, c.stream, true, 'MP4');
  if (mode === 'low' && v.low) return playMP4(video, status, v.low, false, '720p');
  if (mode === 'hd' && v.hd) return playMP4(video, status, v.hd, false, '1080p');
  return playWebRTC(video, status, c.stream, mode !== 'webrtc');
}

async function playWebRTC(video, status, stream, allowFallback = true) {
  if (allowFallback && prefersMP4()) { playMP4(video, status, stream); return; }
  const pc = new RTCPeerConnection();
  livePeers.push(pc);
  const media = new MediaStream();
  video.srcObject = media;
  pc.ontrack = (e) => media.addTrack(e.track);
  let fellBack = false;
  const fallBack = () => {
    // 'closed': stopLive() closed this tile (tab switch, quality change): not a WebRTC failure, keep the preference
    if (!allowFallback || fellBack || pc.connectionState === 'connected' || pc.signalingState === 'closed') return;
    fellBack = true;
    try { localStorage.setItem(MP4_PREF, '1'); } catch (e) { /* private mode: fall back each time */ }
    try { pc.close(); } catch (e) { /* already closed */ }
    playMP4(video, status, stream);
  };
  liveTimers.push(setTimeout(fallBack, 6000));  // Firefox takes ~13 s to declare failure; don't make the viewer wait
  pc.onconnectionstatechange = () => {
    const s = pc.connectionState;
    if (fellBack) return;
    setStatus(status, { connected: '● LIVE', failed: 'connection failed', disconnected: 'reconnecting…' }[s] || s, s === 'connected');
    if (s === 'failed') { reportIceFailure(pc, stream, status); fallBack(); }
  };
  pc.addTransceiver('video', { direction: 'recvonly' });
  pc.addTransceiver('audio', { direction: 'recvonly' });
  await pc.setLocalDescription(await pc.createOffer());
  await iceGathered(pc);
  const res = await fetch('/api/cameras/webrtc', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ stream, offer: { sdp: pc.localDescription.sdp } }),
  });
  const json = await res.json();
  if (!json.ok) throw new Error(json.error || 'signalling failed');
  await pc.setRemoteDescription(json.answer);
}

async function loadSnapshot(img, status, entity, force) {
  setStatus(status, force ? 'waking camera…' : 'loading…');
  const res = await fetch(`/api/cameras/snapshot?entity=${encodeURIComponent(entity)}${force ? '&force=1' : ''}`);
  if (!res.ok) { setStatus(status, (await res.json().catch(() => ({}))).error || `HTTP ${res.status}`); return; }
  const age = parseInt(res.headers.get('X-Snapshot-Age') || '0', 10);
  if (img.dataset.blob) URL.revokeObjectURL(img.dataset.blob);
  img.dataset.blob = URL.createObjectURL(await res.blob());
  img.src = img.dataset.blob;
  setStatus(status, age < 5 ? 'snapshot · just now' : `snapshot · ${age < 120 ? age + ' s' : Math.round(age / 60) + ' min'} old`);
}

async function ptz(entity, action, preset, status) {
  setStatus(status, action === 'preset' ? `moving to ${preset.trim()}…` : `moving ${action}…`);
  const res = await fetch('/api/cameras/ptz', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ entity, action, preset }),
  });
  const json = await res.json();
  setStatus(status, json.ok ? 'moved' : `PTZ: ${json.error}`);
}

function tileHTML(c, i) {
  const media = c.live === 'snapshot'
    ? `<img id="cam-img-${i}" alt="${c.name}" style="width:100%; aspect-ratio:16/9; object-fit:cover; display:block; background:#000;">`
    : `<video id="cam-vid-${i}" autoplay muted playsinline style="width:100%; aspect-ratio:16/9; display:block; background:#000;" title="Click to toggle sound"></video>`;
  const btn = 'class="preset-btn" style="padding:2px 8px; font-size:0.72rem;"';
  const ptzHTML = !c.ptz ? '' : `
    <div style="display:flex; flex-wrap:wrap; gap:4px; align-items:center; padding:4px 6px; background:var(--term-bg); border-top:1px dashed var(--term-border-dim);">
      <button type="button" ${btn} data-cam="${i}" data-ptz="left" title="Pan left">◀</button>
      <button type="button" ${btn} data-cam="${i}" data-ptz="up" title="Tilt up">▲</button>
      <button type="button" ${btn} data-cam="${i}" data-ptz="down" title="Tilt down">▼</button>
      <button type="button" ${btn} data-cam="${i}" data-ptz="right" title="Pan right">▶</button>
      ${(c.ptz.presets || []).map((p) => `<button type="button" ${btn} data-cam="${i}" data-ptz="preset" data-preset="${p.replace(/"/g, '&quot;')}">${p.trim()}</button>`).join('')}
    </div>`;
  const modes = c.live !== 'webrtc' ? '' : `
        <select class="form-input" data-cam="${i}" data-mode="1" title="Stream quality (remembered in this browser)" style="font-size:0.66rem; padding:0 2px; max-width:150px;">
          ${Object.entries(MODES).filter(([k]) => (k !== 'low' || (c.variants || {}).low) && (k !== 'hd' || (c.variants || {}).hd))
            .map(([k, label]) => `<option value="${k}" ${getMode(c.entity) === k ? 'selected' : ''}>${label}</option>`).join('')}
        </select>`;
  const refresh = c.live === 'snapshot'
    ? `<button type="button" ${btn} data-cam="${i}" data-refresh="1" title="${c.min_refresh_s ? `Battery camera: at most one new frame every ${Math.round(c.min_refresh_s / 60)} min` : 'New snapshot'}">⟳ Refresh</button>` : '';
  return `
    <div style="background:#000; border:1px solid var(--term-border-dim); border-radius:4px; overflow:hidden;">
      ${media}
      <div style="display:flex; justify-content:space-between; align-items:center; gap:6px; padding:4px 6px; font-size:0.72rem; background:var(--term-bg);">
        <span style="font-weight:bold; color:var(--term-text-bright);">${c.name}</span>
        <span style="display:flex; gap:6px; align-items:center;">
          <span id="cam-status-${i}" style="font-family:monospace; color:var(--term-text-muted);">connecting…</span>${refresh}${modes}
        </span>
      </div>${ptzHTML}${c.ptz ? `<div id="cam-patrol-${i}" style="padding:3px 6px; font-size:0.68rem; color:var(--term-text-muted); background:var(--term-bg);"></div>` : ''}
    </div>`;
}

// PTZ patrol line under each PTZ tile: last sweep, what it saw, next sweep or why it waits, thumbnails, "Sweep now".
const hhmm = (t) => (t ? new Date(t * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '–');

async function renderPatrol(cams) {
  let st;
  try { st = await (await fetch('/api/patrol/status')).json(); } catch (e) { return; }
  cams.forEach((c, i) => {
    const box = document.getElementById(`cam-patrol-${i}`);
    const p = (st.cameras || {})[c.entity];
    if (!box) return;
    if (!p) { box.textContent = ''; return; }
    const saw = (p.seen || []).flatMap((s) => s.seen);
    const when = p.busy ? 'sweeping now…' : `last ${hhmm(p.last)} · ${p.frames} frames${saw.length ? ` · saw ${[...new Set(saw)].join(', ')}` : ''}`;
    const next = !st.enabled ? 'patrol off' : p.skip ? p.skip : p.next ? `next ${hhmm(p.next)}` : '';
    const thumbs = Array.from({ length: p.frames || 0 }, (_, k) =>
      `<img src="/api/patrol/frame?entity=${encodeURIComponent(c.entity)}&i=${k}&t=${p.last}" loading="lazy" style="height:34px; border:1px solid var(--term-border-dim);" title="pan ${(p.first_pan || 0) + k * (p.step || 30)}°">`).join('');
    box.innerHTML = `🛡️ Patrol: ${when} · ${next}
      <button type="button" class="preset-btn" data-cam="${i}" data-patrol="1" style="padding:0 6px; font-size:0.66rem;" ${p.busy ? 'disabled' : ''}>Sweep now</button>
      ${thumbs ? `<div style="display:flex; gap:2px; overflow-x:auto; margin-top:3px;">${thumbs}</div>` : ''}`;
  });
}

async function startLive() {
  const grid = document.getElementById('presence-live-grid');
  if (!grid) return;
  stopLive();
  grid.innerHTML = '<div style="color:var(--term-text-muted); font-size:0.75rem;">Connecting to cameras…</div>';
  let cams = [];
  try {
    cams = (await (await fetch('/api/cameras/live')).json()).cameras || [];
  } catch (e) { /* shown below */ }
  if (!cams.length) {
    grid.innerHTML = '<div style="color:var(--term-text-muted); font-size:0.75rem;">No cameras configured (config.json camera_ui).</div>';
    return;
  }
  grid.innerHTML = cams.map(tileHTML).join('');
  grid.onclick = (e) => {
    const b = e.target.closest('[data-cam]');
    if (!b) return;
    const i = +b.dataset.cam;
    const status = document.getElementById(`cam-status-${i}`);
    if (b.dataset.refresh) return loadSnapshot(document.getElementById(`cam-img-${i}`), status, cams[i].entity, true);
    if (b.dataset.ptz) return ptz(cams[i].entity, b.dataset.ptz, b.dataset.preset || '', status);
    if (b.dataset.patrol) {
      postJSON('/api/patrol/run', { entity: cams[i].entity }).then((r) => {
        if (!r.ok) setStatus(status, `patrol: ${r.error}`);
        renderPatrol(cams);
      });
    }
  };
  renderPatrol(cams);
  patrolTimer = setInterval(() => renderPatrol(cams), 10000);
  grid.onchange = (e) => {
    const sel = e.target.closest('select[data-mode]');
    if (!sel) return;
    setMode(cams[+sel.dataset.cam].entity, sel.value);
    if (sel.value === 'webrtc' || sel.value === 'auto') { try { localStorage.removeItem(MP4_PREF); } catch (err) { /* ignore */ } }
    startLive();  // reopen the tiles with the new choice
  };
  cams.forEach((c, i) => {
    const status = document.getElementById(`cam-status-${i}`);
    if (c.live === 'snapshot') { loadSnapshot(document.getElementById(`cam-img-${i}`), status, c.entity, false); return; }
    const video = document.getElementById(`cam-vid-${i}`);
    video.addEventListener('click', () => { video.muted = !video.muted; });
    Promise.resolve(playTile(c, video, status)).catch((e) => setStatus(status, e.message));
  });
}

export function showPresenceTab(which) {
  const live = which === 'live';
  document.getElementById('presence-locations-grid').style.display = live ? 'none' : 'grid';
  document.getElementById('presence-live-grid').style.display = live ? 'grid' : 'none';
  document.getElementById('presence-tab-people').classList.toggle('active', !live);
  document.getElementById('presence-tab-live').classList.toggle('active', live);
  if (live) startLive(); else stopLive();  // only hold camera streams open while someone is watching
  if (!live) drawBoxes(document.getElementById('presence-locations-grid'));  // boxes need the grid visible to size
}

// Close the streams when the page is hidden (phone locked, tab switched); reopen on return
document.addEventListener('visibilitychange', () => {
  const liveOn = document.getElementById('presence-tab-live')?.classList.contains('active');
  if (!liveOn) return;
  if (document.hidden) stopLive(); else startLive();
});

export async function askCourage(query) {
  const inputEl = document.getElementById('courage-query-input');
  const q = query || (inputEl ? inputEl.value.trim() : '');
  if (!q) return;

  const respBox = document.getElementById('courage-response-box');
  const audioEl = document.getElementById('courage-audio-player');
  const btn = document.getElementById('courage-ask-btn');

  if (respBox) respBox.innerHTML = '<span style="color:var(--term-accent-gold);">Thinking with biting sarcasm...</span>';
  if (btn) btn.disabled = true;

  try {
    const res = await fetch('/api/presence/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: q })
    });
    const json = await res.json();
    if (json.ok && json.data) {
      const ans = json.data;
      if (respBox) {
        respBox.innerHTML = `
          <div style="color:var(--term-accent-gold); font-weight:bold; margin-bottom:4px;">💻 COURAGE COMPUTER:</div>
          <div style="font-size:0.88rem; line-height:1.4; color:var(--term-text-bright);">${ans.response}</div>
        `;
      }
      if (ans.audio_base64 && audioEl) {
        audioEl.src = `data:audio/wav;base64,${ans.audio_base64}`;
        audioEl.style.display = 'block';
        audioEl.play().catch(e => console.log('Audio autoplay prevented:', e));
      }
    } else {
      if (respBox) respBox.innerText = `Error: ${json.error || 'Failed to query'}`;
    }
  } catch (e) {
    if (respBox) respBox.innerText = `Error: ${e.message}`;
  } finally {
    if (btn) btn.disabled = false;
  }
}

export async function fetchIrrigationState() {
  try {
    const res = await fetch('/api/garden/status');
    const json = await res.json();
    if (!json.ok) return null;
    renderIrrigation(json.data);
    return json.data;
  } catch (e) {
    console.warn('Irrigation fetch failed:', e);
    return null;
  }
}

export function renderIrrigation(data) {
  if (!data) return;

  // Battery and rain delay badges
  const battEl = document.getElementById('irrigation-battery-badge');
  if (battEl) battEl.innerText = `🔋 Battery: ${data.battery_level}%`;

  const rainBtn = document.getElementById('irrigation-rain-delay-btn');
  if (rainBtn) {
    rainBtn.innerText = data.rain_delay_active ? '[🌧️ RAIN DELAY: ACTIVE]' : '[☀️ RAIN DELAY: OFF]';
    rainBtn.style.color = data.rain_delay_active ? '#38bdf8' : 'var(--term-text-bright)';
  }

  // Render 4 Zones
  const zonesGrid = document.getElementById('irrigation-zones-grid');
  if (zonesGrid && data.zones) {
    let html = '';
    for (const [key, z] of Object.entries(data.zones)) {
      const isOpen = z.is_open;
      const statusBadge = isOpen
        ? `<span style="color:#22c55e; font-weight:bold; background:rgba(34,197,94,0.15); padding:2px 6px; border-radius:3px; border:1px solid #16a34a;">🌊 OPEN</span>`
        : `<span style="color:var(--term-text-muted); background:rgba(255,255,255,0.05); padding:2px 6px; border-radius:3px; border:1px solid var(--term-border-dim);">CLOSED</span>`;

      html += `
        <div style="background:var(--term-bg); border:1px solid var(--term-border-dim); border-radius:4px; padding:10px;">
          <div style="display:flex; justify-content:space-between; align-items:center;">
            <span style="font-weight:bold; color:var(--term-text-bright);">${z.name}</span>
            ${statusBadge}
          </div>
          <div style="font-size:0.72rem; color:var(--term-text-muted); margin:4px 0;">${z.description}</div>
          <div style="font-size:0.70rem; color:var(--term-text-muted); font-family:monospace; margin-bottom:8px;">
            Last: ${z.last_watered ? z.last_watered.replace('T', ' ').substring(0, 16) : 'None'}
          </div>
          <div style="display:flex; gap:6px;">
            <button type="button" class="term-cmd-btn" style="flex:1; background:var(--term-accent-blue); color:#fff; font-weight:bold;" onclick="window.controlIrrigationZone('${key}', 'open', 15)">
              [OPEN 15m]
            </button>
            <button type="button" class="term-cmd-btn" style="color:var(--term-accent-red); font-weight:bold;" onclick="window.controlIrrigationZone('${key}', 'close')">
              [CLOSE]
            </button>
          </div>
        </div>
      `;
    }
    zonesGrid.innerHTML = html;
  }
}

export async function controlIrrigationZone(zone, action, durationMinutes = 15) {
  try {
    const res = await fetch('/api/garden/zone/control', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ zone, action, duration_minutes: durationMinutes })
    });
    const json = await res.json();
    if (json.ok) {
      fetchIrrigationState();
    } else {
      alert(`Zone command error: ${json.error || 'Failed'}`);
    }
  } catch (e) {
    alert(`Zone command error: ${e.message}`);
  }
}

export async function toggleRainDelay() {
  try {
    const cur = document.getElementById('irrigation-rain-delay-btn')?.innerText.includes('ACTIVE');
    const res = await fetch('/api/garden/rain_delay', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: !cur })
    });
    const json = await res.json();
    if (json.ok) {
      fetchIrrigationState();
    }
  } catch (e) {
    console.error('Rain delay error:', e);
  }
}

export async function emergencyCloseAll() {
  try {
    const res = await fetch('/api/garden/close_all', { method: 'POST' });
    const json = await res.json();
    if (json.ok) {
      fetchIrrigationState();
    }
  } catch (e) {
    console.error('Emergency close error:', e);
  }
}

// Bind to window for HTML onclick handlers
window.askCourage = askCourage;
window.controlIrrigationZone = controlIrrigationZone;
window.toggleRainDelay = toggleRainDelay;
window.emergencyCloseAll = emergencyCloseAll;
window.fetchPresenceState = fetchPresenceState;
window.fetchIrrigationState = fetchIrrigationState;
window.showPresenceTab = showPresenceTab;
