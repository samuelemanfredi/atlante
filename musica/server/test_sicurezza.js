// Prove di sicurezza dell'app (musica/index.html) e dei moduli (musica/x/) con JavaScriptCore, che c'è su ogni Mac:
//
//     osascript -l JavaScript test_sicurezza.js ../index.html ../x
//
// Le funzioni si prendono dalle pagine così come sono (come in test_mix.js) e si provano con un ambiente finto:
// - nomi e titoli che arrivano da fuori (altri dispositivi, moduli, feed) non diventano HTML (devRender, devRiga);
// - le copertine (art, mosaic, coverUrl, rcBrano, i feed dei podcast, i libri EPUB) solo http, https e blob:, sempre
//   protette; i podcast salvati da una versione di prima si ripuliscono, e AX.player (x/kit.js) lo controlla per tutti;
// - un link da fuori non fa partire da solo una chiamata (x/chiamate.html ?chiama=: chiama da sola solo la cornice
//   dell'app, con il suo gettone usa e getta) né uno scaricamento (x/video.html: il link resta scritto, serve un tocco);
//   l'indirizzo dell'app non porta queste richieste;
// - gli allegati della chat: immagini solo raster (mai SVG), video e audio con tipi conosciuti, il resto come file;
// - numeri, id e testi che arrivano dal Mac (inviti, gestione, controllo, strumenti, playlist e classifiche dell'app)
//   non diventano HTML, e i link solo http e https.
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
  return src.slice(i, chiusa(src, src.indexOf('{', src.indexOf(')', i))) + 1);
}
// istruzione che comincia con `inizio` (es. «pages.classifica = function», «$('x').onchange =») fino alla sua graffa chiusa
function blocco(src, inizio) {
  const m = new RegExp('\\n[ \\t]*' + inizio.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).exec(src);
  if (!m) throw new Error('nella pagina manca «' + inizio + '…»');
  const i = m.index + m[0].length - inizio.length, k = chiusa(src, src.indexOf('{', i + inizio.length));
  return src.slice(i, k + 1) + ';';
}
// in k comincia una stringa, un commento o un'espressione regolare (/…/ dopo «(», «=», «,», «return»…)? dove finisce;
// se no -1. Serve a contare le parentesi senza farsi ingannare da quelle scritte dentro
function salta(src, k) {
  const ch = src[k];
  if (ch === '"' || ch === "'" || ch === '`') { k++; while (k < src.length && src[k] !== ch) { if (src[k] === '\\') k++; k++; } return k; }
  if (ch !== '/') return -1;
  if (src[k + 1] === '/') { while (k < src.length && src[k] !== '\n') k++; return k; }
  if (src[k + 1] === '*') return src.indexOf('*/', k + 2) + 1;
  let j = k - 1;
  while (j >= 0 && /\s/.test(src[j])) j--;
  if (j >= 0 && !/[(,=:[!&|?{};+\-*%<>~^]/.test(src[j]) && !/\b(return|typeof|case)$/.test(src.slice(Math.max(0, j - 6), j + 1))) return -1;   // una divisione
  let classe = false;
  for (k++; k < src.length && src[k] !== '\n'; k++) {
    if (src[k] === '\\') { k++; continue; }
    if (src[k] === '[') classe = true;
    else if (src[k] === ']') classe = false;
    else if (src[k] === '/' && !classe) return k;
  }
  return k;
}
// la parentesi che chiude quella in k: «(» → «)», «{» → «}»
function chiusaDi(src, k, apre, chiude) {
  let d = 0;
  for (; k < src.length; k++) {
    const f = salta(src, k);
    if (f >= 0) { k = f; continue; }
    if (src[k] === apre) d++;
    else if (src[k] === chiude && !--d) break;
  }
  return k;
}
const chiusaTonda = (src, k) => chiusaDi(src, k, '(', ')');
const chiusa = (src, k) => chiusaDi(src, k, '{', '}');
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
// righe (costanti) e funzioni; quelle in `forse` solo se la pagina le ha (aiutanti nuovi). forse.esporta: costanti
// (delle righe) da restituire insieme alle funzioni
function carica(src, nomi, righe, env, forse) {
  forse = forse || {};
  const r = righe.concat((forse.righe || []).filter(x => c_e(src, x)));
  const n = nomi.concat((forse.nomi || []).filter(x => trova(src, x)));
  const codice = r.map(x => riga(src, x)).join('\n') + '\n' + n.map(x => sincrona(estrai(src, x))).join('\n');
  const f = new Function('env', 'with (env) { return (function () {\n' + codice + '\nreturn {' + n.concat(forse.esporta || []).join(',') + '};\n})(); }');
  return f(env);
}
// codice qualsiasi (un blocco, più righe) fatto girare con l'ambiente finto
function esegui(codice, env) { return new Function('env', 'with (env) {\n' + codice + '\n}')(env); }
// le prime n righe da quella che comincia con `inizio` (costanti scritte su più righe)
function righe(src, inizio, n) {
  const r = riga(src, inizio), i = src.indexOf(r);
  return src.slice(i).split('\n').slice(0, n).join('\n');
}
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

// URL minimo (JavaScriptCore non ce l'ha): assoluti, «//host/…», «/percorso», relativi con «./» e «../»; href, host
function URLfinto(u, base) {
  u = String(u).replace(/^[\u0000-\u0020]+|[\u0000-\u0020]+$/g, '');
  const sch = /^([a-zA-Z][a-zA-Z0-9+.-]*):/.exec(u);
  let href;
  if (sch) href = sch[1].toLowerCase() + u.slice(sch[1].length);
  else {
    if (base === undefined || base === null) throw new TypeError('URL non valido: ' + u);
    const b = new URLfinto(base).href, m = /^([a-z][a-z0-9+.-]*:)(\/\/[^\/?#]*)?([^?#]*)/.exec(b);
    if (u.slice(0, 2) === '//') href = m[1] + u;
    else if (u[0] === '/') href = m[1] + (m[2] || '') + punti(u);
    else href = m[1] + (m[2] || '') + punti(m[3].replace(/[^\/]*$/, '') + u);
  }
  const h = /^[a-z][a-z0-9+.-]*:\/\/([^\/?#]*)/.exec(href);
  this.href = href; this.protocol = href.slice(0, href.indexOf(':') + 1); this.host = h ? h[1] : '';
}
function punti(p) {   // via «.» e «..» dal percorso (non dalla query)
  const i = p.search(/[?#]/), percorso = i < 0 ? p : p.slice(0, i), resto = i < 0 ? '' : p.slice(i), out = [];
  const pezzi = percorso.split('/');
  pezzi.forEach((x, k) => {
    if (x === '..') { if (out.length > 1) out.pop(); } else if (x !== '.') out.push(x);
    if ((x === '..' || x === '.') && k === pezzi.length - 1) out.push('');
  });
  return out.join('/') + resto;
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
    const g = esegui(riga(pod, 'const copertinaSicura =') + '\nreturn copertinaSicura;', {URL:URLfinto});
    eq(g('javascript:alert(1)'), '', 'podcast: copertina javascript: dal feed');
    eq(g('data:image/svg+xml,<svg onload=alert(1)>'), '', 'podcast: copertina data: dal feed');
    eq(g(' https://feed.esempio/c.jpg '), 'https://feed.esempio/c.jpg', 'podcast: copertina https');
    eq(g(null), '', 'podcast: copertina mancante');
    // indirizzi relativi del feed: letti dall'indirizzo del feed, poi lo schema
    const feed = 'https://feed.esempio/podcast/rss.xml';
    eq(g('//cdn.esempio/c.jpg', feed), 'https://cdn.esempio/c.jpg', 'podcast: copertina «//cdn…» del feed');
    eq(g('img/c.jpg', feed), 'https://feed.esempio/podcast/img/c.jpg', 'podcast: copertina relativa del feed');
    eq(g('/c.jpg', feed), 'https://feed.esempio/c.jpg', 'podcast: copertina «/…» del feed');
    eq(g('javascript:alert(1)', feed), '', 'podcast: copertina javascript: con l\'indirizzo del feed');
    eq(g('//cdn.esempio/c.jpg'), '', 'podcast: copertina relativa senza feed');
    eq(g('https://cdn.esempio/c.jpg', 'non un indirizzo'), 'https://cdn.esempio/c.jpg', 'podcast: copertina https con un feed strano');
    const pf = estrai(pod, 'parseFeed'), di = riga(pod, 'const daItunes =');
    const cop = (pf + '\n' + di).match(/\bcover:[^,}]*/g) || [];
    vero(cop.length >= 3 && cop.every(c => c.indexOf('copertinaSicura(') >= 0), 'podcast: copertine del feed non controllate: ' + cop.join(' | '));
    // nel feed ogni copertina si legge dall'indirizzo del feed
    const chiamate = [];
    for (let i = pf.indexOf('copertinaSicura('); i >= 0; i = pf.indexOf('copertinaSicura(', i + 1)) chiamate.push(pf.slice(i, chiusaTonda(pf, i + 15) + 1));
    vero(chiamate.length >= 2 && chiamate.every(c => /, feed\)$/.test(c)), 'podcast: copertine del feed non lette dal suo indirizzo: ' + chiamate.join(' | '));
    fatte.push('podcast');
  });

  /* ---------- podcast salvati da una versione di prima: ripuliti quando si rileggono (x/podcast.html) ---------- */
  gruppo(() => {
    const pod = modulo('podcast.html');
    const F = carica(pod, ['pulisciSalvato'], ['const copertinaSicura ='], {URL:URLfinto});
    const ep = F.pulisciSalvato({feed:'https://feed.esempio/p/rss', agg:1, info:{titolo:'P', cover:'javascript:alert(1)'},
      lista:[{id:'e1', url:'javascript:alert(2)', cover:'data:image/svg+xml,<svg onload=alert(3)>'}, {id:'e2', url:' https://feed.esempio/e2.mp3 ', cover:'//cdn.esempio/e2.jpg'}]});
    eq(ep.info.cover, '', 'podcast salvati: copertina javascript: del podcast');
    eq(ep.lista.map(e => [e.url, e.cover]), [['', ''], ['https://feed.esempio/e2.mp3', 'https://cdn.esempio/e2.jpg']], 'podcast salvati: episodi');
    eq(ep.info.titolo, 'P', 'podcast salvati: il resto non cambia');
    const st = F.pulisciSalvato({id:'e3', feed:'https://feed.esempio/p/rss', t:12, pod:{titolo:'P', cover:'vbscript:x'}, ep:{titolo:'E', url:'https://feed.esempio/e3.mp3', cover:'img/e3.jpg'}});
    eq([st.pod.cover, st.ep.cover, st.ep.url, st.t], ['', 'https://feed.esempio/p/img/e3.jpg', 'https://feed.esempio/e3.mp3', 12], 'podcast salvati: «Continua ad ascoltare»');
    eq(F.pulisciSalvato({feed:'https://feed.esempio/p/rss', cover:'https://x.esempio/c.jpg'}).cover, 'https://x.esempio/c.jpg', 'podcast salvati: iscrizione con copertina https');
    eq(F.pulisciSalvato(undefined), undefined, 'podcast salvati: niente in archivio');
    // tutto quello che si rilegge dall'archivio (iscrizioni, episodi, «Continua», scaricati) passa di lì
    vero(/\n\s*get: \(s, k\) => run\([^\n]*pulisciSalvato\(/.test(pod) && /\n\s*all: s => run\([^\n]*pulisciSalvato\(/.test(pod), 'podcast: l\'archivio non ripulisce quello che rilegge');
    vero(/S\.top = cache\.list\.map\(p => pulisciSalvato\(p\)\)/.test(pod), 'podcast: la classifica salvata non si ripulisce');
    fatte.push('podcast salvati');
  });

  /* ---------- chiamate: un link non chiama da solo (x/chiamate.html) ---------- */
  gruppo(() => {
    const ch = modulo('chiamate.html');
    const R = (c_e(ch, 'const IN_CORNICE =') ? ['const IN_CORNICE ='] : []).concat(['const CORNICE =']);
    const prova = (q, host, frame, nome) => {
      const fatto = [];
      const env = {Q:new Parametri(q), host, window:{frameElement:frame, parent:frame ? {AM:host} : null},
        AX:{header() {}, api() { return {then() { return {catch() {}}; }}; }}, $:() => ({}), history:{replaceState() {}},
        location:{pathname:'/x/chiamate.html'}, C:{preparando:false, attivo:false}, disegnaRecenti() {}, caricaPersone:() => true,
        ascoltaEntrate() {}, entrata:id => fatto.push('entrata ' + id), chiama:(ids, v) => fatto.push('chiama ' + ids.join(',') + (v ? ' video' : '')),
        avvia:(ids, v) => fatto.push('avvia ' + ids.join(',') + (v ? ' video' : '')), proponi:(ids, v) => fatto.push('proponi ' + ids.join(',') + (v ? ' video' : ''))};
      if (host) host.chiamataFinita = () => fatto.push('finita');
      carica(ch, ['avvio'], R, env, {nomi:['nellaCornice', 'gettoneValido']}).avvio();
      return fatto;
    };
    // l'app finta: il gettone usa e getta che apriChiamata ha messo nell'indirizzo della cornice
    const app_ = g => { let gt = g || ''; return {chiama() {}, gettoneChiamata(x) { const ok = !!gt && x === gt; gt = ''; return ok; }}; };
    // un link mandato da qualcuno, aperto da solo nel browser
    eq(prova('chiama=abc12345&video=1', null, null, 'da solo'), ['proponi abc12345 video'], 'chiamate: link aperto da solo');
    // lo stesso link passato all'app (?x=chiamate&chiama=…&cornice=1): la pagina è nel riquadro dei moduli, non nella cornice
    eq(prova('chiama=abc12345&video=1&cornice=1', app_(), {id:'xf', parentElement:{id:'xp'}}), ['proponi abc12345 video'],
      'chiamate: link passato all\'app con cornice=1 (riquadro dei moduli)');
    // la cornice vera, aperta dall'app (apriChiamata in index.html) con il suo gettone: chiama subito, risponde a chi chiama
    const G = '0123456789abcdef0123456789abcdef', xc = {parentElement:{id:'xc'}};
    eq(prova('chiama=abc12345&video=1&cornice=1&gettone=' + G, app_(G), xc), ['chiama abc12345 video'], 'chiamate: cornice dell\'app');
    eq(prova('entrante=c1&cornice=1&gettone=' + G, app_(G), xc), ['entrata c1'], 'chiamate: chiamata in arrivo nella cornice');
    // nella cornice senza il gettone giusto (o con uno già usato) non chiama: la cornice si chiude
    eq(prova('chiama=abc12345&video=1&cornice=1', app_(G), xc), ['finita'], 'chiamate: cornice senza gettone');
    eq(prova('chiama=abc12345&video=1&cornice=1&gettone=fedcba9876543210fedcba9876543210', app_(G), xc), ['finita'], 'chiamate: cornice con un gettone sbagliato');
    eq(prova('entrante=c1&cornice=1&gettone=' + G, app_(''), xc), ['finita'], 'chiamate: chiamata in arrivo con un gettone che l\'app non ha dato');
    const una = app_(G);
    prova('chiama=abc12345&cornice=1&gettone=' + G, una, xc);
    eq(prova('chiama=abc12345&cornice=1&gettone=' + G, una, xc), ['finita'], 'chiamate: gettone usato due volte');
    fatte.push('chiamate');
  });

  /* ---------- chiamate: il gettone usa e getta dell'app (apriChiamata e AM.gettoneChiamata in index.html) ---------- */
  gruppo(() => {
    let n = 0;
    const cornice = {hidden:true, firstChild:{src:''}};
    const env = {CH:{seq:null, aperta:false, musica:false, ctx:null, gettone:''}, curId:null, au:{paused:true, pause() {}}, RC:{id:null}, pausaVoluta:0,
      chCornice:() => cornice, $:() => cornice, crypto:{getRandomValues(b) { for (let i = 0; i < b.length; i++) b[i] = (n++ * 37 + 11) & 255; return b; }}};
    const F = carica(app, ['apriChiamata', 'chiudiChiamata'], [], env, {nomi:['chGettone', 'chGettoneValido']});
    vero(F.apriChiamata('?chiama=abc12345&video=1'), 'apriChiamata: la chiamata non si apre');
    const m = /^x\/chiamate\.html\?chiama=abc12345&video=1&cornice=1&gettone=([0-9a-f]{32})$/.exec(cornice.firstChild.src);
    vero(m, 'apriChiamata: nell\'indirizzo della cornice manca il gettone: ' + cornice.firstChild.src);
    eq(F.chGettoneValido('f'.repeat(32)), false, 'gettone sbagliato');
    eq(F.chGettoneValido(m[1]), false, 'gettone giusto dopo uno sbagliato (vale una volta sola)');
    F.chiudiChiamata();
    F.apriChiamata('?entrante=c1');
    const m2 = /gettone=([0-9a-f]{32})$/.exec(cornice.firstChild.src);
    vero(m2 && m2[1] !== m[1], 'apriChiamata: lo stesso gettone due volte');
    eq(F.chGettoneValido(m2[1]), true, 'gettone giusto');
    eq(F.chGettoneValido(m2[1]), false, 'gettone usato due volte');
    F.chiudiChiamata();
    eq(F.chGettoneValido(m2[1]), false, 'gettone a chiamata chiusa');
    vero(/\n\s*gettoneChiamata\(g\) \{ return chGettoneValido\(g\); \}/.test(app), 'index.html: AM.gettoneChiamata manca');
    fatte.push('gettone delle chiamate');
  });

  /* ---------- video: un link non scarica mai da solo (x/video.html) ---------- */
  gruppo(() => {
    const vid = modulo('video.html');
    // l'app non manda più «subito»: niente partenza automatica rimasta nella pagina
    for (const x of ['partenzaAutomatica', 'avvioAutomatico', "get('subito')"]) vero(vid.indexOf(x) < 0, 'video.html: resta la partenza automatica (' + x + ')');
    // il Comando Rapido «Scarica con Atlante» apre …/x/video.html?url=…: il link si vede scritto e aspetta un tocco
    const campi = {url:{value:''}, addr:{value:''}};
    let pulito_ = false;
    const F = carica(vid, ['linkDaIndirizzo', 'estraiUrl'], ['const urlRe ='], {$:id => campi[id], history:{replaceState() { pulito_ = true; }}, location:{pathname:'/musica/x/video.html'}});
    eq(F.linkDaIndirizzo(new Parametri('url=https%3A%2F%2Fwww.youtube.com%2Fwatch%3Fv%3Dabc&subito=1')), 'https://www.youtube.com/watch?v=abc', 'video: link del Comando Rapido');
    eq([campi.url.value, campi.addr.value], ['https://www.youtube.com/watch?v=abc', 'https://www.youtube.com/watch?v=abc'], 'video: il link non è scritto');
    vero(pulito_, 'video: l\'indirizzo resta (un ricaricamento lo rimanderebbe)');
    eq(F.linkDaIndirizzo(new Parametri('text=Guarda%20https%3A%2F%2Fvm.tiktok.com%2Fabc%2F')), 'https://vm.tiktok.com/abc/', 'video: link nel testo condiviso');
    eq(F.linkDaIndirizzo(new Parametri('')), '', 'video: senza link');
    vero(/const daIndirizzo = linkDaIndirizzo\(qs\)/.test(vid), 'video.html: l\'apertura con ?url= non usa linkDaIndirizzo');
    vero(/\$\('scBase'\)\.textContent = BASE \+ '\?url=';/.test(vid), 'video.html: l\'indirizzo per il Comando Rapido è cambiato');
    fatte.push('video');
  });

  /* ---------- l'indirizzo dell'app (?x=modulo&…) non porta le richieste che partono da sole ---------- */
  gruppo(() => {
    const F = carica(app, ['moduloDaIndirizzo'], [], {URLSearchParams:Parametri}, {righe:['const SOLO_DALL_APP =']});
    eq(F.moduloDaIndirizzo('?x=video&url=https%3A%2F%2Fx.esempio%2Fv&subito=1&tipo=audio'),
      {k:'video', q:'?url=https%3A%2F%2Fx.esempio%2Fv&tipo=audio'}, 'indirizzo: subito tolto');
    eq(F.moduloDaIndirizzo('?x=chiamate&chiama=abc12345&video=1&cornice=1'), {k:'chiamate', q:'?chiama=abc12345&video=1'}, 'indirizzo: cornice tolta');
    eq(F.moduloDaIndirizzo('?x=chiamate&chiama=abc12345&cornice=1&gettone=0123456789abcdef0123456789abcdef'), {k:'chiamate', q:'?chiama=abc12345'}, 'indirizzo: gettone tolto');
    eq(F.moduloDaIndirizzo('?x=chat&c=d0123'), {k:'chat', q:'?c=d0123'}, 'indirizzo: la chat dalle notifiche');
    eq(F.moduloDaIndirizzo('?x=gestione'), {k:'gestione', q:''}, 'indirizzo: senza altro');
    eq(F.moduloDaIndirizzo(''), null, 'indirizzo: nessun modulo');
    vero(/moduloDaIndirizzo\(location\.search\)/.test(app), 'index.html: l\'apertura dall\'indirizzo non usa moduloDaIndirizzo');
    fatte.push('indirizzo dell\'app');
  });

  /* ---------- chat: allegati, immagini solo raster ---------- */
  gruppo(() => {
    const chat = modulo('chat.html');
    const T = {righe:['const TIPI_ALLEGATO =', 'const ALIAS_ALLEGATO ='], nomi:['mimeAllegato']};
    const F = carica(chat, ['pulisci'], [], {MAX_TESTO:4000, MAX_ALLEGATO:200 * 1024 * 1024}, T);
    const al = mime => ({id:'A'.repeat(22), chiave:'B'.repeat(43), pezzi:1, dimensione:10, mime, nome:'x'});
    eq(F.pulisci({tipo:'foto', allegato:al('image/svg+xml')}).allegato.mime, '', 'chat: foto SVG');
    eq(F.pulisci({tipo:'video', allegato:al('image/svg+xml')}).allegato.mime, '', 'chat: video con tipo SVG');
    eq(F.pulisci({tipo:'foto', allegato:al('image/jpeg')}).allegato.mime, 'image/jpeg', 'chat: foto JPEG');
    eq(F.pulisci({tipo:'foto', allegato:al('image/heic')}).allegato.mime, 'image/heic', 'chat: foto HEIC');
    eq(F.pulisci({tipo:'video', allegato:al('video/mp4')}).allegato.mime, 'video/mp4', 'chat: video');
    eq(F.pulisci({tipo:'vocale', allegato:al('audio/mp4')}).allegato.mime, 'audio/mp4', 'chat: vocale');
    // video e audio solo con i tipi conosciuti: un «…+xml» si aprirebbe come una pagina
    eq(F.pulisci({tipo:'video', allegato:al('video/x+xml')}).allegato.mime, '', 'chat: video con tipo XML');
    eq(F.pulisci({tipo:'audio', allegato:al('audio/svg+xml')}).allegato.mime, '', 'chat: audio con tipo SVG');
    eq(F.pulisci({tipo:'audio', allegato:al('audio/html')}).allegato.mime, '', 'chat: audio con tipo HTML');
    eq(F.pulisci({tipo:'video', allegato:al('video/quicktime')}).allegato.mime, 'video/quicktime', 'chat: video dell\'iPhone');
    eq(F.pulisci({tipo:'audio', allegato:al('audio/mpeg')}).allegato.mime, 'audio/mpeg', 'chat: MP3');
    eq(F.pulisci({tipo:'foto', allegato:al('image/jpg')}).allegato.mime, 'image/jpeg', 'chat: foto image/jpg (altro nome)');
    // un allegato già salvato sul telefono con il tipo SVG (da una versione di prima): il suo blob: non è più un SVG
    let creato = null;
    const blob = t => ({type:t, size:10, slice(a, b, tipo) { return blob(tipo || ''); }});
    const env = {URLS:new Map(), blobDi:a => blob(a.mime), URL:{createObjectURL:b => { creato = b; return 'blob:x'; }}};
    const G = carica(chat, ['urlDi'], [], env, T);
    G.urlDi({id:'s1', mime:'image/svg+xml'});
    vero(creato && creato.type !== 'image/svg+xml', 'chat: blob: di un SVG aperto come SVG (' + (creato && creato.type) + ')');
    G.urlDi({id:'j1', mime:'image/jpeg'});
    eq(creato.type, 'image/jpeg', 'chat: blob: di una foto');
    // senza tipo (un browser lo indovinerebbe, magari HTML): file qualsiasi
    G.urlDi({id:'v1', mime:''});
    eq(creato.type, 'application/octet-stream', 'chat: blob: senza tipo');
    G.urlDi({id:'x1', mime:'video/x+xml'});
    eq(creato.type, 'application/octet-stream', 'chat: blob: di un video con tipo XML');
    G.urlDi({id:'j2', mime:'image/jpg'});
    eq(creato.type, 'image/jpeg', 'chat: blob: di una foto image/jpg');
    fatte.push('allegati della chat');
  });

  /* ---------- chat: foto dalla libreria con altri nomi del tipo, immagini di altri tipi come file (x/chat.html) ---------- */
  gruppo(() => {
    const chat = modulo('chat.html'), T = {righe:['const TIPI_ALLEGATO =', 'const ALIAS_ALLEGATO ='], nomi:['mimeAllegato']};
    // la scelta dalla libreria: niente foto rifiutate
    const mandati = [], avvisi = [], el = {};
    esegui(blocco(chat, "$('sceltaMedia').onchange ="), {$:() => el, mandaFile:(f, t) => mandati.push([f.type, t]), toast:m => avvisi.push(m),
      TIPI_ALLEGATO:esegui(riga(chat, 'const TIPI_ALLEGATO =') + '\nreturn TIPI_ALLEGATO;', {})});
    for (const t of ['image/jpg', 'image/pjpeg', 'image/x-png', 'image/apng', 'image/svg+xml', 'image/x-icon', 'video/mp4']) el.onchange({target:{files:[{type:t}], value:'x'}});
    eq(mandati, [['image/jpg', 'foto'], ['image/pjpeg', 'foto'], ['image/x-png', 'foto'], ['image/apng', 'foto'], ['image/svg+xml', 'foto'], ['image/x-icon', 'foto'], ['video/mp4', 'video']],
      'chat: foto rifiutate dalla scelta (avvisi: ' + avvisi.join(' | ') + ')');
    // mandaFile: il tipo giusto per i nomi diversi, vuoto (file qualsiasi) per quelli che non si mostrano
    const contenuti = [];
    const env = {aperta:'c1', MAX_ALLEGATO:200 * 1024 * 1024, C:{pezzi:() => 1}, anteprimaDi:() => '', durataDi:() => 0, toast() {}, carica() {},
      nuovoMessaggio:(conv, c) => { contenuti.push(c); return {id:'m' + contenuti.length}; }, db:{put() {}}};
    const F = carica(chat, ['mandaFile'], [], env, T);
    const tipo = t => { F.mandaFile({size:10, type:t, name:'f'}, 'foto'); return contenuti[contenuti.length - 1].allegato.mime; };
    eq(['image/jpg', 'image/pjpeg', 'image/x-png', 'image/apng', 'image/png', 'image/heic'].map(tipo), ['image/jpeg', 'image/jpeg', 'image/png', 'image/png', 'image/png', 'image/heic'], 'chat: tipo delle foto mandate');
    eq(['image/svg+xml', 'image/x-icon', 'text/html', ''].map(tipo), ['', '', '', ''], 'chat: immagini di altri tipi mandate come file');
    fatte.push('foto della chat');
  });

  /* ---------- libri: copertine degli EPUB solo raster (x/libri.html) ---------- */
  gruppo(() => {
    const lib = modulo('libri.html');
    const blob = t => ({type:t, size:10, slice(a, b, tipo) { return blob(tipo || ''); }});
    const creati = [];
    const env = {S:{covers:new Map(), urlsHost:new Map()}, URL:{createObjectURL:b => { creati.push(b.type); return 'blob:https://mac.esempio/' + creati.length; }},
      urlHost:b => { creati.push(b.type); return 'blob:https://mac.esempio/h' + creati.length; }, hashN:() => 7};
    const F = carica(lib, ['coverHtml', 'coverPerLettore'], [], env, {righe:['const TIPI_COPERTINA =', 'const tipoCopertina ='], nomi:['copertinaBlob']});
    // un libro importato prima, con la copertina SVG: niente blob: SVG, né qui né nell'app (lettore)
    const svg = {id:'b1', tipo:'testo', titolo:'<img src=x onerror=alert(1)>', autore:'A', cover:blob('image/svg+xml')};
    const h = F.coverHtml(svg, '');
    vero(h.indexOf('<img') < 0 && creati.length === 0, 'libri: copertina SVG mostrata (' + creati.join(',') + ') ' + h);
    pulito(h, 'libri: titolo nella copertina disegnata');
    eq(F.coverPerLettore(svg), '', 'libri: copertina SVG passata al lettore dell\'app');
    eq(creati, [], 'libri: blob: SVG creato');
    vero(F.coverHtml({id:'b2', tipo:'testo', titolo:'T', cover:blob('image/png')}, '').indexOf('src="blob:https://mac.esempio/1"') >= 0, 'libri: la copertina PNG non si vede');
    eq(F.coverPerLettore({id:'b3', tipo:'testo', titolo:'T', cover:blob('image/jpeg')}), 'blob:https://mac.esempio/h2', 'libri: copertina JPEG al lettore');
    eq(creati, ['image/png', 'image/jpeg'], 'libri: tipi dei blob: creati');
    const G = carica(lib, ['copertinaBlob'], ['const TIPI_COPERTINA =', 'const tipoCopertina ='], {}, {esporta:['tipoCopertina']});
    eq(['image/svg+xml', 'image/jpeg', 'image/jpg', 'IMAGE/PNG', 'image/webp', 'image/gif', 'text/html', 'image/svg+xml; charset=utf-8', 'constructor', '', null].map(G.tipoCopertina),
      ['', 'image/jpeg', 'image/jpeg', 'image/png', 'image/webp', 'image/gif', '', '', '', '', ''], 'libri: tipi delle copertine');
    eq(G.copertinaBlob({cover:blob('image/svg+xml')}), null, 'libri: copertina SVG salvata');
    eq(G.copertinaBlob({cover:blob('')}), null, 'libri: copertina senza tipo');
    eq(G.copertinaBlob({cover:blob('image/jpg')}).type, 'image/jpeg', 'libri: copertina image/jpg');
    eq(G.copertinaBlob({cover:null}), null, 'libri: senza copertina');
    // all'importazione dell'EPUB: si sceglie e si salva solo una copertina raster, con il suo tipo giusto
    const ep = estrai(lib, 'leggiEpub');
    vero(/new Blob\(\[await f\.async\('arraybuffer'\)\], \{type:tipoCopertina\(cov\.tipo\)\}\)/.test(ep), 'libri: la copertina dell\'EPUB si salva con il tipo del file');
    vero(/if \(!cov \|\| !tipoCopertina\(cov\.tipo\)\)/.test(ep) && ep.indexOf('/^image\\//') < 0, 'libri: la copertina dell\'EPUB può essere un SVG');
    fatte.push('copertine dei libri');
  });

  /* ---------- AX.player: indirizzi e copertine solo http, https e blob: per tutti i moduli (x/kit.js) ---------- */
  gruppo(() => {
    const kit = modulo('kit.js');
    let dato = null;
    const win = {parent:{AM:{playExternal(items, start, from) { dato = {items, start, from}; }}}};
    esegui(kit, {window:win, location:{href:'https://mac.esempio/musica/x/podcast.html'}, URL:URLfinto, document:{title:'Podcast'}});
    const P = win.AX.player;
    P.play([{id:'a', url:'javascript:alert(1)', cover:'https://x.esempio/a.jpg'},
      {id:'b', url:'https://x.esempio/b.mp3', cover:'javascript:alert(2)'},
      {id:'c', url:'blob:https://mac.esempio/123', cover:'data:image/svg+xml,<svg onload=alert(3)>'},
      {id:'d', url:'../api/musica/file/abc', cover:'../api/musica/copertina/x.jpg'},
      {id:'e', url:' JavaScript:alert(4)', cover:''}], 2, 'Prova');
    vero(dato, 'kit: l\'app non ha ricevuto niente');
    eq(dato.items.map(i => i.id), ['b', 'c', 'd'], 'kit: brani con indirizzo javascript: passati all\'app');
    eq(dato.items.map(i => i.url), ['https://x.esempio/b.mp3', 'blob:https://mac.esempio/123', 'https://mac.esempio/musica/api/musica/file/abc'], 'kit: indirizzi dei brani');
    eq(dato.items.map(i => i.cover), ['', '', 'https://mac.esempio/musica/api/musica/copertina/x.jpg'], 'kit: copertine javascript: o data: passate all\'app');
    eq([dato.start, dato.from], [1, 'Prova'], 'kit: non parte il brano scelto');
    dato = null;
    P.play([{id:'a', url:'javascript:alert(1)'}, {id:'b', url:'https://x.esempio/b.mp3'}], 0);
    eq([dato.items.map(i => i.id), dato.start], [['b'], 0], 'kit: il brano scelto non va: parte il seguente');
    dato = null;
    P.play([{id:'a', url:'javascript:alert(1)'}], 0);
    eq(dato, null, 'kit: nessun brano buono, ma l\'app ha ricevuto qualcosa');
    fatte.push('lettore dei moduli');
  });

  /* ---------- inviti: lo stato che arriva dal Mac (x/inviti.html) ---------- */
  gruppo(() => {
    const inv = modulo('inviti.html');
    const el = {innerHTML:'', querySelectorAll:() => []};
    const env = {$:() => el, api:() => ({inviti:[{stato:'<img src=x onerror=alert(1)>', nome:'Anna'}, {stato:'consentita', nome:'Bea', link:'https://mac.esempio/i/abc', scade:1900000000}]}), toast() {}};
    carica(inv, ['disegna'], ['const STATI ='], env).disegna();
    pulito(el.innerHTML, 'inviti: stato non protetto');
    vero(el.innerHTML.indexOf('&lt;img src=x') >= 0 && el.innerHTML.indexOf('Consentito') >= 0, 'inviti: gli stati non si vedono ' + el.innerHTML.slice(0, 300));
    fatte.push('inviti');
  });

  /* ---------- app: numeri e link che arrivano dal Mac (index.html) ---------- */
  gruppo(() => {
    const X = '<img src=x onerror=alert(1)>', Y = '<svg onload=alert(2)>';
    // l'indirizzo https fisso dell'app (SRVURL): link solo http o https
    let F = carica(app, ['secureBanner'], [], {window:{PWA:true, isSecureContext:false}, SRVURL:'javascript://mac.esempio/%0aalert(1)', URL:URLfinto});
    pulito(F.secureBanner(), 'secureBanner: link javascript:');
    F = carica(app, ['secureBanner'], [], {window:{PWA:true, isSecureContext:false}, SRVURL:'https://mac.esempio/musica/', URL:URLfinto});
    vero(F.secureBanner().indexOf('<a class="ba" href="https://mac.esempio/musica/">') >= 0, 'secureBanner: il link https non c\'è più');
    vero(/SRVURL = typeof j\.indirizzo === 'string' && \/\^https\?:/.test(app), 'index.html: SRVURL non controllato quando arriva dal Mac');
    // playlist di Atlante: numero dei brani e stato del Mac
    const tile = esegui(righe(app, 'const atlTile =', 2) + '\nreturn atlTile;', {mosaic:() => ''});
    pulito(tile({id:'p1', nome:'P', n:X, copertine:[]}), 'atlTile: numero dei brani');
    vero(tile({id:'p1', nome:'P', n:12, copertine:[]}).indexOf('12 brani') >= 0, 'atlTile: il numero dei brani non si vede');
    F = carica(app, ['atlStato'], [], {ATL:{stato:{attivo:true, totale:Y, fatti:X}}});
    pulito(F.atlStato(), 'atlStato: numeri del Mac');
    F = carica(app, ['atlStato'], [], {ATL:{stato:{attivo:true, totale:300, fatti:12}}});
    vero(/12 di 300/.test(F.atlStato()), 'atlStato: i numeri non si vedono ' + F.atlStato());
    let foglio = '';
    carica(app, ['iaSheet'], [], {ATL:{ia:{rimaste:X}}, openSheet:h => { foglio = h; }, cancel:'', setTimeout() {}, $:() => null}).iaSheet();
    pulito(foglio, 'iaSheet: richieste rimaste');
    // ascolti: soglia e ascolti salvati
    F = carica(app, ['ascAltro'], ['const ascSoglia ='], {ASC:{soglia:Y, io:{condivido:false, ascolti:X, nome:'Io'}}, ic:() => '', HEARD:30});
    const asc = F.ascAltro();
    pulito(asc, 'ascAltro: ascolti o soglia del Mac');
    vero(asc.indexOf('almeno 3 persone') >= 0, 'ascSoglia: senza un numero non torna 3 ' + asc.slice(-200));
    F = carica(app, ['ascAltro'], ['const ascSoglia ='], {ASC:{soglia:5, io:{condivido:false, ascolti:1, nome:'Io'}}, ic:() => '', HEARD:30});
    vero(/1 ascolto salvato[\s\S]*almeno 5 persone/.test(F.ascAltro()), 'ascAltro: i numeri non si vedono');
    // playlist del Mac: brani fuori dalla libreria
    const pl = fuori => carica(app, ['mplPagina'], [], {mplDi:() => ({id:'m1', nome:'M', fonte:'disco', fuori}), mplTracce:() => [{id:'r1', duration:60}], regList:() => 0,
      topBar:() => '', ic:() => '', mosaic:() => '', mplFonte:() => '', mplN:n => n + ' brani', pbtns:() => '', trackRows:() => ''}).mplPagina({k:'m1'});
    pulito(pl(X), 'mplPagina: brani fuori dalla libreria');
    vero(pl(3).indexOf('3 brani della playlist non sono') >= 0, 'mplPagina: il numero dei brani fuori non si vede');
    // classifiche: numero dei brani
    const cl = c => {
      const env = {pages:{}, CLS:[c], topBar:() => '', mosaic:() => '', clTracce:() => [], clTraccia:() => null, clData:() => '1 maggio', ic:() => '', regList:() => 0,
        pbtns:() => '', clRighe:() => '', clSegno:() => ''};
      if (c_e(app, 'const clQuanti =')) env.clQuanti = esegui(riga(app, 'const clQuanti =') + '\nreturn clQuanti;', {});
      esegui(blocco(app, 'pages.classifiche = function') + '\n' + blocco(app, 'pages.classifica = function'), env);
      return env.pages.classifiche() + env.pages.classifica({k:c.id});
    };
    const finti = {length:X, filter:() => [], map:() => [], slice:() => []};
    pulito(cl({id:'c1', nome:'C', brani:finti, libera:false}), 'classifiche: numero dei brani');
    vero(/20 brani · aggiornata il 1 maggio[\s\S]*0 di 20 nella libreria/.test(cl({id:'c1', nome:'C', brani:new Array(20).fill({}), libera:false})), 'classifiche: il numero dei brani non si vede');
    fatte.push('numeri dell\'app');
  });

  /* ---------- gestione: id, numeri e testi dal Mac (x/gestione.html) ---------- */
  gruppo(() => {
    const ges = modulo('gestione.html');
    const X = '"><img src=x onerror=alert(1)>', N = '<svg onload=alert(2)>';
    const els = {}, $ = id => els[id] || (els[id] = {innerHTML:'', hidden:false, querySelectorAll:() => []});
    // persone, richieste, dispositivi
    const persone = {persone:[{id:'p1" onload="alert(1)', nome:'Anna', dispositivi:[{id:X, visto:0, tipo:'iphone'}]},
      {id:'p2', nome:'Bea', dispositivi:{length:N, map:() => []}}], tuoi:[]};
    const env = {$, api:p => p === 'gestione/persone' ? persone : {richieste:[{stato:'attesa', id:X, nome:'Cleo', da_nome:'Anna', creato:0}]},
      Promise:{all:L => L}, aperta:'p1" onload="alert(1)', quando:() => 'oggi', descrivi:() => 'iPhone', dettagli:() => '', toast() {}};
    carica(ges, ['disegna'], [], env).disegna();
    pulito(els.rlist.innerHTML, 'gestione: id delle richieste');
    pulito(els.plist.innerHTML, 'gestione: id e numeri delle persone');
    vero(els.plist.innerHTML.indexOf('data-p="p2"') >= 0 && els.plist.innerHTML.indexOf('0 dispositivi') >= 0, 'gestione: le persone non si vedono');
    // classifiche degli ascolti
    carica(ges, ['ascolti'], [], {$, api:() => ({soglia:N, settimana:[{titolo:'T', artista:'A', ascolti:X}], mese:[], condivisi:[], io:{condivido:false}})}).ascolti();
    pulito(els.alist.innerHTML, 'gestione: soglia e ascolti');
    // classifiche e musica libera
    const cls = ges.slice(ges.indexOf('// ----- Classifiche e musica libera'));
    const j = {blocco:null, attivo:true, pausa:null, ora:'', fonte:{in_coda:X, nome:'F'}, oggi:N, totale:X, provati:N, spazio_libero_gb:12, spazio_min_gb:'" onerror="alert(3)',
      jamendo:true, ultimi:[], libere:[{esito:'ok', brani:X, nome:'L', letta:0}], classifiche:[]};
    carica(cls, ['disegna', 'riga', 'licenza'], ['const pill ='], {$, api:() => j, quando:() => 'oggi', alle:() => 'alle 10:00', document:{querySelectorAll:() => []}}).disegna();
    pulito(els.cl.innerHTML, 'gestione: numeri delle classifiche');
    fatte.push('gestione');
  });

  /* ---------- controllo: l'indirizzo d'origine di un brano diventa un link solo se è http o https (x/controllo.html) ---------- */
  gruppo(() => {
    const con = modulo('controllo.html');
    const els = {}, $ = id => els[id] || (els[id] = {innerHTML:'', hidden:false});
    const b = url => ({id:'b1', origine:'scaricato', chi_nome:'Anna', quando:0, copertina:'', titolo:'T', artista:'A', doppioni:[], durata:0, dimensione:0, formato:'', nome:'',
      url, tag:{titolo:'', artista:'', album:''}});
    const prova = url => {
      carica(con, ['disegna'], [], {$, D:{brani:[b(url)], scartati:[]}, aperto:'b1', conferma:'', bozza:{}, versione:1, document:{querySelectorAll:() => []},
        disegnaFilm() {}, quando:() => '', durata:() => '', misura:() => '', copUrl:() => '', campo:() => ''}).disegna();
      return els.brani.innerHTML;
    };
    for (const u of ['javascript:alert(1)', ' JavaScript:alert(1)', 'data:text/html,<script>alert(1)</script>'])
      vero(!/href="\s*(javascript|data):/i.test(prova(u)), 'controllo: link ' + u + ' → ' + prova(u).slice(0, 300));
    vero(prova('https://www.youtube.com/watch?v=abc').indexOf('<a href="https://www.youtube.com/watch?v=abc"') >= 0, 'controllo: il link https non c\'è più');
    fatte.push('controllo');
  });

  /* ---------- strumenti: le misure che arrivano dal Mac (x/strumenti.html) ---------- */
  gruppo(() => {
    const F = carica(modulo('strumenti.html'), ['fmtB'], [], {});
    pulito(F.fmtB('<img src=x onerror=alert(1)>'), 'strumenti: fmtB di un testo');
    eq(F.fmtB(null), '—', 'strumenti: fmtB senza numero');
    vero(/ KB$/.test(F.fmtB(2048)), 'strumenti: fmtB di un numero ' + F.fmtB(2048));
    fatte.push('strumenti');
  });
}
