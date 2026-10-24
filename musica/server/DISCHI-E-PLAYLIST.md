# Musica: due dischi e playlist sul Mac mini

Queste istruzioni sono per la sessione di Claude sul Mac mini di Samuele. L'app Musica gira già come servizio
**it.samuele.atlante-musica-app** (porta 8790, dietro `tailscale serve`) e si aggiorna da sola dal ramo `main`
con `~/Atlante/aggiorna.sh`. Qui si aggiungono al servizio gli argomenti per:

- leggere **i due dischi**: prima il principale, poi il backup, senza doppioni (un brano che sta su tutti e due compare
  una volta sola e, se il principale manca, arriva dal backup);
- mostrare **le playlist del disco** (`/Volumes/Cloud/Musica Cloud/Playlist`, una cartella per playlist con i link ai brani);
- mostrare **le playlist della vecchia radio** (`~/.cloud-web/playlist.json`).

Benessere non si tocca. Non si tocca nemmeno l'rsync che copia su Cippo2, la vecchia pagina radio e `playlist.json`:
la musica legge i dischi e quel file, non ci scrive niente. Indice e copertine stanno in `~/.atlante-musica`.

Fai tutto tu, un passo alla volta. Se un passo non va come scritto, torna indietro (punto 7) e prova un'altra strada.
A Samuele va chiesto solo un permesso di macOS, se serve (punto 5).

---

## 1. La versione nuova è installata?

```sh
cat ~/Atlante/musica/VERSIONE.txt
ls ~/Atlante/musica/server/prova_musica.py ~/Atlante/musica/server/DISCHI-E-PLAYLIST.md
tail -5 ~/Atlante/registro.log
```

Se `prova_musica.py` non c'è, la versione con i due dischi non è ancora arrivata: lancia `~/Atlante/aggiorna.sh`,
aspetta che finisca e guarda di nuovo `registro.log` (deve dire `installata …`).

## 2. Aggiorna lo script di aggiornamento (una volta sola)

Da questa versione `aggiorna.sh` installa una versione nuova solo se passano **tutte e due** le prove:
`test_extra.py` (moduli della scheda Altro) e `prova_musica.py` (dischi, doppioni, playlist), fatte con lo stesso Python
del servizio (il primo argomento del suo plist). Così una versione con i doppioni rotti non arriva mai sul Mac. Lo script che gira adesso è quello vecchio: va sostituito una volta, poi
si aggiorna da solo.

```sh
bash -n ~/Atlante/musica/server/aggiorna.sh && cp ~/Atlante/aggiorna.sh ~/Atlante/aggiorna.sh.prima \
  && cp ~/Atlante/musica/server/aggiorna.sh ~/Atlante/aggiorna.sh.nuovo && chmod 755 ~/Atlante/aggiorna.sh.nuovo \
  && mv -f ~/Atlante/aggiorna.sh.nuovo ~/Atlante/aggiorna.sh && grep -c prova_musica ~/Atlante/aggiorna.sh
```

L'ultimo comando deve stampare un numero maggiore di 0.

## 3. Fai la prova

Usa lo stesso Python del servizio, cioè `/usr/bin/python3` (3.9), con `-I -B`:

```sh
cd ~/Atlante/musica/server && /usr/bin/python3 -I -B prova_musica.py | tail -3
```

Dura pochi secondi. Crea due dischi finti in una cartella temporanea, avvia il server come lo avvierà il servizio
(due dischi, playlist, `playlist.json`, moduli extra) e alla fine cancella tutto. Non tocca i dischi veri.
L'ultima riga deve essere **`TUTTO OK`**. Se no, fermati qui: il servizio è ancora com'era.

## 4. Gli argomenti da aggiungere al servizio

Il file è `~/Library/LaunchAgents/it.samuele.atlante-musica-app.plist`. Guarda com'è adesso:

```sh
plutil -p ~/Library/LaunchAgents/it.samuele.atlante-musica-app.plist
```

Gli argomenti di `musica_libreria.py` diventano:

| Argomento | Valore |
|---|---|
| cartelle (subito dopo `musica_libreria.py`, **in quest'ordine**) | `/Volumes/Cloud/Musica Cloud/Da YouTube` poi `/Volumes/Cippo2/Backup Cloud/Musica Cloud/Da YouTube` |
| `--playlist` | `/Volumes/Cloud/Musica Cloud/Playlist` |
| `--playlist-json` | `~/.cloud-web/playlist.json` (il server sa leggere `~`) |

Tutto il resto che c'è già (`--porta 8790`, `--indirizzo`, `--cache`, `--app`, `--video-dir`, `--libri-dir`, `--ia-…`,
la variabile `ATLANTE_ASCOLTO`) resta com'è. Le cartelle di prima (per esempio solo il backup) si tolgono:
le due nuove le sostituiscono.

Esempio di `ProgramArguments` completo, se il servizio prima aveva solo la porta (con l'utente `samuelemanfredi`:
controlla il tuo con `echo $HOME`, e la riga dello script con il percorso che vedi in `plutil -p`):

```xml
<key>ProgramArguments</key>
<array>
  <string>/usr/bin/python3</string>
  <string>-I</string>
  <string>-B</string>
  <string>/Users/samuelemanfredi/Atlante/musica/server/musica_libreria.py</string>
  <string>/Volumes/Cloud/Musica Cloud/Da YouTube</string>
  <string>/Volumes/Cippo2/Backup Cloud/Musica Cloud/Da YouTube</string>
  <string>--porta</string>
  <string>8790</string>
  <string>--playlist</string>
  <string>/Volumes/Cloud/Musica Cloud/Playlist</string>
  <string>--playlist-json</string>
  <string>~/.cloud-web/playlist.json</string>
</array>
```

Invece di scriverlo a mano puoi usare questo comando: fa una copia del file (`.prima-dei-dischi`, solo la prima volta:
se lo rilanci la copia resta quella con gli argomenti di prima), tiene interprete, script e tutte le opzioni che ci sono
(anche un `--collegamento` già aggiunto), sostituisce le cartelle e mette le playlist. Si può rilanciare senza danni.
Se non riconosce la forma del file non cambia niente e lo dice: allora modificalo a mano come nell'esempio.

```sh
/usr/bin/python3 -I - <<'EOF'
import os, plistlib, shutil
f = os.path.expanduser("~/Library/LaunchAgents/it.samuele.atlante-musica-app.plist")
with open(f, "rb") as x:
    d = plistlib.load(x)
a = d.get("ProgramArguments") or []
i = next((k for k, v in enumerate(a) if v.endswith("musica_libreria.py")), None)
if i is None:
    raise SystemExit("musica_libreria.py non è tra gli argomenti: modifica il file a mano (vedi l'esempio)")
via = {"--playlist", "--playlist-json"}
resto, k = [], i + 1
while k < len(a):
    v = a[k]
    if v.startswith("--"):
        nome = v.split("=", 1)[0]
        con_valore = "=" not in v and nome != "--radio-per-persona"
        if nome not in via:
            resto += a[k:k + 2] if con_valore else [v]
        k += 2 if con_valore else 1
    else:
        print("tolgo la cartella di prima:", v)
        k += 1
d["ProgramArguments"] = a[:i + 1] + ["/Volumes/Cloud/Musica Cloud/Da YouTube",
                                     "/Volumes/Cippo2/Backup Cloud/Musica Cloud/Da YouTube"] + resto + [
    "--playlist", "/Volumes/Cloud/Musica Cloud/Playlist", "--playlist-json", "~/.cloud-web/playlist.json"]
if os.path.exists(f + ".prima-dei-dischi"):
    print("la copia .prima-dei-dischi c'è già: resta quella (argomenti di prima)")
else:
    shutil.copy2(f, f + ".prima-dei-dischi")
with open(f, "wb") as x:
    plistlib.dump(d, x)
print("\n".join(d["ProgramArguments"]))
EOF
plutil -lint ~/Library/LaunchAgents/it.samuele.atlante-musica-app.plist
```

**Facoltativi**, da aggiungere solo se servono:

- `--collegamento` `Benessere del Mac=/benessere/`: nella scheda **Altro** dell'app compare la tessera
  «Benessere del Mac» (sotto «Dal Mac») che apre la pagina di Benessere nello stesso riquadro dei moduli, con la
  musica che continua. Il nome non deve essere solo «Benessere»: così si chiama già il modulo della scheda Altro
  (Calorie, Settimana, Trainer), che è un'altra pagina, e ci sarebbero due tessere uguali.
  Prima guarda con `tailscale serve status` dove sta Benessere: se è `/benessere` sullo stesso indirizzo dell'app,
  l'url è `/benessere/`; se la vecchia radio lo apre con un parametro (cerca `iframe` nel codice della radio, per
  esempio `?incorporata=1`), mettilo anche qui: `Benessere del Mac=/benessere/?incorporata=1`. Se Benessere non è
  sullo stesso indirizzo, non metterlo.
- `--radio-per-persona`: delle playlist della radio ognuno vede solo le sue (il campo `di` di `playlist.json` è il
  login Tailscale di chi l'ha fatta). Mettilo solo se la vecchia radio fa così (cerca `"di"` nel suo codice).
  Senza, tutti vedono tutte.
- `--intervallo 3600`: rilegge i dischi una volta all'ora invece che ogni 10 minuti, se il backup non deve
  svegliarsi così spesso. Il tasto Aggiorna dell'app rilegge comunque subito.

## 5. Riavvia il servizio con gli argomenti nuovi

`kickstart` da solo non rilegge il file: il servizio va tolto e rimesso.

```sh
P=~/Library/LaunchAgents/it.samuele.atlante-musica-app.plist
launchctl bootout gui/$(id -u)/it.samuele.atlante-musica-app; sleep 2
launchctl bootstrap gui/$(id -u) "$P"
launchctl print gui/$(id -u)/it.samuele.atlante-musica-app | grep -E 'state|path|arguments' | head
```

Il server parte subito dall'indice salvato e rilegge i dischi in sottofondo. La prima volta legge i tag dei file che
non conosce ancora, e dipende da cosa leggeva prima:

- **solo il principale**: i circa 3.000 file che stanno solo sul backup (le copie sul backup dei brani del principale
  non si rileggono);
- **solo il backup** (il caso dell'esempio del punto 4): tutti i 6.392 file del principale, perché il principale si
  legge per primo e non ha ancora niente da cui copiare (quelli del backup restano come sono). Ci vuole circa il
  doppio: è normale.

Con dischi USB sono alcuni minuti; `stato` dice a che punto è (`letti`). Intanto l'app funziona.
Se nel log compare `Operation not permitted`, macOS non lascia leggere un disco esterno a Python: serve il permesso
in Impostazioni di Sistema › Privacy e sicurezza (è l'unica cosa da chiedere a Samuele).

## 6. Controlla

```sh
curl -s http://127.0.0.1:8790/api/musica/stato; echo
curl -s 'http://127.0.0.1:8790/api/musica/catalogo?v=2' | /usr/bin/python3 -I -c '
import json, sys
c = json.load(sys.stdin)
print("brani:", len(c["brani"]), "| assenti:", sum(1 for b in c["brani"] if b.get("assente")), "| letto:", c["aggiornato"])
for p in c["playlist"]: print(" ", p["fonte"], repr(p["nome"]), len(p["brani"]), "brani")
print("collegamenti:", c.get("collegamenti"))'
```

Cosa aspettarsi, a lettura finita (`stato` non dice più `in_corso: true`):

- **brani**: circa 9.400, cioè i 6.392 del disco principale più i circa 3.000 che stanno solo sul backup;
- **assenti**: 0 se i due dischi sono collegati;
- **playlist**: una per ogni cartella in `Playlist` (fonte `cartella`) più quelle della radio (fonte `radio`).

Se `brani` resta molto più basso del previsto, guarda il log del servizio (il percorso lo dà `launchctl print`, riga
`path`) e torna indietro (punto 7).

Sull'iPhone, all'indirizzo di sempre: in **Libreria › Playlist** ci sono le playlist con scritto sotto «Dal disco» o
«Dalla radio»; se un disco è staccato, i brani che stanno solo lì e non sono sull'iPhone diventano grigi, gli altri
arrivano dall'altro disco. Preferiti, playlist e brani salvati restano: anche se gli id dei brani sono cambiati
rispetto alla versione di prima, il server dice all'app quali id vecchi corrispondono a quali nuovi.
Un iPhone che ha ancora in memoria l'app di prima (che chiede `catalogo` senza `?v=2`) riceve i brani con gli id di
prima: così non toglie niente da preferiti, playlist e coda dei download finché non si apre l'app nuova, che chiede
`catalogo?v=2` e sposta tutto sugli id nuovi.

## 7. Tornare indietro

Gli argomenti di prima:

```sh
P=~/Library/LaunchAgents/it.samuele.atlante-musica-app.plist
cp "$P.prima-dei-dischi" "$P"          # oppure rimetti a mano le righe di prima
launchctl bootout gui/$(id -u)/it.samuele.atlante-musica-app; sleep 2
launchctl bootstrap gui/$(id -u) "$P"
```

Lo script di aggiornamento di prima: `mv -f ~/Atlante/aggiorna.sh.prima ~/Atlante/aggiorna.sh`.

Se invece è la versione nuova del server a non andare (non gli argomenti): `aggiorna.sh` tiene la versione di prima
in `~/Atlante/musica.precedente`. Ferma gli aggiornamenti automatici, altrimenti tra 10 minuti la rimettono, e rimetti
quella:

```sh
launchctl bootout gui/$(id -u)/it.samuele.atlante-musica-aggiorna
rsync -a --delete ~/Atlante/musica.precedente/ ~/Atlante/musica/
launchctl kickstart -k gui/$(id -u)/it.samuele.atlante-musica-app
```

Gli aggiornamenti ripartono con
`launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/it.samuele.atlante-musica-aggiorna.plist`.
La versione di prima non conosce `--playlist` e `--playlist-json` e non partirebbe: rimetti anche gli argomenti di
prima (vedi sopra). Indice e copertine in `~/.atlante-musica` possono restare.

## Da sapere

- **Ogni quanto rilegge i dischi.** Ogni 10 minuti (o `--intervallo`) controlla nomi e dimensioni dei file e legge i
  tag solo di quelli nuovi o cambiati; in più guarda ogni 2 secondi se un disco è stato collegato o staccato.
- **Cosa viene saltato.** Link simbolici, file e cartelle che cominciano col punto (come `.atlante-tmp` dell'rsync) e
  file vuoti. Le cartelle `Playlist` su Cippo2 non vengono usate.
- **Copertine.** Arrivano dagli MP3. Quelle grandi (oltre 200 KB, come le PNG che yt-dlp mette negli mp3) vengono
  ridotte una volta sola con `/usr/bin/sips` e tenute in `~/.atlante-musica/copertine`.
- **Disco scollegato.** I suoi brani restano nel catalogo come «assenti» e tornano normali appena il disco torna.
  Gli id non cambiano, così brani salvati e playlist sull'iPhone restano a posto.
- **Doppioni.** Lo stesso brano sui due dischi (stesso percorso, oppure stessi tag e durata) compare una volta sola;
  un file rinominato o spostato prende un id nuovo, e l'app sposta da sola preferiti, playlist e brani salvati.
