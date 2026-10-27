// Prove del mix tra i brani dell'app (musica/index.html: mix a fine brano e «mix al salto») con JavaScriptCore, che c'è
// su ogni Mac:
//
//     osascript -l JavaScript test_mix.js ../index.html
//
// Le funzioni si prendono dall'app così come sono (non se ne copia nessuna) e si provano da sole e dentro una
// simulazione dei due lettori audio con un orologio finto: niente browser, niente suono, pochi secondi.
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

/* ---------- estrazione delle funzioni dall'app ---------- */
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
function riga(src, inizio) {
  const i = src.indexOf('\n' + inizio);
  if (i < 0) throw new Error("nell'app manca la riga «" + inizio + '…»');
  return src.slice(i + 1, src.indexOf('\n', i + 1));
}
// le funzioni (e le righe) date, eseguite con «env» come ambiente: quello che non definiscono lo cercano lì
function carica(src, nomi, righe, env, trasforma, extra) {
  let codice = righe.map(r => riga(src, r)).join('\n') + '\n' + nomi.map(n => estrai(src, n)).join('\n');
  if (trasforma) codice = trasforma(codice);
  const tutti = nomi.concat(Object.keys(extra || {}));
  codice += '\n' + Object.values(extra || {}).join('\n');
  const f = new Function('env', 'with (env) { return (function () {\n' + codice + '\nreturn {' + tutti.join(',') + '};\n})(); }');
  return f(env);
}

/* ---------- promesse finte: si risolvono quando lo dice la simulazione (drena), come i microtask del browser ---------- */
const MICRO = [];
function drena() { let n = 0; while (MICRO.length) { if (++n > 1e6) throw new Error('promesse senza fine'); MICRO.shift()(); } }
class SP {
  constructor(ex) {
    this.s = 0; this.v = undefined; this.cb = []; let fatto = false;
    const ok = v => { if (!fatto) { fatto = true; this._ris(v); } }, ko = e => { if (!fatto) { fatto = true; this._fin(2, e); } };
    try { ex(ok, ko); } catch (e) { ko(e); }
  }
  _ris(v) { if (v && typeof v.then === 'function') MICRO.push(() => v.then(x => this._fin(1, x), e => this._fin(2, e))); else this._fin(1, v); }
  _fin(s, v) { if (this.s) return; this.s = s; this.v = v; this.cb.splice(0).forEach(f => MICRO.push(f)); }
  then(ok, ko) {
    return new SP((res, rej) => {
      const f = () => { try { if (this.s === 1) res(typeof ok === 'function' ? ok(this.v) : this.v); else if (typeof ko === 'function') res(ko(this.v)); else rej(this.v); } catch (e) { rej(e); } };
      if (this.s) MICRO.push(f); else this.cb.push(f);
    });
  }
  catch(ko) { return this.then(null, ko); }
  finally(fn) { return this.then(v => { fn(); return v; }, e => { fn(); throw e; }); }
  static resolve(v) { return v instanceof SP ? v : new SP(r => r(v)); }
  static reject(e) { return new SP((r, j) => j(e)); }
  static all(a) { return new SP((res, rej) => { const out = []; let n = a.length; if (!n) return res(out); a.forEach((p, i) => SP.resolve(p).then(v => { out[i] = v; if (!--n) res(out); }, rej)); }); }
}
// una funzione «async function nome(…)» dell'app che gira con le promesse finte (diventa un generatore)
function sincrona(src, nome) {
  const f = estrai(src, nome);
  vero(f.startsWith('async function ' + nome + '('), nome + ' non è più async: aggiornare la prova');
  const testa = f.slice(0, f.indexOf('{') + 1), corpo = f.slice(f.indexOf('{') + 1, -1);
  return testa.replace('async function', 'function') + ' return corri(function* () {' + corpo.replace(/\bawait /g, 'yield ') + '}.bind(this)); }';
}
// una funzione async dell'app fatta girare con le promesse finte (async → generatore)
function corri(gen) {
  return new SP((res, rej) => {
    const it = gen();
    const passo = (v, err) => { let r; try { r = err ? it.throw(v) : it.next(v); } catch (e) { return rej(e); } if (r.done) return res(r.value); SP.resolve(r.value).then(x => passo(x), e => passo(e, true)); };
    passo();
  });
}

/* ---------- controlli ---------- */
function vero(c, msg) { if (!c) throw new Error(msg); }
function vicino(a, b, tol, msg) { if (!(Math.abs(a - b) <= tol)) throw new Error(msg + ': ' + a + ' invece di ' + b + ' (±' + tol + ')'); }
const wrapT = x => x - Math.round(x);

function prove(src, fatte) {
  const prova = (nome, fn) => { fn(); fatte.push(nome); };
  prova('sintassi dell\'app', () => sintassi(src));
  prova('collegamenti', () => collegamenti(src));
  prova('funzioni pure', () => pure(src));
  prova('scelta del fattore come prima', () => fattoreComePrima(src));
  prova('pezzi di grid', () => pezziGrid(src));
  prova('battiti a metà brano', () => battitiAMeta(src));
  prova('salto a tempo', () => scenarioATempo(src));
  prova('salto verso un brano in coda', () => scenarioCoda(src));
  prova('ritmi lontani', () => scenarioLontani(src));
  prova('misura lenta', () => scenarioMisuraLenta(src));
  prova('seconda pressione in attesa', () => scenarioSecondaAttesa(src));
  prova('seconda pressione in dissolvenza', () => scenarioSecondaDissolvenza(src));
  prova('brano nuovo non pronto', () => scenarioNonPronto(src));
  prova('come prima', () => scenarioComePrima(src));
  prova('pausa in attesa', () => scenarioPausa(src));
  prova('pausa non chiesta in attesa', () => scenarioPausaNonChiesta(src));
  prova('lettore rifiutato', () => scenarioRifiutato(src));
  prova('precedente in attesa', () => scenarioPrecedente(src));
  prova('brano spostato in attesa', () => scenarioSpostato(src));
  prova('iPhone con il mixer', () => scenarioMixer(src));
  prova('vicino alla fine del brano', () => scenarioVicinoFine(src));
  prova('velocità a gradini', () => gradiniPuri(src));
  prova('mix a fine brano senza mixer', () => scenarioFineBrano(src, false));
  prova('mix a fine brano con il mixer', () => scenarioFineBrano(src, true));
  prova('salto con il mixer: pochi cambi di velocità', () => scenarioSaltoCambi(src));
  prova('iOS prima del 27 senza mixer', () => versioneIOS(src));
  prova('niente più «un lettore solo»', () => senzaUnSolo(src));
}

// tutto lo script dell'app si compila (non si esegue): un errore di battitura in qualsiasi punto si vede qui
function sintassi(src) {
  const re = /<script>([\s\S]*?)<\/script>/g; let m, n = 0;
  while ((m = re.exec(src))) { try { new Function(m[1]); } catch (e) { throw new Error('lo script dell\'app non si compila: ' + e.message); } n++; }
  vero(n >= 2, 'script dell\'app non trovati');
}

// i tasti giusti passano dal mix al salto (un'unione di rami potrebbe rimettere il cambio immediato)
function collegamenti(src) {
  const c = (re, msg) => vero(re.test(src), 'collegamento mancante: ' + msg);
  c(/\nMIX\.XS = 6;/, 'MIX.XS = 6');
  c(/case 'next': avanti\(\); break;[\s\S]*case 'next': avanti\(\); break;/, "tasto Successivo e comando dal telecomando → avanti()");
  c(/case 'qjump': saltaA\(\+d\.i, /, 'tocco su un brano di «In coda» → saltaA');
  c(/sa\('nexttrack', \(\) => avanti\(\)\)/, 'schermata di blocco e cuffie → avanti()');
  c(/ArrowRight' && e\.metaKey\)[^\n]*avanti\(\)/, 'Cmd+→ → avanti()');
  c(/next\(\) \{ avanti\(\); \}/, 'AM.next → avanti()');
  c(/auOn\('timeupdate', saltoTick\)/, "auOn('timeupdate', saltoTick)");
  c(/auOn\('pause', saltoPausa\)/, "auOn('pause', saltoPausa)");
  c(/auOn\('pause', xfPausa\)/, "auOn('pause', xfPausa)");
  c(/auOn\('seeking', saltoSpostato\)/, "auOn('seeking', saltoSpostato)");
  // «Precedente» e la libreria restano immediati
  vero(!/function prev\(\)[^\n]*saltaA/.test(src), 'Precedente non deve mixare');
  vero(/function prev\(\) \{ saltoAnnulla\(\);/.test(src), 'Precedente annulla il salto in attesa');
}

function pure(src) {
  const env = {MIX:{XS:6}};
  const F = carica(src, ['rampaVel', 'pezzoQui', 'gridPezzo', 'partenzaBattito', 'accorciaW', 'saltoResto'], ['const SALTO = '], env);
  // rampa a S: da 1 a r, a metà strada a metà tempo, sempre in salita
  vicino(F.rampaVel(0, 1.08), 1, 1e-9, 'rampa all\'inizio'); vicino(F.rampaVel(1, 1.08), 1.08, 1e-9, 'rampa alla fine');
  vicino(F.rampaVel(-1, 1.08), 1, 1e-9, 'rampa prima'); vicino(F.rampaVel(3, 0.9), 0.9, 1e-9, 'rampa dopo');
  vicino(F.rampaVel(0.5, 1.08), 1.04, 1e-9, 'rampa a metà');
  let p = 1; for (let q = 0; q <= 1; q += 0.01) { const v = F.rampaVel(q, 1.1); vero(v >= p - 1e-12, 'rampa non monotona'); p = v; }
  vero(F.rampaVel(0.1, 1.1) - 1 < 0.1 * 0.1 * 0.5, 'la rampa deve partire piano');
  // pezzo da misurare nel punto in cui si è
  vero(F.pezzoQui(3, 200) === 'i' && F.pezzoQui(10, 200) === 'i', 'vicino all\'inizio basta la misura dei primi 30 s');
  vero(F.pezzoQui(175, 200) === 'f', 'vicino alla fine basta la misura degli ultimi 30 s');
  vero(F.pezzoQui(87.34567, 200) === 87.346, 'a metà brano: 20 s da lì (al millesimo)');
  vero(F.pezzoQui(87.3, 0) === 87.3, 'durata sconosciuta: 20 s da lì');
  // pezzi di grid
  const g = (part, dur) => JSON.stringify(F.gridPezzo(part, dur));
  vero(g('i', 200) === '{"da":0,"lung":30}', "grid 'i'"); vero(g('f', 200) === '{"da":170,"lung":30}', "grid 'f'");
  vero(g('f', 20) === '{"da":0,"lung":30}', "grid 'f' di un brano corto"); vero(g(87.3, 200) === '{"da":87.3,"lung":20}', 'grid a metà');
  vero(g(-2, 200) === '{"da":0,"lung":20}', 'grid con un punto negativo');
  // partenza sul battito: battiti di A a 0.5 + k·0.5 (120 BPM), il nuovo con il primo battito a 0.2
  const ga = {bpm:120, b0:0.5}, gb = {bpm:125, b0:0.2};
  let r = F.partenzaBattito(10.3, 1, ga, gb, false);   // a 0.6 del battito: si aspetta il prossimo (0.2 s)
  vicino(r.attesa, 0.2, 1e-9, 'attesa del battito'); vicino(r.pos, 0.2, 1e-9, 'il nuovo parte dal suo primo battito');
  r = F.partenzaBattito(10.3, 1.25, ga, gb, false);    // più veloce: il battito arriva prima
  vicino(r.attesa, 0.16, 1e-9, 'attesa del battito a velocità 1.25');
  r = F.partenzaBattito(10.02, 1, ga, gb, false);      // appena dopo un battito: si parte subito, il nuovo spostato uguale
  vicino(r.attesa, 0, 1e-12, 'subito dopo il battito'); vicino(r.pos, 0.22, 1e-9, 'nuovo spostato della stessa frazione');
  r = F.partenzaBattito(10.2, 1.1, ga, gb, true);      // timer arrivato tardi: si parte subito, comunque a tempo
  vicino(r.attesa, 0, 1e-12, 'timer tardi'); vicino(r.pos, 0.2 + 0.2 / 1.1, 1e-9, 'timer tardi: nuovo spostato del tempo passato');
  r = F.partenzaBattito(10.49, 1, ga, gb, true);       // timer arrivato appena prima del battito: si aspetta l'ultimo pezzetto
  vicino(r.attesa, 0.01, 1e-9, 'timer appena in anticipo');
  // simulazione: comunque si prema, quando il nuovo parte i battiti coincidono
  for (let c = 30; c < 31; c += 0.013) for (const rate of [0.9, 1, 1.07]) {
    const x = F.partenzaBattito(c, rate, ga, gb, false), cA = c + x.attesa * rate;
    vero(x.attesa >= 0 && x.attesa <= 0.5 / rate + 1e-9, 'attesa più lunga di un battito');
    // fase di A (in battiti) quando parte il nuovo, e tempo passato dal battito di A in secondi veri = posizione del nuovo oltre il suo battito
    const fa = (cA - ga.b0) / 0.5, dopo = (fa - Math.floor(fa)) * 0.5 / rate;
    vicino(wrapT((x.pos - gb.b0 - dopo) / 0.5), 0, 1e-6, 'nuovo non a tempo (c=' + c + ', v=' + rate + ')');
  }
  // dissolvenza accorciata: stesso punto adesso, fine dopo s secondi
  for (const [q, s] of [[0, 1], [0.3, 1], [0.9, 0.5]]) {
    const W = F.accorciaW(q, s), t0 = -q * W;   // ora = 0
    vicino((0 - t0) / W, q, 1e-9, 'accorcia: il punto adesso cambia'); vicino((s - t0) / W, 1, 1e-9, 'accorcia: non finisce dopo s secondi');
  }
  // tempo che deve restare al brano in corso
  vicino(F.saltoResto(), 6 + 1 + 6 * 1.12, 1e-9, 'tempo che deve restare');
  env.MIX.XS = 10; vicino(F.saltoResto(), 6 + 1 + 10 * 1.12, 1e-9, 'tempo che deve restare con 10 s');
}

// la scelta del fattore BPM è quella di sempre del mix a fine brano (qui il codice di prima, copiato com'era)
function fattoreComePrima(src) {
  const F = carica(src, ['mixFattore'], [], {});
  const prima = (ga, gb) => {
    const P = {r:1, f:1, match:false};
    if (ga && gb && ga.c >= 1.15 && gb.c >= 1.15) {
      let bf = 1, be = 9; for (const f of [0.5, 1, 2]) { const e = Math.abs(gb.bpm * f / ga.bpm - 1); if (e < be) { be = e; bf = f; } }
      if (be <= 0.12) { P.match = true; P.f = bf; P.r = gb.bpm * bf / ga.bpm; }
    }
    return P;
  };
  let seme = 7; const rnd = () => (seme = (seme * 16807) % 2147483647) / 2147483647;
  for (let i = 0; i < 4000; i++) {
    const ga = rnd() < 0.05 ? null : {bpm:70 + rnd() * 120, c:0.9 + rnd()}, gb = rnd() < 0.05 ? null : {bpm:70 + rnd() * 120, c:0.9 + rnd()};
    const a = prima(ga, gb), b = F.mixFattore(ga, gb);
    vero(a.match === b.match && a.f === b.f && Math.abs(a.r - b.r) < 1e-12, 'fattore diverso da prima per ' + JSON.stringify([ga, gb]));
    if (!b.match) vero(b.perche === (ga && gb && ga.c >= 1.15 && gb.c >= 1.15 ? 'ritmi troppo lontani' : 'ritmi non riconosciuti'), 'motivo sbagliato: ' + b.perche);
  }
  const m = F.mixFattore({bpm:128, c:2}, {bpm:63, c:2});
  vero(m.match && m.f === 2 && Math.abs(m.r - 126 / 128) < 1e-12, 'il doppio dei BPM del nuovo');
}

// grid() con un punto qualsiasi: il pezzo giusto (dal file o dal Mac) e b0 in secondi del brano; non resta in memoria
function pezziGrid(src) {
  const SR = 11025, log = {url:[], put:[], bg:[]};
  const tutto = new Float32Array(200 * SR); for (let i = 0; i < tutto.length; i++) tutto[i] = i;
  const env = {
    Promise:SP, setTimeout:f => f(), BPMC:new Map(), SRV:true, API:'api/musica/', bpmPut:(k, v) => log.put.push(k),
    T:new Map([['rS', {id:'rS', remote:true, sid:'S', duration:200}], ['rL', {id:'rL', remote:true, sid:'L', duration:200}]]),
    getFile:id => SP.resolve(id === 'rL' ? {size:1000, arrayBuffer:() => SP.resolve('buf')} : null),
    decodeMono:() => SP.resolve({x:tutto, sr:SR, dur:200}),
    fetchT:url => { log.url.push(url); return SP.resolve({ok:true, arrayBuffer:() => SP.resolve('wav')}); },
    wavPCM:() => ({x:new Float32Array(20 * SR), sr:SR}),
    beatGrid:(x, sr) => { log.bg.push([x.length, x[0]]); return {bpm:128, b0:0.25, c:2}; }
  };
  const F = carica(src, ['grid', 'gridPezzo'], ['const gridWait = '], env, c => {
    vero(c.indexOf('(async () => {') > 0 && c.indexOf('})().catch(() => null)') > 0, 'grid(): forma cambiata, aggiornare la prova');
    return c.replace('(async () => {', 'corri(function* () {').replace('})().catch(() => null)', '}).catch(() => null)').replace(/\bawait /g, 'yield ');
  });
  env.corri = corri;
  const chiedi = (id, part) => { let v; F.grid(id, part).then(x => { v = x; }); drena(); return v; };
  let v = chiedi('rS', 87.3);
  vero(/pezzo\/S\?da=87\.300&durata=20$/.test(log.url.pop()), 'dal Mac: 20 s da quel punto');
  vicino(v.b0, 87.55, 1e-9, 'b0 in secondi del brano'); vero(!log.put.length && !env.BPMC.size, 'un pezzo a metà brano non va in memoria');
  v = chiedi('rS', 'f'); vero(/da=170\.000&durata=30$/.test(log.url.pop()), "dal Mac: 'f' come prima"); vero(log.put.pop() === 'rS|f', "'f' in memoria come prima");
  v = chiedi('rS', 'i'); vero(/da=0\.000&durata=30$/.test(log.url.pop()), "dal Mac: 'i' come prima"); vero(log.put.pop() === 'rS|i', "'i' in memoria");
  v = chiedi('rL', 50); let b = log.bg.pop();
  vero(b[0] === 20 * SR && b[1] === 50 * SR, 'dal file: 20 s da quel punto (' + b + ')'); vicino(v.b0, 50.25, 1e-9, 'dal file: b0 in secondi del brano');
  chiedi('rL', 'f'); b = log.bg.pop(); vero(b[0] === 30 * SR && b[1] === 170 * SR, "dal file: 'f' come prima (" + b + ')');
  chiedi('rL', 'i'); b = log.bg.pop(); vero(b[0] === 30 * SR && b[1] === 0, "dal file: 'i' come prima (" + b + ')');
}

// la misura vera (beatGrid) su un brano finto a 128 BPM: a metà brano trova gli stessi battiti che all'inizio
function battitiAMeta(src) {
  const F = carica(src, ['beatGrid', 'fftMag', 'gridPezzo'], ['const FFTN = ', 'const HANN = '], {});
  const SR = 11025, T = 60 / 128, a0 = 0.31, dur = 120, x = new Float32Array(dur * SR);
  let seme = 3; const rnd = () => (seme = (seme * 16807) % 2147483647) / 2147483647 - 0.5;
  for (let i = 0; i < x.length; i++) x[i] = 0.01 * rnd();
  const colpo = (t, amp, lung) => { const s = Math.round(t * SR); for (let i = 0; i < lung * SR && s + i < x.length; i++) x[s + i] += amp * Math.exp(-i / (0.012 * SR)) * (rnd() + 0.6 * Math.sin(2 * Math.PI * 70 * i / SR)); };
  for (let t = a0; t < dur; t += T) { colpo(t, 0.9, 0.08); colpo(t + T / 2, 0.25, 0.03); }
  const misura = part => {
    const {da, lung} = F.gridPezzo(part, dur), a = Math.floor(da * SR), b = Math.min(x.length, Math.floor((da + lung) * SR));
    const g = F.beatGrid(x.subarray(a, b), SR); vero(g, 'nessun ritmo trovato in ' + part);
    vicino(g.bpm, 128, 0.6, 'BPM in ' + part); vero(g.c >= 1.15, 'ritmo poco sicuro in ' + part + ' (' + g.c + ')');
    return wrapT((da + g.b0 - a0) / T) * T;   // scarto dal battito vero, in secondi
  };
  const ei = misura('i'), em = misura(61.7), em2 = misura(37.123);
  vicino(ei, 0, 0.05, 'primo battito all\'inizio');
  vicino(em, ei, 0.012, 'a metà brano i battiti non coincidono con quelli dell\'inizio');
  vicino(em2, ei, 0.012, 'a 37 s i battiti non coincidono con quelli dell\'inizio');
}

/* ---------- simulazione dei due lettori ---------- */
function mondo(src, opz) {
  opz = opz || {};
  const W = {t:0, timers:[], seq:0}, log = {toast:[], diario:[], load:[], next:[], fine:[], grid:[], gain:[], prep:[]};
  const tempo = {
    setTimeout(fn, ms) { const id = ++W.seq; W.timers.push({id, at:W.t + Math.max(0, +ms || 0), fn}); return id; },
    setInterval(fn, ms) { const id = ++W.seq; W.timers.push({id, at:W.t + ms, fn, ogni:ms}); return id; },
    clear(id) { W.timers = W.timers.filter(x => x.id !== id); }
  };
  const brani = opz.brani || [
    {id:'rA', title:'Brano A', sid:'A', duration:240, bpm:124, b0:0.37, c:2},
    {id:'rB', title:'Brano B', sid:'B', duration:200, bpm:128, b0:0.21, c:2},
    {id:'rC', title:'Brano C', sid:'C', duration:210, bpm:126, b0:0.12, c:2},
    {id:'rD', title:'Brano D', sid:'D', duration:180, bpm:122, b0:0.4, c:2}];
  const T = new Map(brani.map(b => [b.id, Object.assign({remote:true}, b)]));
  const salvati = new Set(opz.salvati || brani.map(b => b.id));
  const file = u => {   // durata e tempo di caricamento di quello che si mette nel lettore
    const m = /^blob:(.+)$/.exec(u) || /^api\/musica\/file\/(.+)$/.exec(u); if (!m) return null;
    const b = brani.find(x => x.id === m[1] || x.sid === m[1]); if (!b) return null;
    return {dur:b.duration, ms:u.startsWith('blob:') ? 60 : (opz.streamMs != null ? opz.streamMs : 700)};
  };
  class Lettore {
    constructor(nome) { Object.assign(this, {nome, paused:true, currentTime:0, _v:1, cambi:[], readyState:0, muted:false, duration:NaN, ended:false, seeking:false, volume:1, _src:null, _l:{}, _vuole:false, _da:0, rifiuta:false, rifiutaUnaVolta:false}); }
    get playbackRate() { return this._v; }
    set playbackRate(v) { if (v !== this._v) this.cambi.push([W.t, v]); this._v = v; }   // i cambi veri di velocità
    get buffered() { const d = this.readyState >= 3 ? this.duration : 0; return {length:d ? 1 : 0, start:() => 0, end:() => d}; }
    addEventListener(ev, fn, o) { (this._l[ev] = this._l[ev] || []).push({fn, once:!!(o && o.once)}); }
    removeEventListener(ev, fn) { this._l[ev] = (this._l[ev] || []).filter(x => x.fn !== fn); }
    emit(ev) { const l = (this._l[ev] || []).slice(); this._l[ev] = (this._l[ev] || []).filter(x => !x.once); l.forEach(x => x.fn({target:this})); }
    getAttribute(a) { return a === 'src' ? this._src : null; }
    removeAttribute(a) { if (a === 'src') this._src = null; }
    load() { this.pause(); Object.assign(this, {readyState:0, currentTime:0, duration:NaN, ended:false, _vuole:false}); const u = this._src, f = file(u); if (f) tempo.setTimeout(() => { if (this._src !== u) return; this.duration = f.dur; this.readyState = 4; this.emit('loadedmetadata'); this._forse(); }, f.ms); }
    set src(u) { this._src = u; this.load(); }
    get src() { return this._src || ''; }
    play() { if (this.rifiuta) return SP.reject(new Error('NotAllowedError')); if (this.rifiutaUnaVolta) { this.rifiutaUnaVolta = false; const e = new Error('interrotto'); e.name = 'AbortError'; return SP.reject(e); } if (this.paused) { this.paused = false; this.ended = false; this._vuole = true; this.emit('play'); this._forse(); } return SP.resolve(); }
    _forse() { if (!this.paused && this._vuole && this.readyState >= 3) { this._vuole = false; this._da = W.t + (opz.latenza != null ? opz.latenza : 20); this.emit('playing'); } }
    pause() { if (!this.paused) { this.paused = true; this.emit('pause'); } }
    avanza(dt) {
      if (this.paused || this.readyState < 3 || this._vuole || W.t < this._da) return;
      this.currentTime += dt * this.playbackRate;
      if (this.currentTime >= this.duration) { this.currentTime = this.duration; this.ended = true; this.paused = true; this.emit('pause'); this.emit('ended'); }
    }
  }
  const DECKS = [new Lettore('au'), new Lettore('au2')], AUX = new Lettore('auX'), tutti = DECKS.concat(AUX);
  const ritardo = (ms, v) => new SP(ok => tempo.setTimeout(() => ok(v), ms));
  const env = {
    au:DECKS[0], DECKS, AUX, curId:null, curUrl:null, keptFor:null, countFor:null, userVol:1, pausaVoluta:0,
    S:{queue:brani.map(b => b.id), orig:[], qi:0, shuffle:false, repeat:'off', from:''}, T,
    MIX:{on:true, X:10, plan:null, act:null, ctx:null, gains:new Map(), loop:0, prepT:0, XS:6, salto:null},
    BPMC:new Map(), RC:{id:null}, NEEDS_MIXER:false, API:'api/musica/', SRV:true, ls:{get:(k, d) => d, set:() => {}},
    Promise:SP, setTimeout:tempo.setTimeout, setInterval:tempo.setInterval, clearTimeout:tempo.clear, clearInterval:tempo.clear,
    performance:{now:() => W.t}, Date:{now:() => 1e12 + W.t},
    URL:{createObjectURL:f => 'blob:' + f.nome, revokeObjectURL:() => {}},
    TT:id => T.get(id), canPlay:() => true,
    toast:m => log.toast.push(m),
    diario:(e, x) => log.diario.push(Object.assign({e, b:(T.get(env.curId) || {}).title || '', t:W.t}, x || {})),
    load:(i, play) => { log.load.push([i, !!play]); F.xfAbort(); env.S.qi = i; env.curId = env.S.queue[i]; const t = T.get(env.curId); env.au.src = salvati.has(t.id) ? 'blob:' + t.id : 'api/musica/file/' + t.sid; if (play) env.au.play(); },
    next:auto => log.next.push(auto),
    updateNow() {}, saveState() {}, markCur() {}, checkHeard() {}, controllaPassaggio() {},
    xfPreparaFinto:P => { log.prep.push([P.idB, W.t, !!env.MIX.salto]); return SP.resolve(); },   // mix a fine brano finto: si guarda solo quando si prepara
    document:{hidden:false}, corri,
    getFile:id => SP.resolve(salvati.has(id) ? {nome:id} : null),
    grid:(id, part) => {
      log.grid.push([id, part]);
      const b = brani.find(x => x.id === id); if (!b || b.c == null) return SP.resolve(null);
      let v = {bpm:b.bpm, b0:b.b0, c:b.c};
      if (part !== 'i') { const da = part === 'f' ? b.duration - 30 : part, TT = 60 / b.bpm; v.b0 = b.b0 + Math.ceil((da - b.b0) / TT) * TT; }
      return ritardo(opz.misuraMs != null ? opz.misuraMs : 500, v);
    }
  };
  const F = carica(src, ['xfStart', 'xfRun', 'xfEnd', 'xfAbort', 'xfLoop', 'xfRifiutato', 'xfTick', 'xfAccorcia', 'nextIdx', 'setGain', 'applyVol', 'prev', 'xfPausa',
    'mixFattore', 'rampaVel', 'pezzoQui', 'partenzaBattito', 'accorciaW', 'saltoResto', 'velocita', 'conPitch', 'gradini', 'rampaGradini', 'correzioneFase',
    'avanti', 'saltaA', 'saltoIdx', 'gridQui', 'saltoMisurato', 'saltoTick', 'saltoParti', 'saltoCarica', 'saltoVia', 'saltoSubito', 'saltoAnnulla', 'saltoPausa', 'saltoSpostato'],
  ['const wrap = ', 'const otherDeck = ', 'const SALTO = '], env, null,
  {xfPrepare:opz.finebrano ? sincrona(src, 'xfPrepare') : 'function xfPrepare(P) { return xfPreparaFinto(P); }'});
  // gli eventi come auOn nell'app: contano solo quelli del lettore in uso
  tutti.forEach(el => {
    el.addEventListener('timeupdate', () => { if (el === env.au) { F.xfTick(); F.saltoTick(); } });
    el.addEventListener('pause', () => { if (el === env.au) { F.xfPausa(); F.saltoPausa(); } });
    el.addEventListener('seeking', () => { if (el === env.au) F.saltoSpostato(); });
    el.addEventListener('ended', () => { if (el === env.au) log.fine.push(env.curId); });
  });
  const passa = (s, ogni) => {
    const fine = W.t + s * 1000;
    while (W.t < fine - 1e-9) {
      W.t += 5;
      tutti.forEach(el => el.avanza(0.005));
      for (;;) {
        const due = W.timers.filter(x => x.at <= W.t).sort((a, b) => a.at - b.at || a.id - b.id)[0]; if (!due) break;
        if (due.ogni) due.at += due.ogni; else W.timers = W.timers.filter(x => x !== due);
        due.fn(); drena();
      }
      if (W.t % 250 === 0) tutti.forEach(el => { if (!el.paused && el.readyState >= 3) el.emit('timeupdate'); });
      drena();
      if (ogni) ogni();
    }
  };
  const fai = fn => { const v = fn(); drena(); return v; };
  // si comincia con il brano A che suona, al secondo «da»
  env.curId = 'rA'; env.S.qi = 0; DECKS[0].src = 'blob:rA'; DECKS[0].play(); passa(0.2);
  DECKS[0].currentTime = opz.da != null ? opz.da : 60;
  return {W, log, env, F, DECKS, AUX, passa, fai, brano:id => brani.find(b => b.id === id), ora:() => W.t};
}
const ultimo = (log, e) => log.diario.filter(x => x.e === e).pop();
// scarto di fase (in battiti) tra il brano che esce e quello che entra, dalle griglie vere dei due brani
function scarto(M, out, inn, ida, idb, f) {
  const a = M.brano(ida), b = M.brano(idb), TA = 60 / a.bpm, TB = 60 / (b.bpm * (f || 1));
  return wrapT((out.currentTime - a.b0) / TA - (inn.currentTime - b.b0) / TB);
}

function scenarioATempo(src) {
  const M = mondo(src), {env, log, DECKS} = M, A = DECKS[0], B = DECKS[1], t0 = M.ora();
  M.fai(() => M.F.avanti());
  vero(log.toast.pop() === 'Mix verso «Brano B»…', 'manca il toast del mix');
  vero(env.MIX.salto && !log.next.length && !log.load.length, 'deve aspettare il mix, non cambiare subito');
  vero(log.grid.some(g => g[0] === 'rA' && typeof g[1] === 'number' && Math.abs(g[1] - 60) < 0.01), 'deve misurare il brano in corso da dove è');
  vero(log.grid.some(g => g[0] === 'rB' && g[1] === 'i'), "deve misurare l'inizio del brano nuovo");
  let partito = 0, maxV = 1, err = [];
  M.passa(12, () => {
    if (!partito && env.curId === 'rB') partito = M.ora();
    if (!partito) maxV = Math.max(maxV, A.playbackRate);
    if (partito && env.MIX.act && M.ora() - partito > 60) err.push(Math.abs(scarto(M, A, B, 'rA', 'rB')));
  });
  vero(partito, 'il brano nuovo non è partito');
  const s = (partito - t0) / 1000;
  vero(s >= 2.4 && s <= 5.5, 'dalla pressione al brano nuovo ' + s + ' s (atteso 3–5 s)');
  vicino(maxV, 128 / 124, 0.002, 'velocità del brano in corso prima del nuovo');
  vero(err.length > 100, 'dissolvenza troppo corta (' + err.length + ')');
  vero(err[0] < 0.08, 'battiti non agganciati alla partenza (' + err[0] + ')');
  vero(Math.max.apply(null, err.slice(-40)) < 0.03, 'battiti non agganciati a fine dissolvenza (' + Math.max.apply(null, err.slice(-40)) + ')');
  vero(!env.MIX.act && !env.MIX.salto, 'dissolvenza non finita');
  vero(env.au === B && !B.paused && B.playbackRate === 1 && B._g === 1, 'il brano nuovo deve suonare da solo a volume pieno');
  vero(A.paused && !A.getAttribute('src'), 'il lettore del brano vecchio deve essere libero');
  vero(env.S.qi === 1 && env.curId === 'rB', 'coda non aggiornata');
  const d = ultimo(log, 'mix al salto');
  vero(d && d.esito === 'a tempo' && d.match === true && Math.abs(d.r - 1.032) < 0.001 && d.verso === 'Brano B', 'diario: ' + JSON.stringify(d));
  // durata della dissolvenza: MIX.XS secondi
  vicino(err.length * 5 / 1000 + 0.06, 6, 0.15, 'durata della dissolvenza');
}

function scenarioCoda(src) {
  const M = mondo(src), {env, log, DECKS} = M;
  M.fai(() => M.F.saltaA(3, () => log.next.push('subito'), true));
  vero(log.toast.pop() === 'Mix verso «Brano D»…', 'toast verso il brano toccato');
  M.passa(14);
  vero(env.curId === 'rD' && env.S.qi === 3 && !log.next.length && !log.load.length, 'deve arrivare a D col mix');
  vero(ultimo(log, 'mix al salto').esito === 'a tempo', 'diario');
  vero(DECKS[0].paused && !DECKS[0].getAttribute('src'), 'lettore vecchio libero');
}

function scenarioLontani(src) {
  const brani = [{id:'rA', title:'Brano A', sid:'A', duration:240, bpm:90, b0:0.3, c:2}, {id:'rB', title:'Brano B', sid:'B', duration:200, bpm:140, b0:0.2, c:2}];
  const M = mondo(src, {brani}), {env, log, DECKS} = M, t0 = M.ora();
  M.fai(() => M.F.avanti());
  let partito = 0, maxV = 0, minV = 9;
  M.passa(8, () => { if (!partito && env.curId === 'rB') partito = M.ora(); maxV = Math.max(maxV, DECKS[0].playbackRate); minV = Math.min(minV, DECKS[0].playbackRate); });
  vero(partito && (partito - t0) / 1000 < 1.5, 'senza cambio di velocità si parte appena pronti');
  vero(maxV === 1 && minV === 1, 'il brano in corso non deve cambiare velocità');
  const d = ultimo(log, 'mix al salto'); vero(d.esito === 'dissolvenza: ritmi troppo lontani' && d.match === false, 'diario: ' + JSON.stringify(d));
  vero(!env.MIX.act && env.curId === 'rB', 'dissolvenza finita');
  // ritmi non riconosciuti
  const M2 = mondo(src, {brani:[brani[0], Object.assign({}, brani[1], {c:null})]});
  M2.fai(() => M2.F.avanti()); M2.passa(8);
  vero(ultimo(M2.log, 'mix al salto').esito === 'dissolvenza: ritmi non riconosciuti', 'ritmi non riconosciuti');
  vero(M2.env.curId === 'rB', 'brano nuovo');
}

function scenarioMisuraLenta(src) {
  const M = mondo(src, {misuraMs:5000}), {env, log, DECKS} = M, t0 = M.ora();
  M.fai(() => M.F.avanti());
  let partito = 0, maxV = 0;
  M.passa(12, () => { if (!partito && env.curId === 'rB') partito = M.ora(); if (!partito) maxV = Math.max(maxV, DECKS[0].playbackRate); });
  const s = (partito - t0) / 1000;
  vero(s >= 4 && s < 4.5, 'misura lenta: dissolvenza normale dopo 4 s (' + s + ')');
  vero(maxV === 1, 'misura lenta: nessun cambio di velocità');
  vero(ultimo(log, 'mix al salto').esito === 'dissolvenza: misura lenta', 'diario');
}

function scenarioSecondaAttesa(src) {
  const M = mondo(src), {env, log, DECKS} = M;
  M.fai(() => M.F.avanti()); M.passa(1.5);
  vero(env.MIX.salto && DECKS[0].playbackRate > 1, 'in rampa');
  M.fai(() => M.F.avanti());
  vero(JSON.stringify(log.load) === '[[1,true]]', 'seconda pressione: cambio subito verso B (' + JSON.stringify(log.load) + ')');
  vero(!env.MIX.salto && DECKS[0].playbackRate === 1, 'piano annullato, velocità normale');
  vero(!DECKS[1].getAttribute('src') && DECKS[1].paused, "l'altro lettore torna libero");
  vero(ultimo(log, 'mix al salto').esito === 'seconda pressione: cambio subito', 'diario');
  // dalla coda: il brano appena toccato
  const M2 = mondo(src);
  M2.fai(() => M2.F.avanti()); M2.passa(0.8);
  M2.fai(() => M2.F.saltaA(2, () => M2.log.next.push('subito'), true));
  vero(JSON.stringify(M2.log.load) === '[[2,true]]', 'seconda pressione dalla coda: il brano toccato');
}

function scenarioSecondaDissolvenza(src) {
  const M = mondo(src), {env, log, DECKS} = M;
  M.fai(() => M.F.avanti());
  M.passa(8, () => {});
  vero(env.curId === 'rB' && env.MIX.act, 'in dissolvenza verso B');
  const A = env.MIX.act.out; let libero = 0; const t1 = M.ora();
  M.fai(() => M.F.avanti());
  vero(log.toast.pop() === 'Mix verso «Brano C»…', 'nuovo salto dal brano nuovo');
  vero(env.MIX.salto && !log.load.length && !log.next.length, 'aspetta il nuovo mix');
  vero(log.grid.some(g => g[0] === 'rB' && g[1] === 'i'), "a pochi secondi dall'inizio basta la misura dei primi 30 s");
  let partito = 0;
  M.passa(12, () => { if (!libero && !A.getAttribute('src')) libero = M.ora(); if (!partito && env.curId === 'rC') partito = M.ora(); });
  vero(libero && libero - t1 <= 1100, 'la dissolvenza di prima deve finire in 1 s (' + (libero - t1) + ' ms)');
  vero(partito, 'C non è partito');
  vero(env.curId === 'rC' && env.S.qi === 2 && !env.MIX.act && !log.load.length && !log.next.length, 'arrivato a C col mix');
  const dd = log.diario.filter(x => x.e === 'mix al salto');
  vero(dd.length === 2 && dd.every(x => x.esito === 'a tempo'), 'due mix al salto a tempo nel diario: ' + JSON.stringify(dd));
}

function scenarioNonPronto(src) {
  const M = mondo(src, {salvati:['rA'], streamMs:60000}), {env, log} = M, t0 = M.ora();
  M.fai(() => M.F.avanti());
  let quando = 0;
  M.passa(8, () => { if (!quando && log.load.length) quando = M.ora(); });
  vero(JSON.stringify(log.load) === '[[1,true]]', 'non pronto: cambio subito');
  vicino((quando - t0) / 1000, 6, 0.1, 'non pronto: dopo 6 s');
  vero(/^non pronto/.test(ultimo(log, 'mix al salto').esito), 'diario');
  vero(!env.MIX.salto && env.au.playbackRate === 1, 'piano annullato');
  // in streaming ma in tempo: col mix
  const M2 = mondo(src, {salvati:['rA'], streamMs:1500});
  M2.fai(() => M2.F.avanti()); M2.passa(12);
  vero(M2.env.curId === 'rB' && !M2.log.load.length && ultimo(M2.log, 'mix al salto').esito === 'a tempo', 'streaming in tempo: mix');
}

function scenarioComePrima(src) {
  const casi = [
    ['in pausa', M => { M.env.pausaVoluta = 1e12 + M.ora(); M.DECKS[0].pause(); }],
    ['mix spento', M => { M.env.MIX.on = false; }],
    ['podcast', M => { M.DECKS[0].pause(); M.env.au = M.AUX; M.AUX.src = 'blob:rA'; M.AUX.play(); M.passa(0.2); }],
    ['poco tempo', M => { M.DECKS[0].currentTime = 240 - 13; }],
    ['altro lettore rifiutato', M => { M.DECKS[1]._rifiuta = true; }],
    ['nessun brano dopo', M => { M.env.S.queue = ['rA']; }]
  ];
  for (const [nome, prep] of casi) {
    const M = mondo(src); prep(M);
    M.fai(() => M.F.avanti());
    vero(JSON.stringify(M.log.next) === '[false]' && !M.env.MIX.salto && !M.log.toast.length, nome + ': deve cambiare subito come prima');
  }
  const M = mondo(src);
  M.fai(() => M.F.saltaA(2, () => M.log.next.push('subito'), true));
  vero(M.env.MIX.salto, 'dalla coda col mix');
}

function scenarioPausa(src) {
  const M = mondo(src), {env, log, DECKS} = M;
  M.fai(() => M.F.avanti()); M.passa(1);
  env.pausaVoluta = 1e12 + M.ora(); M.fai(() => DECKS[0].pause());
  vero(JSON.stringify(log.load) === '[[1,false]]', 'pausa: il brano scelto pronto, fermo (' + JSON.stringify(log.load) + ')');
  vero(!env.MIX.unSolo && /^pausa/.test(ultimo(log, 'mix al salto').esito), 'diario');
}

// cuffie staccate, una chiamata… mentre si aspetta il salto: il brano scelto pronto e fermo; il mix resta acceso
function scenarioPausaNonChiesta(src) {
  const M = mondo(src), {env, log, DECKS} = M;
  M.fai(() => M.F.avanti()); M.passa(0.3);
  M.fai(() => DECKS[0].pause());   // fermato da solo appena l'altro lettore si carica
  vero(JSON.stringify(log.load) === '[[1,false]]', 'pausa non chiesta: brano scelto pronto, fermo (' + JSON.stringify(log.load) + ')');
  vero(env.MIX.on && env.MIX.unSolo === undefined, 'il mix resta acceso');
  vero(ultimo(log, 'mix al salto').esito === 'pausa non chiesta: brano nuovo pronto, fermo', 'diario');
}

function scenarioRifiutato(src) {
  const M = mondo(src), {env, log, DECKS} = M;
  DECKS[1].rifiuta = true;   // l'iPhone non lascia partire l'altro lettore
  M.fai(() => M.F.avanti()); M.passa(8);
  vero(log.diario.some(x => x.e === 'rifiutato'), 'diario: rifiutato');
  vero(JSON.stringify(log.load) === '[[1,true]]', 'rifiutato: cambio subito sul lettore che suona (' + JSON.stringify(log.load) + ')');
  vero(env.au === DECKS[0] && env.curId === 'rB', 'si suona B sul lettore di prima');
}

function scenarioPrecedente(src) {
  const M = mondo(src), {env, log, DECKS} = M;
  M.fai(() => M.F.avanti()); M.passa(1.5);
  M.fai(() => M.F.prev());
  vero(!env.MIX.salto && DECKS[0].playbackRate === 1 && !DECKS[1].getAttribute('src'), 'Precedente annulla il salto');
  vero(env.curId === 'rA' && DECKS[0].currentTime < 0.1 && !log.load.length, 'Precedente: il brano in corso dall\'inizio, come prima');
  M.passa(10); vero(env.curId === 'rA', 'nessun mix dopo Precedente');
}

function scenarioSpostato(src) {
  const M = mondo(src), {env, log, DECKS} = M;
  M.fai(() => M.F.avanti()); M.passa(0.2);
  DECKS[0].currentTime = 120; M.fai(() => DECKS[0].emit('seeking'));
  let maxV = 0;
  M.passa(10, () => { if (env.curId === 'rA') maxV = Math.max(maxV, DECKS[0].playbackRate); });
  vero(env.curId === 'rB' && maxV === 1, 'spostato: dissolvenza normale');
  vero(ultimo(log, 'mix al salto').esito === 'dissolvenza: brano spostato', 'diario');
  // spostato durante la rampa
  const M2 = mondo(src);
  M2.fai(() => M2.F.avanti()); M2.passa(1.5);
  vero(M2.DECKS[0].playbackRate > 1, 'in rampa');
  M2.DECKS[0].currentTime = 100; M2.fai(() => M2.DECKS[0].emit('seeking'));
  vero(M2.DECKS[0].playbackRate === 1, 'spostato in rampa: velocità normale');
  M2.passa(10); vero(M2.env.curId === 'rB', 'spostato in rampa: si arriva comunque');
}

// iPhone: i volumi passano dal mixer (curve sull'orologio audio). La dissolvenza accorciata riprogramma le curve.
function scenarioMixer(src) {
  const M = mondo(src), {env, log, DECKS} = M;
  const nodo = nome => ({gain:{value:1, cancelScheduledValues:t => log.gain.push([nome, 'cancel']), setValueAtTime:(v, t) => log.gain.push([nome, 'set', Math.round(v * 100) / 100]),
    setValueCurveAtTime:(c, t, d) => log.gain.push([nome, 'curva', d]), linearRampToValueAtTime:(v, t) => log.gain.push([nome, 'rampa', v, Math.round(t * 100) / 100])}});
  env.MIX.ctx = {get currentTime() { return M.ora() / 1000; }};
  env.MIX.gains = new Map([[DECKS[0], nodo('au')], [DECKS[1], nodo('au2')]]);
  M.fai(() => M.F.avanti()); M.passa(5);
  vero(env.MIX.act && log.gain.filter(g => g[1] === 'curva' && g[2] === 6).length === 2, 'curve di volume di MIX.XS secondi');
  const t = M.ora() / 1000; log.gain.length = 0;
  M.fai(() => M.F.avanti());
  const r = log.gain.filter(g => g[1] === 'rampa');
  vero(r.length === 2 && r.every(g => Math.abs(g[3] - (t + 1)) < 0.02) && r.some(g => g[2] === 1) && r.some(g => g[2] === 0), 'mixer: la dissolvenza in corso deve finire in 1 s ' + JSON.stringify(log.gain));
  M.passa(12); vero(env.curId === 'rC', 'mixer: arrivato a C');
}

// a 35 s dalla fine il mix a fine brano si sta già preparando: il salto lo sostituisce e lui non riparte finché si aspetta
function scenarioVicinoFine(src) {
  const M = mondo(src, {da:205}), {env, log, DECKS} = M;
  M.passa(0.6);
  vero(env.MIX.plan && log.prep.length === 1, 'il mix a fine brano dovrebbe prepararsi');
  M.fai(() => M.F.saltaA(3, () => log.next.push('subito'), true));
  vero(!env.MIX.plan && env.MIX.salto, 'il salto prende il posto del mix a fine brano');
  M.passa(12);
  vero(log.prep.every(x => !x[2]), 'il mix a fine brano non deve prepararsi durante il salto');
  vero(env.curId === 'rD' && !log.load.length && ultimo(log, 'mix al salto').esito === 'a tempo', 'arrivato a D col mix');
  // troppo vicino alla fine: cambio subito, come prima
  const M2 = mondo(src, {da:240 - 13});
  M2.fai(() => M2.F.avanti());
  vero(JSON.stringify(M2.log.next) === '[false]', 'a 13 s dalla fine: cambio subito');
}

function gradiniPuri(src) {
  const env = {MIX:{gains:new Map()}};
  const F = carica(src, ['rampaVel', 'rampaGradini', 'gradini', 'correzioneFase', 'velocita'], [], env);
  for (let q = -0.1; q <= 1.1; q += 0.01) vicino(F.rampaGradini(q, 1.07, 0), F.rampaVel(q, 1.07), 1e-12, 'senza mixer la rampa è quella di prima');
  const valori = new Set(); let cambi = 0, prima = 1;
  for (let q = 0; q <= 1.0001; q += 0.001) { const v = F.rampaGradini(q, 1.07, 4); valori.add(v); if (v !== prima) cambi++; prima = v; }
  vero(cambi === 4 && valori.size === 5, 'rampa a 4 gradini: ' + cambi + ' cambi, ' + valori.size + ' valori');
  vicino(F.rampaGradini(1, 1.07, 4), 1.07, 1e-12, 'ultimo gradino'); vicino(F.rampaGradini(0, 1.07, 4), 1, 1e-12, 'prima del primo gradino');
  vicino(F.rampaGradini(0.3, 1.07, 4), F.rampaVel(0.5, 1.07), 1e-12, 'secondo gradino');
  vero(F.gradini(4) === 0, 'senza mixer: rampa continua');
  env.MIX.gains.set(1, 1);
  vero(F.gradini(4) === 4 && F.gradini(2) === 2 && F.gradini(9) === 4 && F.gradini(0.2) === 1, 'con il mixer: un gradino al secondo, da 1 a 4');
  // correzione dei battiti
  vicino(F.correzioneFase(0.1, 0.5, false), -0.1 * 0.5 / 1.5, 1e-12, 'senza mixer come prima');
  vicino(F.correzioneFase(-2, 0.5, false), 0.05, 1e-12, 'al più +5%');
  vero(F.correzioneFase(0.07, 0.5, true) === 0 && F.correzioneFase(-0.08, 0.5, true) === 0, 'con il mixer niente correzione sotto 0,08 battito');
  vicino(F.correzioneFase(0.1, 0.5, true), -0.025, 1e-12, 'con il mixer: lo scarto si recupera in 2 s');
  vicino(F.correzioneFase(0.45, 0.5, true), -0.05, 1e-12, 'con il mixer al più −5%');
  const el = {_v:1, n:0, get playbackRate() { return this._v; }, set playbackRate(v) { this.n++; this._v = v; }};
  F.velocita(el, 1); F.velocita(el, 1.00005); vero(el.n === 0, 'velocita: nessun cambio se è già quella');
  F.velocita(el, 1.03); vero(el.n === 1 && el._v === 1.03, 'velocita: cambio');
}

// mix a fine brano dall'inizio alla fine, con il vero xfPrepare: rampa, partenza sul battito, dissolvenza agganciata
function mixerFinto(M) {
  const nodo = () => ({gain:{value:1, cancelScheduledValues() {}, setValueAtTime() {}, setValueCurveAtTime() {}, linearRampToValueAtTime() {}}});
  M.env.MIX.ctx = {state:'running', get currentTime() { return M.ora() / 1000; }};
  M.env.MIX.gains = new Map([[M.DECKS[0], nodo()], [M.DECKS[1], nodo()]]);
}
function scenarioFineBrano(src, mixer) {
  // con il mixer il brano nuovo parte 60 ms in ritardo: serve almeno una correzione dei battiti
  const M = mondo(src, {finebrano:true, da:240 - 42, latenza:mixer ? 60 : 20}), {env, log, DECKS} = M, A = DECKS[0], B = DECKS[1];
  if (mixer) mixerFinto(M);
  let partito = 0, err = [], cambiRampa = 0;
  M.passa(45, () => {
    if (!partito && env.curId === 'rB') { partito = M.ora(); cambiRampa = A.cambi.length; }
    if (partito && env.MIX.act && M.ora() - partito > 600) err.push(Math.abs(scarto(M, A, B, 'rA', 'rB')));
  });
  vero(partito, 'il mix a fine brano non è partito');
  vero(log.diario.some(x => x.e === 'mix' && x.match === true), 'mix a tempo nel diario');
  vero(!env.MIX.act && env.au === B && A.paused && !A.getAttribute('src'), 'dissolvenza finita');
  vero(err.length > 50 && Math.max.apply(null, err) < (mixer ? 0.16 : 0.05), 'battiti non agganciati nella dissolvenza: ' + Math.max.apply(null, err));
  vero(Math.max.apply(null, err.slice(-25)) < (mixer ? 0.08 : 0.03), 'battiti non agganciati a fine dissolvenza: ' + Math.max.apply(null, err.slice(-25)));
  const inDiss = A.cambi.slice(cambiRampa).filter(c => c[0] < M.ora() - 100 && c[1] !== 1);
  if (mixer) {
    vero(cambiRampa <= 4, 'con il mixer la rampa deve avere al più 4 gradini (' + cambiRampa + ')');
    vero(inDiss.length >= 1, 'con il mixer serviva una correzione dei battiti');
    for (let i = 1; i < inDiss.length; i++) vero(inDiss[i][0] - inDiss[i - 1][0] >= 1990, 'con il mixer al più una correzione ogni 2 s: ' + JSON.stringify(inDiss));
    vero(B.cambi.length === 0, 'il brano nuovo non cambia velocità');
  } else vero(cambiRampa > 20, 'senza mixer la rampa resta continua (' + cambiRampa + ')');
}

function scenarioSaltoCambi(src) {
  const M = mondo(src, {latenza:60}), {env, DECKS} = M, A = DECKS[0], B = DECKS[1];
  mixerFinto(M);
  let partito = 0, prima = 0, err = [];
  M.fai(() => M.F.avanti());
  M.passa(12, () => {
    if (!partito && env.curId === 'rB') { partito = M.ora(); prima = A.cambi.length; }
    if (partito && env.MIX.act && M.ora() - partito > 60) err.push(Math.abs(scarto(M, A, B, 'rA', 'rB')));
  });
  vero(partito && env.curId === 'rB' && !env.MIX.act, 'salto con il mixer non arrivato');
  vero(prima <= 2, 'rampa del salto con il mixer: al più 2 gradini (' + prima + ')');
  const dopo = A.cambi.slice(prima).filter(c => c[1] !== 1);
  vero(dopo.length <= 3, 'dissolvenza del salto con il mixer: al più una correzione ogni 2 s (' + JSON.stringify(dopo) + ')');
  vero(dopo.length >= 1, 'serviva una correzione dei battiti');
  vero(Math.max.apply(null, err) < 0.16 && Math.max.apply(null, err.slice(-25)) < 0.08, 'battiti agganciati anche con poche correzioni (' + Math.max.apply(null, err) + ', alla fine ' + Math.max.apply(null, err.slice(-25)) + ')');
}

// iOS prima del 27: niente mixer. La versione dallo User-Agent (bloccato a 18_6 da iOS 26) o dalle novità di Safari 27
function versioneIOS(src) {
  const F = carica(src, ['iosDa27'], [], {});
  const casi = [
    ['Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148', true, false, 'app Home su iOS 17'],
    ['Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1', true, false, 'Safari iOS 18.5'],
    ['Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.6 Mobile/15E148 Safari/604.1', true, false, 'Safari iOS 18.6'],
    ['Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 Mobile/15E148 Safari/604.1', true, false, 'Safari iOS 26'],
    ['Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.3 Mobile/15E148 Safari/604.1', true, false, 'Safari iOS 26 (18_7)'],
    ['Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/27.0 Mobile/15E148 Safari/604.1', false, true, 'Safari iOS 27'],
    ['Mozilla/5.0 (iPhone; CPU iPhone OS 26_0_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/140.0.7339.122 Mobile/15E148 Safari/604.1', true, false, 'Chrome iOS 26'],
    ['Mozilla/5.0 (iPhone; CPU iPhone OS 27_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/150.0 Mobile/15E148 Safari/604.1', false, true, 'Chrome iOS 27'],
    ['Mozilla/5.0 (iPad; CPU OS 18_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.1 Mobile/15E148 Safari/604.1', true, false, 'iPad iOS 26'],
    ['Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 Safari/605.1.15', true, false, 'iPad «desktop» 26'],
    ['Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/27.0 Safari/605.1.15', false, true, 'iPad «desktop» 27']];
  for (const [ua, vecchio, nuovo, nome] of casi) {
    vero(F.iosDa27(ua, false) === !vecchio && F.iosDa27(ua, true) === !vecchio, nome + ': la versione si legge dallo User-Agent');
    vero(!vecchio === nuovo, nome);
  }
  // app sulla schermata Home da iOS 26: nessuna versione, decidono le novità di Safari 27
  for (const ua of ['Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko)'])
    vero(F.iosDa27(ua, true) === true && F.iosDa27(ua, false) === false, 'app Home bloccata a 18_6: decidono le novità');
  vero(/function mixerInit\(\) \{\n  if \(MIX\.ctx \|\| !NEEDS_MIXER \|\| IOS_PRIMA_27 \|\|/.test(src), 'mixerInit deve restare spento prima di iOS 27');
  vero(/const IOS_PRIMA_27 = isIOS && !iosDa27\(navigator\.userAgent, novita27\(\)\);/.test(src), 'IOS_PRIMA_27');
  vero(/240405/.test(src), 'il commento con il numero del bug');
}

// «un lettore solo» tolto: una pausa non chiesta mentre si prepara il mix a fine brano va solo nel diario, e il flag
// salvato sui dispositivi si cancella una volta
function senzaUnSolo(src) {
  vero(!/unSolo/.test(src), 'MIX.unSolo è ancora nell\'app');
  vero((src.match(/am-mix-unlettore/g) || []).length === 2, 'am-mix-unlettore deve restare solo nella pulizia una tantum');
  for (const era of [true, false, null]) {
    const fatto = {via:[], diario:[]};
    const env = {ls:{get:(k, d) => k === 'am-mix-unlettore' && era !== null ? era : d}, localStorage:{removeItem:k => fatto.via.push(k)}, setTimeout:f => f(), diario:e => fatto.diario.push(e)};
    new Function('env', 'with (env) { ' + riga(src, "(function () { const era = ls.get('am-mix-unlettore'") + ' }')(env);
    vero(fatto.via.length === (era === null ? 0 : 1), 'pulizia di am-mix-unlettore (' + era + ')');
    vero(fatto.diario.length === (era ? 1 : 0) && (!era || fatto.diario[0] === 'un lettore solo: tolto'), 'diario della pulizia (' + era + ')');
  }
  // mix a fine brano: pausa da sola mentre si prepara il successivo → nel diario, il piano resta e si riprende
  const M = mondo(src, {finebrano:true, da:240 - 41}), {env, log, DECKS} = M;
  M.passa(1.2);
  vero(env.MIX.plan, 'il mix a fine brano si sta preparando');
  M.fai(() => DECKS[0].pause());
  vero(env.MIX.plan && env.MIX.on && env.MIX.unSolo === undefined && !log.load.length, 'la pausa non spegne il mix né cambia brano');
  vero(log.diario.some(x => x.e === 'pausa in preparazione'), 'la pausa va nel diario');
  M.fai(() => DECKS[0].play()); M.passa(45);
  vero(env.curId === 'rB' && log.diario.some(x => x.e === 'mix' && x.match), 'dopo la ripresa il mix a fine brano si fa');
}
