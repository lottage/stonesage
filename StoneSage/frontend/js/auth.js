/*
 * Login plumbing for every page of the cockpit (loaded first, as a plain script).
 *
 * The server answers 401 + "X-StoneSage-Auth: login" to a request without a valid session. Any fetch to this site that gets
 * that answer sends the person to /login.html, and back to the page they were on once they have entered the key. The session
 * itself is an HttpOnly cookie the browser sends on its own (same origin), so nothing else in the page has to know about it.
 */
(function () {
  'use strict';
  var LOGIN = '/login.html';
  if (location.pathname === LOGIN) return;

  var going = false;
  function toLogin() {
    if (going) return;
    going = true;
    var next = location.pathname + location.search + location.hash;
    location.href = LOGIN + '?next=' + encodeURIComponent(next);
  }

  var realFetch = window.fetch;
  window.fetch = function (input, init) {
    return realFetch.apply(this, arguments).then(function (res) {
      try {
        var url = new URL(typeof input === 'string' ? input : input.url, location.href);
        if (res.status === 401 && url.origin === location.origin && res.headers.get('X-StoneSage-Auth') === 'login') toLogin();
      } catch (e) { /* not a URL we can read: leave the answer alone */ }
      return res;
    });
  };

  // For a button anywhere in the UI: end this browser's session and show the login page.
  window.stonesageLogout = function () {
    return realFetch('/api/auth/logout', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
      .then(function () { going = true; location.href = LOGIN; });
  };

  // EventSource has no status to read: when one fails, ask the server whether the session is still good.
  if (window.EventSource) {
    var RealES = window.EventSource;
    window.EventSource = function (url, opts) {
      var es = new RealES(url, opts);
      es.addEventListener('error', function () {
        realFetch('/api/auth/whoami', { cache: 'no-store' }).then(function (r) { if (r.status === 401) toLogin(); }).catch(function () {});
      });
      return es;
    };
    window.EventSource.prototype = RealES.prototype;
  }
})();
