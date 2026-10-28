// Prove delle playlist del Mac nell'app (musica/index.html) con JavaScriptCore, che c'è su ogni Mac:
//
//     osascript -l JavaScript test_playlist_mac.js ../index.html
//
// Le funzioni si prendono dall'app così come sono (come in test_tutto.js) e si provano con un ambiente finto: l'elenco
// arriva da api/extra/playlist e resta sul telefono, le righe dicono «Dal disco» o «Dalla radio», la pagina è di sola
// lettura e «Copia nelle mie playlist» ne fa una propria con gli stessi brani della libreria.
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

/* ---------- estrazione delle funzioni dall'app (come in test_tutto.js) ---------- */
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
// JavaScriptCore qui non fa girare le promesse da solo: le funzioni «async» diventano sincrone (via «await»)
function sincrona(f) { return f.replace(/^async function/, 'function').replace(/\bawait /g, ''); }
function carica(src, nomi, env) {
  const codice = nomi.map(n => sincrona(estrai(src, n))).join('\n');
  const f = new Function('env', 'with (env) { return (function () {\n' + codice + '\nreturn {' + nomi.join(',') + '};\n})(); }');
  return f(env);
}

const ELENCO = [
  {id:'AM-ABC123', nome:'POWER', fonte:'disco', brani:['a', 'b', 'x'], fuori:2},
  {id:'RA-01e7', nome:'Allenamento <1>', fonte:'radio', brani:['b'], fuori:0},
  {id:'AM-VUOTA', nome:'Altrove', fonte:'disco', brani:['x'], fuori:0},   // nessun brano nella libreria di questo telefono
];

function ambiente(risposta) {
  const salvati = {}, env = {
    MPL: [], mplT: 0, SRV: true, XAPI: 'api/extra/', P: [], salvati, disegni: 0, avvisi: [], liste: [], righe: [], salvate: [], rotta: {v:'playlists'},
    T: new Map([['ra', {id:'ra', remote:true, sid:'a', title:'Uno', duration:200}], ['rb', {id:'rb', remote:true, sid:'b', title:'Due', duration:100}]]),
    ls: {get: (k, d) => (k in salvati ? salvati[k] : d), set: (k, v) => { salvati[k] = JSON.parse(JSON.stringify(v)); }},
    fetchT: (url) => { env.chiesto = url; if (risposta instanceof Error) throw risposta; return risposta; },
    curRoute: () => env.rotta, render: () => { env.disegni++; }, toast: m => { env.avvisi.push(m); },
    esc: s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c])),
    ic: n => '<i ' + n + '>', mosaic: tr => '<m' + tr.length + '>', topBar: x => '<top>' + (x || ''),
    regList: (ids, label) => { env.liste.push({ids, label}); return env.liste.length - 1; },
    pbtns: l => '<pbtns ' + l + '>', trackRows: (tr, opt) => { env.righe.push({ids:tr.map(t => t.id), opt}); return '<righe>'; },
    uid: () => 'nuova', savePl: p => { env.salvate.push(p); },
  };
  return env;
}

function prove(src, fatte) {
  const eq = (a, b, m) => { const x = JSON.stringify(a), y = JSON.stringify(b); if (x !== y) throw new Error(m + ': ' + x + ' invece di ' + y); };
  const vero = (c, m) => { if (!c) throw new Error(m); };
  const NOMI = ['mplDi', 'mplNome', 'mplTracce', 'mplFonte', 'mplN', 'mplCarica', 'mplRighe', 'mplPagina', 'mplCopia'];

  /* ---------- l'elenco arriva dal Mac e resta sul telefono ---------- */
  let env = ambiente({ok:true, status:200, json: () => ({playlist:ELENCO})});
  let F = carica(src, NOMI, env);
  F.mplCarica();
  eq(env.chiesto, 'api/extra/playlist', 'chiede le playlist al Mac');
  eq(env.MPL, ELENCO, 'le playlist ricevute');
  eq(env.salvati['am-mpl'], ELENCO, 'restano sul telefono per quando non c\'è rete');
  eq(env.disegni, 1, 'la pagina delle playlist si ridisegna');
  F.mplCarica();
  eq(env.disegni, 1, 'stesso elenco: niente da ridisegnare');
  fatte.push('elenco dal Mac');

  for (const [r, perche] of [[new Error('rete'), 'senza rete'], [{ok:false, status:502, json: () => ({})}, 'il Mac risponde male'],
                             [{ok:true, status:200, json: () => ({errore:'x'})}, 'risposta strana']]) {
    env.fetchT = () => { if (r instanceof Error) throw r; return r; };
    F.mplCarica();
    eq(env.MPL, ELENCO, perche + ': resta l\'ultimo elenco');
  }
  env.fetchT = () => ({ok:false, status:403, json: () => ({errore:'Non disponibile per gli invitati'})});
  F.mplCarica();
  eq([env.MPL, env.salvati['am-mpl']], [[], []], 'invitato: le playlist del Mac non sono sue');
  fatte.push('senza rete e invitati');

  /* ---------- Libreria › Playlist: dopo le proprie, con «Dal disco» o «Dalla radio» ---------- */
  env = ambiente({ok:true, status:200, json: () => ({playlist:ELENCO})}); env.MPL = ELENCO; F = carica(src, NOMI, env);
  const h = F.mplRighe();
  eq(env.chiesto, 'api/extra/playlist', 'aperta la pagina, l\'elenco si rinfresca');
  env.chiesto = null; F.mplRighe();
  eq(env.chiesto, null, 'ma non a ogni disegno (al più ogni 2 minuti)');
  vero(h.indexOf('Dal Mac') >= 0, 'il titolo della sezione');
  vero(h.indexOf('data-v="macpl" data-k="AM-ABC123"') >= 0 && h.indexOf('Dal disco · 2 brani') >= 0, 'la playlist del disco: ' + h);
  vero(h.indexOf('Allenamento &lt;1&gt;') >= 0 && h.indexOf('Dalla radio · 1 brano') >= 0, 'quella della radio, col nome protetto');
  vero(h.indexOf('AM-VUOTA') < 0, 'senza brani della libreria non si mostra');
  env.MPL = []; eq(F.mplRighe(), '', 'senza playlist del Mac niente sezione');
  fatte.push('righe');

  /* ---------- la pagina: sola lettura, brani della libreria ---------- */
  env.MPL = ELENCO;
  const pg = F.mplPagina({v:'macpl', k:'AM-ABC123'});
  eq(env.righe[0], {ids:['ra', 'rb'], opt:{label:'POWER'}}, 'i brani della libreria, senza «Rimuovi dalla playlist»');
  eq(env.liste[0], {ids:['ra', 'rb'], label:'POWER'}, 'si suonano e si scaricano come gli altri');
  vero(pg.indexOf('data-a="mpl-menu"') >= 0 && pg.indexOf('5 min') >= 0, 'menu e durata: ' + pg);
  vero(pg.indexOf('2 brani della playlist non sono nella libreria') >= 0, 'quelli che la libreria non ha');
  vero(pg.indexOf('plmenu') < 0 && pg.indexOf('data-v="pick"') < 0, 'niente modifiche: né Rinomina né Aggiungi brani');
  vero(F.mplPagina({v:'macpl', k:'nessuna'}).indexOf('Playlist non trovata') >= 0, 'una playlist che il Mac non ha più');
  eq([F.mplNome('RA-01e7'), F.mplNome('nessuna')], ['Allenamento <1>', 'Playlist'], 'il nome per il tasto Indietro');
  fatte.push('pagina');

  /* ---------- «Copia nelle mie playlist» ---------- */
  const n = F.mplCopia('AM-ABC123');
  eq(n && [n.id, n.name, n.ids], ['nuova', 'POWER', ['ra', 'rb']], 'la copia ha i brani della libreria');
  eq([env.P.length, env.salvate.length], [1, 1], 'tra le proprie playlist, salvata');
  eq(F.mplCopia('AM-VUOTA'), null, 'senza brani niente copia');
  eq(env.P.length, 1, 'e niente playlist vuota');
  fatte.push('copia');
}
