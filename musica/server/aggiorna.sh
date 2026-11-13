#!/bin/bash
# Aggiornamento da remoto di Atlante Musica sul Mac mini.
# Gira ogni 10 minuti (LaunchAgent it.samuele.atlante-musica-aggiorna): se sul ramo main del repo
# samuelemanfredi/atlante la cartella musica/ è cambiata, la scarica, fa le prove del server e solo
# se passano la mette al posto di quella installata e riavvia il server. Se le prove falliscono resta
# la versione di prima. L'app sull'iPhone prende la versione nuova alla prossima apertura.
# Mentre qualcuno ascolta (il server dice quanti dispositivi suonano: api/extra/suona) non si installa niente: il
# riavvio del server fermerebbe la musica in streaming (13/11). Si riprova al giro dopo; dopo 6 ore si installa lo stesso.
# Il server, all'avvio, mette questo file al posto di ~/Atlante/aggiorna.sh se la versione qui sotto è più nuova.
# aggiorna-versione: 2
set -u
REPO="${ATLANTE_REPO:-$HOME/Atlante/repo}"         # copia del repo usata solo per gli aggiornamenti
APP="${ATLANTE_APP:-$HOME/Atlante/musica}"         # app installata (quella servita)
RAMO="${ATLANTE_RAMO:-main}"
SERVIZIO="${ATLANTE_SERVIZIO:-it.samuele.atlante-musica-app}"
LOG="$HOME/Atlante/registro.log"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') aggiorna: $*" >> "$LOG"; }

PORTA="${ATLANTE_PORTA:-8790}"
RIMANDATO="$HOME/Atlante/.aggiorna.rimandato"   # da quando si aspetta (secondi), finché qualcuno ascolta
# vero (0) se adesso c'è musica che suona: allora si aspetta. Se il server non risponde, o risponde altro, non si aspetta.
FORZATO=0
musica_in_corso() {
  local n primo ora
  [ "$FORZATO" = 1 ] && return 1   # già deciso: si installa (oltre 6 ore di attesa)
  n=$(curl -s -m 5 "http://127.0.0.1:$PORTA/api/extra/suona" 2>/dev/null | sed -n 's/.*"suonano": *\([0-9][0-9]*\).*/\1/p')
  if [ -z "$n" ] || [ "$n" -eq 0 ]; then rm -f "$RIMANDATO"; return 1; fi
  ora=$(date +%s)
  primo=$(cat "$RIMANDATO" 2>/dev/null || true)
  case "$primo" in
    ''|*[!0-9]*) primo=$ora; echo "$ora" > "$RIMANDATO"; log "musica in riproduzione su $n dispositivi: l'aggiornamento aspetta" ;;
  esac
  if [ $((ora - primo)) -ge "${AGGIORNA_ATTESA_MAX:-21600}" ]; then
    log "musica in riproduzione da oltre $(( ${AGGIORNA_ATTESA_MAX:-21600} / 3600 )) ore: installo lo stesso"; rm -f "$RIMANDATO"; FORZATO=1; return 1
  fi
  return 0
}

exec 9>"$HOME/Atlante/.aggiorna.lock"
/usr/bin/lockf -t 0 9 2>/dev/null || flock -n 9 2>/dev/null || exit 0

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

musica_in_corso && exit 0   # prima delle prove (minuti di lavoro del Mac): si riprova al giro dopo

PROVA=$(mktemp -d "$HOME/Atlante/.prova.XXXX")
trap 'rm -rf "$PROVA"' EXIT
git archive "$NUOVO" musica | tar -x -C "$PROVA" || { log "estrazione non riuscita"; exit 1; }
if ! (cd "$PROVA/musica/server" && python3 -I test_extra.py >"$PROVA/prove.txt" 2>&1); then
  log "prove fallite su ${NUOVO:0:7}: resta la versione ${ATTUALE:0:7}"; tail -5 "$PROVA/prove.txt" >> "$LOG"; exit 1
fi

musica_in_corso && exit 0   # qualcuno ha cominciato ad ascoltare durante le prove

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
