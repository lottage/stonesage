/*
 * One request per endpoint (loaded right after auth.js, as a plain script).
 *
 * Several parts of the page ask for the same status endpoint on their own timers or at start (the engine dashboard, the
 * fleet matrix, the top bar's ping, the model pickers), so one cycle fetched /api/engine/state/all three or four times.
 * This wraps fetch: GET requests to the status endpoints below that are still in flight, or answered less than TTL_MS ago,
 * share one network call. Every caller still gets its own Response to read. Nothing else is touched (POSTs, streams, files,
 * or any path not listed), and a `?fresh=1` request is a different URL so it always goes out.
 */
(function () {
  'use strict';
  var TTL_MS = 1500;
  var SHARED = [
    '/api/engine/state/all', '/api/health/all', '/api/cluster/health', '/api/cluster/models', '/api/cluster/modes',
    '/api/harness/instances', '/api/proxmox/nodes', '/api/system/profile', '/api/ui/features',
    '/api/ha/status', '/api/ha/dashboard', '/api/presence/status', '/api/garden/status'
  ];
  var realFetch = window.fetch;
  var entries = {};            // url -> { at: ms when the answer came (0 = in flight), promise: Promise<Response> }

  function urlOf(input) {
    try { return new URL(typeof input === 'string' ? input : input.url, location.href); } catch (e) { return null; }
  }

  window.fetch = function (input, init) {
    var method = ((init && init.method) || (input && input.method) || 'GET').toUpperCase();
    var u = urlOf(input);
    if (method !== 'GET' || !u || u.origin !== location.origin || SHARED.indexOf(u.pathname) < 0 || (init && init.signal)) {
      return realFetch.apply(this, arguments);
    }
    var key = u.pathname + u.search;
    var hit = entries[key];
    if (hit && (hit.at === 0 || Date.now() - hit.at < TTL_MS)) {
      return hit.promise.then(function (res) { return res.clone(); });
    }
    var entry = { at: 0, promise: null };
    entry.promise = realFetch.apply(this, arguments).then(function (res) {
      entry.at = Date.now();
      return res;
    }, function (err) {
      delete entries[key];
      throw err;
    });
    entries[key] = entry;
    return entry.promise.then(function (res) { return res.clone(); });
  };
  window.__sharedFetchEntries = entries;     // for the headless test
})();
