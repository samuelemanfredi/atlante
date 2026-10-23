// Atlante Musica: copia offline dell'app (la musica è salvata a parte, in IndexedDB).
const CACHE = 'atlante-musica-v3';
const FILES = ['./', './index.html', './manifest.webmanifest', './icon-192.png', './icon-512.png', './icon-maskable-512.png', './apple-touch-icon.png'];
const ATTESA = 3000;   // se il Mac non risponde entro 3 s l'app si apre dalla copia offline
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
    // prima la rete (versione nuova), ma con un limite d'attesa: un Mac in stop o fuori rete può tenere
    // la connessione aperta senza rispondere, e la pagina resterebbe bianca. La rete aggiorna comunque la copia.
    const net = fetch(r);
    e.waitUntil(net.then(res => { if (res.ok) { const cp = res.clone(); return caches.open(CACHE).then(c => c.put('./index.html', cp)); } }).catch(() => {}));
    const copia = () => caches.match('./index.html').then(hit => hit || net);
    e.respondWith(new Promise(ok => {
      let fatto = false;
      const usa = p => { if (!fatto) { fatto = true; clearTimeout(tm); ok(p); } };
      const tm = setTimeout(() => usa(copia()), ATTESA);
      net.then(res => usa(res), () => usa(copia()));
    }));
    return;
  }
  e.respondWith(caches.match(r).then(hit => hit || fetch(r).then(res => {
    if (res.ok) { const cp = res.clone(); caches.open(CACHE).then(c => c.put(r, cp)); }
    return res;
  })));
});
