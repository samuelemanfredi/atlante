// Prove di «Condividi l'app» (x/inviti.html, musica/index.html, sw.js) con JavaScriptCore, che c'è su ogni Mac:
//
//     osascript -l JavaScript test_condividi.js ..
//
// Le funzioni si prendono dai file così come sono (come in test_remoto.js) e si provano con un ambiente finto:
// - chi vede «Condividi l'app»: Samuele in cima ad Altro (e nella barra dell'iPad), gli invitati nel loro gruppo, non chi
//   è in casa; il tasto in Libreria solo per chi può invitare;
// - Samuele crea il link subito: QR, Condividi e messaggio con il link, «Entra» e la schermata Home; nomi e link dal Mac
//   protetti; un errore del Mac (manca l'indirizzo pubblico) si vede nella pagina;
// - «Non ancora entrati»: solo chi non ha dispositivi, i più recenti prima, «Nuovo link»;
// - all'avvio la pagina giusta per Samuele, per un invitato, per chi è in casa e senza Mac;
// - sw.js: copia offline nuova (le app già installate la rinnovano) con la pagina degli inviti.
// Scrive «OK …» o «PROVA FALLITA: …» (test_extra.py lo controlla).
ObjC.import('Foundation');

function run(argv) {
  const fatte = [];
  try {
    const dir = argv[0] || '..';
    prove(leggi(dir + '/index.html'), leggi(dir + '/x/inviti.html'), leggi(dir + '/sw.js'), fatte);
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

/* ---------- estrazione dai file (come in test_remoto.js) ---------- */
function estrai(src, nome) {
  const m = new RegExp('\\n(async )?function ' + nome + '\\(').exec(src);
  if (!m) throw new Error('manca la funzione ' + nome);
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
  if (i < 0) throw new Error('manca «' + inizio + '…»');
  return src.slice(i + 1, src.indexOf('\n', i + 1));
}
// JavaScriptCore qui non fa girare le promesse da solo: le funzioni «async» diventano sincrone e le finte rispondono subito
function sincrona(f) { return f.replace(/^async function/, 'function').replace(/\bawait /g, ''); }
function carica(src, righe, nomi, env) {
  const codice = righe.map(r => riga(src, r)).concat(nomi.map(n => sincrona(estrai(src, n)))).join('\n');
  const f = new Function('env', 'with (env) { return (function () {\n' + codice + '\nreturn {' + nomi.join(',') + '};\n})(); }');
  return f(env);
}
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

// la pagina finta: elementi con hidden, innerHTML, value…; querySelector dà un elemento nuovo (canvas, tasti)
function pagina() {
  const els = {};
  const el = id => els[id] || (els[id] = {id, hidden: true, innerHTML: '', textContent: '', value: '', disabled: false, sotto: [],
    focus() { this.fuoco = true; }, blur() {}, scrollIntoView() {},
    querySelector(sel) { const x = {sel, onclick: null}; this.sotto.push(x); return x; },
    querySelectorAll(sel) { const m = /^\[data-([a-z]+)\]$/.exec(sel); if (!m) return [];
      const re = new RegExp('data-' + m[1] + '="([^"]*)"', 'g'), out = []; let r;
      while ((r = re.exec(this.innerHTML))) out.push({dataset: {[m[1]]: r[1]}, disabled: false, onclick: null});
      this.tasti = out; return out; }});
  return {els, $: el};
}
const X = '<img src=x onerror=alert(1)>';
function pulito(h, m) { if (/<img|<svg/i.test(h)) throw new Error(m + ': ' + h.slice(0, 300)); }

function prove(app, inv, sw, fatte) {
  const eq = (a, b, m) => { const x = JSON.stringify(a), y = JSON.stringify(b); if (x !== y) throw new Error(m + ': ' + x + ' invece di ' + y); };
  const vero = (c, m) => { if (!c) throw new Error(m); };

  /* ---------- chi vede «Condividi l'app» (index.html) ---------- */
  const XMODS = [{g: 'Ascolta', m: [{k: 'podcast', t: 'Podcast', d: '', i: '', c: ''}]}];
  const XINV = [{g: 'Per te', m: [{k: 'inviti', t: 'Condividi l\'app', d: '', i: '', c: ''}]}];
  const conRuolo = ruolo => carica(app, ['const XREMOTO', 'const XGESTIONE', 'const XCONDIVIDI', 'const puoInvitare'], ['xmodsVisibili'],
    {IO: {ruolo}, XMODS, XINVITATI: XINV});
  const G = conRuolo('proprietario').xmodsVisibili();
  eq([G[0].g, G[0].m.map(m => m.k)], ['Condividi', ['inviti']], 'Samuele: «Condividi l\'app» in cima ad Altro');
  vero(/Condividi l'app/.test(G[0].m[0].t), 'il titolo è «Condividi l\'app»: ' + G[0].m[0].t);
  eq(conRuolo('').xmodsVisibili().flatMap(g => g.m).some(m => m.k === 'inviti'), false, 'chi è in casa non la vede');
  const env = {IO: {ruolo: ''}};
  const pi = new Function('env', 'with (env) { ' + riga(app, 'const puoInvitare') + ' return puoInvitare; }')(env);
  eq(['proprietario', 'invitato', 'casa', '', 'nessuno'].map(r => { env.IO.ruolo = r; return pi(); }), [true, true, false, false, false], 'chi può invitare');
  const xinv = app.slice(app.indexOf('\nconst XINVITATI'), app.indexOf('];', app.indexOf('\nconst XINVITATI')));   // scritta su due righe
  vero(/\{k:'inviti', t:'Condividi l\\'app'/.test(xinv), 'gli invitati: la stessa voce «Condividi l\'app»');
  vero(/puoInvitare\(\) \? '<button class="round" data-a="condividi"/.test(app), 'il tasto in Libreria solo per chi può invitare');
  vero(/case 'condividi':[^\n]*openX\('inviti'\)/.test(app), 'il tasto apre «Condividi l\'app»');
  fatte.push('chi la vede');

  /* ---------- il messaggio da mandare ---------- */
  const M = carica(inv, ['const giorno'], ['messaggio'], {});
  const link = 'https://mac.esempio.ts.net:10000/accesso/invito/abcDEF123', scade = 1900000000;
  const ms = M.messaggio('Anna', link, scade, true);
  vero(ms.indexOf(link) >= 0 && /Anna/.test(ms), 'il messaggio ha nome e link: ' + ms);
  vero(/«Entra»/.test(ms) && /Aggiungi alla schermata Home/.test(ms) && /Android/.test(ms), 'il messaggio spiega come entrare e installare: ' + ms);
  vero(/\d/.test(ms.split(link)[1]), 'il messaggio dice fino a quando vale');
  vero(/Samuele/.test(M.messaggio('Bea', link, scade, false)) && !/la mia app/.test(M.messaggio('Bea', link, scade, false)), 'da un invitato: «di Samuele»');
  fatte.push('messaggio');

  /* ---------- Samuele crea il link: QR, Condividi, nomi e link protetti ---------- */
  let P = pagina(), chiamate = [], qr = [], condivisi = [];
  const nomi = ['messaggio', 'mostraCarta', 'erroreCarta', 'disegnaMiei', 'crea', 'richiesteInAttesa', 'fermo', 'avvio'];
  const ambiente = (risposte) => ({
    $: P.$, esc, toast: () => {}, disegnaQR: (cv, t) => qr.push(t), condividi: t => condivisi.push(t), copia: () => {}, disegna: () => { P.invitato = true; },
    api: (path, opt) => { chiamate.push([path, opt && opt.method || 'GET', opt && opt.body]); const r = risposte[path]; if (r instanceof Error) throw r; if (r === undefined) throw new Error('richiesta inattesa ' + path); return typeof r === 'function' ? r(opt) : r; },
  });
  let R = {
    inviti: {persona: {id: 'p1', nome: 'Anna ' + X}, invito: {link: link + '"><img src=x onerror=alert(2)>', scade}},
    'gestione/persone': {persone: [
      {id: 'v', nome: 'Vecchio', creato: 1000, dispositivi: []},
      {id: 'd', nome: 'Dentro', creato: 2000, dispositivi: [{id: 'x', visto: 5}]},
      {id: 'n"><b', nome: 'Nuovo ' + X, creato: 3000, dispositivi: [], da_nome: 'Bea'}]},
  };
  let F = carica(inv, ['const giorno'], nomi, ambiente(R));
  P.$('mnome').value = '  Anna  ';
  F.crea();
  eq(chiamate[0], ['inviti', 'POST', {nome: 'Anna'}], 'crea il link subito, col nome pulito');
  const carta = P.$('carta');
  eq(carta.hidden, false, 'il link si vede');
  pulito(carta.innerHTML, 'carta: nome o link non protetti');
  vero(carta.innerHTML.indexOf('&lt;img') >= 0, 'il nome si vede (protetto)');
  vero(/<canvas/.test(carta.innerHTML), 'c\'è il codice QR');
  eq(qr, [link + '"><img src=x onerror=alert(2)>'], 'il QR è del link');
  const share = carta.sotto.find(x => x.sel === '[data-k="share"]');
  vero(share && typeof share.onclick === 'function', 'il tasto Condividi');
  share.onclick();
  vero(condivisi.length === 1 && condivisi[0].indexOf(link) >= 0 && /schermata Home/.test(condivisi[0]), 'Condividi manda il messaggio con il link');
  eq(P.$('mnome').value, '', 'il campo si svuota');
  fatte.push('link subito');

  /* ---------- «Non ancora entrati» ---------- */
  const al = P.$('alist').innerHTML;
  eq(P.$('attesa').hidden, false, 'la lista si vede');
  pulito(al, 'non ancora entrati: nomi non protetti');
  vero(al.indexOf('Dentro') < 0, 'chi ha un dispositivo non c\'è');
  vero(al.indexOf('Nuovo') >= 0 && al.indexOf('Vecchio') > al.indexOf('Nuovo'), 'i più recenti prima');
  vero(/lo ha chiesto Bea/.test(al), 'chi lo ha chiesto');
  vero(al.indexOf('data-nl="n&quot;&gt;&lt;b"') >= 0, 'l\'id dal Mac protetto: ' + al);
  vero(/1 persona è già dentro/.test(P.$('mnota').innerHTML), 'quante persone sono già dentro');
  // «Nuovo link»
  R['gestione/persone/v/invito'] = {invito: {link: link + '2', scade}};
  const tasto = P.$('alist').tasti.find(t => t.dataset.nl === 'v');
  qr = []; tasto.onclick();
  eq(qr, [link + '2'], '«Nuovo link» mostra il link nuovo col QR');
  vero(chiamate.some(c => c[0] === 'gestione/persone/v/invito' && c[1] === 'POST'), '«Nuovo link» lo chiede al Mac');
  fatte.push('non ancora entrati');

  /* ---------- errore del Mac: si vede nella pagina ---------- */
  P = pagina(); chiamate = [];
  F = carica(inv, ['const giorno'], nomi, ambiente({inviti: new Error('Manca l\'indirizzo pubblico ' + X), 'gestione/persone': {persone: []}}));
  P.$('mnome').value = 'Ugo'; F.crea();
  vero(/non è stato creato/.test(P.$('carta').innerHTML) && /indirizzo pubblico/.test(P.$('carta').innerHTML), 'l\'errore si vede');
  pulito(P.$('carta').innerHTML, 'errore non protetto');
  eq(P.$('crea').disabled, false, 'il tasto torna attivo');
  P.$('mnome').value = '  '; chiamate = []; F.crea();
  eq([chiamate.length, P.$('mnome').fuoco], [0, true], 'senza nome non chiede niente');
  fatte.push('errore');

  /* ---------- all'avvio: la pagina giusta per chi la apre ---------- */
  const avvia = io => { P = pagina(); chiamate = []; const f = carica(inv, ['const giorno'], nomi, ambiente({io, 'gestione/persone': {persone: []}})); f.avvio(); return P; };
  let p = avvia({ruolo: 'proprietario', nuove: {richieste: 2}});
  eq([p.$('sezMio').hidden, p.$('sezChiedi').hidden, p.$('fermo').hidden], [false, true, true], 'Samuele: crea i link');
  vero(!p.$('mRichieste').hidden && /2 richieste/.test(p.$('mRichieste').innerHTML), 'Samuele: le richieste degli invitati');
  p = avvia({ruolo: 'invitato'});
  eq([p.$('sezMio').hidden, p.$('sezChiedi').hidden, !!p.invitato], [true, false, true], 'un invitato: chiede a Samuele');
  p = avvia({ruolo: 'casa'});
  eq([p.$('sezMio').hidden, p.$('sezChiedi').hidden, p.$('fermo').hidden], [true, true, false], 'chi è in casa: lo spiega');
  p = avvia(new Error('Il Mac non risponde'));
  vero(!p.$('fermo').hidden && /non risponde/.test(p.$('fermo').innerHTML), 'senza Mac: lo spiega');
  fatte.push('avvio');

  /* ---------- sw.js: copia nuova con la pagina degli inviti ---------- */
  const cache = /const CACHE = '([^']+)'/.exec(sw);
  vero(cache && cache[1] !== 'atlante-musica-v5' && /^atlante-musica-v\d+$/.test(cache[1]), 'una copia offline nuova (le app già installate la rinnovano): ' + (cache && cache[1]));
  vero(/'inviti'\]\.map\(m => 'x\/' \+ m \+ '\.html'\)/.test(sw), 'la pagina degli inviti nella copia offline');
  fatte.push('copia offline');
}
