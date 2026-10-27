// Prove della cifratura della chat (x/chat-cifra.js) con la WebCrypto vera di Deno, la stessa dei browser.
// Le lancia test_extra.py (TestChatCifra) se Deno c'è:
//   deno run --allow-read=… test_chat_cifra.js prove
//   deno run --allow-read=… --allow-net=127.0.0.1 test_chat_cifra.js server <indirizzo> <json con le intestazioni>
//   deno run test_chat_cifra.js vapid <jwt> <chiave pubblica base64url>
// Scrive una riga JSON: {"ok": true, ...} o {"ok": false, "errore": "..."}.
await import(new URL('../x/chat-cifra.js', import.meta.url).href);
const C = globalThis.ChatCifra;
const te = new TextEncoder();
const prove = [];
const prova = (nome, fn) => prove.push([nome, fn]);
function vero(c, m) { if (!c) throw new Error(m || 'falso'); }
async function fallisce(p, m) {
  try { await p; } catch (e) { return; }
  throw new Error('doveva fallire: ' + m);
}
async function disp(persona) {
  const k = await C.nuoveChiavi();
  return {persona, disp:C.nuovoId(), privata:k.privata, pubblica:k.pubblica};
}
const per = d => ({id:d.disp, chiave:d.pubblica});
const ricevuto = (spedito, chiave, da) => ({id:spedito.id, conv:spedito.conv, da:da.persona, disp:da.disp, corpo:spedito.corpo, sale:spedito.sale, chiave});

prova('la chiave privata non esce dal browser', async () => {
  const a = await disp('p');
  vero(a.privata.extractable === false, 'privata esportabile');
  for (const f of ['pkcs8', 'jwk', 'raw']) await fallisce(crypto.subtle.exportKey(f, a.privata), 'export ' + f);
  vero(C.deb64u(a.pubblica).length === 65 && C.deb64u(a.pubblica)[0] === 4, 'pubblica non P-256 non compressa');
});

prova('messaggio per più dispositivi, ognuno lo apre', async () => {
  const a1 = await disp('aaaaaaaaaaaaaaaa'), a2 = await disp('aaaaaaaaaaaaaaaa'), b1 = await disp('bbbbbbbbbbbbbbbb'), b2 = await disp('bbbbbbbbbbbbbbbb');
  const contenuto = {tipo:'testo', testo:'Ciao Bruno, è cifrato 🙂'};
  const m = await C.cifra(a1, 'd0123456789abcdef01234567', C.nuovoId(), contenuto, [per(a2), per(b1), per(b2)]);
  vero(Object.keys(m.chiavi).length === 3 && !m.chiavi[a1.disp], 'chiavi dei destinatari');
  for (const r of [a2, b1, b2]) {
    const x = await C.decifra(r, ricevuto(m, m.chiavi[r.disp], a1), a1.pubblica);
    vero(JSON.stringify(x) === JSON.stringify(contenuto), 'contenuto diverso per ' + r.disp);
  }
  vero(!JSON.stringify(m).includes('Bruno'), 'testo in chiaro nel messaggio');
});

prova('chi non è destinatario non apre, e niente si può cambiare', async () => {
  const a = await disp('aaaaaaaaaaaaaaaa'), b = await disp('bbbbbbbbbbbbbbbb'), b2 = await disp('bbbbbbbbbbbbbbbb'), c = await disp('cccccccccccccccc');
  const m = await C.cifra(a, 'd0123456789abcdef01234567', C.nuovoId(), {tipo:'testo', testo:'segreto'}, [per(b), per(b2)]);
  const buono = ricevuto(m, m.chiavi[b.disp], a);
  await C.decifra(b, buono, a.pubblica);
  await fallisce(C.decifra(c, buono, a.pubblica), 'un altro dispositivo con la chiave di Bruno');
  await fallisce(C.decifra(b, Object.assign({}, buono, {chiave:m.chiavi[b2.disp]}), a.pubblica), 'chiave di un altro dispositivo');
  for (const [k, v] of [['conv', 'd99999999999999999999999'], ['id', C.nuovoId()], ['da', 'cccccccccccccccc'], ['disp', c.disp]])
    await fallisce(C.decifra(b, Object.assign({}, buono, {[k]:v}), a.pubblica), 'cambiato ' + k);
  const corpo = C.deb64u(buono.corpo); corpo[20] ^= 1;
  await fallisce(C.decifra(b, Object.assign({}, buono, {corpo:C.b64u(corpo)}), a.pubblica), 'corpo cambiato');
  // un messaggio cifrato da Carla che dice di venire da Anna: la chiave di Anna non lo apre
  const falso = await C.cifra(Object.assign({}, c, {persona:a.persona, disp:a.disp}), buono.conv, C.nuovoId(), {tipo:'testo', testo:'sono Anna'}, [per(b)]);
  await fallisce(C.decifra(b, ricevuto(falso, falso.chiavi[b.disp], a), a.pubblica), 'mittente falso');
});

prova('la grandezza non dice quanto testo', async () => {
  const a = await disp('aaaaaaaaaaaaaaaa'), b = await disp('bbbbbbbbbbbbbbbb');
  const l = [];
  for (const t of ['ciao', 'un testo un po\' più lungo di ciao, ma non troppo']) l.push(C.deb64u((await C.cifra(a, 'd0', C.nuovoId(), {tipo:'testo', testo:t}, [per(b)])).corpo).length);
  vero(l[0] === l[1] && (l[0] - C.CIFRA) % 256 === 0, 'grandezze ' + l);
});

prova('allegato a pezzi', async () => {
  const k = await C.chiaveAllegato(C.nuovaChiaveAllegato()), aid = 'AAAAAAAAAAAAAAAAAAAAAA';
  vero(C.pezzi(1) === 1 && C.pezzi(C.PEZZO) === 1 && C.pezzi(C.PEZZO + 1) === 2 && C.dimensioneCifrata(C.PEZZO + 1) === C.PEZZO + 1 + 56, 'conti dei pezzi');
  const p0 = te.encode('primo pezzo'), p1 = te.encode('secondo');
  const c0 = await C.cifraPezzo(k, aid, 0, 2, p0), c1 = await C.cifraPezzo(k, aid, 1, 2, p1);
  vero(c0.length === p0.length + C.CIFRA, 'grandezza del pezzo cifrato');
  vero(new TextDecoder().decode(await C.decifraPezzo(k, aid, 0, 2, c0)) === 'primo pezzo', 'pezzo 0');
  await fallisce(C.decifraPezzo(k, aid, 1, 2, c0), 'pezzo 0 al posto dell\'1');
  await fallisce(C.decifraPezzo(k, aid, 0, 1, c0), 'totale cambiato (pezzi tolti)');
  await fallisce(C.decifraPezzo(k, 'BBBBBBBBBBBBBBBBBBBBBB', 0, 2, c0), 'pezzo di un altro allegato');
  await fallisce(C.decifraPezzo(await C.chiaveAllegato(C.nuovaChiaveAllegato()), aid, 1, 2, c1), 'chiave sbagliata');
});

prova('codice di sicurezza', async () => {
  const a = await disp('aaaaaaaaaaaaaaaa'), a2 = await disp('aaaaaaaaaaaaaaaa'), b = await disp('bbbbbbbbbbbbbbbb'), b2 = await disp('bbbbbbbbbbbbbbbb');
  const A = {id:'aaaaaaaaaaaaaaaa', chiavi:[a.pubblica, a2.pubblica]}, B = {id:'bbbbbbbbbbbbbbbb', chiavi:[b.pubblica]};
  const x = await C.codice(A, B);
  vero(/^\d{12}$/.test(x), 'non 12 cifre: ' + x);
  vero(x === await C.codice(B, A), 'dipende dall\'ordine delle persone');
  vero(x === await C.codice({id:A.id, chiavi:[a2.pubblica, a.pubblica]}, B), 'dipende dall\'ordine delle chiavi');
  vero(x !== await C.codice(A, {id:B.id, chiavi:[b.pubblica, b2.pubblica]}), 'una chiave in più non lo cambia');
  vero(x !== await C.codice(A, {id:B.id, chiavi:[b2.pubblica]}), 'una chiave diversa non lo cambia');
  const c = C.confronta([a.pubblica], [a.pubblica, a2.pubblica]);
  vero(c.aggiunte.length === 1 && c.aggiunte[0] === a2.pubblica && !c.tolte.length, 'confronto delle chiavi');
});

// ----- con il server di prova: due persone vere, i loro telefoni e il Mac mini che vede solo il cifrato -----
async function sulServer(base, intest) {
  const chiama = async (chi, metodo, percorso, corpo, grezzi) => {
    const h = Object.assign({}, intest[chi]);
    let body;
    if (grezzi) { h['Content-Type'] = 'application/octet-stream'; body = grezzi; }
    else if (corpo !== undefined) { h['Content-Type'] = 'application/json'; body = JSON.stringify(corpo); }
    const r = await fetch(base + 'api/extra/chat/' + percorso, {method:metodo, headers:h, body});
    if ((r.headers.get('content-type') || '').startsWith('application/octet-stream')) return {stato:r.status, byte:new Uint8Array(await r.arrayBuffer())};
    return {stato:r.status, j:await r.json()};
  };
  const ok = async (...a) => { const r = await chiama(...a); if (r.stato !== 200) throw new Error(a[1] + ' ' + a[2] + ': ' + r.stato + ' ' + JSON.stringify(r.j)); return r.j || r.byte; };
  const entra = async chi => {
    const k = await C.nuoveChiavi(), d = {disp:C.nuovoId(), privata:k.privata, pubblica:k.pubblica};
    const st = await ok(chi, 'POST', 'dispositivi', {id:d.disp, chiave:d.pubblica});
    d.persona = st.io.id;
    return d;
  };
  const anna = await entra('anna'), bruno = await entra('bruno');
  const conv = (await ok('anna', 'POST', 'conversazioni', {con:bruno.persona})).conversazione;
  let rubrica = (await ok('anna', 'GET', 'persone')).persone;
  const manda = async (contenuto, allegati) => {
    for (let giro = 0; giro < 3; giro++) {
      const m = await C.cifra(anna, conv.id, C.nuovoId(), contenuto, C.destinatari(rubrica, conv.membri, anna.disp));
      const r = await chiama('anna', 'POST', 'messaggi', Object.assign(m, {allegati:allegati || []}));
      if (r.stato === 200) return r.j;
      if (r.stato !== 409 || !r.j.mancano) throw new Error('manda: ' + r.stato + ' ' + JSON.stringify(r.j));
      rubrica = (await ok('anna', 'GET', 'persone')).persone;   // chiavi cambiate: si rilegge e si cifra di nuovo
    }
    throw new Error('manda: sempre 409');
  };
  const segreti = ['Primo messaggio segreto per Bruno', 'Secondo messaggio, dopo il telefono nuovo di Bruno'];
  await manda({tipo:'testo', testo:segreti[0]});
  const bruno2 = await entra('bruno2');   // Anna non lo sa ancora: il Mac mini risponde 409 e lei cifra anche per lui
  vero(bruno2.persona === bruno.persona, 'stessa persona');
  await manda({tipo:'testo', testo:segreti[1]});
  vero(rubrica.find(p => p.id === bruno.persona).dispositivi.length === 2, 'Anna non ha riletto le chiavi di Bruno');
  // allegato di due pezzi (8 MB + qualcosa), cifrato prima di partire
  const chiaro = new Uint8Array(C.PEZZO + 1000);
  for (let i = 0; i < chiaro.length; i += 65536) crypto.getRandomValues(chiaro.subarray(i, Math.min(i + 65536, chiaro.length)));
  const segno = te.encode('ATLANTE-SEGNO-IN-CHIARO-DELLALLEGATO');
  chiaro.set(segno, 5000);
  const ka = C.nuovaChiaveAllegato(), k = await C.chiaveAllegato(ka), np = C.pezzi(chiaro.length);
  const al = await ok('anna', 'POST', 'allegati', {conv:conv.id, dimensione:C.dimensioneCifrata(chiaro.length), pezzi:np});
  for (let n = 0; n < np; n++) await ok('anna', 'POST', 'allegati/' + al.id + '/' + n, undefined, await C.cifraPezzo(k, al.id, n, np, chiaro.subarray(n * C.PEZZO, (n + 1) * C.PEZZO)));
  await manda({tipo:'foto', allegato:{id:al.id, chiave:ka, pezzi:np, dimensione:chiaro.length, mime:'image/jpeg', nome:'foto.jpg'}}, [al.id]);
  // Bruno legge con i suoi due telefoni: il primo tutto, il secondo solo da quando c'è
  const leggi = async (chi, d) => {
    const r = await ok(chi, 'GET', 'messaggi?disp=' + encodeURIComponent(d.disp) + '&dopo=0');
    const rb = (await ok(chi, 'GET', 'persone')).persone;
    const out = [];
    for (const m of r.messaggi) out.push(await C.decifra(d, m, C.chiaveDi(rb, m.da, m.disp)));
    await ok(chi, 'GET', 'messaggi?disp=' + encodeURIComponent(d.disp) + '&dopo=' + r.fino);   // conferma: consegnati
    return out;
  };
  const b1 = await leggi('bruno', bruno), b2 = await leggi('bruno2', bruno2);
  vero(b1.length === 3 && b1[0].testo === segreti[0] && b1[1].testo === segreti[1], 'Bruno: ' + JSON.stringify(b1.slice(0, 2)));
  vero(b2.length === 2 && b2[0].testo === segreti[1], 'il telefono nuovo ha i messaggi vecchi: ' + JSON.stringify(b2.slice(0, 1)));
  const a = b1[2].allegato, kb = await C.chiaveAllegato(a.chiave), parti = [];
  for (let n = 0; n < a.pezzi; n++) parti.push(await C.decifraPezzo(kb, a.id, n, a.pezzi, await ok('bruno', 'GET', 'allegati/' + a.id + '/' + n)));
  const riavuto = new Uint8Array(await new Blob(parti).arrayBuffer());
  vero(riavuto.length === chiaro.length && riavuto.every((x, i) => x === chiaro[i]), 'allegato diverso');
  // Anna vede le due spunte: consegnato a Bruno
  const st = (await ok('anna', 'GET', 'conversazioni')).conversazioni.find(c => c.id === conv.id).stati[bruno.persona];
  vero(st && st.c >= 3, 'non consegnato: ' + JSON.stringify(st));
  return {segreti, segno:new TextDecoder().decode(segno)};
}

async function vapid(jwt, k) {
  const [t, d, f] = jwt.split('.');
  const pub = await crypto.subtle.importKey('raw', C.deb64u(k), {name:'ECDSA', namedCurve:'P-256'}, false, ['verify']);
  vero(await crypto.subtle.verify({name:'ECDSA', hash:'SHA-256'}, pub, C.deb64u(f), te.encode(t + '.' + d)), 'firma VAPID non valida');
  return {testa:JSON.parse(new TextDecoder().decode(C.deb64u(t)))};
}

const [modo, ...arg] = Deno.args;
try {
  if (modo === 'prove') {
    const fatte = [];
    for (const [nome, fn] of prove) { try { await fn(); fatte.push(nome); } catch (e) { throw new Error(nome + ': ' + e.message); } }
    console.log(JSON.stringify({ok:true, prove:fatte}));
  } else if (modo === 'server') {
    console.log(JSON.stringify(Object.assign({ok:true}, await sulServer(arg[0], JSON.parse(arg[1])))));
  } else if (modo === 'vapid') {
    console.log(JSON.stringify(Object.assign({ok:true}, await vapid(arg[0], arg[1]))));
  } else throw new Error('modo sconosciuto: ' + modo);
} catch (e) {
  console.log(JSON.stringify({ok:false, errore:String(e && e.message || e)}));
}
