// Prove di x/cifra.js e delle regole di x/posizione.js con la WebCrypto vera (Deno), lanciate da test_extra.py
// (TestPosizioneJS):   deno run --no-config --no-remote --allow-read=<cartella musica> test_posizione.js <cartella musica>
// Tre telefoni: Samuele manda la sua posizione ad Anna; Marco non è tra i destinatari. Il «Mac mini» qui è chi prova a
// cambiare, spostare o attribuire a un altro la posizione cifrata: il telefono di Anna se ne deve accorgere.
const dir = Deno.args[0];
if (Deno.args[1] === 'server') { await attraversoIlServer(Deno.args[2], JSON.parse(Deno.args[3])); Deno.exit(0); }
(0, eval)(Deno.readTextFileSync(dir + '/x/cifra.js'));
(0, eval)(Deno.readTextFileSync(dir + '/x/posizione.js'));
const C = globalThis.Cifra, R = globalThis.POS_REGOLE;
const riuscite = [], fallite = [];
async function prova(nome, fn) {
  try { await fn(); riuscite.push(nome); } catch (e) { fallite.push(nome + ': ' + (e && e.message || e)); }
}
function uguale(a, b, m) { if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error((m || '') + ' ' + JSON.stringify(a) + ' ≠ ' + JSON.stringify(b)); }
function vero(x, m) { if (!x) throw new Error(m || 'falso'); }
async function fallisce(fn, m) { let ok = false; try { await fn(); } catch (e) { ok = true; } if (!ok) throw new Error('doveva fallire: ' + m); }

const telefono = async persona => { const k = await C.nuoveChiavi(); return {persona, disp:C.nuovoId(), privata:k.privata, pubblica:k.pubblica}; };
const sam = await telefono('p'), anna = await telefono('aaaaaaaaaaaaaaaa'), marco = await telefono('mmmmmmmmmmmmmmmm');
const voce = (id, nome, t) => ({id, nome, dispositivi:[{id:t.disp, chiave:t.pubblica, attivo:true}]});
const rubrica = {io:'p', persone:[voce('p', 'Samuele', sam), voce(anna.persona, 'Anna', anna), voce(marco.persona, 'Marco', marco)]};
const pos = {tipo:'vivo', lat:45.464211, lon:9.190111, acc:8, vel:6.2, dir:90, t:1761580000000};
const busta = await C.cifra(sam, 'c0ffee0000000001', C.nuovoId(), pos, C.destinatari(rubrica.persone, [anna.persona], sam.disp));
const perAnna = (b, extra) => Object.assign({id:b.id, conv:'c0ffee0000000001', da:'p', disp:sam.disp, corpo:b.corpo, sale:b.sale, chiave:b.chiavi[anna.disp]}, extra);

await prova('Anna apre la posizione di Samuele', async () => {
  uguale(Object.keys(busta.chiavi), [anna.disp], 'solo il telefono di Anna');
  uguale(await C.decifra(anna, perAnna(busta), sam.pubblica), pos);
});
await prova('stessi formati della chat (chiavi, sale, corpo, id)', async () => {
  const pub = C.deb64u(sam.pubblica);
  uguale([pub.length, pub[0]], [65, 4], 'chiave pubblica P-256 non compressa');
  uguale(C.deb64u(busta.chiavi[anna.disp]).length, 60, 'chiave cifrata');
  uguale(C.deb64u(busta.sale).length, 16, 'sale');
  uguale((C.deb64u(busta.corpo).length - 28) % 256, 0, 'corpo allungato a multipli di 256');
  vero(/^[A-Za-z0-9_-]{22}$/.test(sam.disp) && /^[A-Za-z0-9_-]{22}$/.test(busta.id), 'id di 16 byte in base64url');
});
await prova('Marco non la apre', async () => {
  await fallisce(() => C.decifra(marco, perAnna(busta), sam.pubblica), 'Marco con la chiave di Anna');
});
await prova('il Mac mini non la sposta in un\'altra condivisione né cambia l\'aggiornamento', async () => {
  await fallisce(() => C.decifra(anna, perAnna(busta, {conv:'c0ffee0000000002'}), sam.pubblica), 'altra condivisione');
  await fallisce(() => C.decifra(anna, perAnna(busta, {id:C.nuovoId()}), sam.pubblica), 'altro id');
});
await prova('il Mac mini non la attribuisce a un altro', async () => {
  await fallisce(() => C.decifra(anna, perAnna(busta, {da:marco.persona}), sam.pubblica), 'altra persona');
  await fallisce(() => C.decifra(anna, perAnna(busta, {disp:marco.disp}), marco.pubblica), 'altro dispositivo');
});
await prova('una posizione cambiata si scopre', async () => {
  const b = C.deb64u(busta.corpo); b[40] ^= 1;
  await fallisce(() => C.decifra(anna, perAnna(busta, {corpo:C.b64u(b)}), sam.pubblica), 'corpo cambiato');
});
await prova('la chiave privata non esce dal telefono', async () => {
  vero(!sam.privata.extractable, 'esportabile');
  await fallisce(() => crypto.subtle.exportKey('pkcs8', sam.privata), 'esportata');
});
await prova('le chiavi di questo dispositivo si creano una volta sola', async () => {
  let salvato = null, scritture = 0;
  C.archivio.leggi = async () => salvato;
  C.archivio.scrivi = async x => { salvato = x; scritture++; };
  const a = await C.mie(), b = await C.mie();
  vero(a === b && scritture === 1 && /^[A-Za-z0-9_-]{22}$/.test(a.disp), 'una coppia di chiavi');
});
await prova('chiudi e apri passano dalla rubrica del Mac mini', async () => {
  const io = await C.mie(), richieste = [];
  const r = {io:'p', persone:[{id:'p', nome:'Samuele', dispositivi:[{id:io.disp, chiave:io.pubblica, attivo:true}]}, voce(anna.persona, 'Anna', anna)]};
  C.usa(async (percorso, o) => { richieste.push(percorso + ' ' + ((o && o.method) || 'GET')); return percorso === 'chiavi' && !(o && o.method) ? r : {ok:true}; });
  await C.registra();
  const b = await C.chiudi('c0ffee0000000003', 'p', pos, [anna.persona]);
  uguale(Object.keys(b.chiavi), [anna.disp]);
  uguale(await C.decifra(anna, {id:b.id, conv:'c0ffee0000000003', da:'p', disp:io.disp, corpo:b.corpo, sale:b.sale, chiave:b.chiavi[anna.disp]}, io.pubblica), pos);
  // e al contrario: Anna manda, questo dispositivo apre (con la chiave di Anna presa dalla rubrica)
  const da = await C.cifra(anna, 'c0ffee0000000004', C.nuovoId(), {tipo:'punto', lat:44.1, lon:11.2}, [{id:io.disp, chiave:io.pubblica}]);
  uguale(await C.apri({id:da.id, disp:anna.disp, corpo:da.corpo, sale:da.sale, chiave:da.chiavi[io.disp]}, 'c0ffee0000000004', anna.persona), {tipo:'punto', lat:44.1, lon:11.2});
  vero(richieste[0] === 'chiavi POST', 'prima manda la sua chiave: ' + richieste.join(', '));
});
await prova('al più ogni 5 secondi e solo dopo 20 metri, comunque una al minuto', async () => {
  const p0 = {lat:45.4642, lon:9.19}, t0 = 1e12, prec = {lat:p0.lat, lon:p0.lon, inviato:t0};
  const a30m = {lat:p0.lat + 30 / 111195, lon:p0.lon}, a10m = {lat:p0.lat + 10 / 111195, lon:p0.lon};
  vero(R.daMandare(null, p0, t0), 'la prima sempre');
  vero(!R.daMandare(prec, a30m, t0 + 4000), 'prima di 5 s no');
  vero(R.daMandare(prec, a30m, t0 + 5000), '30 m dopo 5 s sì');
  vero(!R.daMandare(prec, a10m, t0 + 30000), '10 m no');
  vero(R.daMandare(prec, a10m, t0 + 60000), 'dopo un minuto sì');
  vero(Math.abs(R.distanza(p0, a30m) - 30) < 0.5, 'distanza ' + R.distanza(p0, a30m));
});
await prova('sintassi delle pagine toccate', async () => {
  for (const f of ['x/naviga.html', 'x/posizione.html', 'index.html']) {
    const t = Deno.readTextFileSync(dir + '/' + f), re = /<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g;
    let m; while ((m = re.exec(t))) new Function(m[1]);
  }
});
console.log(JSON.stringify({riuscite, fallite}));

// Con il server di prova vero: due telefoni (Samuele dal Mac, Anna con il suo invito da Internet), ognuno con le sue chiavi
// (due copie di x/cifra.js), le rotte vere e un aggiornamento che passa cifrato dal Mac mini e si apre solo da Anna.
async function attraversoIlServer(base, intest) {
  const src = Deno.readTextFileSync(dir + '/x/cifra.js');
  const richiesta = h => async (percorso, o) => {
    o = Object.assign({}, o);
    const r = await fetch(base + 'api/extra/' + percorso, {method:o.method || 'GET', headers:Object.assign({'Content-Type':'application/json'}, h),
      body:o.body ? JSON.stringify(o.body) : undefined});
    const j = await r.json();
    if (!r.ok) throw new Error(percorso + ': ' + r.status + ' ' + j.errore);
    return j;
  };
  function telefono(h) {
    const G = {crypto, navigator:{}, isSecureContext:true};
    new Function('globalThis', 'self', src)(G, G);
    let x = null;
    G.Cifra.archivio.leggi = async () => x;
    G.Cifra.archivio.scrivi = async v => { x = v; };
    G.Cifra.usa(richiesta(h));
    G.Cifra.chiama = richiesta(h);
    return G.Cifra;
  }
  const sam = telefono(intest.samuele), anna = telefono(intest.anna), esito = {riuscite:[], fallite:[]};
  try {
    const ioS = await sam.registra(), ioA = await anna.registra();
    const chi = (await anna.rubrica(true)).io;
    const pos = {tipo:'vivo', lat:43.7696, lon:11.2558, acc:6, t:Date.now()};
    const h = (C, metodo, p, body) => C.chiama(p, {method:metodo, body});
    const c = (await h(sam, 'POST', 'posizione', {tipo:'vivo', a:[chi], durata:900, disp:ioS.disp})).condivisione;
    await sam.rubrica(true);
    const busta = await sam.chiudi(c.id, 'p', pos, [chi]);
    const r = await h(sam, 'POST', 'posizione/' + c.id, Object.assign({}, busta, {disp:ioS.disp}));
    if (r.mancano.length) throw new Error('mancano ' + r.mancano);
    const j = await h(anna, 'GET', 'posizione?disp=' + encodeURIComponent(ioA.disp));
    const x = j.per_me.find(y => y.id === c.id);
    uguale(await anna.apri(x.pos, x.id, x.da.id), pos);
    esito.riuscite.push('Anna apre la posizione passata dal Mac mini');
    vero(!JSON.stringify(j).includes(String(pos.lat)), 'coordinate in chiaro nella risposta');
    esito.riuscite.push('il Mac mini non ha le coordinate in chiaro');
    await h(sam, 'DELETE', 'posizione/' + c.id);
    esito.riuscite.push('Samuele la ferma');
  } catch (e) { esito.fallite.push(String(e && e.message || e)); }
  console.log(JSON.stringify(esito));
}
