// Prove del testo sincronizzato (karaoke) nel lettore dell'app (musica/index.html) con JavaScriptCore, che c'è su ogni Mac:
//
//     osascript -l JavaScript test_testi.js ../index.html
//
// Le funzioni si prendono dall'app così come sono (come in test_mix.js) e si provano con un ambiente finto: la riga e la
// parola del momento, le righe da mostrare, il brano di cui chiedere il testo, il tocco su una riga (sul lettore che suona,
// anche durante il mix tra due brani, e con il telecomando). Scrive «OK …» o «PROVA FALLITA: …» (test_extra.py lo controlla).
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
function riga(src, inizio) {
  const i = src.indexOf('\n' + inizio);
  if (i < 0) throw new Error("nell'app manca la riga «" + inizio + '…»');
  return src.slice(i + 1, src.indexOf('\n', i + 1));
}
function carica(src, nomi, righe, env) {
  const codice = righe.map(r => riga(src, r)).join('\n') + '\n' + nomi.map(n => estrai(src, n)).join('\n');
  const f = new Function('env', 'with (env) { return (function () {\n' + codice + '\nreturn {' + nomi.join(',') + '};\n})(); }');
  return f(env);
}

function prove(src, fatte) {
  const eq = (a, b, m) => { const x = JSON.stringify(a), y = JSON.stringify(b); if (x !== y) throw new Error(m + ': ' + x + ' invece di ' + y); };
  const COST = ['const TESTO_ANTICIPO', 'const TESTO_PAUSA'];

  /* ---------- riga e parola del momento ---------- */
  const F = carica(src, ['testoRiga', 'testoParola', 'testoFine', 'testoRighe'], COST, {});
  const R = [{t:1, testo:'Uno'}, {t:3, testo:'Due'}, {t:7.5, testo:'Tre'}];
  // la riga si accende un attimo prima (TESTO_ANTICIPO = 0,25 s), poi resta accesa fino alla prossima
  eq([0, 0.5, 0.8, 1, 2.9, 3, 7.3, 100].map(t => F.testoRiga(R, t)), [-1, -1, 0, 0, 1, 1, 2, 2], 'riga del momento');
  eq(F.testoRiga([], 5), -1, 'nessuna riga');
  fatte.push('riga');

  const P = [{t:10, testo:'Hello '}, {t:10.5, testo:'big '}, {t:11, testo:'world'}];
  eq(F.testoParola(P, 12, 9.9), {i:-1, q:0}, 'prima della prima parola');
  eq(F.testoParola(P, 12, 10.25), {i:0, q:0.5}, 'a metà della prima parola');
  eq(F.testoParola(P, 12, 11.5), {i:2, q:0.5}, "a metà dell'ultima (fino alla fine della riga)");
  eq(F.testoParola(P, 12, 13), {i:2, q:1}, 'riga finita');
  // fine della riga: quella della fonte; se no la riga dopo, ma l'ultima parola dura al più 1,5 s
  const R2 = [{t:10, testo:'Hello world', parole:[{t:10, testo:'Hello '}, {t:10.5, testo:'world'}]}, {t:20, testo:'Dopo'}];
  eq(F.testoFine(R2, 0), 12, 'ultima parola al più 1,5 s');
  eq(F.testoFine([{t:10, testo:'x', fine:11, parole:P}, {t:20, testo:'y'}], 0), 11, 'fine dalla fonte');
  eq(F.testoFine([{t:10, testo:'x', parole:[{t:10, testo:'x'}, {t:11, testo:'y'}]}, {t:11.2, testo:'y'}], 0), 11.2, 'la riga dopo arriva prima');
  fatte.push('parola');

  // righe da mostrare: i puntini dell'introduzione (prima riga dopo TESTO_PAUSA s), una sola pausa di seguito, niente
  // pausa in fondo; il testo senza tempi resta com'è
  eq(F.testoRighe({sincronizzato:true, righe:[{t:6, testo:'A'}, {t:8, testo:''}, {t:8.5, testo:''}, {t:9, testo:'B'}, {t:30, testo:''}]}),
    [{t:0, testo:''}, {t:6, testo:'A'}, {t:8, testo:''}, {t:9, testo:'B'}], 'righe a tempo');
  eq(F.testoRighe({sincronizzato:true, righe:[{t:2, testo:'A'}]}), [{t:2, testo:'A'}], 'senza introduzione');
  const semplici = [{t:null, testo:'A'}, {t:null, testo:''}, {t:null, testo:'B'}];
  eq(F.testoRighe({sincronizzato:false, righe:semplici}), semplici, 'righe senza tempi');
  fatte.push('righe');

  /* ---------- il brano di cui chiedere il testo ---------- */
  const T = new Map([
    ['r1', {id:'r1', remote:true, sid:'abc', title:'Kings & Queens', artist:'Ava Max', album:'Heaven & Hell', duration:162.7, cover:'s:k'}],
    ['f1', {id:'f1', title:'Canzone', artist:'Anna', album:'', duration:200.4, cover:'c1'}],
    ['p1', {id:'p1', ext:true, title:'Episodio', artist:'Podcast'}]]);
  const E = {T, TT:id => T.get(id), inLib:t => t && t.remote && t.sid, curId:'r1', au:{duration:NaN}, RC:{id:null, dev:null}, XAPI:'api/extra/'};
  const B = carica(src, ['testoBrano'], [], E);
  let b = B.testoBrano();
  eq([b.url, b.k, b.titolo, b.cover], ['api/extra/testi/abc', 's:abc', 'Kings & Queens', 's:k'], 'brano della libreria');
  E.curId = 'f1'; b = B.testoBrano();
  eq([b.url, b.k], ['api/extra/testi?titolo=Canzone&artista=Anna&album=&durata=200', 'q:canzone|anna||200'], 'brano solo sul telefono');
  E.curId = 'p1'; eq(B.testoBrano(), null, 'podcast: niente testo');
  E.curId = null; eq(B.testoBrano(), null, 'niente in riproduzione');
  E.RC = {id:'x', dev:{stato:{brano:{sid:'def', titolo:'Remoto', artista:'Duo', album:'', durata:100, copertina:''}}}};
  eq(B.testoBrano().url, 'api/extra/testi/def', 'brano del dispositivo comandato');
  E.RC.dev.stato.brano = {sid:'', titolo:'Fuori & dentro', artista:'Duo', album:'', durata:99.6, copertina:'x.jpg'};
  b = B.testoBrano();
  eq([b.url, b.cover], ['api/extra/testi?titolo=Fuori%20%26%20dentro&artista=Duo&album=&durata=100', 'u:x.jpg'], 'brano solo sul dispositivo comandato');
  E.RC.dev.stato = {}; eq(B.testoBrano(), null, 'il dispositivo comandato non suona niente');
  fatte.push('brano');

  /* ---------- tocco su una riga ---------- */
  const lettore = (t, ferma) => ({currentTime:t, paused:!!ferma, suonato:0, play() { this.paused = false; this.suonato++; return {catch() {}}; }});
  const V = {RC:{id:null, dev:null}, curId:'r1', MIX:{act:null}, fine:0, tic:0, inviati:[]};
  V.xfEnd = () => { V.fine++; V.MIX.act = null; };
  V.tick = () => { V.tic++; };
  V.rcInvia = (a, x) => { V.inviati.push([a, x === undefined ? null : x]); };
  const G = carica(src, ['testoVai'], [], V);
  V.au = lettore(5); G.testoVai(42);
  eq([V.au.currentTime, V.au.suonato, V.fine, V.tic], [42, 0, 0, 1], 'salto sul lettore che suona');
  V.au = lettore(5, true); G.testoVai(-3);
  eq([V.au.currentTime, V.au.suonato], [0, 1], 'in pausa: salto e riparte');
  // durante il mix: suonano due lettori, «au» è quello del brano nuovo (di cui si vede il testo). Il salto va lì e il
  // brano che esce si ferma (fine del passaggio)
  const esce = lettore(200), entra = lettore(3);
  V.au = entra; V.MIX.act = {out:esce, inn:entra};
  G.testoVai(30);
  eq([entra.currentTime, esce.currentTime, V.fine, V.MIX.act], [30, 200, 1, null], 'salto durante il mix');
  // telecomando: il salto va al dispositivo comandato; se era in pausa riparte
  V.au = lettore(1); V.RC = {id:'x', dev:{stato:{suona:false}}};
  G.testoVai(12.5);
  eq([V.inviati, V.RC.dev.stato.suona, V.au.currentTime], [[['seek', {t:12.5}], ['play', null]], true, 1], 'salto col telecomando');
  V.inviati = []; G.testoVai(20);
  eq(V.inviati, [['seek', {t:20}]], 'col telecomando, mentre suona');
  V.RC = {id:null, dev:null}; V.curId = null; V.au = lettore(7); G.testoVai(50);
  eq(V.au.currentTime, 7, 'niente in riproduzione: niente salto');
  fatte.push('tocco');
}
