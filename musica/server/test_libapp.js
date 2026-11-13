// Prove di «Dalle app del dispositivo» (musica/index.html) con JavaScriptCore, che c'è su ogni Mac:
//
//     osascript -l JavaScript test_libapp.js ../index.html
//
// Le funzioni si prendono dall'app così come sono (come in test_tutto.js) e si provano con un ambiente finto: i CSV
// esportati dalle altre app (Spotify con Exportify, TuneMyMusic, Excel con il punto e virgola), gli elenchi incollati,
// l'abbinamento con i brani di Atlante (trovati nella playlist, gli altri da cercare) e l'elenco dei brani da cercare.
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
    if (ch === '/' && /[(,=:[!&|?{};]\s*$/.test(src.slice(Math.max(0, k - 20), k))) {   // un'espressione regolare
      k++; let cl = false;
      while (k < src.length && (src[k] !== '/' || cl)) { if (src[k] === '\\') k++; else if (src[k] === '[') cl = true; else if (src[k] === ']') cl = false; k++; }
      continue;
    }
    if (ch === '{') d++;
    else if (ch === '}' && !--d) break;
  }
  return src.slice(i, k + 1);
}
// le costanti per confrontare i titoli (fold, low, nrm, STOPA, nrmA): stanno tutte insieme, subito prima di matcher()
function costantiTitoli(src) {
  const i = src.indexOf('\nconst fold = '), j = src.indexOf('\nfunction matcher(');
  if (i < 0 || j < i) throw new Error("nell'app non trovo le costanti dei titoli (fold … nrmA)");
  return src.slice(i + 1, j);
}
// JavaScriptCore qui non fa girare le promesse da solo: le funzioni «async» dell'app diventano sincrone (via «await»)
function sincrona(f) { return f.replace(/^async function/, 'function').replace(/\bawait /g, ''); }
function carica(src, nomi, env, prima) {
  const codice = (prima || '') + '\n' + nomi.map(n => sincrona(estrai(src, n))).join('\n');
  const f = new Function('env', 'with (env) { return (function () {\n' + codice + '\nreturn {' + nomi.join(',') + '};\n})(); }');
  return f(env);
}

function prove(src, fatte) {
  const eq = (a, b, m) => { const x = JSON.stringify(a), y = JSON.stringify(b); if (x !== y) throw new Error(m + ': ' + x + ' invece di ' + y); };
  const PARSER = ['csvRighe', 'parseCSV', 'parseTXT', 'parseElenco'];
  const F = carica(src, PARSER, {});

  /* ---------- Spotify (Exportify): virgolette, virgole nei titoli, più artisti ---------- */
  const exportify = '﻿"Track URI","Track Name","Artist URI(s)","Artist Name(s)","Album Name"\r\n' +
    '"spotify:track:1","Bohemian Rhapsody - Remastered 2011","spotify:artist:1","Queen","A Night at the Opera"\r\n' +
    '"spotify:track:2","Under Pressure","spotify:artist:1,spotify:artist:2","Queen, David Bowie","Hot Space"\r\n' +
    '"spotify:track:3","Hello, ""Goodbye""","spotify:artist:3","The Beatles","Magical Mystery Tour"\r\n';
  let r = F.parseCSV(exportify, 'Brani che ti piacciono');
  eq(r.length, 1, 'Exportify: una playlist');
  eq(r[0].name, 'Brani che ti piacciono', 'col nome del file');
  eq(r[0].items.map(x => [x.title, x.artist, x.album]), [['Bohemian Rhapsody - Remastered 2011', 'Queen', 'A Night at the Opera'],
    ['Under Pressure', 'Queen, David Bowie', 'Hot Space'], ['Hello, "Goodbye"', 'The Beatles', 'Magical Mystery Tour']], 'Exportify: le righe');
  eq(r[0].items[1].alt, [{title:'Under Pressure', artist:'Queen'}], 'più artisti: anche solo il primo');
  fatte.push('Spotify');

  /* ---------- TuneMyMusic: più playlist in un file; Excel italiano con il punto e virgola ---------- */
  r = F.parseCSV('Track name,Artist name,Album,Playlist name,Type\nImagine,John Lennon,Imagine,Relax,Playlist\nOne,U2,Achtung Baby,Rock,Playlist\nYellow,Coldplay,Parachutes,Relax,Playlist\n', 'export');
  eq(r.map(p => [p.name, p.items.map(x => x.title)]), [['Relax', ['Imagine', 'Yellow']], ['Rock', ['One']]], 'TuneMyMusic: per playlist');
  r = F.parseCSV('Titolo;Artista;Album\nAlbachiara;Vasco Rossi;Non siamo mica gli americani!\n;;\n', 'excel');
  eq(r[0].items, [{title:'Albachiara', artist:'Vasco Rossi', album:'Non siamo mica gli americani!'}], 'punto e virgola');
  eq(F.parseCSV('a,b\n1,2\n', 'x'), [], 'senza una colonna del titolo: niente');
  fatte.push('altre app');

  /* ---------- elenchi incollati: numeri, trattini, tutte e due le forme, link saltati ---------- */
  r = F.parseElenco('1. Bohemian Rhapsody – Queen\n- Vasco Rossi - Albachiara\nhttps://open.spotify.com/track/abc\n\n• Imagine\n', 'Incollati');
  const it = r[0].items;
  eq(it.length, 3, 'tre brani (il link no)');
  eq([it[0].title, it[0].artist], ['Bohemian Rhapsody', 'Queen'], 'Titolo – Artista');
  eq(it[0].alt[0], {title:'Queen', artist:'Bohemian Rhapsody'}, 'e al contrario');
  eq(it[1].alt[0], {title:'Albachiara', artist:'Vasco Rossi'}, 'Artista - Titolo tra le alternative');
  eq([it[2].title, it[2].artist], ['Imagine', ''], 'solo il titolo');
  eq(F.parseElenco('Nome\tArtista\tAlbum\nOne\tU2\tAchtung Baby\n', 'Musica')[0].items, [{title:'One', artist:'U2'}], 'il TXT di Musica');
  eq(F.parseElenco('Track Name,Artist Name(s)\nOne,U2\n', 'csv')[0].items[0].title, 'One', 'un CSV incollato');
  eq(F.parseElenco('\n  \nhttps://x.it/a\n', 'vuoto'), [], 'niente da cercare');
  fatte.push('elenchi');

  /* ---------- abbinamento e playlist: trovati in Atlante, gli altri da cercare; rifatta, non doppia ---------- */
  const T = new Map([['b1', {id:'b1', title:'Bohemian Rhapsody', artist:'Queen'}], ['b2', {id:'b2', title:'Albachiara', artist:'Vasco Rossi'}],
    ['b3', {id:'b3', title:'Under Pressure', artist:'Queen & David Bowie'}], ['b4', {id:'b4', title:'Without Me', artist:'Eminem'}]]);
  let salvate = 0, n = 0;
  const env = {T, GONE:new Map(), P:[], coll:{compare:(a, b) => String(a).localeCompare(String(b))},
    uid:() => 'pl' + (++n), savePl:() => { salvate++; }};
  const G = carica(src, ['matcher', 'matchItem', 'importaListe'], env, costantiTitoli(src));
  const lib = [{title:'Bohemian Rhapsody - Remastered 2011', artist:'Queen', album:''}, {title:'Vasco Rossi', artist:'Albachiara', alt:[{title:'Albachiara', artist:'Vasco Rossi'}]},
    {title:'Under Pressure', artist:'Queen, David Bowie'}, {title:'Canzone che non c\'è', artist:'Nessuno'},
    {title:'Without Me (Explicit)', artist:'Eminem'}];
  let st = G.importaListe([{name:'Libreria di Musica · iPhone', items:lib, appleId:'libapp:dev1'}]);
  eq([st.made, st.found, st.miss], [1, 4, 1], 'quattro trovati («(Explicit)» non conta), uno da cercare');
  const p = env.P[0];
  eq(p.ids, ['b1', 'b2', 'b3', 'b4'], 'nella playlist nell\'ordine della libreria');
  eq(p.miss.map(m => [m.title, m.artist, m.after]), [['Canzone che non c\'è', 'Nessuno', 'b3']], 'da cercare, dopo il brano che lo precede');
  st = G.importaListe([{name:'Libreria di Musica · iPhone', items:lib.slice(0, 1), appleId:'libapp:dev1'}]);
  eq([env.P.length, env.P[0].ids, env.P[0].miss], [1, ['b1'], []], 'letta di nuovo: la stessa playlist, aggiornata');
  eq(salvate, 2, 'salvata ogni volta');
  fatte.push('abbinamento');

  /* ---------- l'elenco dei brani da cercare: la lente apre Cerca e scarica; filtro e al più 300 righe ---------- */
  const miss = [];
  for (let i = 0; i < 320; i++) miss.push({title:'Brano ' + i, artist:i === 7 ? 'Artista Speciale' : 'X'});
  let visibili = true;
  const E = {esc:s => String(s).replace(/[&<>"']/g, c => '&#' + c.charCodeAt(0) + ';'), ic:() => '<svg></svg>',
    xmodsVisibili:() => visibili ? [{g:'Per te', m:[{k:'cerca'}]}] : [{g:'Per te', m:[{k:'idee'}]}]};
  const H = carica(src, ['missRighe'], E, costantiTitoli(src));
  let h = H.missRighe(miss, '');
  eq((h.match(/data-a="misscerca"/g) || []).length, 300, 'al più 300 righe, ognuna con la lente');
  if (h.indexOf('…e altri 20') < 0) throw new Error('manca «…e altri 20»');
  h = H.missRighe(miss, 'speciale');
  eq((h.match(/data-q="([^"]*)"/g) || []), ['data-q="Brano 7 Artista Speciale"'], 'il filtro trova titolo e artista');
  if (H.missRighe(miss, 'zzz').indexOf('Nessun brano') < 0) throw new Error('filtro senza risultati');
  visibili = false;
  if (H.missRighe(miss.slice(0, 2), '').indexOf('misscerca') >= 0) throw new Error('senza Cerca e scarica non c\'è la lente');
  fatte.push('da cercare');
}
