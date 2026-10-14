/*
 * Tells StoneSage that a person is using this page (loaded right after net.js, as a plain script).
 *
 * Background work (the garden's sessions) sits on Computer's GPU only while nobody is at the cockpit. A tab that is merely open
 * polls all day, so polling proves nothing: this posts /api/activity/heartbeat on real input (key, tap, pointer, scroll) and when
 * the tab becomes visible again, at most once a minute, and never while the tab is hidden. A failed beat is ignored.
 */
(function () {
  'use strict';
  var MIN_GAP_MS = 60 * 1000;
  var last = 0;

  function beat() {
    if (document.hidden) return;
    var now = Date.now();
    if (now - last < MIN_GAP_MS) return;
    last = now;
    try {
      fetch('/api/activity/heartbeat', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: '{}' })
        .catch(function () {});
    } catch (e) { /* no network, no beat */ }
  }

  ['keydown', 'pointerdown', 'touchstart', 'wheel'].forEach(function (name) {
    window.addEventListener(name, beat, { passive: true, capture: true });
  });
  document.addEventListener('visibilitychange', function () { if (!document.hidden) beat(); });
})();
