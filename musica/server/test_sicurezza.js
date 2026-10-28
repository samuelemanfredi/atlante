// Prove di sicurezza dell'app (musica/index.html) e dei moduli (musica/x/) con JavaScriptCore, che c'è su ogni Mac:
//
//     osascript -l JavaScript test_sicurezza.js ../index.html ../x
//
// Le funzioni si prendono dalle pagine così come sono (come in test_mix.js) e si provano con un ambiente finto:
// - nomi e titoli che arrivano da fuori (altri dispositivi, moduli, feed) non diventano HTML (devRender, devRiga);
// - le copertine (art, mosaic, coverUrl, rcBrano, i feed dei podcast) solo http, https e blob:, sempre protette;
// - un link da fuori non fa partire da solo una chiamata (x/chiamate.html ?chiama=) né uno scaricamento
//   (x/video.html ?subito=1): da solo parte solo dentro l'app, e l'indirizzo dell'app non porta queste richieste;
// - gli allegati della chat: immagini solo raster (mai SVG), il resto come file.
// Scrive «OK …» o «PROVA FALLITA: …» (test_extra.py lo controlla).
ObjC.import('Foundation');

function run(argv) {
  const fatte = [], errori = [];
  try {
    const app = argv[0] || '../index.html', x = argv[1] || '../x';
    prove(leggi(app), p => leggi(x + '/' + p), fatte, errori);
  } catch (e) {
    errori.push((e && e.message) || String(e));
  }
  if (errori.length) return 'PROVA FALLITA: ' + errori.join(' ‖ ') + (fatte.length ? ' (riuscite: ' + fatte.join(', ') + ')' : '');
  return 'OK ' + fatte.length + ' prove: ' + fatte.join(', ');
}

function leggi(p) {
  const s = $.NSString.stringWithContentsOfFileEncodingError(p, $.NSUTF8StringEncoding, null);
  if (!s || s.isNil()) throw new Error('non trovo ' + p);
  return s.js;
}

/* ---------- estrazione delle funzioni dalle pagine (come in test_mix.js) ----------
   Qui anche le funzioni rientrate (dentro un blocco dei moduli) e quelle chiamate subito, «(async function avvio() {…})()» */
function trova(src, nome) {
  return new RegExp('\\n[ \\t]*\\(?(async )?function ' + nome + '\\(').exec(src);
}
function estrai(src, nome) {
  const m = trova(src, nome);
  if (!m) throw new Error('nella pagina manca la funzione ' + nome);
  const i = m.index + m[0].indexOf(m[1] ? 'async' : 'function');
  let k = src.indexOf('{', src.indexOf(')', i)), d = 0;
  for (; k < src.length; k++) {
    const ch = src[k];
    if (ch === '"' || ch === "'" || ch === '`') { k++; while (k < src.length && src[k] !== ch) { if (src[k] === '\\') k++; k++; } continue; }
    if (ch === '/' && src[k + 1] === '/') { while (k < src.length && src[k] !== '\n') k++; continue; }
    if (ch === '/' && src[k + 1] === '*') { k = src.indexOf('*/', k + 2) + 1; continue; }
    if (ch === '{') d++;
    else if (ch === '}' && !--d) break;
  }
  return src.slice(i, k + 1);
}
// JavaScriptCore qui non fa girare le promesse da solo: le funzioni «async» diventano sincrone (via «await»), e le finte
// rispondono subito (come in test_tutto.js)
function sincrona(f) { return f.replace(/^async function/, 'function').replace(/\bawait /g, ''); }
function riga(src, inizio) {
  const m = new RegExp('\\n[ \\t]*' + inizio.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).exec(src);
  if (!m) throw new Error('nella pagina manca la riga «' + inizio + '…»');
  const i = m.index + 1;
  return src.slice(i, src.indexOf('\n', i));
}
const c_e = (src, inizio) => new RegExp('\\n[ \\t]*' + inizio.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).test(src);
// righe (costanti) e funzioni; quelle in `forse` solo se la pagina le ha (aiutanti nuovi)
function carica(src, nomi, righe, env, forse) {
  forse = forse || {};
  const r = righe.concat((forse.righe || []).filter(x => c_e(src, x)));
  const n = nomi.concat((forse.nomi || []).filter(x => trova(src, x)));
  const codice = r.map(x => riga(src, x)).join('\n') + '\n' + n.map(x => sincrona(estrai(src, x))).join('\n');
  const f = new Function('env', 'with (env) { return (function () {\n' + codice + '\nreturn {' + n.join(',') + '};\n})(); }');
  return f(env);
}

// URLSearchParams minimo (JavaScriptCore non ce l'ha): get, has, delete, toString
function Parametri(s) {
  this.v = String(s || '').replace(/^\?/, '').split('&').filter(Boolean).map(p => {
    const i = p.indexOf('='), d = x => decodeURIComponent(x.replace(/\+/g, ' '));
    return i < 0 ? [d(p), ''] : [d(p.slice(0, i)), d(p.slice(i + 1))];
  });
}
Parametri.prototype.get = function (k) { const x = this.v.find(p => p[0] === k); return x ? x[1] : null; };
Parametri.prototype.has = function (k) { return this.v.some(p => p[0] === k); };
Parametri.prototype.delete = function (k) { this.v = this.v.filter(p => p[0] !== k); };
Parametri.prototype.toString = function () { return this.v.map(p => encodeURIComponent(p[0]) + '=' + encodeURIComponent(p[1])).join('&'); };

function prove(app, modulo, fatte, errori) {
  // ogni gruppo di prove va avanti da solo: un errore non nasconde gli altri
  const gruppo = f => { try { f(); } catch (e) { errori.push((e && e.message) || String(e)); } };
  const eq = (a, b, m) => { const x = JSON.stringify(a), y = JSON.stringify(b); if (x !== y) throw new Error(m + ': ' + x + ' invece di ' + y); };
  const vero = (c, m) => { if (!c) throw new Error(m); };
  // HTML scritto da fuori che non deve mai diventare un elemento (né un attributo nuovo)
  const pulito = (h, m) => {
    for (const x of ['<img src=x', '<svg', '<script', '<i>tel', '" onerror=', '" onload=', 'javascript:', '<b>di</b>'])
      vero(h.indexOf(x) < 0, m + ': nel codice HTML c\'è «' + x + '» → ' + h.slice(0, 400));
  };

  /* ---------- scheda «Riproduci su» (devRender, devRiga): titoli e nomi degli altri dispositivi ---------- */
  gruppo(() => {
    let html = '';
    const env = {devSheetK:'', devAzioni:() => '', curId:'a', au:{paused:false}, RC:{id:null}, ic:() => '', SRV:true, T:new Map(),
      TT:() => ({title:'<img src=x onerror=alert(1)>', artist:'A'}), DEV:{nome:'Mac <b>di</b> Anna'}, openSheet:h => { html = h; },
      castNota:() => '', cancel:'', DEV_ICONA:() => '',
      DEVS:[{id:'tel-00000001', nome:'<i>tel</i>', stato:{suona:true, brano:{titolo:'<svg onload=alert(2)>', artista:'<script>x</script>', copertina:'javascript:alert(3)'}}}]};
    const F = carica(app, ['devRender', 'devRiga', 'rcBrano'], ['const esc ='], env);
    F.devRender();
    vero(html.indexOf('Riproduci su') >= 0, 'devRender non ha aperto la scheda');
    pulito(html, 'devRender: titolo o nome di un altro dispositivo non protetto');
    vero(html.indexOf('&lt;img src=x onerror=alert(1)&gt;') >= 0, 'devRender: il titolo del brano in ascolto qui non si vede (protetto)');
    vero(html.indexOf('Mac &lt;b&gt;di&lt;/b&gt; Anna') >= 0, 'devRender: il nome di questo dispositivo non si vede (protetto)');
    fatte.push('riproduci su');
  });

  /* ---------- copertine: art, mosaic, coverUrl, rcBrano ---------- */
  gruppo(() => {
    const env = {C:new Map([['k1', {url:'blob:https://mac.esempio/1'}]]), SRV:'api/', API:'api/musica/', ic:() => '', T:new Map()};
    const F = carica(app, ['art', 'mosaic', 'rcBrano'], ['const esc =', 'const coverUrl ='], env, {righe:['const copertinaSicura =']});
    // coverUrl è una costante: la si legge dal codice caricato con una funzione che la restituisce
    const G = new Function('env', 'with (env) { return (function () {\n' + (c_e(app, 'const copertinaSicura =') ? riga(app, 'const copertinaSicura =') + '\n' : '') +
      riga(app, 'const coverUrl =') + '\nreturn coverUrl;\n})(); }')(env);
    eq(G('u:javascript:alert(1)'), '', 'coverUrl: javascript: da un modulo o da un altro dispositivo');
    eq(G('u: JavaScript:alert(1)'), '', 'coverUrl: javascript: con uno spazio davanti');
    eq(G('u:data:text/html,<script>alert(1)</script>'), '', 'coverUrl: data:text/html');
    eq(G('u:vbscript:x'), '', 'coverUrl: un altro schema');
    eq(G('u:https://i.ytimg.com/vi/abc/hq.jpg'), 'https://i.ytimg.com/vi/abc/hq.jpg', 'coverUrl: https');
    eq(G('u:http://esempio.it/c.jpg'), 'http://esempio.it/c.jpg', 'coverUrl: http');
    eq(G('u:blob:https://mac.esempio/123'), 'blob:https://mac.esempio/123', 'coverUrl: blob:');
    eq(G('k1'), 'blob:https://mac.esempio/1', 'coverUrl: copertina salvata qui');
    eq(G('s:a b.jpg'), 'api/musica/copertina/a%20b.jpg', 'coverUrl: copertina del Mac');
    fatte.push('coverUrl');

    const a1 = F.art('u:https://x.esempio/a.jpg" onerror="alert(1)');
    pulito(a1, 'art: virgolette nella copertina');
    vero(a1.indexOf('<img') >= 0 && a1.indexOf('&quot;') >= 0, 'art: la copertina https non si vede più ' + a1);
    const a2 = F.art('u:javascript:alert(1)');
    vero(a2.indexOf('<img') < 0, 'art: copertina javascript: mostrata ' + a2);
    const tr = ['u:https://x.esempio/1.jpg', 'u:https://x.esempio/2.jpg"><script>alert(1)</script>', 'u:javascript:alert(1)', 's:abc.jpg']
      .map((cover, i) => ({id:'t' + i, cover}));
    pulito(F.mosaic(tr), 'mosaic: copertina non protetta');
    // il brano di un altro dispositivo (stato mandato al Mac da chiunque abbia l'app aperta come Samuele)
    const b = F.rcBrano({titolo:'x', copertina:'javascript:alert(1)'});
    vero(F.art(b.cover).indexOf('<img') < 0, 'rcBrano: copertina javascript: di un altro dispositivo mostrata');
    pulito(F.art(F.rcBrano({titolo:'x', copertina:'https://x.esempio/c.jpg" onerror="alert(1)'}).cover), 'rcBrano: copertina con virgolette');
    fatte.push('copertine');
  });

  /* ---------- classifiche: posizione e salto arrivano dal Mac ---------- */
  gruppo(() => {
    const F = carica(app, ['clSegno'], ['const esc ='], {});
    pulito(F.clSegno({segno:'su', di:'<img src=x onerror=alert(1)>'}), 'clSegno: salto non protetto');
    fatte.push('classifiche');
  });

  /* ---------- podcast: copertine dai feed e da Apple ---------- */
  gruppo(() => {
    const pod = modulo('podcast.html');
    vero(c_e(pod, 'const copertinaSicura ='), 'podcast.html: manca copertinaSicura (le copertine dei feed passano com\'erano)');
    const g = new Function('return (function () {\n' + riga(pod, 'const copertinaSicura =') + '\nreturn copertinaSicura;\n})();')();
    eq(g('javascript:alert(1)'), '', 'podcast: copertina javascript: dal feed');
    eq(g('data:image/svg+xml,<svg onload=alert(1)>'), '', 'podcast: copertina data: dal feed');
    eq(g(' https://feed.esempio/c.jpg '), 'https://feed.esempio/c.jpg', 'podcast: copertina https');
    eq(g(null), '', 'podcast: copertina mancante');
    const pf = estrai(pod, 'parseFeed'), di = riga(pod, 'const daItunes =');
    const cop = (pf + '\n' + di).match(/\bcover:[^,}]*/g) || [];
    vero(cop.length >= 3 && cop.every(c => c.indexOf('copertinaSicura(') >= 0), 'podcast: copertine del feed non controllate: ' + cop.join(' | '));
    fatte.push('podcast');
  });

  /* ---------- chiamate: un link non chiama da solo (x/chiamate.html) ---------- */
  gruppo(() => {
    const ch = modulo('chiamate.html');
    const prova = (q, host, frame, nome) => {
      const fatto = [];
      const env = {Q:new Parametri(q), host, window:{frameElement:frame, parent:frame ? {AM:host} : null},
        AX:{header() {}, api() { return {then() { return {catch() {}}; }}; }}, $:() => ({}), history:{replaceState() {}},
        location:{pathname:'/x/chiamate.html'}, C:{preparando:false, attivo:false}, disegnaRecenti() {}, caricaPersone:() => true,
        ascoltaEntrate() {}, entrata:id => fatto.push('entrata ' + id), chiama:(ids, v) => fatto.push('chiama ' + ids.join(',') + (v ? ' video' : '')),
        avvia:(ids, v) => fatto.push('avvia ' + ids.join(',') + (v ? ' video' : '')), proponi:(ids, v) => fatto.push('proponi ' + ids.join(',') + (v ? ' video' : ''))};
      if (host) host.chiamataFinita = () => fatto.push('finita');
      carica(ch, ['avvio'], ['const CORNICE ='], env, {nomi:['nellaCornice']}).avvio();
      return fatto;
    };
    const app_ = () => ({chiama() {}});
    // un link mandato da qualcuno, aperto da solo nel browser
    eq(prova('chiama=abc12345&video=1', null, null, 'da solo'), ['proponi abc12345 video'], 'chiamate: link aperto da solo');
    // lo stesso link passato all'app (?x=chiamate&chiama=…&cornice=1): la pagina è nel riquadro dei moduli, non nella cornice
    eq(prova('chiama=abc12345&video=1&cornice=1', app_(), {id:'xf', parentElement:{id:'xp'}}), ['proponi abc12345 video'],
      'chiamate: link passato all\'app con cornice=1 (riquadro dei moduli)');
    // la cornice vera, aperta dall'app (apriChiamata in index.html): chiama subito, risponde a chi chiama
    eq(prova('chiama=abc12345&video=1&cornice=1', app_(), {parentElement:{id:'xc'}}), ['chiama abc12345 video'], 'chiamate: cornice dell\'app');
    eq(prova('entrante=c1&cornice=1', app_(), {parentElement:{id:'xc'}}), ['entrata c1'], 'chiamate: chiamata in arrivo nella cornice');
    fatte.push('chiamate');
  });

  /* ---------- video: ?subito=1 parte da solo solo dentro l'app (x/video.html) ---------- */
  gruppo(() => {
    const vid = modulo('video.html');
    const F = carica(vid, ['partenzaAutomatica'], [], {});
    const p = (q, w) => F.partenzaAutomatica(new Parametri(q), w);
    const sola = {frameElement:null, parent:null}, modulo_ = {frameElement:{id:'xf'}, parent:{AM:{}}};
    eq(p('url=https%3A%2F%2Fx.esempio%2Fv&subito=1', sola), false, 'video: link con subito=1 aperto da solo');
    eq(p('url=https%3A%2F%2Fx.esempio%2Fv&subito=1', {frameElement:{id:'altro'}, parent:{}}), false, 'video: dentro un\'altra pagina');
    eq(p('url=https%3A%2F%2Fx.esempio%2Fv', modulo_), false, 'video: senza subito');
    eq(p('url=https%3A%2F%2Fx.esempio%2Fv&subito=1', modulo_), true, 'video: subito=1 dentro l\'app');
    vero(/auto = partenzaAutomatica\(qs, window\)/.test(vid), 'video.html: la partenza automatica non usa partenzaAutomatica');
    fatte.push('video');
  });

  /* ---------- l'indirizzo dell'app (?x=modulo&…) non porta le richieste che partono da sole ---------- */
  gruppo(() => {
    const F = carica(app, ['moduloDaIndirizzo'], [], {URLSearchParams:Parametri}, {righe:['const SOLO_DALL_APP =']});
    eq(F.moduloDaIndirizzo('?x=video&url=https%3A%2F%2Fx.esempio%2Fv&subito=1&tipo=audio'),
      {k:'video', q:'?url=https%3A%2F%2Fx.esempio%2Fv&tipo=audio'}, 'indirizzo: subito tolto');
    eq(F.moduloDaIndirizzo('?x=chiamate&chiama=abc12345&video=1&cornice=1'), {k:'chiamate', q:'?chiama=abc12345&video=1'}, 'indirizzo: cornice tolta');
    eq(F.moduloDaIndirizzo('?x=chat&c=d0123'), {k:'chat', q:'?c=d0123'}, 'indirizzo: la chat dalle notifiche');
    eq(F.moduloDaIndirizzo('?x=gestione'), {k:'gestione', q:''}, 'indirizzo: senza altro');
    eq(F.moduloDaIndirizzo(''), null, 'indirizzo: nessun modulo');
    vero(/moduloDaIndirizzo\(location\.search\)/.test(app), 'index.html: l\'apertura dall\'indirizzo non usa moduloDaIndirizzo');
    fatte.push('indirizzo dell\'app');
  });

  /* ---------- chat: allegati, immagini solo raster ---------- */
  gruppo(() => {
    const chat = modulo('chat.html');
    const F = carica(chat, ['pulisci'], [], {MAX_TESTO:4000, MAX_ALLEGATO:200 * 1024 * 1024}, {righe:['const TIPI_ALLEGATO =']});
    const al = mime => ({id:'A'.repeat(22), chiave:'B'.repeat(43), pezzi:1, dimensione:10, mime, nome:'x'});
    eq(F.pulisci({tipo:'foto', allegato:al('image/svg+xml')}).allegato.mime, '', 'chat: foto SVG');
    eq(F.pulisci({tipo:'video', allegato:al('image/svg+xml')}).allegato.mime, '', 'chat: video con tipo SVG');
    eq(F.pulisci({tipo:'foto', allegato:al('image/jpeg')}).allegato.mime, 'image/jpeg', 'chat: foto JPEG');
    eq(F.pulisci({tipo:'foto', allegato:al('image/heic')}).allegato.mime, 'image/heic', 'chat: foto HEIC');
    eq(F.pulisci({tipo:'video', allegato:al('video/mp4')}).allegato.mime, 'video/mp4', 'chat: video');
    eq(F.pulisci({tipo:'vocale', allegato:al('audio/mp4')}).allegato.mime, 'audio/mp4', 'chat: vocale');
    // un allegato già salvato sul telefono con il tipo SVG (da una versione di prima): il suo blob: non è più un SVG
    let creato = null;
    const blob = t => ({type:t, size:10, slice(a, b, tipo) { return blob(tipo || ''); }});
    const env = {URLS:new Map(), blobDi:a => blob(a.mime), URL:{createObjectURL:b => { creato = b; return 'blob:x'; }}};
    const G = carica(chat, ['urlDi'], [], env, {righe:['const TIPI_ALLEGATO =']});
    G.urlDi({id:'s1', mime:'image/svg+xml'});
    vero(creato && creato.type !== 'image/svg+xml', 'chat: blob: di un SVG aperto come SVG (' + (creato && creato.type) + ')');
    G.urlDi({id:'j1', mime:'image/jpeg'});
    eq(creato.type, 'image/jpeg', 'chat: blob: di una foto');
    fatte.push('allegati della chat');
  });
}
