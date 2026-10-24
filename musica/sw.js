// Atlante Musica: copia offline dell'app e dei moduli della scheda Altro (la musica è salvata a parte, in IndexedDB).
const CACHE = 'atlante-musica-v6';
const FILES = ['./', './index.html', './manifest.webmanifest', './icon-192.png', './icon-512.png', './icon-maskable-512.png', './apple-touch-icon.png'];
// percorsi di FILES: solo loro si servono dalla copia. Il resto dello stesso indirizzo (con l'app alla radice, per esempio
// Benessere su /benessere/ aperto nel riquadro di Altro) passa senza copia: altrimenti resterebbe per sempre la versione vecchia
const MIEI = new Set(FILES.map(f => new URL(f, self.registration.scope).pathname));
// moduli della scheda Altro: si mettono in copia uno per uno (se uno manca, gli altri restano)
const MODULI = ['x/kit.css', 'x/kit.js'].concat(['podcast', 'libri', 'video', 'live', 'naviga', 'benessere', 'strumenti', 'studio'].map(m => 'x/' + m + '.html'));
const ATTESA = 3000;   // se il Mac non risponde entro 3 s l'app (o il modulo) si apre dalla copia offline
// una risposta che non si usa (errore del Mac, 404, oppure arrivata quando si era già data la copia) si chiude: lasciata a metà
// terrebbe occupata una connessione col Mac, e alla sesta il browser non manda più niente (installazione ferma per sempre)
const scarta = res => { try { if (res && res.body && !res.bodyUsed) res.body.cancel().catch(() => {}); } catch (err) {} };
// 'reload': la copia offline si prende dal Mac, non da una vecchia copia nella cache del browser
const modulo = (c, f) => fetch(new Request(f, {cache:'reload'})).then(res => res.ok ? c.put(f, res) : scarta(res)).catch(() => {});
self.addEventListener('install', e => { e.waitUntil(caches.open(CACHE).then(c => c.addAll(FILES.map(f => new Request(f, {cache:'reload'})))
  .then(() => Promise.all(MODULI.map(f => modulo(c, f))))).then(() => self.skipWaiting())); });
// pagina di un'app di prima degli alias (copia v1-v4: quella di main) rimasta aperta: l'iPhone la tiene in memoria per ore e,
// con gli id nuovi del Mac, crederebbe spariti preferiti, playlist e coda (e un Play toglierebbe il brano dalla coda). La si
// riapre con l'app nuova, che sposta tutto sugli id nuovi. Le pagine dell'app nuova rispondono al messaggio e restano com'erano.
// navigate() non si aspetta: la navigazione passa da questo service worker, che finisce di attivarsi solo dopo
const nuova = c => new Promise(ok => {
  const ch = new MessageChannel(), tm = setTimeout(() => ok(false), 3000);
  ch.port1.onmessage = () => { clearTimeout(tm); ok(true); };
  try { c.postMessage('atlante-musica?', [ch.port2]); } catch (err) { clearTimeout(tm); ok(true); }
});
function riapriVecchie() {
  const base = new URL('./', self.registration.scope);
  return self.clients.matchAll({type:'window'}).then(cs => Promise.all(cs.map(c => {
    const p = new URL(c.url).pathname;
    if (c.frameType === 'nested' || (p !== base.pathname && p !== base.pathname + 'index.html')) return null;
    return nuova(c).then(n => { if (!n) try { c.navigate(base.href).catch(() => {}); } catch (err) {} });   // senza «?x=…»: un link non si rimanda
  })));
}
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks => {
    const vecchie = ks.filter(k => k.startsWith('atlante-musica-') && k !== CACHE);
    const primaDegliAlias = vecchie.some(k => /^atlante-musica-v[1-4]$/.test(k));
    return Promise.all(vecchie.map(k => caches.delete(k))).then(() => self.clients.claim()).then(() => primaDegliAlias && riapriVecchie());
  }));
});
// prima la rete (versione nuova), ma con un limite d'attesa: un Mac in stop o fuori rete può tenere la connessione aperta
// senza rispondere, e la pagina resterebbe bianca. La rete aggiorna comunque la copia (salvata sotto `chiave`), ma solo con
// una risposta `buona`: un errore del Mac o il 502 di tailscale serve con il server fermo non devono sostituire la copia.
// 'no-cache': si chiede sempre al Mac se il file è cambiato (altrimenti il browser può riusare per giorni una versione vecchia)
function reteOCopia(e, r, chiave, buona) {
  let req = r; try { req = new Request(r, {cache:'no-cache'}); } catch (err) {}
  const net = fetch(req);
  e.waitUntil(net.then(res => { if (buona(res) && !res.redirected) { const cp = res.clone(); return caches.open(CACHE).then(c => c.put(chiave, cp)); } }).catch(() => {}));
  e.respondWith(new Promise(ok => {
    let fatto = false;
    const usa = x => { if (fatto || !x) return false; fatto = true; clearTimeout(tm); ok(x); return true; };
    const tm = setTimeout(() => caches.match(chiave).then(usa), ATTESA);   // senza copia si aspetta la rete
    net.then(res => {
      if (buona(res) || res.type === 'opaqueredirect') { if (!usa(res)) scarta(res); return; }
      caches.match(chiave).then(hit => { if (usa(hit) || !usa(res)) scarta(res); });   // risposta cattiva: meglio la copia, se c'è
    }, () => caches.match(chiave).then(hit => { if (!usa(hit)) usa(Response.error()); }));
  }));
}
const pagina = res => res.ok && /text\/html/i.test(res.headers.get('content-type') || '');
self.addEventListener('fetch', e => {
  const r = e.request;
  const u = new URL(r.url);
  // la musica del Mac (catalogo, brani, copertine, aggiorna) e le API dei moduli vanno sempre chieste al Mac: niente /api/ nella copia offline
  if (r.method !== 'GET' || u.origin !== location.origin || u.pathname.indexOf('/api/') >= 0) return;
  const base = new URL('./', self.registration.scope).pathname;
  if (r.mode === 'navigate') {
    // pagine dei moduli (x/….html, anche con ?…): una copia per pagina, senza la parte dopo «?»
    if (u.pathname.startsWith(base + 'x/') && u.pathname.endsWith('.html')) { reteOCopia(e, r, u.origin + u.pathname, pagina); return; }
    // solo la pagina dell'app (./ o ./index.html, anche con ?…): le altre pagine dello stesso indirizzo (un'icona, una cartella,
    // una pagina collegata come Benessere) passano senza toccare la copia offline, che altrimenti verrebbe sostituita da loro
    if (u.pathname !== base && u.pathname !== base + 'index.html') return;
    reteOCopia(e, r, './index.html', pagina);
    return;
  }
  // file comuni dei moduli (x/kit.js, x/kit.css): anche loro prima la rete, così un aggiornamento arriva insieme alle pagine
  if (u.pathname.startsWith(base + 'x/')) { reteOCopia(e, r, r, res => res.ok); return; }
  if (!MIEI.has(u.pathname)) return;   // non è un file dell'app: niente copia
  e.respondWith(caches.match(r).then(hit => hit || fetch(r).then(res => {
    if (res.ok) { const cp = res.clone(); caches.open(CACHE).then(c => c.put(r, cp)); }
    return res;
  })));
});
