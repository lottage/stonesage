/**
 * StoneSage Service Worker
 * Enables PWA offline shell and background Web Push notification alerts.
 */

const CACHE_NAME = 'stonesage-v4.3.2-push';
const STATIC_ASSETS = [
  '/',
  '/index.html',
  '/style.css',
  '/style.css?v=4.0.4',
  '/vendor/xterm/xterm.css',
  '/vendor/xterm/xterm.css?v=4.0.4',
  '/vendor/xterm/xterm.js',
  '/vendor/xterm/xterm.js?v=4.0.4',
  '/vendor/xterm/xterm-addon-fit.js',
  '/vendor/xterm/xterm-addon-fit.js?v=4.0.4',
  '/vendor/ace/ace.js',
  '/vendor/ace/ext-language_tools.js',
  '/vendor/ace/ext-searchbox.js',
  '/js/app.js',
  '/js/app.js?v=4.0.4',
  '/js/state.js',
  '/js/state.js?v=4.0.4',
  '/js/chat.js',
  '/js/convo_audio.js',
  '/js/chat.js?v=4.0.4',
  '/js/terminal.js',
  '/js/terminal.js?v=4.0.4',
  '/js/harness.js',
  '/js/harness.js?v=4.0.4',
  '/js/fleet.js',
  '/js/fleet.js?v=4.0.4',
  '/js/agent_dna.js',
  '/js/agent_dna.js?v=4.0.4',
  '/js/presence_garden.js',
  '/js/presence_garden.js?v=4.0.4',
  '/js/studio3d.js',
  '/js/studio3d.js?v=4.0.4',
  '/js/workstation_ace.js',
  '/js/workstation_ace.js?v=4.0.4',
  '/js/engine_studio.js',
  '/js/engine_studio.js?v=4.0.4',
  '/manifest.webmanifest',
  '/icon.svg'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(STATIC_ASSETS);
    })
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))
      );
    })
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  // Never cache API or websocket requests
  if (event.request.url.includes('/api/') || event.request.url.includes('/ws')) {
    event.respondWith(fetch(event.request));
    return;
  }

  // Network-first strategy with cache fallback (ignoring query search if needed)
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        if (response && response.status === 200 && response.type === 'basic') {
          const responseToCache = response.clone();
          caches.open(CACHE_NAME).then((cache) => {
            cache.put(event.request, responseToCache);
          });
        }
        return response;
      })
      .catch(() => {
        return caches.match(event.request).then((matched) => {
          return matched || caches.match(event.request, { ignoreSearch: true });
        });
      })
  );
});

// Push Notifications
self.addEventListener('push', (event) => {
  let data = { title: 'StoneSage Homelab Alert', body: 'Cluster or automation event detected.' };
  if (event.data) {
    try {
      data = event.data.json();
    } catch (e) {
      data.body = event.data.text();
    }
  }

  event.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body,
      icon: '/icon.svg',
      badge: '/icon.svg',
      vibrate: [100, 50, 100],
      data: {
        dateOfArrival: Date.now(),
        primaryKey: '1'
      }
    })
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  event.waitUntil(
    clients.matchAll({ type: 'window' }).then((clientList) => {
      for (const client of clientList) {
        if (client.url === '/' && 'focus' in client) {
          return client.focus();
        }
      }
      if (clients.openWindow) {
        return clients.openWindow('/');
      }
    })
  );
});
