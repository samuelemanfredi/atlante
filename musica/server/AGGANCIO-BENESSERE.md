# Musica dentro Benessere: istruzioni per il Mac mini

Queste istruzioni sono per la sessione di Claude sul Mac mini di Samuele. Servono a mettere la
nuova app Musica dentro il server di Benessere che c'è già, così:

- l'app si apre su **https://mac-mini-di-samuele.tail1b5bf3.ts.net/benessere/musica/**;
- legge i brani dal disco principale e, se quello manca, dal backup;
- mostra le playlist del disco e quelle della vecchia radio;
- ha una scheda **Benessere** (con il cuore) in basso. Puoi usare Benessere mentre la musica continua a suonare.

**Cosa non toccare:** l'rsync che sta copiando su Cippo2, la vecchia pagina radio (:8443) e
`~/.cloud-web/playlist.json` (la musica lo legge e basta). L'app legge i dischi e non ci scrive niente.
Indice e cache stanno in `~/.atlante-musica`.

Ci vogliono 10 minuti. Se un passo non va come scritto, fermati e torna indietro (punto 8).

---

## 1. Copia di sicurezza di server.py

```sh
cp ~/.cloud-web/benessere/server.py ~/.cloud-web/benessere/server.py.prima-della-musica
```

## 2. Copia i file

I file stanno nella cartella `musica/` di questo repository (branch `fase2-mac`). Metti in `REPO`
il percorso del repository sul Mac:

```sh
REPO="/percorso/del/repository"          # da cambiare
# il modulo della musica e la sua prova, accanto a server.py
cp "$REPO/musica/server/musica_libreria.py" "$REPO/musica/server/prova_musica.py" ~/.cloud-web/benessere/
# l'app (solo questi file, niente cartella server/)
mkdir -p ~/.cloud-web/musica-app
cp "$REPO"/musica/{index.html,sw.js,manifest.webmanifest,icon-192.png,icon-512.png,icon-maskable-512.png,apple-touch-icon.png} ~/.cloud-web/musica-app/
```

## 3. Fai la prova prima di toccare Benessere

Usa lo stesso Python di Benessere, cioè `/usr/bin/python3` (3.9) con `-I`:

```sh
cd ~/.cloud-web/benessere && /usr/bin/python3 -I prova_musica.py
```

La prova crea due dischi finti in una cartella temporanea e alla fine li cancella. Non tocca i dischi veri.
L'ultima riga deve essere **`TUTTO OK`**. Se ci sono errori, fermati qui.

## 4. Prima lettura dei dischi veri (consigliata)

Questo passo legge soltanto i dischi e non tocca Benessere. Prepara `~/.atlante-musica/indice.json`,
così quando Benessere riparte la musica c'è subito. La prima volta ci vogliono alcuni minuti:
si leggono i tag di circa 9.400 file. Le copie uguali sul backup non vengono rilette.

```sh
cd ~/.cloud-web/benessere && /usr/bin/python3 -I -c '
import sys; sys.path.insert(0, ".")
import musica_libreria as atlante_musica
l = atlante_musica.Libreria(["/Volumes/Cloud/Musica Cloud/Da YouTube", "/Volumes/Cippo2/Backup Cloud/Musica Cloud/Da YouTube"],
                            "~/.atlante-musica", playlist_cartelle=["/Volumes/Cloud/Musica Cloud/Playlist"],
                            playlist_json="~/.cloud-web/playlist.json", collegamenti=[{"nome": "Benessere", "url": "../"}])
print("brani:", l.scansiona())
c = l.catalogo()
print("doppioni uniti:", len(c["alias"]), "| assenti:", sum(1 for b in c["brani"] if b.get("assente")),
      "| senza durata:", sum(1 for b in c["brani"] if not b["duration"]))
for p in c["playlist"]: print(" ", p["fonte"], repr(p["nome"]), len(p["brani"]), "brani")
print("playlist della radio fatte da:", sorted({p.get("_di") or "?" for p in l.dati.playlist if p["fonte"] == "radio"}))
k = [r["cpos"][1] for r in l.record.values() if r.get("cpos")]
print("copertine negli mp3:", len(k), "| media KB:", sum(k) // max(1, len(k)) // 1024, "| oltre 200 KB:", sum(1 for x in k if x > 200 * 1024))
'
```

Cosa aspettarsi:

- **brani**: circa 9.400, cioè i 6.392 del disco principale più i circa 3.000 che stanno solo sul backup;
- **assenti**: 0 se i due dischi sono collegati;
- **playlist**: una per ogni cartella in `Playlist` più le 7 della radio;
- **playlist della radio fatte da**: i login Tailscale di chi le ha fatte (vedi «Da sapere» in fondo);
- **copertine negli mp3**: quante sono e quanto pesano; quelle oltre 200 KB l'app le riceve ridotte (vedi «Da sapere»).

Due avvisi:

- Se esce `Operation not permitted`, macOS non lascia leggere i dischi esterni a Python: va dato il permesso in
  Impostazioni di Sistema › Privacy e sicurezza.
- Se `brani` è molto più basso del previsto, fermati e controlla.

## 5. Le righe da aggiungere a `~/.cloud-web/benessere/server.py`

Prima controlla che i nomi `atlante_musica` e `APP_MUSICA` non siano già usati:
`grep -nw -e atlante_musica -e APP_MUSICA server.py` non deve trovare niente. (I nomi sono lunghi apposta: un nome
corto come `ml` potrebbe già esserci in Benessere, per esempio per i millilitri dell'acqua.)

**5a. In cima, subito dopo gli `import`.** Questo blocco viene eseguito una sola volta, all'avvio:

```python
# --- Atlante Musica: la musica dentro Benessere ---
import os
import sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # con python3 -I la cartella di server.py non è nel percorso dei moduli
try:
    import musica_libreria as atlante_musica   # NON chiamarlo "gestisci": il Gestore ha già un metodo con quel nome
    atlante_musica.avvia(["/Volumes/Cloud/Musica Cloud/Da YouTube", "/Volumes/Cippo2/Backup Cloud/Musica Cloud/Da YouTube"],
                         "~/.atlante-musica",
                         playlist_cartelle=["/Volumes/Cloud/Musica Cloud/Playlist"],
                         playlist_json="~/.cloud-web/playlist.json",
                         collegamenti=[{"nome": "Benessere", "url": "../"}],
                         radio_per_persona=True)   # vedi 5e
except Exception as e:   # la musica non deve mai impedire a Benessere di partire
    atlante_musica = None
    print("Musica non disponibile:", e, flush=True)
APP_MUSICA = os.path.expanduser("~/.cloud-web/musica-app")
# --- fine Atlante Musica ---
```

`atlante_musica.avvia` torna subito. Carica l'indice salvato e poi rilegge i dischi in sottofondo: subito, poi ogni
10 minuti, e in più appena un disco viene collegato o staccato (lo guarda ogni 2 secondi).

**5b. Nel metodo `gestisci` della classe `Gestore`**, quello chiamato da `do_GET` con `self.gestisci(post=False)`.
Metti il blocco subito dopo la riga `percorso = unquote(urlsplit(self.path).path)` e **dentro il `try:`**,
prima dei file statici e prima del controllo `/api/`. Deve stare dentro il `try` perché così
`self.persona()` senza login Tailscale diventa una risposta 403, come per il resto di Benessere.
Se il `try:` comincia dopo la riga di `percorso`, metti il blocco come prime righe dentro il `try:`.

```python
            # --- Atlante Musica ---
            if not post and atlante_musica and (percorso == "/musica" or percorso.startswith("/musica/")):
                self.persona()   # 403 senza login Tailscale
                atlante_musica.servi_app(self, percorso, app=APP_MUSICA)
                return
            # --- fine Atlante Musica ---
```

Come risulta, per esempio (le altre righe sono quelle che ci sono già):

```python
    def gestisci(self, post=False):
        percorso = unquote(urlsplit(self.path).path)
        try:
            # --- Atlante Musica ---
            if not post and atlante_musica and (percorso == "/musica" or percorso.startswith("/musica/")):
                self.persona()   # 403 senza login Tailscale
                atlante_musica.servi_app(self, percorso, app=APP_MUSICA)
                return
            # --- fine Atlante Musica ---
            ...statici di Benessere...
            if not percorso.startswith("/api/"):
                raise Rifiuto(404 ...)
            ...
        except Rifiuto as r:
            ...
```

Tailscale toglie `/benessere`, quindi `/benessere/musica/...` arriva qui come `/musica/...`. Cosa fa `servi_app`:

- per `/musica` risponde con un redirect a `musica/`;
- serve l'app e le sue API sotto `/musica/`;
- non serve niente fuori da `~/.cloud-web/musica-app`.

**5c. (Facoltativo) HEAD.** All'app non serve. Aggiungilo solo se il `Gestore` non ha `do_HEAD`
e lo vuoi per provare con `curl -I`:

```python
    def do_HEAD(self):
        percorso = unquote(urlsplit(self.path).path)
        if atlante_musica and (percorso == "/musica" or percorso.startswith("/musica/")) and self.headers.get("Tailscale-User-Login"):
            atlante_musica.servi_app(self, percorso, app=APP_MUSICA)
        else:
            self.send_error(405)
```

**5d. Modalità «incorporata».** Guarda come la vecchia pagina radio mette Benessere nell'iframe
(cerca `iframe` nella radio). Se usa un parametro, per esempio `…/benessere/?incorporata=1`, metti lo stesso
parametro nell'url del collegamento al punto 5a: `{"nome": "Benessere", "url": "../?incorporata=1"}`.
L'url è relativo all'app (`/benessere/musica/`), quindi `../` è `/benessere/`.
Il CSP di Benessere (`frame-ancestors 'self' …`) va già bene, perché app e Benessere stanno sullo stesso indirizzo.

**5e. Playlist della radio: chi vede quali.** Ogni playlist di `~/.cloud-web/playlist.json` ha il campo `di`, il login
Tailscale di chi l'ha fatta (il punto 4 li ha stampati: «playlist della radio fatte da»). Guarda nel codice della
vecchia radio come usa `di` (cerca `"di"`):

- se ognuno vede solo le sue playlist, lascia `radio_per_persona=True` al punto 5a: nell'app sarà lo stesso;
- se tutti vedono tutte le playlist, metti `radio_per_persona=False`.

Se non trovi il codice della radio o non è chiaro, lascia `True` e dillo a Samuele.

Controlla che il file non abbia errori:

```sh
/usr/bin/python3 -I -m py_compile ~/.cloud-web/benessere/server.py && echo "server.py ok"
```

## 6. Riavvia Benessere

Prima trova come viene avviato Benessere. Il nome del servizio di launchd (l'«etichetta») non è scritto qui:
va letto sul Mac.

```sh
launchctl list | grep -i benessere
grep -l "benessere" ~/Library/LaunchAgents/*.plist /Library/LaunchAgents/*.plist 2>/dev/null
```

- Il primo comando stampa una riga come `812  0  qualcosa.benessere`: l'etichetta è l'**ultima colonna**.
  Se non stampa niente, apri il file `.plist` trovato dal secondo comando: l'etichetta è il testo sotto `<key>Label</key>`.
- Mettila nella variabile `ETICHETTA` e riavvia (`-k` ferma Benessere e lo fa ripartire subito):

```sh
ETICHETTA="etichetta.trovata.sopra"          # da cambiare
launchctl kickstart -k gui/$(id -u)/"$ETICHETTA"
```

**Se nessuno dei due comandi trova niente**, Benessere non è avviato da launchd. Allora guarda come gira adesso:
`ps -axo pid,command | grep "[b]enessere/server.py"`. Fermalo con `kill <pid>` (il numero della prima colonna) e
fallo ripartire con **lo stesso comando** che mostra `ps`, dalla cartella `~/.cloud-web/benessere`.
Se non è chiaro come ripartirlo, non fermarlo: chiedi a Samuele.

## 7. Controlla

Per vedere dove scrive il log: `launchctl print gui/$(id -u)/"$ETICHETTA" | grep -i path` (con l'etichetta del punto 6).
Nel log devono comparire `Musica: indice caricato: … brani` e, dopo poco, `Musica: N brani, M playlist (letti in … s)`.

```sh
# senza login Tailscale: 403 (giusto)
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8093/musica/
# con login: 301, poi 200
curl -s -o /dev/null -w '%{http_code}\n' -H 'Tailscale-User-Login: prova' http://127.0.0.1:8093/musica
curl -s -o /dev/null -w '%{http_code}\n' -H 'Tailscale-User-Login: prova' http://127.0.0.1:8093/musica/
# lo stato della musica
curl -s -H 'Tailscale-User-Login: prova' http://127.0.0.1:8093/musica/api/musica/stato; echo
```

Poi apri **https://mac-mini-di-samuele.tail1b5bf3.ts.net/benessere/** e controlla che Benessere funzioni come
prima. Poi apri **https://mac-mini-di-samuele.tail1b5bf3.ts.net/benessere/musica/**:

- in **Libreria** c'è «Musica del Mac: N brani» con il tasto **Aggiorna**. Aggiorna dice
  «Rileggo il disco… N brani» e poi «Libreria aggiornata»;
- in **Libreria › Playlist** ci sono le playlist con scritto sotto «Dal disco» o «Dalla radio»;
- in basso c'è la scheda **Benessere** con il cuore. Fai partire un brano e passa a Benessere:
  la musica continua e il mini player resta visibile;
- se il disco principale non è collegato, i brani che stanno solo lì e non sono sull'iPhone diventano grigi.
  Gli altri arrivano dal backup.

Per Samuele, sull'iPhone: apri l'indirizzo in Safari, poi Condividi › **Aggiungi alla schermata Home**.

## 8. Tornare indietro

```sh
cp ~/.cloud-web/benessere/server.py.prima-della-musica ~/.cloud-web/benessere/server.py
ETICHETTA="etichetta.trovata.al.punto.6"     # la stessa del punto 6
launchctl kickstart -k gui/$(id -u)/"$ETICHETTA"
```

Se Benessere non è avviato da launchd (vedi punto 6), dopo il `cp` riavvialo come al punto 6, con lo stesso comando di prima.
Se ti sei fermato prima del punto 6, basta il `cp`: Benessere non è mai stato riavviato e gira ancora com'era.

Benessere torna come prima. I file copiati non danno fastidio e si possono lasciare:
`musica_libreria.py`, `prova_musica.py`, `~/.cloud-web/musica-app` e `~/.atlante-musica`.
La radio e l'rsync non sono mai stati toccati.

## Da sapere

- **Ogni quanto rilegge i dischi.** Ogni 10 minuti la musica controlla nomi e dimensioni dei file e legge i tag
  solo di quelli nuovi o cambiati. Se Samuele non vuole che il disco di backup si svegli così spesso,
  aggiungi `intervallo=3600` in `atlante_musica.avvia(...)` (una volta all'ora). Il tasto Aggiorna rilegge comunque subito.
- **Cosa viene saltato.** Link simbolici, file e cartelle che cominciano col punto (come `.atlante-tmp` dell'rsync)
  e file vuoti. Le cartelle `Playlist` su Cippo2 non vengono usate.
- **Copertine.** Arrivano dagli MP3. Quelle grandi (oltre 200 KB, come le PNG che yt-dlp mette negli mp3) vengono
  ridotte una volta sola con `/usr/bin/sips`, che c'è di serie sul Mac, e tenute in `~/.atlante-musica/copertine`
  (20-40 KB l'una invece di 0,5-1,5 MB): così sull'iPhone la libreria non consuma la rete. Quanto pesano quelle
  negli mp3 lo stampa il punto 4 («copertine negli mp3»).
- **Disco scollegato.** Se un disco non è collegato, i suoi brani restano nel catalogo con «assente» e tornano
  normali appena il disco torna (la musica se ne accorge in pochi secondi, anche col Mac appena riacceso e i dischi
  montati dopo). Gli id dei brani non cambiano. Così i brani salvati sull'iPhone e le playlist restano a posto.
- **Playlist della radio.** Con `radio_per_persona=True` ognuno vede solo quelle che ha fatto lui: il campo `di` di
  `playlist.json` si confronta con il login Tailscale di chi apre l'app. Con `False` tutti vedono tutte (vedi 5e).
