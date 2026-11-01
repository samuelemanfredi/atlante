// Prove delle funzioni «come in Apple Music» (musica/index.html) con JavaScriptCore, che c'è su ogni Mac:
//
//     osascript -l JavaScript test_ascolto.js ../index.html
//
// Le funzioni si prendono dall'app così come sono (come in test_mix.js) e si provano con un ambiente finto: i brani
// simili della riproduzione automatica e delle stazioni, i generi, Replay e il volume uniforme.
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

// l'ambiente dell'app: una piccola libreria
function ambiente() {
  const brani = [
    {id:'a1', title:'Uno', artist:'Alfa', genre:'Rock', year:'1999', sid:'s1'},
    {id:'a2', title:'Due', artist:'Alfa', genre:'Rock', year:'2001', sid:'s2'},
    {id:'a3', title:'Tre', artist:'Alfa', genre:'rock', year:'2002', sid:'s3'},
    {id:'b1', title:'Quattro', artist:'Beta', genre:'Rock/Indie', year:'2000', sid:'s4', fav:true},
    {id:'c1', title:'Cinque', artist:'Gamma', genre:'Jazz', year:'1960', sid:'s5', meno:true},
    {id:'c2', title:'Sei', artist:'Gamma', genre:'Jazz', year:'1961', sid:'s6'},
    {id:'d1', title:'Sette', artist:'Delta', genre:'', year:'', sid:'s7', plays:3, duration:200},
  ].map(t => Object.assign({remote:true, plays:0, duration:180}, t));
  const T = new Map(brani.map(t => [t.id, t]));
  const ritmi = {s1:[120, .8, .7, 1, -8], s2:[121, .8, .7, 1, -20], s4:[60, .5, .2, 1, null]};
  return {
    T, P:[{id:'p', ids:['a1', 'c2']}], RITMI:{ritmi}, VOLN:{on:true, rif:-14}, MIX:{norm:new Map()},
    low: s => String(s || '').toLowerCase().trim(), primary: t => t.albumArtist || t.artist || '?', canPlay: () => true,
    shuffleArr: a => a.slice(), gemelli: () => new Map(), albArt: t => t.albumArtist || t.artist || '',
    coll: {compare: (a, b) => String(a).localeCompare(String(b))},
    bpmDi: t => { const r = ritmi[t.sid]; return r ? {bpm:r[0], reg:r[1], en:r[2], modo:r[3]} : null; },
    Math: Object.assign(Object.create(Math), {random: () => 0}),   // senza caso: la prova è ripetibile
  };
}

function prove(src, fatte) {
  const eq = (a, b, m) => { const x = JSON.stringify(a), y = JSON.stringify(b); if (x !== y) throw new Error(m + ': ' + x + ' invece di ' + y); };
  const env = ambiente();
  const F = carica(src, ['genereDi', 'similiA', 'volNorm', 'generi', 'replayDati'], env);

  /* ---------- brani simili: stesso artista e genere prima, mai «suggerisci meno» né quelli già in coda ---------- */
  let s = F.similiA(['a1'], 4, new Set(['a1']));
  eq(s.slice(0, 2), ['a2', 'a3'], 'stesso artista e genere per primi');
  eq(s.indexOf('c1'), -1, '«suggerisci meno» non torna mai');
  eq(s.includes('b1'), true, 'stesso genere (Rock/Indie → rock) e anni vicini');
  eq(s.includes('c2'), true, 'nella stessa playlist del brano');
  fatte.push('simili');
  s = F.similiA(['a1'], 10, new Set(['a1', 'a2', 'a3', 'b1']));
  eq(s.includes('a2') || s.includes('b1'), false, 'quelli già in coda (o sentiti da poco) no');
  eq(s.includes('d1'), true, 'senza niente di simile: comunque altri brani, la musica non si ferma');
  fatte.push('niente di simile');
  eq(F.similiA(['a1'], 2, new Set(['a1'])).filter(x => x[0] === 'a').length <= 2, true, 'al più 2 dello stesso artista');
  fatte.push('varietà');

  /* ---------- generi: dal tag, maiuscole e «Rock/Indie» insieme a «Rock» ---------- */
  eq(F.generi().map(g => [g.nome, g.tracks.length]), [['Rock', 4], ['Jazz', 2]], 'generi');
  fatte.push('generi');

  /* ---------- Replay: ascolti, minuti, artisti ---------- */
  env.T.get('a1').plays = 2;
  const R = F.replayDati();
  eq([R.ascolti, R.minuti, R.nArtisti, R.brani.map(t => t.id)], [5, 16, 2, ['d1', 'a1']], 'Replay');
  fatte.push('replay');

  /* ---------- volume uniforme: i brani forti si abbassano, i deboli restano, senza misura niente ---------- */
  eq([F.volNorm('a1').toFixed(2), F.volNorm('a2'), F.volNorm('b1'), F.volNorm('c2')], ['0.50', 1, 1, 1], 'volume uniforme');
  env.VOLN.on = false;
  eq(F.volNorm('a1'), 1, 'spento: niente');
  fatte.push('volume uniforme');
}
