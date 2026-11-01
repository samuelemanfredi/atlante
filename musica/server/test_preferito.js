// Prove della ★ dalla schermata di blocco (musica/index.html) con JavaScriptCore, che c'è su ogni Mac:
//
//     osascript -l JavaScript test_preferito.js ../index.html
//
// Il comando rapido «Preferito Atlante» chiede al Mac (POST api/extra/preferito: lo prova test_extra.py) e il Mac manda
// all'app il comando «preferito» {id, on}. Qui, con un ambiente finto e le funzioni dell'app così come sono:
// - devEsegui passa «preferito» a devPreferito, che accende o spegne la stella del brano giusto e sincronizza i Preferiti;
// - un comando uguale a quello che c'è già non risincronizza; senza id vale il brano che suona; un brano sconosciuto
//   (e niente in riproduzione) non rompe niente;
// - nel menu del brano che suona c'è «★ dalla schermata di blocco…», con la sua scheda (il comando rapido, i passi per la
//   schermata di blocco, l'indirizzo da copiare per farlo a mano: quello di questo Mac, con il tipo del dispositivo).
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
function carica(src, env, nomi) {
  const codice = nomi.map(n => estrai(src, n)).join('\n');
  const f = new Function('env', 'with (env) { return (function () {\n' + codice + '\nreturn {' + nomi.join(',') + '};\n})(); }');
  return f(env);
}

function vero(c, testo) { if (!c) throw new Error(testo); }
function uguale(a, b, testo) { if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error(testo + ': ' + JSON.stringify(a) + ' invece di ' + JSON.stringify(b)); }

/* ---------- le prove ---------- */
function prove(src, fatte) {
  const env = {
    T: new Map(), RC: {id: ''}, EXT: new Set(), curId: '', DEV: {id: 'iphone-0001', tipo: 'iphone', bloccato: false},
    au: {paused: false, currentTime: 0}, ALLAU: [], userVol: 1, vol: {style: {setProperty() {}}},
    ls: {set() {}, get(k, d) { return d; }},
    sinc: 0, segnati: [], stelle: 0, avvisi: [], schede: [], inviati: 0,
    dbPut() { return Promise.resolve(); },
    favCambiati() { env.sinc++; }, favMark(id) { env.segnati.push(id); }, favTasti() { env.stelle++; },
    toast(m) { env.avvisi.push(m); }, devInvia() { env.inviati++; }, devAvvia() {}, toggle() {}, avanti() {}, prev() {},
    applyVol() {}, setTimeout() {}, openSheet(h) { env.schede.push(h); }, ic(n) { return '<i ' + n + '>'; }, cancel: '<annulla>',
    XAPI: 'api/extra/', location: {href: 'https://mac-mini.prova.ts.net:8448/'},
    URL: function (p, base) { this.href = base.replace(/[^/]*$/, '') + p; },
  };
  const f = carica(src, env, ['devEsegui', 'devPreferito', 'favBrano', 'bloccoUrl', 'bloccoScheda']);
  const t1 = {id: 'rabc', title: 'Levitating', fav: false, remote: true}, t2 = {id: 'rdef', title: 'Altro', fav: true, remote: true};
  env.T.set(t1.id, t1); env.T.set(t2.id, t2);

  // dal Mac: accendi la stella di «rabc» (passa da devEsegui, come i comandi del telecomando)
  f.devEsegui({azione: 'preferito', id: 'rabc', on: true});
  vero(t1.fav === true, 'la stella non si è accesa');
  uguale([env.sinc, env.segnati, env.avvisi], [1, ['rabc'], ['Aggiunto ai Preferiti']], 'dopo «preferito» on');
  vero(env.stelle >= 1 && env.inviati >= 1, 'stella a schermo e stato al Mac non aggiornati');
  fatte.push('comando dal Mac');

  // di nuovo uguale: niente nuova sincronizzazione, l'avviso sì
  f.devPreferito({id: 'rabc', on: true});
  uguale([t1.fav, env.sinc, env.avvisi.length], [true, 1, 2], 'comando uguale a quello che c\'è');
  fatte.push('niente doppioni');

  // spegnere
  f.devPreferito({id: 'rdef', on: false});
  uguale([t2.fav, env.sinc, env.segnati[env.segnati.length - 1], env.avvisi[env.avvisi.length - 1]],
         [false, 2, 'rdef', 'Tolto dai Preferiti'], 'dopo «preferito» off');
  fatte.push('togli');

  // senza id: il brano che suona; senza «on»: cambia (come la stella di Apple Music)
  env.curId = 'rdef';
  f.devPreferito({});
  uguale([t2.fav, env.sinc], [true, 3], 'senza id né on');
  fatte.push('brano che suona');

  // brano sconosciuto e niente in riproduzione: nessun errore, niente cambia
  env.curId = '';
  f.devPreferito({id: 'rnessuno', on: true});
  uguale([env.sinc, t1.fav, t2.fav], [3, true, true], 'brano sconosciuto');
  fatte.push('brano sconosciuto');

  // l'indirizzo per il comando rapido fatto a mano: questo Mac, con il tipo del dispositivo
  uguale(f.bloccoUrl(), 'https://mac-mini.prova.ts.net:8448/api/extra/preferito?formato=testo&tipo=iphone', 'indirizzo');
  env.DEV.tipo = 'ipad';
  vero(f.bloccoUrl().endsWith('tipo=ipad'), 'il tipo del dispositivo non è nell\'indirizzo');
  fatte.push('indirizzo');

  // la scheda: il comando rapido, i passi della schermata di blocco, l'indirizzo da copiare
  f.bloccoScheda();
  const h = env.schede[env.schede.length - 1] || '';
  for (const x of ['data-a="blocco-cr"', 'data-a="blocco-copia"', 'Personalizza', 'Comandi Rapidi', '«Preferito Atlante»', 'tasto Azione', '<annulla>'])
    vero(h.includes(x), 'nella scheda manca ' + x);
  fatte.push('scheda');

  // nel menu del brano che suona e tra i tasti dell'app
  vero(/data-a="m-blocco"[^>]*><span class="l">★ dalla schermata di blocco…/.test(src), 'nel menu manca «★ dalla schermata di blocco…»');
  for (const x of ["case 'm-blocco': bloccoScheda();", "case 'blocco-cr': bloccoComando();", "case 'blocco-copia': bloccoCopia();",
                   "case 'preferito': devPreferito(c);"])
    vero(src.includes(x), 'manca il tasto: ' + x);
  vero(/api\/extra\/preferito\/comando-rapido|'preferito\/comando-rapido'/.test(src), 'il comando rapido non si scarica dal Mac');
  fatte.push('menu e tasti');
}
