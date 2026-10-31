#!/bin/bash
# Doppio clic nel Finder: apre «Copia audio e video» nel browser (questa finestra del Terminale resta aperta finché
# il programma lavora; si chiude con il pulsante «Chiudi» nella pagina).
cd "$(dirname "$0")" || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  echo "Serve Python 3: installalo da https://www.python.org/downloads/ (oppure con: xcode-select --install)"
  read -r -p "Premi Invio per chiudere" _
  exit 1
fi
exec python3 copia_audio_video.py "$@"
