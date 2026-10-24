#!/bin/bash
# Aggiornamento da remoto di Atlante Musica sul Mac mini.
# Gira ogni 10 minuti (LaunchAgent it.samuele.atlante-musica-aggiorna): se sul ramo main del repo
# samuelemanfredi/atlante la cartella musica/ è cambiata, la scarica, fa le prove del server e solo
# se passano tutte e due (test_extra.py: API dei moduli; prova_musica.py: dischi, doppioni, playlist,
# migrazione degli id e avvio come servizio) la mette al posto di quella installata e riavvia il server.
# Se una prova fallisce resta la versione di prima. L'app sull'iPhone prende la versione nuova alla prossima apertura.
# Dopo un'installazione riuscita si aggiorna anche questo script (se la versione nuova è diversa e senza errori di sintassi).
set -u
REPO="${ATLANTE_REPO:-$HOME/Atlante/repo}"         # copia del repo usata solo per gli aggiornamenti
APP="${ATLANTE_APP:-$HOME/Atlante/musica}"         # app installata (quella servita)
RAMO="${ATLANTE_RAMO:-main}"
SERVIZIO="${ATLANTE_SERVIZIO:-it.samuele.atlante-musica-app}"
LOG="$HOME/Atlante/registro.log"
TEMPO_PROVE="${ATLANTE_TEMPO_PROVE:-600}"         # secondi massimi per ogni prova (di solito bastano pochi secondi)
QUESTO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"   # questo script (prima di ogni cd)
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
# le prove girano con il Python del servizio (il primo argomento del suo plist, di solito /usr/bin/python3, cioè 3.9), non con
# il primo python3 del PATH: con Homebrew sarebbe un 3.1x e passerebbe una versione che il servizio poi non riesce a far girare
PY="${ATLANTE_PYTHON:-}"
if [ -z "$PY" ]; then
  PY=$(plutil -extract ProgramArguments.0 raw -o - "$HOME/Library/LaunchAgents/$SERVIZIO.plist" 2>/dev/null)
  case "$PY" in /*python*) [ -x "$PY" ] || PY="" ;; *) PY="" ;; esac
  PY="${PY:-/usr/bin/python3}"
fi

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') aggiorna: $*" >> "$LOG"; }

# un giro alla volta: mkdir è atomico e c'è ovunque (lockf e flock non sono sicuri su ogni macOS). Un lucchetto rimasto
# da un giro interrotto (Mac spento a metà) si toglie dopo un'ora
LUCCHETTO="$HOME/Atlante/.aggiorna.lucchetto"
if ! mkdir "$LUCCHETTO" 2>/dev/null; then
  [ -n "$(find "$LUCCHETTO" -maxdepth 0 -mmin +60 2>/dev/null)" ] && rm -rf "$LUCCHETTO" && mkdir "$LUCCHETTO" 2>/dev/null || exit 0
fi
PROVA=""
trap 'rm -rf "$LUCCHETTO" ${PROVA:+"$PROVA"}' EXIT

if [ ! -d "$REPO/.git" ]; then
  git clone -q --filter=blob:none https://github.com/samuelemanfredi/atlante.git "$REPO" || { log "clone non riuscito"; exit 1; }
fi
cd "$REPO" || exit 1
git fetch -q origin "$RAMO" || { log "fetch non riuscito"; exit 1; }
NUOVO=$(git rev-parse "origin/$RAMO")
ATTUALE=$(sed -n 's/^commit //p' "$APP/VERSIONE.txt" 2>/dev/null | head -1)
[ "$NUOVO" = "$ATTUALE" ] && exit 0
# niente da fare se musica/ non è cambiata
if [ -n "$ATTUALE" ] && git cat-file -e "$ATTUALE" 2>/dev/null && git diff --quiet "$ATTUALE" "$NUOVO" -- musica; then
  printf 'commit %s\nramo %s\naggiornato %s\n' "$NUOVO" "$RAMO" "$(date '+%Y-%m-%d %H:%M')" > "$APP/VERSIONE.txt"
  exit 0
fi

PROVA=$(mktemp -d "$HOME/Atlante/.prova.XXXX") || exit 1
git archive "$NUOVO" musica | tar -x -C "$PROVA" || { log "estrazione non riuscita"; exit 1; }
mkdir -p "$PROVA/tmp"

# una prova del server: $PY -I -B (niente __pycache__), file temporanei solo dentro $PROVA (si cancella alla fine),
# al massimo TEMPO_PROVE secondi (macOS non ha `timeout`): se si blocca si ferma con i processi che ha avviato e conta come fallita
set -m   # ogni prova in un gruppo di processi suo, così si ferma tutta insieme
prova() {
  local nome="$1" t=0 pid esito
  (cd "$PROVA/musica/server" && TMPDIR="$PROVA/tmp" exec "$PY" -I -B "$nome") >"$PROVA/$nome.txt" 2>&1 &
  pid=$!
  while kill -0 "$pid" 2>/dev/null; do
    if [ "$t" -ge "$TEMPO_PROVE" ]; then
      kill -TERM -- "-$pid" 2>/dev/null; sleep 2; kill -KILL -- "-$pid" 2>/dev/null
      echo "prova fermata dopo $TEMPO_PROVE s" >> "$PROVA/$nome.txt"
      wait "$pid" 2>/dev/null
      return 1
    fi
    sleep 1; t=$((t + 1))
  done
  wait "$pid"; esito=$?
  return "$esito"
}
for P in test_extra.py prova_musica.py; do
  if ! prova "$P"; then
    log "prove fallite su ${NUOVO:0:7} ($P): resta la versione ${ATTUALE:0:7} (prove con $PY)"; tail -5 "$PROVA/$P.txt" >> "$LOG"; exit 1
  fi
done
set +m

VECCHIA="$APP.precedente"
rm -rf "$VECCHIA"
cp -R "$APP" "$VECCHIA"
rsync -a --delete --exclude VERSIONE.txt "$PROVA/musica/" "$APP/"
printf 'commit %s\nramo %s\naggiornato %s\n' "$NUOVO" "$RAMO" "$(date '+%Y-%m-%d %H:%M')" > "$APP/VERSIONE.txt"
launchctl kickstart -k "gui/$(id -u)/$SERVIZIO"
sleep 5
if ! curl -s -m 10 -o /dev/null "http://127.0.0.1:${ATLANTE_PORTA:-8790}/"; then
  log "il server non risponde con ${NUOVO:0:7}: torno a ${ATTUALE:0:7}"
  rsync -a --delete "$VECCHIA/" "$APP/"
  launchctl kickstart -k "gui/$(id -u)/$SERVIZIO"
  exit 1
fi
log "installata ${NUOVO:0:7} (prima ${ATTUALE:0:7})"

# questo script: se nella versione installata è cambiato, si sostituisce (mv: la copia che sta girando resta intatta)
NUOVO_SCRIPT="$APP/server/aggiorna.sh"
case "$QUESTO" in "$APP"/*) exit 0 ;; esac
if [ -f "$NUOVO_SCRIPT" ] && ! cmp -s "$NUOVO_SCRIPT" "$QUESTO"; then
  if bash -n "$NUOVO_SCRIPT" 2>/dev/null && cp "$NUOVO_SCRIPT" "$QUESTO.nuovo" && chmod 755 "$QUESTO.nuovo" && mv -f "$QUESTO.nuovo" "$QUESTO"; then
    log "aggiornato anche aggiorna.sh"
  else
    rm -f "$QUESTO.nuovo"; log "aggiorna.sh nuovo non installato: resta quello di prima"
  fi
fi
