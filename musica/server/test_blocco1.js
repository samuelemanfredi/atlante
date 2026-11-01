// Prove del «blocco 1» delle funzioni di Apple Music che mancavano (musica/index.html): coda modificabile, ricerche
// recenti, fissati in cima, cartelle di playlist, equalizzatore, compositori, pronuncia dei testi, condividi un verso.
// Con JavaScriptCore, che c'è su ogni Mac:
//
//     osascript -l JavaScript test_blocco1.js ../index.html
//
// Le funzioni si prendono dall'app così come sono (non se ne copia nessuna) e si provano da sole, con un ambiente finto.
// Scrive «OK …» se va tutto bene, «PROVA FALLITA: …» se no (test_extra.py lo controlla).
ObjC.import('Foundation');

function run(argv) {
  const fatte = [];
  try {
    prove(leggi(argv[0] || '../index.html'), fatte);
    return 'OK ' + fatte.length + ' prove: ' + fatte.join(', ');
  } catch (e) {
    return 'PROVA FALLITA: ' + ((e && e.message) || e) + (fatte.length ? ' (dopo: ' + fatte.join(', ') + ')' : '');
  }
}

function leggi(p) {
  const s = $.NSString.stringWithContentsOfFileEncodingError(p, $.NSUTF8StringEncoding, null);
  if (!s || s.isNil()) throw new Error('non trovo ' + p);
  return s.js;
}

/* ---------- estrazione delle funzioni dall'app (come in test_mix.js) ---------- */
function estrai(src, nome) {
  const m = new RegExp('\\n(async )?function ' + nome + '\\(').exec(src);
  if (!m) throw new Error("nell'app manca la funzione " + nome);
  const i = m.index + 1;
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
// più righe: dall'inizio dato fino alla chiusura (es. «]];» di una tabella su più righe)
function righeFino(src, inizio, chiusura) {
  const i = src.indexOf('\n' + inizio);
  if (i < 0) throw new Error("nell'app manca «" + inizio + '…»');
  const j = src.indexOf(chiusura + '\n', i);
  if (j < 0) throw new Error("«" + inizio + "…» non finisce con «" + chiusura + '»');
  return src.slice(i + 1, j + chiusura.length);
}
function riga(src, inizio) {
  const i = src.indexOf('\n' + inizio);
  if (i < 0) throw new Error("nell'app manca la riga «" + inizio + '…»');
  return src.slice(i + 1, src.indexOf('\n', i + 1));
}
// le funzioni (e le righe) date, eseguite con «env» come ambiente: quello che non definiscono lo cercano lì
// asinc: le funzioni «async» da far girare con le promesse finte (vedi sincrona)
function carica(src, nomi, righe, env, asinc) {
  if (asinc && asinc.length) env.corri = corri;
  const codice = righe.map(r => Array.isArray(r) ? righeFino(src, r[0], r[1]) : riga(src, r)).join('\n') + '\n' + nomi.map(n => asinc && asinc.indexOf(n) >= 0 ? sincrona(src, n) : estrai(src, n)).join('\n');
  const f = new Function('env', 'with (env) { return (function () {\n' + codice + '\nreturn {' + nomi.join(',') + '};\n})(); }');
  return f(env);
}
/* ---------- promesse finte (come in test_mix.js): si risolvono con drena(), come i microtask del browser ---------- */
const MICRO = [];
function drena() { let n = 0; while (MICRO.length) { if (++n > 1e6) throw new Error('promesse senza fine'); MICRO.shift()(); } }
class SP {
  constructor(ex) {
    this.s = 0; this.v = undefined; this.cb = []; let fatto = false;
    const ok = v => { if (!fatto) { fatto = true; this._ris(v); } }, ko = e => { if (!fatto) { fatto = true; this._fin(2, e); } };
    try { ex(ok, ko); } catch (e) { ko(e); }
  }
  _ris(v) { if (v && typeof v.then === 'function') MICRO.push(() => v.then(x => this._fin(1, x), e => this._fin(2, e))); else this._fin(1, v); }
  _fin(s, v) { if (this.s) return; this.s = s; this.v = v; this.cb.splice(0).forEach(f => MICRO.push(f)); }
  then(ok, ko) {
    return new SP((res, rej) => {
      const f = () => { try { if (this.s === 1) res(typeof ok === 'function' ? ok(this.v) : this.v); else if (typeof ko === 'function') res(ko(this.v)); else rej(this.v); } catch (e) { rej(e); } };
      if (this.s) MICRO.push(f); else this.cb.push(f);
    });
  }
  catch(ko) { return this.then(null, ko); }
  finally(fn) { return this.then(v => { fn(); return v; }, e => { fn(); throw e; }); }
  static resolve(v) { return v instanceof SP ? v : new SP(r => r(v)); }
}
function sincrona(src, nome) {
  const f = estrai(src, nome);
  vero(f.startsWith('async function ' + nome + '('), nome + ' non è più async: aggiornare la prova');
  const testa = f.slice(0, f.indexOf('{') + 1), corpo = f.slice(f.indexOf('{') + 1, -1);
  return testa.replace('async function', 'function') + ' return corri(function* () {' + corpo.replace(/\bawait /g, 'yield ') + '}.bind(this)); }';
}
function corri(gen) {
  return new SP((res, rej) => {
    const it = gen();
    const passo = (v, err) => { let r; try { r = err ? it.throw(v) : it.next(v); } catch (e) { return rej(e); } if (r.done) return res(r.value); SP.resolve(r.value).then(x => passo(x), e => passo(e, true)); };
    passo();
  });
}
const fine = p => { let v, e = null; p.then(x => { v = x; }, x => { e = x; }); drena(); if (e) throw e; return v; };   // il risultato di una promessa finta
function vero(c, msg) { if (!c) throw new Error(msg); }
function uguale(a, b, msg) { const x = JSON.stringify(a), y = JSON.stringify(b); if (x !== y) throw new Error(msg + ': ' + x + ' invece di ' + y); }
function vicino(a, b, tol, msg) { if (!(Math.abs(a - b) <= tol)) throw new Error(msg + ': ' + a + ' invece di ' + b + ' (±' + tol + ')'); }
const memoria = () => { const m = {}; return {m, get:(k, d) => k in m ? JSON.parse(m[k]) : d, set:(k, v) => { m[k] = JSON.stringify(v); }}; };

function prove(src, fatte) {
  const prova = (nome, fn) => { fn(); fatte.push(nome); };
  prova('sintassi dell\'app', () => sintassi(src));
  prova('coda modificabile', () => codaModificabile(src));
  prova('ricerche recenti', () => ricercheRecenti(src));
  prova('fissati in cima', () => fissatiInCima(src));
  prova('cartelle di playlist', () => cartelleDiPlaylist(src));
  prova('equalizzatore', () => equalizzatore(src));
  prova('compositori e opere', () => compositoriEOpere(src));
  prova('pronuncia dei testi', () => pronunciaTesti(src));
  prova('condividi un verso', () => condividiVerso(src));
}

// tutto lo script dell'app si compila (non si esegue)
function sintassi(src) {
  const re = /<script>([\s\S]*?)<\/script>/g; let m, n = 0;
  while ((m = re.exec(src))) { try { new Function(m[1]); } catch (e) { throw new Error('lo script dell\'app non si compila: ' + e.message); } n++; }
  vero(n >= 2, 'script dell\'app non trovati');
  // un'icona ridefinita (I.nome = … due volte, o un nome già nell'oggetto I) ne cancella un'altra: il pollice in giù di
  // «Suggerisci meno» stava per diventare il − della coda
  const base = (src.slice(src.indexOf('const I = {'), src.indexOf('};', src.indexOf('const I = {'))).match(/^  ([A-Za-z0-9]+):'/gm) || []).map(x => x.trim().slice(0, -2));
  const dopo = (src.match(/\nI\.([A-Za-z0-9]+) = /g) || []).map(x => x.slice(3, -3));
  const visti = new Set(base);
  for (const n of dopo) { vero(!visti.has(n), 'icona definita due volte: ' + n); visti.add(n); }
  vero(base.length > 30 && dopo.length > 10, 'icone non trovate (' + base.length + ', ' + dopo.length + ')');
}

/* ---------- coda modificabile ---------- */
function codaModificabile(src) {
  const toasts = [], salvati = [];
  const env = {S:{queue:'abcde'.split(''), orig:'abcde'.split(''), qi:1, shuffle:false}, MIX:{act:null, salto:null, plan:null}, au:{currentTime:10},
    toast:m => toasts.push(m), saveState:() => salvati.push(1), renderQueue() {}, devInvia() {}};
  const F = carica(src, ['codaBloccata', 'codaCambiata', 'codaSposta', 'codaTogli', 'codaSvuota'], ['const CODA_ATTESA = '], env);
  const q = () => env.S.queue.join(''), o = () => env.S.orig.join('');
  vero(F.codaSposta(4, 2) && q() === 'abecd' && o() === 'abecd', 'sposta e prima di c: ' + q() + ' / ' + o());
  vero(F.codaSposta(2, 5) && q() === 'abcde', 'sposta e in fondo: ' + q());
  vero(!F.codaSposta(3, 3) && !F.codaSposta(3, 4) && salvati.length === 2, 'stesso posto: niente');
  vero(!F.codaSposta(1, 4) && !F.codaSposta(0, 3) && !F.codaTogli(1) && !F.codaTogli(0) && q() === 'abcde', 'il brano in corso e i precedenti non si toccano');
  vero(F.codaSposta(4, 0) && q() === 'abecd', 'mai prima del brano in corso: ' + q());
  vero(F.codaTogli(2) && q() === 'abcd' && o() === 'abcd', 'togli: ' + q() + ' / ' + o());
  // con il casuale l'ordine di prima (orig) resta, perde solo il brano tolto
  Object.assign(env.S, {shuffle:true, queue:'adbc'.split(''), orig:'abcd'.split(''), qi:1});
  vero(F.codaSposta(3, 2) && q() === 'adcb' && o() === 'abcd', 'casuale, sposta: ' + q() + ' / ' + o());
  vero(F.codaTogli(2) && q() === 'adb' && o() === 'abd', 'casuale, togli: ' + q() + ' / ' + o());
  vero(F.codaSvuota() && q() === 'ad' && o() === 'ad' && /Tolto 1 brano/.test(toasts.pop()), 'svuota: ' + q() + ' / ' + o());
  vero(!F.codaSvuota(), 'niente da svuotare');
  // durante un passaggio tra i brani non si tocca niente
  Object.assign(env.S, {shuffle:false, queue:'abc'.split(''), orig:'abc'.split(''), qi:0}); env.MIX.act = {};
  vero(!F.codaSposta(2, 1) && !F.codaTogli(1) && !F.codaSvuota() && toasts.length === 3 && q() === 'abc', 'durante la dissolvenza: niente');
  env.MIX.act = null; env.MIX.salto = {};
  vero(F.codaBloccata(), 'salto in preparazione: bloccata');
  env.MIX.salto = null; env.MIX.plan = {st:'ready', tR:20}; env.au.currentTime = 19.6;
  vero(F.codaBloccata(), 'rampa a fine brano: bloccata');
  env.au.currentTime = 10; vero(!F.codaBloccata(), 'mix pronto ma rampa lontana: libera');
  env.MIX.plan = {st:'prep', tR:20}; env.au.currentTime = 25; vero(!F.codaBloccata(), 'mix in preparazione: libera (si rifà da solo)');
  // la riga: ≡, − e il tocco che salta lì
  vero(/data-a="qdel" data-i="' \+ k \+ '"/.test(src) && /class="qh" data-i="' \+ k \+ '"/.test(src) && /data-a="qsvuota">Svuota</.test(src), 'righe della coda: ≡, − e Svuota');
  vero(/case 'qdel': codaTogli\(\+d\.i\); break;/.test(src) && /case 'qsvuota': codaSvuota\(\); break;/.test(src), 'tasti della coda collegati');
}

/* ---------- ricerche recenti ---------- */
function ricercheRecenti(src) {
  const ls = memoria(), esc = x => String(x).replace(/[&<>"']/g, c => '&#' + c.charCodeAt(0) + ';');
  const env = {ls, esc, UNK_ART:'Artista sconosciuto', ic:n => '<svg ' + n + '>', art:(c, k) => '<i art ' + (k || '') + '>', mosaic:() => '<i mosaico>', plTracks:() => [],
    artists:() => [{name:'Dua Lipa', cover:null}], albums:() => [{key:'k1', name:'Future Nostalgia', artist:'Dua Lipa'}],
    P:[{id:'p1', name:'Corsa'}], T:new Map([['rS1', {id:'rS1', title:'Levitating', artist:'Dua Lipa'}], ['rS2', {id:'rS2', title:'Via', gone:true}]])};
  const F = carica(src, ['ricercheLeggi', 'ricercaAggiungi', 'ricercaDaTocco', 'ricercaRiga', 'ricercheHtml'], ['const RICERCHE = '], env);
  uguale(F.ricercheHtml(), '', 'niente ricerche: niente sezione');
  F.ricercaAggiungi({t:'artist', k:'Dua Lipa'}); F.ricercaAggiungi({t:'q', k:'levit'}); F.ricercaAggiungi({t:'artist', k:'Dua Lipa'});
  uguale(F.ricercheLeggi(), [{t:'artist', k:'Dua Lipa'}, {t:'q', k:'levit'}], 'la più recente in cima, senza doppioni');
  for (let i = 0; i < 20; i++) F.ricercaAggiungi({t:'q', k:'q' + i});
  vero(F.ricercheLeggi().length === 15 && F.ricercheLeggi()[0].k === 'q19', 'al più 15');
  F.ricercaAggiungi(null); F.ricercaAggiungi({t:'q', k:''}); vero(F.ricercheLeggi().length === 15, 'vuote: niente');
  ls.set('am-cerca-recenti', [{t:'song', k:'rS1'}, {t:'song', k:'rS2'}, {t:'album', k:'k1'}, {t:'playlist', k:'p1'}, {t:'playlist', k:'via'}, {t:'q', k:'a "b" <c>'}, {t:'artist', k:'Dua Lipa'}, 7]);
  const h = F.ricercheHtml();
  vero(/Ricerche recenti<button class="hcanc" data-a="srecvia">Cancella/.test(h), 'titolo e Cancella');
  vero(/data-a="srecs" data-k="rS1"/.test(h) && !/rS2/.test(h), 'brano sì, brano sparito no');
  vero(/data-a="go" data-v="album" data-k="k1"/.test(h) && /data-a="go" data-v="playlist" data-k="p1"/.test(h) && !/data-k="via"/.test(h), 'album e playlist (quelle sparite no)');
  vero(/data-a="srecq" data-k="a &#34;b&#34; &#60;c&#62;"/.test(h) && !/<c>/.test(h), 'parole cercate, al sicuro: ' + h.slice(0, 0));
  vero(/data-a="go" data-v="artist" data-k="Dua Lipa"/.test(h), 'artista');
  // il tocco tra i risultati: che cosa si ricorda
  const el = (sel) => ({closest:s => sel[s] || null});
  uguale(F.ricercaDaTocco(el({'[data-a="go"]':{dataset:{v:'artist', k:'Dua Lipa'}}})), {t:'artist', k:'Dua Lipa'}, 'artista toccato');
  uguale(F.ricercaDaTocco(el({'[data-a="go"]':{dataset:{v:'albums'}}})), null, 'Sfoglia › Album: niente');
  uguale(F.ricercaDaTocco(el({'.trk[data-id]':{dataset:{id:'rS1'}}, '[data-a="play"]':{}})), {t:'song', k:'rS1'}, 'brano toccato');
  uguale(F.ricercaDaTocco(el({'.trk[data-id]':{dataset:{id:'rS1'}}})), null, '… ma non i suoi ⋯');
  vero(/case 'srecvia': ls\.set\(RICERCHE\.k, \[\]\); render\(true\); break;/.test(src) && /return ricercheHtml\(\) \+ '<h2 class="h2" style="margin-top:12px">Sfoglia/.test(src), 'collegamenti: Cancella e la sezione sopra Sfoglia');
}

/* ---------- fissati in cima alla Libreria ---------- */
function fissatiInCima(src) {
  const ls = memoria(), log = {toast:[], sync:0, render:0}, esc = x => String(x).replace(/[&<>"']/g, c => '&#' + c.charCodeAt(0) + ';');
  const env = {ls, esc, UNK_ART:'?', SYNC:{corre:false, visti:{}}, toast:m => log.toast.push(m), syncPresto:() => log.sync++, render:() => log.render++,
    ic:n => '<svg ' + n + '>', art:(c, k) => '<i ' + (k || '') + '>', mosaic:() => '<i mosaico>', plTracks:() => [],
    albumByKey:k => k === 'A\u0001X' ? {key:k, name:'X', artist:'A'} : null, artists:() => [{name:'Mina'}], P:[{id:'p1', name:'Corsa'}],
    T:new Map([['r1', {id:'r1', title:'Brano', artist:'Lui'}], ['r2', {id:'r2', title:'Via', gone:true}]]), Date:{now:() => 1234}};
  const F = carica(src, ['fissato', 'fissaCambia', 'applicaFissati', 'fissaTasto', 'fissaVoce', 'fissatoTile', 'fissatiHtml'], ['const FIX = '], env);
  uguale(F.fissatiHtml(), '', 'niente fissati: niente sezione');
  vero(F.fissaCambia('album', 'A\u0001X') === true && F.fissato('album', 'A\u0001X'), 'fissa un album');
  vero(log.sync === 1 && log.toast.pop() === 'Fissato in cima alla Libreria' && ls.get('am-fissati-agg') === 1234, 'si salva e parte la sincronizzazione');
  F.fissaCambia('song', 'r1'); F.fissaCambia('artist', 'Mina'); F.fissaCambia('playlist', 'p1'); F.fissaCambia('song', 'r2'); F.fissaCambia('playlist', 'via');
  uguale(ls.get('am-fissati').map(x => x.t + ':' + x.k), ['playlist:via', 'song:r2', 'playlist:p1', 'artist:Mina', 'song:r1', 'album:A\u0001X'], 'il più recente in cima');
  const h = F.fissatiHtml();
  vero(/<h2 class="h2"[^>]*>Fissati<\/h2><div class="grid fis">/.test(h), 'sezione Fissati');
  vero(/data-a="fisplay" data-k="r1"/.test(h) && !/r2/.test(h) && !/data-k="via"/.test(h), 'brano sì, quelli spariti no');
  vero(/data-v="album" data-k="A&#1;X"|data-v="album" data-k="A\u0001X"/.test(h) && /data-v="artist" data-k="Mina"/.test(h) && /data-v="playlist" data-k="p1"/.test(h), 'album, artista e playlist');
  vero(h.indexOf('data-k="p1"') < h.indexOf('data-k="Mina"') && h.indexOf('data-k="Mina"') < h.indexOf('data-k="r1"'), 'nell\'ordine');
  vero(F.fissaCambia('song', 'r1') === false && !F.fissato('song', 'r1') && log.toast.pop() === 'Tolto dai Fissati', 'togli');
  vero(/aria-pressed="true"/.test(F.fissaTasto('artist', 'Mina')) && /aria-pressed="false"/.test(F.fissaTasto('artist', 'Nessuno')), 'puntina accesa e spenta');
  vero(/Togli dai Fissati/.test(F.fissaVoce('playlist', 'p1')) && /Fissa in cima alla Libreria/.test(F.fissaVoce('song', 'r9')), 'voce del menu');
  for (let i = 0; i < 40; i++) F.fissaCambia('song', 's' + i);
  vero(ls.get('am-fissati').length === 30 && ls.get('am-fissati')[0].k === 's39', 'al più 30');
  // dall'account (un altro dispositivo)
  F.applicaFissati({updated:2000, fissati:[{t:'artist', k:'Mina', x:1}, null, {t:'song'}]});
  uguale(ls.get('am-fissati'), [{t:'artist', k:'Mina'}], 'arrivati dall\'account, ripuliti');
  vero(env.SYNC.visti._fissati === 2000 && ls.get('am-fissati-agg') === 2000, 'visti');
  env.SYNC.corre = true; F.fissaCambia('song', 'r1'); vero(env.SYNC.fixToccati === true, 'cambiati durante la sincronizzazione');
  F.applicaFissati({updated:3000, fissati:[]}); vero(F.fissato('song', 'r1'), 'quelli appena cambiati qui non si perdono');
  // collegamenti: sincronizzazione e pagine
  vero(/if \(FIX\.agg && FIX\.agg !== SYNC\.visti\._fissati\) manda\.push\(\{id:'_fissati', name:'', ids:\[\], fissati:FIX\.lista, updated:FIX\.agg\}\);/.test(src), 'si mandano al Mac');
  vero(/if \(l\.id === '_fissati'\) \{ applicaFissati\(l\); continue; \}/.test(src), 'si ricevono dal Mac');
  vero(/h \+= fissatiHtml\(\);\n    h \+= '<div class="lrows">'/.test(src), 'in cima alla Libreria');
  vero(/topBar\(fissaTasto\('artist', r\.k\)\)/.test(src) && /topBar\(fissaTasto\('album', a\.key\)\)/.test(src) && /fissaVoce\('song', id\)/.test(src) && /fissaVoce\('playlist', d\.k\)/.test(src), 'puntina su artista, album, brano e playlist');
}

/* ---------- cartelle di playlist ---------- */
function cartelleDiPlaylist(src) {
  const log = {salvate:[], toast:[], render:0, chiusa:0};
  const env = {P:[{id:'a', name:'Uno', ids:[]}, {id:'b', name:'Due', ids:[], cartella:'Sport'}, {id:'c', name:'Tre', ids:[], cartella:'Cena'}, {id:'d', name:'Quattro', ids:[], cartella:'Sport'}],
    coll:{compare:(x, y) => x.localeCompare(y)}, tabs:{lib:[{v:'lib'}, {v:'playlists'}, {v:'cartella', k:'Sport'}]},
    savePl:p => { log.salvate.push(p.id + ':' + (p.cartella || '')); return Promise.resolve(); }, toast:m => log.toast.push(m), render:() => log.render++, closeSheet:() => log.chiusa++};
  env.savePl = p => { log.salvate.push(p.id + ':' + (p.cartella || '')); return SP.resolve(); };
  const F = carica(src, ['cartelle', 'cartellaMetti', 'cartellaRinomina'], ['const nomeCartella = '], env, ['cartellaMetti', 'cartellaRinomina']);
  uguale(F.cartelle().map(c => c.nome + ':' + c.pl.map(p => p.id).join('')), ['Cena:c', 'Sport:bd'], 'cartelle in ordine, con le loro playlist');
  vero(fine(F.cartellaMetti('a', '  Nuova   cartella ')) === true && env.P[0].cartella === 'Nuova cartella' && log.salvate.pop() === 'a:Nuova cartella', 'in una cartella nuova');
  vero(/«Uno» è in «Nuova cartella»/.test(log.toast.pop()) && log.chiusa === 1, 'avviso e foglio chiuso');
  vero(fine(F.cartellaMetti('a', 'Nuova cartella')) === false && !log.toast.length, 'già lì: niente');
  vero(fine(F.cartellaMetti('a', '')) === true && !('cartella' in env.P[0]) && /fuori dalle cartelle/.test(log.toast.pop()), 'fuori dalle cartelle');
  vero(fine(F.cartellaMetti('zz', 'X')) === false, 'playlist che non c\'è');
  vero(fine(F.cartellaRinomina('Sport', ' Allenamento ')) === 2 && env.P[1].cartella === 'Allenamento' && env.P[3].cartella === 'Allenamento', 'rinomina: tutte le sue playlist');
  vero(env.tabs.lib[2].k === 'Allenamento', 'rinomina: anche la pagina aperta');
  vero(fine(F.cartellaRinomina('Cena', '')) === 1 && !('cartella' in env.P[2]) && env.P.length === 4, 'togli la cartella: la playlist resta, fuori');
  uguale(F.cartelle().map(c => c.nome), ['Allenamento'], 'resta una cartella');
  fine(F.cartellaMetti('a', 'x'.repeat(100))); vero(env.P[0].cartella.length === 60, 'nome al più 60 caratteri');
  vero(/if \(p\.cartella\) x\.cartella = p\.cartella;/.test(src) && /if \(l\.cartella\) n\.cartella = l\.cartella; else delete n\.cartella;/.test(src), 'la cartella va e viene con la sincronizzazione');
  vero(/h \+= cartelle\(\)\.map\(cartRiga\)\.join\(''\) \+ P\.filter\(p => !p\.cartella\)\.map\(plRiga\)\.join\(''\);/.test(src), 'in Playlist prima le cartelle, poi le playlist fuori');
  vero(/case 'cartsposta':/.test(src) && /else if \(kind === 'cartnuova'\) \{ await cartellaMetti\(k, name\); \}/.test(src) && /else if \(kind === 'cartren'\)/.test(src), 'menu e nomi delle cartelle collegati');
}

/* ---------- equalizzatore ---------- */
function equalizzatore(src) {
  // un AudioContext finto: nodi, collegamenti e valori
  class Par { constructor(v) { this.value = v; } cancelScheduledValues() {} setTargetAtTime(v) { this.value = v; } setValueAtTime(v) { this.value = v; } }
  class Nodo { constructor(tipo) { this.tipo = tipo; this.uscite = []; this.gain = new Par(1); this.frequency = new Par(350); this.Q = new Par(1); this.type = ''; } connect(n) { this.uscite.push(n); return n; } }
  let creati = 0;
  class Ctx { constructor() { creati++; this.state = 'running'; this.currentTime = 0; this.destination = new Nodo('casse'); this.sorgenti = []; }
    createGain() { return new Nodo('gain'); } createBiquadFilter() { return new Nodo('filtro'); }
    createMediaElementSource(el) { const n = new Nodo('sorgente'); n.el = el; this.sorgenti.push(n); return n; } resume() { return {catch() {}}; } addEventListener() {} }
  const ls = memoria(), log = {toast:[], chiusa:0};
  const env = {ls, MIX:{ctx:null, gains:new Map(), norm:new Map(), on:true}, NEEDS_MIXER:false, IOS_PRIMA_27:false, userVol:0.5, AudioContext:Ctx, window:{AudioContext:Ctx},
    navigator:{}, DECKS:[{}, {}], au:{paused:true}, AUX:{}, diario() {}, document:{hidden:false}, Math, esc:x => x, ic:n => '<' + n + '>', cancel:'',
    applyVol() {}, toast:m => log.toast.push(m), closeSheet:() => log.chiusa++, openSheet:h => { log.foglio = h; }};
  const F = carica(src, ['eqValori', 'eqNome', 'eqPre', 'eqCostruisci', 'eqLivello', 'eqApplica', 'eqDisponibile', 'eqScegli', 'eqSheet', 'mixerInit'],
    ['const EQ_BANDE = ', 'const EQ = ', 'const eqImposta = '], env);
  env.EQ_PRESET = null;   // i preset li legge dalla riga vera
  const P = new Function('return ' + src.slice(src.indexOf('const EQ_PRESET = [') + 18, src.indexOf(']]];', src.indexOf('const EQ_PRESET = [')) + 3))();
  env.EQ_PRESET = P;
  vero(P.length >= 20 && P[0][0] === 'no' && P[0][2].every(v => v === 0), 'preset: «No» per primo, piatto');
  vero(P.every(p => p[2].length === 10 && p[2].every(v => typeof v === 'number' && Math.abs(v) <= 8)), 'ogni preset: 10 bande, al più ±8 dB');
  vero(new Set(P.map(p => p[0])).size === P.length && new Set(P.map(p => p[1])).size === P.length, 'nomi e chiavi diversi');
  vicino(F.eqPre(P[0][2]), 1, 1e-12, 'piatto: niente margine'); vicino(F.eqPre([0, 7, 0, 0, 0, 0, 0, 0, 0, 0]), Math.pow(10, -5 / 20), 1e-12, '+7 dB: 5 dB di margine');
  vero(!/rock/.test(ls.get('am-eq', '')), 'spento all\'inizio');
  // Mac: senza equalizzatore il mixer non si accende; scegliendo un preset sì, e fa anche il volume
  F.mixerInit(); vero(creati === 0, 'Mac senza equalizzatore: niente mixer');
  vero(F.eqScegli('rock') === true && creati === 1 && ls.get('am-eq') === 'rock' && /Equalizzatore: Rock/.test(log.toast.pop()), 'Mac: il mixer si accende per l\'equalizzatore');
  const ctx = env.MIX.ctx; vero(ctx && env.MIX.gains.size === 2, 'i due lettori nel mixer');
  // catena: sorgente → dissolvenza → volume uniforme → preamp → 10 filtri → casse
  const pre = ctx.sorgenti[0].uscite[0].uscite[0].uscite[0];
  vero(pre && pre === ctx.sorgenti[1].uscite[0].uscite[0].uscite[0], 'i due lettori passano dallo stesso equalizzatore');
  let n = pre.uscite[0], tipi = [], freq = [];
  while (n && n.tipo === 'filtro') { tipi.push(n.type); freq.push(n.frequency.value); n = n.uscite[0]; }
  uguale(freq, [32, 64, 125, 250, 500, 1000, 2000, 4000, 8000, 16000], 'le 10 bande');
  vero(tipi[0] === 'lowshelf' && tipi[9] === 'highshelf' && tipi.slice(1, 9).every(t => t === 'peaking') && n === ctx.destination, 'filtri giusti, poi le casse');
  const rock = P.find(p => p[0] === 'rock')[2];
  let f = pre.uscite[0], g = []; while (f.tipo === 'filtro') { g.push(f.gain.value); f = f.uscite[0]; }
  uguale(g, rock, 'valori del preset Rock');
  vicino(pre.gain.value, F.eqPre(rock) * 0.5, 1e-9, 'sul Mac il preamp porta anche il volume');
  env.userVol = 1; F.eqLivello(); vicino(pre.gain.value, F.eqPre(rock), 1e-9, 'volume al massimo');
  F.eqScegli('no'); f = pre.uscite[0]; while (f.tipo === 'filtro') { vero(f.gain.value === 0, 'No: tutte le bande a 0'); f = f.uscite[0]; }
  vero(/Equalizzatore spento/.test(log.toast.pop()), 'spento');
  vero(F.eqScegli('inventato') === false, 'preset che non c\'è');
  // iPhone: il volume resta ai tasti (preamp senza userVol); prima di iOS 27 niente equalizzatore
  env.NEEDS_MIXER = true; env.userVol = 0.3; F.eqScegli('bassi-su'); vicino(pre.gain.value, F.eqPre(P.find(p => p[0] === 'bassi-su')[2]), 1e-9, 'iPhone: volume dei tasti');
  env.IOS_PRIMA_27 = true; vero(!F.eqDisponibile() && F.eqScegli('rock') === false, 'prima di iOS 27: non disponibile');
  F.eqSheet(); vero(/serve iOS 27/.test(log.foglio) && !/data-a="eq"/.test(log.foglio), 'il foglio lo dice');
  env.IOS_PRIMA_27 = false; F.eqSheet(); vero(/data-a="eq" data-k="bassi-su" aria-pressed="true"><span class="l">Aumento bassi<\/span><check>/.test(log.foglio), 'il preset scelto è segnato');
  vero(/'<button data-a="m-eq"><span class="l">Equalizzatore: ' \+ esc\(eqNome\(EQ\.id\)\)/.test(src) && /case 'eq': eqScegli\(d\.k\); break;/.test(src), 'nel menu del brano in riproduzione');
  vero(/n\.connect\(uscita\)/.test(src) && /if \(MIX\.gains\.has\(el\)\) \{ el\.volume = 1; eqLivello\(\); return; \}/.test(src), 'collegamenti del mixer e del volume');
}

/* ---------- compositori e opere ---------- */
function compositoriEOpere(src) {
  const T = new Map(), env = {T, coll:{compare:(a, b) => a.localeCompare(b)}, WeakMap, I:{}};
  const F = carica(src, ['compositoreDi', 'operaDi', 'compositori', 'haCompositori', 'opereDi'],
    [['const COMP_CATALOGHI = ', ']];'], ['const COMP_NOMI = ', ']];'], 'const COMP_GENERE = ', 'const COMP_PAROLE = ', 'const compClassica = ', 'const COMPC = '], env);
  const casi = [
    [{title:'Partenope, HWV 27, Act 1: Recitativo. "Regina, in folte schiere" (Ormonte, Arsace)', artist:'Philippe Jaroussky & George Frideric Handel', genre:'Classical', album:'Partenope'},
      'Georg Friedrich Händel', 'Partenope, HWV 27', 'Act 1: Recitativo. "Regina, in folte schiere" (Ormonte, Arsace)'],
    [{title:'IV. Recitativo. Wer sollte sich demnach (Alto)', artist:'Netherlands Bach Collegium, Pieter Jan Leusink & Sytse Buwalda & Johann Sebastian Bach', album:'Bach Edition: Cantatas Vol. 1'},
      'Johann Sebastian Bach', 'Bach Edition: Cantatas Vol. 1', 'IV. Recitativo. Wer sollte sich demnach (Alto)'],
    [{title:'Handel: Tolomeo, Re d\'Egitto HWV 25 / Act 3 - Rec.: Eccoti, o Prence', artist:'Pietro Spagnoli, Romina Basso'}, 'Georg Friedrich Händel', 'Tolomeo, Re d\'Egitto HWV 25', 'Act 3 - Rec.: Eccoti, o Prence'],
    [{title:'Symphony No. 40 in G Minor, K. 550: I. Molto allegro', artist:'Berliner Philharmoniker', genre:'Classical'}, 'Wolfgang Amadeus Mozart', 'Symphony No. 40 in G Minor, K. 550', 'I. Molto allegro'],
    [{title:'Piano Sonata No. 21 in B-Flat Major, D. 960: I. Molto moderato', artist:'Alfred Brendel'}, 'Franz Schubert', 'Piano Sonata No. 21 in B-Flat Major, D. 960', 'I. Molto moderato'],
    [{title:'Le quattro stagioni: La primavera, RV 269: I. Allegro', artist:'I Musici'}, 'Antonio Vivaldi', 'Le quattro stagioni: La primavera, RV 269', 'I. Allegro'],
    [{title:'Goldberg Variations, BWV 988: Aria', artist:'Glenn Gould', album:'Bach: Goldberg'}, 'Johann Sebastian Bach', 'Goldberg Variations, BWV 988', 'Aria'],
    [{title:'Aria', artist:'Glenn Gould', composer:'Johann Sebastian Bach', work:'Goldberg-Variationen, BWV 988', movement:'Aria'}, 'Johann Sebastian Bach', 'Goldberg-Variationen, BWV 988', 'Aria'],
    [{title:'Overture', artist:'Il Pomo d\'Oro', composer:'George Frideric Handel', genre:'Classical'}, 'Georg Friedrich Händel', null, 'Overture'],
    [{title:'Levitating', artist:'Dua Lipa', composer:'Dua Lipa,  Clarence Coffee Jr., Sarah Hudson', genre:'Pop'}, 'Dua Lipa, Clarence Coffee Jr., Sarah Hudson', '', 'Levitating'],
    [{title:'Bellaquita', artist:'Mozart La Para', genre:'Reggaeton'}, '', '', 'Bellaquita'],
    [{title:'99 Problems', artist:'Jay-Z', genre:'Hip-Hop'}, '', '', '99 Problems'],
    [{title:'D 12 Remix', artist:'Gruppo', genre:'House'}, '', '', 'D 12 Remix'],
    [{title:'Symphony in D minor', artist:'Orchestra', genre:'Pop'}, '', '', 'Symphony in D minor'],
    [{title:'La donna è mobile', artist:'Luciano Pavarotti & Giuseppe Verdi'}, 'Giuseppe Verdi', null, null],
    [{title:'Nocturne Op. 9 No. 2', artist:'Chopin'}, 'Fryderyk Chopin', null, null],
    [{title:'Mozart', artist:'Pop band', genre:'Pop'}, '', '', 'Mozart']];
  casi.forEach(([t, c, o, m], i) => {
    vero(F.compositoreDi(t) === c, 'compositore ' + i + ' (' + t.title + '): «' + F.compositoreDi(t) + '» invece di «' + c + '»');
    const x = F.operaDi(t);
    if (o !== null) vero(x.opera === o, 'opera ' + i + ': «' + x.opera + '» invece di «' + o + '»');
    if (m !== null) vero(x.mov === m, 'movimento ' + i + ': «' + x.mov + '» invece di «' + m + '»');
  });
  // ricordato, ma rifatto se il brano cambia
  const t = {title:'Aria', artist:'X'}; vero(F.compositoreDi(t) === '', 'nessuno'); t.composer = 'Antonio Vivaldi'; vero(F.compositoreDi(t) === 'Antonio Vivaldi', 'il tag arriva dopo: si rifà');
  // la Libreria: compositori con le loro opere, i movimenti nell'ordine dell'album
  vero(!F.haCompositori(), 'libreria vuota: niente Compositori');
  const brano = (id, x) => T.set(id, Object.assign({id}, x));
  brano('a', {title:'Goldberg Variations, BWV 988: Variatio 2', album:'Goldberg', track:3, artist:'Glenn Gould', cover:'c1'});
  brano('b', {title:'Goldberg Variations, BWV 988: Aria', album:'Goldberg', track:1, artist:'Glenn Gould'});
  brano('c', {title:'Cello Suite No. 1, BWV 1007: Prélude', album:'Suites', track:1, artist:'Yo-Yo Ma'});
  brano('d', {title:'Levitating', artist:'Dua Lipa', composer:'Dua Lipa'});
  brano('e', {title:'Toccata', artist:'Johann Sebastian Bach', genre:'Classical', gone:true});
  vero(F.haCompositori(), 'adesso sì');
  const C = F.compositori();
  uguale(C.map(c => c.nome + ':' + c.brani.length + ':' + c.opere), ['Dua Lipa:1:0', 'Johann Sebastian Bach:3:2'], 'compositori, brani e opere (quelli spariti no)');
  const O = F.opereDi(C[1]);
  uguale(O.map(o => o.nome + ':' + o.brani.map(x => x.id).join('')), ['Cello Suite No. 1, BWV 1007:c', 'Goldberg Variations, BWV 988:ba'], 'opere in ordine, movimenti nell\'ordine dell\'album');
  vero(O[1].cover === 'c1' && O[1].artista === 'Glenn Gould', 'copertina e interprete dell\'opera');
  vero(/\(haCompositori\(\) \? row\('compositori', 'compositore', 'Compositori'\) : ''\)/.test(src), 'riga Compositori in Libreria (solo se ce ne sono)');
  vero(/composer:b\.composer \|\| undefined, work:b\.work \|\| undefined, movement:b\.movement \|\| undefined/.test(src), 'dal catalogo del Mac');
}

/* ---------- pronuncia dei testi ---------- */
function pronunciaTesti(src) {
  const ls = memoria(), log = {fetch:[], toast:[], disegni:0}, esc = x => String(x).replace(/[&<>"']/g, c => '&#' + c.charCodeAt(0) + ';');
  let risposta = null;
  const env = {ls, esc, SRV:true, XAPI:'api/extra/', I:{}, TESTO:{j:{stato:'ok'}, righe:[]}, toast:m => log.toast.push(m), testoDisegna:() => log.disegni++, testoTasti() {},
    $:() => null, JSON, Map, Set,
    fetchT:(u, o) => { log.fetch.push([u, JSON.parse(o.body).righe]); return SP.resolve({ok:true, json:() => SP.resolve(risposta(JSON.parse(o.body).righe))}); }};
  const F = carica(src, ['testoPronRiga', 'testoPronChiedi', 'testoPronCambia'], ['const PRON = ', 'const PRON_ALTRO = ', 'const testoPronServe = '], env);
  const R = ['Ciao amore', 'Привет', '我爱你', 'Привет', 'ありがとう', ''].map(testo => ({testo}));
  env.TESTO.righe = R;
  uguale(R.map(r => F.testoPronRiga(r)), ['', '', '', '', '', ''], 'spenta: niente');
  F.testoPronChiedi(R); vero(!log.fetch.length, 'spenta: non chiede niente');
  F.testoPronCambia(); vero(ls.get('am-pronuncia') === true && /Pronuncia accesa/.test(log.toast.pop()) && log.disegni === 1, 'accesa e ridisegnata');
  risposta = r => ({righe:r.map(x => x === 'Привет' ? 'Privet' : x === '我爱你' ? 'wǒ ài nǐ' : x === 'ありがとう' ? 'arigatou' : x)});
  F.testoPronChiedi(R); F.testoPronChiedi(R);   // la seconda volta le righe sono già in attesa
  vero(log.fetch.length === 1 && log.fetch[0][0] === 'api/extra/pronuncia', 'una richiesta sola al Mac');
  uguale(log.fetch[0][1], ['Привет', '我爱你', 'ありがとう'], 'solo le righe in altri alfabeti, una volta ciascuna');
  drena(); vero(log.disegni === 2, 'al ritorno si ridisegna');
  uguale(R.map(r => F.testoPronRiga(r)), ['', '<span class="lp">Privet</span>', '<span class="lp">wǒ ài nǐ</span>', '<span class="lp">Privet</span>', '<span class="lp">arigatou</span>', ''], 'sotto le righe');
  F.testoPronChiedi(R); vero(log.fetch.length === 1, 'già fatte: niente di nuovo');
  R.push({testo:'<b>한국</b>'}); risposta = r => ({righe:['<i>hangug</i>']});
  F.testoPronChiedi(R); drena(); vero(F.testoPronRiga(R[R.length - 1]) === '<span class="lp">&#60;i&#62;hangug&#60;/i&#62;</span>', 'al sicuro: ' + F.testoPronRiga(R[R.length - 1]));
  F.testoPronCambia(); vero(ls.get('am-pronuncia') === false && F.testoPronRiga(R[1]) === '', 'spenta di nuovo');
  vero(/const sotto = r => testoPronRiga\(r\) \+ \(tr\.has\(r\)/.test(src) && /else if \(a === 'testo-pron'\) testoPronCambia\(\);/.test(src) && /id="nplPron" data-a="testo-pron"/.test(src), 'collegamenti nel testo');
}

/* ---------- condividi un verso ---------- */
function condividiVerso(src) {
  const log = {toast:[], prepara:0};
  const R = ['Prima riga', '', 'Seconda riga', 'Terza', 'Quarta', 'Quinta', 'Sesta'].map(testo => ({testo}));
  const env = {TESTO:{righe:R, riga:-1}, toast:m => log.toast.push(m), $:() => null, setTimeout:() => 0, clearTimeout() {}};
  const F = carica(src, ['versoTocca', 'versoSegna', 'versoRighe', 'versoACapo', 'versoDisegna'], ['const VERSO = '], env);
  env.versoPrepara = () => log.prepara++;
  const V = () => F.versoRighe();
  vero(F.versoTocca(2) && F.versoTocca(0) && V().join('|') === 'Prima riga|Seconda riga', 'scelte, nell\'ordine del testo');
  vero(!F.versoTocca(1) && V().length === 2, 'i puntini di una pausa non si scelgono');
  vero(F.versoTocca(3) && F.versoTocca(4) && !F.versoTocca(5) && /Al più 4/.test(log.toast.pop()) && V().length === 4, 'al più 4 righe');
  vero(F.versoTocca(0) && V().join('|') === 'Seconda riga|Terza|Quarta', 'un secondo tocco la toglie');
  vero(log.prepara === 5, 'l\'immagine si prepara a ogni scelta (' + log.prepara + ')');
  // a capo: misura finta, 10 px a carattere
  const ctx = {measureText:t => ({width:t.length * 10})};
  uguale(F.versoACapo(ctx, 'uno due tre quattro cinque', 100), ['uno due', 'tre', 'quattro', 'cinque'], 'a capo nelle parole');
  uguale(F.versoACapo(ctx, '我爱你中国北京欢迎你', 40), ['我爱你中', '国北京欢', '迎你'], 'cinese: a capo nei caratteri');
  uguale(F.versoACapo(ctx, 'supercalifragilistichespiralidoso', 100).join(''), 'supercalifragilistichespiralidoso', 'parola lunghissima spezzata, niente perso');
  vero(F.versoACapo(ctx, 'supercalifragilistichespiralidoso', 100).every(l => l.length <= 10), 'ogni pezzo nella riga');
  // la scheda: tutto quello che serve, e le righe più piccole se sono tante
  const disegno = righe => {
    const scritto = [];
    const c = {measureText:t => ({width:t.length * (parseInt(c.font.split(' ')[1]) || 40) * 0.5}), font:'', fillStyle:'', globalAlpha:1, textBaseline:'',
      createLinearGradient:() => ({addColorStop() {}}), fillRect() {}, save() {}, restore() {}, beginPath() {}, roundRect() {}, rect() {}, clip() {}, drawImage:() => scritto.push(['img']),
      fillText:(t, x, y) => scritto.push([t, x, y, c.font])};
    const n = F.versoDisegna(c, {righe, titolo:'Levitating', artista:'Dua Lipa', colore:'#123', img:{}});
    return {scritto, n};
  };
  const a = disegno(['If you wanna run away with me, I know a galaxy']);
  const testi = a.scritto.map(x => x[0]);
  vero(testi[0] === 'img' && testi.indexOf('Levitating') > 0 && testi.indexOf('Dua Lipa') > 0 && testi.indexOf('Atlante Musica') > 0, 'copertina, titolo, artista e firma: ' + JSON.stringify(testi));
  vero(a.scritto.every(x => x[0] === 'img' || (x[1] >= 90 && x[1] < 1080 && x[2] > 0 && x[2] <= 1350)), 'tutto dentro la scheda');
  const grande = a.scritto.find(x => /galaxy|If you/.test(x[0]))[3], piccolo = disegno(Array(4).fill('Una riga lunga quanto basta per andare a capo almeno due volte nella scheda')).scritto.find(x => /Una riga/.test(x[0]))[3];
  vero(parseInt(grande.split(' ')[1]) > parseInt(piccolo.split(' ')[1]), 'tante righe: più piccole (' + grande + ' / ' + piccolo + ')');
  vero(/else if \(a === 'testo-vai' && VERSO\.on\) versoTocca\(\+x\.dataset\.i\);/.test(src) && /else if \(a === 'verso-condividi'\) versoCondividi\(\);/.test(src) && /id="nplVerso" data-a="testo-verso"/.test(src), 'collegamenti nel testo');
  vero(/navigator\.canShare && navigator\.canShare\(\{files:\[f\]\}\)/.test(src) && /a\.download = nome/.test(src), 'condivisione dell\'iPhone o, se no, l\'immagine scaricata');
}
