// Prove delle notifiche della chat e delle chiamate nel service worker (musica/sw.js) con JavaScriptCore, che c'è su ogni Mac:
//
//     osascript -l JavaScript test_notifiche.js ../sw.js
//
// Le funzioni si prendono dal service worker così come sono (come in test_tutto.js) e si provano con un telefono finto:
// le notifiche sullo schermo (self.registration.showNotification e getNotifications), la risposta del Mac mini a
// chat/avvisi (fetch), la cache e le finestre dell'app (clients). Si prova che: arrivano «Chiamata da…»,
// «Videochiamata da…» e «Chiamata persa da…» con l'etichetta «chiamata-<id>»; «persa» prende il posto di «arriva»;
// «chiusa» toglie la notifica; ogni push mostra una notifica (Safari lo vuole), ma mai un «Nuovo messaggio» vuoto;
// una chiamata persa tolta a mano non torna al messaggio dopo; il tocco apre l'app (chiamate) o la chat (messaggi).
// Scrive «OK …» o «PROVA FALLITA: …» (test_extra.py lo controlla).
ObjC.import('Foundation');

function run(argv) {
  const fatte = [];
  try {
    prove(leggi(argv[0] || '../sw.js'), fatte);
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

/* ---------- estrazione delle funzioni dal service worker (come in test_tutto.js) ---------- */
function estrai(src, nome) {
  const m = new RegExp('\\n(async )?function ' + nome + '\\(').exec(src);
  if (!m) throw new Error('nel service worker manca la funzione ' + nome);
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
  if (i < 0) throw new Error('nel service worker manca la riga «' + inizio + '…»');
  return src.slice(i + 1, src.indexOf('\n', i + 1));
}
// JavaScriptCore qui non fa girare le promesse da solo: le funzioni «async» diventano sincrone (via «await»), e le
// finte (fetch, showNotification, caches…) rispondono subito, come in test_tutto.js
function sincrona(f) { return f.replace(/^async function/, 'function').replace(/\bawait /g, ''); }
function carica(src, nomi, righe, env) {
  const codice = righe.map(r => riga(src, r)).join('\n') + '\n' + nomi.map(n => sincrona(estrai(src, n))).join('\n');
  const f = new Function('env', 'with (env) { return (function () {\n' + codice + '\nreturn {' + nomi.join(',') + '};\n})(); }');
  return f(env);
}

/* ---------- il telefono finto ---------- */
// JavaScriptCore non ha URL né Response: bastano queste (indirizzi assoluti, o relativi alla cartella di `base`)
function FintoURL(u, base) {
  u = String(u && u.href || u);
  let h = u;
  if (!/^[a-z]+:\/\//.test(u)) {
    const b = String(base && base.href || base || '');
    h = u.charAt(0) === '/' ? /^[a-z]+:\/\/[^/]+/.exec(b)[0] + u : b.slice(0, b.split('?')[0].lastIndexOf('/') + 1) + u.replace(/^\.\//, '');
  }
  const m = /^([a-z]+:\/\/[^/]+)([^?#]*)/.exec(h);
  this.href = h; this.origin = m[1]; this.pathname = m[2] || '/';
}
FintoURL.prototype.toString = function () { return this.href; };
function FintoResponse(corpo) { this.corpo = String(corpo); this.ok = true; }
FintoResponse.prototype.json = function () { return JSON.parse(this.corpo); };

// `env.risposta`: quello che il Mac mini risponde a chat/avvisi (null = non risponde). Lo schermo (env.schermo) e la
// cache restano da un push all'altro, come sul telefono vero.
function telefono() {
  const cache = {};
  const env = {
    risposta: {avvisi:[], chiamate:[]}, disp: 'd1', chieste: [], mostrate: [], schermo: [], finestre: [], aperte: [],
    chatDispositivo: () => env.disp,
    fetch: (u) => {
      env.chieste.push(String(u));
      if (env.risposta === null) throw new Error('il Mac mini non risponde');
      if (env.risposta === 500) return {ok:false, json: () => ({})};
      const j = JSON.stringify(env.risposta);
      return {ok:true, json: () => JSON.parse(j)};
    },
    URL: FintoURL, Response: FintoResponse,
    caches: {open: nome => ({
      match: k => (nome + ' ' + k in cache ? new FintoResponse(cache[nome + ' ' + k]) : undefined),
      put: (k, r) => { cache[nome + ' ' + k] = r.corpo; }
    })},
    self: {
      registration: {
        scope: 'https://mac.example/musica/',
        // stessa etichetta = la notifica nuova prende il posto di quella vecchia (come nei browser)
        showNotification: (titolo, o) => {
          o = o || {};
          const n = {titolo, body:o.body || '', tag:o.tag || '', data:o.data, close: () => { env.schermo = env.schermo.filter(x => x !== n); }};
          env.mostrate.push(n);
          env.schermo = env.schermo.filter(x => !o.tag || x.tag !== o.tag).concat([n]);
        },
        getNotifications: f => env.schermo.filter(x => !f || !f.tag || x.tag === f.tag)
      },
      clients: {
        matchAll: () => env.finestre,
        openWindow: u => { env.aperte.push(String(u)); }
      }
    }
  };
  env.push = () => {   // un push: quante notifiche si sono mostrate (Safari ne vuole almeno una)
    const prima = env.mostrate.length;
    carica(env.src, ['chatNotifiche'].concat(env.altre), env.righe, env).chatNotifiche();
    return env.mostrate.slice(prima);
  };
  return env;
}
function finestra(url) {
  const w = {url, frameType:'top-level', messaggi:[], fuoco:0};
  w.postMessage = m => { w.messaggi.push(m); };
  w.focus = () => { w.fuoco++; };
  return w;
}

function prove(src, fatte) {
  const eq = (a, b, m) => { const x = JSON.stringify(a), y = JSON.stringify(b); if (x !== y) throw new Error(m + ': ' + x + ' invece di ' + y); };
  // le funzioni del service worker che chatNotifiche usa (oltre a sé), e le costanti che servono
  const altre = ['chiamateViste', 'segnaViste'].filter(n => new RegExp('\\nasync function ' + n + '\\(|\\nfunction ' + n + '\\(').test(src));
  const righe = src.indexOf('\nconst VISTE') >= 0 ? ['const VISTE'] : [];
  const nuovo = () => { const t = telefono(); t.src = src; t.altre = altre; t.righe = righe; return t; };
  const sullo = t => t.schermo.map(n => [n.tag, n.titolo]);
  const anna = (id, evento, video, t) => ({id, evento, nome:'Anna', video:!!video, t});
  // mai un «Nuovo messaggio» senza chi l'ha scritto
  const maiVuoto = (t, m) => t.mostrate.forEach(n => {
    if (/Nuovo messaggio/.test(n.titolo + ' ' + n.body) && !/^Nuovo messaggio da \S/.test(n.titolo)) throw new Error(m + ': «' + n.titolo + ' / ' + n.body + '»');
  });

  /* ---------- i messaggi: come prima ---------- */
  let t = nuovo();
  t.risposta = {avvisi:[{conv:'c1', da:'a', nome:'Anna', gruppo:null, n:2, t:10}, {conv:'g1', da:'b', nome:'Bruno', gruppo:'Calcetto', n:1, t:9}], chiamate:[]};
  let m = t.push();
  eq(t.chieste, ['https://mac.example/musica/api/extra/chat/avvisi?disp=d1'], 'si chiede al Mac mini chi ha scritto');
  eq(m.map(n => [n.tag, n.titolo, n.body]), [['chat-c1', 'Nuovo messaggio da Anna', '2 messaggi nuovi'], ['chat-g1', 'Nuovo messaggio da Bruno', 'Nel gruppo «Calcetto»']], 'messaggi');
  eq(m[0].data, {conv:'c1'}, 'il tocco apre la conversazione');
  fatte.push('messaggi');

  /* ---------- chiamata in arrivo, voce e video: niente «Nuovo messaggio» ---------- */
  t = nuovo();
  t.risposta = {avvisi:[], chiamate:[anna('k1', 'arriva', false, 100)]};
  m = t.push();
  eq(m.map(n => [n.tag, n.titolo]), [['chiamata-k1', 'Chiamata da Anna']], 'chiamata in arrivo');
  eq(sullo(t), [['chiamata-k1', 'Chiamata da Anna']], 'sullo schermo la chiamata');
  eq(m[0].data, {chiamata:'k1'}, 'la notifica sa di che chiamata è');
  maiVuoto(t, 'con una chiamata non si mostra un «Nuovo messaggio» vuoto');
  t.risposta = {avvisi:[], chiamate:[{id:'k2', evento:'arriva', nome:'Bruno', video:true, t:120}, anna('k1', 'arriva', false, 100)]};
  m = t.push();
  eq(m.map(n => [n.tag, n.titolo]), [['chiamata-k2', 'Videochiamata da Bruno']], 'videochiamata (e quella di Anna non si ripete)');
  maiVuoto(t, 'videochiamata');
  fatte.push('chiamata e videochiamata');

  /* ---------- «persa» prende il posto di «arriva» (stessa etichetta) ---------- */
  t = nuovo();
  t.risposta = {avvisi:[], chiamate:[anna('k1', 'arriva', true, 100)]};
  t.push();
  t.risposta = {avvisi:[], chiamate:[anna('k1', 'persa', true, 140)]};
  m = t.push();
  eq(m.map(n => [n.tag, n.titolo]), [['chiamata-k1', 'Chiamata persa da Anna']], 'chiamata persa');
  eq(sullo(t), [['chiamata-k1', 'Chiamata persa da Anna']], 'una notifica sola per la chiamata');
  maiVuoto(t, 'chiamata persa');
  fatte.push('persa');

  /* ---------- «chiusa» (risposto o rifiutato, anche altrove): via la notifica; Safari vuole lo stesso una notifica ---------- */
  t = nuovo();
  t.risposta = {avvisi:[], chiamate:[anna('k3', 'arriva', false, 100)]};
  t.push();
  t.risposta = {avvisi:[], chiamate:[anna('k3', 'chiusa', false, 105)]};
  m = t.push();
  eq(sullo(t), [], 'la chiamata chiusa non resta sullo schermo');
  if (!m.length) throw new Error('un push senza nessuna notifica: Safari dopo tre smette di mandarle');
  eq(m.map(n => n.tag), ['chiamata-k3'], 'la notifica per Safari ha la stessa etichetta (prende il posto di quella che squillava)');
  maiVuoto(t, 'chiamata chiusa');
  // chiusa e un messaggio nello stesso push: il messaggio basta a Safari, la chiamata si toglie
  t = nuovo();
  t.risposta = {avvisi:[], chiamate:[anna('k4', 'arriva', false, 100)]};
  t.push();
  t.risposta = {avvisi:[{conv:'c1', da:'b', nome:'Bruno', gruppo:null, n:1, t:106}], chiamate:[anna('k4', 'chiusa', false, 105)]};
  m = t.push();
  eq(m.map(n => n.titolo), ['Nuovo messaggio da Bruno'], 'con un messaggio si mostra solo quello');
  eq(sullo(t), [['chat-c1', 'Nuovo messaggio da Bruno']], 'e la chiamata non c\'è più');
  fatte.push('chiusa');

  /* ---------- il Mac mini risponde ma non c'è niente di nuovo: niente sullo schermo, ma una notifica per Safari ---------- */
  t = nuovo();
  t.risposta = {avvisi:[], chiamate:[]};
  m = t.push();
  if (!m.length) throw new Error('niente di nuovo: serve lo stesso una notifica (Safari)');
  eq(sullo(t), [], 'niente di nuovo: niente resta sullo schermo');
  maiVuoto(t, 'niente di nuovo');
  fatte.push('niente di nuovo');

  /* ---------- il Mac mini non risponde (o la chat qui non c'è): una notifica generica, che non dice «Nuovo messaggio» ---------- */
  for (const [nome, prepara] of [['non risponde', x => { x.risposta = null; }], ['errore', x => { x.risposta = 500; }], ['senza chat', x => { x.disp = ''; }]]) {
    t = nuovo();
    prepara(t);
    m = t.push();
    eq(m.length, 1, 'Mac mini ' + nome + ': una notifica');
    eq([m[0].tag, m[0].titolo, m[0].data], ['chat', 'Atlante Musica', {conv:''}], 'Mac mini ' + nome + ': generica, il tocco apre la chat');
    eq(sullo(t).length, 1, 'Mac mini ' + nome + ': resta sullo schermo');
    maiVuoto(t, 'Mac mini ' + nome);
  }
  fatte.push('Mac mini muto');

  /* ---------- una chiamata persa tolta a mano non torna con il messaggio dopo ---------- */
  t = nuovo();
  t.risposta = {avvisi:[], chiamate:[anna('k5', 'persa', false, 200)]};
  t.push();
  t.schermo[0].close();   // Samuele la toglie
  t.risposta = {avvisi:[{conv:'c1', da:'a', nome:'Anna', gruppo:null, n:1, t:300}], chiamate:[anna('k5', 'persa', false, 200)]};
  m = t.push();
  eq(m.map(n => n.titolo), ['Nuovo messaggio da Anna'], 'la chiamata persa già vista non si mostra di nuovo');
  // la stessa chiamata che squilla di nuovo (aggiunto di nuovo in un gruppo): è un avviso nuovo, si mostra
  t.risposta = {avvisi:[], chiamate:[anna('k5', 'arriva', false, 400)]};
  m = t.push();
  eq(m.map(n => n.titolo), ['Chiamata da Anna'], 'squilla di nuovo: si mostra');
  fatte.push('persa tolta a mano');

  /* ---------- il tocco: la chiamata apre l'app (compare da sola), il messaggio la chat ---------- */
  const F = carica(src, ['apriNotifica'], [], t = nuovo());
  F.apriNotifica({chiamata:'k1'});
  eq(t.aperte, ['https://mac.example/musica/'], 'chiamata ad app chiusa: si apre ./');
  F.apriNotifica({conv:'c1'});
  eq(t.aperte[1], 'https://mac.example/musica/?x=chat&c=c1', 'messaggio ad app chiusa: si apre la conversazione');
  const w = finestra('https://mac.example/musica/?x=chat');
  t.finestre = [w];
  F.apriNotifica({chiamata:'k1'});
  eq([w.fuoco, w.messaggi], [1, []], 'chiamata ad app aperta: solo in primo piano, non si apre la chat');
  F.apriNotifica({conv:'c1'});
  eq([w.fuoco, w.messaggi], [2, [{tipo:'apri-chat', conv:'c1'}]], 'messaggio ad app aperta: la chat');
  eq(t.aperte.length, 2, 'con l\'app aperta non si apre un\'altra finestra');
  fatte.push('tocco');
}
