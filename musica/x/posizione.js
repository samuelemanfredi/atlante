/* Atlante – posizione condivisa: il motore che gira nell'app (index.html), così la condivisione continua mentre si
   ascolta la musica o si apre un altro modulo. x/posizione.html è solo la pagina dei comandi (usa window.parent.POS).
   - Dal vivo: la posizione parte al più ogni 5 secondi e solo dopo 20 metri (comunque una volta al minuto, così chi la
     segue sa che la condivisione è viva), cifrata da telefono a telefono con x/cifra.js; lo schermo resta acceso con
     la Wake Lock API, dove c'è. Sull'iPhone la posizione arriva solo con l'app aperta sullo schermo: in sottofondo o a
     schermo spento Apple la ferma, e riparte quando si torna nell'app. Si ferma da sola alla scadenza e con «Ferma».
   - Un punto solo: la posizione di adesso, una volta (ricifrata se un destinatario apre l'app su un telefono nuovo).
   - In alto, sempre: «Stai condividendo la posizione con …» con «Ferma»; a chi la riceve «Nome condivide la sua
     posizione» con «Raggiungilo», che apre il Navigatore (x/naviga.html) con il percorso già impostato.
   Le coordinate non passano mai in chiaro dal Mac mini: al Navigatore arrivano dopo il # dell'indirizzo. */
(function (G) {
  'use strict';
  const OGNI = 5000, METRI = 20, BATTITO = 60000;
  const RAD = Math.PI / 180;
  function distanza(a, b) {   // metri tra due punti {lat, lon}
    const dLat = (b.lat - a.lat) * RAD, dLon = (b.lon - a.lon) * RAD;
    const x = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * RAD) * Math.cos(b.lat * RAD) * Math.sin(dLon / 2) ** 2;
    return 12742000 * Math.asin(Math.min(1, Math.sqrt(x)));
  }
  // va mandata la posizione `nuova`? prec = {lat, lon, inviato} dell'ultima mandata (null: nessuna ancora)
  function daMandare(prec, nuova, ora) {
    if (!prec) return true;
    const dt = ora - prec.inviato;
    if (dt < OGNI) return false;
    return dt >= BATTITO || distanza(prec, nuova) >= METRI;
  }
  G.POS_REGOLE = {OGNI, METRI, BATTITO, distanza, daMandare};
  if (typeof document === 'undefined' || G.POS) return;

  /* ---------- l'app che ospita (index.html): ruolo, avvisi, richieste al Mac ---------- */
  const ruolo = () => { try { return (IO && IO.ruolo) || ''; } catch (e) { return ''; } };   // il ruolo che l'app ha chiesto al Mac (IO)
  const avvisa = m => { try { toast(m); } catch (e) {} };
  const leggi = (k, d) => { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } };
  const scrivi = (k, v) => { try { if (v == null) localStorage.removeItem(k); else localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} };
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
  let sveglia = null;   // l'attesa tra due tentativi finisce subito quando l'app torna sullo schermo
  const dormi = ms => new Promise(r => { const t = setTimeout(r, ms); sveglia = () => { clearTimeout(t); r(); }; });
  function prendi(u, o, ms) { try { return fetchT(u, o, ms); } catch (e) { return fetch(u, o); } }   // fetchT dell'app: chiede la password se serve
  async function api(percorso, opt) {
    opt = Object.assign({cache:'no-store'}, opt);
    if (opt.body && typeof opt.body !== 'string') { opt.body = JSON.stringify(opt.body); opt.headers = {'Content-Type':'application/json'}; }
    const r = await prendi('api/extra/' + percorso, opt, opt.timeout || 30000);
    let j = null; try { j = await r.json(); } catch (e) {}
    if (!r.ok) { const e = new Error((j && j.errore) || ('Il Mac ha risposto ' + r.status)); e.codice = r.status; e.dati = j || {}; throw e; }
    return j;
  }
  function caricaCifra() {
    if (G.Cifra) return Promise.resolve();
    return new Promise((ok, no) => {
      const s = document.createElement('script'); s.src = 'x/cifra.js';
      s.onload = () => ok(); s.onerror = () => { s.remove(); no(new Error('cifratura non scaricata')); };
      document.head.appendChild(s);
    });
  }

  /* ---------- stato ---------- */
  const st = {pronto:false, motivo:'attesa', errore:'', v:0, io:null, mie:[], perMe:[], aperte:{}, vivo:null,
    punti:leggi('am-pos-punti', {}), inviatiPunti:{}, chiusi:new Set(leggi('am-pos-chiusi', [])), visti:null,
    fermate:new Set(), avviando:false};
  const ascolta = new Set();
  function cambia() {
    disegna();
    const s = stato();
    ascolta.forEach(cb => { try { cb(s); } catch (e) {} });
  }
  const nomi = L => { const n = L.map(x => x.nome); return n.length < 2 ? n.join('') : n.slice(0, -1).join(', ') + ' e ' + n[n.length - 1]; };
  const ora2 = s => new Date(s * 1000).toLocaleTimeString('it-IT', {hour:'2-digit', minute:'2-digit'});
  function fa(s) {
    const d = Math.max(0, Date.now() / 1000 - s);
    return d < 45 ? 'adesso' : d < 3600 ? Math.round(d / 60) + ' min fa' : 'alle ' + ora2(s);
  }
  const fino = c => c.durata ? 'fino alle ' + ora2(c.scade) : 'finché non la fermi';

  /* ---------- prepararsi: ruolo, chiavi di questo dispositivo, rubrica ---------- */
  async function prepara() {
    const r = ruolo();
    if (r !== 'proprietario' && r !== 'invitato') { st.motivo = r === 'casa' ? 'casa' : 'attesa'; return false; }
    if (!(G.crypto && G.crypto.subtle) || !G.indexedDB || G.isSecureContext === false) { st.motivo = 'https'; return false; }
    await caricaCifra();
    G.Cifra.usa(api);
    await G.Cifra.registra();
    st.pronto = true; st.motivo = ''; st.errore = '';
    return true;
  }

  /* ---------- attesa lunga: le mie condivisioni e quelle verso di me ---------- */
  // un giro alla volta: tornando sullo schermo ne parte uno nuovo (la richiesta di prima, rimasta appesa mentre l'app
  // era in sottofondo, quando torna non conta più)
  let giroN = 0;
  function giro() { corri(++giroN); }
  async function corri(n) {
    let pausa = 0;
    while (n === giroN && document.visibilityState === 'visible') {
      if (!st.pronto) {
        let ok = false;
        try { ok = await prepara(); } catch (e) { st.motivo = 'errore'; st.errore = e.message; }
        if (!ok) { cambia(); await dormi(st.motivo === 'attesa' ? 2000 : 30000); continue; }
      }
      try {
        const io = await G.Cifra.mie();
        const j = await api('posizione?disp=' + encodeURIComponent(io.disp) + '&v=' + st.v + '&attesa=25', {timeout:40000});
        if (n !== giroN) return;
        pausa = 0;
        await elenco(j);
      } catch (e) {
        if (n !== giroN) return;
        if (e.dati && e.dati.registra) { await G.Cifra.registra(true).catch(() => {}); await dormi(1000); continue; }
        if (e.codice === 403 && /rete di casa/.test(e.message)) { st.pronto = false; st.motivo = 'casa'; cambia(); }
        pausa = Math.min(60000, (pausa || 2500) * 2);
        await dormi(pausa);
      }
    }
  }
  async function elenco(j) {
    st.v = j.v; st.io = j.io; st.perMe = j.per_me || [];
    // fermata qui ma ancora sul Mac mini (rete caduta durante «Ferma»): si riprova, e non riparte da sola
    const mie = j.mie || [];
    mie.filter(c => st.fermate.has(c.id)).forEach(c => api('posizione/' + c.id, {method:'DELETE'}).catch(() => {}));
    st.mie = mie.filter(c => !st.fermate.has(c.id));
    // condivisioni nuove verso di me: un avviso (non al primo giro, quando si apre l'app)
    const primo = !st.visti;
    if (primo) st.visti = new Set();
    for (const c of st.perMe) if (!st.visti.has(c.id)) {
      st.visti.add(c.id);
      if (!primo) { avvisa(c.da.nome + ' condivide la sua posizione'); try { navigator.vibrate && navigator.vibrate(200); } catch (e) {} }
    }
    for (const c of st.perMe) if (c.pos && (!st.aperte[c.id] || st.aperte[c.id].seq !== c.seq)) {
      try { st.aperte[c.id] = {seq:c.seq, p:await G.Cifra.apri(c.pos, c.id, c.da.id)}; }
      catch (e) { st.aperte[c.id] = {seq:c.seq, errore:e.message}; }
    }
    for (const k in st.aperte) if (!st.perMe.some(c => c.id === k)) delete st.aperte[k];
    const chiusi = [...st.chiusi].filter(k => st.perMe.some(c => c.id === k));
    if (chiusi.length !== st.chiusi.size) { st.chiusi = new Set(chiusi); scrivi('am-pos-chiusi', chiusi); }
    // la mia condivisione dal vivo: fermata altrove o scaduta, oppure da riprendere (app ricaricata su questo telefono)
    const io = await G.Cifra.mie(), mia = st.mie.find(c => c.tipo === 'vivo');
    if (st.vivo && !st.avviando && (!mia || mia.id !== st.vivo.id)) fineVivo(false, mia ? '' : 'La condivisione della posizione è finita');
    if (mia && !st.vivo && !st.avviando && mia.disp === io.disp) iniziaVivo(mia, true);
    if (st.vivo && mia) { st.vivo.c = mia; if (mia.mancano.length) st.vivo.forza = true; }
    // i punti: ricifrati per un telefono nuovo di un destinatario; quelli finiti si dimenticano
    let pulito = false;
    for (const k in st.punti) if (!st.mie.some(c => c.id === k)) { delete st.punti[k]; pulito = true; }
    if (pulito) scrivi('am-pos-punti', st.punti);
    for (const c of st.mie) if (c.tipo === 'punto' && c.mancano.length && st.punti[c.id]) mandaPunto(c, st.punti[c.id]).catch(() => {});
    cambia();
  }

  /* ---------- dal vivo ---------- */
  function iniziaVivo(c, ripresa) {
    if (!('geolocation' in navigator)) { avvisa('Posizione non disponibile su questo dispositivo'); return; }
    const v = st.vivo = {id:c.id, c, prec:null, ultima:null, invio:false, forza:false, inviate:0, wl:null, wakeOk:false, geo:'', nascosta:0};
    v.watch = navigator.geolocation.watchPosition(nuovaPos, e => {
      if (st.vivo !== v) return;
      v.geo = e.code === 1 ? 'negato' : 'attesa';
      if (e.code === 1) { avvisa('Permesso di posizione negato: la condivisione si ferma'); ferma(v.id).catch(() => {}); }
      disegna();
    }, {enableHighAccuracy:true, maximumAge:2000, timeout:30000});
    v.battito = setInterval(() => {   // fermo da un minuto: la stessa posizione di nuovo, così chi segue sa che è viva
      if (st.vivo === v && v.prec) provaManda(v);
      if (st.vivo === v && Date.now() / 1000 >= v.c.scade) fineVivo(false, 'La condivisione della posizione è finita');
    }, 5000);
    schermoAcceso();
    if (ripresa) avvisa('Riprendo la condivisione della posizione');
    disegna();
  }
  function nuovaPos(p) {
    const v = st.vivo; if (!v) return;
    const c = p.coords;
    const q = {tipo:'vivo', lat:+c.latitude.toFixed(6), lon:+c.longitude.toFixed(6), acc:Math.round(c.accuracy || 0),
      vel:c.speed != null && c.speed >= 0 ? +c.speed.toFixed(1) : null, dir:c.heading != null && !isNaN(c.heading) ? Math.round(c.heading) : null,
      t:p.timestamp || Date.now()};
    v.ultima = q; if (v.geo) { v.geo = ''; disegna(); }
    provaManda(v);
  }
  function provaManda(v) {
    if (st.vivo !== v || !v.ultima || v.invio) return;
    const ora = Date.now(), q = v.ultima;
    if (daMandare(v.prec, q, ora) || (v.forza && ora - v.prec.inviato >= OGNI)) { manda(v, q); return; }
    // spostata, ma prima dei 5 secondi: parte appena passano, anche se il GPS (telefono fermo) non dà altre posizioni
    if (!v.dopo && (v.forza || distanza(v.prec, q) >= METRI)) v.dopo = setTimeout(() => { v.dopo = 0; provaManda(v); }, Math.max(100, OGNI - (ora - v.prec.inviato)));
  }
  async function manda(v, q) {
    if (v.invio || !st.io) return;
    v.invio = true; v.forza = false;
    try {
      const busta = await G.Cifra.chiudi(v.id, st.io.id, q, v.c.a.map(x => x.id));
      const r = await api('posizione/' + v.id, {method:'POST', body:busta});
      v.prec = {lat:q.lat, lon:q.lon, inviato:Date.now()}; v.inviate++; v.errore = '';
      if (r.mancano && r.mancano.length) { await G.Cifra.rubrica(true); v.forza = true; }   // un telefono nuovo: il prossimo va anche a lui
    } catch (e) {
      if (e.codice === 404 && !(e.dati && e.dati.registra)) fineVivo(false, 'La condivisione della posizione è finita');
      else if (e.dati && e.dati.registra) await G.Cifra.registra(true).catch(() => {});
      else if (e.codice !== 429) v.errore = e.message;
    } finally { v.invio = false; disegna(); provaManda(v); }   // una posizione arrivata mentre questa partiva
  }
  async function schermoAcceso() {
    const v = st.vivo;
    if (!v || v.wl || !('wakeLock' in navigator) || document.visibilityState !== 'visible') { disegna(); return; }
    try {
      const wl = await navigator.wakeLock.request('screen');
      if (st.vivo !== v) { wl.release().catch(() => {}); return; }
      v.wl = wl; v.wakeOk = true;
      wl.addEventListener('release', () => { if (st.vivo === v) { v.wl = null; v.wakeOk = false; disegna(); } });
    } catch (e) { v.wakeOk = false; }
    disegna();
  }
  function fineVivo(cancella, messaggio) {
    const v = st.vivo; if (!v) return;
    st.vivo = null;
    try { navigator.geolocation.clearWatch(v.watch); } catch (e) {}
    clearInterval(v.battito); clearTimeout(v.dopo);
    if (v.wl) v.wl.release().catch(() => {});
    if (cancella) api('posizione/' + v.id, {method:'DELETE'}).catch(() => {});
    if (messaggio) avvisa(messaggio);
    cambia();
  }

  /* ---------- un punto solo ---------- */
  // la posizione migliore entro `ms` (si ferma prima se è precisa a 25 m)
  function posizioneMigliore(ms) {
    return new Promise((ok, no) => {
      if (!('geolocation' in navigator)) { no(new Error('Posizione non disponibile su questo dispositivo')); return; }
      let meglio = null, fatto = false;
      const fine = () => { if (fatto) return; fatto = true; navigator.geolocation.clearWatch(w); clearTimeout(t); meglio ? ok(meglio) : no(new Error('Nessun segnale GPS: riprova all\'aperto')); };
      const w = navigator.geolocation.watchPosition(p => {
        if (!meglio || p.coords.accuracy < meglio.coords.accuracy) meglio = p;
        if (p.coords.accuracy <= 25) fine();
      }, e => {
        if (e.code === 1) { fatto = true; navigator.geolocation.clearWatch(w); clearTimeout(t); no(new Error('Permesso di posizione negato: su iPhone Impostazioni ▸ Privacy e sicurezza ▸ Localizzazione')); }
      }, {enableHighAccuracy:true, maximumAge:10000, timeout:ms});
      const t = setTimeout(fine, ms);
    });
  }
  async function mandaPunto(c, q) {
    const ultimo = st.inviatiPunti[c.id] || 0;
    if (Date.now() - ultimo < OGNI) return;
    st.inviatiPunti[c.id] = Date.now();
    const busta = await G.Cifra.chiudi(c.id, st.io.id, q, c.a.map(x => x.id));
    await api('posizione/' + c.id, {method:'POST', body:busta});
  }

  /* ---------- comandi (dalla pagina x/posizione.html) ---------- */
  // opz: {tipo:'vivo'|'punto', a:[persone], durata:900|3600|0}
  async function condividi(opz) {
    if (!st.pronto && !(await prepara())) throw new Error(spiega());
    const io = await G.Cifra.mie();
    if (!st.io) st.io = {id:(await G.Cifra.rubrica()).io, nome:''};
    if (opz.tipo === 'vivo') {
      // il permesso della posizione prima di avvisare gli altri: se lo nega, non parte niente
      const p = await posizioneMigliore(8000).catch(e => { if (/negato/.test(e.message)) throw e; return null; });
      st.avviando = true;   // intanto l'attesa lunga può tornare con la condivisione nuova: la avvia questa funzione
      try {
        const r = await api('posizione', {method:'POST', body:{tipo:'vivo', a:opz.a, durata:opz.durata, disp:io.disp}});
        if (st.vivo && st.vivo.id !== r.condivisione.id) fineVivo(false);   // una sola dal vivo: il Mac mini ha già tolto quella di prima
        st.mie = [r.condivisione].concat(st.mie.filter(c => c.tipo !== 'vivo'));
        if (!st.vivo) iniziaVivo(r.condivisione, false);
        if (p) nuovaPos(p);
      } finally { st.avviando = false; }
    } else {
      const p = await posizioneMigliore(10000), c = p.coords;
      const q = {tipo:'punto', lat:+c.latitude.toFixed(6), lon:+c.longitude.toFixed(6), acc:Math.round(c.accuracy || 0), t:p.timestamp || Date.now()};
      const r = await api('posizione', {method:'POST', body:{tipo:'punto', a:opz.a, durata:opz.durata, disp:io.disp}});
      st.punti[r.condivisione.id] = q; scrivi('am-pos-punti', st.punti);
      st.mie = [r.condivisione].concat(st.mie);
      await mandaPunto(r.condivisione, q);
    }
    cambia();
    return true;
  }
  async function ferma(id) {
    st.fermate.add(id);
    if (st.vivo && st.vivo.id === id) fineVivo(false);
    if (st.punti[id]) { delete st.punti[id]; scrivi('am-pos-punti', st.punti); }
    st.mie = st.mie.filter(c => c.id !== id);
    cambia();
    try { await api('posizione/' + id, {method:'DELETE'}); } catch (e) { if (e.codice !== 404) throw e; }
  }
  async function fermaTutto() {
    const ids = st.mie.map(c => c.id).concat(st.vivo && !st.mie.some(c => c.id === st.vivo.id) ? [st.vivo.id] : []);
    await Promise.all(ids.map(id => ferma(id).catch(() => {})));
    avvisa('Non condividi più la posizione');
  }
  function chiudi(id) { st.chiusi.add(id); scrivi('am-pos-chiusi', [...st.chiusi]); cambia(); }
  // «Raggiungilo»: il Navigatore dell'app, anche per gli invitati (la sua pagina non usa il Mac)
  function raggiungi(id) {
    const c = st.perMe.find(x => x.id === id); if (!c) return false;
    let h;
    if (c.tipo === 'vivo') h = 'verso=persona:' + encodeURIComponent(c.da.id);
    else {
      const a = st.aperte[c.id];
      if (!a || !a.p) { avvisa('La posizione di ' + c.da.nome + ' non è ancora arrivata su questo telefono'); return false; }
      h = 'verso=' + a.p.lat.toFixed(6) + ',' + a.p.lon.toFixed(6);
    }
    apriNaviga(h + '&nome=' + encodeURIComponent(c.da.nome));
    chiudi(c.id);   // l'avviso non copre il Navigatore, che dice lui come va; la condivisione resta in «Condividi la posizione»
    return true;
  }
  function apriNaviga(hash) {
    const f = document.getElementById('xf'), p = document.getElementById('xp');
    if (!f || !p) return;
    try { closeNP(); } catch (e) {}
    const src = 'x/naviga.html#' + hash;   // dopo il #: le coordinate non vanno al Mac mini
    if (f.getAttribute('src') !== src) f.setAttribute('src', src);
    p.hidden = false; document.body.classList.add('xnav');
    try { xCur = 'naviga'; } catch (e) {}
  }
  function spiega() {
    return {casa:'Dalla rete di casa la posizione non si condivide: apri l\'app con Tailscale o con il tuo invito.',
      https:'Su questo indirizzo il browser non permette la cifratura: apri l\'app dall\'indirizzo https (Tailscale o il tuo invito).',
      errore:'Il Mac non risponde: ' + st.errore, attesa:'Mi sto collegando al Mac…'}[st.motivo] || '';
  }
  function stato() {
    const v = st.vivo;
    return {pronto:st.pronto, motivo:st.motivo, spiega:spiega(), io:st.io, mie:st.mie.slice(), wakeLock:'wakeLock' in navigator,
      perMe:st.perMe.map(c => Object.assign({}, c, {aperta:st.aperte[c.id] ? st.aperte[c.id].p || null : null, chiusa:st.chiusi.has(c.id)})),
      vivo:v ? {id:v.id, inviate:v.inviate, wakeOk:v.wakeOk, geo:v.geo, errore:v.errore || '', ultima:v.ultima} : null};
  }

  /* ---------- in alto: la barra di chi condivide e gli avvisi di chi riceve ---------- */
  const css = document.createElement('style');
  css.textContent = '#posTop{position:fixed;z-index:60;left:0;right:0;top:0;padding:env(safe-area-inset-top,0px) max(8px,env(safe-area-inset-right,0px)) 0 max(8px,env(safe-area-inset-left,0px));pointer-events:none}' +
    '#posTop .in{display:flex;flex-direction:column;gap:6px;max-width:640px;margin:0 auto;padding-top:6px}#posTop .in>*{pointer-events:auto}' +
    '.posbar{display:flex;align-items:center;gap:8px;min-height:44px;padding:5px 6px 5px 12px;border-radius:13px;background:#007aff;color:#fff;box-shadow:0 4px 14px rgba(0,0,0,.2)}' +
    '.posbar .t{flex:1;min-width:0;text-align:left;color:inherit}.posbar .t span{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}' +
    '.posbar .t b,.posavv .t b{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;line-height:1.2}' +
    '.posbar .t b{font-size:14px}.posbar .t span{font-size:12px;opacity:.85;margin-top:1px}' +
    '.posbar .f{flex:none;min-height:34px;padding:0 13px;border-radius:9px;background:rgba(255,255,255,.24);color:#fff;font-size:15px;font-weight:700}' +
    '.posavv{display:flex;align-items:center;gap:8px;padding:7px 4px 7px 12px;border-radius:14px;background:var(--sheet,#fff);color:var(--fg,#000);box-shadow:0 6px 20px rgba(0,0,0,.22)}' +
    '.posavv .t{flex:1;min-width:0}.posavv .t span{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}' +
    '.posavv .t b{font-size:15px}.posavv .t span{font-size:13px;color:var(--fg2,#666)}' +
    '.posavv .r{flex:none;min-height:36px;padding:0 13px;border-radius:10px;background:#34c759;color:#fff;font-size:15px;font-weight:700}' +
    '.posavv .x{flex:none;width:36px;height:36px;color:var(--fg2,#666);font-size:22px;line-height:36px}' +
    'body.posalto #view{padding-top:calc(6px + var(--posh,0px))}body.posalto .sbox{top:calc(env(safe-area-inset-top,0px) + var(--posh,0px))}' +
    'body.posalto #xp{top:calc(env(safe-area-inset-top,0px) + var(--posh,0px))}body.posalto #dlpill{top:calc(env(safe-area-inset-top,0px) + var(--posh,0px) + 54px)}' +
    'body.posalto #np{padding-top:calc(env(safe-area-inset-top,0px) + var(--posh,0px) + 8px)}';
  document.head.appendChild(css);
  const top = document.createElement('div');
  top.id = 'posTop'; top.innerHTML = '<div class="in" id="posIn"></div>';
  document.body.appendChild(top);
  const dentro = top.firstChild;
  let disegnato = '';
  function misura() {
    const h = dentro.children.length ? Math.ceil(dentro.getBoundingClientRect().height) : 0;
    document.documentElement.style.setProperty('--posh', h + 'px');
    document.body.classList.toggle('posalto', h > 0);   // il resto dell'app scende sotto la barra
  }
  if (G.ResizeObserver) new ResizeObserver(misura).observe(dentro);
  function disegna() {
    let h = '';
    const mie = st.mie.slice();
    if (st.vivo && !mie.some(c => c.id === st.vivo.id)) mie.unshift(st.vivo.c);
    if (mie.length) {
      const a = [], visti = new Set();
      mie.forEach(c => c.a.forEach(x => { if (!visti.has(x.id)) { visti.add(x.id); a.push(x); } }));
      const vivo = mie.find(c => c.tipo === 'vivo'), v = st.vivo;
      let sotto;
      if (v && v.geo === 'negato') sotto = 'Permesso di posizione negato';
      else if (v) sotto = (v.wakeOk ? 'Schermo sempre acceso · ' : '') + 'tieni l\'app aperta sullo schermo · ' + fino(v.c);
      else if (vivo) sotto = 'Dal vivo da un altro tuo dispositivo · ' + fino(vivo);
      else sotto = (mie.length > 1 ? mie.length + ' punti' : 'Un punto') + ' · ' + fino(mie[0]);
      h += '<div class="posbar" role="status"><button class="t" data-pos="apri"><b>Stai condividendo la posizione con ' + esc(nomi(a)) + '</b><span>' +
        esc(sotto) + '</span></button><button class="f" data-pos="ferma">Ferma</button></div>';
    }
    const avv = st.perMe.filter(c => !st.chiusi.has(c.id));
    avv.slice(0, 2).forEach(c => {
      const ap = st.aperte[c.id];
      const det = c.tipo === 'vivo' ? 'Dal vivo · ' + (c.t ? 'aggiornata ' + fa(c.t) : 'in attesa della posizione') : 'Un punto · ' + (ap && ap.p ? 'delle ' + ora2(ap.p.t / 1000) : 'in arrivo');
      h += '<div class="posavv" role="status"><div class="t"><b>' + esc(c.da.nome) + ' condivide la sua posizione</b><span>' + esc(det) + '</span></div>' +
        '<button class="r" data-pos="raggiungi" data-id="' + esc(c.id) + '">Raggiungilo</button><button class="x" data-pos="chiudi" data-id="' + esc(c.id) + '" aria-label="Chiudi l\'avviso">×</button></div>';
    });
    if (avv.length > 2) h += '<div class="posavv"><div class="t"><b>E altre ' + (avv.length - 2) + ' persone condividono la posizione</b></div><button class="r" data-pos="apri">Vedi</button></div>';
    if (h !== disegnato) { disegnato = h; dentro.innerHTML = h; }   // solo se cambia: un tocco su «Ferma» non va perso
    misura();
  }
  dentro.addEventListener('click', e => {
    const b = e.target.closest('[data-pos]'); if (!b) return;
    const a = b.dataset.pos;
    if (a === 'ferma') fermaTutto();
    else if (a === 'chiudi') chiudi(b.dataset.id);
    else if (a === 'raggiungi') raggiungi(b.dataset.id);
    else if (a === 'apri') { try { setTab('altro'); } catch (er) {} try { openX('posizione'); } catch (er) {} }
  });
  setInterval(() => { if (st.perMe.length && document.visibilityState === 'visible') disegna(); }, 30000);   // «aggiornata 2 min fa»

  /* ---------- l'app sullo schermo o no ---------- */
  document.addEventListener('visibilitychange', () => {
    const v = st.vivo;
    if (document.visibilityState === 'visible') {
      if (v) {
        if (v.nascosta && Date.now() - v.nascosta > 10000) avvisa('Mentre l\'app non era sullo schermo la posizione non partiva: ripresa ora');
        v.nascosta = 0; v.forza = true; schermoAcceso();
      }
      if (sveglia) sveglia();
      giro();
    } else if (v) v.nascosta = Date.now();
  });

  G.POS = {stato, onChange(cb) { ascolta.add(cb); return () => ascolta.delete(cb); }, condividi, ferma, fermaTutto, raggiungi, chiudi,
    persone: () => prepara().then(ok => { if (!ok) throw new Error(spiega()); return G.Cifra.rubrica(true); }), regole:G.POS_REGOLE};
  disegna();
  giro();
})(typeof globalThis !== 'undefined' ? globalThis : self);
