// Prove di «Per ritmo» (i gruppi pronti per BPM) e dei «Mood» dell'app (musica/index.html) con JavaScriptCore, che c'è
// su ogni Mac:
//
//     osascript -l JavaScript test_ritmo.js ../index.html
//
// Le funzioni si prendono dall'app così come sono e si provano con dati finti: niente browser, pochi istanti.
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
// una dichiarazione che può andare su più righe: da «inizio» fino al primo «;» a fine riga
function dichiarazione(src, inizio) {
  const i = src.indexOf('\n' + inizio);
  if (i < 0) throw new Error("nell'app manca «" + inizio + '…»');
  const f = src.indexOf(';\n', i + 1);
  return src.slice(i + 1, f + 1);
}
function carica(src, nomi, dichiarazioni, env) {
  const codice = dichiarazioni.map(r => dichiarazione(src, r)).join('\n') + '\n' + nomi.map(n => estrai(src, n)).join('\n');
  const f = new Function('env', 'with (env) { return (function () {\n' + codice + '\nreturn {' + nomi.join(',') +
    ', RIT_PRONTI, ritEtichetta, ritIntervallo, ritPronto, UMORE_ASPETTO};\n})(); }');
  return f(env);
}
function uguale(a, b, cosa) {
  if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error(cosa + ': ' + JSON.stringify(a) + ' invece di ' + JSON.stringify(b));
}
function vero(x, cosa) { if (!x) throw new Error(cosa); }

function prove(src, fatte) {
  // Math con un caso ripetibile (LCG): le scelte «sempre diverse» si provano senza prove che falliscono a caso
  let seme = 12345;
  const MathFinto = Object.create(Math);
  MathFinto.random = () => (seme = (seme * 1103515245 + 12345) % 2147483648) / 2147483648;
  const memoria = {}, suonate = [], avvisi = [];
  const env = {
    Math: MathFinto, RSC: {regolare:false, mezzo:false}, RITMI: null, AMATI: [], T: new Map(), saved: new Set(), SRV: true,
    canPlay: id => env.SRV || env.saved.has(id),
    ls: {get: (k, d) => k in memoria ? JSON.parse(memoria[k]) : d, set: (k, v) => { memoria[k] = JSON.stringify(v); }},
    shuffleArr: a => a.slice(), playList: (ids, i, x, nome) => suonate.push({ids, nome}), toast: t => avvisi.push(t),
    esc: s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;'), ic: n => '<svg data-i="' + n + '"></svg>',
    ritMisura: () => '', ritObiettivi: sc => sc.valori.length || sc.intervalli.length ? sc : {valori:[sc.cursore], intervalli:[]}
  };
  const A = carica(src, ['ritFattore', 'ritNome', 'sceltaFresca', 'umPool', 'umVia', 'umScaffale'],
    ['const RIT_PRONTI = ', 'const ritEtichetta = ', 'const ritIntervallo = ', 'const ritPronto = ', 'const UMORE_ASPETTO = ', 'const UMC = '], env);

  // 1. i gruppi pronti coprono tutti i BPM, ognuno in un gruppo solo (anche sui confini: 80 è di «Tranquillo»)
  const gruppi = bpm => A.RIT_PRONTI.filter(p => A.ritFattore(bpm, A.ritPronto(p))).map(p => p.nome);
  for (let d = 0; d <= 2600; d++) {
    const g = gruppi(d / 10);
    if (g.length !== 1) throw new Error((d / 10) + ' BPM sta in ' + g.length + ' gruppi: ' + g.join(', '));
  }
  uguale([30, 79.9, 80, 89.95, 90, 119.9, 120, 139.9, 140, 169.9, 170, 250].map(b => gruppi(b)[0]),
    ['Calmo', 'Calmo', 'Tranquillo', 'Tranquillo', 'Camminata', 'Passo svelto', 'Ritmo costante', 'Energico',
     'Handbike in salita', 'Veloce', 'Scatti', 'Scatti'], 'gruppo di ogni BPM');
  uguale(A.RIT_PRONTI.length, 9, 'quanti gruppi');
  fatte.push('ogni BPM in un gruppo solo');

  // 2. come si scrivono: «<80», «80–90», «170+»; nel riepilogo «fino a 80» e «170 e oltre»
  uguale(A.RIT_PRONTI.map(A.ritEtichetta), ['<80', '80–90', '90–110', '110–120', '120–130', '130–140', '140–160', '160–170', '170+'], 'etichette');
  uguale(A.ritNome(A.ritPronto(A.RIT_PRONTI[0])), 'fino a 80 BPM', 'nome del primo');
  uguale(A.ritNome(A.ritPronto(A.RIT_PRONTI[8])), '170 e oltre BPM', 'nome dell\'ultimo');
  // i ritmi salvati da Samuele restano com'erano: intervallo con tutti e due i capi
  uguale(A.ritFattore(110, {valori:[], intervalli:[[100, 110]], tol:3}), 1, 'intervallo salvato, capo compreso');
  uguale(A.ritFattore(128, {valori:[126], intervalli:[], tol:3}), 1, 'BPM esatto con tolleranza');
  uguale(A.ritNome({valori:[], intervalli:[[100, 110]], tol:3}), '100–110 BPM', 'nome di un intervallo salvato');
  fatte.push('etichette e ritmi salvati');

  // 3. Mood: per ogni mood i brani con il suo bit, solo quelli che si possono ascoltare ora
  env.RITMI = {ritmi: {a:[120, .8, .9, 1, -9, 5], b:[70, .2, .2, 1, -12, 2], c:[null, null, null, 0, null, 1], d:[100, .5, .5, 0, -10, 0]},
    umori: [{id:'umore-focus', nome:'Focus'}, {id:'umore-rilassamento', nome:'Rilassamento'}, {id:'umore-attivazione', nome:'Attivazione'}]};
  ['a', 'b', 'c', 'd'].forEach(s => env.T.set('r' + s, {id:'r' + s, sid:s}));
  env.AMATI = ['a'];
  let pool = A.umPool();
  uguale(pool.map(p => p.map(x => x[0]).sort()), [['ra', 'rc'], ['rb'], ['ra']], 'brani di ogni mood');
  uguale(pool[0].find(x => x[0] === 'ra')[1], 2, 'i più amati pesano il doppio');
  env.SRV = false; env.saved = new Set(['rb']);   // Mac spento: solo quelli salvati sul telefono
  pool = A.umPool();
  uguale(pool.map(p => p.map(x => x[0])), [[], ['rb'], []], 'senza il Mac');
  env.SRV = true;
  fatte.push('brani di ogni mood, anche senza rete');

  // 4. un tocco: una scelta nuova ogni volta (i brani dell'ultima volta tornano poco), con il nome del mood
  const tanti = {umori: env.RITMI.umori, ritmi: {}};
  for (let i = 0; i < 100; i++) { tanti.ritmi['f' + i] = [100, .7, .4, 1, -10, 1]; env.T.set('rf' + i, {id:'rf' + i, sid:'f' + i}); }
  env.RITMI = tanti;
  A.umVia(0); A.umVia(0);
  uguale(suonate.length, 2, 'riproduzioni partite');
  uguale(suonate[0].nome, 'Mood · Focus', 'nome della riproduzione');
  uguale(suonate[0].ids.length, 40, 'quanti brani');
  vero(suonate[0].ids.every(id => /^rf\d+$/.test(id)), 'solo brani del mood');
  const prima = new Set(suonate[0].ids), ripetuti = suonate[1].ids.filter(id => prima.has(id)).length;
  vero(ripetuti < 15, 'la seconda volta troppi brani uguali: ' + ripetuti);
  vero(/^Focus: 40 brani/.test(avvisi[avvisi.length - 1]), 'avviso: ' + avvisi[avvisi.length - 1]);
  A.umVia(2);   // Attivazione: nessun brano tra questi
  uguale(suonate.length, 2, 'mood vuoto: non parte niente');
  vero(/Per ora non ci sono brani per «Attivazione»/.test(avvisi[avvisi.length - 1]), 'avviso del mood vuoto: ' + avvisi[avvisi.length - 1]);
  fatte.push('scelta nuova a ogni tocco');

  // 5. lo scaffale nella Home: un riquadro per mood con quanti brani, niente se il Mac non dà ancora i mood
  const h = A.umScaffale();
  vero(h.indexOf('<h2 class="h2">Mood</h2>') >= 0, 'titolo dello scaffale');
  uguale((h.match(/data-a="um-via"/g) || []).length, 3, 'riquadri');
  vero(h.indexOf('100 brani') >= 0 && h.indexOf('0 brani') >= 0, 'conteggi: ' + h);
  vero(h.indexOf('data-i="umFocus"') >= 0, 'icona del Focus');
  env.RITMI = {ritmi: {}};
  uguale(A.umScaffale(), '', 'senza mood dal Mac');
  fatte.push('scaffale Mood');
}
