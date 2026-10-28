// Prove dei titoli che scorrono nell'app (musica/index.html) con JavaScriptCore, che c'è su ogni Mac:
//
//     osascript -l JavaScript test_titoli.js ../index.html
//
// Un titolo che non sta nel suo spazio non resta tagliato con «…»: fermo, poi scorre piano fino alla fine e torna.
// Le funzioni si prendono dall'app così come sono (come in test_sicurezza.js) e si provano con elementi finti, di cui
// le larghezze (scrollWidth: il testo intero, clientWidth: lo spazio) le decide la prova:
// - quali scorrono: solo quelli più larghi del loro spazio, e non coperti (scheda, lettore, modulo di Altro);
// - di quanto e in quanto tempo: sempre alla stessa velocità (30–40 px al secondo), corti o lunghi, fino alla fine;
// - tutti insieme: partono nello stesso momento e tornano insieme quando il più lungo è arrivato in fondo;
// - un titolo ridisegnato uguale mentre scorre (la scheda «Riproduci su» ogni 2 s) continua dal punto giusto, senza scatti;
// - prima tutte le misure, poi tutte le scritture (niente ricalcoli della pagina a metà);
// - con «Riduci movimento» (o l'app nascosta) niente: restano fermi con «…».
// Scrive «OK …» o «PROVA FALLITA: …» (test_extra.py lo controlla).
ObjC.import('Foundation');

function run(argv) {
  const fatte = [], errori = [];
  try {
    prove(leggi(argv[0] || '../index.html'), fatte, errori);
  } catch (e) {
    errori.push((e && e.message) || String(e));
  }
  if (errori.length) return 'PROVA FALLITA: ' + errori.join(' ‖ ') + (fatte.length ? ' (riuscite: ' + fatte.join(', ') + ')' : '');
  return 'OK ' + fatte.length + ' prove: ' + fatte.join(', ');
}

function leggi(p) {
  const s = $.NSString.stringWithContentsOfFileEncodingError(p, $.NSUTF8StringEncoding, null);
  if (!s || s.isNil()) throw new Error('non trovo ' + p);
  return s.js;
}

/* ---------- estrazione delle funzioni dall'app (come in test_sicurezza.js) ---------- */
function trova(src, nome) {
  return new RegExp('\\n[ \\t]*\\(?(async )?function ' + nome + '\\(').exec(src);
}
function estrai(src, nome) {
  const m = trova(src, nome);
  if (!m) throw new Error("nell'app manca la funzione " + nome);
  const i = m.index + m[0].indexOf(m[1] ? 'async' : 'function');
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
function riga(src, inizio) {
  const m = new RegExp('\\n[ \\t]*' + inizio.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).exec(src);
  if (!m) throw new Error("nell'app manca la riga «" + inizio + '…»');
  const i = m.index + 1;
  return src.slice(i, src.indexOf('\n', i));
}
// righe (costanti) e funzioni dell'app in un ambiente finto; `valori`: le costanti da restituire insieme alle funzioni
function carica(src, nomi, righe, env, valori) {
  valori = valori || [];
  const codice = righe.map(x => riga(src, x)).join('\n') + '\n' + nomi.map(x => estrai(src, x)).join('\n');
  const f = new Function('env', 'with (env) { return (function () {\n' + codice + '\nreturn {' + nomi.concat(valori).join(',') + '};\n})(); }');
  return f(env);
}

/* ---------- elementi finti ----------
   Un titolo con le sue larghezze; il diario segna in ordine le letture (misure) e le scritture (testo avvolto, variabili,
   attributi): le misure devono venire tutte prima */
function finto(nome, sw, cw, diario, opz) {
  opz = opz || {};
  const vars = {}, attr = {};
  return {
    nome, vars, attr, isConnected:opz.staccato ? false : true, coperto:!!opz.coperto, avvolto:0, textContent:opz.testo || nome, nodeType:1,
    matches:() => true, querySelectorAll:() => [], closest() { return this; },
    get scrollWidth() { diario.push('leggi ' + nome); return sw; },
    get clientWidth() { diario.push('leggi ' + nome); return cw; },
    style:{setProperty(k, v) { diario.push('scrivi ' + nome); vars[k] = v; }},
    setAttribute(k, v) { diario.push('scrivi ' + nome); attr[k] = String(v); },
    removeAttribute(k) { delete attr[k]; },
    hasAttribute(k) { return Object.prototype.hasOwnProperty.call(attr, k); },
  };
}
function classi() {
  const s = new Set();
  return {add:c => s.add(c), remove:c => s.delete(c), contains:c => s.has(c), toggle:(c, v) => (v ? s.add(c) : s.delete(c)), s};
}
// orologio finto: setTimeout non parte da solo, la prova fa scattare il prossimo quando vuole
function orologio() {
  const o = {code:[], n:0};
  o.setTimeout = (f, ms) => { o.code.push({f, ms, id:++o.n}); return o.n; };
  o.clearTimeout = id => { o.code = o.code.filter(x => x.id !== id); };
  o.scatta = () => { const x = o.code.shift(); if (!x) throw new Error('nessun orologio in attesa'); x.f(); return x.ms; };
  return o;
}

function prove(app, fatte, errori) {
  // ogni gruppo di prove va avanti da solo: un errore non nasconde gli altri
  const gruppo = f => { try { f(); } catch (e) { errori.push((e && e.message) || String(e)); } };
  const eq = (a, b, m) => { const x = JSON.stringify(a), y = JSON.stringify(b); if (x !== y) throw new Error(m + ': ' + x + ' invece di ' + y); };
  const vero = (c, m) => { if (!c) throw new Error(m); };
  const RIGHE = ['const TS_SEL =', 'const TS_VEL =', 'const TS ='];
  const VALORI = ['TS', 'TS_SEL'];
  const NOMI = ['tsCalcola', 'tsDurata', 'tsRitorno', 'tsPrepara', 'tsSpento', 'tsSveglia', 'tsParti', 'tsTorna', 'tsFerma', 'tsTogli', 'tsVisti', 'tsSegui', 'tsCambiati', 'tsRiprendi', 'tsLascia', 'tsMisure'];
  // l'app finta attorno: copertura e testo avvolto finti (sono DOM vero), orologio finto, documento visibile
  function ambiente(diario, o, extra) {
    const html = classi();
    return Object.assign({
      setTimeout:o.setTimeout, clearTimeout:o.clearTimeout, html, performance:{now:() => o.ora || 0},
      document:{hidden:false, documentElement:{classList:html, style:{setProperty(k, v) { html[k] = v; }}}},
      tsCoperto:el => el.coperto,
      tsAvvolgi:el => { diario.push('scrivi ' + el.nome); el.avvolto++; },
    }, extra || {});
  }

  /* ---------- quali scorrono: solo quelli che non stanno nel loro spazio ---------- */
  gruppo(() => {
    const F = carica(app, ['tsCalcola'], RIGHE, {}, VALORI);
    eq(F.tsCalcola(200, 200, false), null, 'un titolo che sta giusto nel suo spazio scorre');
    eq(F.tsCalcola(120, 200, false), null, 'un titolo corto scorre');
    eq(F.tsCalcola(0, 0, false), null, 'un titolo nascosto (larghezza 0) scorre');
    eq(F.tsCalcola(80, 0, false), null, 'un titolo senza spazio (larghezza 0) scorre');
    vero(F.tsCalcola(201, 200, false), 'un titolo più largo di 1 px non scorre');
    vero(F.tsCalcola(520, 200, false), 'un titolo lungo non scorre');
    fatte.push('quali scorrono');
  });

  /* ---------- di quanto e in quanto tempo: sempre alla stessa velocità, fino a vedere la fine ---------- */
  gruppo(() => {
    const F = carica(app, ['tsCalcola'], RIGHE, {}, VALORI);
    const vel = [];
    for (const fuori of [1, 7, 40, 115, 300, 900, 2400]) {
      const x = F.tsCalcola(200 + fuori, 200, false);
      vero(x && x.d > 0 && x.t > 0, 'nessun movimento per ' + fuori + ' px fuori: ' + JSON.stringify(x));
      vero(x.d >= fuori, 'con ' + fuori + ' px fuori scorre solo ' + x.d + ' px: la fine del titolo non si vede');
      vero(x.d <= fuori + 24, 'con ' + fuori + ' px fuori scorre ' + x.d + ' px: va troppo oltre la fine');
      vel.push(x.d / x.t);
    }
    for (const v of vel) vero(v >= 30 && v <= 40, 'velocità ' + v.toFixed(1) + ' px/s (deve stare fra 30 e 40)');
    const min = Math.min.apply(null, vel), max = Math.max.apply(null, vel);
    vero(max - min < 0.5, 'la velocità cambia con la lunghezza: da ' + min.toFixed(2) + ' a ' + max.toFixed(2) + ' px/s');
    const corto = F.tsCalcola(240, 200, false), lungo = F.tsCalcola(600, 200, false);
    vero(lungo.t > corto.t * 5, 'un titolo lungo ci mette quanto uno corto (' + lungo.t + ' s contro ' + corto.t + ' s)');
    fatte.push('stessa velocità');
  });

  /* ---------- con «Riduci movimento» niente movimento ---------- */
  gruppo(() => {
    const diario = [], o = orologio();
    const F = carica(app, NOMI, RIGHE, ambiente(diario, o), VALORI);
    eq(F.tsCalcola(900, 200, true), null, 'tsCalcola con «Riduci movimento»');
    const els = [finto('a', 900, 200, diario), finto('b', 300, 200, diario)];
    eq(F.tsPrepara(els, true).length, 0, 'tsPrepara con «Riduci movimento»: qualcosa scorre');
    vero(!diario.some(x => x.startsWith('scrivi')), 'con «Riduci movimento» i titoli si toccano: ' + diario.join(', '));
    // l'orologio: con «Riduci movimento» non parte, e se parte non muove niente
    F.TS.rid = {matches:true};
    els.forEach(el => F.TS.vis.add(el));
    F.tsSveglia();
    eq(o.code.length, 0, 'con «Riduci movimento» l\'orologio parte');
    F.tsParti();
    vero(els.every(el => !el.hasAttribute('data-ts')), 'con «Riduci movimento» un titolo scorre');
    eq(o.code.length, 0, 'con «Riduci movimento» dopo un giro l\'orologio continua');
    eq(F.tsSpento(), true, 'tsSpento con «Riduci movimento»');
    // app nascosta (telefono in tasca): nemmeno
    F.TS.rid = {matches:false};
    const G = carica(app, NOMI, RIGHE, ambiente(diario, o, {document:{hidden:true, documentElement:{classList:classi(), style:{setProperty() {}}}}}), VALORI);
    G.TS.rid = {matches:false}; G.TS.vis.add(els[0]);
    G.tsSveglia();
    eq(o.code.length, 0, 'con l\'app nascosta l\'orologio parte');
    fatte.push('riduci movimento');
  });

  /* ---------- un giro: misure prima, poi tutti insieme, poi tutti indietro ---------- */
  gruppo(() => {
    const diario = [], o = orologio(), env = ambiente(diario, o);
    const F = carica(app, NOMI, RIGHE, env, VALORI);
    const corto = finto('corto', 260, 200, diario), lungo = finto('lungo', 900, 200, diario), giusto = finto('giusto', 180, 200, diario);
    const sotto = finto('sotto', 700, 200, diario, {coperto:true}), via = finto('via', 700, 200, diario, {staccato:true});
    F.TS.rid = {matches:false};
    [corto, lungo, giusto, sotto, via].forEach(el => F.TS.vis.add(el));
    // nessuno dimenticato: senza «Riduci movimento» l'orologio parte, con la pausa all'inizio (circa 2 s, coi «…»)
    F.tsSveglia();
    eq(o.code.length, 1, 'l\'orologio non parte');
    const pausa = o.code[0].ms;
    vero(pausa >= 1500 && pausa <= 2500, 'pausa all\'inizio di ' + pausa + ' ms (circa 2 s)');
    F.tsSveglia();
    eq(o.code.length, 1, 'tsSveglia due volte: due orologi');
    diario.length = 0;
    o.scatta();   // parte il giro
    // quali: solo i due che non ci stanno, sullo schermo e non coperti
    eq(['corto', 'lungo', 'giusto', 'sotto', 'via'].filter((n, i) => [corto, lungo, giusto, sotto, via][i].hasAttribute('data-ts')), ['corto', 'lungo'], 'scorrono');
    eq(giusto.avvolto + sotto.avvolto, 0, 'un titolo che non scorre è stato toccato (testo avvolto)');
    vero(!giusto.vars['--ts-d'] && !giusto.vars['--ts-t'], 'un titolo che sta nel suo spazio ha le variabili dello scorrimento');
    vero(!F.TS.vis.has(via), 'un titolo tolto dalla pagina resta fra quelli sullo schermo');
    // misure prima di tutte le scritture
    const ultimaLettura = diario.map(x => x.startsWith('leggi')).lastIndexOf(true), primaScrittura = diario.findIndex(x => x.startsWith('scrivi'));
    vero(primaScrittura > ultimaLettura, 'misure e scritture mescolate (ricalcoli della pagina a metà): ' + diario.join(', '));
    // di quanto e in quanto tempo, nelle variabili CSS del titolo
    const dc = parseFloat(corto.vars['--ts-d']), tc = parseFloat(corto.vars['--ts-t']), dl = parseFloat(lungo.vars['--ts-d']), tl = parseFloat(lungo.vars['--ts-t']);
    vero(/px$/.test(corto.vars['--ts-d']) && /s$/.test(corto.vars['--ts-t']), 'variabili senza unità: ' + JSON.stringify(corto.vars));
    vero(dc < 0 && dl < 0, 'il testo non va verso sinistra: ' + dc + ', ' + dl);
    vero(-dc >= 60 && -dl >= 700, 'non si arriva alla fine: ' + dc + ', ' + dl);
    vero(Math.abs(-dc / tc - -dl / tl) < 0.5, 'corto e lungo a velocità diverse: ' + (-dc / tc).toFixed(1) + ' e ' + (-dl / tl).toFixed(1) + ' px/s');
    // tutti indietro insieme, quando il più lungo è in fondo da circa 1,5 s
    eq(o.code.length, 1, 'un orologio per titolo (o nessuno) invece di uno per tutti');
    const fine = o.code[0].ms / 1000 - tl;
    vero(fine >= 1 && fine <= 2, 'pausa alla fine di ' + fine.toFixed(2) + ' s (circa 1,5 s)');
    o.scatta();   // tornano
    vero(env.html.contains('ts-torna'), 'non tornano all\'inizio');
    vero(corto.hasAttribute('data-ts') && lungo.hasAttribute('data-ts'), 'durante il ritorno hanno già i «…» (scatto)');
    eq(o.code.length, 1, 'il ritorno non finisce');
    // il ritorno: insieme, morbido, fra 1 e 2 s (più lungo per i titoli lunghissimi, come nel CSS --ts-b)
    const b = parseFloat(env.html['--ts-b']), fineRitorno = o.code[0].ms / 1000;
    vero(b >= 1 && b <= 2 && fineRitorno >= b && fineRitorno <= b + 0.2, 'ritorno di ' + env.html['--ts-b'] + ', fine dopo ' + fineRitorno + ' s');
    eq([F.tsRitorno([40, 112]), F.tsRitorno([375]), F.tsRitorno([300, 2400])], [1, 1.5, 2], 'durata del ritorno (1 s, fino a 2 s per i lunghissimi)');
    o.scatta();   // a riposo, coi «…»
    vero(!corto.hasAttribute('data-ts') && !lungo.hasAttribute('data-ts') && !env.html.contains('ts-torna'), 'dopo il ritorno non sono a riposo (coi «…»)');
    eq(F.TS.via.length, 0, 'dopo il ritorno restano titoli in movimento');
    // e da capo: di nuovo la pausa all'inizio
    eq(o.code.length, 1, 'dopo un giro non ne parte un altro');
    vero(o.code[0].ms >= 1500 && o.code[0].ms <= 2500, 'il giro dopo parte senza la pausa all\'inizio');
    o.scatta();
    vero(corto.hasAttribute('data-ts') && lungo.hasAttribute('data-ts'), 'il giro dopo non riparte');
    fatte.push('un giro insieme');

    // uno esce dallo schermo mentre scorre: si ferma lui, l'altro continua; nessuno sullo schermo: l'orologio si ferma
    F.TS.ro = null; F.TS.io = {unobserve() {}};
    F.tsVisti([{target:corto, isIntersecting:false}]);
    vero(!corto.hasAttribute('data-ts') && lungo.hasAttribute('data-ts'), 'uscito dallo schermo: si ferma lui solo');
    vero(!F.TS.vis.has(corto), 'uscito dallo schermo ma ancora fra quelli da muovere');
    F.tsFerma();
    F.TS.vis.clear();
    F.tsSveglia();
    eq(o.code.length, 0, 'niente sullo schermo e l\'orologio gira lo stesso');
    // tutti stanno nel loro spazio: un giro e poi l'orologio si ferma (niente da guardare a vuoto)
    F.TS.vis.add(giusto);
    F.tsSveglia(); o.scatta();
    eq(o.code.length, 0, 'niente da far scorrere e l\'orologio gira lo stesso');
    fatte.push('fuori schermo');
  });

  /* ---------- ridisegnati uguali mentre scorrono: continuano dal punto giusto; i titoli nuovi aspettano il giro dopo ---------- */
  gruppo(() => {
    const diario = [], o = orologio(), env = ambiente(diario, o);
    const F = carica(app, NOMI, RIGHE, env, VALORI);
    F.TS.rid = {matches:false}; F.TS.io = {observe() {}, unobserve() {}};
    const vecchio = finto('vecchio', 900, 200, diario, {testo:'Questo dispositivo · ▶︎ Midnight Sun (Deluxe Edition)'});
    F.TS.vis.add(vecchio);
    o.ora = 10000; F.tsSveglia(); o.ora = 12000; o.scatta();   // il giro parte a 12 s
    vero(vecchio.hasAttribute('data-ts'), 'il titolo lungo non scorre');
    eq(vecchio.vars['--ts-r'] || '0s', '0s', 'all\'inizio del giro un titolo parte in ritardo');
    // 3,5 s dopo l'app ridisegna la scheda: il titolo uguale (elemento nuovo) e uno nuovo, diverso
    o.ora = 15500;
    const uguale = finto('uguale', 900, 200, diario, {testo:vecchio.textContent}), diverso = finto('diverso', 900, 200, diario, {testo:'Un altro brano lunghissimo'});
    const scheda = {nodeType:1, matches:() => false, querySelectorAll:() => [uguale, diverso], closest:() => null};
    F.tsCambiati([{target:{nodeType:1, closest:() => null}, addedNodes:[scheda], removedNodes:[]}]);
    vero(uguale.hasAttribute('data-ts'), 'il titolo ridisegnato uguale non continua a scorrere (torna all\'inizio: scatto)');
    eq(uguale.vars['--ts-r'], '-3.5s', 'il titolo ridisegnato non riprende dal punto giusto del giro');
    eq(uguale.vars['--ts-d'], vecchio.vars['--ts-d'], 'il titolo ridisegnato scorre di un\'altra misura');
    vero(!diverso.hasAttribute('data-ts') && !diverso.avvolto, 'un titolo nuovo parte a metà giro invece di aspettare il giro dopo');
    // al ritorno non si riprende (resta fermo fino al giro dopo); e al giro dopo si riparte da zero
    o.scatta();   // tornano
    const altro = finto('altro', 900, 200, diario, {testo:vecchio.textContent});
    F.tsCambiati([{target:{nodeType:1, closest:() => null}, addedNodes:[altro], removedNodes:[]}]);
    vero(!altro.hasAttribute('data-ts'), 'un titolo ridisegnato durante il ritorno riparte');
    o.scatta(); F.TS.vis.add(uguale); o.ora = 20000; o.scatta();   // a riposo, poi il giro dopo
    eq(uguale.vars['--ts-r'], '0s', 'al giro dopo il titolo riparte a metà');
    // il testo di un titolo che scorre cambia (brano nuovo): si ferma; riscritto uguale: continua
    o.ora = 21000;
    uguale.textContent = 'Un brano nuovo';
    F.tsCambiati([{target:uguale, addedNodes:[], removedNodes:[]}]);
    vero(!uguale.hasAttribute('data-ts'), 'il titolo cambiato continua a scorrere con la misura di prima');
    fatte.push('ridisegnati');
  });

  /* ---------- l'orologio non gira a vuoto: si sveglia solo per i titoli (non per l'ora del brano che cambia) ---------- */
  gruppo(() => {
    const diario = [], o = orologio(), env = ambiente(diario, o);
    const F = carica(app, NOMI, RIGHE, env, VALORI);
    const io = new Set(); F.TS.rid = {matches:false}; F.TS.io = {observe:el => io.add(el), unobserve:el => io.delete(el)};
    const giusto = finto('giusto', 180, 200, diario);
    F.TS.vis.add(giusto);
    F.tsSveglia(); o.scatta();   // un giro: niente da far scorrere, l'orologio si ferma
    eq(o.code.length, 0, 'niente da far scorrere e l\'orologio gira');
    // l'ora del brano (tCur) cambia 4 volte al secondo: non è un titolo
    const ora = {nodeType:1, closest:() => null};
    F.tsCambiati([{target:ora, addedNodes:[], removedNodes:[{nodeType:3}]}]);
    eq(o.code.length, 0, 'l\'ora del brano che cambia sveglia l\'orologio (misure ogni 2 s per niente)');
    F.tsVisti([{target:finto('fuori', 900, 200, diario), isIntersecting:false}]);
    eq(o.code.length, 0, 'un titolo che esce dallo schermo sveglia l\'orologio');
    F.tsMisure([{target:giusto, contentRect:{width:200}}]);
    eq(o.code.length, 0, 'la prima misura di un titolo sveglia l\'orologio');
    // un titolo nuovo o riscritto sì; e uno che entra nello schermo
    F.tsCambiati([{target:giusto, addedNodes:[], removedNodes:[]}]);
    eq(o.code.length, 1, 'un titolo riscritto non sveglia l\'orologio');
    F.tsFerma(); F.tsMisure([{target:giusto, contentRect:{width:150}}]);
    eq(o.code.length, 1, 'un titolo più stretto non sveglia l\'orologio');
    // tolto dalla pagina: non si guarda più e smette di scorrere
    const lungo = finto('lungo', 900, 200, diario); F.TS.vis.add(lungo); io.add(lungo);
    o.scatta(); vero(lungo.hasAttribute('data-ts'), 'il titolo lungo non scorre');
    const riga = {nodeType:1, matches:() => false, querySelectorAll:() => [lungo]};
    F.tsCambiati([{target:ora, addedNodes:[], removedNodes:[riga]}]);
    vero(!io.has(lungo) && !F.TS.vis.has(lungo) && !lungo.hasAttribute('data-ts') && !F.TS.via.length, 'un titolo tolto dalla pagina resta guardato o in movimento');
    fatte.push('orologio fermo');
  });

  /* ---------- il testo dentro <ts-in> (è lui che si sposta): una volta sola, icone comprese ----------
     Un elemento suo e non uno <span>: le regole dell'app come «#mini .mt span» (13 px, grigio) o «.qrow span»
     (display:block, opacità) cambierebbero il titolo appena avvolto (il 28/10 nel mini player: da 387 a 335 px) */
  gruppo(() => {
    let creati = 0;
    const nodo = tag => {
      const e = {nodeType:1, tag, localName:tag, childNodes:[], parent:null, className:'',
        get firstChild() { return this.childNodes[0] || null; },
        get lastChild() { return this.childNodes[this.childNodes.length - 1] || null; },
        appendChild(n) { if (n.parent) n.parent.childNodes.splice(n.parent.childNodes.indexOf(n), 1); n.parent = this; this.childNodes.push(n); return n; },
        classList:{contains:c => e.className.split(' ').indexOf(c) >= 0}};
      return e;
    };
    const testo = t => ({nodeType:3, data:t, parent:null});
    const F = carica(app, ['tsAvvolgi'], [], {document:{createElement:t => { creati++; return nodo(t); }}});
    const b = nodo('b'), stella = nodo('span');
    b.appendChild(testo('Midnight Sun (Deluxe Edition) ')); b.appendChild(stella);
    const s = F.tsAvvolgi(b);
    vero(b.childNodes.length === 1 && b.firstChild === s, 'il testo non è dentro un solo elemento');
    eq(s.tag, 'ts-in', 'il testo avvolto (le regole come «.qrow span» lo cambiano se è uno span)');
    eq(s.childNodes.map(n => n.nodeType === 3 ? n.data : n.tag), ['Midnight Sun (Deluxe Edition) ', 'span'], 'testo e icona dentro <ts-in>');
    vero(F.tsAvvolgi(b) === s && creati === 1 && b.childNodes.length === 1, 'avvolto due volte');
    b.childNodes = []; b.appendChild(testo('Altro brano'));   // l'app riscrive il titolo (brano nuovo): si riavvolge
    const s2 = F.tsAvvolgi(b);
    vero(s2 !== s && creati === 2 && s2.childNodes.length === 1 && s2.childNodes[0].data === 'Altro brano', 'il titolo riscritto non si riavvolge');
    fatte.push('testo avvolto');
  });

  /* ---------- dove: i titoli tagliati con «…» dell'app, e il CSS (transform, stessa sfumatura, riduci movimento) ---------- */
  gruppo(() => {
    const F = carica(app, [], RIGHE, {}, VALORI);
    const sel = F.TS_SEL.split(',').map(s => s.trim());
    for (const s of ['.trk .t1', '.trk .t2', '.tile .n', '.tile .a', '#miniT', '#miniA', '#npT', '#npA', '#npqT', '#npqA',
      '.nplhead .tx>b', '.nplhead .tx>span', '.qrow .tx>b', '.qrow .tx>span', '.shead .tx>b', '.shead .tx>span', '.back>span', '.npdev>span', '.l.dv>b', '.l.dv>small'])
      vero(sel.indexOf(s) >= 0, 'il titolo «' + s + '» non scorre (manca in TS_SEL)');
    let css = ''; for (const m of app.match(/<style>[\s\S]*?<\/style>/g) || []) css += m;   // tutti i fogli di stile della pagina
    vero(/\[data-ts\]>ts-in\{[^}]*transform:translateX\(var\(--ts-d/.test(css), 'il testo non si sposta con transform e --ts-d');
    vero(/\[data-ts\]>ts-in\{[^}]*animation:tsVa var\(--ts-t/.test(css), 'la durata non è --ts-t');
    vero(/@keyframes tsVa\{from\{transform:none\}\}/.test(css), 'manca l\'animazione tsVa (solo transform)');
    vero(/html\.ts-torna \[data-ts\]>ts-in\{transform:none;animation:none;transition:transform var\(--ts-b,1s\) ease-in-out\}/.test(css), 'il ritorno non è morbido o non dura --ts-b');
    vero(/\[data-ts\]\{[^}]*text-overflow:clip/.test(css) && /\bts-in\{pointer-events:none\}/.test(css), 'manca il CSS dei titoli che scorrono');
    vero(/@media \(prefers-reduced-motion:reduce\)\{\[data-ts\]\{text-overflow:ellipsis/.test(css), 'con «Riduci movimento» il CSS non lascia i «…»');
    const sf = /#000 calc\(100% - (\d+)px\)/.exec(css.slice(css.indexOf('[data-ts]{')));
    vero(sf, 'manca la sfumatura a destra');
    const G = carica(app, ['tsCalcola'], RIGHE, {}, VALORI), d = G.tsCalcola(300, 200, false).d;
    vero(d - 100 >= +sf[1], 'la fine del titolo si ferma dentro la sfumatura (' + (d - 100) + ' px contro ' + sf[1] + ' px)');
    fatte.push('dove e CSS');
  });
}
