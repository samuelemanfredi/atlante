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
    ora: 0, carichi: [], avanti: 0, avvisi: [], diari: [],
    au: {paused:false, ended:false, currentTime:42, readyState:4, networkState:1, error:null},
    AUX: {}, curId: 'b1', RC: {id:null}, MIX: {act:null, salto:null, ctx:null}, CH: {aperta:false},
    S: {qi:3, queue:['a', 'b', 'c', 'b1', 'd']}, navigator: {onLine:true}, document: {hidden:false},
    GUARDIA: {t:-1, da:0, id:null, volte:0, FERMO:8000, ARRIVA:15000},
  };
  env.Date = {now: () => env.ora};
  env.load = (i, play, at) => { env.carichi.push([i, play, at]); };
  env.next = () => { env.avanti++; };
  env.toast = m => { env.avvisi.push(m); };
  env.diario = (e, x) => { env.diari.push(e); };
  return env;
}

function prove(src, fatte) {
  const eq = (a, b, m) => { const x = JSON.stringify(a), y = JSON.stringify(b); if (x !== y) throw new Error(m + ': ' + x + ' invece di ' + y); };
  let env = ambiente(), F = carica(src, ['guardiaGiro'], env);
  const giri = (n, passo) => { for (let k = 0; k < n; k++) { env.ora += passo || 2000; F.guardiaGiro(); } };

  /* ---------- il tempo va avanti: niente ---------- */
  for (let k = 0; k < 10; k++) { env.au.currentTime += 2; env.ora += 2000; F.guardiaGiro(); }
  eq([env.carichi.length, env.avanti], [0, 0], 'suona davvero: niente da fare');
  fatte.push('suona');

  /* ---------- fermo da 8 s: riparte dallo stesso punto ---------- */
  const t = env.au.currentTime;
  giri(5);
  eq(env.carichi, [[3, true, t]], 'fermo: lo stesso brano, dallo stesso punto');
  eq(env.diari, ['fermo a metà'], 'nella scatola nera');
  fatte.push('riparte');

  /* ---------- tre blocchi dello stesso brano: il successivo ---------- */
  giri(5); giri(5);
  eq([env.carichi.length, env.avanti], [2, 1], 'al terzo blocco si passa al successivo');
  fatte.push('successivo');

  /* ---------- brano che arriva dalla rete: si aspetta di più ---------- */
  env = ambiente(); F = carica(src, ['guardiaGiro'], env);
  env.au.readyState = 2; giri(5);
  eq(env.carichi.length, 0, 'sta arrivando: 10 s non bastano');
  giri(4);
  eq(env.carichi.length, 1, 'oltre 15 s sì');
  fatte.push('rete lenta');

  /* ---------- in pausa, nel mix, telecomando: niente ---------- */
  for (const [k, v] of [['pausa', e => { e.au.paused = true; }], ['mix', e => { e.MIX.act = {}; }], ['telecomando', e => { e.RC.id = 'x'; }]]) {
    env = ambiente(); F = carica(src, ['guardiaGiro'], env); v(env); giri(12);
    eq(env.carichi.length + env.avanti, 0, k + ': niente');
  }
  fatte.push('pausa, mix, telecomando');

  /* ---------- brano cambiato: si ricomincia a contare ---------- */
  env = ambiente(); F = carica(src, ['guardiaGiro'], env);
  giri(3); env.curId = 'd'; giri(3);
  eq(env.carichi.length, 0, 'brano nuovo: il conto riparte');
  fatte.push('brano nuovo');
}
