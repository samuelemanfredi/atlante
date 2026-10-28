// Prove del riquadro «Controllo remoto» (scheda Altro di musica/index.html) con JavaScriptCore, che c'è su ogni Mac:
//
//     osascript -l JavaScript test_remoto.js ../index.html
//
// Le funzioni si prendono dall'app così come sono (come in test_tutto.js) e si provano con un ambiente finto:
// - il riquadro lo vede solo Samuele (non chi è in casa, non gli invitati), nel gruppo Gestione;
// - apre Atlante Accessi (lo schermo dei Mac) sulla porta 8449 dello stesso nome Tailscale del Mac mini, in una finestra
//   a parte: niente riquadro dei moduli, niente «am-x» (all'avvio non si riapre da solo);
// - da Internet (Funnel, porta 10000), dal Mac stesso (127.0.0.1) o da un altro nome non apre niente e lo spiega;
// - gli altri moduli si aprono come prima.
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

/* ---------- estrazione dall'app (come in test_tutto.js) ---------- */
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
// una dichiarazione scritta su una riga sola, es. «const XGESTIONE = {…};»
function riga(src, inizio) {
  const i = src.indexOf('\n' + inizio);
  if (i < 0) throw new Error("nell'app manca «" + inizio + "…»");
  return src.slice(i + 1, src.indexOf('\n', i + 1));
}
function carica(src, env) {
  const nomi = ['xmodsVisibili', 'openX', 'remotoUrl', 'apriRemoto'];
  const codice = ['const ACCESSI_PORTA', 'const XREMOTO', 'const XGESTIONE'].map(r => riga(src, r))
    .concat(nomi.map(n => estrai(src, n))).join('\n');
  const f = new Function('env', 'with (env) { return (function () {\n' + codice + '\nreturn {' + nomi.join(',') + '};\n})(); }');
  return f(env);
}

const TAILNET = 'mac-mini-di-samuele.tail1b5bf3.ts.net';
// l'ambiente dell'app: chi è (api/extra/io), da quale indirizzo è aperta, il riquadro dei moduli e localStorage
function ambiente(ruolo, loc) {
  const salvati = {}, xf = {src: null}, xp = {hidden: true};
  const env = {
    IO: {ruolo, persona: null}, salvati, xf, xp, aperti: [], avvisi: [], xCur: '',
    XMODS: [{g: 'Ascolta', m: [{k: 'cerca', t: 'Cerca e scarica musica', d: '', i: '🔎', c: '#fa2d48'}]}],
    XINVITATI: [{g: 'Per te', m: [{k: 'cerca', t: 'Cerca e scarica musica', d: '', i: '🔎', c: '#fa2d48'}]}],
    location: Object.assign({protocol: 'https:', hostname: TAILNET, port: '8448'}, loc || {}),
    window: {open: (u, t, f) => { env.aperti.push([u, t, f]); return null; }},
    document: {body: {classList: {toggle: () => {}}}},
    $: id => ({xf: {getAttribute: () => xf.src, setAttribute: (n, v) => { xf.src = v; }}, xp}[id]),
    ls: {get: (k, d) => (k in salvati ? salvati[k] : d), set: (k, v) => { salvati[k] = v; }},
    toast: m => { env.avvisi.push(m); }, closeNP: () => {}, uiApri: () => {},
  };
  return env;
}

function prove(src, fatte) {
  const eq = (a, b, m) => { const x = JSON.stringify(a), y = JSON.stringify(b); if (x !== y) throw new Error(m + ': ' + x + ' invece di ' + y); };
  const chiavi = F => F.xmodsVisibili().flatMap(g => g.m).map(m => m.k);

  /* ---------- l'indirizzo di Atlante Accessi: lo stesso nome Tailscale, porta 8449, solo dentro la rete ---------- */
  const F0 = carica(src, ambiente('proprietario'));
  eq(F0.remotoUrl({protocol: 'https:', hostname: TAILNET, port: '8448'}), 'https://' + TAILNET + ':8449/', 'dalla rete Tailscale');
  eq(F0.remotoUrl({protocol: 'https:', hostname: TAILNET.toUpperCase(), port: '8448'}), 'https://' + TAILNET.toUpperCase() + ':8449/', 'il nome con le maiuscole');
  eq(F0.remotoUrl({protocol: 'https:', hostname: TAILNET, port: '10000'}), '', 'da Internet (Funnel) Accessi non si raggiunge');
  eq(F0.remotoUrl({protocol: 'http:', hostname: '127.0.0.1', port: '8790'}), '', 'dal Mac stesso');
  eq(F0.remotoUrl({protocol: 'https:', hostname: 'atlante.example.com', port: ''}), '', 'un altro nome');
  eq(F0.remotoUrl({protocol: 'https:', hostname: TAILNET + '.example.com', port: ''}), '', 'un nome che contiene ts.net ma non finisce così');
  eq(F0.remotoUrl({protocol: 'http:', hostname: TAILNET, port: '8448'}), '', 'senza https');
  fatte.push('indirizzo');

  /* ---------- chi vede il riquadro ---------- */
  eq(chiavi(carica(src, ambiente('proprietario'))).indexOf('remoto') >= 0, true, 'Samuele vede «Controllo remoto»');
  const gest = carica(src, ambiente('proprietario')).xmodsVisibili().find(g => g.m.some(m => m.k === 'remoto'));
  eq(gest.g, 'Gestione', 'nel gruppo Gestione');
  eq(chiavi(carica(src, ambiente(''))).indexOf('remoto'), -1, 'chi è in casa non lo vede');
  eq(chiavi(carica(src, ambiente('invitato'))).indexOf('remoto'), -1, 'un invitato non lo vede');
  fatte.push('chi lo vede');

  /* ---------- il tocco: Accessi in una finestra a parte, il riquadro dei moduli resta chiuso ---------- */
  let env = ambiente('proprietario'), F = carica(src, env);
  F.openX('remoto');
  eq(env.aperti, [['https://' + TAILNET + ':8449/', '_blank', 'noopener']], 'si apre Atlante Accessi');
  eq([env.xf.src, env.xp.hidden, 'am-x' in env.salvati], [null, true, false], 'senza il riquadro dei moduli e senza ricordarlo per il prossimo avvio');
  eq(env.avvisi, [], 'senza avvisi');
  fatte.push('tocco');

  /* ---------- da Internet: niente finestra, un avviso che spiega ---------- */
  env = ambiente('proprietario', {port: '10000'}); F = carica(src, env);
  F.openX('remoto');
  eq(env.aperti, [], 'da Internet non si apre');
  eq(env.avvisi.length, 1, 'un avviso');
  eq(/Tailscale/.test(env.avvisi[0]), true, "l'avviso dice di accendere Tailscale: " + env.avvisi[0]);
  fatte.push('da Internet');

  /* ---------- un invitato con il link ?x=remoto: niente ---------- */
  env = ambiente('invitato'); F = carica(src, env);
  F.openX('remoto');
  eq([env.aperti, env.avvisi], [[], []], 'un invitato non apre niente');
  fatte.push('invitato');

  /* ---------- gli altri moduli come prima ---------- */
  env = ambiente('proprietario'); F = carica(src, env);
  F.openX('cerca');
  eq([env.xf.src, env.xp.hidden, env.salvati['am-x'], env.aperti], ['x/cerca.html', false, 'cerca', []], '«Cerca e scarica» nel riquadro');
  fatte.push('altri moduli');
}
