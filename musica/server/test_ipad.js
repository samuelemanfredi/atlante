// Prove della barra laterale dell'iPad (musica/index.html) con JavaScriptCore, che c'è su ogni Mac:
//
//     osascript -l JavaScript test_ipad.js ../index.html
//
// Le funzioni si prendono dall'app così come sono (come in test_remoto.js) e si provano con un ambiente finto:
// - la barra ha le schede, le voci della Libreria e TUTTI i moduli di Altro di chi usa l'app (Samuele con Gestione e il
//   suo numero, chi è in casa, un invitato con i soli moduli suoi);
// - si ridisegna solo quando cambiano i moduli o il numero della Gestione;
// - si accende una voce sola: il modulo aperto, la voce della Libreria aperta dalla barra o la scheda;
// - Libreria › Album dalla barra chiude il modulo e apre la scheda Libreria su quella pagina;
// - il CSS: la barra solo da 800 px di larghezza e 560 di altezza, senza le schede in basso; l'icona di Altro disegnata.
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

/* ---------- estrazione dall'app (come in test_remoto.js) ---------- */
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
// una dichiarazione scritta su una riga sola, es. «const SIDE_LIB = […];»
function riga(src, inizio) {
  const i = src.indexOf('\n' + inizio);
  if (i < 0) throw new Error("nell'app manca «" + inizio + "…»");
  return src.slice(i + 1, src.indexOf('\n', i + 1));
}
function carica(src, env) {
  const nomi = ['xmodsVisibili', 'sideRender', 'sideMark', 'sideVai'];
  const codice = ['const XREMOTO', 'const XGESTIONE', 'const nuoveGestione', 'const SIDE_LIB', 'let sideChiave'].map(r => riga(src, r))
    .concat(nomi.map(n => estrai(src, n))).join('\n');
  const f = new Function('env', 'with (env) { return (function () {\n' + codice + '\nreturn {' + nomi.join(',') + '};\n})(); }');
  return f(env);
}

// la barra finta: conta i disegni e dai bottoni scritti nell'HTML fa elementi con dataset e classList
function barraFinta() {
  const b = {disegni: 0, html: '', voci: []};
  Object.defineProperty(b, 'innerHTML', {get: () => b.html, set: h => {
    b.disegni++; b.html = h;
    b.voci = (h.match(/<button class="sv"[^>]*>[\s\S]*?<\/button>/g) || []).map(t => {
      const dataset = {}, cls = new Set(), attr = {};
      (t.match(/data-[a-z]+="[^"]*"/g) || []).forEach(a => { const m = /data-([a-z]+)="([^"]*)"/.exec(a); dataset[m[1]] = m[2]; });
      return {dataset, testo: t.replace(/<[^>]+>/g, ''), attr,
        classList: {toggle: (c, on) => { if (on) cls.add(c); else cls.delete(c); }, contains: c => cls.has(c)},
        setAttribute: (n, v) => { attr[n] = v; }, removeAttribute: n => { delete attr[n]; }};
    });
  }});
  b.querySelectorAll = sel => { if (sel !== '.sv') throw new Error('selettore inatteso ' + sel); return b.voci; };
  return b;
}

const XMODS = [
  {g: 'Chat', m: [{k: 'chat', t: 'Chat', d: '', i: '💬', c: '#34c759'}]},
  {g: 'Ascolta', m: [{k: 'cerca', t: 'Cerca e scarica musica', d: '', i: '🔎', c: '#fa2d48'}, {k: 'podcast', t: 'Podcast', d: '', i: '🎙️', c: '#8e44ec'}]},
  {g: 'Per te', m: [{k: 'strumenti', t: 'Strumenti', d: '', i: '🧰', c: '#8e8e93'}]}];
const XINVITATI = [{g: 'Per te', m: [{k: 'chat', t: 'Chat', d: '', i: '💬', c: '#34c759'}, {k: 'inviti', t: 'Invita un amico', d: '', i: '✉️', c: '#007aff'}]}];

// l'ambiente dell'app: chi è (api/extra/io), la scheda e le sue pagine, il modulo aperto
function ambiente(ruolo, nuove) {
  const side = barraFinta(), salvati = {};
  const env = {
    IO: {ruolo, persona: null, nuove: nuove || null}, XMODS, XINVITATI, side, salvati,
    tab: 'home', tabs: {home: [{v: 'home'}], lib: [{v: 'lib'}], search: [{v: 'search'}], altro: [{v: 'altro'}]}, xCur: '',
    scrollY: 0, chiusi: 0, disegni: 0,
    $: id => (id === 'side' ? side : null),
    ic: n => '<svg class="i"><path d="' + n + '"/></svg>',
    esc: s => String(s).replace(/[&<>"']/g, c => '&#' + c.charCodeAt(0) + ';'),
    ls: {get: (k, d) => (k in salvati ? salvati[k] : d), set: (k, v) => { salvati[k] = v; }},
    closeX: () => { env.chiusi++; env.xCur = ''; },
    render: () => { env.disegni++; },
  };
  return env;
}

function prove(src, fatte) {
  const eq = (a, b, m) => { const x = JSON.stringify(a), y = JSON.stringify(b); if (x !== y) throw new Error(m + ': ' + x + ' invece di ' + y); };
  const accese = env => env.side.voci.filter(v => v.classList.contains('on')).map(v => v.dataset.k || v.dataset.v || v.dataset.tab);
  const chiavi = env => env.side.voci.map(v => v.dataset.k || v.dataset.v || v.dataset.tab);

  /* ---------- le voci: schede, Libreria e tutti i moduli di chi usa l'app ---------- */
  let env = ambiente('proprietario', {inviti: 2, idee: 1}), F = carica(src, env);
  F.sideRender();
  eq(chiavi(env), ['home', 'lib', 'search', 'altro', 'playlists', 'artists', 'albums', 'songs', 'favs', 'gestione', 'remoto', 'chat', 'cerca', 'podcast', 'strumenti'],
    'Samuele: schede, Libreria, Gestione e tutti i moduli');
  eq(env.side.voci.find(v => v.dataset.k === 'gestione').testo.indexOf('3') >= 0, true, 'il numero delle cose nuove sulla Gestione');
  eq(env.side.voci.filter(v => v.dataset.k).every(v => v.dataset.a === 'xopen'), true, 'i moduli si aprono come i riquadri di Altro (xopen)');
  eq(env.side.voci.filter(v => v.dataset.v).every(v => v.dataset.a === 'sgo'), true, 'le voci della Libreria: sgo');
  env = ambiente(''); F = carica(src, env); F.sideRender();
  eq(chiavi(env).slice(9), ['chat', 'cerca', 'podcast', 'strumenti'], 'chi è in casa: i moduli, senza Gestione');
  env = ambiente('invitato'); F = carica(src, env); F.sideRender();
  eq(chiavi(env).slice(9), ['chat', 'inviti'], 'un invitato: solo i moduli suoi');
  fatte.push('voci');

  /* ---------- si ridisegna solo quando cambia qualcosa ---------- */
  env = ambiente('proprietario', {inviti: 1}); F = carica(src, env);
  F.sideRender(); F.sideRender(); F.sideRender();
  eq(env.side.disegni, 1, 'tre disegni dell\'app, la barra una volta');
  env.IO.nuove = {inviti: 4}; F.sideRender();
  eq(env.side.disegni, 2, 'il numero della Gestione cambia: si ridisegna');
  env.IO.ruolo = ''; F.sideRender();
  eq([env.side.disegni, chiavi(env).indexOf('gestione')], [3, -1], 'i moduli cambiano (non più Samuele): si ridisegna senza Gestione');
  fatte.push('ridisegno');

  /* ---------- una voce accesa sola ---------- */
  env = ambiente(''); F = carica(src, env); F.sideRender();
  eq(accese(env), ['home'], 'all\'avvio: Home');
  env.tab = 'lib'; env.tabs.lib = [{v: 'lib'}, {v: 'albums'}]; F.sideMark();
  eq(accese(env), ['albums'], 'Libreria › Album: Album, non Libreria');
  env.tabs.lib = [{v: 'lib'}, {v: 'album', k: 'x'}]; F.sideMark();
  eq(accese(env), ['lib'], 'una pagina della Libreria che non è nella barra: Libreria');
  env.tabs.lib = [{v: 'lib'}, {v: 'artists'}, {v: 'artist', k: 'y'}]; F.sideMark();
  eq(accese(env), ['artists'], 'dentro Artisti: resta Artisti');
  env.xCur = 'podcast'; F.sideMark();
  eq(accese(env), ['podcast'], 'un modulo aperto: solo lui');
  eq(env.side.voci.find(v => v.dataset.k === 'podcast').attr['aria-current'], 'page', 'aria-current sul modulo aperto');
  env.xCur = ''; env.tab = 'altro'; F.sideMark();
  eq(accese(env), ['altro'], 'modulo chiuso: Altro');
  eq(env.side.voci.find(v => v.dataset.k === 'podcast').attr['aria-current'], undefined, 'aria-current tolto');
  fatte.push('voce accesa');

  /* ---------- Libreria › Album dalla barra ---------- */
  env = ambiente(''); F = carica(src, env); env.xCur = 'strumenti'; env.scrollY = 120;
  F.sideVai('albums');
  eq([env.chiusi, env.tab, env.tabs.lib, env.salvati['am-tab'], env.disegni], [1, 'lib', [{v: 'lib'}, {v: 'albums'}], 'lib', 1],
    'chiude il modulo, scheda Libreria su Album, ricordata per il prossimo avvio');
  eq(env.tabs.home[0].y, 120, 'la scheda di prima ricorda dov\'era');
  fatte.push('Libreria dalla barra');

  /* ---------- CSS e HTML ---------- */
  const mq = /@media \(min-width:800px\) and \(min-height:560px\)\{([\s\S]*?)\n\}/.exec(src);
  if (!mq) throw new Error('manca il blocco CSS dell\'iPad (min-width 800px, min-height 560px)');
  ['#tabs{display:none}', '#side{display:flex', '#xp{left:var(--sidew)}', '#mini{left:var(--sidew)'].forEach(r => {
    if (mq[1].indexOf(r) < 0) throw new Error('nel blocco CSS dell\'iPad manca ' + r);
  });
  if (src.indexOf('#side{display:none}') < 0) throw new Error('fuori dall\'iPad la barra deve essere nascosta');
  if (src.indexOf('<nav id="side"') < 0) throw new Error('manca <nav id="side">');
  if (src.indexOf('html.ui-ed #side{') < 0 && !/html\.ui-ed #side[,{]/.test(src)) throw new Error('con «Modifica interfaccia» la barra deve stare sotto la sua barra');
  const grid = /\n  grid:'<path d="([^"]*)"/.exec(src);
  if (!grid || grid[1].length < 20) throw new Error('l\'icona di Altro (grid) non è disegnata');
  fatte.push('CSS');
}
