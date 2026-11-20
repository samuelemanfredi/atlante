#!/bin/bash
# Lavoro di una volta sul Mac mini: lo lancia il server di Atlante Musica all'avvio (vedi «Lavori di una volta» in
# musica_libreria.py), dopo che l'aggiornamento automatico l'ha portato qui.
#
# Riaccende il Remote Control di Claude Code sul Mac mini e lo tiene acceso: dall'app Claude (iPhone, iPad, claude.ai)
# si aprono di nuovo sessioni che lavorano su questo Mac, nella cartella ~/Atlante, come prima del 7/11/2026 (quando la
# sessione che lo teneva aperto nel Terminale si è chiusa). Chiesto da Samuele il 20/11/2026.
# Un LaunchAgent (it.samuele.claude-controllo-remoto) lancia ~/Atlante/claude-controllo-remoto.sh all'accesso e lo
# rilancia se si chiude: `claude remote-control` dentro un terminale finto (script), perché di solito gira nel Terminale.
# Uscita 0 solo se dopo 2 minuti è ancora acceso e nel suo registro compare claude.ai (il collegamento). Se no il
# LaunchAgent si toglie, così non riprova a vuoto ogni 5 minuti, e il server riprova ai prossimi avvii (al più 3 volte).
# Quello che succede: ~/Atlante/una_volta/controllo_remoto.txt, ~/Atlante/claude-controllo-remoto.log e registro.log.
set -u
export PATH="$HOME/.local/bin:$HOME/.claude/local:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
LOG="$HOME/Atlante/registro.log"
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') controllo-remoto: $*" >> "$LOG"; echo "$*"; }

ETICHETTA="it.samuele.claude-controllo-remoto"
PLIST="$HOME/Library/LaunchAgents/$ETICHETTA.plist"
AVVIO="$HOME/Atlante/claude-controllo-remoto.sh"
REG="$HOME/Atlante/claude-controllo-remoto.log"
DOM="gui/$(id -u)"

CLAUDE=""
for c in "$(command -v claude 2>/dev/null)" "$HOME/.local/bin/claude" "$HOME/.claude/local/claude" \
         /opt/homebrew/bin/claude /usr/local/bin/claude "$HOME/.npm-global/bin/claude"; do
  if [ -n "$c" ] && [ -x "$c" ]; then CLAUDE="$c"; break; fi
done
if [ -z "$CLAUDE" ]; then log "Claude Code non è installato su questo Mac: niente controllo remoto"; exit 1; fi
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Atlante" || exit 1

# lo script che il LaunchAgent lancia (percorsi di adesso scritti dentro; $HOME resta da leggere quando parte)
cat > "$AVVIO" <<FINE
#!/bin/bash
# Remote Control di Claude Code sempre acceso sul Mac mini (LaunchAgent $ETICHETTA, messo il $(date '+%d/%m/%Y') da
# server/una_volta/controllo_remoto.sh di Atlante Musica): dall'app Claude si aprono sessioni su questo Mac, in ~/Atlante.
export PATH="$PATH"
cd "\$HOME/Atlante" 2>/dev/null || cd "\$HOME" || exit 1
# un terminale finto (script) e un ingresso che resta aperto (sleep): fuori dal Terminale non si chiude da solo
/usr/bin/script -q /dev/null "$CLAUDE" remote-control < <(exec /bin/sleep 2147483647)
FINE
chmod 755 "$AVVIO" || exit 1

cat > "$PLIST" <<FINE
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$ETICHETTA</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$AVVIO</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>300</integer>
  <key>StandardOutPath</key><string>$REG</string>
  <key>StandardErrorPath</key><string>$REG</string>
</dict>
</plist>
FINE
if ! plutil -lint "$PLIST" >/dev/null; then log "il LaunchAgent non è valido: niente controllo remoto"; mv -f "$PLIST" "$PLIST.non-va"; exit 1; fi

launchctl bootout "$DOM/$ETICHETTA" 2>/dev/null   # uno di prima (un tentativo precedente): si riparte puliti
sleep 2
: > "$REG"
if ! launchctl bootstrap "$DOM" "$PLIST"; then log "launchctl non ha caricato il LaunchAgent"; mv -f "$PLIST" "$PLIST.non-va"; exit 1; fi
log "LaunchAgent caricato con $CLAUDE: aspetto il collegamento"

i=0
while [ "$i" -lt 24 ]; do   # 24 × 5 s = 2 minuti
  sleep 5
  i=$((i + 1))
done
if launchctl print "$DOM/$ETICHETTA" 2>/dev/null | grep -q 'state = running' && grep -q 'claude\.ai' "$REG" 2>/dev/null; then
  log "acceso: dall'app Claude si aprono di nuovo sessioni sul Mac mini (cartella ~/Atlante)"
  exit 0
fi
log "non si è collegato entro 2 minuti: tolgo il LaunchAgent (vedi ~/Atlante/claude-controllo-remoto.log)"
launchctl bootout "$DOM/$ETICHETTA" 2>/dev/null
mv -f "$PLIST" "$PLIST.non-va"   # non riparte al prossimo accesso: lo rimette il prossimo tentativo
echo "--- registro di claude remote-control ---"
tail -c 3000 "$REG" 2>/dev/null
exit 1
