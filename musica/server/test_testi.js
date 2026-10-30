// Prove del testo sincronizzato (karaoke) nel lettore dell'app (musica/index.html) con JavaScriptCore, che c'è su ogni Mac:
//
//     osascript -l JavaScript test_testi.js ../index.html
//
// Le funzioni si prendono dall'app così come sono (come in test_mix.js) e si provano con un ambiente finto: la riga e la
// parola del momento, le righe da mostrare (anche le pause lunghe), il brano di cui chiedere il testo, il tocco su una riga
// (sul lettore che suona, anche durante il mix tra due brani, e con il telecomando), la luce che scorre lettera per lettera,
// i tocchi su parole e puntini, l'indirizzo con la traduzione. Scrive «OK …» o «PROVA FALLITA: …» (test_extra.py lo controlla).
ObjC.import('Foundation');

function run(argv) {
  const fatte = [];
  try {
    prove(leggi(argv[0] || '../index.html'), fatte);
    proveTraduzione(leggi(argv[0] || '../index.html'), fatte);
    if (argv[1]) proveGestione(leggi(argv[1]), fatte);   // la pagina Gestione (x/gestione.html), se data
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

  /* ---------- karaoke: la luce che scorre, le pause lunghe, i tocchi su parole e puntini, la traduzione ---------- */
  const K = carica(src, ['testoRighe', 'testoFine', 'testoParola', 'testoPezzi', 'testoTocco', 'testoPesi', 'testoLettera', 'testoUrl'],
    ['const TESTO_PAUSA', 'const TESTO_VOC'], {});
  // una pausa lunga dopo una riga che sa dove finisce (4 s o più): i puntini, anche senza una riga vuota
  const A = {t:2, testo:'A', fine:3}, Bp = {t:9, testo:'B b', parole:[{t:9, testo:'B '}, {t:9.5, testo:'b'}]}, C = {t:12, testo:'C'};
  eq(K.testoRighe({sincronizzato:true, righe:[A, Bp, C]}), [A, {t:3, testo:''}, Bp, C], 'pausa lunga dopo una riga');
  // senza fine (testo vecchio salvato sul telefono): non si sa quando finisce, niente puntini inventati
  eq(K.testoRighe({sincronizzato:true, righe:[{t:2, testo:'A'}, {t:20, testo:'B'}]}), [{t:2, testo:'A'}, {t:20, testo:'B'}], 'senza fine niente pausa');
  // una riga vuota c'è già: una sola pausa
  eq(K.testoRighe({sincronizzato:true, righe:[A, {t:4, testo:''}, {t:20, testo:'B'}]}), [A, {t:4, testo:''}, {t:20, testo:'B'}], 'una pausa sola');
  fatte.push('pause');

  // la parola finisce prima della successiva quando dopo c'è una pausa (fine della parola)
  const P2 = [{t:10, testo:'Uno '}, {t:10.5, testo:'due ', fine:11}, {t:13, testo:'tre'}];
  eq(K.testoParola(P2, 14, 10.75), {i:1, q:0.5}, 'a metà di una parola che finisce prima della pausa');
  eq(K.testoParola(P2, 14, 12), {i:1, q:1}, 'nella pausa la parola resta accesa');
  eq(K.testoParola(P2, 14, 13.5), {i:2, q:0.5}, 'la parola dopo la pausa');
  eq(K.testoPezzi({t:5, testo:'Sola'}), [{t:5, testo:'Sola'}], 'riga senza parole: un pezzo solo');
  eq(K.testoPezzi(Bp), Bp.parole, 'le parole della riga');
  fatte.push('parole');

  // la luce dentro la parola: lettera per lettera, lenta sulle vocali (3) e veloce sulle consonanti (1)
  const Cw = K.testoPesi('ciao');
  eq(Cw, [0, 1, 4, 7, 10], 'pesi delle lettere');
  eq(K.testoLettera(Cw, 0), {i:0, q:0}, 'la luce comincia');
  eq(K.testoLettera(Cw, 0.05), {i:0, q:0.5}, 'la consonante passa in fretta (un decimo della parola)');
  eq(K.testoLettera(Cw, 0.25), {i:1, q:0.5}, 'la vocale si tiene');
  eq(K.testoLettera(Cw, 1), {i:3, q:1}, 'la luce arriva in fondo');
  eq(K.testoPesi('a b'), [0, 3, 3.15, 4.15], 'gli spazi quasi niente');
  fatte.push('luce');

  // un tocco: sulla riga, su una parola, nei puntini (il punto toccato vale il suo secondo)
  const RT = [{t:0, testo:''}, {t:10, testo:'Uno due', parole:[{t:10, testo:'Uno '}, {t:10.6, testo:'due'}]}, {t:20, testo:''}, {t:30, testo:'Fine'}];
  eq(K.testoTocco(RT, 1, null, null), 10, 'tocco sulla riga');
  eq(K.testoTocco(RT, 1, 1, null), 10.6, 'tocco su una parola');
  eq(K.testoTocco(RT, 0, null, 0.5), 5, "tocco a metà dei puntini dell'introduzione");
  eq(K.testoTocco(RT, 2, null, 0.25), 22.5, 'tocco nei puntini di una pausa');
  eq(K.testoTocco(RT, 2, null, 3), 30, 'oltre i puntini: la riga dopo');
  eq(K.testoTocco(RT, 3, 0, null), 30, 'una riga senza parole: il suo inizio');
  eq(K.testoTocco(RT, 9, null, null), null, 'riga che non c\'è');
  eq(K.testoTocco([{t:null, testo:'senza tempi'}], 0, null, null), null, 'testo senza tempi: niente salto');
  fatte.push('tocchi');

  eq(K.testoUrl({url:'api/extra/testi/abc'}, 'en'), 'api/extra/testi/abc?lingua=en', 'traduzione, brano della libreria');
  eq(K.testoUrl({url:'api/extra/testi?titolo=x&durata=3'}, 'pt'), 'api/extra/testi?titolo=x&durata=3&lingua=pt', 'traduzione, brano sul telefono');
  eq(K.testoUrl({url:'api/extra/testi/abc'}, ''), 'api/extra/testi/abc', 'senza traduzione');
  fatte.push('traduzione');

  /* ---------- dopo la revisione ---------- */
  // parole stimate (Whisper non ha ancora ascoltato): la loro fine non si sa, niente puntini in mezzo a una riga cantata
  const Bs = {t:2, testo:'A b', parole:[{t:2, testo:'A '}, {t:2.4, testo:'b'}]}, Cs = {t:20, testo:'C'};
  eq(K.testoRighe({sincronizzato:true, parole_da:'stima', righe:[Bs, Cs]}), [Bs, Cs], 'parole stimate: niente puntini inventati');
  eq(K.testoRighe({sincronizzato:true, parole_da:'voce', righe:[Bs, Cs]}), [Bs, {t:3.9, testo:''}, Cs], 'parole dalla voce: i puntini nella pausa');
  const Bf = {t:2, testo:'A b', fine:3, parole:Bs.parole};
  eq(K.testoRighe({sincronizzato:true, parole_da:'stima', righe:[Bf, Cs]}), [Bf, {t:3, testo:''}, Cs], 'fine della fonte: i puntini anche con le parole stimate');
  fatte.push('pause stimate');

  // da destra a sinistra (ebraico, arabo): la luce cresce dal bordo destro, senza tornare indietro tra una lettera e l'altra
  const KL = carica(src, ['testoLettera', 'testoLuceX'], [], {});
  const scatole = [[20, 30], [10, 20], [0, 10]], pesi = [0, 1, 2, 3];
  const rtl = [0, 0.1, 0.2, 0.3, 0.33, 0.34, 0.5, 0.66, 0.67, 0.8, 1].map(q => KL.testoLuceX(scatole, KL.testoLettera(pesi, q), true, 30));
  eq([rtl[0], rtl[rtl.length - 1]], [0, 30], 'da destra a sinistra: dal bordo destro fino in fondo');
  for (let k = 1; k < rtl.length; k++) if (rtl[k] < rtl[k - 1] - 1e-9) throw new Error('da destra a sinistra la luce torna indietro: ' + JSON.stringify(rtl));
  eq(KL.testoLuceX([[0, 10], [10, 20], [20, 30]], KL.testoLettera(pesi, 0.5), false, 30), 15, 'da sinistra a destra');
  fatte.push('destra a sinistra');

  // il Mac lavora ancora (Whisper, traduzione): si richiede solo per il brano di adesso e solo se il testo si vede
  const Pv = {TESTO:{k:'s:1', vis:false, sospeso:''}, carichi:[]};
  Pv.testoCarica = b => Pv.carichi.push(b.k);
  const PR = carica(src, ['testoRiprendi'], [], Pv);
  PR.testoRiprendi({k:'s:1'});
  eq([Pv.carichi, Pv.TESTO.sospeso], [[], 's:1'], 'lettore chiuso: niente richieste, si riprende dopo');
  Pv.TESTO.k = 's:2'; Pv.TESTO.sospeso = '';
  PR.testoRiprendi({k:'s:1'});
  eq([Pv.carichi, Pv.TESTO.sospeso], [[], ''], 'brano cambiato: niente richieste');
  Pv.TESTO = {k:'s:1', vis:true, sospeso:''};
  PR.testoRiprendi({k:'s:1'});
  eq(Pv.carichi, ['s:1'], 'testo in vista: si richiede');
  const timer = [];
  const Av = {TESTO:{on:true, vis:true, raf:1, k:'s:1', b:{k:'s:1'}, sospeso:'', poll:7, riga:0, primo:false, visto:0},
    np:{classList:{contains:() => true}}, document:{hidden:true}, requestAnimationFrame:() => 1, testoGiro() {},
    clearTimeout:x => timer.push(x), carichi:[]};
  Av.testoCarica = b => Av.carichi.push(b.k);
  const AV = carica(src, ['testoAvvia'], [], Av);
  AV.testoAvvia();   // il testo esce di vista con una richiesta in attesa: si ferma
  eq([timer, Av.TESTO.poll, Av.TESTO.sospeso, Av.carichi], [[7], 0, 's:1', []], 'testo chiuso: la richiesta in attesa si ferma');
  Av.document.hidden = false;
  AV.testoAvvia();   // di nuovo in vista: riprende
  eq([Av.carichi, Av.TESTO.sospeso], [['s:1'], ''], 'di nuovo in vista: si riprende a chiedere');
  fatte.push('richieste');

  /* ---------- revisione di sicurezza (28/10): il Mac sta cercando il testo («in arrivo»), troppe richieste (429) ---------- */
  const IC = new Function('return (function () {\n' + riga(src, 'const testoInCorso') + '\nreturn testoInCorso;\n})();')();
  eq([IC({stato:'in arrivo'}), IC({stato:'ok'}), IC({stato:'ok', allineamento:'in coda'}), IC(null)], [true, false, true, false],
    'il Mac ci lavora ancora');
  const E2 = carica(src, ['testoEsito', 'testoAttesa'], [], {});
  const copia = {stato:'ok', righe:[{t:1, testo:'dal telefono'}]};
  eq(E2.testoEsito(true, 200, {stato:'in arrivo'}, null), {j:{stato:'in arrivo'}}, '«in arrivo»: non si salva sul telefono');
  eq(E2.testoEsito(true, 200, {stato:'in arrivo'}, copia), {j:copia, riprova:true, presto:true}, '«in arrivo» con la copia sul telefono');
  eq(!!E2.testoEsito(true, 200, {stato:'ok', righe:[]}, null).salva, true, 'il testo vero si salva sul telefono');
  const t429 = E2.testoEsito(false, 429, {errore:'Hai cercato molti testi nuovi: riprova tra poco'}, null);
  eq([t429.j.stato, t429.j.tardi, t429.j.errore, t429.riprova, t429.presto, !!t429.salva],
    ['errore', true, 'Hai cercato molti testi nuovi: riprova tra poco', true, true, false], '429: «riprova tra poco», niente salvato');
  eq(E2.testoEsito(false, 429, null, null).j.errore, 'Troppe richieste di testi: riprova tra poco', '429 senza messaggio del Mac');
  eq(E2.testoEsito(false, 429, null, copia), {j:copia, riprova:true, presto:true}, '429 con la copia sul telefono');
  eq(E2.testoEsito(false, 503, {errore:'LRCLIB non risponde'}, null), {j:{stato:'errore', errore:'LRCLIB non risponde'}, riprova:true},
    'altro errore: si richiede dopo mezzo minuto');
  eq(E2.testoEsito(false, 404, null, null), {j:{stato:'nessuno'}, riprova:true}, 'brano che non c\'è');
  // quando si richiede: «in arrivo» presto; dopo un 429 una volta sola (niente richieste all'infinito)
  eq(E2.testoAttesa({stato:'in arrivo'}, null, false), 2500, 'in arrivo: si richiede tra 2,5 s');
  eq([E2.testoAttesa(t429.j, null, false), E2.testoAttesa(t429.j, null, true)], [10000, 0], '429: un tentativo, poi basta');
  eq(E2.testoAttesa({stato:'ok', allineamento:'in coda'}, null, false), 15000, 'Whisper in coda');
  eq(E2.testoAttesa({stato:'ok'}, {stato:'in corso'}, false), 3000, 'traduzione in corso');
  eq(E2.testoAttesa({stato:'ok'}, null, false), 0, 'pronto: niente richieste');
  fatte.push('in arrivo e 429');
}

/* ---------- doppio testo: all'inizio nella lingua del telefono, oppure quella scelta (o nessuna) ---------- */
function proveTraduzione(src, fatte) {
  const eq = (a, b, m) => { const x = JSON.stringify(a), y = JSON.stringify(b); if (x !== y) throw new Error(m + ': ' + x + ' invece di ' + y); };
  const L = riga(src, 'const TESTO_LINGUE');
  let lingue = L, i = src.indexOf('\n' + L);
  for (let k = src.indexOf('\n', i + 1); !/\]\];\s*$/.test(lingue); k = src.indexOf('\n', k + 1)) lingue = src.slice(i + 1, src.indexOf('\n', k + 1));
  const F = new Function('return (function () {\n' + lingue + '\n' + estrai(src, 'testoTradRisolvi') + '\nreturn testoTradRisolvi; })();')();
  eq(['it-IT', 'pt-BR', 'zh-Hant-TW', 'en-US', 'nb-NO', 'xx-YY', ''].map(l => F('auto', l)), ['it', 'pt', 'zh', 'en', 'no', 'en', 'en'],
    'lingua del telefono');
  eq([F('', 'it-IT'), F('de', 'it-IT'), F('ja', 'en-US')], ['', 'de', 'ja'], 'scelta a mano (o nessuna) vale più del telefono');
  // all'inizio (mai scelto) è «auto»: il doppio testo c'è senza toccare niente
  if (!/scelta:ls\.get\('am-trad', 'auto'\)/.test(src)) throw new Error("all'inizio la traduzione deve essere nella lingua del telefono");
  if (!/data-v="auto"/.test(src)) throw new Error('nel menu manca «Lingua del telefono»');
  // ogni dispositivo la sua lingua: la scelta resta sul dispositivo, e se la sua lingua cambia ad app aperta la traduzione la segue
  if (!/addEventListener\('languagechange'/.test(src)) throw new Error('la traduzione non segue la lingua del dispositivo quando cambia');
  fatte.push('traduzione nella lingua del telefono');
}

function proveGestione(src, fatte) {
  const eq = (a, b, m) => { const x = JSON.stringify(a), y = JSON.stringify(b); if (x !== y) throw new Error(m + ': ' + x + ' invece di ' + y); };
  // Gestione → Testi e karaoke: niente di pesante senza un sì (Homebrew, il modello di 574 MB), anche se si accende solo Whisper
  const G = carica(src, ['testiConferma'], [], {});
  const nuovo = {acceso:true, con_whisper:false, whisper:{modello_scaricato:false}};
  if (!/574 MB/.test(G.testiConferma(nuovo, {whisper:true}))) throw new Error('Whisper acceso col lavoro già acceso: manca la domanda');
  const spento = {acceso:false, con_whisper:true, whisper:{modello_scaricato:false}};
  const d = G.testiConferma(spento, {acceso:true});
  if (!/574 MB/.test(d) || !/Lo accendo\?/.test(d)) throw new Error('acceso con Whisper: ' + d);
  eq([G.testiConferma(spento, {acceso:false}), G.testiConferma(nuovo, {whisper:false})], ['', ''], 'spegnere non si chiede');
  const pronto = {acceso:true, con_whisper:false, whisper:{modello_scaricato:true}};
  const e = G.testiConferma(pronto, {whisper:true});
  if (!e || /574 MB/.test(e)) throw new Error('Whisper già scaricato: ' + e);
  if (/574 MB/.test(G.testiConferma({acceso:false, con_whisper:false, whisper:{}}, {acceso:true}))) throw new Error('senza Whisper non si scarica niente');
  fatte.push('gestione');
}
