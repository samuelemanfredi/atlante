#!/usr/bin/env python3
"""La mia Volkswagen: il modulo auto di Atlante (pagina x/auto.html, API sotto api/extra/auto/, solo Samuele).

- Collega l'account Volkswagen (lo stesso dell'app Volkswagen) con un processo a parte, auto_ponte.py, che usa
  CarConnectivity in un suo ambiente Python (<cache>/auto/python, preparato qui da solo la prima volta). Sull'auto
  non si installa niente: si parla con il cloud Volkswagen, come fa l'app ufficiale.
- Ogni 5 minuti legge batteria, autonomia, chilometri, temperature, clima, porte, ricarica e posizione di parcheggio.
  Comandi: apri e chiudi (con l'S-PIN, chiesto ogni volta e mai salvato), clima, sbrinamento.
- Diario (SQLite): soste, viaggi ricavati da contachilometri e posizione, ricariche. Il cloud non dà il tracciato GPS:
  la strada di un viaggio è ricostruita con OSRM (la più probabile tra partenza e arrivo).
- Dai viaggi impara quanto consuma l'auto a ogni temperatura (stile di guida e clima) e pianifica i viaggi lunghi con
  le soste di ricarica secondo le regole di Samuele: alla colonnina ideale 33%, almeno 25%; 20–45% solo se nella
  fascia giusta non c'è una colonnina; ricarica fino all'80%.

Servizi pubblici senza chiavi: OSRM (percorsi), Open-Meteo (quote e meteo), Overpass/OpenStreetMap (colonnine),
Nominatim (indirizzi). Solo libreria standard di Python, come musica_libreria.py, che carica questo file e controlla
che chi chiede sia Samuele. Opzioni (variabili d'ambiente): ATLANTE_AUTO_PORTACHIAVI=0 (password in un file 0600
invece che nel Portachiavi), ATLANTE_AUTO_INSTALLA=0, ATLANTE_AUTO_PYTHON, ATLANTE_AUTO_PONTE (prove),
ATLANTE_AUTO_OSRM, ATLANTE_AUTO_METEO, ATLANTE_AUTO_OVERPASS, ATLANTE_AUTO_NOMINATIM.
"""
import datetime
import json
import math
import os
import re
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request

QUI = os.path.dirname(os.path.abspath(__file__))
PONTE = os.path.join(QUI, "auto_ponte.py")
PACCHETTI = ["carconnectivity>=0.11.11", "carconnectivity-connector-volkswagen>=0.10.6"]
UA = "AtlanteAuto/1.0 (+https://github.com/samuelemanfredi/atlante; uso personale)"
INTERVALLO = 300           # secondi tra due letture del cloud Volkswagen (più spesso il cloud rallenta l'account)
SERVIZIO_PORTACHIAVI = "Atlante - La mia Volkswagen"
G = 9.81
AZIONI = ("blocca", "sblocca", "clima_on", "clima_off", "sbrina_on", "sbrina_off", "aggiorna")


class ErroreAuto(Exception):
    """Risposta d'errore per la pagina: {errore: testo} con il codice HTTP dato."""

    def __init__(self, codice, testo):
        super().__init__(testo)
        self.codice, self.testo = codice, testo


# ---------------------------------------------------------------------------------------------------------------
# impostazioni (valori tipici della ID. Buzz Pro 150 kW, batteria da 77 kWh utili; le regole sono quelle di Samuele)
# ---------------------------------------------------------------------------------------------------------------
PREDEFINITE = {
    "auto": {"nome": "ID. Buzz Pro 77 kWh", "capacita_kwh": 77.0, "massa_kg": 2500.0, "carico_kg": 100.0,
             "cda": 0.92, "crr": 0.009, "rend_motore": 0.87, "rend_recupero": 0.62, "elettronica_kw": 0.30,
             "musica_kw": 0.04, "pompa_calore": False, "dc_max_kw": 170.0},
    "regole": {"arrivo_min": 25.0, "arrivo_ideale": 33.0, "toll_min": 20.0, "toll_max": 45.0, "ricarica_max": 80.0,
               "destinazione_min": 25.0},
    "viaggio": {"velocita_max": 110.0, "abitacolo_c": 21.0, "musica": True, "ionity_prima": True,
                "precondiziona": True},
    "prezzi": {"ionity_IT": 0.47, "ionity_FR": 0.33, "ionity_ES": 0.35, "ionity_altri": 0.47, "altri": 0.65},
}
LIMITI = {
    "auto.capacita_kwh": (20, 150), "auto.massa_kg": (800, 4500), "auto.carico_kg": (0, 1500), "auto.cda": (0.3, 2.0),
    "auto.crr": (0.004, 0.02), "auto.rend_motore": (0.6, 0.98), "auto.rend_recupero": (0.0, 0.9),
    "auto.elettronica_kw": (0.0, 2.0), "auto.musica_kw": (0.0, 0.5), "auto.dc_max_kw": (20, 400),
    "regole.arrivo_min": (5, 60), "regole.arrivo_ideale": (5, 70), "regole.toll_min": (3, 60),
    "regole.toll_max": (10, 90), "regole.ricarica_max": (50, 100), "regole.destinazione_min": (3, 70),
    "viaggio.velocita_max": (50, 150), "viaggio.abitacolo_c": (15, 27),
    "prezzi.ionity_IT": (0, 3), "prezzi.ionity_FR": (0, 3), "prezzi.ionity_ES": (0, 3), "prezzi.ionity_altri": (0, 3),
    "prezzi.altri": (0, 3),
}
ETICHETTE = {
    "auto.capacita_kwh": "capacità utile", "auto.massa_kg": "massa", "auto.carico_kg": "carico",
    "auto.cda": "resistenza dell'aria (CdA)", "auto.crr": "resistenza al rotolamento", "auto.dc_max_kw": "potenza di ricarica",
    "regole.arrivo_min": "arrivo minimo", "regole.arrivo_ideale": "arrivo ideale", "regole.toll_min": "tolleranza minima",
    "regole.toll_max": "tolleranza massima", "regole.ricarica_max": "ricarica massima",
    "regole.destinazione_min": "arrivo a destinazione", "viaggio.velocita_max": "velocità massima",
    "viaggio.abitacolo_c": "temperatura dell'abitacolo",
}


def _num(v, predefinito=None):
    if isinstance(v, bool):
        return predefinito
    try:
        x = float(v)
    except (TypeError, ValueError):
        return predefinito
    return x if math.isfinite(x) else predefinito


def _tondo(x, cifre=1):
    return None if x is None or not isinstance(x, (int, float)) or not math.isfinite(x) else round(x, cifre)


def _leggi_json(percorso, predefinito):
    try:
        with open(percorso, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return predefinito


def _scrivi_json_privato(percorso, dati):
    """Scrive il file leggibile solo da Samuele (0600), al posto di quello vecchio solo quando è completo."""
    tmp = "%s.%s.tmp" % (percorso, secrets.token_hex(4))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(dati, f, ensure_ascii=False)
        os.replace(tmp, percorso)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _copia(d):
    return json.loads(json.dumps(d))


def _unisci(predefinite, salvate):
    """Le impostazioni salvate sopra quelle predefinite, solo per le chiavi conosciute e con il tipo giusto."""
    out = _copia(predefinite)
    if not isinstance(salvate, dict):
        return out
    for gruppo, valori in predefinite.items():
        s = salvate.get(gruppo)
        if not isinstance(s, dict):
            continue
        for k, base in valori.items():
            if k not in s:
                continue
            if isinstance(base, bool):
                out[gruppo][k] = bool(s[k])
            elif isinstance(base, (int, float)):
                x = _num(s[k])
                lim = LIMITI.get(gruppo + "." + k)
                if x is not None and (not lim or lim[0] <= x <= lim[1]):
                    out[gruppo][k] = x
            elif isinstance(base, str):
                out[gruppo][k] = str(s[k])[:80]
    return out


class Impostazioni:
    def __init__(self, file):
        self.file = file
        self.lock = threading.Lock()
        self.dati = _unisci(PREDEFINITE, _leggi_json(file, {}))

    def tutte(self):
        with self.lock:
            return _copia(self.dati)

    def aggiorna(self, nuove):
        if not isinstance(nuove, dict):
            raise ErroreAuto(400, "Impostazioni non valide")
        with self.lock:
            prova = _copia(self.dati)
            for gruppo, valori in nuove.items():
                if gruppo not in PREDEFINITE or not isinstance(valori, dict):
                    continue
                for k, v in valori.items():
                    if k not in PREDEFINITE[gruppo]:
                        continue
                    base = PREDEFINITE[gruppo][k]
                    if isinstance(base, bool):
                        prova[gruppo][k] = bool(v)
                    elif isinstance(base, (int, float)):
                        x = _num(v)
                        lim = LIMITI.get(gruppo + "." + k)
                        if x is None or (lim and not lim[0] <= x <= lim[1]):
                            raise ErroreAuto(400, "Valore non valido: %s" % ETICHETTE.get(gruppo + "." + k, k))
                        prova[gruppo][k] = x
                    elif isinstance(base, str):
                        prova[gruppo][k] = str(v).strip()[:80]
            r = prova["regole"]
            if not r["toll_min"] <= r["arrivo_min"] <= r["arrivo_ideale"] <= r["toll_max"] <= r["ricarica_max"]:
                raise ErroreAuto(400, "Le soglie vanno in ordine: tolleranza minima ≤ minimo ≤ ideale ≤ tolleranza "
                                      "massima ≤ ricarica massima")
            if not r["toll_min"] <= r["destinazione_min"] < r["ricarica_max"]:
                raise ErroreAuto(400, "L'arrivo a destinazione va tra la tolleranza minima e la ricarica massima")
            self.dati = prova
            _scrivi_json_privato(self.file, self.dati)
            return _copia(self.dati)


# ---------------------------------------------------------------------------------------------------------------
# credenziali: l'email in account.json, la password nel Portachiavi del Mac (se non c'è: nello stesso file, 0600)
# ---------------------------------------------------------------------------------------------------------------
class Credenziali:
    def __init__(self, cartella):
        self.file = os.path.join(cartella, "account.json")

    @staticmethod
    def _security():
        if os.environ.get("ATLANTE_AUTO_PORTACHIAVI", "1") == "0":
            return None
        return "/usr/bin/security" if os.path.exists("/usr/bin/security") else None

    def dati(self):
        d = _leggi_json(self.file, {})
        return d if isinstance(d, dict) else {}

    def utente(self):
        return str(self.dati().get("utente") or "")

    def _dal_portachiavi(self, utente):
        sec = self._security()
        if not sec or not utente:
            return None
        try:
            r = subprocess.run([sec, "find-generic-password", "-a", utente, "-s", SERVIZIO_PORTACHIAVI, "-w"],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return None
        if r.returncode != 0:
            return None
        return r.stdout.decode("utf-8", "replace").rstrip("\n")

    def salva(self, utente, password, forza_accesso=False):
        d = {"utente": utente, "forza_accesso": bool(forza_accesso), "dove": "file"}
        sec = self._security()
        if sec:
            try:
                r = subprocess.run([sec, "add-generic-password", "-U", "-a", utente, "-s", SERVIZIO_PORTACHIAVI,
                                    "-w", password], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
                if r.returncode == 0 and self._dal_portachiavi(utente) == password:
                    d["dove"] = "portachiavi"
            except (OSError, subprocess.SubprocessError):
                pass
        if d["dove"] == "file":
            d["password"] = password
        _scrivi_json_privato(self.file, d)
        return d["dove"]

    def imposta(self, **campi):
        d = self.dati()
        if not d:
            return
        d.update(campi)
        _scrivi_json_privato(self.file, d)

    def leggi(self):
        """(utente, password, forza_accesso) o None."""
        d = self.dati()
        utente = str(d.get("utente") or "")
        if not utente:
            return None
        pw = self._dal_portachiavi(utente) if d.get("dove") == "portachiavi" else d.get("password")
        if not pw:
            return None
        return utente, pw, bool(d.get("forza_accesso"))

    def cancella(self):
        utente = self.utente()
        sec = self._security()
        if sec and utente:
            try:
                subprocess.run([sec, "delete-generic-password", "-a", utente, "-s", SERVIZIO_PORTACHIAVI],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
            except (OSError, subprocess.SubprocessError):
                pass
        try:
            os.unlink(self.file)
        except OSError:
            pass


def maschera(utente):
    u = str(utente or "")
    if "@" not in u:
        return u[:2] + "•••" if u else ""
    nome, dom = u.split("@", 1)
    return nome[:2] + "•••@" + dom


# ---------------------------------------------------------------------------------------------------------------
# il ponte: il processo con CarConnectivity (installato la prima volta), riavviato se si ferma
# ---------------------------------------------------------------------------------------------------------------
def _breve(e):
    return (str(e) or e.__class__.__name__).strip()[:300]


class Ponte:
    def __init__(self, cartella, alla_lettura):
        self.cartella = cartella
        self.venv = os.path.join(cartella, "python")
        self.registro = os.path.join(cartella, "ponte.log")
        self.alla_lettura = alla_lettura
        self.lock = threading.Lock()
        self.p = None
        self.attese = {}
        self.fermo = threading.Event()
        self.subito = threading.Event()   # riparti senza aspettare (nuove credenziali mentre il filo finiva)
        self.filo = None
        self.bloccato = ""          # accesso rifiutato: non riparte finché Samuele non ricollega l'account
        self.reinstalla = False
        self.stato = {"fase": "spento", "messaggio": "", "dal": time.time()}

    def _fase(self, fase, messaggio=""):
        self.stato = {"fase": fase, "messaggio": messaggio, "dal": time.time()}

    def _python(self):
        return os.environ.get("ATLANTE_AUTO_PYTHON") or os.path.join(self.venv, "bin", "python3")

    def attivo(self):
        return bool(self.filo and self.filo.is_alive())

    def avvia(self, credenziali, forza_installazione=False):
        """credenziali: funzione che dà (utente, password, forza_accesso) o None. Non aspetta: tutto in un filo."""
        with self.lock:
            self.bloccato = ""
            self.reinstalla = self.reinstalla or forza_installazione
            if self.attivo():
                # il filo di prima (fermato, ma magari ancora a metà dell'installazione) va avanti con queste
                # credenziali, subito; un ponte ancora acceso si chiude e riparte con loro
                self.fermo.clear()
                self.subito.set()
                if self.p is not None and self.p.poll() is None:
                    try:
                        self.p.stdin.close()
                    except OSError:
                        pass
                return
            self.fermo.clear()
            self.filo = threading.Thread(target=self._vita, args=(credenziali,), name="auto-ponte", daemon=True)
            self.filo.start()

    def ferma(self, attesa=10.0):
        self.fermo.set()
        with self.lock:
            p = self.p
        if p is not None and p.poll() is None:
            try:
                p.stdin.close()
            except OSError:
                pass
            try:
                p.wait(attesa)
            except subprocess.TimeoutExpired:
                p.terminate()
                try:
                    p.wait(5)
                except subprocess.TimeoutExpired:
                    p.kill()
        filo = self.filo
        if filo is not None and filo is not threading.current_thread():
            filo.join(attesa + 5)
        if not self.bloccato:
            self._fase("spento")

    def _vita(self, credenziali):
        attesa = 15
        while not self.fermo.is_set():
            try:
                self._installa()
            except Exception as e:  # noqa: BLE001
                self._fase("errore", "Preparazione del collegamento non riuscita, riprovo tra 10 minuti: %s" % _breve(e))
                if self.fermo.wait(600):
                    break
                continue
            if self.fermo.is_set():
                break
            cred = credenziali()
            if not cred:
                self._fase("errore", "Password Volkswagen non leggibile: ricollega l'account")
                break
            inizio = time.time()
            self.subito.clear()
            self._esegui(cred)
            cred = None
            if self.subito.is_set() and not self.fermo.is_set():
                self.subito.clear()
                attesa = 15
                continue
            if self.fermo.is_set() or self.bloccato:
                break
            if time.time() - inizio > 600:
                attesa = 15
            self._fase("attesa", "Il collegamento si è fermato: riparto tra %d secondi" % attesa)
            if self.fermo.wait(attesa):
                break
            attesa = min(attesa * 2, 900)

    def _installa(self):
        if os.environ.get("ATLANTE_AUTO_INSTALLA", "1") == "0" or os.environ.get("ATLANTE_AUTO_PYTHON"):
            return
        py = self._python()
        if not os.path.exists(py):
            self._fase("installazione", "Preparo il collegamento con Volkswagen: una volta sola, 1–3 minuti")
            r = subprocess.run([sys.executable, "-m", "venv", self.venv], stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, timeout=600)
            if r.returncode != 0:
                raise RuntimeError(r.stdout.decode("utf-8", "replace")[-300:])
        prova = subprocess.run([py, "-I", "-c", "import carconnectivity.carconnectivity, "
                                                "carconnectivity_connectors.volkswagen.connector"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180)
        if prova.returncode != 0 or self.reinstalla:
            self._fase("installazione", "Scarico la libreria per il cloud Volkswagen (CarConnectivity)…")
            subprocess.run([py, "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "--upgrade", "pip"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=600)
            r = subprocess.run([py, "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "--upgrade"]
                               + PACCHETTI, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=1800)
            if r.returncode != 0:
                raise RuntimeError(r.stdout.decode("utf-8", "replace").strip()[-300:])
            self.reinstalla = False

    def _ruota_registro(self):
        try:
            if os.path.getsize(self.registro) > 2 * 1024 * 1024:
                os.replace(self.registro, self.registro + ".1")
        except OSError:
            pass

    def _esegui(self, cred):
        self._fase("avvio", "Collego l'account Volkswagen…")
        self._ruota_registro()
        script = os.environ.get("ATLANTE_AUTO_PONTE") or PONTE
        env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": os.path.expanduser("~"), "LANG": "it_IT.UTF-8",
               "PYTHONIOENCODING": "utf-8"}
        try:
            with open(self.registro, "ab") as reg:
                os.chmod(self.registro, 0o600)
                p = subprocess.Popen([self._python(), "-I", script], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=reg, cwd=self.cartella, env=env, start_new_session=True)
        except OSError as e:
            self._fase("errore", "Il collegamento non parte: %s" % _breve(e))
            return -1
        with self.lock:
            self.p = p
            fermato = self.fermo.is_set()
        if fermato:          # fermato proprio mentre partiva: si chiude senza collegarsi
            p.terminate()
        try:
            prima = {"utente": cred[0], "password": cred[1], "intervallo": INTERVALLO, "forza_accesso": cred[2],
                     "cartella": self.cartella}
            p.stdin.write((json.dumps(prima) + "\n").encode("utf-8"))
            p.stdin.flush()
            prima = None
        except OSError:
            pass
        for riga in p.stdout:
            try:
                m = json.loads(riga.decode("utf-8"))
            except ValueError:
                continue
            if isinstance(m, dict):
                try:
                    self._messaggio(m)
                except Exception as e:  # noqa: BLE001  una lettura strana non ferma il collegamento
                    print("Atlante auto: messaggio non gestito: %s" % _breve(e), flush=True)
        codice = p.wait()
        with self.lock:
            self.p = None
            for ev in self.attese.values():
                ev[1] = {"ok": False, "errore": "Il collegamento con Volkswagen si è interrotto"}
                ev[0].set()
            self.attese.clear()
        return codice

    def _messaggio(self, m):
        tipo = m.get("tipo")
        if tipo == "pronto":
            self._fase("collegato", "In attesa della prima lettura…")
        elif tipo == "stato":
            self._fase("collegato", "")
            self.alla_lettura(m)
        elif tipo == "errore":
            testo = str(m.get("testo") or "Errore del collegamento")
            if m.get("codice") == "libreria":
                self.reinstalla = True
                self._fase("errore", testo)
            elif m.get("grave"):
                self.bloccato = testo
                self._fase("errore", testo)
            else:
                self._fase(self.stato["fase"] if self.stato["fase"] in ("collegato", "avvio") else "collegato", testo)
        elif tipo == "risposta":
            with self.lock:
                ev = self.attese.get(m.get("id"))
            if ev is not None:
                ev[1] = m
                ev[0].set()

    def comando(self, richiesta, attesa=50.0):
        with self.lock:
            p = self.p
            if p is None or p.poll() is not None:
                raise ErroreAuto(503, "Il collegamento con Volkswagen non è attivo: %s"
                                 % (self.stato.get("messaggio") or self.stato.get("fase")))
            rid = secrets.token_hex(8)
            ev = [threading.Event(), None]
            self.attese[rid] = ev
            try:
                p.stdin.write((json.dumps(dict(richiesta, id=rid)) + "\n").encode("utf-8"))
                p.stdin.flush()
            except (OSError, ValueError):
                self.attese.pop(rid, None)
                raise ErroreAuto(503, "Il collegamento con Volkswagen non risponde")
        arrivata = ev[0].wait(attesa)
        with self.lock:
            self.attese.pop(rid, None)
        if not arrivata:
            raise ErroreAuto(504, "Volkswagen non ha risposto in tempo: il comando potrebbe arrivare lo stesso all'auto")
        risposta = ev[1] or {}
        if not risposta.get("ok"):
            raise ErroreAuto(502, str(risposta.get("errore") or "Comando non riuscito"))
        return risposta


# ---------------------------------------------------------------------------------------------------------------
# letture: dai valori di CarConnectivity (percorso → [valore, unità, misurato]) ai campi che servono
# ---------------------------------------------------------------------------------------------------------------
def epoca(s):
    """Data ISO (anche con «Z») o numero → secondi; None se non si legge."""
    if s is None or s == "" or isinstance(s, bool):
        return None
    if isinstance(s, (int, float)):
        return float(s)
    try:
        d = datetime.datetime.fromisoformat(str(s).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=datetime.timezone.utc)
    return d.timestamp()


def _val(attr, percorso):
    v = attr.get(percorso)
    return v[0] if isinstance(v, list) and v else None


def _quando(attr, percorso):
    v = attr.get(percorso)
    return epoca(v[2]) if isinstance(v, list) and len(v) > 2 else None


def normalizza(vin, attr, comandi, t):
    """I campi di una lettura che servono ad Atlante (tutti None se il cloud non li dà)."""
    drive = None
    for p in sorted(attr):
        m = re.match(r"^drives/([^/]+)/type$", p)
        if m and _val(attr, p) == "electric":
            drive = m.group(1)
            break
    if drive is None and "drives/primary/level" in attr:
        drive = "primary"
    d = "drives/%s/" % drive if drive else "drives/-/"
    lat, lon = _num(_val(attr, "position/latitude")), _num(_val(attr, "position/longitude"))
    if lat is None or lon is None or (lat == 0 and lon == 0):
        lat = lon = None
    chiavi = (d + "level", "odometer", "position/latitude", "outside_temperature", "climatization/state",
              "doors/lock_state", "charging/state")
    tempi = [x for x in (_quando(attr, k) for k in chiavi) if x]
    return {
        "vin": vin, "t": t,
        "nome": str(_val(attr, "name") or ""), "modello": str(_val(attr, "model") or ""),
        "anno": _val(attr, "model_year"), "targa": str(_val(attr, "license_plate") or ""),
        "soc": _num(_val(attr, d + "level")),
        "autonomia": _num(_val(attr, d + "range"), _num(_val(attr, "drives/total_range"))),
        "km": _num(_val(attr, "odometer")),
        "est": _num(_val(attr, "outside_temperature")),
        "batt_min": _num(_val(attr, d + "battery/temperature_min")),
        "batt_max": _num(_val(attr, d + "battery/temperature_max")),
        "clima": _val(attr, "climatization/state"),
        "clima_target": _num(_val(attr, "climatization/settings/target_temperature")),
        "clima_fine": epoca(_val(attr, "climatization/estimated_date_reached")),
        "clima_senza_cavo": _val(attr, "climatization/settings/climatization_without_external_power"),
        "sbrina": _val(attr, "window_heating/heating_state"),
        "porte": _val(attr, "doors/lock_state"), "porte_aperte": _val(attr, "doors/open_state"),
        "finestrini": _val(attr, "windows/open_state"),
        "ricarica": _val(attr, "charging/state"), "tipo_ricarica": _val(attr, "charging/type"),
        "potenza": _num(_val(attr, "charging/power")), "velocita_ricarica": _num(_val(attr, "charging/rate")),
        "fine_ricarica": epoca(_val(attr, "charging/estimated_date_reached")),
        "obiettivo": _num(_val(attr, "charging/settings/target_level")),
        "spina": _val(attr, "charging/connector/connection_state"),
        "lat": lat, "lon": lon, "pos_t": _quando(attr, "position/latitude") if lat is not None else None,
        "stato": _val(attr, "state"), "online": _val(attr, "connection_state"),
        "aggiornato": max(tempi) if tempi else None,
        "comandi": {"porte": bool(comandi.get("doors/commands/lock-unlock")),
                    "clima": bool(comandi.get("climatization/commands/start-stop")),
                    "sbrina": bool(comandi.get("window_heating/commands/start-stop"))},
    }


def distanza_m(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371008.8 * math.asin(min(1.0, math.sqrt(h)))


def rotta(lat1, lon1, lat2, lon2):
    """Direzione da 1 a 2 in gradi (0 nord, 90 est)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def interpola(x, punti):
    if x <= punti[0][0]:
        return punti[0][1]
    for (x0, y0), (x1, y1) in zip(punti, punti[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0) if x1 > x0 else y1
    return punti[-1][1]


# ---------------------------------------------------------------------------------------------------------------
# archivio: letture, soste, viaggi, ricariche, nomi dei luoghi, percorsi ricostruiti
# ---------------------------------------------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS istantanee (t REAL, vin TEXT, soc REAL, autonomia REAL, km REAL, lat REAL, lon REAL,
    est REAL, batt REAL, clima TEXT, ricarica TEXT, potenza REAL, stato TEXT);
CREATE INDEX IF NOT EXISTS istantanee_vin_t ON istantanee (vin, t);
CREATE TABLE IF NOT EXISTS soste (id INTEGER PRIMARY KEY, vin TEXT, lat REAL, lon REAL, arrivo REAL, partenza REAL,
    km REAL);
CREATE INDEX IF NOT EXISTS soste_vin_arrivo ON soste (vin, arrivo);
CREATE TABLE IF NOT EXISTS viaggi (id INTEGER PRIMARY KEY, vin TEXT, partenza REAL, arrivo REAL, durata_stimata INTEGER,
    da_lat REAL, da_lon REAL, a_lat REAL, a_lon REAL, km REAL, km_stimati INTEGER, soc_da REAL, soc_a REAL,
    temp REAL, temp_fonte TEXT, con_ricarica INTEGER, quota_da REAL, quota_a REAL);
CREATE INDEX IF NOT EXISTS viaggi_vin_partenza ON viaggi (vin, partenza);
CREATE TABLE IF NOT EXISTS ricariche (id INTEGER PRIMARY KEY, vin TEXT, inizio REAL, fine REAL, soc_da REAL,
    soc_a REAL, potenza_max REAL, tipo TEXT, lat REAL, lon REAL);
CREATE TABLE IF NOT EXISTS luoghi (chiave TEXT PRIMARY KEY, lat REAL, lon REAL, nome TEXT, indirizzo TEXT,
    mio INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS percorsi (viaggio INTEGER PRIMARY KEY, geo TEXT);
CREATE TABLE IF NOT EXISTS memoria (vin TEXT PRIMARY KEY, dati TEXT);
"""
CAMPI_VIAGGIO = ("vin", "partenza", "arrivo", "durata_stimata", "da_lat", "da_lon", "a_lat", "a_lon", "km",
                 "km_stimati", "soc_da", "soc_a", "temp", "temp_fonte", "con_ricarica")
CAMPI_ISTANTANEA = ("soc", "autonomia", "km", "lat", "lon", "est", "batt", "clima", "ricarica", "potenza", "stato")


def _chiave(lat, lon):
    return "%.3f,%.3f" % (lat, lon)


class Archivio:
    def __init__(self, file):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(file, check_same_thread=False, timeout=30)
        self.db.row_factory = sqlite3.Row
        with self.lock:
            try:
                self.db.execute("PRAGMA journal_mode=WAL")
            except sqlite3.DatabaseError:
                pass
            self.db.executescript(SCHEMA)
            self.db.commit()
        try:
            os.chmod(file, 0o600)
        except OSError:
            pass

    def chiudi(self):
        with self.lock:
            try:
                self.db.close()
            except sqlite3.Error:
                pass

    def _uno(self, sql, args=()):
        with self.lock:
            r = self.db.execute(sql, args).fetchone()
            return dict(r) if r is not None else None

    def _tutti(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def _fai(self, sql, args=()):
        with self.lock:
            c = self.db.execute(sql, args)
            self.db.commit()
            return c.lastrowid

    # --- letture (una riga quando cambia qualcosa, o ogni mezz'ora)
    def aggiungi_istantanea(self, n):
        n = dict(n)
        mn, mx = n.get("batt_min"), n.get("batt_max")
        n["batt"] = round((mn + mx) / 2, 1) if mn is not None and mx is not None else (mn if mn is not None else mx)
        valori = tuple(n.get(k) for k in CAMPI_ISTANTANEA)
        ult = self._uno("SELECT * FROM istantanee WHERE vin = ? ORDER BY t DESC LIMIT 1", (n["vin"],))
        if ult and n["t"] - ult["t"] < 1800 and tuple(ult[k] for k in CAMPI_ISTANTANEA) == valori:
            return False
        self._fai("INSERT INTO istantanee (t, vin, %s) VALUES (?, ?, %s)"
                  % (", ".join(CAMPI_ISTANTANEA), ", ".join("?" * len(CAMPI_ISTANTANEA))), (n["t"], n["vin"]) + valori)
        return True

    def memoria(self, vin):
        r = self._uno("SELECT dati FROM memoria WHERE vin = ?", (vin,))
        try:
            m = json.loads(r["dati"]) if r else {}
        except ValueError:
            m = {}
        return m if isinstance(m, dict) else {}

    def salva_memoria(self, vin, m):
        self._fai("INSERT OR REPLACE INTO memoria (vin, dati) VALUES (?, ?)", (vin, json.dumps(m)))

    # --- soste, viaggi, ricariche
    def nuova_sosta(self, vin, lat, lon, arrivo, km):
        return self._fai("INSERT INTO soste (vin, lat, lon, arrivo, partenza, km) VALUES (?, ?, ?, ?, NULL, ?)",
                         (vin, lat, lon, arrivo, km))

    def chiudi_sosta(self, sid, partenza):
        self._fai("UPDATE soste SET partenza = ? WHERE id = ?", (partenza, sid))

    def nuovo_viaggio(self, v):
        return self._fai("INSERT INTO viaggi (%s) VALUES (%s)" % (", ".join(CAMPI_VIAGGIO),
                                                                  ", ".join("?" * len(CAMPI_VIAGGIO))),
                         tuple(v.get(k) for k in CAMPI_VIAGGIO))

    def nuova_ricarica(self, vin, t, soc, tipo, lat, lon, potenza):
        return self._fai("INSERT INTO ricariche (vin, inizio, fine, soc_da, soc_a, potenza_max, tipo, lat, lon) "
                         "VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?)", (vin, t, soc, soc, potenza, tipo, lat, lon))

    def aggiorna_ricarica(self, rid, soc, potenza_max, fine=None):
        self._fai("UPDATE ricariche SET soc_a = COALESCE(?, soc_a), potenza_max = ?, fine = COALESCE(?, fine) "
                  "WHERE id = ?", (soc, potenza_max, fine, rid))

    def _dove(self, tabella, colonna, vin, da):
        sql, args = "SELECT * FROM %s WHERE %s >= ?" % (tabella, colonna), [da]
        if vin:
            sql += " AND vin = ?"
            args.append(vin)
        return sql + " ORDER BY %s DESC" % colonna, args

    def viaggi(self, vin=None, da=0.0):
        return self._tutti(*self._dove("viaggi", "partenza", vin, da))

    def soste(self, vin=None, da=0.0):
        sql, args = self._dove("soste", "arrivo", vin, 0.0)
        sql = sql.replace("arrivo >= ?", "(arrivo >= ? OR partenza IS NULL OR partenza >= ?)")
        return self._tutti(sql, [da, da] + args[1:])

    def ricariche(self, vin=None, da=0.0):
        sql, args = self._dove("ricariche", "inizio", vin, da)
        return self._tutti(sql, args)

    def viaggio(self, vid):
        return self._uno("SELECT * FROM viaggi WHERE id = ?", (vid,))

    def viaggi_senza_quota(self, quanti):
        return self._tutti("SELECT * FROM viaggi WHERE quota_da IS NULL ORDER BY partenza DESC LIMIT ?", (quanti,))

    def imposta_quote(self, vid, da, a):
        self._fai("UPDATE viaggi SET quota_da = ?, quota_a = ? WHERE id = ?", (da, a, vid))

    def viaggi_senza_temp(self, quanti):
        return self._tutti("SELECT * FROM viaggi WHERE temp IS NULL AND (temp_fonte IS NULL OR temp_fonte != 'no') "
                           "ORDER BY partenza DESC LIMIT ?", (quanti,))

    def imposta_temp(self, vid, temp, fonte):
        self._fai("UPDATE viaggi SET temp = ?, temp_fonte = ? WHERE id = ?", (temp, fonte, vid))

    def percorso(self, vid):
        r = self._uno("SELECT geo FROM percorsi WHERE viaggio = ?", (vid,))
        try:
            return json.loads(r["geo"]) if r else None
        except ValueError:
            return None

    def salva_percorso(self, vid, geo):
        self._fai("INSERT OR REPLACE INTO percorsi (viaggio, geo) VALUES (?, ?)", (vid, json.dumps(geo)))

    def cancella_diario(self):
        with self.lock:
            for t in ("istantanee", "soste", "viaggi", "ricariche", "percorsi", "memoria"):
                self.db.execute("DELETE FROM %s" % t)
            self.db.commit()

    # --- nomi dei luoghi: quelli dati da Samuele (entro 150 m) vincono sugli indirizzi trovati
    def _miei(self):
        return self._tutti("SELECT * FROM luoghi WHERE mio = 1")

    def nome_luogo(self, lat, lon, miei=None):
        """(nome, indirizzo); (None, None) se l'indirizzo non è ancora stato cercato."""
        if lat is None or lon is None:
            return None, None
        for l in (self._miei() if miei is None else miei):
            if distanza_m(lat, lon, l["lat"], l["lon"]) <= 150:
                return l["nome"], l["indirizzo"] or ""
        r = self._uno("SELECT indirizzo FROM luoghi WHERE chiave = ? AND mio = 0", (_chiave(lat, lon),))
        return (r["indirizzo"], r["indirizzo"]) if r else (None, None)

    def rinomina(self, lat, lon, nome):
        nome = str(nome or "").strip()[:60]
        with self.lock:
            for l in self._miei():
                if distanza_m(lat, lon, l["lat"], l["lon"]) <= 150:
                    if nome:
                        self._fai("UPDATE luoghi SET nome = ? WHERE chiave = ?", (nome, l["chiave"]))
                    else:
                        self._fai("DELETE FROM luoghi WHERE chiave = ?", (l["chiave"],))
                    return
            if nome:
                auto = self._uno("SELECT indirizzo FROM luoghi WHERE chiave = ? AND mio = 0", (_chiave(lat, lon),))
                self._fai("INSERT OR REPLACE INTO luoghi (chiave, lat, lon, nome, indirizzo, mio) VALUES (?, ?, ?, ?, ?, 1)",
                          ("mio:%.5f,%.5f" % (lat, lon), lat, lon, nome, auto["indirizzo"] if auto else ""))

    def salva_indirizzo(self, lat, lon, indirizzo):
        self._fai("INSERT OR REPLACE INTO luoghi (chiave, lat, lon, nome, indirizzo, mio) VALUES (?, ?, ?, '', ?, 0)",
                  (_chiave(lat, lon), lat, lon, indirizzo))

    def luoghi_senza_nome(self, quanti):
        noti = {r["chiave"] for r in self._tutti("SELECT chiave FROM luoghi WHERE mio = 0")}
        miei = self._miei()
        out = []
        for s in self._tutti("SELECT lat, lon FROM soste ORDER BY arrivo DESC LIMIT 400"):
            k = _chiave(s["lat"], s["lon"])
            if k in noti or any(distanza_m(s["lat"], s["lon"], l["lat"], l["lon"]) <= 150 for l in miei):
                continue
            noti.add(k)
            out.append((s["lat"], s["lon"]))
            if len(out) >= quanti:
                break
        return out


# ---------------------------------------------------------------------------------------------------------------
# diario: soste, viaggi e ricariche ricavati dalle letture
# Il cloud dà la posizione solo da parcheggiata (con l'ora in cui si è fermata) e il contachilometri: un viaggio è
# «prima ferma in A, poi ferma in B con più chilometri». La partenza si stima tra l'ultima lettura ancora in A e la
# prima senza posizione (in movimento); se il viaggio sta tutto tra due letture, dai chilometri.
# ---------------------------------------------------------------------------------------------------------------
class Diario:
    def __init__(self, arch):
        self.arch = arch

    def passo(self, n):
        vin, t = n["vin"], n["t"]
        m = self.arch.memoria(vin)
        eventi = []
        s = m.get("sosta")
        if n.get("lat") is not None and n.get("lon") is not None:
            arrivo = n.get("pos_t") or t
            if s is None:
                sid = self.arch.nuova_sosta(vin, n["lat"], n["lon"], arrivo, n.get("km"))
                m["sosta"] = self._sosta(sid, n, arrivo, t)
                eventi.append("sosta")
            else:
                spostata = distanza_m(s["lat"], s["lon"], n["lat"], n["lon"]) > 150
                km_fatti = None
                if n.get("km") is not None and s.get("km") is not None and n["km"] >= s["km"]:
                    km_fatti = n["km"] - s["km"]
                if not spostata and not (km_fatti is not None and km_fatti >= 0.5):
                    s["visto"] = t
                    for k in ("soc", "est"):
                        if n.get(k) is not None:
                            s[k] = n[k]
                    if s.get("km") is None and n.get("km") is not None:
                        s["km"] = n["km"]
                    m.pop("via", None)
                else:
                    self._viaggio(vin, m, s, n, arrivo, km_fatti)
                    eventi += ["viaggio", "sosta"]
        elif s is not None:
            via = m.get("via")
            if via is None:
                via = m["via"] = {"dal": t, "temp": [], "ricarica": False}
            if n.get("est") is not None and len(via["temp"]) < 200:
                via["temp"].append(n["est"])
            if n.get("ricarica") == "charging":
                via["ricarica"] = True
        # ricariche (anche durante un viaggio, se il cloud lo dice)
        r = m.get("ricarica")
        if n.get("ricarica") == "charging":
            pot = n.get("potenza") or 0.0
            if r is None:
                pos = m.get("sosta") or {}
                rid = self.arch.nuova_ricarica(vin, t, n.get("soc"), n.get("tipo_ricarica"), pos.get("lat"),
                                               pos.get("lon"), pot)
                m["ricarica"] = {"id": rid, "pmax": pot}
            else:
                r["pmax"] = max(r.get("pmax") or 0.0, pot)
                self.arch.aggiorna_ricarica(r["id"], n.get("soc"), r["pmax"])
        elif r is not None:
            self.arch.aggiorna_ricarica(r["id"], n.get("soc"), r.get("pmax") or 0.0, fine=t)
            m.pop("ricarica", None)
            eventi.append("ricarica")
        self.arch.salva_memoria(vin, m)
        return eventi

    @staticmethod
    def _sosta(sid, n, arrivo, t):
        return {"id": sid, "lat": n["lat"], "lon": n["lon"], "arrivo": arrivo, "km": n.get("km"), "visto": t,
                "soc": n.get("soc"), "est": n.get("est")}

    def _viaggio(self, vin, m, s, n, arrivo, km_fatti):
        via = m.get("via")
        if arrivo <= s["visto"]:
            arrivo = n["t"]
        if km_fatti is not None and km_fatti >= 0.1:
            km, km_stimati = km_fatti, 0
        else:
            km, km_stimati = distanza_m(s["lat"], s["lon"], n["lat"], n["lon"]) * 1.3 / 1000.0, 1
        finestra = max(60.0, arrivo - s["visto"])
        if via and via["dal"] - s["visto"] <= 2.5 * INTERVALLO:
            partenza, stimata = (s["visto"] + via["dal"]) / 2.0, 0
        else:
            media = 35.0 if km < 20 else 65.0
            partenza, stimata = arrivo - min(finestra, km / media * 3600.0), 1
        partenza = min(partenza, arrivo - 60.0)
        temps = [x for x in [s.get("est")] + (via["temp"] if via else []) + [n.get("est")] if x is not None]
        soc_da, soc_a = s.get("soc"), n.get("soc")
        con_ricarica = bool(via and via.get("ricarica")) or (soc_da is not None and soc_a is not None
                                                             and soc_a > soc_da + 1)
        self.arch.nuovo_viaggio({
            "vin": vin, "partenza": partenza, "arrivo": arrivo, "durata_stimata": stimata,
            "da_lat": s["lat"], "da_lon": s["lon"], "a_lat": n["lat"], "a_lon": n["lon"], "km": round(km, 2),
            "km_stimati": km_stimati, "soc_da": soc_da, "soc_a": soc_a,
            "temp": round(sum(temps) / len(temps), 1) if temps else None, "temp_fonte": "auto" if temps else None,
            "con_ricarica": 1 if con_ricarica else 0})
        self.arch.chiudi_sosta(s["id"], partenza)
        sid = self.arch.nuova_sosta(vin, n["lat"], n["lon"], arrivo, n.get("km"))
        m["sosta"] = self._sosta(sid, n, arrivo, n["t"])
        m.pop("via", None)


# ---------------------------------------------------------------------------------------------------------------
# modello dei consumi: fisica dell'auto + i due fattori imparati dai viaggi veri (stile di guida e clima)
# ---------------------------------------------------------------------------------------------------------------
def energia_trazione(d_m, v_kmh, dh_m, t_c, auto, vento_ms=0.0, pioggia=False, k_guida=1.0):
    """kWh dalla batteria per muovere l'auto su un tratto di d_m metri a v_kmh, con dislivello dh_m (+ salita),
    temperatura t_c, vento contrario vento_ms (negativo = a favore). Negativo in discesa (recupero)."""
    if d_m <= 0:
        return 0.0
    v = max(float(v_kmh), 3.0) / 3.6
    m = auto["massa_kg"] + auto["carico_kg"]
    rho = 1.225 * 288.15 / (273.15 + max(-40.0, min(50.0, t_c)))     # aria fredda = più densa
    va = v + vento_ms
    f_aria = 0.5 * rho * auto["cda"] * va * abs(va)
    crr = auto["crr"] * (1 + 0.004 * max(0.0, 20.0 - t_c)) * (1.15 if pioggia else 1.0)   # gomme fredde, bagnato
    f_rot = crr * m * G
    f_pend = m * G * dh_m / d_m
    lavoro = (f_aria + f_rot + f_pend) * d_m
    e = lavoro / auto["rend_motore"] / 3.6e6 if lavoro >= 0 else lavoro * auto["rend_recupero"] / 3.6e6
    k_acc = 0.022 if v_kmh < 60 else (0.008 if v_kmh < 100 else 0.003)   # accelerazioni e frenate: più in città
    e += k_acc * m / 1000.0 * d_m / 1000.0
    if e > 0:
        e *= (1 + 0.005 * max(0.0, 15.0 - t_c)) * k_guida              # batteria fredda e stile di guida
    return e


def potenza_clima_kw(t_c, auto, abitacolo_c=21.0, k_clima=1.0):
    """Potenza media del clima con l'auto in marcia (riscaldamento sotto i 16 °C, aria condizionata sopra i 24)."""
    if t_c < abitacolo_c - 5:
        p = min(5.5, 0.13 * (abitacolo_c - t_c))
        if auto.get("pompa_calore") and t_c > -10:
            p *= 0.55
    elif t_c > 24:
        p = 0.35 + 0.09 * (t_c - 24)
    else:
        p = 0.15
    return p * k_clima


def preriscaldo_kwh(t_c, auto, abitacolo_c=21.0, k_clima=1.0):
    """Energia per portare l'abitacolo a temperatura alla partenza (se il clima da remoto non l'ha già fatto)."""
    if t_c >= abitacolo_c - 5:
        return 0.0
    e = 0.05 * (abitacolo_c - t_c)
    if auto.get("pompa_calore") and t_c > -10:
        e *= 0.7
    return e * k_clima


def viaggio_valido(v):
    """Un viaggio che dice qualcosa sui consumi: almeno 5 km, 2% di batteria, senza ricariche in mezzo."""
    try:
        ore = (v["arrivo"] - v["partenza"]) / 3600.0
        return (v["km"] is not None and v["km"] >= 5 and not v.get("con_ricarica") and v["soc_da"] is not None
                and v["soc_a"] is not None and v["soc_da"] - v["soc_a"] >= 2 and ore > 0 and v["km"] / ore <= 160)
    except (KeyError, TypeError):
        return False


def componenti(v, auto, abitacolo_c=21.0):
    """kWh previsti per un viaggio registrato, con i fattori a 1: (trazione, clima, fisso, velocità media, °C)."""
    km = v["km"]
    ore = max((v["arrivo"] - v["partenza"]) / 3600.0, km / 130.0)
    vel = km / ore
    t = v["temp"] if v.get("temp") is not None else 15.0
    dh = (v["quota_a"] - v["quota_da"]) if v.get("quota_a") is not None and v.get("quota_da") is not None else 0.0
    trazione = energia_trazione(km * 1000.0, vel, dh, t, auto)
    clima = potenza_clima_kw(t, auto, abitacolo_c) * ore + preriscaldo_kwh(t, auto, abitacolo_c)
    fisso = (auto["elettronica_kw"] + auto["musica_kw"]) * ore
    return trazione, clima, fisso, vel, t


def calibra(viaggi, auto, abitacolo_c=21.0):
    """I due fattori personali, dai viaggi veri: k_guida (quanto consumi in marcia rispetto al modello) e k_clima
    (quanto pesa il clima). Minimi quadrati pesati, con un'attesa iniziale di 1 che vale come 3 viaggi: pochi viaggi
    non spostano troppo, molti viaggi decidono loro."""
    righe = []
    for v in viaggi:
        if not viaggio_valido(v):
            continue
        oss = (v["soc_da"] - v["soc_a"]) / 100.0 * auto["capacita_kwh"]
        tr, cl, fi, _, _ = componenti(v, auto, abitacolo_c)
        if tr <= 0.05:
            continue
        prev = tr + cl + fi
        w = 1.0 / (0.1225 + 0.01 * prev * prev)    # ±0,35 kWh per la batteria letta a punti interi, ±10% il resto
        if v.get("durata_stimata"):
            w *= 0.5
        righe.append((tr, cl, oss - fi, w, oss, fi, v["km"]))
    if not righe:
        return {"k_guida": 1.0, "k_clima": 1.0, "viaggi": 0, "km": 0.0, "errore_pct": None, "tipici": True}
    n = len(righe)
    la = 3.0 * sum(r[3] * r[0] * r[0] for r in righe) / n
    lb = 3.0 * sum(r[3] * r[1] * r[1] for r in righe) / n + 1e-9
    a11 = sum(r[3] * r[0] * r[0] for r in righe) + la
    a12 = sum(r[3] * r[0] * r[1] for r in righe)
    a22 = sum(r[3] * r[1] * r[1] for r in righe) + lb
    b1 = sum(r[3] * r[0] * r[2] for r in righe) + la
    b2 = sum(r[3] * r[1] * r[2] for r in righe) + lb
    det = a11 * a22 - a12 * a12
    if abs(det) < 1e-12:
        kg, kc = 1.0, 1.0
    else:
        kg = (b1 * a22 - a12 * b2) / det
        kc = (a11 * b2 - a12 * b1) / det
    kg = max(0.6, min(1.8, kg))
    kc = max(0.3, min(3.0, kc))
    err = sum(abs(kg * r[0] + kc * r[1] + r[5] - r[4]) / max(r[4], 0.5) for r in righe) / n * 100.0
    return {"k_guida": round(kg, 3), "k_clima": round(kc, 3), "viaggi": n, "km": round(sum(r[6] for r in righe), 1),
            "errore_pct": round(err, 1), "tipici": n < 5}


def _mediana(coppie):
    coppie = sorted((x, w) for x, w in coppie if x is not None and w > 0)
    if not coppie:
        return None
    meta, acc = sum(w for _, w in coppie) / 2.0, 0.0
    for x, w in coppie:
        acc += w
        if acc >= meta:
            return x
    return coppie[-1][0]


def consumo_100(t_c, vel, lung_km, auto, cal, abitacolo_c=21.0, musica=True):
    """kWh per 100 km in piano a vel km/h e t_c °C, con viaggi lunghi lung_km (il preriscaldo si ripete a ogni viaggio)."""
    ore = 100.0 / vel
    tr = energia_trazione(100000.0, vel, 0.0, t_c, auto, k_guida=cal["k_guida"])
    cl = (potenza_clima_kw(t_c, auto, abitacolo_c, cal["k_clima"]) * ore
          + preriscaldo_kwh(t_c, auto, abitacolo_c, cal["k_clima"]) * 100.0 / max(lung_km, 5.0))
    return tr + cl + (auto["elettronica_kw"] + (auto["musica_kw"] if musica else 0.0)) * ore


FASCE = ((-25, -5), (-5, 0), (0, 5), (5, 10), (10, 15), (15, 20), (20, 25), (25, 30), (30, 50))


def rendimento(viaggi, auto, abitacolo_c=21.0):
    """Quanto rende la batteria a ogni temperatura: le fasce misurate (dai viaggi veri) e la curva del modello
    calibrato, alla velocità e con la lunghezza tipiche dei viaggi di Samuele. rendimento_pct = km rispetto a 20 °C."""
    cal = calibra(viaggi, auto, abitacolo_c)
    validi = [v for v in viaggi if viaggio_valido(v) and v.get("temp") is not None]
    vel = _mediana([(componenti(v, auto, abitacolo_c)[3], v["km"]) for v in validi]) or 60.0
    lung = _mediana([(v["km"], 1.0) for v in validi]) or 20.0
    curva = []
    for t in range(-10, 36):
        k = consumo_100(t, vel, lung, auto, cal, abitacolo_c)
        curva.append({"t": t, "kwh_100": round(k, 1), "autonomia_100": int(round(auto["capacita_kwh"] / k * 100))})
    rif = next(c["kwh_100"] for c in curva if c["t"] == 20)
    for c in curva:
        c["rendimento_pct"] = int(round(rif / c["kwh_100"] * 100))
    fasce = []
    for da, a in FASCE:
        vv = [v for v in validi if da <= v["temp"] < a]
        if not vv:
            continue
        km = sum(v["km"] for v in vv)
        pct = sum(v["soc_da"] - v["soc_a"] for v in vv)
        kwh100 = pct / 100.0 * auto["capacita_kwh"] / km * 100.0
        fasce.append({"da": da, "a": a, "viaggi": len(vv), "km": round(km, 1), "kwh_100": round(kwh100, 1),
                      "km_per_pct": round(km / pct, 2), "autonomia_100": int(round(km / pct * 100)),
                      "rendimento_pct": int(round(rif / kwh100 * 100)),
                      "temp_media": round(sum(v["temp"] for v in vv) / len(vv), 1)})
    return {"fasce": fasce, "curva": curva, "calibrazione": cal, "velocita_tipica": int(round(vel)),
            "viaggio_tipico_km": round(lung, 1), "capacita_kwh": auto["capacita_kwh"], "viaggi_validi": len(validi)}


# ---------------------------------------------------------------------------------------------------------------
# ricarica rapida (ID. Buzz 77 kWh: picco 170 kW, 10→80% in circa 30 minuti a batteria calda)
# ---------------------------------------------------------------------------------------------------------------
CURVA_DC = [(0, 145), (10, 165), (25, 170), (35, 160), (45, 135), (55, 115), (65, 95), (75, 80), (80, 70), (90, 40),
            (100, 12)]
TEMP_RICARICA = [(-10, 0.25), (0, 0.4), (10, 0.65), (15, 0.8), (20, 0.95), (25, 1.0)]


def minuti_ricarica(da, a, kw_colonnina, auto, t_batteria):
    """Minuti per caricare da da% ad a% (compresi 3 minuti per collegarsi e pagare)."""
    if a <= da + 1e-9:
        return 0.0
    scala = auto["dc_max_kw"] / 170.0
    f = interpola(t_batteria, TEMP_RICARICA)
    minuti, s = 0.0, float(da)
    while s < a - 1e-9:
        passo = min(1.0, a - s)
        p = max(2.0, min(float(kw_colonnina), interpola(s + passo / 2, CURVA_DC) * scala) * f)
        minuti += auto["capacita_kwh"] * passo / 100.0 / p * 60.0
        s += passo
    return minuti + 3.0


# ---------------------------------------------------------------------------------------------------------------
# servizi pubblici (senza chiavi): percorsi, quote, meteo, colonnine, indirizzi
# ---------------------------------------------------------------------------------------------------------------
def _kw(s):
    """«150 kW», «50kW;22 kW», «350», «150000 W» → il massimo in kW; None se non c'è un numero."""
    if not s:
        return None
    migliore = None
    for m in re.finditer(r"(\d+(?:[.,]\d+)?)\s*(kw|kva|w)?\b", str(s).lower()):
        v = float(m.group(1).replace(",", "."))
        u = m.group(2) or ""
        if u == "w" or (not u and v > 1000):
            v /= 1000.0
        migliore = v if migliore is None else max(migliore, v)
    return migliore


def leggi_colonnina(tags, lat, lon):
    """Una colonnina di OpenStreetMap utile all'auto (ricarica rapida CCS); None se non lo è."""
    if tags.get("access") in ("private", "no") or tags.get("motorcar") == "no":
        return None
    nomi = " ".join(str(tags.get(k, "")) for k in ("operator", "network", "brand", "name")).lower()
    ionity = "ionity" in nomi
    ccs = ionity or any(k.startswith("socket:type2_combo") and str(v) not in ("0", "no") for k, v in tags.items())
    if not ccs:
        return None
    kw = (_kw(tags.get("socket:type2_combo:output")) or _kw(tags.get("charging_station:output"))
          or _kw(tags.get("maxpower")) or _kw(tags.get("output")))
    if ionity and not kw:
        kw = 350.0
    if kw is not None and kw < 20:
        return None
    posti = tags.get("capacity")
    return {"lat": lat, "lon": lon, "nome": str(tags.get("name") or tags.get("operator") or tags.get("network")
                                              or "Colonnina rapida")[:80],
            "operatore": str(tags.get("operator") or tags.get("network") or tags.get("brand") or "")[:60],
            "kw": float(kw) if kw else 50.0, "kw_noto": kw is not None, "ionity": ionity,
            "posti": int(posti) if str(posti or "").isdigit() else None}


class Servizi:
    def __init__(self):
        self.osrm = os.environ.get("ATLANTE_AUTO_OSRM", "https://router.project-osrm.org/route/v1/driving/")
        self.meteo_url = os.environ.get("ATLANTE_AUTO_METEO", "https://api.open-meteo.com/v1/")
        self.overpass = [x for x in os.environ.get(
            "ATLANTE_AUTO_OVERPASS",
            "https://overpass-api.de/api/interpreter,https://overpass.kumi.systems/api/interpreter").split(",") if x]
        self.nominatim = os.environ.get("ATLANTE_AUTO_NOMINATIM", "https://nominatim.openstreetmap.org/")
        self._cache, self._cache_lock = {}, threading.Lock()
        self._nom_lock, self._nom_ultima = threading.Lock(), 0.0

    def _json(self, url, tempo=25, dati=None):
        req = urllib.request.Request(url, data=dati, headers={"User-Agent": UA, "Accept": "application/json",
                                                              "Accept-Language": "it"})
        with urllib.request.urlopen(req, timeout=tempo) as r:
            return json.loads(r.read(25 * 1024 * 1024).decode("utf-8"))

    def _memo(self, chiave, durata, fai):
        with self._cache_lock:
            x = self._cache.get(chiave)
            if x and x[0] > time.time():
                return x[1]
        valore = fai()
        with self._cache_lock:
            if len(self._cache) > 300:
                self._cache.clear()
            self._cache[chiave] = (time.time() + durata, valore)
        return valore

    def percorso(self, da, a):
        """{"coord": [[lon, lat]…], "dist": [m per tratto], "dur": [s per tratto], "m", "s"} (OSRM, in auto)."""
        def fai():
            url = ("%s%.6f,%.6f;%.6f,%.6f?overview=full&geometries=geojson&annotations=distance,duration&steps=false"
                   % (self.osrm, da[1], da[0], a[1], a[0]))
            j = self._json(url, 40)
            if j.get("code") != "Ok" or not j.get("routes"):
                raise ErroreAuto(502, "Nessun percorso trovato tra i due punti (%s)" % j.get("code"))
            r = j["routes"][0]
            coord = r["geometry"]["coordinates"]
            dist, dur = [], []
            for leg in r.get("legs") or []:
                an = leg.get("annotation") or {}
                dist += an.get("distance") or []
                dur += an.get("duration") or []
            if len(dist) != len(coord) - 1 or len(dur) != len(dist):
                dist = [distanza_m(coord[i][1], coord[i][0], coord[i + 1][1], coord[i + 1][0])
                        for i in range(len(coord) - 1)]
                tot = sum(dist) or 1.0
                dur = [x / tot * float(r.get("duration") or tot / 15.0) for x in dist]
            return {"coord": coord, "dist": dist, "dur": dur, "m": float(r.get("distance") or sum(dist)),
                    "s": float(r.get("duration") or sum(dur))}
        return self._memo(("percorso", round(da[0], 5), round(da[1], 5), round(a[0], 5), round(a[1], 5)), 3600, fai)

    def quote(self, punti):
        """Quota in metri di ogni punto (lat, lon), dal modello del terreno di Open-Meteo (100 punti per richiesta)."""
        out = []
        for i in range(0, len(punti), 100):
            pezzo = punti[i:i + 100]
            chiave = ("quote",) + tuple((round(p[0], 4), round(p[1], 4)) for p in pezzo)

            def fai(pezzo=pezzo):
                url = "%selevation?latitude=%s&longitude=%s" % (
                    self.meteo_url, ",".join("%.5f" % p[0] for p in pezzo), ",".join("%.5f" % p[1] for p in pezzo))
                el = self._json(url, 30).get("elevation") or []
                if len(el) != len(pezzo):
                    raise ValueError("quote incomplete")
                return [float(x) if x is not None else 0.0 for x in el]
            out += self._memo(chiave, 7 * 86400, fai)
        return out

    def meteo(self, punti, giorno_da, giorno_a):
        """Previsioni orarie (ore UTC) per ogni punto: [{"t": [epoca], "temp", "vento" (m/s), "dir" (da dove
        viene), "pioggia" (mm)}]. Anche per giorni passati recenti (fino a 3 mesi)."""
        def fai():
            url = ("%sforecast?latitude=%s&longitude=%s&hourly=temperature_2m,precipitation,wind_speed_10m,"
                   "wind_direction_10m&wind_speed_unit=ms&timezone=GMT&timeformat=unixtime&start_date=%s&end_date=%s"
                   % (self.meteo_url, ",".join("%.4f" % p[0] for p in punti), ",".join("%.4f" % p[1] for p in punti),
                      giorno_da, giorno_a))
            j = self._json(url, 30)
            j = j if isinstance(j, list) else [j]
            out = []
            for x in j:
                h = x.get("hourly") or {}
                out.append({"t": h.get("time") or [], "temp": h.get("temperature_2m") or [],
                            "vento": h.get("wind_speed_10m") or [], "dir": h.get("wind_direction_10m") or [],
                            "pioggia": h.get("precipitation") or []})
            if len(out) != len(punti):
                raise ValueError("meteo incompleto")
            return out
        chiave = ("meteo", giorno_da, giorno_a) + tuple((round(p[0], 3), round(p[1], 3)) for p in punti)
        return self._memo(chiave, 1800, fai)

    def temperatura_passata(self, lat, lon, quando):
        giorno = datetime.datetime.fromtimestamp(quando, datetime.timezone.utc).strftime("%Y-%m-%d")
        serie = self.meteo([(lat, lon)], giorno, giorno)[0]
        return valore_ora(serie, "temp", quando)

    def colonnine(self, punti, raggio=2000):
        """Le colonnine rapide CCS di OpenStreetMap entro raggio metri dalla linea dei punti (lat, lon)."""
        trovate = {}
        for i in range(0, len(punti), 120):
            pezzo = punti[max(0, i - 1):i + 120]
            if len(pezzo) < 2:
                continue
            linea = ",".join("%.5f,%.5f" % (p[0], p[1]) for p in pezzo)
            q = ('[out:json][timeout:80];(node["amenity"="charging_station"](around:%d,%s);'
                 'way["amenity"="charging_station"](around:%d,%s););out center tags;') % (raggio, linea, raggio, linea)
            j = self._memo(("overpass", q), 6 * 3600, lambda q=q: self._overpass(q))
            for el in j.get("elements") or []:
                lat = el.get("lat", (el.get("center") or {}).get("lat"))
                lon = el.get("lon", (el.get("center") or {}).get("lon"))
                if lat is None or lon is None:
                    continue
                c = leggi_colonnina(el.get("tags") or {}, float(lat), float(lon))
                if c is None:
                    continue
                k = (round(c["lat"], 3), round(c["lon"], 3))
                if k not in trovate or c["kw"] > trovate[k]["kw"]:
                    trovate[k] = c
        return list(trovate.values())

    def _overpass(self, q):
        dati = urllib.parse.urlencode({"data": q}).encode("utf-8")
        errore = None
        for base in self.overpass:
            try:
                return self._json(base, 100, dati)
            except Exception as e:  # noqa: BLE001  si prova il server seguente
                errore = e
        raise ErroreAuto(502, "Colonnine non disponibili adesso (OpenStreetMap/Overpass): %s" % _breve(errore))

    def _nominatim(self, percorso):
        with self._nom_lock:     # Nominatim: al più una richiesta al secondo
            attesa = self._nom_ultima + 1.1 - time.time()
            if attesa > 0:
                time.sleep(attesa)
            try:
                return self._json(self.nominatim + percorso + "&accept-language=it", 20)
            finally:
                self._nom_ultima = time.time()

    def cerca(self, testo):
        j = self._memo(("cerca", testo.lower()), 86400,
                       lambda: self._nominatim("search?format=jsonv2&limit=6&q=" + urllib.parse.quote(testo)))
        return [{"nome": str(x.get("display_name") or "")[:160], "lat": float(x["lat"]), "lon": float(x["lon"])}
                for x in (j if isinstance(j, list) else []) if "lat" in x and "lon" in x]

    def indirizzo(self, lat, lon):
        j = self._nominatim("reverse?format=jsonv2&zoom=17&addressdetails=1&lat=%.6f&lon=%.6f" % (lat, lon))
        a = j.get("address") or {}
        via = a.get("road") or a.get("pedestrian") or a.get("square") or a.get("amenity") or ""
        if via and a.get("house_number"):
            via += " " + a["house_number"]
        posto = a.get("village") or a.get("town") or a.get("city") or a.get("hamlet") or a.get("suburb") or ""
        return (", ".join(x for x in (via, posto) if x) or str(j.get("display_name") or ""))[:120]

    def paese(self, lat, lon):
        def fai():
            j = self._nominatim("reverse?format=jsonv2&zoom=3&lat=%.5f&lon=%.5f" % (lat, lon))
            return str((j.get("address") or {}).get("country_code") or "").upper()
        return self._memo(("paese", round(lat, 2), round(lon, 2)), 30 * 86400, fai)


def valore_ora(serie, campo, quando):
    """Il valore dell'ora più vicina a quando (secondi) in una serie oraria di Open-Meteo; None se non c'è."""
    tt, vv = serie.get("t") or [], serie.get(campo) or []
    if not tt or len(tt) != len(vv):
        return None
    i = min(range(len(tt)), key=lambda k: abs(tt[k] - quando))
    if abs(tt[i] - quando) > 3 * 3600:
        return None
    return _num(vv[i])


# ---------------------------------------------------------------------------------------------------------------
# pianificatore: percorso a pezzi di ~1 km, energia di ogni pezzo, colonnine lungo la strada, soste scelte con la
# programmazione dinamica (il costo più basso: fasce di arrivo di Samuele, minuti di ricarica, deviazioni)
# ---------------------------------------------------------------------------------------------------------------
FISSO_SOSTA = 8.0          # una sosta in più «costa» come 16 minuti di ricarica: niente soste inutili


def penalita_arrivo(soc, r):
    """Quanto non piace arrivare a una colonnina con soc%: 0 all'ideale; None sotto la tolleranza minima."""
    if soc < r["toll_min"] - 1e-9:
        return None
    if r["arrivo_min"] <= soc <= r["arrivo_ideale"]:
        return (r["arrivo_ideale"] - soc) * 0.5
    if r["arrivo_ideale"] < soc <= r["toll_max"]:
        return 6.0 + (soc - r["arrivo_ideale"])
    if soc < r["arrivo_min"]:
        return 10.0 + (r["arrivo_min"] - soc) * 3.0
    return 40.0 + (soc - r["toll_max"]) * 1.5


def penalita_destinazione(soc, r):
    if soc >= r["destinazione_min"] - 1e-9:
        return 0.0
    if soc >= r["toll_min"] - 1e-9:
        return 25.0 + (r["destinazione_min"] - soc) * 3.0
    return None


def fascia(soc, r):
    if soc is None:
        return ""
    if r["arrivo_min"] - 0.5 <= soc <= r["arrivo_ideale"] + 0.5:
        return "ideale"
    if r["toll_min"] - 0.5 <= soc <= r["toll_max"] + 0.5:
        return "tollerata"
    return "fuori"


def t_batteria_alla_colonnina(t_aria, precondiziona):
    return 25.0 if precondiziona else min(25.0, t_aria + 12.0)


def scegli_soste(e_tot, soc0, colonnine, auto, r, opz):
    """Le soste di ricarica migliori. colonnine: [{"s" (km), "e" (kWh dalla partenza fin lì), "dev_kwh", "dev_min",
    "kw", "kw_noto", "ionity", "t" (°C), …}] in qualsiasi ordine. Ogni sosta ricarica fino a ricarica_max, l'ultima
    quanto basta per arrivare con l'arrivo ideale (almeno destinazione_min).
    Restituisce {"soste": [{…, "arrivo_soc", "partenza_soc", "minuti"}], "arrivo_soc", "riuscito", "motivo"}."""
    cap = auto["capacita_kwh"]
    pre = float(opz.get("precond_kwh") or 0.0)
    obiettivo = max(r["destinazione_min"], r["arrivo_ideale"])
    massimo = r["ricarica_max"]
    memo = {}

    def pct(kwh):
        return kwh / cap * 100.0

    def costo_sosta(c, da, a):
        tb = t_batteria_alla_colonnina(c.get("t", 15.0), opz.get("precondiziona", True))
        k = (round(c["kw"]), round(tb), round(da * 2) / 2, round(a * 2) / 2)
        if k not in memo:
            memo[k] = minuti_ricarica(k[2], k[3], c["kw"], auto, tb)
        bonus = -4.0 if (opz.get("ionity_prima", True) and c.get("ionity")) else 0.0
        return FISSO_SOSTA + 0.5 * memo[k] + c.get("dev_min", 0.0) + bonus + (0.0 if c.get("kw_noto", True) else 3.0)

    cc = sorted(colonnine, key=lambda c: c["s"])
    n = len(cc)
    costo, prec, arr = [None] * n, [None] * n, [None] * n
    for j, c in enumerate(cc):
        a = soc0 - pct(c["e"] + c.get("dev_kwh", 0.0) + pre)
        p = penalita_arrivo(a, r)
        if p is not None:
            costo[j], arr[j] = p + costo_sosta(c, a, max(a, massimo)), a
    for j in range(n):
        if costo[j] is None:
            continue
        for k in range(j + 1, n):
            if cc[k]["s"] - cc[j]["s"] < 1.0:
                continue
            uso = pct(cc[k]["e"] - cc[j]["e"] + cc[j].get("dev_kwh", 0.0) + cc[k].get("dev_kwh", 0.0) + pre)
            if uso > massimo + 60:   # troppo lontana (anche con le discese): le seguenti lo sono ancora di più
                break
            a = max(arr[j], massimo) - uso
            p = penalita_arrivo(a, r)
            if p is None:
                continue
            nuovo = costo[j] + p + costo_sosta(cc[k], a, max(a, massimo))
            if costo[k] is None or nuovo < costo[k] - 1e-9:
                costo[k], prec[k], arr[k] = nuovo, j, a
    # all'arrivo: diretto, o dall'ultima sosta caricando solo quanto serve
    migliore = None
    a = soc0 - pct(e_tot)
    pd = penalita_destinazione(a, r)
    if pd is not None:
        migliore = (pd, None, a, None)
    for j in range(n):
        if costo[j] is None:
            continue
        serve = pct(e_tot - cc[j]["e"] + cc[j].get("dev_kwh", 0.0))
        part = max(arr[j], min(massimo, serve + obiettivo))
        a = part - serve
        pd = penalita_destinazione(a, r)
        if pd is None:
            continue
        tot = costo[j] - costo_sosta(cc[j], arr[j], max(arr[j], massimo)) + costo_sosta(cc[j], arr[j], part) + pd
        if migliore is None or tot < migliore[0] - 1e-9:
            migliore = (tot, j, a, part)
    if migliore is None:
        return {"soste": [], "arrivo_soc": soc0 - pct(e_tot), "riuscito": False,
                "motivo": "Con le colonnine rapide trovate non si arriva senza scendere sotto il %d%%"
                          % int(r["toll_min"])}
    _, j, a_dest, part = migliore
    catena = []
    while j is not None:
        catena.append(j)
        j = prec[j]
    catena.reverse()
    soste = []
    for i, j in enumerate(catena):
        c = dict(cc[j])
        partenza = part if i == len(catena) - 1 else max(arr[j], massimo)
        tb = t_batteria_alla_colonnina(c.get("t", 15.0), opz.get("precondiziona", True))
        c.update({"arrivo_soc": arr[j], "partenza_soc": partenza,
                  "minuti": minuti_ricarica(arr[j], partenza, c["kw"], auto, tb)})
        soste.append(c)
    return {"soste": soste, "arrivo_soc": a_dest, "riuscito": True, "motivo": ""}


def _proietta(lat, lon, punti, cum):
    """La colonnina sul percorso: (indice del pezzo, frazione 0–1, distanza in m, lato: 1 sinistra / -1 destra)."""
    migliore = None
    cl = math.cos(math.radians(lat))
    for i in range(len(punti) - 1):
        (la1, lo1), (la2, lo2) = punti[i], punti[i + 1]
        if abs(la1 - lat) > 0.05 and abs(la2 - lat) > 0.05:
            continue
        ax, ay = (lo1 - lon) * cl * 111320.0, (la1 - lat) * 110540.0
        bx, by = (lo2 - lon) * cl * 111320.0, (la2 - lat) * 110540.0
        dx, dy = bx - ax, by - ay
        ll = dx * dx + dy * dy
        u = 0.0 if ll <= 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / ll))
        px, py = ax + u * dx, ay + u * dy
        d = math.hypot(px, py)
        if migliore is None or d < migliore[2]:
            lato = 1 if (dx * (-py) - dy * (-px)) > 0 else -1     # dov'è la colonnina rispetto al senso di marcia
            migliore = (i, u, d, lato)
    return migliore


def _semplifica(coord, massimo=1500):
    passo = max(1, len(coord) // massimo)
    out = [[round(p[1], 5), round(p[0], 5)] for p in coord[::passo]]
    if (len(coord) - 1) % passo:
        out.append([round(coord[-1][1], 5), round(coord[-1][0], 5)])
    return out


def pianifica(srv, imp, cal, da, a, soc0, partenza, opz, clima_acceso=False, temp_riserva=15.0):
    """Il viaggio da da a a (lat, lon) partendo con soc0% alle partenza (secondi): energia di ogni km (velocità,
    salite, temperatura, vento e pioggia previsti, clima, elettronica, musica, stile di guida imparato), colonnine
    rapide lungo la strada e soste di ricarica secondo le regole. opz sovrascrive le impostazioni del viaggio."""
    auto, r = dict(imp["auto"]), dict(imp["regole"])
    vo = dict(imp["viaggio"])
    for k in ("velocita_max", "abitacolo_c", "musica", "ionity_prima", "precondiziona"):
        if k in opz and opz[k] is not None:
            vo[k] = opz[k]
    if opz.get("carico_kg") is not None:
        auto["carico_kg"] = float(opz["carico_kg"])
    vmax, abit = float(vo["velocita_max"]), float(vo["abitacolo_c"])
    avvisi = []
    rp = srv.percorso(da, a)
    coord, dist, dur = rp["coord"], rp["dist"], rp["dur"]
    if len(coord) < 2:
        raise ErroreAuto(502, "Percorso vuoto")
    # pezzi di circa 1 km
    pezzi, acc_d, acc_t, i0 = [], 0.0, 0.0, 0
    for i in range(len(dist)):
        acc_d += dist[i]
        acc_t += dur[i]
        if acc_d >= 1000 or i == len(dist) - 1:
            if acc_d > 1:
                pezzi.append([i0, i + 1, acc_d, acc_t])
            i0, acc_d, acc_t = i + 1, 0.0, 0.0
    if not pezzi:
        raise ErroreAuto(400, "Partenza e arrivo coincidono")
    punti = [(coord[p[0]][1], coord[p[0]][0]) for p in pezzi] + [(coord[pezzi[-1][1]][1], coord[pezzi[-1][1]][0])]
    vel, tempo = [], [0.0]
    for p in pezzi:
        v = p[2] / p[3] * 3.6 if p[3] > 0 else 50.0
        v = max(5.0, min(v, vmax))
        vel.append(v)
        tempo.append(tempo[-1] + p[2] / (v / 3.6))
    cum = [0.0]
    for p in pezzi:
        cum.append(cum[-1] + p[2] / 1000.0)
    # quote
    try:
        quota = srv.quote(punti)
    except Exception:  # noqa: BLE001
        quota = [0.0] * len(punti)
        avvisi.append("Quote non disponibili: salite e discese non contate")
    # meteo ogni ~40 km
    campioni = [0]
    for k in range(1, len(punti)):
        if cum[k] - cum[campioni[-1]] >= 40:
            campioni.append(k)
    if campioni[-1] != len(punti) - 1:
        campioni.append(len(punti) - 1)
    campioni = campioni[:30]
    meteo = None
    fine_t = partenza + tempo[-1] * 1.5 + 3600     # con le soste si arriva più tardi della sola guida
    if fine_t - time.time() > 15 * 86400:
        avvisi.append("Oltre 15 giorni non ci sono previsioni del tempo: uso %d °C" % round(temp_riserva))
    else:
        try:
            meteo = srv.meteo([punti[k] for k in campioni],
                              datetime.datetime.fromtimestamp(partenza, datetime.timezone.utc).strftime("%Y-%m-%d"),
                              datetime.datetime.fromtimestamp(fine_t, datetime.timezone.utc).strftime("%Y-%m-%d"))
        except Exception:  # noqa: BLE001
            avvisi.append("Previsioni del tempo non disponibili: uso %d °C, senza vento" % round(temp_riserva))
    # energia di ogni pezzo
    k_g, k_c = cal["k_guida"], cal["k_clima"]
    musica_kw = auto["musica_kw"] if vo["musica"] else 0.0
    e_cum, temps = [0.0], []
    parti = {"guida": 0.0, "recupero": 0.0, "clima": 0.0, "elettronica": 0.0, "musica": 0.0, "preriscaldo": 0.0}
    ci = 0
    for idx, p in enumerate(pezzi):
        while ci + 1 < len(campioni) and campioni[ci + 1] <= idx:
            ci += 1
        vicino = ci if ci + 1 >= len(campioni) or idx - campioni[ci] <= campioni[ci + 1] - idx else ci + 1
        quando = partenza + tempo[idx]
        t_c, vento, pioggia = temp_riserva, 0.0, False
        if meteo:
            s = meteo[vicino]
            t_c = valore_ora(s, "temp", quando)
            t_c = temp_riserva if t_c is None else t_c
            w, wd = valore_ora(s, "vento", quando), valore_ora(s, "dir", quando)
            if w is not None and wd is not None:
                dirz = rotta(punti[idx][0], punti[idx][1], punti[idx + 1][0], punti[idx + 1][1])
                vento = 0.6 * w * math.cos(math.radians(wd - dirz))   # contrario positivo; a 2 m il vento è minore
            pioggia = (valore_ora(s, "pioggia", quando) or 0.0) > 0.3
        temps.append(t_c)
        ore = p[2] / (vel[idx] / 3.6) / 3600.0
        tr = energia_trazione(p[2], vel[idx], quota[idx + 1] - quota[idx], t_c, auto, vento, pioggia, k_g)
        cl = potenza_clima_kw(t_c, auto, abit, k_c) * ore
        el = auto["elettronica_kw"] * ore
        mu = musica_kw * ore
        parti["guida" if tr >= 0 else "recupero"] += tr
        parti["clima"] += cl
        parti["elettronica"] += el
        parti["musica"] += mu
        e_cum.append(e_cum[-1] + tr + cl + el + mu)
    if not clima_acceso:
        pr = preriscaldo_kwh(temps[0], auto, abit, k_c)
        parti["preriscaldo"] = pr
        e_cum = [e_cum[0]] + [x + pr for x in e_cum[1:]]
    e_tot = e_cum[-1]
    # colonnine lungo la strada
    colonnine, viste = [], 0
    linea = [punti[0]]
    for k in range(1, len(punti)):
        if cum[k] - cum[k - 1] > 0 and (k % 3 == 0 or k == len(punti) - 1):
            linea.append(punti[k])
    try:
        trovate = srv.colonnine(linea)
    except ErroreAuto as e:
        trovate = []
        avvisi.append(e.testo)
    medio = e_tot / max(cum[-1], 0.1)    # kWh/km del viaggio, per le deviazioni
    for c in trovate:
        pr = _proietta(c["lat"], c["lon"], punti, cum)
        if pr is None:
            continue
        i, u, d_off, lato = pr
        if d_off > 3000:
            continue
        autostrada = vel[i] >= 90
        if autostrada and lato > 0 and 15 < d_off < 400:
            continue      # area di servizio della carreggiata opposta: non si raggiunge senza uscire
        viste += 1
        dev_km = 2 * d_off / 1000.0 * 1.4 if d_off > 50 else 0.0
        dev_min = dev_km / 40.0 * 60.0 + (6.0 if autostrada and d_off >= 400 else 0.0)
        cc = dict(c)
        cc.update({"s": cum[i] + u * (cum[i + 1] - cum[i]), "e": e_cum[i] + u * (e_cum[i + 1] - e_cum[i]),
                   "dev_km": round(dev_km, 1), "dev_kwh": dev_km / 2 * medio, "dev_min": dev_min,
                   "t": temps[i], "tempo": tempo[i] + u * (tempo[i + 1] - tempo[i]), "autostrada": autostrada})
        colonnine.append(cc)
    # al più 3 colonnine ogni 10 km (le più rapide, Ionity prima): bastano e il calcolo resta veloce
    gruppi = {}
    for c in colonnine:
        gruppi.setdefault(int(c["s"] // 10), []).append(c)
    scelte = []
    for g in gruppi.values():
        g.sort(key=lambda c: (-(1 if vo["ionity_prima"] and c["ionity"] else 0), -c["kw"], c["dev_min"]))
        scelte += g[:3]
    precond = 1.0 if vo["precondiziona"] and min(temps) < 10 else 0.0
    sol = scegli_soste(e_tot, soc0, scelte, auto, r, {"precond_kwh": precond, "precondiziona": vo["precondiziona"],
                                                       "ionity_prima": vo["ionity_prima"]})
    if not sol["riuscito"]:
        avvisi.insert(0, sol["motivo"] + (": parti più carico, scegli un'altra strada o abbassa la velocità"
                                          if trovate else ""))
    # orari, costi e profilo della batteria
    cap = auto["capacita_kwh"]
    prezzi = imp["prezzi"]
    soste_out, attesa, soc_prof = [], 0.0, []
    for c in sol["soste"]:
        ora_arrivo = partenza + c["tempo"] + attesa + c["dev_min"] * 30.0
        paese = ""
        try:
            paese = srv.paese(c["lat"], c["lon"])
        except Exception:  # noqa: BLE001
            pass
        if c["ionity"]:
            prezzo = prezzi.get("ionity_" + paese, prezzi["ionity_altri"])
        else:
            prezzo = prezzi["altri"]
        kwh_rete = (c["partenza_soc"] - c["arrivo_soc"]) / 100.0 * cap / 0.92
        soste_out.append({
            "nome": c["nome"], "operatore": c["operatore"], "ionity": c["ionity"], "lat": c["lat"], "lon": c["lon"],
            "km": round(c["s"], 1), "kw": round(c["kw"]), "kw_noto": c["kw_noto"], "posti": c.get("posti"),
            "arrivo_soc": round(c["arrivo_soc"], 1), "partenza_soc": round(c["partenza_soc"], 1),
            "fascia": fascia(c["arrivo_soc"], r), "minuti": int(round(c["minuti"])), "deviazione_km": c["dev_km"],
            "ora_arrivo": ora_arrivo, "ora_partenza": ora_arrivo + c["minuti"] * 60.0, "paese": paese,
            "prezzo_kwh": prezzo, "costo": round(kwh_rete * prezzo, 2), "temp": _tondo(c["t"], 0)})
        attesa += c["minuti"] * 60.0 + c["dev_min"] * 60.0
        soc_prof.append((c["s"], c["e"], c["arrivo_soc"], c["partenza_soc"], c["dev_kwh"]))
    profilo, sp = [], 0
    soc_corr, e_rif = soc0, 0.0
    passo_km = max(2.0, cum[-1] / 150.0)
    ultimo_km = -1e9
    for k in range(len(punti)):
        while sp < len(soc_prof) and soc_prof[sp][0] <= cum[k]:
            s_c, e_c, a_c, p_c, d_c = soc_prof[sp]
            profilo.append({"km": round(s_c, 1), "soc": round(a_c, 1), "quota": round(quota[k]), "sosta": True})
            profilo.append({"km": round(s_c, 1), "soc": round(p_c, 1), "quota": round(quota[k]), "sosta": True})
            soc_corr, e_rif = p_c, e_c - d_c        # al rientro dalla deviazione
            sp += 1
            ultimo_km = s_c
        if cum[k] - ultimo_km >= passo_km or k == len(punti) - 1:
            profilo.append({"km": round(cum[k], 1), "soc": round(soc_corr - (e_cum[k] - e_rif) / cap * 100.0, 1),
                            "quota": round(quota[k])})
            ultimo_km = cum[k]
    arrivo_soc = sol["arrivo_soc"]
    if sol["riuscito"] and arrivo_soc < r["destinazione_min"] - 0.5:
        avvisi.append("Arrivi al %d%%: sotto il tuo %d%%, ma meglio di una sosta in più" % (round(arrivo_soc),
                                                                                         r["destinazione_min"]))
    fuori = [s for s in soste_out if s["fascia"] != "ideale"]
    if fuori:
        avvisi.append("Colonnina nella fascia di tolleranza (%s): nella fascia %d–%d%% non ce n'erano"
                      % (", ".join("%s al %d%%" % (s["nome"], round(s["arrivo_soc"])) for s in fuori),
                         r["arrivo_min"], r["arrivo_ideale"]))
    if not trovate and e_tot / cap * 100 > soc0 - r["destinazione_min"]:
        avvisi.append("Nessuna colonnina rapida CCS trovata vicino al percorso")
    parti["precondizionamento"] = precond * len(soste_out)
    return {
        "da": {"lat": da[0], "lon": da[1]}, "a": {"lat": a[0], "lon": a[1]},
        "km": round(cum[-1], 1), "ore_guida": round(tempo[-1] / 3600.0, 2), "partenza": partenza,
        "arrivo": partenza + tempo[-1] + attesa, "soc_partenza": round(soc0, 1), "soc_arrivo": round(arrivo_soc, 1),
        "riuscito": sol["riuscito"], "soste": soste_out,
        "minuti_ricarica": int(round(sum(s["minuti"] for s in soste_out))),
        "costo": round(sum(s["costo"] for s in soste_out), 2),
        "energia": {k: round(v, 2) for k, v in dict(parti, totale=e_tot + parti["precondizionamento"]).items()},
        "kwh_100": round(e_tot / max(cum[-1], 0.1) * 100.0, 1),
        "temperature": {"min": _tondo(min(temps), 0), "max": _tondo(max(temps), 0),
                        "media": _tondo(sum(temps) / len(temps), 0)},
        "profilo": profilo, "geo": _semplifica(coord), "avvisi": avvisi, "colonnine_viste": viste,
        "regole": r, "calibrazione": cal, "velocita_max": vmax, "musica": bool(vo["musica"]),
    }


# ---------------------------------------------------------------------------------------------------------------
# il modulo: stato dell'auto, comandi, diario, rendimento, viaggi; le API (chiamate da musica_libreria.py)
# ---------------------------------------------------------------------------------------------------------------
class Auto:
    Errore = ErroreAuto

    def __init__(self, cartella, servizi=None):
        self.cartella = os.path.abspath(os.path.expanduser(cartella))
        os.makedirs(self.cartella, mode=0o700, exist_ok=True)
        self.cred = Credenziali(self.cartella)
        self.imp = Impostazioni(os.path.join(self.cartella, "impostazioni.json"))
        self.arch = Archivio(os.path.join(self.cartella, "diario.sqlite"))
        self.diario = Diario(self.arch)
        self.srv = servizi or Servizi()
        self.ponte = Ponte(self.cartella, self._alla_lettura)
        self.lock = threading.RLock()
        self._file_stato = os.path.join(self.cartella, "stato.json")
        s = _leggi_json(self._file_stato, {})
        s = s if isinstance(s, dict) else {}
        self.veicoli = s.get("veicoli") if isinstance(s.get("veicoli"), dict) else {}
        self.lettura = s.get("lettura")
        self._sveglia = threading.Event()
        self._arricchitore = None

    # --- vita del modulo
    def avvia(self):
        if self.cred.utente():
            self.ponte.avvia(self.cred.leggi)
        if self._arricchitore is None:
            self._arricchitore = threading.Thread(target=self._arricchisci_sempre, name="auto-diario", daemon=True)
            self._arricchitore.start()

    def chiudi(self):
        self.ponte.ferma(5)
        self.arch.chiudi()

    def _alla_lettura(self, m):
        t = _num(m.get("t"), time.time())
        nuovi, eventi = {}, []
        for v in m.get("veicoli") or []:
            vin = str(v.get("vin") or "")
            if not vin:
                continue
            n = normalizza(vin, v.get("attr") or {}, v.get("comandi") or {}, t)
            nuovi[vin] = n
            try:
                self.arch.aggiungi_istantanea(n)
                eventi += self.diario.passo(n)
            except Exception as e:  # noqa: BLE001  il diario non deve fermare le letture
                print("Atlante auto: diario non aggiornato: %s" % _breve(e), flush=True)
        with self.lock:
            self.veicoli.update(nuovi)
            self.lettura = t
            try:
                _scrivi_json_privato(self._file_stato, {"veicoli": self.veicoli, "lettura": self.lettura})
            except OSError:
                pass
        if eventi:
            self._sveglia.set()

    def _arricchisci_sempre(self):
        while True:
            self._sveglia.wait(3600)
            self._sveglia.clear()
            try:
                self._arricchisci()
            except Exception as e:  # noqa: BLE001
                print("Atlante auto: arricchimento del diario: %s" % _breve(e), flush=True)

    def _arricchisci(self):
        """Quote e temperature dei viaggi (quando l'auto non l'ha data) e indirizzi delle soste, dai servizi pubblici."""
        mancano = self.arch.viaggi_senza_quota(50)
        if mancano:
            punti = []
            for v in mancano:
                punti += [(v["da_lat"], v["da_lon"]), (v["a_lat"], v["a_lon"])]
            try:
                q = self.srv.quote(punti)
                for i, v in enumerate(mancano):
                    self.arch.imposta_quote(v["id"], q[2 * i], q[2 * i + 1])
            except Exception:  # noqa: BLE001
                pass
        for v in self.arch.viaggi_senza_temp(10):
            try:
                t = self.srv.temperatura_passata(v["da_lat"], v["da_lon"], (v["partenza"] + v["arrivo"]) / 2.0)
            except Exception:  # noqa: BLE001
                break
            if t is None and time.time() - v["arrivo"] > 7 * 86400:
                self.arch.imposta_temp(v["id"], None, "no")
            elif t is not None:
                self.arch.imposta_temp(v["id"], round(t, 1), "meteo")
        for lat, lon in self.arch.luoghi_senza_nome(15):
            try:
                ind = self.srv.indirizzo(lat, lon)
            except Exception:  # noqa: BLE001
                break
            if ind:
                self.arch.salva_indirizzo(lat, lon, ind)

    # --- le risposte
    def _vista(self, n, miei):
        nome, indirizzo = self.arch.nome_luogo(n.get("lat"), n.get("lon"), miei)
        pos = None
        if n.get("lat") is not None:
            pos = {"lat": n["lat"], "lon": n["lon"], "dal": n.get("pos_t"), "nome": nome, "indirizzo": indirizzo}
        return {
            "vin": n["vin"], "nome": n.get("nome") or n.get("modello") or "La tua Volkswagen",
            "modello": n.get("modello"), "anno": n.get("anno"), "targa": n.get("targa"),
            "soc": n.get("soc"), "autonomia": n.get("autonomia"), "km": n.get("km"), "est": n.get("est"),
            "batteria": {"min": n.get("batt_min"), "max": n.get("batt_max")},
            "clima": {"stato": n.get("clima"), "target": n.get("clima_target"), "fine": n.get("clima_fine"),
                      "senza_cavo": n.get("clima_senza_cavo")},
            "sbrina": n.get("sbrina"), "porte": n.get("porte"), "porte_aperte": n.get("porte_aperte"),
            "finestrini": n.get("finestrini"),
            "ricarica": {"stato": n.get("ricarica"), "tipo": n.get("tipo_ricarica"), "potenza": n.get("potenza"),
                         "velocita": n.get("velocita_ricarica"), "fine": n.get("fine_ricarica"),
                         "obiettivo": n.get("obiettivo"), "spina": n.get("spina")},
            "posizione": pos, "stato": n.get("stato"), "online": n.get("online"), "aggiornato": n.get("aggiornato"),
            "letto": n.get("t"), "comandi": n.get("comandi") or {}}

    def stato(self):
        d = self.cred.dati()
        miei = self.arch._miei()
        with self.lock:
            veicoli = [self._vista(n, miei) for n in self.veicoli.values()]
            lettura = self.lettura
        return {"account": {"collegato": bool(d.get("utente")), "utente": maschera(d.get("utente")),
                            "dove": d.get("dove") or "", "forza_accesso": bool(d.get("forza_accesso")),
                            "ponte": dict(self.ponte.stato), "bloccato": self.ponte.bloccato},
                "veicoli": veicoli, "lettura": lettura, "intervallo": INTERVALLO, "ora": time.time()}

    def collega(self, corpo):
        """{utente, password, forza_accesso}: collega l'account (o un altro). Senza utente e password: lo stesso
        account riparte, e con forza_accesso cambia solo quell'opzione (la password resta dov'è)."""
        utente = str(corpo.get("utente") or "").strip()
        password = str(corpo.get("password") or "")
        vecchio = self.cred.dati()
        if not utente and not password:
            if not vecchio.get("utente"):
                raise ErroreAuto(400, "Scrivi email e password dell'account Volkswagen")
            self.ponte.ferma()
            if "forza_accesso" in corpo:
                self.cred.imposta(forza_accesso=bool(corpo.get("forza_accesso")))
        else:
            if not re.fullmatch(r"[^@\s]{1,100}@[^@\s]{1,100}\.[^@\s]{1,30}", utente):
                raise ErroreAuto(400, "Scrivi l'email dell'account Volkswagen")
            if not 1 <= len(password) <= 200:
                raise ErroreAuto(400, "Scrivi la password dell'account Volkswagen")
            self.ponte.ferma()
            if vecchio.get("utente") and vecchio.get("utente") != utente:
                self.cred.cancella()
                self._cancella_sessione()
            self.cred.salva(utente, password, bool(corpo.get("forza_accesso")))
            password = None
        self.ponte.avvia(self.cred.leggi)
        return self.stato()

    def _cancella_sessione(self):
        for f in ("token.json", "cache.json", "stato.json"):
            try:
                os.unlink(os.path.join(self.cartella, f))
            except OSError:
                pass
        with self.lock:
            self.veicoli, self.lettura = {}, None

    def scollega(self):
        self.ponte.ferma()
        self.cred.cancella()
        self._cancella_sessione()
        self.ponte.bloccato = ""
        self.ponte._fase("spento", "")
        return self.stato()

    def comando(self, corpo):
        azione = str(corpo.get("azione") or "")
        if azione not in AZIONI:
            raise ErroreAuto(400, "Comando sconosciuto")
        richiesta = {"azione": azione, "vin": str(corpo.get("vin") or "")}
        if azione in ("blocca", "sblocca"):
            spin = str(corpo.get("spin") or "")
            if not re.fullmatch(r"\d{4}", spin):
                raise ErroreAuto(400, "Scrivi l'S-PIN di 4 cifre (lo stesso dell'app Volkswagen)")
            richiesta["spin"] = spin
        if azione == "clima_on" and corpo.get("temperatura") is not None:
            t = _num(corpo.get("temperatura"))
            if t is None or not 15.5 <= t <= 30:
                raise ErroreAuto(400, "Temperatura tra 16 e 29,5 °C")
            richiesta["temperatura"] = round(t * 2) / 2
        self.ponte.comando(richiesta)
        richiesta.pop("spin", None)
        testo = {"blocca": "Chiusura inviata", "sblocca": "Apertura inviata", "clima_on": "Clima acceso",
                 "clima_off": "Clima spento", "sbrina_on": "Sbrinamento acceso", "sbrina_off": "Sbrinamento spento",
                 "aggiorna": "Lettura chiesta"}[azione]
        return {"ok": True, "messaggio": testo + ": l'auto conferma entro un minuto circa"}

    def diario_api(self, giorni, vin):
        giorni = max(1, min(3650, int(_num(giorni, 30))))
        da = time.time() - giorni * 86400
        cap = self.imp.tutte()["auto"]["capacita_kwh"]
        miei = self.arch._miei()
        viaggi, senza = [], False
        for v in self.arch.viaggi(vin or None, da):
            dn, _ = self.arch.nome_luogo(v["da_lat"], v["da_lon"], miei)
            an, _ = self.arch.nome_luogo(v["a_lat"], v["a_lon"], miei)
            senza = senza or dn is None or an is None
            buono = viaggio_valido(v)
            kwh = (v["soc_da"] - v["soc_a"]) / 100.0 * cap if buono else None
            viaggi.append(dict(v, da_nome=dn, a_nome=an, minuti=int(round((v["arrivo"] - v["partenza"]) / 60.0)),
                               kwh=_tondo(kwh), kwh_100=_tondo(kwh / v["km"] * 100.0) if kwh else None,
                               valido=buono))
        soste = []
        for s in self.arch.soste(vin or None, da):
            nome, indirizzo = self.arch.nome_luogo(s["lat"], s["lon"], miei)
            senza = senza or nome is None
            soste.append(dict(s, nome=nome, indirizzo=indirizzo))
        ricariche = []
        for c in self.arch.ricariche(vin or None, da):
            nome, _ = self.arch.nome_luogo(c["lat"], c["lon"], miei)
            ricariche.append(dict(c, nome=nome))
        if senza:
            self._sveglia.set()
        km = sum(v["km"] or 0 for v in viaggi)
        buoni = [v for v in viaggi if v["valido"]]
        km_b, kwh_b = sum(v["km"] for v in buoni), sum(v["kwh"] for v in buoni)
        return {"giorni": giorni, "viaggi": viaggi, "soste": soste, "ricariche": ricariche,
                "totali": {"viaggi": len(viaggi), "km": round(km, 1),
                           "ore": round(sum(v["minuti"] for v in viaggi) / 60.0, 1),
                           "kwh_100": round(kwh_b / km_b * 100.0, 1) if km_b > 0 else None}}

    def percorso_viaggio(self, vid):
        v = self.arch.viaggio(vid)
        if v is None:
            raise ErroreAuto(404, "Viaggio non trovato")
        geo = self.arch.percorso(vid)
        if geo is None:
            try:
                rp = self.srv.percorso((v["da_lat"], v["da_lon"]), (v["a_lat"], v["a_lon"]))
            except ErroreAuto:
                raise
            except Exception as e:  # noqa: BLE001
                raise ErroreAuto(502, "Percorso non disponibile adesso: %s" % _breve(e))
            geo = {"punti": _semplifica(rp["coord"], 800), "km": round(rp["m"] / 1000.0, 1)}
            self.arch.salva_percorso(vid, geo)
        return dict(geo, ricostruito=True, km_registrati=v["km"])

    def rendimento_api(self, vin):
        imp = self.imp.tutte()
        out = rendimento(self.arch.viaggi(vin or None, 0.0), imp["auto"], imp["viaggio"]["abitacolo_c"])
        out["auto"] = imp["auto"]["nome"]
        return out

    def viaggio_api(self, corpo):
        imp = self.imp.tutte()
        with self.lock:
            veicoli = list(self.veicoli.values())
        n = next((v for v in veicoli if v["vin"] == corpo.get("vin")), veicoli[0] if veicoli else None)

        def punto(x, nome):
            if not isinstance(x, dict):
                return None
            lat, lon = _num(x.get("lat")), _num(x.get("lon"))
            if lat is None or lon is None or not -90 <= lat <= 90 or not -180 <= lon <= 180:
                raise ErroreAuto(400, "Punto di %s non valido" % nome)
            return lat, lon
        a = punto(corpo.get("a"), "arrivo")
        if a is None:
            raise ErroreAuto(400, "Scegli dove vuoi andare")
        da = punto(corpo.get("da"), "partenza")
        if da is None:
            if not n or n.get("lat") is None:
                raise ErroreAuto(400, "Non so dov'è l'auto: scegli il punto di partenza")
            da = (n["lat"], n["lon"])
        soc0 = _num(corpo.get("soc"))
        if soc0 is None:
            soc0 = n.get("soc") if n else None
        if soc0 is None or not 1 <= soc0 <= 100:
            raise ErroreAuto(400, "Scrivi con quanta batteria parti")
        partenza = _num(corpo.get("partenza")) or time.time()
        opz = corpo.get("opzioni") if isinstance(corpo.get("opzioni"), dict) else {}
        pulite = {}
        for k, (lo, hi) in (("velocita_max", (50, 150)), ("abitacolo_c", (15, 27)), ("carico_kg", (0, 1500))):
            if opz.get(k) is not None:
                x = _num(opz.get(k))
                if x is None or not lo <= x <= hi:
                    raise ErroreAuto(400, "Valore non valido: %s" % k)
                pulite[k] = x
        for k in ("musica", "ionity_prima", "precondiziona"):
            if opz.get(k) is not None:
                pulite[k] = bool(opz[k])
        cal = calibra(self.arch.viaggi(None, 0.0), imp["auto"], imp["viaggio"]["abitacolo_c"])
        clima_acceso = bool(n and n.get("clima") in ("heating", "cooling", "ventilation"))
        riserva = n.get("est") if n and n.get("est") is not None else 15.0
        try:
            return pianifica(self.srv, imp, cal, da, a, soc0, partenza, pulite, clima_acceso, riserva)
        except ErroreAuto:
            raise
        except Exception as e:  # noqa: BLE001
            raise ErroreAuto(502, "Pianificazione non riuscita: %s" % _breve(e))

    def api(self, metodo, parti, query, corpo):
        """parti: i pezzi dell'indirizzo dopo «auto/»; query: dict di liste (parse_qs); → (codice, dati)."""
        cosa, resto = (parti[0] if parti else ""), list(parti[1:])
        corpo = corpo if isinstance(corpo, dict) else {}

        def q(nome, predefinito=""):
            return (query.get(nome) or [predefinito])[0]

        def solo(*metodi):
            if metodo not in metodi:
                raise ErroreAuto(405, "Metodo %s non permesso qui" % metodo)

        if cosa in ("", "stato") and not resto:
            solo("GET")
            return 200, self.stato()
        if cosa == "account" and not resto:
            solo("POST", "DELETE")
            return 200, (self.collega(corpo) if metodo == "POST" else self.scollega())
        if cosa == "comando" and not resto:
            solo("POST")
            return 200, self.comando(corpo)
        if cosa == "libreria" and not resto:      # aggiorna CarConnectivity (se il cloud Volkswagen è cambiato)
            solo("POST")
            if not self.cred.utente():
                raise ErroreAuto(400, "Prima collega l'account Volkswagen")
            self.ponte.ferma()
            self.ponte.avvia(self.cred.leggi, forza_installazione=True)
            return 200, self.stato()
        if cosa == "diario" and not resto:
            solo("GET")
            return 200, self.diario_api(q("giorni", "30"), q("vin"))
        if cosa == "diario" and resto == ["cancella"]:
            solo("POST")
            if corpo.get("conferma") is not True:
                raise ErroreAuto(400, "Conferma la cancellazione del diario")
            self.arch.cancella_diario()
            return 200, {"ok": True}
        if cosa == "viaggi" and len(resto) == 2 and resto[1] == "percorso" and resto[0].isdigit():
            solo("GET")
            return 200, self.percorso_viaggio(int(resto[0]))
        if cosa == "luoghi" and not resto:
            solo("POST")
            lat, lon = _num(corpo.get("lat")), _num(corpo.get("lon"))
            if lat is None or lon is None:
                raise ErroreAuto(400, "Luogo non valido")
            self.arch.rinomina(lat, lon, corpo.get("nome"))
            return 200, {"ok": True, "nome": self.arch.nome_luogo(lat, lon)[0]}
        if cosa == "rendimento" and not resto:
            solo("GET")
            return 200, self.rendimento_api(q("vin"))
        if cosa == "impostazioni" and not resto:
            solo("GET", "POST")
            return 200, (self.imp.tutte() if metodo == "GET" else self.imp.aggiorna(corpo))
        if cosa == "cerca" and not resto:
            solo("GET")
            testo = q("q").strip()
            if len(testo) < 3:
                return 200, {"risultati": []}
            try:
                return 200, {"risultati": self.srv.cerca(testo[:200])}
            except Exception as e:  # noqa: BLE001
                raise ErroreAuto(502, "Ricerca non disponibile adesso: %s" % _breve(e))
        if cosa == "viaggio" and not resto:
            solo("POST")
            return 200, self.viaggio_api(corpo)
        raise ErroreAuto(404, "Richiesta sconosciuta: api/extra/auto/%s" % "/".join(parti))
