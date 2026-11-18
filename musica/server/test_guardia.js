// Prove del guardiano della riproduzione (musica/index.html) con JavaScriptCore, che c'è su ogni Mac:
//
//     osascript -l JavaScript test_guardia.js ../index.html
//
// La funzione si prende dall'app così com'è e si prova con un lettore finto: quando dovrebbe suonare ma il tempo non va
// avanti, riparte dallo stesso punto; al terzo blocco dello stesso brano passa al successivo; in pausa, durante un
// passaggio del mix o con il tempo che avanza non fa niente. Se il brano che serve è solo sul Mac e il Mac non risponde
// (13/11: l'aggiornamento automatico che riavvia il server), aspetta il Mac senza contare tentativi e riparte da lì
// appena torna. Scrive «OK …» o «PROVA FALLITA: …».
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
// JavaScriptCore qui non fa girare le promesse da solo: le funzioni «async» dell'app diventano sincrone (via «await»),
// e le finte (fetchT, r.json) rispondono subito, come fa test_mix.js con le sue promesse finte
function sincrona(f) { return f.replace(/^async function/, 'function').replace(/\bawait /g, ''); }
function carica(src, nomi, env) {
  const codice = nomi.map(n => sincrona(estrai(src, n))).join('\n');
  const f = new Function('env', 'with (env) { return (function () {\n' + codice + '\nreturn {' + nomi.join(',') + '};\n})(); }');
  return f(env);
}

function ambiente() {
  const env = {
    ora: 100000, perf: 0, carichi: [], avanti: [], avvisi: [], diari: [], suonati: 0, chiusi: [],
    au: null, curId: 'b1', RC: {id:null}, MIX: {act:null, salto:null, ctx:null}, CH: {aperta:false},
    S: {qi:3, queue:['a', 'b', 'c', 'b1', 'd']}, navigator: {onLine:true}, document: {hidden:false},
    suonaVoluta: 99000, pausaVoluta: 0, AUX: {nome:'aux'},
    GUARDIA: {t:-1, da:0, id:null, volte:0, FERMO:8000, ARRIVA:15000, PARTE:5000, MIXMAX:15000, SALTOMAX:25000},
    seekH: null, seekOnLoad: {at:0},   // nessun brano che sta tornando a un punto
    INTERR: {da:0, uscita:0, prova:0, MAX:30 * 60000, OGNI:3000}, interrotta: false,
    MACATT: null, ERRATT: null, SRV: true, SRVKO: 0, srvWait: false, sulMac: new Set(), sulTelefono: new Set(), macTorna: false, richieste: 0,
  };
  env.TT = id => (id ? {id, remote:env.sulMac.has(id)} : null);
  env.sulTel = id => env.sulTelefono.has(id);
  env.loadServer = () => ({then(si) { env.richieste++; if (env.macTorna) env.SRV = true; si(!!env.macTorna); }});
  env.render = () => {}; env.runDl = () => {};
  env.riavvii = []; env.riavviaApp = (perche, suonava) => { env.riavvii.push([perche, suonava]); };
  env.sessioneInterrotta = () => env.interrotta;
  const lettore = nome => ({nome, paused:false, ended:false, currentTime:42, readyState:4, networkState:1, error:null, _sbloccato:true,
    getAttribute: () => 'blob:x', removeAttribute() {}, load() {}, pause() { this.paused = true; },
    play() { env.suonati++; return {catch() {}}; }});
  env.DECKS = [lettore('A'), lettore('B')]; env.au = env.DECKS[0]; env.DIRETTO = lettore('D');
  env.diretti = []; env.passaAlDiretto = perche => { env.diretti.push(perche); return true; };
  env.risvegli = 0; env.risveglia = () => { env.risvegli++; };
  env.memoria = {}; env.ls = {get: (k, d) => (k in env.memoria ? env.memoria[k] : d), set: (k, v) => { env.memoria[k] = v; }};
  env.Date = {now: () => env.ora};
  env.performance = {now: () => env.perf};
  env.load = (i, play, at) => { env.carichi.push([env.au.nome, i, play, at]); };
  env.next = auto => { env.avanti.push(auto); };
  env.toast = m => { env.avvisi.push(m); };
  env.diario = e => { env.diari.push(e); };
  env.xfEnd = () => { env.chiusi.push('mix'); env.MIX.act = null; };
  env.saltoAnnulla = () => { env.chiusi.push('salto'); env.MIX.salto = null; };
  env.updateNow = () => {};
  return env;
}

function prove(src, fatte) {
  const eq = (a, b, m) => { const x = JSON.stringify(a), y = JSON.stringify(b); if (x !== y) throw new Error(m + ': ' + x + ' invece di ' + y); };
  let env, F;
  // MACATT com'è nell'app (i tempi dell'attesa del Mac)
  const mm = /\nconst MACATT = (\{[^\n]*\});/.exec(src), me = /\nconst ERRATT = (\{[^\n]*\});/.exec(src), mi = /\nconst INTERR = (\{[^\n]*\});/.exec(src);
  if (!mm || !me || !mi) throw new Error("nell'app manca MACATT, ERRATT o INTERR");
  const nuovo = f => { env = ambiente(); env.MACATT = new Function('return ' + mm[1])(); env.ERRATT = new Function('return ' + me[1])();
    env.INTERR = new Function('return ' + mi[1])(); if (f) f(env);
    F = carica(src, ['guardiaGiro', 'serveIlMac', 'aspettaMac', 'macTornato', 'mixerMuto', 'piccoMixer', 'posVoluta', 'riprendiDopoInterruzione'], env); };
  const giri = (n, passo) => { for (let k = 0; k < n; k++) { env.ora += passo || 2000; env.perf += passo || 2000; F.guardiaGiro(); } };

  /* ---------- il tempo va avanti: niente ---------- */
  nuovo();
  for (let k = 0; k < 10; k++) { env.au.currentTime += 2; env.ora += 2000; F.guardiaGiro(); }
  eq([env.carichi.length, env.avanti.length, env.suonati], [0, 0, 0], 'suona davvero: niente da fare');
  fatte.push('suona');

  /* ---------- «suona» ma fermo da 8 s (rete caduta a metà): dallo stesso punto ---------- */
  nuovo(); giri(5);
  eq(env.carichi, [['A', 3, true, 42]], 'fermo a metà: lo stesso brano, dallo stesso punto');
  fatte.push('fermo a metà');

  /* ---------- il brano nuovo non è partito nel passaggio (nessuno l'ha fermato): a passi ---------- */
  nuovo(e => { e.au.paused = true; e.au.currentTime = 0; });
  giri(4);
  eq([env.suonati, env.carichi.length], [1, 0], 'primo passo: play()');
  giri(3);
  eq(env.carichi, [['A', 3, true, 0]], 'secondo passo: ricaricato');
  giri(3);
  eq([env.diretti, env.carichi.length], [['lettore fermo: in pausa'], 1], 'terzo passo: il lettore diretto');
  giri(3);
  eq(env.avanti, [false], 'quarto passo: il brano dopo');
  giri(3);
  eq([env.pausaVoluta, env.riavvii, env.diari.indexOf('niente è servito: riprovo da capo') >= 0], [0, [], true], 'quinto: mai fermi, mai l\'app ricaricata: da capo');
  giri(30);
  if (env.carichi.length < 3 || env.avanti.length < 2) throw new Error('dopo deve continuare a provare: ' + JSON.stringify([env.carichi, env.avanti]));
  eq([env.pausaVoluta, env.riavvii.length], [0, 0], 'per un minuto: sempre tentativi, mai una pausa');
  // lettore diretto non disponibile (mai sbloccato): il terzo passo è l'altro lettore, già sbloccato
  nuovo(e => { e.au.paused = true; e.au.currentTime = 0; e.passaAlDiretto = () => false; });
  giri(10);
  eq(env.carichi[1], ['B', 3, true, 0], 'senza lettore diretto: sull\'altro lettore, già sbloccato');
  fatte.push('passaggio non riuscito');

  /* ---------- brano finito e il successivo mai partito: avanti ---------- */
  nuovo(e => { e.au.paused = true; e.au.ended = true; });
  giri(4);
  eq(env.avanti, [true], 'finito: il successivo');
  fatte.push('finito');

  /* ---------- in pausa voluta, telecomando, chiamata, radio: niente ---------- */
  for (const [k, f] of [['pausa', e => { e.au.paused = true; e.pausaVoluta = e.ora; }], ['telecomando', e => { e.RC.id = 'x'; }],
    ['chiamata', e => { e.CH.aperta = true; }], ['radio', e => { e.au = e.AUX; }]]) {
    nuovo(f); giri(12);
    eq(env.carichi.length + env.avanti.length + env.suonati, 0, k + ': niente');
  }
  fatte.push('pausa, telecomando, chiamata, radio');

  /* ---------- passaggio del mix: aspetta la dissolvenza, se resta a metà lo chiude e riparte ---------- */
  nuovo(e => { e.MIX.act = {t0:0, W:12}; e.au.paused = true; e.au.currentTime = 0; });
  giri(10);
  eq([env.chiusi, env.suonati], [[], 0], 'durante la dissolvenza: niente');
  giri(5);
  eq(env.chiusi, ['mix'], 'rimasto a metà: chiuso');
  giri(4);
  eq(env.suonati, 1, 'e la musica riparte');
  fatte.push('mix a metà');

  /* ---------- il Mac si riavvia (aggiornamento) al cambio brano: si aspetta, niente tentativi; torna: riparte da lì ---------- */
  const macSparito = e => { e.SRV = false; e.sulMac = new Set(['a', 'b', 'c', 'b1', 'd']); e.au.paused = true; e.au.currentTime = 42; };
  nuovo(macSparito);
  giri(25);   // 50 s senza Mac
  eq([env.carichi.length, env.avanti.length, env.pausaVoluta], [0, 0, 0], 'Mac sparito: niente tentativi, niente brani saltati, nessuna resa');
  eq(env.avvisi, ['Il Mac non risponde: la musica riprende da qui appena torna'], 'un avviso solo');
  if (env.richieste < 10) throw new Error('il Mac si richiede ogni 4 s: solo ' + env.richieste + ' volte in 50 s');
  env.macTorna = true; giri(3);
  eq(env.carichi, [['A', 3, true, 42]], 'il Mac torna: lo stesso brano, dallo stesso punto');
  eq(env.diari.filter(d => /Mac/.test(d)), ['aspetto il Mac', 'Mac tornato: riparto'], 'nel diario');
  fatte.push('Mac che si riavvia');

  /* ---------- il brano finito e il successivo è solo sul Mac: quando torna, il successivo ---------- */
  nuovo(e => { macSparito(e); e.au.ended = true; });
  giri(10);
  eq(env.avanti, [], 'finito, Mac sparito: si aspetta');
  env.macTorna = true; giri(2);
  eq(env.avanti, [false], 'il Mac torna: il brano dopo');
  fatte.push('Mac sparito a fine brano');

  /* ---------- Mac assente più di 30 s e in coda un brano già sul telefono: intanto quello ---------- */
  nuovo(e => { macSparito(e); e.sulTelefono = new Set(['d']); });
  giri(14);
  eq(env.carichi, [], 'prima dei 30 s si aspetta il Mac');
  giri(4);
  eq(env.carichi, [['A', 4, true, undefined]], 'poi il brano già sul telefono');
  fatte.push('intanto un brano sul telefono');

  /* ---------- 18/11: senza Mac per ore non ci si ferma mai: lo si richiama più piano, e quando torna si riparte ---------- */
  nuovo(macSparito);
  giri(305);
  eq([env.pausaVoluta, env.avvisi[env.avvisi.length - 1]], [0, 'Il Mac non risponde da 10 minuti: la musica riprende da sola appena torna'], '10 minuti: nessuna pausa');
  eq(env.carichi.length + env.avanti.length, 0, 'senza saltare brani');
  let prima = env.richieste; giri(150);   // altri 5 minuti
  if (env.richieste - prima > 25 || env.richieste - prima < 15) throw new Error('dopo 10 minuti il Mac si richiede ogni 15 s: ' + (env.richieste - prima) + ' volte in 5 minuti');
  giri(1500);   // un'ora
  eq(env.pausaVoluta, 0, 'un\'ora dopo: ancora nessuna pausa');
  env.macTorna = true; giri(3);
  eq(env.carichi, [['A', 3, true, 42]], 'il Mac torna dopo un\'ora: lo stesso brano, dallo stesso punto');
  fatte.push('ore senza Mac');

  /* ---------- 18/11: il Mac non risponde da pochi secondi (SRVKO, ancora «collegato»): si aspetta, niente tentativi ---------- */
  nuovo(e => { e.SRVKO = e.ora; e.sulMac = new Set(['b1']); e.au.paused = true; });
  giri(10);
  eq([env.carichi.length, env.avanti.length, env.suonati, env.pausaVoluta], [0, 0, 0, 0], 'Mac che tarda: si aspetta senza contare');
  env.SRVKO = 0; env.macTorna = true; giri(2);
  eq(env.carichi, [['A', 3, true, 42]], 'risponde: lo stesso brano, dallo stesso punto');
  nuovo(e => { e.SRVKO = e.ora; e.sulMac = new Set(['b1']); e.sulTelefono = new Set(['b1']); e.au.paused = true; });
  giri(4);
  eq(env.suonati, 1, 'brano già sul telefono: il Mac non serve, si riparte subito');
  fatte.push('Mac che tarda');

  /* ---------- 18/11: brani che non arrivano (tre errori di fila): mai fermi, si riprova da soli ogni 30 s ---------- */
  nuovo(e => { e.au.paused = true; e.au.error = {code:4}; e.ERRATT.fino = e.ora + 30000; });
  giri(14);
  eq([env.carichi.length, env.suonati, env.pausaVoluta], [0, 0, 0], 'si aspettano i 30 s');
  giri(2);
  eq(env.carichi, [['A', 3, true, 42]], 'poi lo stesso brano, dallo stesso punto');
  fatte.push('errori: si riprova');

  /* ---------- il brano va avanti ma il mixer dell'iPhone è fermo: musica muta. Lo si fa ripartire, poi riavvio ---------- */
  const mixer = (stato, va) => e => {
    let c = 50, gira = va;
    e.MIX.gains = new Map([[e.DECKS[0], {}]]);
    e.MIX.ctx = {state:stato, resumes:0, get currentTime() { return gira ? c + e.ora / 1000 : c; },
      resume() { this.resumes++; if (e.riparte) { this.state = 'running'; gira = true; } return {catch() {}}; }};
  };
  const avanti = n => { for (let k = 0; k < n; k++) { env.au.currentTime += 2; env.ora += 2000; F.guardiaGiro(); } };
  nuovo(mixer('running', true)); avanti(10);
  eq([env.riavvii, env.MIX.ctx.resumes], [[], 0], 'mixer che va: niente');
  // 16/11: il brano va avanti ma il mixer è fermo: prima lo si fa ripartire, al giro dopo il lettore diretto (niente riavvii)
  nuovo(mixer('suspended', false)); avanti(2);
  eq([env.MIX.ctx.resumes, env.diretti.length], [1, 0], 'mixer sospeso: prima lo si fa ripartire');
  avanti(1);
  eq([env.diretti, env.riavvii], [['mixer suspended'], []], 'ancora fermo: il lettore diretto, senza ricaricare l\'app');
  nuovo(mixer('running', false)); avanti(4);   // il primo giro conosce il brano, il secondo l'orologio, poi due giri fermi
  eq(env.diretti.length, 1, 'mixer «running» con l\'orologio fermo: muto anche lui');
  nuovo(e => { mixer('interrupted', false)(e); e.riparte = true; }); avanti(6);
  eq([env.MIX.ctx.resumes, env.diretti.length, env.diari.indexOf('mixer ripartito') >= 0], [1, 0, true], 'ripartito: resta il mixer');
  nuovo(e => { mixer('interrupted', false)(e); e.MIX.gains = new Map(); }); avanti(6);
  eq(env.diretti.length + env.riavvii.length, 0, 'lettore fuori dal mixer (radio, Mac): non conta');
  // il mixer va (running, orologio che avanza) ma dal misuratore non esce suono: a 6 s i guadagni, a 10 s il lettore diretto
  const zitto = picco => e => { mixer('running', true)(e); e.au.duration = 300; e.MIX.metro = {fftSize:8, getFloatTimeDomainData(b) { b.fill(0); b[3] = picco(); }}; };
  let liv = 0.2;
  nuovo(zitto(() => liv)); avanti(2);   // prima la musica si sente: il misuratore funziona (e lo si ricorda)
  eq([env.MIX.metroOk, env.memoria['am-metro-ok']], [true, true], 'il misuratore ha sentito la musica: ci si fida');
  liv = 0; avanti(4);
  eq(env.diretti.length, 0, 'silenzio da poco: si aspetta');
  avanti(1);
  eq(env.diretti, ['dal mixer non esce suono'], '10 s senza suono a metà brano: il lettore diretto');
  if (env.diari.indexOf('muto: dal mixer non esce suono') < 0) throw new Error('nel diario il silenzio');
  // 15/11: un misuratore che non ha mai sentito niente (su un iPhone potrebbe dare sempre zero): non ci si fida, niente passaggi
  nuovo(zitto(() => 0)); avanti(30);
  eq([env.diretti.length, env.risvegli, !!env.MIX.metroOk], [0, 0, false], 'misuratore mai sentito suonare: nessun passaggio');
  nuovo(e => { zitto(() => 0)(e); e.MIX.metroOk = true; }); avanti(6);   // ricordato da un'altra volta su questo dispositivo
  eq(env.diretti, ['dal mixer non esce suono'], 'fiducia ricordata: il silenzio conta da subito');
  // brano fermo con i dati arrivati: «mixer rotto» solo se il misuratore (fidato) è muto; se no, i passi di sempre
  nuovo(e => { zitto(() => 0)(e); }); giri(6);
  eq(env.diretti.length, 0, 'brano fermo, misuratore mai sentito: niente lettore diretto');
  nuovo(zitto(() => 0.2)); avanti(20);
  eq(env.diretti.length, 0, 'musica che si sente: niente');
  nuovo(e => { zitto(() => 0)(e); e.au.currentTime = 2; }); avanti(4);   // primi secondi: silenzio vero possibile
  eq(env.diretti.length, 0, 'nei primi 8 s non conta');
  fatte.push('mixer muto');

  /* ---------- ricaricato due volte di fila: torna sempre al punto voluto, non all'inizio ---------- */
  nuovo(); giri(5);
  eq(env.carichi, [['A', 3, true, 42]], 'prima volta: dal punto dove era');
  env.au.currentTime = 0; env.seekH = () => {}; env.seekOnLoad.at = 42;   // ricaricato, non ancora pronto: il lettore dice 0
  giri(6);   // il salto da 42 a 0 conta come movimento, poi di nuovo 8 s fermo
  eq(env.carichi[1], ['A', 3, true, 42], 'seconda volta: ancora da 42, non da 0');
  fatte.push('punto voluto');

  /* ---------- 14/11: l'iPhone interrompe l'audio (la dettatura mentre si scrive, Siri, una chiamata): si aspetta, poi da lì ---------- */
  nuovo(e => { e.au.paused = true; e.INTERR.da = e.ora; e.interrotta = true; });
  giri(60);   // 2 minuti di interruzione
  eq([env.suonati, env.carichi.length, env.avanti.length, env.riavvii.length, env.pausaVoluta], [0, 0, 0, 0, 0], 'durante l\'interruzione: niente tentativi, niente brani saltati, nessuna resa');
  env.interrotta = false; giri(2);
  eq([env.suonati, env.carichi.length], [1, 0], 'finita l\'interruzione: play() dallo stesso punto, senza ricaricare');
  fatte.push('interruzione dell\'iPhone');

  nuovo(e => { e.au.paused = true; e.INTERR.da = e.ora; });   // interruzione senza segnali: si riprova ogni 3 s, senza mai arrendersi
  giri(30);
  eq([env.carichi.length + env.avanti.length + env.riavvii.length, env.pausaVoluta, env.suonati >= 15], [0, 0, true], 'play() ripetuto, mai altro');
  nuovo(e => { e.au.paused = true; e.INTERR.da = e.ora; e.interrotta = true; });
  giri(905);   // oltre 30 minuti
  eq([env.pausaVoluta, env.suonati], [0, 0], 'oltre 30 minuti di interruzione: nessuna pausa, si aspetta ancora');
  env.interrotta = false; giri(8);
  if (env.suonati < 1) throw new Error('finita l\'interruzione lunga deve ripartire');
  fatte.push('interruzione lunga');

  /* ---------- in secondo piano mai il riavvio dell'app (fermerebbe la musica): si ricomincia dai passi gentili ---------- */
  nuovo(e => { e.au.paused = true; e.au.currentTime = 0; e.document.hidden = true; });
  giri(30);
  eq([env.riavvii.length, env.pausaVoluta], [0, 0], 'in secondo piano: nessun riavvio, nessuna resa');
  if (env.carichi.length + env.diretti.length + env.avanti.length < 4) throw new Error('in secondo piano deve continuare a riprovare: ' + JSON.stringify([env.carichi, env.diretti, env.avanti]));
  nuovo(e => { mixer('interrupted', false)(e); e.document.hidden = true; }); avanti(10);
  eq([env.riavvii.length, env.diretti.length >= 1], [0, true], 'mixer fermo a schermo bloccato: il lettore diretto, mai il riavvio');
  fatte.push('mai il riavvio in secondo piano');

  /* ---------- 15/11: passaAlDiretto vero: il lettore diretto deve andare avanti da solo, se no si torna al mixer ---------- */
  // il lettore diretto finto: «parte» (e.dDa = quando) se load lo fa suonare; il suo tempo va avanti con l'orologio
  const diretto = (prepara) => {
    const e = ambiente(), timer = [];
    e.setTimeout = (f, ms) => { timer.push([e.ora + ms, f]); };
    e.xfAbort = () => {}; e.MIX.ctx = {state:'interrupted'};
    e.dDa = null; e.dInizio = 42; e.dParte = true;
    const D0 = e.DIRETTO; D0._sbloccato = true; D0.paused = true; D0.readyState = 0;
    Object.defineProperty(D0, 'currentTime', {get: () => e.dInizio + (e.dDa == null ? 0 : (e.ora - e.dDa) / 1000), set() {}});
    e.load = (i, play, at) => { e.carichi.push([e.au === D0 ? 'D' : e.au.nome, i, play, at, !!e.MIX.fuori]); if (e.MIX.fuori && e.dParte) { D0.paused = false; D0.readyState = 4; e.dDa = e.ora; } };
    if (prepara) prepara(e);
    const G = carica(src, ['passaAlDiretto', 'verificaDiretto', 'posVoluta'], e);
    const passa = ms => { const fine = e.ora + ms; while (e.ora < fine) { e.ora += 250; for (let k = 0; k < timer.length; k++) if (timer[k] && timer[k][0] <= e.ora) { const f = timer[k][1]; timer[k] = null; f(); } } };
    return {e, G, passa};
  };
  const nonSuona = 'il lettore diretto non suona: torno al mixer';
  let D = diretto();
  eq(D.G.passaAlDiretto('prova'), true, 'passa al lettore diretto');
  eq([D.e.au === D.e.DIRETTO, D.e.MIX.fuori, D.e.carichi[0]], [true, true, ['D', 3, true, 42, true]], 'lo stesso brano, dallo stesso punto, sul lettore diretto');
  D.passa(7000);
  eq([D.e.carichi.length, D.e.MIX.fuori, D.e.diari.indexOf('lettore diretto: suona') >= 0], [1, true, true], 'il lettore diretto suona: ci si resta');
  D = diretto(e => { e.dParte = false; });   // iOS non lo lascia partire: resta fermo
  D.G.passaAlDiretto('prova'); D.passa(7000);
  eq([D.e.MIX.fuori, D.e.carichi[1], D.e.diari.indexOf(nonSuona) >= 0], [false, ['D', 3, true, 42, false], true], 'non suona: di nuovo sul mixer, dallo stesso punto');
  D.e.au = D.e.DECKS[0];   // (load ha cambiato lettore)
  eq(D.G.passaAlDiretto('ancora'), false, 'per 5 minuti non si riprova');
  D.passa(5 * 60000);
  eq(D.G.passaAlDiretto('dopo 5 minuti'), true, 'dopo 5 minuti sì');
  D = diretto(e => { e.load = (i, play, at) => { e.carichi.push(['D', i, play, at, !!e.MIX.fuori]); if (e.MIX.fuori) e.DIRETTO.paused = false; }; });
  D.G.passaAlDiretto('prova'); D.passa(7000);   // non in pausa ma i dati stanno ancora arrivando dalla rete: si riguarda
  eq([D.e.carichi.length, D.e.diari.indexOf(nonSuona)], [1, -1], 'ancora in arrivo: si aspetta');
  D.e.DIRETTO.readyState = 4; D.e.dDa = D.e.ora; D.passa(7000);
  eq([D.e.carichi.length, D.e.MIX.fuori], [1, true], 'arrivato e suona: ci si resta');
  D = diretto(e => { e.load = (i, play, at) => { e.carichi.push(['D', i, play, at, !!e.MIX.fuori]); if (e.MIX.fuori) { e.DIRETTO.paused = false; e.DIRETTO.readyState = 4; } }; });
  D.G.passaAlDiretto('prova'); D.passa(7000);   // «suona» con i dati, ma il tempo non va avanti
  eq([D.e.MIX.fuori, D.e.carichi.length], [false, 2], 'non va avanti: di nuovo sul mixer');
  D = diretto(e => { e.load = (i, play, at) => { e.carichi.push(['D', i, play, at, !!e.MIX.fuori]); if (e.MIX.fuori) { e.DIRETTO.paused = false; e.DIRETTO.readyState = 4; e.dInizio = 0; e.dDa = e.ora; } }; });
  D.G.passaAlDiretto('prova'); D.passa(7000);   // il salto al punto non è riuscito e suona dall'inizio: suona comunque
  eq([D.e.MIX.fuori, D.e.carichi.length], [true, 1], 'suona dall\'inizio: ci si resta (il suono c\'è)');
  D = diretto(); D.G.passaAlDiretto('prova'); D.e.pausaVoluta = D.e.ora + 1; D.e.DIRETTO.paused = true; D.e.dDa = null; D.passa(7000);
  eq(D.e.carichi.length, 1, 'messo in pausa intanto: non si tocca niente');
  D = diretto(e => { e.DIRETTO._sbloccato = false; });
  eq([D.G.passaAlDiretto('prova'), D.e.carichi.length], [false, 0], 'lettore diretto mai sbloccato da un tocco: si resta sul mixer');
  fatte.push('lettore diretto controllato');

  /* ---------- 18/11: il Mac non sparisce per una risposta mancata: perso solo dopo 30 s di silenzio, richiamato ogni 5 s ---------- */
  const collegamento = (risponde) => {
    const e = ambiente(), timer = [];
    e.SRV = true; e.SRVKO = 0; e.srvKoT = 0; e.render = () => {}; e.chiamate = 0;
    e.setTimeout = (f, ms) => { timer.push([e.ora + ms, f]); return timer.length; }; e.clearTimeout = () => {};
    e.loadServer = () => { e.chiamate++; if (risponde(e)) G.macRisponde(); else G.macTace(); };
    const SRVT = /\nconst SRV_TOLLERA = (\d+);/.exec(src); if (!SRVT) throw new Error("nell'app manca SRV_TOLLERA");
    e.SRV_TOLLERA = +SRVT[1];
    const G = carica(src, ['macTace', 'macRisponde'], e);
    const passa = ms => { const fine = e.ora + ms; while (e.ora < fine) { e.ora += 500; for (let k = 0; k < timer.length; k++) if (timer[k] && timer[k][0] <= e.ora) { const f = timer[k][1]; timer[k] = null; f(); } } };
    return {e, G, passa};
  };
  let K = collegamento(() => false);
  K.G.macTace();
  eq([K.e.SRV, K.e.SRVKO > 0], [true, true], 'una risposta mancata: il Mac resta collegato');
  K.passa(20000);
  eq(K.e.SRV, true, 'dopo 20 s: ancora collegato');
  if (K.e.chiamate < 3) throw new Error('il Mac si richiama ogni 5 s: ' + K.e.chiamate);
  K.passa(15000);
  eq([K.e.SRV, K.e.diari.indexOf('Mac perso') >= 0], [false, true], 'dopo 30 s di silenzio: perso');
  K = collegamento(e => e.chiamate >= 2);   // al secondo richiamo risponde
  K.G.macTace(); K.passa(12000);
  eq([K.e.SRV, K.e.SRVKO, K.e.diari.indexOf('il Mac risponde di nuovo') >= 0], [true, 0, true], 'risponde al richiamo: mai perso');
  fatte.push('il Mac non sparisce per una risposta');

  /* ---------- 18/11: schermo bloccato: la musica passa subito al lettore diretto (fuori dal mixer), senza buchi ---------- */
  const blocco = (accetta) => {
    const e = ambiente();
    e.NEEDS_MIXER = true; e.SILENZIO = 'data:silenzio'; e.suonaVoluta = 99000; e.pausaVoluta = 0;
    e.MIX.ctx = {state:'running'}; e.MIX.gains = new Map([[e.DECKS[0], {}], [e.DECKS[1], {}]]);
    e.castUnLettore = () => false; e.saltoAnnulla = () => {}; e.xfAbort = () => {}; e.setGain = () => {}; e.verificaDiretto = () => {};
    e.updateNow = () => {}; e.chiusi = [];
    const A = e.DECKS[0]; A.paused = false; A.currentTime = 42; A.getAttribute = () => 'blob:brano'; A.removeAttribute = () => { A.tolto = true; };
    const D = e.DIRETTO; D._sbloccato = true; D.paused = true; D.ascolta = {};
    D.addEventListener = (n, f) => { D.ascolta[n] = f; }; D.removeEventListener = (n) => { delete D.ascolta[n]; };
    D.removeAttribute = () => { D.src = ''; };
    let esito = null;
    D.play = () => { D.paused = !accetta; return {then(si, no) { esito = () => (accetta ? si() : no({name:'NotAllowedError'})); }}; };
    const G = carica(src, ['alSicuro', 'tornaAlMixer'], e);
    return {e, G, A, D, finisci: () => esito && esito()};
  };
  let B = blocco(true);
  eq(B.G.alSicuro('prova'), true, 'schermo bloccato: si passa al lettore diretto');
  eq([B.e.au === B.D, B.D.src, B.D.currentTime, B.e.MIX.fuori, B.A.paused], [true, 'blob:brano', 42, true, false], 'stesso file, stesso punto; il mixer suona finché il diretto non parte');
  B.finisci();
  eq([B.A.paused, B.A.tolto, B.e.diari.indexOf('schermo bloccato: suona il lettore diretto') >= 0], [true, true, true], 'il diretto suona: il lettore del mixer si ferma');
  B.e.MIX.ctx.state = 'suspended'; B.G.tornaAlMixer();
  eq(B.e.MIX.fuori, true, 'di nuovo nell\'app con il mixer fermo: si resta sul diretto');
  B.e.MIX.ctx.state = 'running'; B.G.tornaAlMixer();
  eq([B.e.MIX.fuori, B.e.MIX.perBlocco], [false, false], 'mixer di nuovo in funzione: la dissolvenza torna dal brano dopo');
  B = blocco(false);
  B.G.alSicuro('prova'); B.finisci();
  eq([B.e.au === B.A, B.A.paused, B.e.MIX.fuori, !!B.e.MIX.bloccoNo], [true, false, false, true], 'iOS rifiuta il diretto: la musica resta sul mixer, come prima');
  eq(B.G.alSicuro('ancora'), false, 'appena rifiutato: non si insiste');
  for (const [k, f] of [['in pausa', b => { b.e.pausaVoluta = b.e.ora; }], ['diretto mai sbloccato', b => { b.D._sbloccato = false; }],
    ['telecomando', b => { b.e.RC.id = 'x'; }], ['radio', b => { b.e.au = b.e.AUX; }], ['non iPhone', b => { b.e.NEEDS_MIXER = false; }]]) {
    B = blocco(true); f(B);
    eq([B.G.alSicuro('prova'), B.e.au === B.D], [false, false], k + ': niente');
  }
  fatte.push('schermo bloccato');

  /* ---------- brano cambiato: il conto riparte ---------- */
  nuovo(); giri(3); env.curId = 'd'; giri(3);
  eq(env.carichi.length, 0, 'brano nuovo: il conto riparte');
  fatte.push('brano nuovo');
}
