// Atlante Musica: copia offline dell'app (la musica è salvata a parte, in IndexedDB).
const CACHE = 'atlante-musica-v6';   // v6: «Condividi l'app» (x/inviti.html) nella copia; le app già installate la rinnovano da sole
const FILES = ['./', './index.html', './manifest.webmanifest', './icon-192.png?v=2', './icon-512.png?v=2', './icon-maskable-512.png?v=2', './apple-touch-icon.png?v=2'];
// moduli della scheda Altro: si mettono in copia uno per uno (se uno manca, gli altri restano)
const MODULI = ['x/kit.css', 'x/kit.js'].concat(['podcast', 'libri', 'video', 'live', 'naviga', 'benessere', 'strumenti', 'studio', 'inviti'].map(m => 'x/' + m + '.html'));
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

/* ---------- chat (fase 5a) e chiamate (fasi 5b e 5c): notifiche senza contenuto ----------
   La notifica arriva vuota (il Mac mini non conosce i testi): qui si chiede al Mac mini (chat/avvisi) chi ha scritto e
   quali chiamate ci sono, con il biscotto di questo telefono e l'id del suo dispositivo della chat (in IndexedDB,
   «ax-chat»), e si mostra «Nuovo messaggio da …», «Chiamata da …», «Videochiamata da …» o «Chiamata persa da …».
   Ogni chiamata ha una notifica sola (etichetta «chiamata-<id>»): «persa» prende il posto di «arriva», e quando si è
   risposto o rifiutato, anche da un altro dispositivo («chiusa»), la notifica si toglie. Toccandola si apre l'app: se
   squilla ancora, la chiamata compare da sola.
   Safari vuole una notifica per ogni push (dopo tre push senza notifica smette di mandarle): se il Mac mini non risponde
   se ne mostra una generica; se risponde ma non c'è niente di nuovo, se ne mostra una che si chiude subito. Mai un
   «Nuovo messaggio» senza chi l'ha scritto: il push può essere anche una chiamata.
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
// le chiamate già mostrate («id istante» → quando), in una cache a parte che gli aggiornamenti dell'app non toccano: una
// chiamata persa tolta a mano non torna a ogni messaggio dell'ora dopo (il Mac mini la ricorda per un'ora)
const VISTE = 'atlante-chiamate';
async function chiamateViste() {
  try {
    const r = await (await caches.open(VISTE)).match('viste');
    const v = r ? await r.json() : {};
    return v && typeof v === 'object' ? v : {};
  } catch (e) { return {}; }
}
async function segnaViste(v) {
  try { await (await caches.open(VISTE)).put('viste', new Response(JSON.stringify(v), {headers:{'Content-Type':'application/json'}})); } catch (e) {}
}
async function chatNotifiche() {
  let avvisi = [], chiamate = [], risposto = false;
  try {
    const disp = await chatDispositivo();
    if (disp) {
      const r = await fetch(new URL('api/extra/chat/avvisi?disp=' + encodeURIComponent(disp), self.registration.scope), {credentials:'same-origin', cache:'no-store'});
      if (r.ok) {
        const j = await r.json();
        avvisi = (j.avvisi || []).filter(a => a && typeof a.nome === 'string');
        chiamate = (j.chiamate || []).filter(c => c && c.id && typeof c.nome === 'string' && ['arriva', 'persa', 'chiusa'].indexOf(c.evento) >= 0);
        risposto = true;
      }
    }
  } catch (err) {}
  const reg = self.registration, icona = new URL('icon-192.png?v=2', reg.scope).href;
  // il Mac mini non risponde: non si sa se è un messaggio o una chiamata. Il tocco apre la chat (una chiamata che squilla
  // ancora compare lo stesso, sopra tutto)
  if (!risposto) return reg.showNotification('Atlante Musica', {body:'Un messaggio o una chiamata: tocca per aprire', tag:'chat', icon:icona, data:{conv:''}});
  const mostra = avvisi.slice(0, 4).map(a => reg.showNotification('Nuovo messaggio da ' + a.nome, {
    body:a.gruppo ? 'Nel gruppo «' + a.gruppo + '»' : (a.n > 1 ? a.n + ' messaggi nuovi' : 'Tocca per leggerlo'), tag:'chat-' + a.conv, icon:icona, data:{conv:a.conv}}));
  const viste = await chiamateViste(), ora = Date.now(), chiuse = [];
  for (const k in viste) if (!(ora - viste[k] < 2 * 3600e3)) delete viste[k];   // dopo due ore il Mac mini non le ha più
  for (const c of chiamate) {
    const tag = 'chiamata-' + c.id, tipo = c.video ? 'Videochiamata' : 'Chiamata', data = {chiamata:String(c.id)};
    if (c.evento === 'chiusa') { chiuse.push({tag, titolo:tipo + ' da ' + c.nome, data}); continue; }
    const k = c.id + ' ' + c.t;   // ogni avviso del Mac mini ha il suo istante: «arriva» e poi «persa» sono due avvisi
    if (viste[k]) continue;   // già mostrato (e forse tolto a mano): non si ripete
    viste[k] = ora;
    mostra.push(reg.showNotification(c.evento === 'persa' ? 'Chiamata persa da ' + c.nome : tipo + ' da ' + c.nome, {
      body:c.evento === 'persa' ? (c.video ? 'Videochiamata senza risposta' : 'Chiamata vocale senza risposta') : 'Tocca per rispondere', tag, icon:icona, data}));
  }
  // niente da mostrare (già letto nell'app, chiamata finita o già vista): Safari vuole lo stesso una notifica. Prende il
  // posto di quella da togliere (o della generica) e si chiude subito, insieme alle chiamate chiuse
  if (!mostra.length) {
    const x = chiuse[0] || {tag:'chat', titolo:'Atlante Musica', data:{conv:''}};
    await reg.showNotification(x.titolo, {body:chiuse.length ? 'Non squilla più' : 'Niente di nuovo', tag:x.tag, icon:icona, data:x.data, silent:true});
    if (!chiuse.length) chiuse.push(x);
  }
  await Promise.all(mostra);
  await segnaViste(viste);
  // risposto o rifiutato (anche da un altro dispositivo): via la notifica che squillava
  for (const x of chiuse) for (const n of await reg.getNotifications({tag:x.tag})) n.close();
}
// tocco su una notifica: una chiamata apre l'app (se squilla ancora, la chiamata compare da sola), un messaggio la chat
async function apriNotifica(d) {
  const conv = d.conv || '';
  const base = new URL('./', self.registration.scope);
  const L = await self.clients.matchAll({type:'window', includeUncontrolled:true});
  const w = L.find(c => c.frameType !== 'nested' && new URL(c.url).pathname.startsWith(base.pathname));
  if (d.chiamata) return w ? w.focus() : self.clients.openWindow(base.href);
  if (w) { w.postMessage({tipo:'apri-chat', conv}); return w.focus(); }
  return self.clients.openWindow(new URL('./?x=chat' + (conv ? '&c=' + encodeURIComponent(conv) : ''), base).href);
}
self.addEventListener('push', e => { e.waitUntil(chatNotifiche()); });
self.addEventListener('notificationclick', e => {
  e.notification.close();
  e.waitUntil(apriNotifica(e.notification.data || {}));
});
