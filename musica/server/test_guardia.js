// Prove del guardiano della riproduzione (musica/index.html) con JavaScriptCore, che c'è su ogni Mac:
//
//     osascript -l JavaScript test_guardia.js ../index.html
//
// La funzione si prende dall'app così com'è e si prova con un lettore finto: quando dovrebbe suonare ma il tempo non va
// avanti, riparte dallo stesso punto; al terzo blocco dello stesso brano passa al successivo; in pausa, durante un
// passaggio del mix o con il tempo che avanza non fa niente. Scrive «OK …» o «PROVA FALLITA: …».
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
  };
  const lettore = nome => ({nome, paused:false, ended:false, currentTime:42, readyState:4, networkState:1, error:null, _sbloccato:true,
    getAttribute: () => 'blob:x', removeAttribute() {}, load() {}, pause() { this.paused = true; },
    play() { env.suonati++; return {catch() {}}; }});
  env.DECKS = [lettore('A'), lettore('B')]; env.au = env.DECKS[0];
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
  const nuovo = f => { env = ambiente(); if (f) f(env); F = carica(src, ['guardiaGiro'], env); };
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
  eq(env.carichi[1], ['B', 3, true, 0], 'terzo passo: sull\'altro lettore, già sbloccato');
  env.au = env.DECKS[0]; env.au.paused = true;
  giri(3);
  eq(env.avanti, [false], 'quarto passo: il brano dopo');
  giri(3);
  eq([env.au.paused, env.pausaVoluta > 0, env.avvisi[env.avvisi.length - 1]], [true, true, 'La musica non riparte da sola: tocca ▶︎'], 'quinto: si aspetta ▶︎');
  giri(6);
  eq(env.avanti.length, 1, 'dopo, fermo davvero: niente altro');
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

  /* ---------- brano cambiato: il conto riparte ---------- */
  nuovo(); giri(3); env.curId = 'd'; giri(3);
  eq(env.carichi.length, 0, 'brano nuovo: il conto riparte');
  fatte.push('brano nuovo');
}
