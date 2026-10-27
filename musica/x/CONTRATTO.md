# Moduli extra di Atlante Musica — contratto comune

L'app Musica (`musica/index.html`) ha una scheda **Altro** che apre ogni modulo (`musica/x/<nome>.html`)
in un riquadro a tutto schermo **dentro l'app**: la musica continua a suonare, il mini player resta.
Il server sul Mac (`musica/server/musica_libreria.py`) serve sia l'app sia queste pagine e le API `api/extra/`.

## Regole per ogni modulo
- Un file HTML autonomo in `musica/x/`, in italiano, che include `kit.css` e `kit.js` (`window.AX`).
- Nessun framework; librerie esterne solo da `cdn.jsdelivr.net/npm/<pacchetto>@<versione esatta>` o `unpkg.com`, caricate solo quando servono.
- Funziona su iPhone (Safari, PWA installata) e su Mac (Safari/Chrome); larghezza minima 320 px; tema chiaro/scuro da `kit.css`.
- Dati dell'utente: `AX.store` (localStorage, prefisso `ax-`) o IndexedDB proprio del modulo (nome `ax-<modulo>`). Mai numeri completi di carte di pagamento, CVV o password.
- Se il Mac non risponde (`(await AX.stato()).ok === false`) il modulo resta usabile per tutto ciò che non dipende dal Mac e lo dice con una nota chiara.
- Audio da riprodurre (podcast, audiolibri, voce) → `AX.player.play([{id,title,artist,album,url,cover}], start, 'Nome lista')`: suona nel lettore dell'app (schermata di blocco, CarPlay, cuffie).
- Intestazione con `AX.header('Titolo')` (pulsante «‹ Altro» che chiude il riquadro).
- Niente `alert()`/`confirm()`: usare `AX.toast` e controlli nella pagina.

## API del Mac (`AX.api(path, opt)` → JSON; errori: `{errore:"testo"}` con codice ≥ 400)
Aperta da Internet (`tailscale funnel`) senza password, ogni API risponde 401 con `X-Atlante-Accesso: richiesto`: `AX.fetchT` (e quindi `AX.api`, `AX.proxy`) fa aprire all'app la pagina della password.

| Metodo | Percorso | Risposta |
|---|---|---|
| GET | `stato` | `{ytdlp:bool, ffmpeg:bool, say:bool, ollama:bool, modelli:[nomi], video_dir, libri_dir, sistema}` |
| GET | `proxy?url=<url codificato>` | il contenuto della pagina/feed (stesso Content-Type), max 15 MB, solo http/https pubblici (`AX.proxy(url)` restituisce la Response) |
| POST | `video` `{url, tipo:'video'|'audio', qualita:'1080'|'720'|'best'}` | `{id}` — scaricamento con yt-dlp in sottofondo |
| GET | `video` | `{lavori:[{id,url,titolo,tipo,stato:'in coda'|'in corso'|'fatto'|'errore',progresso:0-100,file,nome,dimensione,errore}]}` |
| GET | `video/file/<id>` | il file scaricato (con Range) |
| POST | `video/<id>/annulla` | `{ok:true}` |
| GET | `musica/cerca?q=<testo>&fonte=ytm\|yt` | `{fonte, risultati:[{id,titolo,artista,album,durata,copertina,explicit,censurata}]}` — YouTube Music (predefinita) o YouTube; le censurate in fondo |
| GET | `musica/ascolta/<id YouTube>` | l'audio del brano per ascoltarlo prima (m4a, con Range; la prima volta ~15 s) |
| POST | `musica` `{id, titolo, artista, album, copertina, explicit}` oppure `{url}` | `{id}` — scarica il brano in MP3 (tag, copertina, purl) nella cartella della musica scaricata, che fa parte della libreria; lo stesso brano due volte = lo stesso lavoro |
| GET | `musica` | `{lavori:[{id,yt,url,titolo,artista,album,stato,progresso,nome,dimensione,brano,errore}]}` — `brano` = id nel catalogo quando è in libreria |
| POST | `musica/<id>/annulla` | `{ok:true}` — ferma lo scaricamento; uno finito esce dall'elenco (il brano resta) |
| GET | `dispositivi` | `{dispositivi:[{id,nome,tipo,fa,stato}]}` — le app aperte sentite negli ultimi 60 s (telecomando dell'app: `fa` = secondi dall'ultimo stato) |
| POST | `dispositivi/<id>/stato` `{nome,tipo,stato}` | `{comandi:[…]}` — lo manda ogni app ogni pochi secondi |
| GET | `dispositivi/<id>/comandi?attesa=25` | `{comandi:[…]}` appena arriva un comando (attesa lunga) |
| POST | `dispositivi/<id>/comando` `{azione, …}` | `{ok:true}` — toggle, play, pause, next, prev, seek {t}, volume {v}, suona {coda,t,da,shuffle,repeat} |
| GET | `voci` | `{voci:[{nome, lingua, esempio}]}` — voci di sintesi del Mac (`say -v ?`), prima quelle italiane |
| POST | `audiolibro` `{titolo, autore, voce, velocita, capitoli:[{titolo, testo}]}` | `{id}` — ogni capitolo diventa un file m4a con `say` |
| GET | `audiolibro` | `{lavori:[{id,titolo,autore,stato,progresso,capitoli:[{n,titolo,pronto:bool,durata}]}]}` |
| GET | `audiolibro/file/<id>/<n>` | m4a del capitolo n (da 0), con Range |
| DELETE | `audiolibro/<id>` | `{ok:true}` |
| GET | `ia` | `{attiva:bool, modelli:[...], modello}` |
| POST | `ia` `{prompt, sistema, json:bool, modello}` | `{testo}` — IA locale (Ollama su `localhost:11434`), tempo massimo 180 s |
| GET | `idee` | `{idee:[{id,testo,creato,stato}], restano, max_caratteri, max_giorno}` — le idee di chi chiede (anche un invitato), la più recente per prima; `stato` = nuova, proposta, accettata, scartata, fatta |
| POST | `idee` `{testo}` | `{idea, restano}` — al più 2000 caratteri e 10 idee al giorno per persona (429 oltre) |
| GET | `tematiche` | `{gruppi:[{id,nome,playlist:[{id,nome,descrizione,n,durata,copertine}]}], stato:{attivo,fatti,totale,ora}, ia:{rimaste,al_giorno}}` — Playlist di Atlante fatte dal Mac (anche per gli invitati); le vuote non ci sono |
| GET | `tematiche/<id>` | `{id,nome,descrizione,n,brani:[id del catalogo]}` (404 se vuota o sconosciuta) |
| GET | `tematiche/ritmi` | `{ritmi:{id:[bpm,regolarita,energia,modo]}, buonumore:[[id,peso]]}` — per «Per ritmo» e «Mettimi di buonumore» (l'app lo tiene per quando non c'è rete); modo 1 maggiore, -1 minore, 0 incerto |
| GET | `tematiche/campione` | `{stato, brani:[{titolo,artista,anno,generi,fonte,italiano,bpm,energia,regolarita,tonalita,playlist}]}` — solo Samuele: controllo a mano dei dati dei brani |
| POST | `tematiche/ia` `{richiesta}` oppure `{su_misura:true, ascolti:[{id,n,fav}]}` | `{nome,criteri,riassunto,brani,durata,note,rimaste}` — anteprima: l'IA traduce la richiesta in criteri, il Mac sceglie i brani dalla libreria; una richiesta all'IA alla volta, 20 al giorno per persona (429) |
| POST | `tematiche/condivise` `{nome,brani,locale}` | `{id,n}` — «Condividi in Atlante» (la stessa `locale` di nuovo la aggiorna) |
| DELETE | `tematiche/condivise/<id>` | `{ok:true}` — solo chi l'ha condivisa o Samuele |
| GET | `dispositivo` | `{nome, sistema, versione, avvio, memoria:{totale,libera}, dischi:[{nome,percorso,totale,libero}], pulizia:[{nome,percorso,dimensione}]}` |
| GET | `ascoltati` | `{io:{nome,condivido,ascolti,cambia}, settimana:[…], mese:[…], per_te:[{…,da}], condivisi:[{nome,brani}], soglia}` — classifiche «I più ascoltati in Atlante» (uguali per tutti; un brano solo se l'hanno ascoltato almeno `soglia` persone), «Per te» e «Cosa ascolta …» di chi condivide; ogni brano `{brano,titolo,artista,album,copertina}` |
| POST | `ascoltati` `{ascolti:[{brano, giorno:'AAAA-MM-GG'}]}` | `{contati, scartati}` — brani del catalogo che hanno suonato davvero (anche quelli di giorni passati, fatti senza rete) |
| POST | `ascoltati/condividi` `{si:bool}` | `{condivido}` — «Condividi i miei ascolti» (spento all'inizio) |
| DELETE | `ascoltati` | `{ok, cancellati}` — cancella dal Mac mini i propri ascolti |
| GET | `ascoltati/anche/<brano>` | `{brani:[…]}` — «Chi ascolta questo brano ascolta anche…» |

### Chat cifrata (`chat/…`, fase 5a)
Per Samuele (`"p"`) e per gli invitati; dalla rete di casa senza biscotto 403. Il Mac mini riceve solo cifrato (vedi
`x/chat-cifra.js`): ognuno vede solo le sue conversazioni e solo i messaggi diretti ai suoi dispositivi, Samuele compreso.

| Metodo | Percorso | Risposta |
|---|---|---|
| GET | `chat/stato` | `{io:{id,nome}, dispositivi:[{id,tipo,creato,visto,notifiche,questo}], vapid, pezzo, giorni, v}` |
| POST | `chat/dispositivi` `{id, chiave}` | come `chat/stato` — chiave pubblica P-256 non compressa in base64url; 409 se l'id è di un altro |
| GET | `chat/persone` | `{io, persone:[{id,nome,dispositivi:[{id,chiave,attivo}]}], bloccati:[id], v}` |
| GET | `chat/conversazioni` | `{conversazioni:[{id,tipo:'due'\|'gruppo',membri,nome,creatore,creato,ultimo,stati:{persona:{c,l}},eventi}], v}` |
| POST | `chat/conversazioni` `{con}` o `{nome, membri}` | `{conversazione}` — la chat a due è sempre la stessa; gruppi al più 32 persone |
| POST / DELETE | `chat/conversazioni/<id>/membri` `{persone}` · `…/membri/<persona>` | aggiunge, toglie (chi ha creato il gruppo) o esce (sé stessi) |
| POST | `chat/conversazioni/<id>/letto` `{seq}` | `{ok:true}` |
| POST | `chat/messaggi` `{id, conv, disp, corpo, sale, chiavi:{disp:chiave}, allegati}` | `{seq, t}`; 409 `{mancano:[disp]}` se manca un dispositivo (chiavi cambiate), 409 `{nessuno:true}` se l'altra persona non ha ancora la chat |
| GET | `chat/messaggi?disp=&dopo=&v=&attesa=25` | `{messaggi:[{seq,id,conv,da,disp,t,corpo,sale,chiave,allegati}], fino, altri, v}` — attesa lunga; `dopo` conferma la consegna |
| POST | `chat/allegati` `{conv, dimensione, pezzi}` | `{id, pezzo}` — al più 200 MB in chiaro, pezzi da 8 MB (+28 byte cifrati) |
| POST / GET | `chat/allegati/<id>/<n>` | il pezzo cifrato (`application/octet-stream`); si scarica solo se si è ricevuto il messaggio |
| POST / DELETE | `chat/blocchi` `{persona}` · `chat/blocchi/<persona>` | `{bloccati}` |
| POST / DELETE | `chat/push` `{disp, endpoint}` · `chat/push/<disp>` | notifiche (solo servizi di Apple, Google, Mozilla, Microsoft) |
| GET | `chat/avvisi?disp=` | `{avvisi:[{conv,da,nome,gruppo,n,t}]}` — per il testo della notifica, letto dal service worker |

## API dell'app che ospita (`AX.player`, oppure `parent.AM`)
`playExternal(items, start, from)`, `toggle()`, `next()`, `prev()`, `seek(sec)`,
`now()` → `{id,title,artist,album,cover,playing,ext,t,d}` oppure `null`, `onChange(cb)` → funzione per smettere, `toast(msg)`, `close()`,
`libreria()` → Promise (rilegge il catalogo del Mac), `suona(ids, start, from)` e `dopo(id)` per i brani della libreria (id del catalogo, es. `brano` di `musica`).

## Moduli
| File | Contenuto |
|---|---|
| `cerca.html` | cerca su YouTube Music e YouTube (anche dalla barra del tasto **+** dell'app), ascolta prima, scarica nella libreria, scarica da un link; «Cerca anche su» altri siti (mp3juice.sc segnato come sito a rischio, voluto da Samuele) |
| `podcast.html` | cerca (iTunes Search API), iscrizioni, episodi dal feed (via proxy), ascolto con ripresa del punto, scarica per l'ascolto offline |
| `libri.html` | libri (EPUB, TXT, PDF testuale, testo incollato) e lettore; lettura ad alta voce con voce naturale; «Crea audiolibro» col Mac; audiolibri (anche LibriVox) |
| `video.html` | accesso ai servizi di streaming; scarica video/audio da qualsiasi sito supportato da yt-dlp sul Mac; elenco scaricati |
| `live.html` | andare in diretta su social e piattaforme di streaming audio/video: collegamenti, prova fotocamera/microfono, chiavi di trasmissione (solo sul dispositivo) |
| `naviga.html` | navigatore GPS con musica (comandi del lettore sempre visibili), istruzioni vocali, vignette autostradali con link ufficiali, scheda veicolo da foto e targa |
| `benessere.html` | conta calorie (Open Food Facts), «Settimana» (dispendio dal calendario .ics, bilancio dieta e integratori, confronto prezzi), Trainer |
| `strumenti.html` | ricerca del prezzo migliore, negozi per categoria, risolutore di test (crocette e domande aperte), carte fedeltà e di pagamento, gestore dispositivo |
| `idee.html` | «Idee»: l'invito a proporre miglioramenti dell'app, il modulo per mandarne una e «Le tue idee» con lo stato (aperto anche agli invitati); le idee di tutti sono in Gestione → Idee |
| `studio.html` | Atlante UCI (accesso con le proprie credenziali), notizie di ciclismo, allenamenti creati dall'IA su qualsiasi argomento, Assistente Idee |

## Rete: Tailscale (Gestione → Rete e modulo «Rete e VPN»)
Un'app web non può accendere il VPN del telefono: si comanda Tailscale sul Mac mini e si guida il telefono.

| Metodo | Percorso | Risposta |
|---|---|---|
| GET | `io` | `{ruolo, persona, nuove, via:'tailscale'\|'casa'\|'internet'}` — `via`: da dove arriva questo dispositivo (per tutti) |
| GET | `gestione/rete` | solo Samuele: `{installato, collegato, stato, errore, versione, nome, dns, indirizzo, rete, dispositivi:[{nome,sistema,indirizzo,online,visto}], serve:[{porta,indirizzo,destino,funnel}], questo, pannello, funnel:{acceso,porta,indirizzo,destino,porte,predefiniti:{porta,destino},pubblico}, uscita:{annunciata,approvata}}` |
| POST | `gestione/rete/funnel` `{acceso, porta?:443\|8443\|10000, destino?:'http://127.0.0.1:<porta>'}` | lo stato come sopra; se Tailscale dice di no 409 `{errore, dettaglio, link, pannello, rete}` (`pannello`: va permesso nel pannello di Tailscale) |
| POST | `gestione/rete/uscita` `{acceso}` | uscita da casa (exit node): lo stato come sopra, o 409 come per Funnel |

| File | Contenuto |
|---|---|
| `rete.html` | per tutti: se questo dispositivo arriva con Tailscale, dalla rete di casa o da Internet; come accendere Tailscale (App Store, Google Play); agli invitati spiega che non serve. Resta nella copia offline |

## Caricamenti, «Da controllare», film e contenuti (Gestione, parte 2)
Moduli: `carica.html` (Carica nella libreria: brani e film, «I miei caricamenti»; per tutti, invitati compresi),
`controllo.html` e `contenuti.html` (Gestione → Da controllare e Contenuti, solo Samuele, aperti da `gestione.html`).
Un invitato usa anche `cerca.html`: i brani che scarica vanno in «Da controllare» e l'app ne prende una copia
(`AM.copie()`). Ognuno vede e tocca solo i suoi caricamenti; Samuele non ha il limite giornaliero e i suoi brani entrano subito.

| Metodo | Percorso | Risposta |
|---|---|---|
| POST | `caricamenti` `{tipo:'brano', nome, dimensione, modificato}` | il caricamento `{id, ricevuti, stato:'in arrivo', pezzo}`; lo stesso file (nome, dimensione, data) a metà: lo stesso, da riprendere |
| POST | `caricamenti` `{tipo:'film', titolo, nome, dimensione, modificato}` | la richiesta `{id, stato:'richiesta'}` (di Samuele: `'consentito'`) |
| PUT | `caricamenti/<id>?da=<byte>` (corpo: i byte, al più 8 MB) | `{ricevuti, stato}`; alla fine `fatto` (brano: `elemento`; film: `finale`) o `errore`; 409 `{ricevuti}` = riprendi da lì; 429 limite di oggi; 507 disco quasi pieno |
| GET | `caricamenti` | `{caricamenti:[a metà e film], brani:[{id, stato:'attesa'\|'approvato'\|'scartato', origine:'caricato'\|'scaricato', titolo, artista…}], oggi:{usati, limite}\|null}` |
| GET / DELETE | `caricamenti/<id>` | il caricamento / annulla |
| GET | `caricamenti/<id>/file` | un mio brano di «Da controllare» (la copia per il telefono) |
| GET | `film` · `film/file/<nome>` | film nel Cloud `{film:[{nome, titolo, dimensione, quando, chi_nome}]}` · il file (con Range) — solo Samuele |
| GET | `gestione/controllo` | `{brani:[… tag, originali, chi_nome, doppioni]}, film:[richieste], scartati, libero, libero_film, spazio_min}` |
| GET | `gestione/controllo/<id>/file` · `…/copertina` | ascolto (con Range) · copertina |
| POST | `gestione/controllo/<id>` `{titolo, artista, album}` | tag corretti; PUT `…/copertina` (JPEG o PNG) copertina nuova |
| POST | `gestione/controllo/<id>/approva` `{forza}` | `{ok, brano, nome}`; 409 `{doppioni}` se c'è già lo stesso artista e titolo |
| POST | `gestione/controllo/<id>/scarta` · `gestione/film/<id>/consenti\|rifiuta` | `{ok:true}` |
| GET | `gestione/contenuti?q=` · `gestione/contenuti/tolti` | ricerca nella libreria (senza `q` gli ultimi arrivati) · brani tolti |
| POST | `gestione/contenuti/<id>` `{titolo, artista, album}` · PUT `…/copertina` · POST `…/ripristina` | correzione (in `correzioni.json`, il file non cambia) · copertina · via le correzioni |
| POST | `gestione/contenuti/<id>/togli` · `gestione/contenuti/tolti/<id>/rimetti` | il file in «Tolti» (stesso disco) e fuori dal catalogo, che elenca gli id in `tolti` · di nuovo al suo posto |
| `chat.html` | chat cifrata da telefono a telefono (a due e di gruppo): testo, foto, video, vocali, brani della libreria, letto/consegnato, Blocca, codice di sicurezza, notifiche; la cifratura è in `chat-cifra.js` (WebCrypto, provata con Deno in `server/test_chat_cifra.js`) |
## Chiamate vocali e video (`chiamate.html`)
Voce e video da telefono a telefono con WebRTC, cifrati (DTLS-SRTP); il Mac mini fa solo da centralino (chi chiama chi,
squillo su tutti i dispositivi della persona chiamata, risponde il primo; rifiutata, occupato, nessuna risposta dopo 40 s).
Gruppi fino a 4 persone, collegati ognuno con ognuno (chi entra manda l'offerta a chi è già dentro). Per Samuele e gli
invitati; la rete di casa senza nessuno dietro no. Le persone sono `"p"` (Samuele) o l'id dell'invitato.

| Metodo | Percorso | Risposta |
|---|---|---|
| GET | `chiamate/persone` | `{io:{id,nome}, persone:[{id,nome}]}` — tutte tranne chi chiede, solo id e nome |
| GET | `chiamate/ice` | `{iceServers:[…], turn:bool}` — STUN (`--stun`/`ATLANTE_STUN`) e, se configurato, TURN con credenziali a tempo |
| GET | `chiamate/attendi?disp=<id>&dopo=<seq>&attesa=25` | `{seq, chiamate:[…], eventi:[{seq,tipo:'chiamata'\|'segnale',chiamata,da,segnale,dati}]}` — senza `dopo`: subito, con il `seq` da cui partire |
| POST | `chiamate` `{a:[persone], video, disp}` | `{chiamata}` |
| POST | `chiamate/<id>/rispondi` \| `rifiuta` \| `chiudi` `{disp}` | `{chiamata}` |
| POST | `chiamate/<id>/aggiungi` `{a:[persone], disp}` | `{chiamata}` |
| POST | `chiamate/<id>/segnale` `{disp, a:persona, tipo:'offerta'\|'risposta'\|'candidati'\|'stato'\|'riprova', dati}` | `{ok:true}` — solo tra chi è dentro |

`chiamata` = `{id, da, da_nome, video, stato:'squilla'|'in corso'|'finita', motivo, partecipanti:[{id, nome, stato, io?, disp?, scade?}]}`
(il proprio `disp` solo per sé). Dall'app che ospita: `AM.chiama(persona o [persone], video)` apre la chiamata in una
cornice sopra tutto (true se è partita); oppure `x/chiamate.html?chiama=<persona>&video=1`. Notifiche ad app chiusa:
`avviso_chiamata(persona, evento, chiamata)` in `musica_libreria.py` (eventi `arriva`, `persa`, `chiusa`); il blocco
di una persona: `chiamata_bloccata(da, a)`.
## Posizione condivisa (cifrata da telefono a telefono)
`posizione.html` (riquadro «Condividi la posizione» in «Altro», anche per gli invitati): la propria posizione **dal vivo**
per 15 minuti, un'ora o finché la si ferma (al più 12 ore), oppure **un punto solo**, a una o più persone di Atlante. Il
motore gira nell'app (`x/posizione.js`, `window.POS` per la pagina): la barra in alto «Stai condividendo la posizione con
…» con «Ferma», e a chi riceve «*Nome* condivide la sua posizione» con **Raggiungilo**, che apre `naviga.html` (anche agli
invitati, solo da lì: la pagina non usa il Mac) con `#verso=persona:<id>` (dal vivo: percorso ricalcolato quando la
persona si sposta di più di 50 m) o `#verso=<lat>,<lon>` (un punto); dopo il `#`, così le coordinate non vanno al Mac.
Cifratura: `x/cifra.js` (`window.Cifra`), lo schema e i formati della chat (ECDH P-256, HKDF-SHA256, AES-GCM con una
chiave nuova per aggiornamento cifrata per ciascun dispositivo; etichetta `atlante-posizione/1`); chiavi del dispositivo
in IndexedDB `ax-cifra`. Il Mac mini tiene in memoria solo l'ultimo aggiornamento cifrato.

| Metodo | Percorso | Risposta |
|---|---|---|
| GET | `chiavi` | `{io, persone:[{id, nome, dispositivi:[{id, chiave, attivo}]}]}` — chi è in Atlante (Samuele `"p"` per primo) e le chiavi pubbliche dei dispositivi attivi |
| POST | `chiavi` `{id, chiave}` | `{ok, io}` — la chiave pubblica di questo dispositivo (P-256 non compressa, base64url; id di 16 byte casuali) |
| GET | `posizione?disp=&v=&attesa=25` | `{v, io:{id,nome}, mie:[{id,tipo,durata,inizio,scade,seq,t,a:[{id,nome}],disp,mancano}], per_me:[{…, da:{id,nome}, pos:{id,disp,corpo,sale,chiave}\|null}]}` — attesa lunga finché cambia la versione `v`; in `pos` solo la chiave cifrata per `disp` |
| POST | `posizione` `{tipo:'vivo'\|'punto', a:[persone], durata:900\|3600\|0, disp}` | `{condivisione}` — una sola dal vivo per persona (la nuova prende il posto della vecchia), al più 10 attive |
| POST | `posizione/<id>` `{id, disp, corpo, sale, chiavi:{disp: chiave cifrata}}` | `{seq, t, scade, mancano}` — al più uno ogni 2 s sul Mac (il telefono: ogni 5 s e 20 m); `mancano` = dispositivi nuovi dei destinatari |
| DELETE | `posizione/<id>` | `{ok:true}` — solo chi l'ha avviata |

Tutte rispondono 403 dalla rete di casa senza biscotto (lì non si sa chi c'è) e sono permesse agli invitati.
## Testo sincronizzato (karaoke) nel lettore
Il tasto «Testo» del lettore a tutto schermo (in `index.html`, blocco «testo sincronizzato») mostra le parole del brano a
tempo con la musica, come in Apple Music: un tocco su una riga porta la musica lì (anche durante il mix e comandando un
altro dispositivo). Il testo viene dal file del brano (ID3 SYLT/USLT, MP4 ©lyr, FLAC LYRICS, anche in formato LRC) o da
LRCLIB (`lrclib.net`, gratuito, senza chiave; `ATLANTE_LRCLIB` per un indirizzo diverso), chiesto dal Mac e tenuto in
`<cache>/testi/`; sul telefono resta in IndexedDB `am-testi`. Anche per gli invitati.

| Metodo | Percorso | Risposta |
|---|---|---|
| GET | `testi/<id del catalogo>` | `{stato:'ok'\|'nessuno'\|'strumentale', sincronizzato, parole, fonte:'file'\|'lrclib', righe:[{t, testo, parole?:[{t,testo}], fine?}]}` — `t` in secondi (null senza tempi); 404 se il brano non c'è; 503 `{errore}` se LRCLIB non risponde e il file non ha un testo |
| GET | `testi?titolo=&artista=&album=&durata=` | come sopra, per un brano che non è nella libreria (solo LRCLIB); 400 senza titolo |
