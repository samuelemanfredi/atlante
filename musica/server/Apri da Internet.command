#!/bin/bash
# Atlante Musica da Internet senza Tailscale: Cloudflare Tunnel verso l'app sul Mac (127.0.0.1:8790).
# Da Internet si entra solo con la password (Samuele), un link d'invito o un codice di collegamento:
# il server riconosce le richieste di cloudflared (Cf-Ray, Cf-Connecting-Ip) e le tratta come Internet.
# Senza dominio: indirizzo provvisorio *.trycloudflare.com (cambia a ogni riavvio).
# Con un dominio su Cloudflare: ATLANTE_TUNNEL=<nome del tunnel> (creato con `cloudflared tunnel create`).
set -e
PORTA="${ATLANTE_PORTA:-8790}"
if ! command -v cloudflared >/dev/null; then
  command -v brew >/dev/null || { echo "Manca Homebrew: https://brew.sh"; read -r -p "Invio per chiudere"; exit 1; }
  brew install cloudflared
fi
if [ -n "$ATLANTE_TUNNEL" ]; then
  exec cloudflared tunnel run --url "http://127.0.0.1:$PORTA" "$ATLANTE_TUNNEL"
fi
echo "Tra poco compare l'indirizzo https://….trycloudflare.com: aprilo dall'iPad, poi Condividi › Aggiungi alla schermata Home."
exec cloudflared tunnel --no-autoupdate --url "http://127.0.0.1:$PORTA"
