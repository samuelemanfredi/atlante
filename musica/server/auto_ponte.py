#!/usr/bin/env python3
"""Ponte di Atlante verso il cloud Volkswagen: lo stesso servizio dell'app Volkswagen, con l'account di Samuele.

Gira nel suo ambiente Python (<cache>/auto/python, preparato da auto_vw.py), dove c'è CarConnectivity
(https://github.com/tillsteinbach/CarConnectivity, connettore «volkswagen»): il server di Atlante resta con la sola
libreria standard e, se il cloud Volkswagen cambia e la libreria si blocca, si ferma solo questo processo.

Protocollo, una riga JSON per messaggio:
  stdin, la prima riga  {"utente", "password", "intervallo", "forza_accesso", "cartella"}
  stdin, poi            {"id", "azione", "vin", "spin"?, "temperatura"?}
                        azione: blocca, sblocca, clima_on, clima_off, sbrina_on, sbrina_off, aggiorna
  stdout                {"tipo": "pronto"}
                        {"tipo": "stato", "t", "veicoli": [{"vin", "attr": {percorso: [valore, unità, misurato]},
                                                           "comandi": {percorso: disponibile}}]}
                        {"tipo": "errore", "testo", "grave"}   grave: accesso rifiutato (il ponte si chiude)
                        {"tipo": "risposta", "id", "ok", "errore"?}
Quando stdin si chiude (Atlante si ferma o si riavvia) il ponte salva la sessione e si chiude. La password resta solo
in memoria; su disco (token.json, cache.json) c'è solo la sessione di CarConnectivity, come fa l'app ufficiale.
L'S-PIN arriva con il singolo comando e non viene mai salvato.
"""
import datetime
import enum
import json
import logging
import os
import re
import sys
import threading
import time
import traceback

USCITA = sys.stdout            # il canale verso Atlante: solo righe JSON
sys.stdout = sys.stderr        # quello che stampano le librerie va nel registro, non nel protocollo
SCRIVI = threading.Lock()
LAVORO = threading.Lock()      # CarConnectivity fa una cosa alla volta: una lettura o un comando
SVEGLIA = threading.Event()    # una lettura adesso («aggiorna», o poco dopo un comando)
FINE = threading.Event()
MIN_TRA_LETTURE = 60           # al più una lettura al minuto: il cloud Volkswagen limita le richieste dell'account
DOPO_COMANDO = 35              # dopo un comando si rilegge: l'auto lo ha eseguito e il cloud lo sa
AZIONI = {
    "blocca": ("doors/commands/lock-unlock", "lock"),
    "sblocca": ("doors/commands/lock-unlock", "unlock"),
    "clima_on": ("climatization/commands/start-stop", "start"),
    "clima_off": ("climatization/commands/start-stop", "stop"),
    "sbrina_on": ("window_heating/commands/start-stop", "start"),
    "sbrina_off": ("window_heating/commands/start-stop", "stop"),
}
NON_DISPONIBILE = {
    "doors": "Apri e chiudi da remoto non è attivo per quest'auto nel cloud Volkswagen (serve «Lock & Unlock» "
             "nell'app Volkswagen, con Volkswagen Ident e S-PIN)",
    "climatization": "Il clima da remoto non è disponibile per quest'auto nel cloud Volkswagen",
    "window_heating": "Lo sbrinamento da remoto non è disponibile per quest'auto nel cloud Volkswagen",
}


def scrivi(messaggio):
    riga = json.dumps(messaggio, ensure_ascii=False, default=str)
    with SCRIVI:
        USCITA.write(riga + "\n")
        USCITA.flush()


def semplice(v):
    """Un valore di CarConnectivity in JSON: enum → il suo valore, date → ISO, durate → secondi."""
    if isinstance(v, enum.Enum):
        return semplice(v.value)
    if isinstance(v, datetime.datetime):
        return v.isoformat()
    if isinstance(v, datetime.timedelta):
        return v.total_seconds()
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    return str(v)[:200]


def testo_breve(e):
    return (str(e) or e.__class__.__name__).strip()[:300]


def istantanea(cc, comando_cls):
    """Tutti i valori attivi di ogni auto dell'account, con il percorso relativo all'auto (es. «drives/primary/level»),
    e quali comandi ci sono (es. «doors/commands/lock-unlock»). Le temperature in kelvin diventano °C."""
    veicoli = []
    garage = cc.get_garage()
    for veicolo in (garage.list_vehicles() if garage is not None else []):
        base = veicolo.get_absolute_path().rstrip("/") + "/"
        attr, comandi = {}, {}
        for a in veicolo.get_attributes(recursive=True):
            try:
                percorso = a.get_absolute_path()
            except Exception:  # noqa: BLE001
                continue
            if not percorso.startswith(base):
                continue
            percorso = percorso[len(base):]
            if isinstance(a, comando_cls):
                comandi[percorso] = bool(a.enabled)
                continue
            if percorso.startswith("images"):
                continue
            try:
                valore, unita = a.value, getattr(a, "unit", None)
            except Exception:  # noqa: BLE001
                continue
            unita = semplice(unita)
            if unita in ("°K", "K") and isinstance(valore, (int, float)) and not isinstance(valore, bool):
                valore, unita = round(valore - 273.15, 2), "°C"
            agg = getattr(a, "last_updated", None)
            attr[percorso] = [semplice(valore), unita, agg.isoformat() if isinstance(agg, datetime.datetime) else None]
        veicoli.append({"vin": semplice(veicolo.vin.value), "attr": attr, "comandi": comandi})
    return {"tipo": "stato", "t": time.time(), "veicoli": veicoli}


def trova(veicolo, percorso):
    cerca = veicolo.get_absolute_path().rstrip("/") + "/" + percorso
    for a in veicolo.get_attributes(recursive=True):
        try:
            if a.get_absolute_path() == cerca:
                return a
        except Exception:  # noqa: BLE001
            continue
    return None


def esegui(cc, r):
    """Esegue un comando sull'auto: alza ValueError (testo per Samuele) o gli errori di CarConnectivity."""
    from carconnectivity.command_impl import (ClimatizationStartStopCommand, LockUnlockCommand,
                                              WindowHeatingStartStopCommand)
    from carconnectivity.units import Temperature
    azione = r.get("azione")
    garage = cc.get_garage()
    veicoli = garage.list_vehicles() if garage is not None else []
    vin = str(r.get("vin") or "")
    veicolo = next((v for v in veicoli if str(v.vin.value) == vin), None)
    if veicolo is None and not vin and len(veicoli) == 1:
        veicolo = veicoli[0]
    if veicolo is None:
        raise ValueError("Auto non trovata nell'account Volkswagen: aspetta la prossima lettura e riprova")
    percorso, verbo = AZIONI[azione]
    comando = trova(veicolo, percorso)
    if comando is None or not comando.enabled:
        raise ValueError(NON_DISPONIBILE[percorso.split("/", 1)[0]])
    if azione in ("blocca", "sblocca"):
        spin = str(r.get("spin") or "")
        if not re.fullmatch(r"\d{4}", spin):
            raise ValueError("Serve l'S-PIN di 4 cifre")
        comando.value = {"command": LockUnlockCommand.Command(verbo), "spin": spin}
    elif azione in ("clima_on", "clima_off"):
        valore = {"command": ClimatizationStartStopCommand.Command(verbo)}
        if verbo == "start" and r.get("temperatura") is not None:
            valore["target_temperature"] = float(r["temperatura"])
            valore["target_temperature_unit"] = Temperature.C
        comando.value = valore
    else:
        comando.value = {"command": WindowHeatingStartStopCommand.Command(verbo)}


def spiega(e):
    """L'errore di un comando, detto in italiano quando si capisce."""
    if isinstance(e, ValueError):
        return testo_breve(e)
    testo = testo_breve(e)
    m = re.search(r"\((\d{3}):", testo)
    codice = m.group(1) if m else ""
    if codice in ("401", "403"):
        return "Il cloud Volkswagen ha rifiutato il comando: S-PIN sbagliato o funzione non attiva per il tuo account"
    if codice == "429":
        return "Troppe richieste al cloud Volkswagen: riprova tra qualche minuto"
    if "without external power" in testo or "externalPower" in testo:
        return "Il clima senza cavo è spento nelle impostazioni dell'auto: accendilo nell'app Volkswagen o collega il cavo"
    return "Comando non riuscito: " + testo


def leggi_comandi(cc):
    for riga in sys.stdin:
        riga = riga.strip()
        if not riga:
            continue
        try:
            r = json.loads(riga)
        except ValueError:
            continue
        if not isinstance(r, dict):
            continue
        rid = r.get("id")
        azione = r.get("azione")
        try:
            if azione == "aggiorna":
                SVEGLIA.set()
                scrivi({"tipo": "risposta", "id": rid, "ok": True})
                continue
            if azione not in AZIONI:
                scrivi({"tipo": "risposta", "id": rid, "ok": False, "errore": "Comando sconosciuto"})
                continue
            try:
                with LAVORO:
                    esegui(cc, r)
            except Exception as e:  # noqa: BLE001
                if not isinstance(e, ValueError):
                    traceback.print_exc()
                scrivi({"tipo": "risposta", "id": rid, "ok": False, "errore": spiega(e)})
            else:
                scrivi({"tipo": "risposta", "id": rid, "ok": True})
                t = threading.Timer(DOPO_COMANDO, SVEGLIA.set)
                t.daemon = True
                t.start()
        finally:
            r.pop("spin", None)   # l'S-PIN non resta in memoria più del necessario
    FINE.set()      # stdin chiuso: Atlante si è fermato
    SVEGLIA.set()


def main():
    os.umask(0o077)   # sessione e registro leggibili solo da Samuele
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    try:
        conf = json.loads(sys.stdin.readline() or "{}")
    except ValueError:
        conf = {}
    if not isinstance(conf, dict) or not conf.get("utente") or not conf.get("password"):
        scrivi({"tipo": "errore", "grave": True, "testo": "Mancano email e password Volkswagen"})
        return 2
    cartella = conf.get("cartella") or os.getcwd()
    try:
        intervallo = max(180, int(conf.get("intervallo") or 300))
    except (TypeError, ValueError):
        intervallo = 300
    try:
        from carconnectivity import carconnectivity as cc_mod
        from carconnectivity import errors as cc_err
        from carconnectivity.commands import GenericCommand
    except Exception as e:  # noqa: BLE001
        scrivi({"tipo": "errore", "grave": True, "codice": "libreria",
                "testo": "La libreria CarConnectivity non è pronta: %s" % testo_breve(e)})
        return 3
    config = {"carConnectivity": {"log_level": "error", "plugins": [], "connectors": [{
        "type": "volkswagen", "config": {"username": conf["utente"], "password": conf["password"],
                                         "interval": intervallo, "max_age": 30,
                                         "force_enable_access": bool(conf.get("forza_accesso"))}}]}}
    conf["password"] = None
    try:
        cc = cc_mod.CarConnectivity(config=config, tokenstore_file=os.path.join(cartella, "token.json"),
                                    cache_file=os.path.join(cartella, "cache.json"))
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        scrivi({"tipo": "errore", "grave": isinstance(e, cc_err.AuthenticationError),
                "testo": "Avvio del collegamento non riuscito: %s" % testo_breve(e)})
        return 4
    threading.Thread(target=leggi_comandi, args=(cc,), name="comandi", daemon=True).start()
    scrivi({"tipo": "pronto"})
    ultima = 0.0
    while not FINE.is_set():
        attesa = intervallo
        pausa = MIN_TRA_LETTURE - (time.time() - ultima)
        if ultima and pausa > 0 and FINE.wait(pausa):
            break
        try:
            with LAVORO:
                ultima = time.time()
                cc.fetch_all()
                messaggio = istantanea(cc, GenericCommand)
            scrivi(messaggio)
            try:
                cc.persist()
            except Exception:  # noqa: BLE001
                traceback.print_exc()
        except cc_err.TooManyRequestsError:
            scrivi({"tipo": "errore", "grave": False,
                    "testo": "Il cloud Volkswagen chiede di rallentare: riprovo tra 15 minuti"})
            attesa = 900
        except cc_err.TemporaryAuthenticationError as e:
            scrivi({"tipo": "errore", "grave": False,
                    "testo": "Accesso a Volkswagen non riuscito per ora, riprovo: %s" % testo_breve(e)})
        except cc_err.AuthenticationError as e:
            scrivi({"tipo": "errore", "grave": True,
                    "testo": "Volkswagen ha rifiutato l'accesso: controlla email e password (%s)" % testo_breve(e)})
            break
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            scrivi({"tipo": "errore", "grave": False, "testo": "Lettura non riuscita: %s" % testo_breve(e)})
        SVEGLIA.wait(attesa)
        SVEGLIA.clear()
    try:
        cc.shutdown()
    except Exception:  # noqa: BLE001
        traceback.print_exc()
    return 0


if __name__ == "__main__":
    sys.exit(main())
