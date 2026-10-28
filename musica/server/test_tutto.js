// Prove di «Scarica tutta la musica» sul Mac mini stesso (musica/index.html) con JavaScriptCore, che c'è su ogni Mac:
//
//     osascript -l JavaScript test_tutto.js ../index.html
//
// Le funzioni si prendono dall'app così come sono (come in test_mix.js) e si provano con un ambiente finto: quando il
// server dice che l'app è aperta sul Mac stesso (api/extra/io → stesso_mac), «Scarica tutta la musica» non parte da solo
// (il 27/10 ha riempito il disco interno del Mac mini), ma resta acceso se lo si era acceso a mano.
// Scrive «OK …» o «PROVA FALLITA: …» (test_extra.py lo controlla).
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

// l'ambiente dell'app: localStorage con «am-tutto» com'è (null = mai scelto a mano), la risposta di api/extra/io
function ambiente(scelto, io) {
  const salvati = {};
  const env = {
    TUTTO: scelto === null ? true : scelto, IO: {ruolo:'', persona:null}, XAPI: 'api/extra/',
    DLQ: ['a', 'm'], AUTO: new Set(['a']), saved: new Set(), TOLTI: new Set(), dlCur: null, grabbing: new Set(),
    P: [], T: new Map([['a', {id:'a', remote:true}], ['b', {id:'b', remote:true}]]), DB: {}, SRV: 'api/', SRVSCAN: false, SRVN: 2,
    coda: [], salvati,
    localStorage: {getItem: k => (k === 'am-tutto' && scelto !== null ? String(scelto) : null)},
    ls: {get: (k, d) => (k in salvati ? salvati[k] : d), set: (k, v) => { salvati[k] = v; }},
    fetchT: () => ({ok: true, json: () => io}),
    render: () => {}, copieScaricati: () => {}, dlPill: () => {}, toast: () => {},
    gemelli: () => new Map(), coll: {compare: (a, b) => String(a || '').localeCompare(String(b || ''))},
    queueDl: ids => { env.coda.push(ids); },
  };
  return env;
}

function prove(src, fatte) {
  const eq = (a, b, m) => { const x = JSON.stringify(a), y = JSON.stringify(b); if (x !== y) throw new Error(m + ': ' + x + ' invece di ' + y); };
  const NOMI = ['ioCarica', 'tuttoScelto', 'tuttoSpento', 'scaricaTutto'];

  /* ---------- sul Mac stesso, mai scelto a mano: spento, e via dalla coda i brani messi lì da «Scarica tutto» ---------- */
  let env = ambiente(null, {ruolo:'proprietario', stesso_mac:true});
  let F = carica(src, NOMI, env);
  F.ioCarica();
  eq(env.TUTTO, false, 'sul Mac stesso «Scarica tutta la musica» si spegne');
  eq(env.salvati['am-tutto'], false, 'e la scelta si ricorda');
  eq(env.DLQ, ['m'], 'resta in coda solo il brano scaricato a mano');
  fatte.push('mac stesso');

  /* ---------- sul Mac stesso, acceso a mano: resta acceso ---------- */
  env = ambiente(true, {ruolo:'proprietario', stesso_mac:true});
  F = carica(src, NOMI, env);
  F.ioCarica();
  eq(env.TUTTO, true, 'acceso a mano resta acceso');
  fatte.push('acceso a mano');

  /* ---------- sull'iPhone di Samuele, mai scelto: acceso come prima ---------- */
  env = ambiente(null, {ruolo:'proprietario', stesso_mac:false});
  F = carica(src, NOMI, env);
  F.ioCarica();
  eq(env.TUTTO, true, "sull'iPhone resta acceso di serie");
  fatte.push('iPhone');

  /* ---------- prima che il Mac risponda: con «stesso Mac» già saputo (dall'ultima volta) non si mette in coda niente ---------- */
  env = ambiente(null, {});
  env.IO = {ruolo:'proprietario', persona:null, stessoMac:true};
  F = carica(src, NOMI, env);
  F.scaricaTutto();
  eq(env.coda, [], 'sul Mac stesso «Scarica tutto» non mette brani in coda');
  env = ambiente(null, {});
  env.IO = {ruolo:'proprietario', persona:null};
  F = carica(src, NOMI, env);
  F.scaricaTutto();
  eq(env.coda.length, 1, "sull'iPhone sì");
  fatte.push('coda');
}
