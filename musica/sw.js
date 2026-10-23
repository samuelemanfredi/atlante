// Atlante Musica: copia offline dell'app (la musica è salvata a parte, in IndexedDB).
const CACHE = 'atlante-musica-v3';
const FILES = ['./', './index.html', './manifest.webmanifest', './icon-192.png', './icon-512.png', './icon-maskable-512.png', './apple-touch-icon.png'];
const ATTESA = 3000;   // se il Mac non risponde entro 3 s l'app si apre dalla copia offline
// 'reload': la copia offline si prende dal Mac, non da una vecchia copia nella cache del browser
self.addEventListener('install', e => { e.waitUntil(caches.open(CACHE).then(c => c.addAll(FILES.map(f => new Request(f, {cache:'reload'})))).then(() => self.skipWaiting())); });
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k.startsWith('atlante-musica-') && k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const r = e.request;
  const u = new URL(r.url);
  // la musica del Mac (catalogo, brani, copertine, aggiorna) va sempre chiesta al Mac: niente /api/ nella copia offline
  if (r.method !== 'GET' || u.origin !== location.origin || u.pathname.indexOf('/api/') >= 0) return;
  if (r.mode === 'navigate') {
    // solo la pagina dell'app (./ o ./index.html, anche con ?…): le altre pagine dello stesso indirizzo (un'icona, una cartella)
    // passano senza toccare la copia offline, che altrimenti verrebbe sostituita da loro
    const base = new URL('./', self.registration.scope).pathname;
    if (u.pathname !== base && u.pathname !== base + 'index.html') return;
    // prima la rete (versione nuova), ma con un limite d'attesa: un Mac in stop o fuori rete può tenere
    // la connessione aperta senza rispondere, e la pagina resterebbe bianca. La rete aggiorna comunque la copia.
    // 'no-cache': si chiede sempre al Mac se la pagina è cambiata (altrimenti il browser può riusare per giorni una versione vecchia)
    let req = r; try { req = new Request(r, {cache:'no-cache'}); } catch (err) {}
    const net = fetch(req);
    // una risposta che non è la pagina (errore del Mac, 502 di tailscale serve con il server della musica fermo) non è l'app
    const buona = res => res.ok && /text\/html/i.test(res.headers.get('content-type') || '');
    e.waitUntil(net.then(res => { if (buona(res) && !res.redirected) { const cp = res.clone(); return caches.open(CACHE).then(c => c.put('./index.html', cp)); } }).catch(() => {}));
    const copia = () => caches.match('./index.html').then(hit => hit || net);
    e.respondWith(new Promise(ok => {
      let fatto = false;
      const usa = p => { if (!fatto) { fatto = true; clearTimeout(tm); ok(p); } };
      const tm = setTimeout(() => usa(copia()), ATTESA);
      net.then(res => usa(buona(res) || res.type === 'opaqueredirect' ? res : copia()), () => usa(copia()));
    }));
    return;
  }
  e.respondWith(caches.match(r).then(hit => hit || fetch(r).then(res => {
    if (res.ok) { const cp = res.clone(); caches.open(CACHE).then(c => c.put(r, cp)); }
    return res;
  })));
});
