const CACHE_NAME = 'filadelfia-connect-v4';
const STATIC_ASSETS = [
  '/static/base.css', '/static/user_base.css', '/static/pwa.css?v=1', '/static/pwa.js?v=1',
];

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE_NAME)
    .then(cache => cache.addAll(STATIC_ASSETS))
    .then(() => self.skipWaiting()));
});
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(names => Promise.all(
    names.filter(name => name.startsWith('filadelfia-connect-') && name !== CACHE_NAME)
      .map(name => caches.delete(name))
  )).then(() => self.clients.claim()));
});
self.addEventListener('fetch', event => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== self.location.origin) return;
  // Authenticated HTML, APIs and media must never be stored in the PWA cache.
  if (request.mode === 'navigate') {
    event.respondWith(fetch(request).catch(() => new Response(
      '<!doctype html><html lang="pt-br"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>Connect — sem conexão</title><body style="margin:0;min-height:100vh;display:grid;place-items:center;background:#f7f1ef;font-family:system-ui"><main style="padding:2rem;text-align:center"><h1>Você está sem conexão</h1><p>Conecte-se à internet para continuar no Connect.</p><button onclick="location.reload()" style="padding:1rem">Tentar novamente</button></main></body></html>',
      { status: 503, headers: { 'Content-Type': 'text/html; charset=utf-8' } }
    )));
    return;
  }
  if (!STATIC_ASSETS.includes(url.pathname + url.search)) return;
  event.respondWith(fetch(request).then(response => {
    if (response.ok) event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.put(request, response.clone())));
    return response;
  }).catch(() => caches.match(request).then(cached => cached || Response.error())));
});
