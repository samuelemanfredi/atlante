// Atlante Musica: copia offline dell'app (la musica è salvata a parte, in IndexedDB).
const CACHE = 'atlante-musica-v5';
const FILES = ['./', './index.html', './manifest.webmanifest', './icon-192.png', './icon-512.png', './icon-maskable-512.png', './apple-touch-icon.png'];
const ATTESA = 4000;   // ms: se il Mac non risponde entro questo tempo, si apre la copia offline
self.addEventListener('install', e => { e.waitUntil(caches.open(CACHE).then(c => c.addAll(FILES)).then(() => self.skipWaiting())); });
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k.startsWith('atlante-musica-') && k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const r = e.request;
  const u = new URL(r.url);
  // la musica del Mac (catalogo, brani, copertine, aggiorna) va sempre chiesta al Mac: niente /api/ nella copia offline
  if (r.method !== 'GET' || u.origin !== location.origin || u.pathname.indexOf('/api/') >= 0) return;
  if (r.mode === 'navigate') {
    // la pagina: dal Mac, se risponde bene ed entro ATTESA. Altrimenti (Mac spento o addormentato, rete debole,
    // Benessere fermo: 502) la copia offline, così i brani già sull'iPhone si ascoltano subito.
    // La risposta del Mac, anche se arriva tardi, aggiorna la copia per la volta dopo.
    const rete = fetch(r);
    e.waitUntil(rete.then(res => { if (res.ok) { const cp = res.clone(); return caches.open(CACHE).then(c => c.put('./index.html', cp)); } }).catch(() => {}));
    e.respondWith(caches.match('./index.html').then(copia => {
      if (!copia) return rete;   // prima apertura: c'è solo il Mac
      const buona = rete.then(res => res.ok || res.type === 'opaqueredirect' ? res : copia, () => copia);
      return Promise.race([buona, new Promise(ok => setTimeout(() => ok(copia), ATTESA))]);
    }));
    return;
  }
  e.respondWith(caches.match(r).then(hit => hit || fetch(r).then(res => {
    if (res.ok) { const cp = res.clone(); caches.open(CACHE).then(c => c.put(r, cp)); }
    return res;
  })));
});
