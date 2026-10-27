// Atlante Musica: copia offline dell'app (la musica è salvata a parte, in IndexedDB).
const CACHE = 'atlante-musica-v5';
const FILES = ['./', './index.html', './manifest.webmanifest', './icon-192.png?v=2', './icon-512.png?v=2', './icon-maskable-512.png?v=2', './apple-touch-icon.png?v=2'];
// moduli della scheda Altro: si mettono in copia uno per uno (se uno manca, gli altri restano)
const MODULI = ['x/kit.css', 'x/kit.js'].concat(['podcast', 'libri', 'video', 'live', 'naviga', 'benessere', 'strumenti', 'studio'].map(m => 'x/' + m + '.html'));
// «Rete e VPN» serve proprio quando il Mac non risponde (Tailscale spento fuori casa): deve aprirsi anche offline
MODULI.push('x/rete.html');
const ATTESA = 3000;   // se il Mac non risponde entro 3 s l'app si apre dalla copia offline
// 'reload': la copia offline si prende dal Mac, non da una vecchia copia nella cache del browser
self.addEventListener('install', e => { e.waitUntil(caches.open(CACHE).then(c => c.addAll(FILES.map(f => new Request(f, {cache:'reload'})))
  .then(() => Promise.all(MODULI.map(f => c.add(new Request(f, {cache:'reload'})).catch(() => {}))))).then(() => self.skipWaiting())); });
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
    // pagine dei moduli (x/…): prima la rete, se il Mac non risponde la copia offline
    if (u.pathname.startsWith(base + 'x/') && u.pathname.endsWith('.html')) {
      const k = new Request(u.origin + u.pathname);
      e.respondWith(fetch(r).then(res => { if (res.ok) { const cp = res.clone(); caches.open(CACHE).then(c => c.put(k, cp)); } return res; })
        .catch(() => caches.match(k).then(hit => hit || Response.error())));
      return;
    }
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
  // file comuni dei moduli (x/kit.*): prima la rete, così un aggiornamento arriva insieme alle pagine
  if (u.pathname.startsWith(new URL('./x/', self.registration.scope).pathname)) {
    e.respondWith(fetch(r).then(res => { if (res.ok) { const cp = res.clone(); caches.open(CACHE).then(c => c.put(r, cp)); } return res; })
      .catch(() => caches.match(r).then(hit => hit || Response.error())));
    return;
  }
  e.respondWith(caches.match(r).then(hit => hit || fetch(r).then(res => {
    if (res.ok) { const cp = res.clone(); caches.open(CACHE).then(c => c.put(r, cp)); }
    return res;
  })));
});

/* ---------- chat (fase 5a): notifiche senza contenuto ----------
   La notifica arriva vuota (il Mac mini non conosce i testi): qui si chiede al Mac mini chi ha scritto, con il biscotto
   di questo telefono e l'id del suo dispositivo della chat (in IndexedDB, «ax-chat»), e si mostra «Nuovo messaggio da
   …». Safari vuole una notifica per ogni push: se il Mac mini non risponde, se ne mostra una generica.
   Sull'iPhone le notifiche ci sono solo con l'app sulla schermata Home (iOS 16.4 o successivo). */
function chatDispositivo() {
  return new Promise(ok => {
    let r;
    try { r = indexedDB.open('ax-chat'); } catch (e) { return ok(''); }
    r.onupgradeneeded = () => { try { r.transaction.abort(); } catch (e) {} };   // la chat qui non è mai stata aperta: non la si crea vuota
    r.onerror = () => ok('');
    r.onsuccess = () => {
      const db = r.result;
      try {
        const q = db.transaction('io').objectStore('io').get('disp');
        q.onsuccess = () => { db.close(); ok(typeof q.result === 'string' ? q.result : ''); };
        q.onerror = () => { db.close(); ok(''); };
      } catch (e) { db.close(); ok(''); }
    };
  });
}
self.addEventListener('push', e => {
  e.waitUntil((async () => {
    let avvisi = [];
    try {
      const disp = await chatDispositivo();
      if (disp) {
        const r = await fetch(new URL('api/extra/chat/avvisi?disp=' + encodeURIComponent(disp), self.registration.scope), {credentials:'same-origin', cache:'no-store'});
        if (r.ok) avvisi = ((await r.json()).avvisi || []).filter(a => a && typeof a.nome === 'string');
      }
    } catch (err) {}
    const icona = new URL('icon-192.png?v=2', self.registration.scope).href;
    if (!avvisi.length) return self.registration.showNotification('Atlante Musica', {body:'Nuovo messaggio', tag:'chat', icon:icona, data:{conv:''}});
    return Promise.all(avvisi.slice(0, 4).map(a => self.registration.showNotification('Nuovo messaggio da ' + a.nome, {
      body:a.gruppo ? 'Nel gruppo «' + a.gruppo + '»' : (a.n > 1 ? a.n + ' messaggi nuovi' : 'Tocca per leggerlo'), tag:'chat-' + a.conv, icon:icona, data:{conv:a.conv}})));
  })());
});
self.addEventListener('notificationclick', e => {
  e.notification.close();
  const conv = (e.notification.data || {}).conv || '';
  const base = new URL('./', self.registration.scope);
  e.waitUntil(self.clients.matchAll({type:'window', includeUncontrolled:true}).then(L => {
    const w = L.find(c => c.frameType !== 'nested' && new URL(c.url).pathname.startsWith(base.pathname));
    if (w) { w.postMessage({tipo:'apri-chat', conv}); return w.focus(); }
    return self.clients.openWindow(new URL('./?x=chat' + (conv ? '&c=' + encodeURIComponent(conv) : ''), base).href);
  }));
});
