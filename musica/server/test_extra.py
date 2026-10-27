#!/usr/bin/env python3
"""Prove delle API dei moduli extra (api/extra/) di musica_libreria.py. Solo libreria standard:

    python3 -I test_extra.py

Avvia il server su una porta libera con una cartella di musica vuota, cartelle temporanee e programmi finti
(yt-dlp, say) in testa al PATH; l'IA è cercata su una porta dove non risponde nessuno (quindi «Ollama assente»
anche su un Mac dove Ollama c'è). Il proxy verso un sito pubblico si prova solo se la rete c'è.
"""
import collections
import http.client
import http.server
import importlib.util
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
from urllib.parse import quote as _quote, urlsplit

QUI =os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(QUI, "musica_libreria.py")

FINTO_YTDLP = r'''
import json, os, re, sys, time
a = sys.argv[1:]
if "--version" in a:
    print("2099.01.01")
    sys.exit(0)
url = a[-1]
nome = url.rstrip("/").rsplit("/", 1)[-1]
with open(os.path.join(os.environ["FINTO_DIR"], "ytdlp-%s.json" % nome), "w") as f:
    json.dump({"args": a, "pid": os.getpid()}, f)
with open(os.path.join(os.environ["FINTO_DIR"], "chiamate.log"), "a") as f:
    f.write(nome + "\n")
home = [v[5:] for i, v in enumerate(a) if i and a[i - 1] == "-P" and v.startswith("home:")][0]
stampe = [v for i, v in enumerate(a) if i and a[i - 1] in ("--print", "-O")]

def stampa(quando, **campi):
    for s in stampe:
        w, _, t = s.partition(":")
        if w == quando:
            # «%(a,b)s»: il primo dei campi che c'è (quelli che non ci sono restano scritti com'erano, come «NA»)
            t = re.sub(r"%\(([\w,]+)\)s", lambda m: next((campi[x] for x in m.group(1).split(",") if x in campi),
                                                       m.group(0)), t)
            print(t, flush=True)

print("[youtube] %s: Downloading webpage" % nome, flush=True)
if "errore" in nome:
    print("ERROR: [youtube] errore: Video non disponibile", flush=True)
    sys.exit(1)
stampa("before_dl", title="Video di prova")
print("[download]   0.0% of   10.00MiB at    1.00MiB/s ETA 00:10", flush=True)
print("[download]  42.0% of   10.00MiB at    1.00MiB/s ETA 00:05", flush=True)
time.sleep(60 if "lento" in nome else 1.5)
p = os.path.join(home, "Video di prova [%s].%s" % (nome, "m4a" if "-x" in a else "mp4"))
with open(p, "wb") as f:
    f.write(bytes(range(256)) * 4)
print("[download] 100% of   10.00MiB in 00:00:01 at 10.00MiB/s", flush=True)
stampa("after_move", filepath=p)
'''

# ffmpeg finto: scrive un MP3 piccolissimo con i tag ID3v2.3 ricevuti (-metadata), così la libreria li legge davvero
FINTO_FFMPEG = r'''
import json, os, struct, sys
a = sys.argv[1:]
ingressi = [a[i + 1] for i, v in enumerate(a) if v == "-i"]
if a and a[-1] == "-":
    sys.stderr.write("audio grezzo non disponibile\n")
    sys.exit(1)
if not ingressi or not all(os.path.isfile(p) for p in ingressi):
    sys.stderr.write("ingresso mancante\n")
    sys.exit(1)
tag = {}
for i, v in enumerate(a):
    if v == "-metadata" and i + 1 < len(a):
        k, _, x = a[i + 1].partition("=")
        tag[k] = x
with open(os.path.join(os.environ["FINTO_DIR"], "ffmpeg.log"), "a", encoding="utf-8") as f:
    f.write(json.dumps({"args": a, "tag": tag}) + "\n")

def frame(fid, testo):
    d = b"\x01" + testo.encode("utf-16")
    return fid.encode() + struct.pack(">I", len(d)) + b"\x00\x00" + d

corpo = b"".join(frame(f, tag[k]) for f, k in (("TIT2", "title"), ("TPE1", "artist"), ("TALB", "album")) if tag.get(k))
n = len(corpo)
with open(a[-1], "wb") as f:
    f.write(b"ID3\x03\x00\x00" + bytes([(n >> 21) & 127, (n >> 14) & 127, (n >> 7) & 127, n & 127]) + corpo)
    f.write((b"\xff\xfb\x90\x00" + bytes(413)) * 10)   # dieci fotogrammi MPEG da 128 kbit/s
'''

FINTO_SAY = r'''
import json, os, struct, sys
a = sys.argv[1:]
if a[:2] == ["-v", "?"]:
    print("Alex                en_US    # Most people recognize me by my voice.")
    print("Alice               it_IT    # Ciao! Mi chiamo Alice.")
    print("Eddy (Italian (Italy)) it_IT    # Ciao! Mi chiamo Eddy.")
    print("Fiona               en-scotland # Hello, my name is Fiona.")
    sys.exit(0)
if "--file-format=m4af" not in a or "--data-format=aac" not in a or "-o" not in a or "-f" not in a:
    sys.stderr.write("argomenti sbagliati: %r\n" % a)
    sys.exit(2)
if "-v" in a and a[a.index("-v") + 1] not in ("Alex", "Alice", "Eddy (Italian (Italy))", "Fiona"):
    sys.stderr.write("Voice not found\n")
    sys.exit(1)
out = a[a.index("-o") + 1]
with open(a[a.index("-f") + 1], encoding="utf-8") as f:
    testo = f.read()
with open(os.path.join(os.environ["FINTO_DIR"], "say.log"), "a", encoding="utf-8") as f:
    f.write(json.dumps({"args": a, "testo": testo}) + "\n")

def scatola(t, d):
    return struct.pack(">I", 8 + len(d)) + t + d

# m4a minimo: ftyp + moov/mvhd con la durata (1 s ogni 10 caratteri), quanto basta per leggerne la durata
mvhd = bytes(12) + struct.pack(">II", 1000, len(testo) * 100) + bytes(80)
with open(out, "wb") as f:
    f.write(scatola(b"ftyp", b"M4A \0\0\0\0M4A mp42isom") + scatola(b"moov", scatola(b"mvhd", mvhd)))
'''

# ----- tailscale finto (Gestione → Rete) -----
# Le prove girano anche sul Mac mini prima di ogni aggiornamento: lì il tailscale vero non deve mai partire. Questo
# finto sta in testa al PATH, risponde solo ai comandi che il server deve usare (con gli argomenti esatti) e tiene lo
# stato in FINTO_DIR/tailscale-stato.json: {spento, funnel_vietato, attesa, uscita, approvata, serve}.
FINTO_TAILSCALE = r'''
import json, os, re, sys, time
a = sys.argv[1:]
cart = os.environ["FINTO_DIR"]
fs = os.path.join(cart, "tailscale-stato.json")
try:
    with open(fs) as f:
        st = json.load(f)
except (OSError, ValueError):
    st = {}
with open(os.path.join(cart, "tailscale.log"), "a") as f:
    f.write(json.dumps(a) + "\n")

def salva():
    with open(fs, "w") as f:
        json.dump(st, f)

DNS = "mac-mini.tail1234.ts.net"
if st.get("spento"):
    sys.stderr.write("failed to connect to local Tailscale daemon (tailscaled not running?)\n")
    sys.exit(1)
serve = st.setdefault("serve", {})
if a == ["status", "--json"]:
    print(json.dumps({"Version": "1.90.0-finto", "BackendState": "Running", "TailscaleIPs": ["100.100.1.1", "fd7a:115c:a1e0::1"],
        "Self": {"HostName": "Mac-mini", "DNSName": DNS + ".", "OS": "macOS", "Online": True,
                 "TailscaleIPs": ["100.100.1.1", "fd7a:115c:a1e0::1"], "ExitNodeOption": bool(st.get("uscita") and st.get("approvata"))},
        "MagicDNSSuffix": "tail1234.ts.net", "CurrentTailnet": {"Name": "samuele@example.com", "MagicDNSSuffix": "tail1234.ts.net"},
        "Peer": {"nodekey:2": {"HostName": "MacBook-Pro", "DNSName": "macbook.tail1234.ts.net.", "OS": "macOS", "Online": False,
                               "TailscaleIPs": ["100.100.1.3"], "LastSeen": "2026-10-26T18:30:00.123456789Z"},
                 "nodekey:1": {"HostName": "localhost", "DNSName": "iphone-di-samuele.tail1234.ts.net.", "OS": "iOS", "Online": True,
                               "TailscaleIPs": ["100.100.1.2"], "LastSeen": "0001-01-01T00:00:00Z"}}}))
elif a in (["funnel", "status", "--json"], ["serve", "status", "--json"]):
    print(json.dumps(serve, indent=2))
elif a == ["debug", "prefs"]:
    print(json.dumps({"ControlURL": "https://controlplane.tailscale.com", "AdvertiseRoutes": ["0.0.0.0/0", "::/0"] if st.get("uscita") else None}))
elif len(a) == 4 and a[:2] == ["funnel", "--bg"] and re.match(r"^--https=\d+$", a[2]) and re.match(r"^http://127\.0\.0\.1:\d+$", a[3]):
    if st.get("funnel_vietato"):
        print("Funnel is not enabled on your tailnet.\nTo enable, visit:\n\n         https://login.tailscale.com/f/funnel?node=nFINTO123\n", flush=True)
        if st.get("attesa"):
            time.sleep(60)
        sys.exit(1)
    porta = a[2].split("=")[1]
    serve.setdefault("TCP", {})[porta] = {"HTTPS": True}
    serve.setdefault("Web", {})["%s:%s" % (DNS, porta)] = {"Handlers": {"/": {"Proxy": a[3]}}}
    serve.setdefault("AllowFunnel", {})["%s:%s" % (DNS, porta)] = True
    salva()
    print("Available on the internet:\n\nhttps://%s:%s/\n|-- proxy %s\n\nFunnel started and running in the background." % (DNS, porta, a[3]))
elif len(a) == 3 and a[0] == "funnel" and re.match(r"^--https=\d+$", a[1]) and a[2] == "off":
    porta = a[1].split("=")[1]
    hp = "%s:%s" % (DNS, porta)
    if hp not in serve.get("Web", {}):
        sys.stderr.write("error: failed to remove web serve: handler does not exist\n")
        sys.exit(1)
    for k, sotto in (("TCP", porta), ("Web", hp), ("AllowFunnel", hp)):
        serve.get(k, {}).pop(sotto, None)
    salva()
elif len(a) == 2 and a[0] == "set" and a[1] in ("--advertise-exit-node=true", "--advertise-exit-node=false"):
    st["uscita"] = a[1].endswith("true")
    salva()
else:
    sys.stderr.write("comando sconosciuto al finto: %r\n" % a)
    sys.exit(2)
'''


# ffprobe finto: guarda i primi byte del file, come il vero guarda il contenuto e non il nome
FINTO_FFPROBE = r'''
import json, os, sys
p = sys.argv[-1]
with open(os.path.join(os.environ["FINTO_DIR"], "ffprobe.log"), "a", encoding="utf-8") as f:
    f.write(json.dumps(sys.argv[1:]) + "\n")
try:
    with open(p, "rb") as f:
        testa = f.read(16)
except OSError:
    sys.stderr.write("%s: No such file or directory\n" % p)
    sys.exit(1)
audio = {"index": 0, "codec_type": "audio", "codec_name": "mp3", "disposition": {"attached_pic": 0}}
if testa.startswith(b"ID3") or testa.startswith(b"\xff\xfb"):
    o = {"streams": [audio], "format": {"format_name": "mp3", "duration": "3.000000", "tags": {}}}
elif testa.startswith(b"FILM"):
    o = {"streams": [{"index": 0, "codec_type": "video", "codec_name": "h264", "disposition": {"attached_pic": 0}},
                     dict(audio, index=1, codec_name="aac")],
         "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "5400.000000"}}
elif testa.startswith(b"\x89PNG"):   # una foto (anche chiamata .mp4): un flusso video png, fermo
    o = {"streams": [{"index": 0, "codec_type": "video", "codec_name": "png", "disposition": {"attached_pic": 0}}],
         "format": {"format_name": "png_pipe"}}
elif testa.startswith(b"MUTO"):   # un mp4 con il solo audio: non è un film
    o = {"streams": [dict(audio, codec_name="aac")], "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "200.0"}}
else:
    sys.stderr.write("%s: Invalid data found when processing input\n" % p)
    sys.exit(1)
print(json.dumps(o))
'''


def mp3_finto(titolo, artista, album="", fotogrammi=10):
    """Un MP3 piccolissimo con i tag ID3v2.3 (come quello del ffmpeg finto): la libreria lo legge davvero."""
    def frame(fid, testo):
        d = b"\x01" + testo.encode("utf-16")
        return fid.encode() + struct.pack(">I", len(d)) + b"\x00\x00" + d
    corpo = b"".join(frame(f, t) for f, t in (("TIT2", titolo), ("TPE1", artista), ("TALB", album)) if t)
    n = len(corpo)
    return (b"ID3\x03\x00\x00" + bytes([(n >> 21) & 127, (n >> 14) & 127, (n >> 7) & 127, n & 127]) + corpo +
            (b"\xff\xfb\x90\x00" + bytes(413)) * fotogrammi)


def quote(s):
    return _quote(s, safe="")


# ----- YouTube finto: risposte con la stessa struttura di youtubei/v1/search (ridotta all'essenziale) -----

def _brano_ytm(vid, titolo, artisti, album, durata, explicit):
    corse = []
    for a in artisti:
        corse += [{"text": a, "navigationEndpoint": {"browseEndpoint": {"browseId": "UC" + a}}}, {"text": " e "}]
    corse = corse[:-1] + [{"text": " • "}, {"text": album}, {"text": " • "}, {"text": durata}]
    return {"musicResponsiveListItemRenderer": {
        "flexColumns": [
            {"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": [
                {"text": titolo, "navigationEndpoint": {"watchEndpoint": {"videoId": vid}}}]}}},
            {"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": corse}}},
            {"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": [{"text": "3,3 Mld riproduzioni"}]}}}],
        "badges": [{"musicInlineBadgeRenderer": {"icon": {"iconType": "MUSIC_EXPLICIT_BADGE"}}}] if explicit else [],
        "thumbnail": {"musicThumbnailRenderer": {"thumbnail": {"thumbnails": [
            {"url": "https://yt3.googleusercontent.com/c-%s=w60-h60-s-l90-rj" % vid, "width": 60},
            {"url": "https://yt3.googleusercontent.com/c-%s=w120-h120-s-l90-rj" % vid, "width": 120}]}}},
        "overlay": {"musicItemThumbnailOverlayRenderer": {"content": {"musicPlayButtonRenderer": {
            "playNavigationEndpoint": {"watchEndpoint": {"videoId": vid}}}}}}}}


def _video_yt(vid, titolo, canale, durata):
    v = {"videoId": vid, "title": {"runs": [{"text": titolo}]}, "ownerText": {"runs": [{"text": canale}]},
         "thumbnail": {"thumbnails": [{"url": "https://i.ytimg.com/vi/%s/hq720.jpg" % vid}]}}
    if durata:
        v["lengthText"] = {"simpleText": durata}
    return {"videoRenderer": v}


RISPOSTA_YTM = {"contents": {"tabbedSearchResultsRenderer": {"tabs": [{"tabRenderer": {"content": {
    "sectionListRenderer": {"contents": [{"musicShelfRenderer": {"contents": [
        _brano_ytm("NoExplicit1", "Without Me", ["Eminem"], "The Eminem Show", "4:50", False),
        _brano_ytm("tqxRidAWER8", "Without Me", ["Eminem"], "The Eminem Show", "4:51", True),
        _brano_ytm("RadioEdit01", "Shake That (Radio Edit)", ["Eminem", "Nate Dogg"], "Curtain Call", "4:35", True),
        _brano_ytm("D1I1x2pYMK0", "Kill You", ["Eminem"], "The Marshall Mathers LP", "1:04:25", True),
        _brano_ytm("TaylorClean", "Clean", ["Taylor Swift"], "1989", "4:31", False),
        _brano_ytm("tqxRidAWER8", "Without Me", ["Eminem"], "The Eminem Show", "4:51", True)]}}]}}}}]}}}
RISPOSTA_YT = {"contents": {"twoColumnSearchResultsRenderer": {"primaryContents": {"sectionListRenderer": {"contents": [
    {"itemSectionRenderer": {"contents": [
        _video_yt("YVkUvmDQ3HY", "Eminem - Without Me (Official Music Video)", "EminemMusic", "4:58"),
        _video_yt("LiveLiveLiv", "Eminem radio 24/7", "Radio", None),
        _video_yt("fsG39JgqTmo", "Without Me [HD]", "Eminem - Topic", "4:51")]}}]}}}}}


class FintoYouTube(http.server.BaseHTTPRequestHandler):
    richieste = []

    def do_POST(self):
        corpo = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        FintoYouTube.richieste.append((self.path, corpo))
        if corpo.get("query") == "rotto":
            self.send_response(500)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        musica = corpo.get("context", {}).get("client", {}).get("clientName") == "WEB_REMIX"
        dati = json.dumps(RISPOSTA_YTM if musica else RISPOSTA_YT).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(dati)))
        self.end_headers()
        self.wfile.write(dati)

    def log_message(self, *x):
        pass


def porta_libera():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def richiesta(porta, metodo, percorso, corpo=None, intest=None, tempo=30):
    """(codice, intestazioni, corpo in byte)."""
    c = http.client.HTTPConnection("127.0.0.1", porta, timeout=tempo)
    try:
        h = dict(intest or {})
        dati = None
        if corpo is not None:
            dati = corpo if isinstance(corpo, bytes) else json.dumps(corpo).encode("utf-8")
            h.setdefault("Content-Type", "application/json")
        c.request(metodo, percorso, body=dati, headers=h)
        r = c.getresponse()
        return r.status, r.headers, r.read()
    finally:
        c.close()


def aspetta(cond, tempo=20, passo=0.05):
    fine = time.time() + tempo
    while time.time() < fine:
        v = cond()
        if v:
            return v
        time.sleep(passo)
    return cond()


class Server:
    def __init__(self, base, cartelle, extra=()):
        self.base, self.c, self.extra = base, cartelle, list(extra)
        self.porta = porta_libera()
        self.p = None

    def avvia(self):
        env = dict(os.environ)
        env["PATH"] = self.c["bin"] + os.pathsep + env.get("PATH", "")
        env["FINTO_DIR"] = self.c["finto"]
        if self.c.get("youtubei"):
            env["ATLANTE_YOUTUBEI"] = self.c["youtubei"]
        self.log = open(os.path.join(self.base, "server-%d.log" % self.porta), "wb")
        self.p = subprocess.Popen([sys.executable, "-I", SERVER, self.c["musica"], "--porta", str(self.porta),
                                   "--cache", self.c["cache"], "--app", self.c["app"], "--video-dir", self.c["video"],
                                   "--libri-dir", self.c["libri"], "--musica-dir", self.c["scaricati"],
                                   "--ia-indirizzo", "http://127.0.0.1:%d" % self.c["porta_ollama"],
                                   # caricamenti: mai le cartelle vere di chi fa le prove
                                   "--controllo-dir", self.c["controllo"], "--film-dir", self.c["film"],
                                   "--tolti-dir", self.c["tolti"]] + self.extra,
                                  env=env, stdout=self.log, stderr=subprocess.STDOUT)

        def pronto():
            try:
                return richiesta(self.porta, "GET", "/api/extra/stato", tempo=5)[0] == 200
            except OSError:
                return self.p.poll() is not None   # il server è uscito: lo dice l'asserzione sotto
        aspetta(pronto, 20, 0.1)
        if self.p.poll() is not None:
            raise RuntimeError("il server non è partito: %s" % self.leggi_log())
        return self

    def leggi_log(self):
        self.log.flush()
        with open(self.log.name, "rb") as f:
            return f.read().decode("utf-8", "replace")

    def ferma(self):
        if self.p and self.p.poll() is None:
            self.p.terminate()
            try:
                self.p.wait(10)
            except subprocess.TimeoutExpired:
                self.p.kill()
                self.p.wait()
        self.log.close()

    def get(self, percorso, **k):
        return richiesta(self.porta, "GET", percorso, **k)

    def json(self, metodo, percorso, corpo=None, codice=200, **k):
        st, h, b = richiesta(self.porta, metodo, percorso, corpo, **k)
        try:
            j = json.loads(b.decode("utf-8"))
        except ValueError:
            j = None
        if codice is not None and st != codice:
            raise AssertionError("%s %s: atteso %d, avuto %d %r" % (metodo, percorso, codice, st, b[:300]))
        return j


def prepara_cartelle(base):
    c = {k: os.path.join(base, k) for k in ("musica", "cache", "app", "video", "libri", "bin", "finto")}
    for p in c.values():
        os.makedirs(p, exist_ok=True)
    c["scaricati"] = os.path.join(base, "scaricati")   # non c'è ancora: la crea il primo scaricamento
    for k, n in (("controllo", "Da controllare"), ("film", "Film"), ("tolti", "Tolti")):
        c[k] = os.path.join(base, n)   # anche queste le crea il primo caricamento
    os.makedirs(os.path.join(c["app"], "x"))
    for nome, testo in (("index.html", "<!doctype html><title>app</title>"), ("x/prova.html", "<!doctype html><p>x"),
                        ("x/kit.css", "body{}")):
        with open(os.path.join(c["app"], nome), "w", encoding="utf-8") as f:
            f.write(testo)
    for nome, codice in (("yt-dlp", FINTO_YTDLP), ("say", FINTO_SAY), ("ffmpeg", FINTO_FFMPEG), ("ffprobe", FINTO_FFPROBE)):
        p = os.path.join(c["bin"], nome)
        with open(p, "w", encoding="utf-8") as f:
            f.write("#!%s\n%s" % (sys.executable, codice))
        os.chmod(p, 0o755)
    # tailscale finto in ogni server delle prove: anche dove nessuna prova lo chiama, il vero non deve partire mai
    p = os.path.join(c["bin"], "tailscale")
    with open(p, "w", encoding="utf-8") as f:
        f.write("#!%s\n%s" % (sys.executable, FINTO_TAILSCALE))
    os.chmod(p, 0o755)
    c["porta_ollama"] = porta_libera()   # nessuno in ascolto: Ollama «assente»
    return c


class TestExtra(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = tempfile.mkdtemp(prefix="atlante-extra-")
        cls.c = prepara_cartelle(cls.base)
        cls.yt = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FintoYouTube)
        threading.Thread(target=cls.yt.serve_forever, daemon=True).start()
        cls.c["youtubei"] = "http://127.0.0.1:%d" % cls.yt.server_address[1]
        cls.s = Server(cls.base, cls.c).avvia()

    @classmethod
    def tearDownClass(cls):
        cls.s.ferma()
        cls.yt.shutdown()
        shutil.rmtree(cls.base, ignore_errors=True)

    def lavoro(self, tipo, id_):
        for l in self.s.json("GET", "/api/extra/%s" % tipo)["lavori"]:
            if l["id"] == id_:
                return l
        return None

    def finito(self, tipo, id_, tempo=20):
        l = aspetta(lambda: (lambda x: x if x and x["stato"] in ("fatto", "errore") else None)(self.lavoro(tipo, id_)), tempo)
        self.assertIsNotNone(l, "lavoro %s non finito: %r\n%s" % (id_, self.lavoro(tipo, id_), self.s.leggi_log()))
        return l

    def argomenti_ytdlp(self, nome):
        with open(os.path.join(self.c["finto"], "ytdlp-%s.json" % nome)) as f:
            return json.load(f)

    # ----- stato e richieste in generale -----

    def test_stato(self):
        j = self.s.json("GET", "/api/extra/stato")
        for k in ("ytdlp", "ffmpeg", "say", "ollama", "modelli", "video_dir", "libri_dir", "sistema"):
            self.assertIn(k, j)
        self.assertIs(j["ytdlp"], True)
        self.assertIs(j["say"], True)
        self.assertIs(j["ollama"], False)
        self.assertIsInstance(j["ffmpeg"], bool)
        self.assertEqual(j["modelli"], [])
        self.assertEqual(j["video_dir"], self.c["video"])
        self.assertEqual(j["libri_dir"], self.c["libri"])
        self.assertTrue(j["sistema"])
        self.assertEqual(j["ytdlp_versione"], "2099.01.01")

    def test_prefisso_e_head(self):
        self.assertEqual(self.s.get("/musica/api/extra/stato")[0], 200)
        st, h, b = richiesta(self.s.porta, "HEAD", "/api/extra/stato")
        self.assertEqual((st, b), (200, b""))

    def test_intestazioni_cache(self):
        st, h, _ = self.s.get("/api/extra/stato")
        self.assertEqual(h.get_all("Cache-Control"), ["no-store"])
        st, h, _ = self.s.get("/api/extra/proxy?url=http%3A%2F%2F127.0.0.1%2Fx.js")
        self.assertEqual(h.get_all("Cache-Control"), ["no-store"])
        for p in ("/x/prova.html", "/x/kit.css", "/index.html", "/"):
            st, h, _ = self.s.get(p)
            self.assertEqual(st, 200, p)
            self.assertEqual(h.get_all("Cache-Control"), ["no-cache"], p)

    def test_errori_richiesta(self):
        j = self.s.json("GET", "/api/extra/nulla", codice=404)
        self.assertIn("errore", j)
        self.assertIn("errore", self.s.json("DELETE", "/api/extra/stato", codice=405))
        self.assertIn("JSON", self.s.json("POST", "/api/extra/video", b"non json", codice=400)["errore"])
        self.assertIn("JSON", self.s.json("POST", "/api/extra/video", [1, 2], codice=400)["errore"])
        # un altro sito aperto nel browser non può avviare scaricamenti
        self.s.json("POST", "/api/extra/video", {"url": "https://example.com/v"}, codice=403,
                    intest={"Origin": "http://sito-cattivo.example"})
        self.s.json("POST", "/api/extra/video", {"url": "ftp://example.com/v"}, codice=400,
                    intest={"Origin": "http://127.0.0.1:%d" % self.s.porta})
        self.s.json("POST", "/api/extra/video", {"url": "https://example.com/v", "tipo": "film"}, codice=400)
        self.s.json("POST", "/api/extra/video/nessuno/annulla", codice=404)
        self.assertEqual(self.s.get("/api/extra/video/file/nessuno")[0], 404)
        self.assertEqual(richiesta(self.s.porta, "POST", "/altro", {})[0], 501)
        st, _, b = richiesta(self.s.porta, "POST", "/api/extra/ia", b"x" * 10,
                             intest={"Content-Length": str(30 * 1024 * 1024)}, tempo=10)
        self.assertEqual(st, 413)

    # ----- proxy -----

    def test_proxy_rifiuta_indirizzi_locali(self):
        for url, codice in (("http://127.0.0.1:%d/api/extra/stato" % self.s.porta, 403),
                            ("http://localhost:11434/api/tags", 403), ("http://10.0.0.1/", 403),
                            ("http://192.168.1.1/", 403), ("http://169.254.169.254/latest/meta-data/", 403),
                            ("http://100.100.100.100/", 403), ("http://[::1]/", 403), ("http://[::ffff:127.0.0.1]/", 403),
                            ("http://0.0.0.0/", 403), ("file:///etc/passwd", 400), ("ftp://example.com/", 400),
                            ("javascript:alert(1)", 400)):
            st, h, b = self.s.get("/api/extra/proxy?url=" + quote(url))
            self.assertEqual(st, codice, "%s -> %d %r" % (url, st, b))
            self.assertIn("errore", json.loads(b.decode("utf-8")))
        self.assertEqual(self.s.get("/api/extra/proxy")[0], 400)

    def test_proxy_sito_pubblico(self):
        try:
            socket.create_connection(("example.com", 443), 5).close()
        except OSError:
            self.skipTest("rete non disponibile")
        st, h, b = self.s.get("/api/extra/proxy?url=" + quote("https://example.com/"), tempo=40)
        if st in (502, 504):
            self.skipTest("example.com non raggiungibile dal server: %r" % b[:200])
        self.assertEqual(st, 200, b[:300])
        self.assertIn("text/html", h["Content-Type"])
        self.assertEqual(h.get_all("Cache-Control"), ["no-store"])
        self.assertIn(b"Example Domain", b)

    # ----- video -----

    def test_video_audio(self):
        stato = self.s.json("GET", "/api/extra/stato")
        id_ = self.s.json("POST", "/api/extra/video", {"url": "https://example.com/watch/audio1", "tipo": "audio"})["id"]
        self.assertTrue(id_)
        l = aspetta(lambda: (lambda x: x if x and x["stato"] == "in corso" and x["progresso"] > 0 else None)(
            self.lavoro("video", id_)), 15)
        self.assertIsNotNone(l, self.lavoro("video", id_))
        self.assertTrue(0 < l["progresso"] < 100, l)
        l = self.finito("video", id_)
        self.assertEqual(l["stato"], "fatto", l)
        self.assertEqual(l["progresso"], 100)
        self.assertEqual(l["titolo"], "Video di prova")
        self.assertEqual(l["nome"], "Video di prova [audio1].m4a")
        self.assertEqual(l["dimensione"], 1024)
        self.assertEqual(l["file"], "video/file/" + id_)
        self.assertEqual(l["url"], "https://example.com/watch/audio1")
        self.assertNotIn("_percorso", l)
        self.assertTrue(os.path.isfile(os.path.join(self.c["video"], l["nome"])))
        a = self.argomenti_ytdlp("audio1")["args"]
        self.assertIn("--newline", a)
        self.assertEqual(a[-2:], ["--", "https://example.com/watch/audio1"])
        self.assertIn("home:" + self.c["video"], a)
        if stato["ffmpeg"]:
            self.assertIn("-x", a)
            self.assertEqual(a[a.index("--audio-format") + 1], "m4a")
        # il file, intero e a pezzi
        st, h, b = self.s.get("/api/extra/" + l["file"])
        self.assertEqual((st, len(b)), (200, 1024))
        self.assertEqual(h["Content-Type"], "audio/mp4")
        st, h, b = self.s.get("/api/extra/video/file/" + id_, intest={"Range": "bytes=10-19"})
        self.assertEqual((st, b), (206, bytes(range(10, 20))))
        self.assertEqual(h["Content-Range"], "bytes 10-19/1024")
        # nessun file temporaneo rimasto
        self.assertEqual(os.listdir(os.path.join(self.c["cache"], "extra", "tmp")), [])

    def test_video_video_e_riavvio(self):
        stato = self.s.json("GET", "/api/extra/stato")
        id_ = self.s.json("POST", "/api/extra/video", {"url": "https://example.com/v/video1", "tipo": "video",
                                                       "qualita": "720"})["id"]
        lento = self.s.json("POST", "/api/extra/video", {"url": "https://example.com/v/lento"})["id"]
        l = self.finito("video", id_)
        self.assertEqual(l["stato"], "fatto", l)
        self.assertEqual(l["nome"], "Video di prova [video1].mp4")
        a = self.argomenti_ytdlp("video1")["args"]
        f = a[a.index("-f") + 1]
        self.assertIn("height<=720", f)
        if stato["ffmpeg"]:
            self.assertTrue(f.startswith("bv*[height<=720]+ba/b[height<=720]"), f)
            self.assertEqual(a[a.index("--merge-output-format") + 1], "mp4")
        self.assertEqual(self.s.get("/api/extra/video/file/" + id_)[1]["Content-Type"], "video/mp4")
        self.assertTrue(aspetta(lambda: (self.lavoro("video", lento) or {}).get("stato") == "in corso", 10))
        # dopo un riavvio (secondo server con la stessa cache) i lavori finiti restano, quelli a metà sono interrotti
        s2 = Server(self.base, self.c).avvia()
        try:
            elenco = {x["id"]: x for x in s2.json("GET", "/api/extra/video")["lavori"]}
            self.assertEqual(elenco[id_]["stato"], "fatto")
            self.assertEqual(elenco[id_]["nome"], l["nome"])
            self.assertEqual(s2.get("/api/extra/video/file/" + id_)[0], 200)
            self.assertEqual(elenco[lento]["stato"], "errore")
            self.assertIn("Interrotto", elenco[lento]["errore"])
        finally:
            s2.ferma()
        self.s.json("POST", "/api/extra/video/%s/annulla" % lento)
        self.assertEqual(self.finito("video", lento)["errore"], "Annullato")
        # un lavoro finito, «annullato», esce dall'elenco ma il file resta
        self.assertEqual(self.s.json("POST", "/api/extra/video/%s/annulla" % id_), {"ok": True})
        self.assertIsNone(self.lavoro("video", id_))
        self.assertTrue(os.path.isfile(os.path.join(self.c["video"], l["nome"])))

    def test_video_annulla(self):
        id_ = self.s.json("POST", "/api/extra/video", {"url": "https://example.com/v/lento", "tipo": "video"})["id"]
        self.assertTrue(aspetta(lambda: (self.lavoro("video", id_) or {}).get("progresso", 0) > 0, 15))
        pid = aspetta(lambda: os.path.exists(os.path.join(self.c["finto"], "ytdlp-lento.json")) and
                      self.argomenti_ytdlp("lento")["pid"], 5)
        self.assertEqual(self.s.json("POST", "/api/extra/video/%s/annulla" % id_), {"ok": True})
        l = self.finito("video", id_, 15)
        self.assertEqual((l["stato"], l["errore"]), ("errore", "Annullato"))

        def morto():
            try:
                os.kill(pid, 0)
                return False
            except ProcessLookupError:
                return True
            except PermissionError:
                return False
        self.assertTrue(aspetta(morto, 10), "yt-dlp ancora vivo dopo l'annullamento")
        self.assertFalse(os.path.exists(os.path.join(self.c["cache"], "extra", "tmp", "video-" + id_)))

    def test_video_errore(self):
        id_ = self.s.json("POST", "/api/extra/video", {"url": "https://example.com/v/errore"})["id"]
        l = self.finito("video", id_)
        self.assertEqual(l["stato"], "errore")
        self.assertIn("Video non disponibile", l["errore"])

    # ----- musica da internet -----

    def chiamate_ytdlp(self, nome):
        try:
            with open(os.path.join(self.c["finto"], "chiamate.log")) as f:
                return [r.strip() for r in f].count(nome)
        except OSError:
            return 0

    def ffmpeg_ultimo(self):
        with open(os.path.join(self.c["finto"], "ffmpeg.log"), encoding="utf-8") as f:
            return json.loads(f.read().strip().splitlines()[-1])

    def test_musica_cerca_youtube_music(self):
        j = self.s.json("GET", "/api/extra/musica/cerca?q=" + quote("eminem  without me"))
        self.assertEqual(j["fonte"], "ytm")
        r = j["risultati"]
        # doppione tolto; explicit prima, censurate (la copia senza «E» e la «Radio Edit») in fondo, il resto in ordine
        # «Clean» è il titolo del brano, non una versione censurata
        self.assertEqual([x["id"] for x in r], ["tqxRidAWER8", "D1I1x2pYMK0", "TaylorClean", "NoExplicit1", "RadioEdit01"])
        self.assertEqual([x["censurata"] for x in r], [False, False, False, True, True])
        self.assertEqual(r[0], {"id": "tqxRidAWER8", "titolo": "Without Me", "artista": "Eminem", "album": "The Eminem Show",
                                "durata": 291, "copertina": "https://yt3.googleusercontent.com/c-tqxRidAWER8=w544-h544-s-l90-rj",
                                "explicit": True, "censurata": False})
        self.assertEqual(r[1]["durata"], 3865)
        self.assertEqual(r[4]["artista"], "Eminem e Nate Dogg")
        percorso, corpo = FintoYouTube.richieste[-1]
        self.assertEqual(percorso, "/youtubei/v1/search?prettyPrint=false")
        self.assertEqual(corpo["query"], "eminem without me")   # spazi in più tolti
        self.assertEqual(corpo["context"]["client"]["clientName"], "WEB_REMIX")
        self.assertEqual(corpo["context"]["client"]["hl"], "it")
        self.assertTrue(corpo["params"])   # filtro «Brani»

    def test_musica_cerca_youtube(self):
        r = self.s.json("GET", "/api/extra/musica/cerca?fonte=yt&q=eminem")["risultati"]
        self.assertEqual([(x["id"], x["artista"], x["titolo"]) for x in r],
                         [("YVkUvmDQ3HY", "Eminem", "Without Me"), ("fsG39JgqTmo", "Eminem", "Without Me")])   # niente dirette
        self.assertEqual(r[0]["copertina"], "https://i.ytimg.com/vi/YVkUvmDQ3HY/mqdefault.jpg")
        self.assertEqual(r[0]["durata"], 298)
        self.assertNotIn("params", FintoYouTube.richieste[-1][1])

    def test_musica_cerca_errori(self):
        self.assertIn("errore", self.s.json("GET", "/api/extra/musica/cerca?q=%20", codice=400))
        self.assertIn("errore", self.s.json("GET", "/api/extra/musica/cerca?q=x&fonte=spotify", codice=400))
        # YouTube non risponde e anche la riserva (yt-dlp) non trova niente
        self.assertIn("YouTube non risponde", self.s.json("GET", "/api/extra/musica/cerca?q=rotto", codice=502)["errore"])
        self.assertIn("errore", self.s.json("POST", "/api/extra/musica/cerca?q=x", {}, codice=405))

    def test_musica_ascolta(self):
        st, h, b = self.s.get("/api/extra/musica/ascolta/Ascolto0001", tempo=40)
        self.assertEqual((st, len(b)), (200, 1024), b[:200])
        self.assertIn(h["Content-Type"], ("audio/mp4", "video/mp4"))
        a = self.argomenti_ytdlp("watch?v=Ascolto0001")["args"]
        self.assertEqual(a[-2:], ["--", "https://music.youtube.com/watch?v=Ascolto0001"])
        self.assertEqual(a[a.index("-f") + 1], "ba[ext=m4a]/ba/b")
        # la seconda volta (anche a pezzi) dalla cache: yt-dlp non riparte
        st, h, b = self.s.get("/api/extra/musica/ascolta/Ascolto0001", intest={"Range": "bytes=10-19"})
        self.assertEqual((st, b), (206, bytes(range(10, 20))))
        self.assertEqual(self.chiamate_ytdlp("watch?v=Ascolto0001"), 1)
        self.assertEqual(self.s.get("/api/extra/musica/ascolta/corto")[0], 400)
        self.assertEqual(self.s.get("/api/extra/musica/ascolta/errore00000")[0], 502)

    def test_musica_scarica(self):
        brano = {"id": "Scarica0001", "titolo": "Without Me", "artista": "Eminem", "album": "The Eminem Show",
                 "copertina": "https://127.0.0.1/copertina.jpg", "explicit": True}
        id_ = self.s.json("POST", "/api/extra/musica", brano)["id"]
        self.assertEqual(self.s.json("POST", "/api/extra/musica", brano)["id"], id_)   # due tocchi: un lavoro solo
        l = self.finito("musica", id_, 40)
        self.assertEqual(l["stato"], "fatto", l)
        self.assertEqual(l["nome"], "Eminem - Without Me.mp3")
        self.assertEqual((l["yt"], l["url"]), ("Scarica0001", "https://music.youtube.com/watch?v=Scarica0001"))
        self.assertTrue(l["brano"], l)
        self.assertNotIn("_percorso", l)
        self.assertTrue(os.path.isfile(os.path.join(self.c["scaricati"], "Eminem - Without Me.mp3")))
        self.assertEqual([n for n in os.listdir(self.c["scaricati"]) if n.startswith(".")], [])
        f = self.ffmpeg_ultimo()
        self.assertEqual(f["tag"]["title"], "Without Me")
        self.assertEqual(f["tag"]["purl"], "https://music.youtube.com/watch?v=Scarica0001")
        self.assertEqual(f["tag"]["ITUNESADVISORY"], "1")
        self.assertEqual(f["args"][f["args"].index("-q:a") + 1], "0")   # MP3 alla qualità massima, come il Cloud
        # nella libreria, con i tag giusti
        cat = self.s.json("GET", "/api/musica/catalogo")["brani"]
        b = [x for x in cat if x["id"] == l["brano"]]
        self.assertEqual([(x["title"], x["artist"], x["album"]) for x in b], [("Without Me", "Eminem", "The Eminem Show")])
        # di nuovo lo stesso brano a lavoro finito: niente doppione
        self.assertEqual(self.s.json("POST", "/api/extra/musica", brano)["id"], id_)
        self.assertEqual(self.chiamate_ytdlp("watch?v=Scarica0001"), 1)
        # un brano con lo stesso nome ma un altro video: file accanto, non sopra
        l2 = self.finito("musica", self.s.json("POST", "/api/extra/musica", dict(brano, id="Scarica0002"))["id"], 40)
        self.assertEqual(l2["nome"], "Eminem - Without Me (2).mp3")
        # tolto dall'elenco: il brano resta nella libreria
        self.assertEqual(self.s.json("POST", "/api/extra/musica/%s/annulla" % id_), {"ok": True})
        self.assertIsNone(self.lavoro("musica", id_))
        self.assertTrue(os.path.isfile(os.path.join(self.c["scaricati"], "Eminem - Without Me.mp3")))
        self.assertEqual(self.s.json("GET", "/api/extra/stato")["musica_dir"], self.c["scaricati"])

    def test_musica_link(self):
        # un link di un altro sito: titolo dalla pagina
        l = self.finito("musica", self.s.json("POST", "/api/extra/musica", {"url": "https://soundcloud.com/x/brano1"})["id"], 40)
        self.assertEqual((l["stato"], l["nome"], l["yt"]), ("fatto", "Video di prova.mp3", ""), l)
        self.assertEqual(self.ffmpeg_ultimo()["tag"]["purl"], "https://soundcloud.com/x/brano1")
        # un link di YouTube vale come un brano della ricerca
        l = self.finito("musica", self.s.json("POST", "/api/extra/musica", {"url": "https://youtu.be/Collegato01?si=abc"})["id"], 40)
        self.assertEqual((l["stato"], l["yt"]), ("fatto", "Collegato01"), l)
        self.assertEqual(l["titolo"], "Video di prova")

    def test_musica_annulla_ed_errori(self):
        id_ = self.s.json("POST", "/api/extra/musica", {"url": "https://example.com/m/lento"})["id"]
        self.assertTrue(aspetta(lambda: (self.lavoro("musica", id_) or {}).get("progresso", 0) > 0, 15))
        self.assertEqual(self.s.json("POST", "/api/extra/musica/%s/annulla" % id_), {"ok": True})
        l = self.finito("musica", id_, 15)
        self.assertEqual((l["stato"], l["errore"]), ("errore", "Annullato"))
        l = self.finito("musica", self.s.json("POST", "/api/extra/musica", {"url": "https://example.com/m/errore"})["id"])
        self.assertEqual(l["stato"], "errore")
        self.assertIn("Video non disponibile", l["errore"])
        for corpo in ({}, {"id": "corto"}, {"url": "ftp://example.com/x"}, {"url": "non un link"}):
            self.assertIn("errore", self.s.json("POST", "/api/extra/musica", corpo, codice=400), corpo)
        self.s.json("POST", "/api/extra/musica", {"id": "Scarica0001"}, codice=403, intest={"Origin": "http://cattivo.example"})
        self.s.json("POST", "/api/extra/musica/nessuno/annulla", codice=404)

    # ----- dispositivi (telecomando) -----

    def test_gestione_senza_indirizzo_pubblico(self):
        r = self.s.json("POST", "/api/extra/gestione/persone", {"nome": "Ida"}, codice=409)
        self.assertIn("ATLANTE_PUBBLICO", r["errore"])

    def test_dispositivi(self):
        iphone, mac = "iphone-prova-0001", "mac-prova-00000001"
        stato = {"brano": {"sid": "abc", "titolo": "Without Me", "artista": "Eminem"}, "suona": True, "t": 12.5,
                 "coda": ["abc", "def"]}
        self.assertEqual(self.s.json("POST", "/api/extra/dispositivi/%s/stato" % mac,
                                     {"nome": "  MacBook   di Samuele ", "tipo": "mac", "stato": stato}), {"comandi": []})
        self.s.json("POST", "/api/extra/dispositivi/%s/stato" % iphone, {"nome": "iPhone", "tipo": "telefono", "stato": {}})
        d = {x["id"]: x for x in self.s.json("GET", "/api/extra/dispositivi")["dispositivi"]}
        self.assertEqual((d[mac]["nome"], d[mac]["tipo"], d[mac]["stato"]), ("MacBook di Samuele", "mac", stato))
        self.assertEqual(d[iphone]["tipo"], "pc")   # tipo sconosciuto
        self.assertLess(d[mac]["fa"], 5)
        # comando dall'iPhone al Mac: l'attesa lunga del Mac risponde subito
        esito = {}

        def attendi():
            t0 = time.time()
            esito["c"] = self.s.json("GET", "/api/extra/dispositivi/%s/comandi?attesa=10" % mac)["comandi"]
            esito["dopo"] = time.time() - t0
        th = threading.Thread(target=attendi)
        th.start()
        time.sleep(0.4)
        self.assertEqual(self.s.json("POST", "/api/extra/dispositivi/%s/comando" % mac, {"azione": "seek", "t": 42}), {"ok": True})
        th.join(10)
        self.assertEqual(esito["c"], [{"azione": "seek", "t": 42}])
        self.assertLess(esito["dopo"], 5)
        # consegnato una volta sola; senza comandi l'attesa finisce vuota
        self.assertEqual(self.s.json("GET", "/api/extra/dispositivi/%s/comandi?attesa=0.3" % mac)["comandi"], [])
        # i comandi arrivano anche con lo stato (se l'attesa lunga non c'è)
        self.s.json("POST", "/api/extra/dispositivi/%s/comando" % mac, {"azione": "pause"})
        self.s.json("POST", "/api/extra/dispositivi/%s/comando" % mac, {"azione": "next"})
        self.assertEqual(self.s.json("POST", "/api/extra/dispositivi/%s/stato" % mac, {"nome": "MacBook", "stato": stato})["comandi"],
                         [{"azione": "pause"}, {"azione": "next"}])
        # errori
        self.assertIn("errore", self.s.json("POST", "/api/extra/dispositivi/sconosciuto-000/comando", {"azione": "play"}, codice=404))
        self.assertIn("errore", self.s.json("POST", "/api/extra/dispositivi/%s/comando" % mac, {"azione": "spegni"}, codice=400))
        self.assertIn("errore", self.s.json("POST", "/api/extra/dispositivi/corto/stato", {"stato": {}}, codice=400))
        self.assertIn("errore", self.s.json("POST", "/api/extra/dispositivi/%s/stato" % mac, {"stato": [1]}, codice=400))
        self.assertIn("errore", self.s.json("POST", "/api/extra/dispositivi/%s/stato" % mac, {"stato": {"x": "a" * 40000}}, codice=413))
        self.assertIn("errore", self.s.json("GET", "/api/extra/dispositivi/%s/comandi?attesa=x" % mac, codice=400))
        self.assertIn("errore", self.s.json("GET", "/api/extra/dispositivi/%s/stato" % mac, codice=405))
        self.s.json("POST", "/api/extra/dispositivi/%s/comando" % mac, {"azione": "play"}, codice=403,
                    intest={"Origin": "http://cattivo.example"})

    # ----- voci e audiolibri -----

    def test_voci(self):
        v = self.s.json("GET", "/api/extra/voci")["voci"]
        # prima le italiane, poi le altre per lingua
        self.assertEqual([x["nome"] for x in v], ["Alice", "Eddy (Italian (Italy))", "Fiona", "Alex"])
        self.assertEqual(v[1], {"nome": "Eddy (Italian (Italy))", "lingua": "it_IT", "esempio": "Ciao! Mi chiamo Eddy."})
        self.assertEqual(v[2]["lingua"], "en-scotland")

    def test_audiolibro(self):
        corpo = {"titolo": "Libro di prova", "autore": "Autrice", "voce": "Alice", "velocita": 200,
                 "capitoli": [{"titolo": "Uno", "testo": "Nel mezzo del cammin di nostra vita è così."},
                              {"titolo": "Vuoto", "testo": "   "},
                              {"titolo": "Due/tre: fine", "testo": "Secondo capitolo."}]}
        id_ = self.s.json("POST", "/api/extra/audiolibro", corpo)["id"]
        l = self.finito("audiolibro", id_)
        self.assertEqual(l["stato"], "fatto", l)
        self.assertEqual((l["titolo"], l["autore"], l["progresso"]), ("Libro di prova", "Autrice", 100))
        self.assertEqual([(c["n"], c["titolo"], c["pronto"]) for c in l["capitoli"]],
                         [(0, "Uno", True), (1, "Due/tre: fine", True)])
        self.assertAlmostEqual(l["capitoli"][0]["durata"], 4.3, places=1)
        cart = os.path.join(self.c["libri"], "Autrice - Libro di prova")
        self.assertEqual(sorted(os.listdir(cart)), ["001 Uno.m4a", "002 Due tre fine.m4a"])
        with open(os.path.join(self.c["finto"], "say.log"), encoding="utf-8") as f:
            chiamate = [json.loads(r) for r in f if r.strip()]
        primo = [c for c in chiamate if c["testo"].startswith("Nel mezzo")][-1]
        self.assertEqual(primo["testo"], "Nel mezzo del cammin di nostra vita è così.")
        a = primo["args"]
        self.assertEqual(a[a.index("-v") + 1], "Alice")
        self.assertEqual(a[a.index("-r") + 1], "200")
        self.assertTrue(a[a.index("-o") + 1].endswith(".m4a"))
        st, h, b = self.s.get("/api/extra/audiolibro/file/%s/0" % id_)
        self.assertEqual((st, h["Content-Type"]), (200, "audio/mp4"))
        self.assertEqual(b[4:8], b"ftyp")
        st, h, b = self.s.get("/api/extra/audiolibro/file/%s/1" % id_, intest={"Range": "bytes=0-7"})
        self.assertEqual((st, len(b)), (206, 8))
        self.assertEqual(self.s.get("/api/extra/audiolibro/file/%s/2" % id_)[0], 404)
        self.assertEqual(self.s.get("/api/extra/audiolibro/file/%s/x" % id_)[0], 404)
        # DELETE: via dall'elenco e dal disco
        self.assertEqual(self.s.json("DELETE", "/api/extra/audiolibro/" + id_), {"ok": True})
        self.assertIsNone(self.lavoro("audiolibro", id_))
        self.assertFalse(os.path.exists(cart))
        self.s.json("DELETE", "/api/extra/audiolibro/" + id_, codice=404)

    def test_audiolibro_richieste_sbagliate(self):
        self.s.json("POST", "/api/extra/audiolibro", {"titolo": "x", "capitoli": []}, codice=400)
        self.s.json("POST", "/api/extra/audiolibro", {"titolo": "x", "capitoli": [{"testo": ""}]}, codice=400)
        j = self.s.json("POST", "/api/extra/audiolibro", {"voce": "Nessuna", "capitoli": [{"testo": "ciao"}]}, codice=400)
        self.assertIn("Voce", j["errore"])

    # ----- IA e dispositivo -----

    def test_ia_senza_ollama(self):
        self.assertEqual(self.s.json("GET", "/api/extra/ia"), {"attiva": False, "modelli": [], "modello": ""})
        j = self.s.json("POST", "/api/extra/ia", {"prompt": "Ciao", "sistema": "", "json": False}, codice=503)
        self.assertEqual(j, {"errore": "IA locale non attiva sul Mac: installa Ollama (ollama.com) e un modello, "
                                       "es. ollama pull llama3.2"})
        self.s.json("POST", "/api/extra/ia", {"prompt": ""}, codice=400)

    def test_dispositivo(self):
        j = self.s.json("GET", "/api/extra/dispositivo", tempo=60)
        for k in ("nome", "sistema", "versione", "avvio", "memoria", "dischi", "pulizia"):
            self.assertIn(k, j)
        self.assertTrue(j["nome"])
        self.assertGreater(j["memoria"]["totale"], 0)
        self.assertGreaterEqual(j["memoria"]["libera"], 0)
        self.assertGreater(j["avvio"], 0)
        self.assertTrue(j["dischi"])
        d = j["dischi"][0]
        self.assertEqual(d["percorso"], "/")
        self.assertGreater(d["totale"], 0)
        self.assertGreaterEqual(d["libero"], 0)
        self.assertIsInstance(j["pulizia"], list)
        for v in j["pulizia"]:
            self.assertTrue({"nome", "percorso", "dimensione"} <= set(v), v)


def suono_finto(secondi, seme, hz=8000):
    """Audio finto a 8 kHz: colpi casuali (circa 4 al secondo, ampiezze e lunghezze diverse) su un fondo leggero,
    come gli attacchi di un brano vero. Stesso seme = stesso suono."""
    import array
    import random
    r = random.Random(seme)
    a = array.array("h", bytes(2 * int(secondi * hz)))
    t = 0.0
    while True:
        t += r.expovariate(4.0)
        if t >= secondi:
            break
        i, amp, lung = int(t * hz), r.randint(2000, 20000), r.randint(80, 400)
        for k in range(min(lung, len(a) - i)):
            a[i + k] = max(-32767, min(32767, a[i + k] + int(amp * (1 - k / lung) * (1 if (k // 3) % 2 else -1))))
    for j in range(0, len(a), 7):
        a[j] = max(-32767, min(32767, a[j] + r.randint(-150, 150)))
    return a


class TestVideoclip(unittest.TestCase):
    """Allineamento brano ↔ videoclip su suoni finti e preparazione con scaricamento e audio finti."""

    @classmethod
    def setUpClass(cls):
        sys.dont_write_bytecode = True
        spec = importlib.util.spec_from_file_location("musica_libreria_clip", SERVER)
        cls.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.m)
        cls.brano = suono_finto(120, 1)

    def tempo_video(self, mappa, t):
        for x in mappa:
            if x["da"] <= t < x["a"]:
                return t + x["sp"] + x.get("k", 0) * (t - x["da"])
        return None

    def allinea(self, brano, video):
        return self.m.allinea(self.m._caratteristiche([brano]), self.m._caratteristiche([video]))

    def test_introduzione_e_taglio(self):
        b, hz = self.brano, 8000
        video = suono_finto(7.3, 99) + b[:60 * hz] + b[80 * hz:]   # 7,3 s in più all'inizio, 20 s tagliati a metà
        r = self.allinea(b, video)
        self.assertEqual(len(r["mappa"]), 2, r)
        for t in (2, 10, 30, 50, 57):
            self.assertAlmostEqual(self.tempo_video(r["mappa"], t), t + 7.3, delta=0.03, msg=(t, r["mappa"]))
        for t in (83, 90, 110, 117):
            self.assertAlmostEqual(self.tempo_video(r["mappa"], t), t - 12.7, delta=0.03, msg=(t, r["mappa"]))
        for t in (62, 70, 77):   # il pezzo tagliato dal video: copertina, mai un'altra scena
            self.assertIsNone(self.tempo_video(r["mappa"], t), (t, r["mappa"]))
        self.assertGreater(r["copertura"], 0.75)

    def test_finale_diverso_e_silenzio(self):
        import array
        b, hz = self.brano, 8000
        silenzio = array.array("h", bytes(2 * 20 * hz))
        con_silenzio = b[:50 * hz] + silenzio + b[50 * hz:]   # 20 s muti in mezzo al brano
        video = b[:50 * hz] + silenzio + b[50 * hz:100 * hz] + suono_finto(30, 5)   # finale del video diverso
        r = self.allinea(con_silenzio, video)
        self.assertAlmostEqual(self.tempo_video(r["mappa"], 20), 20, delta=0.03, msg=r["mappa"])
        self.assertAlmostEqual(self.tempo_video(r["mappa"], 110), 110, delta=0.03, msg=r["mappa"])
        for t in (125, 135):   # il brano finisce in un altro modo: copertina
            self.assertIsNone(self.tempo_video(r["mappa"], t), (t, r["mappa"]))

    def test_deriva(self):
        # il video è l'1,5 per mille più lento (es. 25 contro 24,96 fotogrammi) e comincia 4 s dopo
        import array
        b = self.brano
        lento = array.array("h", (b[int(i / 1.0015)] for i in range(int(len(b) * 1.0015))))
        r = self.allinea(b, suono_finto(4, 3) + lento)
        for t in (5, 60, 115):
            self.assertAlmostEqual(self.tempo_video(r["mappa"], t), 4 + t * 1.0015, delta=0.03, msg=(t, r["mappa"]))

    def test_video_senza_rapporto(self):
        r = self.allinea(self.brano, suono_finto(150, 42))
        self.assertLess(r["copertura"], self.m.CLIP_COPERTURA, r)

    def test_preparazione_e_scheda(self):
        m = self.m
        base = tempfile.mkdtemp(prefix="atlante-clip-")
        try:
            cart, brano = os.path.join(base, "clip"), os.path.join(base, "A - Brano.mp3")
            with open(brano, "wb") as f:
                f.write(b"mp3")
            piano = os.path.join(base, "piano.json")
            with open(piano, "w") as f:
                json.dump({"A - Brano.mp3": {"id": "abcdefghijk"}, "B.mp3": None}, f)
            v = m.Videoclip(piano, cart)
            pcm = {brano: self.brano}
            vid_pcm = suono_finto(2.5, 7) + self.brano

            def scarica(vid):
                p = os.path.join(cart, vid + ".mp4")
                with open(p, "wb") as f:
                    f.write(b"video")
                pcm[p] = vid_pcm
                return p
            v._scarica = scarica
            m._bande = lambda p, ff: [pcm[p]]
            m._programma = lambda n: "/bin/" + n
            m._durata_audio = lambda p: 122.5
            s = v.prepara("A - Brano.mp3", brano)
            self.assertEqual(s["stato"], "pronto", s)
            self.assertAlmostEqual(s["mappa"][0]["sp"], 2.5, delta=0.03)
            st = os.stat(brano)
            firma = "%d:%d" % (st.st_size, int(st.st_mtime))
            self.assertEqual(v.pronti(), {"A - Brano.mp3": firma})
            x = v.stato(brano, firma)
            self.assertEqual((x["stato"], x["url"], x["durata"]), ("pronto", "clip/file/abcdefghijk.mp4", 122.5))
            # il file del brano cambia (es. versione explicit): si riallinea, la scheda vecchia non vale più
            self.assertEqual(v.stato(brano, "1:2")["stato"], "in coda")
            self.assertEqual(v.stato(os.path.join(base, "B.mp3"), "1:1"), {"stato": "nessuno"})
        finally:
            shutil.rmtree(base, ignore_errors=True)


class TestClipServer(unittest.TestCase):
    """API dei videoclip con un brano della libreria e il suo videoclip già pronto (scheda e MP4 finti)."""

    @classmethod
    def setUpClass(cls):
        cls.base = tempfile.mkdtemp(prefix="atlante-clipsrv-")
        cls.c = prepara_cartelle(cls.base)
        cls.brano = os.path.join(cls.c["musica"], "Artista - Brano.mp3")
        with open(cls.brano, "wb") as f:
            f.write(b"ID3\x03\x00\x00\x00\x00\x00\x00" + (b"\xff\xfb\x90\x00" + bytes(413)) * 20)
        cls.clip = os.path.join(cls.base, "clip")
        os.makedirs(cls.clip)
        with open(os.path.join(cls.clip, "abcdefghijk.mp4"), "wb") as f:
            f.write(bytes(range(256)) * 8)
        st = os.stat(cls.brano)
        cls.mappa = [{"da": 0.0, "a": 8.0, "sp": 3.25}]
        with open(os.path.join(cls.clip, "abcdefghijk.json"), "w") as f:
            json.dump({"id": "abcdefghijk", "nome": "Artista - Brano.mp3", "firma": "%d:%d" % (st.st_size, int(st.st_mtime)),
                       "stato": "pronto", "mappa": cls.mappa, "durata_video": 11.5, "versione": 2}, f)
        cls.piano = os.path.join(cls.base, "videoclip-piano.json")
        with open(cls.piano, "w") as f:
            json.dump({"Artista - Brano.mp3": {"id": "abcdefghijk"}}, f)
        cls.s = Server(cls.base, cls.c, ["--clip-piano", cls.piano, "--clip-dir", cls.clip]).avvia()

    @classmethod
    def tearDownClass(cls):
        cls.s.ferma()
        shutil.rmtree(cls.base, ignore_errors=True)

    def test_pronto(self):
        cat = aspetta(lambda: self.s.json("GET", "/api/musica/catalogo")["brani"], 20)
        self.assertEqual(len(cat), 1, cat)
        self.assertIs(cat[0].get("clip"), True)
        j = self.s.json("GET", "/api/extra/clip/" + cat[0]["id"])
        self.assertEqual(j, {"stato": "pronto", "url": "clip/file/abcdefghijk.mp4", "mappa": self.mappa, "durata": 11.5})
        st, h, b = self.s.get("/api/extra/clip/file/abcdefghijk.mp4", intest={"Range": "bytes=0-9"})
        self.assertEqual((st, b, h["Content-Type"]), (206, bytes(range(10)), "video/mp4"))
        a = self.s.json("GET", "/api/extra/clip")
        self.assertEqual((a["attivo"], a["totale"], a["pronti"], a["auto"]), (True, 1, 1, False))

    def test_errori(self):
        self.assertIn("errore", self.s.json("GET", "/api/extra/clip/nessuno", codice=404))
        for n in ("../x.mp4", "corto.mp4", "abcdefghijk.mov", "zzzzzzzzzzz.mp4"):
            self.assertEqual(self.s.get("/api/extra/clip/file/" + quote(n))[0], 404, n)


class TestDispositiviTempi(unittest.TestCase):
    """Scadenze del telecomando con i tempi accorciati: comandi vecchi scartati, dispositivi spariti, limite dell'elenco."""

    @classmethod
    def setUpClass(cls):
        sys.dont_write_bytecode = True
        spec = importlib.util.spec_from_file_location("musica_libreria_disp", SERVER)
        cls.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.m)

    def test_scadenze(self):
        m = self.m
        m.COMANDO_SCADE, m.DISP_VIVO = 0.3, 0.6
        d = m.Dispositivi()
        d.stato("vecchio-0001", {"nome": "iPhone", "stato": {}})
        d.comando("vecchio-0001", {"azione": "play"})
        time.sleep(0.4)
        self.assertEqual(d.stato("vecchio-0001", {"nome": "iPhone", "stato": {}}), [])   # «riproduci» troppo vecchio
        time.sleep(0.7)
        self.assertEqual(d.elenco(), [])   # non si fa sentire: sparito
        with self.assertRaises(m.ErroreExtra) as e:
            d.comando("vecchio-0001", {"azione": "play"})
        self.assertEqual(e.exception.codice, 404)
        # l'attesa lunga lo tiene vivo
        d.stato("vivo-00000001", {"nome": "Mac", "stato": {}})
        for _ in range(3):
            d.attendi("vivo-00000001", 0.3)
        self.assertEqual([x["id"] for x in d.elenco()], ["vivo-00000001"])

    def test_limite(self):
        m = self.m
        m.DISP_VIVO, m.DISP_MAX = 60, 3
        d = m.Dispositivi()
        for i in range(5):
            d.stato("disp-%04d" % i, {"nome": "D%d" % i, "stato": {}})
            time.sleep(0.01)
        self.assertEqual([x["id"] for x in d.elenco()], ["disp-0004", "disp-0003", "disp-0002"])


class TestProxyInterno(unittest.TestCase):
    """Il proxy dentro un server qualsiasi (gestisci_extra), verso un sito «pubblico» finto su 127.0.0.1:
    rimandi, Content-Type, limite di 15 MB, codici d'errore e rimando verso un indirizzo vietato."""

    @classmethod
    def setUpClass(cls):
        sys.dont_write_bytecode = True   # niente __pycache__ accanto al server
        spec = importlib.util.spec_from_file_location("musica_libreria_prova", SERVER)
        cls.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.m)
        cls.vietato_vero = staticmethod(cls.m._ip_vietato)
        cls.m._ip_vietato = lambda ip: ip == "127.0.0.2"   # qui 127.0.0.1 fa da sito pubblico

        class Sito(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/rimando":
                    self.send_response(302)
                    self.send_header("Location", "/feed?a=1")
                    self.end_headers()
                elif self.path == "/feed?a=1":
                    corpo = "<rss>è</rss>".encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/rss+xml; charset=utf-8")
                    self.send_header("Content-Length", str(len(corpo)))
                    self.end_headers()
                    self.wfile.write(corpo)
                elif self.path == "/vietato":
                    self.send_response(301)
                    self.send_header("Location", "http://127.0.0.2:%d/feed?a=1" % self.server.server_port)
                    self.end_headers()
                elif self.path == "/grande":   # 16 MB senza Content-Length
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.end_headers()
                    try:
                        for _ in range(64):
                            self.wfile.write(b"x" * 262144)
                    except OSError:
                        pass
                elif self.path == "/dichiarato":
                    self.send_response(200)
                    self.send_header("Content-Length", str(20 * 1024 * 1024))
                    self.end_headers()
                elif self.path == "/episodio":   # 20 MB di audio: passa a pezzi, oltre il limite delle pagine
                    self.send_response(200)
                    self.send_header("Content-Type", "audio/mpeg")
                    self.send_header("Content-Length", str(80 * 262144))
                    self.end_headers()
                    try:
                        for _ in range(80):
                            self.wfile.write(b"a" * 262144)
                    except OSError:
                        pass
                elif self.path == "/ua":
                    corpo = (self.headers.get("User-Agent") or "").encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain")
                    self.send_header("Content-Length", str(len(corpo)))
                    self.end_headers()
                    self.wfile.write(corpo)
                else:
                    self.send_error(404)

            def log_message(self, *x):
                pass

        m = cls.m

        class Proxy(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if not m.gestisci_extra(self, "GET"):
                    self.send_error(404)

            def log_message(self, *x):
                pass

        cls.sito = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Sito)
        cls.proxy = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
        for s in (cls.sito, cls.proxy):
            threading.Thread(target=s.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.m._ip_vietato = cls.vietato_vero
        for s in (cls.sito, cls.proxy):
            s.shutdown()
            s.server_close()

    def leggi(self, percorso):
        url = "http://127.0.0.1:%d%s" % (self.sito.server_port, percorso)
        return richiesta(self.proxy.server_port, "GET", "/api/extra/proxy?url=" + quote(url))

    def test_rimando_e_tipo(self):
        st, h, b = self.leggi("/rimando")
        self.assertEqual(st, 200, b)
        self.assertEqual(b.decode("utf-8"), "<rss>è</rss>")
        self.assertEqual(h["Content-Type"], "application/rss+xml; charset=utf-8")
        self.assertEqual(h["Cache-Control"], "no-store")
        self.assertTrue(h["X-Url-Finale"].endswith("/feed?a=1"))

    def test_user_agent_da_browser(self):
        st, h, b = self.leggi("/ua")
        self.assertEqual(st, 200)
        self.assertTrue(b.startswith(b"Mozilla/5.0"), b)

    def test_rimando_verso_indirizzo_vietato(self):
        st, h, b = self.leggi("/vietato")
        self.assertEqual(st, 403, b)

    def test_troppo_grande(self):
        self.assertEqual(self.leggi("/grande")[0], 413)
        self.assertEqual(self.leggi("/dichiarato")[0], 413)

    def test_audio_grande_passa(self):
        st, h, b = self.leggi("/episodio")
        self.assertEqual(st, 200)
        self.assertEqual(h["Content-Type"], "audio/mpeg")
        self.assertEqual(len(b), 80 * 262144)

    def test_errore_del_sito(self):
        st, h, b = self.leggi("/manca")
        self.assertEqual(st, 502)
        self.assertIn("404", json.loads(b.decode("utf-8"))["errore"])

    def test_indirizzi_vietati(self):
        v = self.vietato_vero
        for ip in ("127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.10", "169.254.169.254", "100.101.102.103",
                   "0.0.0.0", "224.0.0.1", "::1", "fe80::1", "fd00::1", "::ffff:192.168.0.1", "64:ff9b::7f00:1",
                   "2002:7f00:1::1", "255.255.255.255", "non un ip"):
            self.assertTrue(v(ip), ip)
        for ip in ("93.184.215.14", "8.8.8.8", "2606:4700:4700::1111", "::ffff:8.8.8.8"):
            self.assertFalse(v(ip), ip)


FUNNEL = {"Tailscale-Funnel-Request": "?1", "X-Forwarded-For": "203.0.113.7", "X-Forwarded-Proto": "https",
          "X-Forwarded-Host": "mac.esempio.ts.net:10000", "Host": "mac.esempio.ts.net:10000"}


class TestAccesso(unittest.TestCase):
    """Da Internet (tailscale funnel) serve la password; dalla rete Tailscale e dal Mac no."""

    @classmethod
    def setUpClass(cls):
        cls.base = tempfile.mkdtemp(prefix="atlante-accesso-")
        cls.c = prepara_cartelle(cls.base)
        cls.s = Server(cls.base, cls.c, ["--pubblico", "https://mac.esempio.ts.net:10000"]).avvia()
        cls.file = os.path.join(cls.c["cache"], "accesso.json")
        # le persone si preparano da qui, sullo stesso file del server (che lo rilegge quando cambia)
        sys.dont_write_bytecode = True
        spec = importlib.util.spec_from_file_location("musica_libreria_accesso", SERVER)
        cls.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.m)
        cls.pers = cls.m.Persone(os.path.join(cls.c["cache"], "persone.json"))

    @classmethod
    def tearDownClass(cls):
        cls.s.ferma()
        shutil.rmtree(cls.base, ignore_errors=True)

    def imposta(self, pw):
        r = subprocess.run([sys.executable, "-I", SERVER, "--imposta-password", "--cache", self.c["cache"]],
                           input=(pw + "\n").encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
        return r.returncode, r.stdout.decode("utf-8", "replace")

    def da_fuori(self, metodo, percorso, corpo=None, ip="203.0.113.7", biscotto=None, extra=None):
        h = dict(FUNNEL, **{"X-Forwarded-For": ip})
        if biscotto:
            h["Cookie"] = "altro=1; am_accesso=" + biscotto
        h.update(extra or {})
        return richiesta(self.s.porta, metodo, percorso, corpo, intest=h)

    def entra(self, pw, ip):
        corpo = ("nome=Atlante+Musica&password=" + quote(pw)).encode("utf-8")
        return self.da_fuori("POST", "/accesso", corpo, ip=ip,
                             extra={"Content-Type": "application/x-www-form-urlencoded", "Origin": "https://mac.esempio.ts.net:10000"})

    @staticmethod
    def biscotto(h):
        for v in h.get_all("Set-Cookie") or []:
            if v.startswith("am_accesso="):
                return v
        return None

    def test_1_senza_password_da_fuori_non_si_entra(self):
        if os.path.exists(self.file):
            with open(self.file) as f:
                self.assertNotIn("hash", json.load(f))
        st, h, b = self.da_fuori("GET", "/", extra={"Sec-Fetch-Mode": "navigate"})
        self.assertEqual((st, h["Location"]), (303, "accesso"))
        st, h, b = self.da_fuori("GET", "/accesso")
        self.assertEqual(st, 200)
        self.assertIn("non è ancora attivo", b.decode("utf-8"))
        self.assertNotIn('name="password"', b.decode("utf-8"))
        self.assertIn('name="codice"', b.decode("utf-8"))   # chi ha un invito entra anche senza password
        st, h, b = self.entra("qualsiasi-cosa", "203.0.113.1")
        self.assertEqual(st, 403)
        self.assertIsNone(self.biscotto(h))

    def test_2_dal_mac_e_da_tailscale_si_entra(self):
        for intest in ({},   # il Mac stesso (127.0.0.1, nessun proxy)
                       {"X-Forwarded-For": "100.127.102.35", "Tailscale-User-Login": "samuele@example.com"},
                       {"X-Forwarded-For": "100.101.1.2"},   # dispositivo della rete Tailscale senza identità
                       {"X-Forwarded-For": "fd7a:115c:a1e0::1"}):
            for p in ("/", "/api/extra/stato", "/x/prova.html"):
                st, h, b = richiesta(self.s.porta, "GET", p, intest=intest)
                self.assertEqual(st, 200, (p, intest))
            self.assertIn("am_accesso=", self.biscotto(h) or "", intest)
        # i biscotti dati in rete Tailscale valgono poi anche da Internet
        v = self.biscotto(h).split(";")[0].split("=", 1)[1]
        self.assertEqual(self.da_fuori("GET", "/api/extra/stato", biscotto=v)[0], 200)

    def test_3_da_fuori_senza_biscotto(self):
        st, h, b = self.da_fuori("GET", "/", extra={"Sec-Fetch-Mode": "navigate"})
        self.assertEqual((st, h["Location"]), (303, "accesso"))
        st, h, b = self.da_fuori("GET", "/x/prova.html")
        self.assertEqual((st, h["Location"]), (303, "../accesso"))
        for metodo, p in (("GET", "/api/extra/stato"), ("GET", "/api/musica/catalogo"), ("GET", "/musica/api/extra/stato"),
                          ("POST", "/api/extra/video"), ("DELETE", "/api/extra/audiolibro/x"), ("GET", "/x/kit.css"),
                          ("GET", "/sw.js"), ("HEAD", "/api/musica/file/1")):
            st, h, b = self.da_fuori(metodo, p, {} if metodo == "POST" else None)
            self.assertEqual(st, 401, (metodo, p))
            self.assertEqual(h["X-Atlante-Accesso"], "richiesto")
            if metodo != "HEAD":
                self.assertIs(json.loads(b.decode("utf-8"))["accesso"], True)
            self.assertIsNone(self.biscotto(h))
        # l'identità di Tailscale scritta da chi arriva da Internet non vale (tailscale serve la toglie, ma comunque)
        self.assertEqual(self.da_fuori("GET", "/api/extra/stato", extra={"Tailscale-User-Login": "x@y.z"})[0], 401)
        # un proxy davanti che non è tailscaled, o un indirizzo pubblico senza funnel: niente
        self.assertEqual(richiesta(self.s.porta, "GET", "/api/extra/stato", intest={"X-Forwarded-For": "8.8.8.8"})[0], 401)
        # per installare l'app: manifest e icone passano (qui non ci sono: 404, non 401)
        for p in ("/manifest.webmanifest", "/apple-touch-icon.png", "/icon-192.png", "/icon-maskable-512.png"):
            self.assertNotEqual(self.da_fuori("GET", p)[0], 401, p)
        for p in ("/api/musica/file/icon-1.png", "/api/extra/video/file/apple-touch-icon.png", "/x/manifest.webmanifest"):
            self.assertEqual(self.da_fuori("GET", p)[0], 401, p)

    def ha_password(self):
        if not os.path.exists(self.file):
            return False
        with open(self.file) as f:
            return "hash" in json.load(f)

    def dal_tailscale(self, metodo, percorso, corpo=None):
        h = {"X-Forwarded-For": "100.127.102.35", "Tailscale-User-Login": "samuele@example.com",
             "X-Forwarded-Proto": "https", "Host": "mac.esempio.ts.net:8448"}
        if corpo is not None:
            h.update({"Content-Type": "application/x-www-form-urlencoded", "Origin": "https://mac.esempio.ts.net:8448"})
        return richiesta(self.s.porta, metodo, percorso, corpo, intest=h)

    def test_3b_password_scelta_dalla_rete_tailscale(self):
        self.assertFalse(self.ha_password())
        # da Internet non si può scegliere
        st, h, b = self.da_fuori("POST", "/accesso", b"nuova=da+internet+123&ripeti=da+internet+123",
                                 extra={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(st, 403)
        self.assertFalse(self.ha_password())
        # dalla rete Tailscale: modulo per la password nuova, senza quella di adesso
        st, h, b = self.dal_tailscale("GET", "/accesso")
        t = b.decode("utf-8")
        self.assertEqual(st, 200)
        self.assertIn('autocomplete="new-password"', t)
        self.assertNotIn('name="password"', t)
        self.assertEqual(self.dal_tailscale("POST", "/accesso", b"nuova=corta&ripeti=corta")[0], 400)
        self.assertEqual(self.dal_tailscale("POST", "/accesso", b"nuova=abcdefgh1&ripeti=abcdefgh2")[0], 400)
        st, h, b = richiesta(self.s.porta, "POST", "/accesso", b"nuova=abcdefgh1&ripeti=abcdefgh1",
                             intest={"Content-Type": "application/x-www-form-urlencoded", "Origin": "https://altro.example"})
        self.assertEqual(st, 403)
        st, h, b = self.dal_tailscale("POST", "/accesso", b"nuova=scelta+su+iphone&ripeti=scelta+su+iphone")
        self.assertEqual(st, 200)
        self.assertIn("Password salvata", b.decode("utf-8"))
        self.assertIn("Secure", self.biscotto(h))
        self.assertEqual(os.stat(self.file).st_mode & 0o777, 0o600)
        self.assertEqual(self.entra("scelta su iphone", "203.0.113.30")[0], 303)
        # con la password già scelta: la pagina rimanda all'app; per cambiarla serve quella di adesso
        self.assertEqual(self.dal_tailscale("GET", "/accesso")[0], 303)
        st, h, b = self.dal_tailscale("GET", "/accesso?cambia")
        self.assertIn('name="password"', b.decode("utf-8"))
        self.assertEqual(self.dal_tailscale("POST", "/accesso", b"nuova=un+altra+ancora&ripeti=un+altra+ancora")[0], 401)
        self.assertEqual(self.dal_tailscale("POST", "/accesso", b"password=sbagliata&nuova=un+altra+ancora&ripeti=un+altra+ancora")[0], 401)
        st, h, b = self.dal_tailscale("POST", "/accesso", b"password=scelta+su+iphone&nuova=un+altra+ancora&ripeti=un+altra+ancora")
        self.assertEqual(st, 200)
        self.assertEqual(self.entra("scelta su iphone", "203.0.113.31")[0], 401)
        self.assertEqual(self.entra("un altra ancora", "203.0.113.31")[0], 303)

    def test_4_password(self):
        self.assertEqual(self.imposta("corta")[0], 1)
        cod, out = self.imposta("una password di prova")
        self.assertEqual(cod, 0, out)
        self.assertNotIn("una password di prova", out)
        self.assertEqual(os.stat(self.file).st_mode & 0o777, 0o600)
        st, h, b = self.da_fuori("GET", "/accesso")
        t = b.decode("utf-8")
        self.assertEqual(st, 200)
        self.assertIn('autocomplete="current-password"', t)
        self.assertIn('href="manifest.webmanifest"', t)
        self.assertEqual(h["Cache-Control"], "no-store")
        # sbagliata
        st, h, b = self.entra("sbagliata!!", "203.0.113.20")
        self.assertEqual(st, 401)
        self.assertIn("Password sbagliata", b.decode("utf-8"))
        self.assertIsNone(self.biscotto(h))
        # giusta: biscotto sicuro e ritorno all'app
        st, h, b = self.entra("una password di prova", "203.0.113.20")
        self.assertEqual((st, h["Location"]), (303, "./"))
        c = self.biscotto(h)
        for parte in ("HttpOnly", "Secure", "SameSite=Lax", "Path=/", "Max-Age=34560000"):
            self.assertIn(parte, c)
        v = c.split(";")[0].split("=", 1)[1]
        for metodo, p in (("GET", "/"), ("GET", "/api/extra/stato"), ("GET", "/x/prova.html")):
            self.assertEqual(self.da_fuori(metodo, p, biscotto=v)[0], 200, p)
        # con il biscotto la pagina della password rimanda all'app
        self.assertEqual(self.da_fuori("GET", "/accesso", biscotto=v)[0], 303)
        # da un altro sito: no
        st, h, b = self.da_fuori("POST", "/accesso", b"password=una+password+di+prova", ip="203.0.113.21",
                                 extra={"Content-Type": "application/x-www-form-urlencoded", "Origin": "https://altro.example"})
        self.assertEqual(st, 403)
        # biscotti falsi, ritoccati o scaduti
        with open(self.file) as f:
            seg = bytes.fromhex(json.load(f)["segreto"])
        import hashlib, hmac
        def firma(scad):
            corpo = "%d.%s" % (scad, "0123456789abcdef")
            return corpo + "." + hmac.new(seg, corpo.encode(), hashlib.sha256).hexdigest()
        self.assertEqual(self.da_fuori("GET", "/api/extra/stato", biscotto=firma(int(time.time()) + 3600))[0], 200)
        for falso in ("x", v[:-1] + ("0" if v[-1] != "0" else "1"), firma(int(time.time()) - 10),
                      firma(int(time.time()) + 900 * 86400), "1.2.3", "é.é.é"):
            self.assertEqual(self.da_fuori("GET", "/api/extra/stato", biscotto=falso)[0], 401, falso)
        # cambiando password il biscotto di prima non vale più
        self.assertEqual(self.imposta("un'altra password ancora")[0], 0)
        self.assertEqual(self.da_fuori("GET", "/api/extra/stato", biscotto=v)[0], 401)
        st, h, b = self.entra("un'altra password ancora", "203.0.113.22")
        self.assertEqual(st, 303)

    def test_5_troppi_tentativi(self):
        if self.imposta("password per i tentativi")[0]:
            self.fail("password non impostata")
        for i in range(5):
            self.assertEqual(self.entra("sbagliata %d" % i, "198.51.100.9")[0], 401)
        st, h, b = self.entra("password per i tentativi", "198.51.100.9")   # giusta, ma deve aspettare
        self.assertEqual(st, 429)
        self.assertGreater(int(h["Retry-After"]), 0)
        self.assertIsNone(self.biscotto(h))
        self.assertEqual(self.entra("password per i tentativi", "198.51.100.10")[0], 303)   # un altro indirizzo sì

    # ----- invitati: link d'invito, codice per l'app sulla Home, revoca -----

    ORIGINE = {"Content-Type": "application/x-www-form-urlencoded", "Origin": "https://mac.esempio.ts.net:10000"}
    IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15"

    def invitato(self, nome="Anna", ip="203.0.113.60"):
        """Persona invitata che entra dal suo link: (persona, biscotto, pagina dopo «Entra»)."""
        a = self.pers.crea(nome)
        codice, _ = self.pers.invito(a["id"])
        st, h, b = self.da_fuori("POST", "/accesso/invito/" + codice, b"", ip=ip,
                                 extra=dict(self.ORIGINE, **{"User-Agent": self.IPHONE}))
        self.assertEqual(st, 200, b[:300])
        v = (self.biscotto(h) or "").split(";")[0].split("=", 1)[-1]
        self.assertTrue(v.startswith("o."), self.biscotto(h))
        return a["id"], v, b.decode("utf-8")

    def test_6_invito_da_fuori(self):
        a = self.pers.crea("Anna")
        codice, _ = self.pers.invito(a["id"])
        for _ in range(3):   # WhatsApp e Messaggi aprono il link per l'anteprima: non deve consumarlo
            st, h, b = self.da_fuori("GET", "/accesso/invito/" + codice, extra={"Sec-Fetch-Mode": "navigate"})
            self.assertEqual(st, 200)
            self.assertIn("Ciao Anna", b.decode("utf-8"))
            self.assertIn('<form method="post"', b.decode("utf-8"))
            self.assertIsNone(self.biscotto(h))
        # Samuele che apre il link per provarlo non diventa Anna e non lo consuma
        st, h, b = self.dal_tailscale("GET", "/accesso/invito/" + codice)
        self.assertIn("già dentro", b.decode("utf-8"))
        self.assertEqual(self.dal_tailscale("POST", "/accesso/invito/" + codice, b"")[0], 409)
        self.assertEqual(self.da_fuori("POST", "/accesso/invito/" + codice, b"",
                                       extra={"Origin": "https://altro.example"})[0], 403)
        st, h, b = self.da_fuori("POST", "/accesso/invito/" + codice, b"", ip="203.0.113.61",
                                 extra=dict(self.ORIGINE, **{"User-Agent": self.IPHONE}))
        t = b.decode("utf-8")
        self.assertEqual(st, 200)
        self.assertIn("Sei dentro", t)
        self.assertRegex(t, r'<b class="codice">[ACDEFGHJKMNPQRTUVWXY3479]{6}</b>')
        v = self.biscotto(h).split(";")[0].split("=", 1)[1]
        self.assertTrue(v.startswith("o."))
        self.assertIn("HttpOnly", self.biscotto(h))
        # usato: da un altro telefono non vale più (dallo stesso, subito dopo, sì: vedi test_9g)
        st, h, b = self.da_fuori("POST", "/accesso/invito/" + codice, b"", ip="203.0.113.62", extra=self.ORIGINE)
        self.assertEqual(st, 410)
        self.assertIn("non vale più", b.decode("utf-8"))
        self.assertEqual(self.da_fuori("GET", "/api/musica/catalogo", biscotto=v)[0], 200)
        self.assertEqual(self.da_fuori("GET", "/accesso/invito/nonesiste12345678")[0], 410)
        self.assertEqual([y["tipo"] for x in self.pers.elenco()["persone"] if x["id"] == a["id"] for y in x["dispositivi"]],
                         ["iphone"])

    def test_7_collegamento_app_home(self):
        pid, v, t = self.invitato("Bruno", ip="203.0.113.70")
        codice = re.search(r'<b class="codice">(\w{6})</b>', t).group(1)
        corpo = ("codice=" + codice[:3].lower() + "-" + codice[3:]).encode("utf-8")
        st, h, b = self.da_fuori("POST", "/accesso", corpo, ip="203.0.113.71", extra=self.ORIGINE)
        self.assertEqual((st, h["Location"]), (303, "./"))
        v2 = self.biscotto(h).split(";")[0].split("=", 1)[1]
        self.assertTrue(v2.startswith("o.") and v2 != v)
        self.assertEqual(self.da_fuori("GET", "/api/musica/catalogo", biscotto=v2)[0], 200)
        st, h, b = self.da_fuori("POST", "/accesso", corpo, ip="203.0.113.72", extra=self.ORIGINE)
        self.assertEqual(st, 401)
        self.assertIn("Codice sbagliato o scaduto", b.decode("utf-8"))
        for i in range(5):
            self.assertEqual(self.da_fuori("POST", "/accesso", b"codice=ZZZZZ%d" % i, ip="198.51.100.77",
                                           extra=self.ORIGINE)[0], 401)
        st, h, b = self.da_fuori("POST", "/accesso", b"codice=" + self.pers.collegamento(pid)[0].encode(),
                                 ip="198.51.100.77", extra=self.ORIGINE)
        self.assertEqual(st, 429)
        self.assertGreater(int(h["Retry-After"]), 0)

    def test_8_password_cambiata_invitati_restano(self):
        self.assertEqual(self.imposta("password dell'ottava prova")[0], 0)
        st, h, b = self.entra("password dell'ottava prova", "203.0.113.80")
        p = self.biscotto(h).split(";")[0].split("=", 1)[1]
        pid, o, t = self.invitato("Carla", ip="203.0.113.81")
        self.assertEqual(self.imposta("password dell'ottava prova, nuova")[0], 0)
        self.assertEqual(self.da_fuori("GET", "/api/musica/catalogo", biscotto=p)[0], 401)
        self.assertEqual(self.da_fuori("GET", "/api/musica/catalogo", biscotto=o)[0], 200)

    def test_9_revoca_immediata(self):
        pid, v, t = self.invitato("Dario", ip="203.0.113.90")
        self.assertEqual(self.da_fuori("GET", "/api/musica/catalogo", biscotto=v)[0], 200)
        did = v.split(".")[2]
        self.assertTrue(self.pers.revoca_dispositivo(did))
        st, h, b = self.da_fuori("GET", "/api/musica/catalogo", biscotto=v)
        self.assertEqual((st, h["X-Atlante-Accesso"]), (401, "richiesto"))

    def test_9b_nome_nella_pagina(self):
        a = self.pers.crea("<script>x</script>")
        st, h, b = self.da_fuori("GET", "/accesso/invito/" + self.pers.invito(a["id"])[0])
        self.assertIn("&lt;script&gt;x&lt;/script&gt;", b.decode("utf-8"))
        self.assertNotIn("<script>x", b.decode("utf-8"))

    def test_9d_permessi_invitato(self):
        pid, v, t = self.invitato("Elena", ip="203.0.113.100")
        for f in ("index.html", "sw.js", "x/kit.js", "x/kit.css", "x/inviti.html", "x/benessere.html", "x/cerca.html",
                  "x/carica.html", "x/controllo.html", "x/contenuti.html"):
            with open(os.path.join(self.c["app"], f), "w", encoding="utf-8") as fh:
                fh.write("ciao")
        permesse = [("GET", "/"), ("GET", "/index.html"), ("GET", "/sw.js"), ("GET", "/x/kit.js"), ("GET", "/x/kit.css"),
                    ("GET", "/x/inviti.html"), ("GET", "/api/musica/catalogo"), ("HEAD", "/api/musica/file/1"),
                    ("GET", "/api/musica/copertina/x.jpg"), ("GET", "/api/musica/pezzo/1?da=0&durata=30"),
                    ("GET", "/api/extra/clip"), ("GET", "/api/extra/clip/file/abc.mp4"), ("GET", "/api/extra/clip/abc"),
                    ("GET", "/api/extra/dispositivi"), ("GET", "/api/extra/io"), ("GET", "/api/extra/inviti"),
                    ("POST", "/api/extra/inviti"), ("GET", "/musica/api/musica/catalogo"),
                    # Cerca e scarica (i brani vanno in «Da controllare») e Carica nella libreria
                    ("GET", "/x/cerca.html"), ("GET", "/x/carica.html"), ("GET", "/api/extra/musica/cerca?q=x"),
                    ("POST", "/api/extra/musica"), ("GET", "/api/extra/musica"), ("POST", "/api/extra/musica/x/annulla"),
                    ("GET", "/api/extra/caricamenti"), ("POST", "/api/extra/caricamenti"),
                    ("GET", "/api/extra/caricamenti/0123456789abcdef"), ("PUT", "/api/extra/caricamenti/0123456789abcdef"),
                    ("DELETE", "/api/extra/caricamenti/0123456789abcdef"),
                    ("GET", "/api/extra/caricamenti/0123456789abcdef/file")]
        for metodo, p in permesse:
            st, h, b = self.da_fuori(metodo, p, {} if metodo == "POST" else None, biscotto=v, extra=self.ORIGINE)
            self.assertNotEqual(st, 403, (metodo, p, b[:200]))
            self.assertNotEqual(st, 401, (metodo, p))
        vietate = [("GET", "/api/musica/aggiorna"), ("GET", "/api/extra/proxy?url=https://example.com/"),
                   ("GET", "/api/extra/video"), ("POST", "/api/extra/video"), ("GET", "/api/extra/film"),
                   ("GET", "/api/extra/film/file/x.mp4"), ("GET", "/api/extra/gestione/controllo"),
                   ("GET", "/api/extra/gestione/contenuti?q=x"), ("GET", "/x/controllo.html"), ("GET", "/x/contenuti.html"),
                   ("PUT", "/api/extra/gestione/contenuti/x/copertina"),
                   ("GET", "/api/extra/voci"), ("POST", "/api/extra/audiolibro"),
                   ("DELETE", "/api/extra/audiolibro/x"), ("POST", "/api/extra/ia"), ("GET", "/api/extra/dispositivo"),
                   ("GET", "/api/extra/stato"), ("GET", "/api/extra/gestione/persone"), ("GET", "/x/benessere.html"),
                   ("GET", "/x/"), ("GET", "/x/studio.html"), ("GET", "/musica/api/extra/stato")]
        for metodo, p in vietate:
            st, h, b = self.da_fuori(metodo, p, {} if metodo == "POST" else None, biscotto=v, extra=self.ORIGINE)
            self.assertEqual(st, 403, (metodo, p))
            self.assertEqual(json.loads(b.decode("utf-8"))["errore"], "Non disponibile per gli invitati", p)
        st, h, b = self.da_fuori("GET", "/api/extra/io", biscotto=v)
        self.assertEqual(json.loads(b.decode("utf-8")), {"ruolo": "invitato", "persona": {"id": pid, "nome": "Elena"},
                                                         "via": "internet"})
        st, h, b = richiesta(self.s.porta, "GET", "/api/extra/io")
        io = json.loads(b.decode("utf-8"))
        self.assertEqual((io["ruolo"], io["persona"], sorted(io["nuove"])),
                         ("proprietario", None, ["da_controllare", "film", "idee", "richieste"]))

    def test_9e_dispositivi_per_persona(self):
        pid, v, t = self.invitato("Franca", ip="203.0.113.110")

        def anna(metodo, p, corpo=None):
            st, h, b = self.da_fuori(metodo, p, corpo, biscotto=v, extra=self.ORIGINE)
            return st, (json.loads(b.decode("utf-8")) if b else None)

        def samuele(metodo, p, corpo=None):
            st, h, b = richiesta(self.s.porta, metodo, p, corpo)
            return st, (json.loads(b.decode("utf-8")) if b else None)

        self.assertEqual(samuele("POST", "/api/extra/dispositivi/samuele-iphone-1/stato", {"nome": "iPhone", "stato": {}})[0], 200)
        self.assertEqual(anna("POST", "/api/extra/dispositivi/franca-telefono-1/stato", {"nome": "Telefono", "stato": {}})[0], 200)
        self.assertEqual([x["id"] for x in anna("GET", "/api/extra/dispositivi")[1]["dispositivi"]], ["franca-telefono-1"])
        self.assertEqual([x["id"] for x in samuele("GET", "/api/extra/dispositivi")[1]["dispositivi"]], ["samuele-iphone-1"])
        # l'id di un dispositivo di Samuele non serve a niente a un'altra persona
        self.assertEqual(anna("POST", "/api/extra/dispositivi/samuele-iphone-1/comando", {"azione": "pause"})[0], 404)
        self.assertEqual(anna("POST", "/api/extra/dispositivi/samuele-iphone-1/stato", {"nome": "x", "stato": {}})[0], 404)
        self.assertEqual(anna("GET", "/api/extra/dispositivi/samuele-iphone-1/comandi?attesa=0")[0], 404)
        self.assertEqual(samuele("GET", "/api/extra/dispositivi/samuele-iphone-1/comandi?attesa=0")[1], {"comandi": []})
        # e tanti dispositivi finti di un'altra persona non fanno uscire quelli di Samuele
        for i in range(40):
            anna("POST", "/api/extra/dispositivi/franca-finto-%04d/stato" % i, {"nome": "F", "stato": {}})
        self.assertEqual([x["id"] for x in samuele("GET", "/api/extra/dispositivi")[1]["dispositivi"]], ["samuele-iphone-1"])
        anna("POST", "/api/extra/dispositivi/franca-telefono-1/stato", {"nome": "Telefono", "stato": {}})
        self.assertEqual(anna("POST", "/api/extra/dispositivi/franca-telefono-1/comando", {"azione": "pause"})[0], 200)
        self.assertEqual(anna("GET", "/api/extra/dispositivi/franca-telefono-1/comandi?attesa=0")[1],
                         {"comandi": [{"azione": "pause"}]})

    def test_9f_gestione_persone(self):
        def samuele(metodo, p, corpo=None, codice=200):
            return self.s.json(metodo, p, corpo, codice)

        def biscotto_da(link, ip):
            st, h, b = self.da_fuori("POST", urlsplit(link).path, b"", ip=ip, extra=dict(self.ORIGINE, **{"User-Agent": self.IPHONE}))
            self.assertEqual(st, 200, b[:200])
            return self.biscotto(h).split(";")[0].split("=", 1)[1]

        r = samuele("POST", "/api/extra/gestione/persone", {"nome": "Gina"})
        gina, link = r["persona"]["id"], r["invito"]["link"]
        self.assertTrue(link.startswith("https://mac.esempio.ts.net:10000/accesso/invito/"), link)
        v = biscotto_da(link, "203.0.113.120")
        el = {x["id"]: x for x in samuele("GET", "/api/extra/gestione/persone")["persone"]}
        self.assertEqual([d["tipo"] for d in el[gina]["dispositivi"]], ["iphone"])
        nuovo = samuele("POST", "/api/extra/gestione/persone/%s/invito" % gina)["invito"]["link"]
        self.assertNotEqual(nuovo, link)
        # le rotte della Gestione: solo Samuele, e solo dalla sua app
        for metodo, rotta in (("GET", "persone"), ("GET", "richieste"), ("POST", "persone"), ("DELETE", "persone/" + gina)):
            st, h, b = self.da_fuori(metodo, "/api/extra/gestione/" + rotta, {} if metodo == "POST" else None, biscotto=v,
                                     extra=self.ORIGINE)
            self.assertEqual(st, 403, rotta)
        st, h, b = richiesta(self.s.porta, "POST", "/api/extra/gestione/persone", {"nome": "X"},
                             intest={"Origin": "https://altro.example"})
        self.assertEqual(st, 403)
        # Gina chiede un invito per Marco; Samuele lo consente; Gina rivede il link da mandare
        st, h, b = self.da_fuori("POST", "/api/extra/inviti", {"nome": "Marco"}, biscotto=v, extra={"Origin": self.ORIGINE["Origin"]})
        self.assertEqual(st, 200, b[:200])
        rid = json.loads(b.decode("utf-8"))["richiesta"]["id"]
        rq = [x for x in samuele("GET", "/api/extra/gestione/richieste")["richieste"] if x["id"] == rid][0]
        self.assertEqual((rq["nome"], rq["da"], rq["da_nome"], rq["stato"]), ("Marco", gina, "Gina", "attesa"))
        marco = samuele("POST", "/api/extra/gestione/richieste/%s/consenti" % rid)
        st, h, b = self.da_fuori("GET", "/api/extra/inviti", biscotto=v)
        suoi = json.loads(b.decode("utf-8"))["inviti"]
        self.assertEqual(suoi[0]["link"], marco["invito"]["link"])
        self.assertNotIn("codice", suoi[0])
        el = {x["id"]: x for x in samuele("GET", "/api/extra/gestione/persone")["persone"]}
        self.assertEqual((el[marco["persona"]["id"]]["da"], el[marco["persona"]["id"]]["da_nome"]), (gina, "Gina"))
        rid2 = json.loads(self.da_fuori("POST", "/api/extra/inviti", {"nome": "Nora"}, biscotto=v,
                                        extra={"Origin": self.ORIGINE["Origin"]})[2].decode("utf-8"))["richiesta"]["id"]
        self.assertEqual(samuele("POST", "/api/extra/gestione/richieste/%s/rifiuta" % rid2), {"ok": True})
        samuele("POST", "/api/extra/gestione/richieste/%s/rifiuta" % rid2, codice=404)
        # revoche: un dispositivo, poi la persona
        did = v.split(".")[2]
        self.assertEqual(samuele("DELETE", "/api/extra/gestione/dispositivi/" + did), {"ok": True})
        self.assertEqual(self.da_fuori("GET", "/api/musica/catalogo", biscotto=v)[0], 401)
        self.assertEqual(samuele("DELETE", "/api/extra/gestione/persone/" + gina), {"ok": True})
        self.assertNotIn(gina, [x["id"] for x in samuele("GET", "/api/extra/gestione/persone")["persone"]])
        samuele("DELETE", "/api/extra/gestione/persone/" + gina, codice=404)
        # Samuele entrato con la password: i suoi dispositivi si vedono, ma non revoca quello che sta usando
        self.assertEqual(self.imposta("password della prova gestione")[0], 0)
        p = self.biscotto(self.entra("password della prova gestione", "203.0.113.121")[1]).split(";")[0].split("=", 1)[1]
        chiave = "p:" + p.split(".")[1]
        self.assertEqual(self.da_fuori("GET", "/api/extra/io", biscotto=p)[0], 200)
        self.assertIn(chiave, [x["id"] for x in samuele("GET", "/api/extra/gestione/persone")["tuoi"]])
        st, h, b = self.da_fuori("DELETE", "/api/extra/gestione/dispositivi/" + chiave, biscotto=p, extra={"Origin": self.ORIGINE["Origin"]})
        self.assertEqual(st, 409)
        self.assertIn("stai usando", json.loads(b.decode("utf-8"))["errore"])
        self.assertEqual(samuele("DELETE", "/api/extra/gestione/dispositivi/" + chiave), {"ok": True})
        self.assertEqual(self.da_fuori("GET", "/api/extra/io", biscotto=p)[0], 401)

    def test_9g_doppio_tocco_su_entra(self):
        a = self.pers.crea("Ivo")
        codice, _ = self.pers.invito(a["id"])
        intest = dict(self.ORIGINE, **{"User-Agent": self.IPHONE})
        v = []
        for _ in range(2):   # due tocchi su «Entra» dallo stesso telefono: dentro tutte e due le volte, stesso dispositivo
            st, h, b = self.da_fuori("POST", "/accesso/invito/" + codice, b"", ip="203.0.113.130", extra=intest)
            self.assertEqual(st, 200, b[:200])
            v.append(self.biscotto(h).split(";")[0].split("=", 1)[1])
        self.assertEqual(v[0].split(".")[2], v[1].split(".")[2])
        self.assertIn('onsubmit=', self.da_fuori("GET", "/accesso/invito/" + self.pers.invito(a["id"])[0])[2].decode("utf-8"))
        # più tardi, con la pagina rimandata (indietro e di nuovo «Entra»): chi è già dentro lo vede, e non conta come errore
        for _ in range(6):
            st, h, b = self.da_fuori("POST", "/accesso/invito/" + codice, b"", ip="203.0.113.131", biscotto=v[1], extra=intest)
            self.assertEqual(st, 200)
            self.assertIn("già dentro", b.decode("utf-8"))
        st, h, b = self.da_fuori("GET", "/accesso/invito/" + codice, biscotto=v[1])
        self.assertIn("già dentro", b.decode("utf-8"))
        nuovo = self.pers.invito(a["id"])[0]
        self.assertEqual(self.da_fuori("POST", "/accesso/invito/" + nuovo, b"", ip="203.0.113.131", extra=intest)[0], 200)

    def test_9h_gestione_riconosce_i_dispositivi(self):
        self.assertEqual(self.imposta("password della prova dispositivi")[0], 0)
        st, h, b = self.da_fuori("POST", "/accesso", b"nome=Atlante+Musica&password=password+della+prova+dispositivi",
                                 ip="203.0.113.140", extra=dict(self.ORIGINE, **{"User-Agent": self.IPHONE + " Version/18.0 Mobile/15E148 Safari/604.1"}))
        self.assertEqual(st, 303)
        p = self.biscotto(h).split(";")[0].split("=", 1)[1]
        chiave = "p:" + p.split(".")[1]
        st, h, b = self.da_fuori("GET", "/api/extra/gestione/persone", biscotto=p,
                                 extra={"User-Agent": self.IPHONE + " Version/18.0 Mobile/15E148 Safari/604.1"})
        tuoi = {x["id"]: x for x in json.loads(b.decode("utf-8"))["tuoi"]}
        self.assertEqual((tuoi[chiave]["via"], tuoi[chiave]["browser"], tuoi[chiave]["tipo"], tuoi[chiave].get("attuale")),
                         ("internet", "Safari", "iphone", True))
        self.assertTrue(all(not x.get("attuale") for k, x in tuoi.items() if k != chiave))

    def test_9i_numeri_nuovi_nella_gestione(self):
        pid, v, t = self.invitato("Lia", ip="203.0.113.150")
        st, h, b = self.da_fuori("POST", "/api/extra/inviti", {"nome": "Amico di Lia"}, biscotto=v, extra={"Origin": self.ORIGINE["Origin"]})
        self.assertEqual(st, 200)
        io = json.loads(richiesta(self.s.porta, "GET", "/api/extra/io")[2].decode("utf-8"))
        self.assertGreaterEqual(io["nuove"]["richieste"], 1)
        self.assertNotIn("nuove", json.loads(self.da_fuori("GET", "/api/extra/io", biscotto=v)[2].decode("utf-8")))

    def test_9j_pagine_non_incorniciabili(self):
        for p in ("/", "/index.html", "/x/inviti.html", "/accesso"):
            st, h, b = self.da_fuori("GET", p)
            self.assertEqual(h["X-Frame-Options"], "SAMEORIGIN", p)
            self.assertIn("frame-ancestors 'self'", h["Content-Security-Policy"], p)

    def test_9k_classifiche_anche_per_gli_invitati(self):
        pid, v, t = self.invitato("Olga", ip="203.0.113.160")
        st, h, b = self.da_fuori("GET", "/api/extra/classifiche", biscotto=v)
        self.assertEqual((st, json.loads(b.decode("utf-8"))), (200, {"classifiche": []}))   # qui le classifiche sono spente
        self.assertEqual(self.da_fuori("GET", "/api/extra/gestione/classifiche", biscotto=v)[0], 403)


class FintaRichiesta:
    """Quanto basta di BaseHTTPRequestHandler per controlla_accesso (indirizzi che dal Mac non si possono fingere)."""

    def __init__(self, ip, intest=None, percorso="/"):
        self.client_address, self.headers, self.path, self.command = (ip, 50000), dict(intest or {}), percorso, "GET"
        self.connection = None


class TestRuoli(unittest.TestCase):
    """Chi è una richiesta: Samuele (Mac, Tailscale, password), casa (rete di casa) o invitato."""

    @classmethod
    def setUpClass(cls):
        sys.dont_write_bytecode = True
        spec = importlib.util.spec_from_file_location("musica_libreria_ruoli", SERVER)
        cls.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.m)
        cls.base = tempfile.mkdtemp(prefix="atlante-ruoli-")
        cls.m.ACC = cls.m.Accesso(os.path.join(cls.base, "accesso.json"))
        cls.m.PERS = cls.m.Persone(os.path.join(cls.base, "persone.json"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.base, ignore_errors=True)

    def ruolo(self, ip, intest=None):
        h = FintaRichiesta(ip, intest)
        self.assertFalse(self.m.controlla_accesso(h))
        return h._chi["ruolo"], h._biscotto

    def test_casa_senza_biscotto(self):
        ruolo, biscotto = self.ruolo("192.168.1.20")
        self.assertEqual(ruolo, "casa")
        self.assertIsNone(biscotto)   # dalla rete di casa si ascolta, ma senza diventare Samuele

    def test_tailscale_vince_sul_biscotto_invitato(self):
        a = self.m.PERS.crea("Ospite")
        did = self.m.PERS.usa_invito(self.m.PERS.invito(a["id"])[0], "iphone")
        intest = {"X-Forwarded-For": "100.127.102.35", "Tailscale-User-Login": "samuele@example.com",
                  "Cookie": "am_accesso=" + self.m.PERS.biscotto(did)}
        ruolo, biscotto = self.ruolo("127.0.0.1", intest)
        self.assertEqual(ruolo, "proprietario")
        self.assertFalse(biscotto.split("=", 1)[1].startswith("o."))   # il telefono torna di Samuele

    def test_mac_e_tailscale(self):
        self.assertEqual(self.ruolo("127.0.0.1")[0], "proprietario")
        self.assertIsNotNone(self.ruolo("127.0.0.1")[1])
        ts = {"X-Forwarded-For": "100.127.102.35", "Tailscale-User-Login": "samuele@example.com"}
        self.assertEqual(self.ruolo("127.0.0.1", ts)[0], "proprietario")
        with unittest.mock.patch.object(self.m, "PROPRIETARI", {"samuele@example.com"}):
            self.assertEqual(self.ruolo("127.0.0.1", ts)[0], "proprietario")
            altro = dict(ts, **{"Tailscale-User-Login": "anna@example.com"})
            self.assertEqual(self.ruolo("127.0.0.1", altro), ("casa", None))


class TestPersone(unittest.TestCase):
    """Archivio delle persone invitate: inviti usabili una volta, codici per l'app sulla Home, revoche, richieste."""

    @classmethod
    def setUpClass(cls):
        sys.dont_write_bytecode = True
        spec = importlib.util.spec_from_file_location("musica_libreria_persone", SERVER)
        cls.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.m)

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="atlante-persone-")
        self.file = os.path.join(self.base, "cache", "persone.json")
        self.p = self.m.Persone(self.file)

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def piu_tardi(self, secondi):
        """time.time spostato in avanti (il modulo usa lo stesso time di queste prove)."""
        return unittest.mock.patch.object(self.m.time, "time", return_value=time.time() + secondi)

    def entrata(self, nome="Anna"):
        a = self.p.crea(nome)
        codice, _ = self.p.invito(a["id"])
        return a["id"], self.p.usa_invito(codice, "iphone")

    def test_invito_una_volta(self):
        a = self.p.crea("Anna")
        codice, scade = self.p.invito(a["id"])
        self.assertAlmostEqual(scade, time.time() + 7 * 86400, delta=5)
        atteso = {"persona": a["id"], "nome": "Anna"}
        self.assertEqual(self.p.leggi_invito(codice), atteso)   # un'anteprima del link non lo consuma
        self.assertEqual(self.p.leggi_invito(codice), atteso)
        did = self.p.usa_invito(codice, "iphone")
        self.assertRegex(did, r"^[0-9a-f]{16}$")
        self.assertIsNone(self.p.usa_invito(codice, "iphone"))
        self.assertIsNone(self.p.leggi_invito(codice))
        self.assertEqual(self.p.chi(self.p.biscotto(did))[0:2], (a["id"], did))
        with open(self.file, encoding="utf-8") as f:
            self.assertNotIn(codice, f.read())   # del codice resta solo l'impronta

    def test_invito_scaduto(self):
        a = self.p.crea("Anna")
        codice, _ = self.p.invito(a["id"])
        with self.piu_tardi(7 * 86400 + 1):
            self.assertIsNone(self.p.leggi_invito(codice))
            self.assertIsNone(self.p.usa_invito(codice, "iphone"))
        with self.assertRaises(self.m.ErroreExtra) as e:
            self.p.invito("nessuno")
        self.assertEqual(e.exception.codice, 404)

    def test_collegamento(self):
        pid, did = self.entrata()
        codice, scade = self.p.collegamento(pid)
        self.assertEqual(len(codice), 6)
        self.assertTrue(set(codice) <= set("ACDEFGHJKMNPQRTUVWXY3479"), codice)
        self.assertAlmostEqual(scade, time.time() + 15 * 60, delta=5)
        d2 = self.p.usa_collegamento(codice.lower()[:3] + "-" + codice.lower()[3:], "iphone")
        self.assertIsNotNone(d2)
        self.assertNotEqual(d2, did)
        self.assertEqual(self.p.chi(self.p.biscotto(d2))[0], pid)
        self.assertIsNone(self.p.usa_collegamento(codice, "iphone"))   # una volta sola
        codice, _ = self.p.collegamento(pid)
        with self.piu_tardi(15 * 60 + 1):
            self.assertIsNone(self.p.usa_collegamento(codice, "iphone"))

    def test_revoca(self):
        pid, d1 = self.entrata()
        d2 = self.p.usa_collegamento(self.p.collegamento(pid)[0], "mac")
        b1, b2 = self.p.biscotto(d1), self.p.biscotto(d2)
        self.assertTrue(self.p.revoca_dispositivo(d1))
        self.assertIsNone(self.p.chi(b1))
        self.assertEqual(self.p.chi(b2)[0:2], (pid, d2))
        falso = b2[:-1] + ("0" if b2[-1] != "0" else "1")
        self.assertIsNone(self.p.chi(falso))
        self.assertTrue(self.p.revoca_persona(pid))
        self.assertIsNone(self.p.chi(b2))
        self.assertIsNone(self.p.chi("o.1.2.3"))
        # i dispositivi di Samuele (biscotto della password): si vedono e si revocano con la loro chiave «p:…»
        self.p.visto("p:00112233aabbccdd", "mac")
        self.assertEqual([x["id"] for x in self.p.elenco()["tuoi"]], ["p:00112233aabbccdd"])
        self.assertFalse(self.p.revocato("p:00112233aabbccdd"))
        self.assertTrue(self.p.revoca_dispositivo("p:00112233aabbccdd"))
        self.assertTrue(self.p.revocato("p:00112233aabbccdd"))
        self.assertEqual(self.p.elenco()["tuoi"], [])

    def test_file_e_limiti(self):
        pid, did = self.entrata("  <b>Anna</b>  " * 10)
        self.assertEqual(os.stat(self.file).st_mode & 0o777, 0o600)
        nome = self.m.Persone(self.file).nome(pid)   # un altro processo legge le stesse persone
        self.assertTrue(nome.startswith("<b>Anna</b> <b>Anna</b>"), nome)
        self.assertLessEqual(len(nome), 40)
        with self.assertRaises(self.m.ErroreExtra) as e:
            self.p.crea("   ")
        self.assertEqual(e.exception.codice, 400)
        # al più 10 dispositivi per persona: l'undicesimo fa uscire quello visto meno di recente
        biscotti = [self.p.biscotto(did)]
        for i in range(10):
            with self.piu_tardi(i + 1):
                biscotti.append(self.p.biscotto(self.p.usa_collegamento(self.p.collegamento(pid)[0], "iphone")))
        self.assertIsNone(self.p.chi(biscotti[0]))
        self.assertTrue(all(self.p.chi(b) for b in biscotti[1:]))
        for i in range(199):
            self.p.crea("Persona %d" % i)
        with self.assertRaises(self.m.ErroreExtra) as e:
            self.p.crea("Una di troppo")
        self.assertEqual(e.exception.codice, 409)

    def test_richieste(self):
        anna, _ = self.entrata()
        r = self.p.richiedi(anna, "Marco")
        self.assertEqual((r["nome"], r["da"], r["stato"]), ("Marco", anna, "attesa"))
        self.assertNotIn("codice", self.p.richieste_di(anna)[0])
        self.assertEqual([x["id"] for x in self.p.richieste()], [r["id"]])
        marco, codice, scade = self.p.consenti(r["id"])
        self.assertEqual((marco["nome"], marco["da"]), ("Marco", anna))
        self.assertEqual(self.p.richieste_di(anna)[0]["codice"], codice)   # Anna rivede il link da mandare
        self.assertEqual([x.get("da") for x in self.p.elenco()["persone"] if x["id"] == marco["id"]], [anna])
        self.assertIsNotNone(self.p.usa_invito(codice, "iphone"))
        self.assertNotIn("codice", self.p.richieste_di(anna)[0])
        with open(self.file, encoding="utf-8") as f:
            self.assertNotIn(codice, f.read())
        attesa = [self.p.richiedi(anna, "Amico %d" % i) for i in range(5)]
        with self.assertRaises(self.m.ErroreExtra) as e:
            self.p.richiedi(anna, "Uno di troppo")
        self.assertEqual(e.exception.codice, 409)
        prima = len(self.p.elenco()["persone"])
        self.assertTrue(self.p.rifiuta(attesa[0]["id"]))
        self.assertEqual([x["stato"] for x in self.p.richieste_di(anna) if x["id"] == attesa[0]["id"]], ["rifiutata"])
        self.assertEqual(len(self.p.elenco()["persone"]), prima)

    def test_doppio_tocco(self):
        a = self.p.crea("Anna")
        codice, _ = self.p.invito(a["id"])
        d1 = self.p.usa_invito(codice, "iphone", "203.0.113.5")
        self.assertIsNotNone(d1)
        # il secondo tocco su «Entra» (o la pagina ricaricata) poco dopo, dallo stesso indirizzo: lo stesso dispositivo
        self.assertEqual(self.p.usa_invito(codice, "iphone", "203.0.113.5"), d1)
        self.assertIsNone(self.p.usa_invito(codice, "iphone", "198.51.100.1"))   # da un altro indirizzo no
        self.assertEqual(self.p.persona_di(d1), a["id"])
        with self.piu_tardi(121):
            self.assertIsNone(self.p.usa_invito(codice, "iphone", "203.0.113.5"))
        self.assertEqual(len([x for x in self.p.elenco()["persone"][0]["dispositivi"]]), 1)

    def test_file_rotto(self):
        pid, did = self.entrata()
        b = self.p.biscotto(did)
        with open(self.file, "w", encoding="utf-8") as f:
            f.write("{rotto")
        self.assertEqual(self.p.chi(b)[0], pid)   # in memoria resta quello di prima
        with self.assertRaises(self.m.ErroreExtra) as e:
            self.p.crea("Bruno")
        self.assertEqual(e.exception.codice, 503)
        with self.piu_tardi(self.m.VISTO_OGNI + 1):
            self.p.visto(did, "iphone")   # non solleva: l'accesso della richiesta non cade
        with open(self.file, encoding="utf-8") as f:
            self.assertEqual(f.read(), "{rotto")   # il file rotto non viene sovrascritto con un archivio vuoto
        nuovo = self.m.Persone(self.file)   # un processo che parte con il file rotto non lo svuota
        with self.assertRaises(self.m.ErroreExtra):
            nuovo.crea("Carla")
        with open(self.file, encoding="utf-8") as f:
            self.assertEqual(f.read(), "{rotto")

    def test_dispositivi_riconoscibili(self):
        pid, did = self.entrata()
        with self.piu_tardi(self.m.VISTO_OGNI + 1):
            self.p.visto("p:00112233aabbccdd", "iphone", "tailscale", "Safari")
            self.p.visto(did, "android", "internet", "Chrome")
        tuo = self.p.elenco()["tuoi"][0]
        self.assertEqual((tuo["id"], tuo["tipo"], tuo["via"], tuo["browser"]), ("p:00112233aabbccdd", "iphone", "tailscale", "Safari"))
        self.assertGreater(tuo["creato"], 0)
        d = self.p.elenco()["persone"][0]["dispositivi"][0]
        self.assertEqual((d["via"], d["browser"]), ("internet", "Chrome"))
        self.assertGreater(d["creato"], 0)
        # e restano dopo la scrittura su disco, letti da un altro processo
        self.assertEqual(self.m.Persone(self.file).elenco()["tuoi"][0]["browser"], "Safari")

    def test_tipo_da_agente(self):
        for ua, tipo in (("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15", "iphone"),
                         ("Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X)", "ipad"),
                         ("Mozilla/5.0 (Linux; Android 14; Pixel 8) Chrome/130.0", "android"),
                         ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Safari/605.1.15", "mac"),
                         ("Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "pc"), ("", "pc")):
            self.assertEqual(self.m.tipo_da_agente(ua), tipo, ua)
        for ua, nome in (("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1", "Safari"),
                         ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 CriOS/130.0 Mobile/15E148 Safari/604.1", "Chrome"),
                         ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 FxiOS/130.0 Mobile/15E148 Safari/605.1.15", "Firefox"),
                         ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36 Edg/130.0", "Edge"),
                         ("Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/130.0 Mobile Safari/537.36", "Chrome"),
                         ("Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0", "Firefox"), ("curl/8", "Altro")):
            self.assertEqual(self.m.browser_da_agente(ua), nome, ua)


# ----- idee per migliorare l'app (riquadro «Idee», Gestione → Idee, --idee-nuove) -----

ANNA = "a0a1a2a3a4a5a6a7"   # id di una persona invitata, come quelli di Persone


class TestIdee(unittest.TestCase):
    """Archivio delle idee: al più 2000 caratteri e 10 al giorno per persona, stati, nota di Samuele, file 0600."""

    @classmethod
    def setUpClass(cls):
        sys.dont_write_bytecode = True
        spec = importlib.util.spec_from_file_location("musica_libreria_idee", SERVER)
        cls.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.m)

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="atlante-idee-")
        self.file = os.path.join(self.base, "cache", "idee.json")
        self.i = self.m.Idee(self.file)

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def errore(self, codice, fn, *a, **k):
        with self.assertRaises(self.m.ErroreExtra) as e:
            fn(*a, **k)
        self.assertEqual(e.exception.codice, codice)
        return e.exception.testo

    def piu_tardi(self, secondi):
        return unittest.mock.patch.object(self.m.time, "time", return_value=time.time() + secondi)

    def test_manda_e_le_mie(self):
        a = self.i.manda(ANNA, "Anna", "  Vorrei vedere i testi delle canzoni\r\nmentre suonano.  ")
        self.assertEqual((a["testo"], a["stato"]), ("Vorrei vedere i testi delle canzoni\nmentre suonano.", "nuova"))
        self.assertRegex(a["id"], r"^[0-9a-f]{16}$")
        s = self.i.manda("p", "Samuele", "Un tasto per la radio")
        self.assertEqual([x["id"] for x in self.i.mie(ANNA)], [a["id"]])
        self.assertEqual([x["id"] for x in self.i.mie("p")], [s["id"]])
        self.assertNotIn("nota", self.i.mie(ANNA)[0])   # la nota è per Samuele e Claude, non per chi scrive
        el = self.i.elenco()
        self.assertEqual([x["id"] for x in el], [s["id"], a["id"]])   # la più recente per prima
        self.assertEqual((el[1]["da"], el[1]["nome"], el[1]["nota"]), (ANNA, "Anna", ""))
        self.assertEqual(self.i.nuove(), 2)
        self.assertEqual(os.stat(self.file).st_mode & 0o777, 0o600)
        self.assertEqual([x["id"] for x in self.m.Idee(self.file).elenco()], [s["id"], a["id"]])   # un altro processo

    def test_limiti(self):
        self.errore(400, self.i.manda, "p", "Samuele", "   \n  ")
        self.errore(400, self.i.manda, "p", "Samuele", None)
        self.errore(400, self.i.manda, "p", "Samuele", ["una", "lista"])
        self.assertIn("2000", self.errore(400, self.i.manda, "p", "Samuele", "x" * 2001))
        self.assertEqual(len(self.i.manda("p", "Samuele", "è" * 2000)["testo"]), 2000)
        self.assertEqual(self.i.restano("p"), 9)
        for k in range(9):
            self.i.manda("p", "Samuele", "Idea %d" % k)
        self.assertIn("domani", self.errore(429, self.i.manda, "p", "Samuele", "L'undicesima"))
        self.assertEqual((self.i.restano("p"), self.i.restano(ANNA)), (0, 10))
        self.i.manda(ANNA, "Anna", "Un'altra persona sì")
        with self.piu_tardi(2 * 86400):   # un altro giorno: si riparte
            self.assertEqual(self.i.restano("p"), 10)
            self.i.manda("p", "Samuele", "Un altro giorno")

    def test_testo_pulito(self):
        # caratteri di controllo e di direzione del testo: in un terminale (--idee-nuove) o in una pagina ingannano
        a = self.i.manda("p", "Samuele", "a\x1b[31mb\u202ec\x00d\te \U0001F468\u200d\U0001F469\u200d\U0001F467")
        self.assertEqual(a["testo"], "a[31mbcd\te \U0001F468\u200d\U0001F469\u200d\U0001F467")

    def test_stati_e_nota(self):
        a = self.i.manda(ANNA, "Anna", "Più playlist")
        self.assertEqual(self.i.cambia(a["id"], stato="proposta")["stato"], "proposta")
        self.assertEqual(self.i.nuove(), 0)
        x = self.i.cambia(a["id"], nota="  Fallo così: una scheda in più ")
        self.assertEqual((x["stato"], x["nota"], x["nome"]), ("proposta", "Fallo così: una scheda in più", "Anna"))
        for s in ("accettata", "scartata", "fatta", "nuova"):
            self.assertEqual(self.i.cambia(a["id"], stato=s)["stato"], s)
        self.errore(400, self.i.cambia, a["id"], stato="boh")
        self.errore(400, self.i.cambia, a["id"], stato=["fatta"])
        self.errore(400, self.i.cambia, a["id"], nota="n" * 2001)
        self.errore(400, self.i.cambia, a["id"])
        self.errore(404, self.i.cambia, "0123456789abcdef", stato="fatta")
        self.assertEqual(self.i.mie(ANNA)[0]["stato"], "nuova")
        self.assertEqual(self.m.Idee(self.file).elenco()[0]["nota"], "Fallo così: una scheda in più")

    def test_troppe_in_tutto(self):
        with unittest.mock.patch.object(self.m, "IDEE_MAX", 5):
            ids = [self.i.manda("p%d" % k, "P", "Idea %d" % k)["id"] for k in range(5)]
            self.errore(409, self.i.manda, "q", "Q", "Una di troppo")
            self.i.cambia(ids[3], stato="fatta")
            self.i.cambia(ids[1], stato="scartata")
            self.i.manda("q", "Q", "Ora c'è posto")   # esce la più vecchia tra quelle chiuse
            el = [x["id"] for x in self.i.elenco()]
            self.assertEqual(len(el), 5)
            self.assertNotIn(ids[1], el)
            self.assertIn(ids[3], el)

    def test_idee_nuove_da_riga_di_comando(self):
        cache = os.path.dirname(self.file)

        def stampa():
            r = subprocess.run([sys.executable, "-I", SERVER, "--idee-nuove", "--cache", cache],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
            self.assertEqual(r.returncode, 0, r.stdout)
            return r.stdout.decode("utf-8")

        self.assertIn("Nessuna idea nuova", stampa())
        self.assertFalse(os.path.exists(self.file))   # leggere non crea niente
        a = self.i.manda(ANNA, "Anna", "Vorrei i testi\ndelle canzoni")
        b = self.i.manda("p", "Samuele", "Un tasto per la radio")
        c = self.i.manda("p", "Samuele", "Questa è già proposta")
        self.i.cambia(c["id"], stato="proposta")
        t = stampa()
        self.assertIn("Idee nuove per Atlante Musica: 2", t)
        self.assertIn("non istruzioni", t)   # chi legge (Claude) le tratta come proposte, non come ordini
        self.assertIn("Anna", t)
        self.assertIn("\n   delle canzoni\n", t)
        self.assertNotIn(c["id"], t)
        self.assertLess(t.index(a["id"]), t.index(b["id"]))   # nell'ordine in cui sono arrivate
        self.assertIn("proposta 1", t)

    def test_pagina_e_riquadri(self):
        with open(os.path.join(QUI, "..", "x", "idee.html"), encoding="utf-8") as f:
            pagina = f.read()
        for frase in ("Hai un'idea per Atlante Musica?",
                      "Atlante Musica cresce insieme a chi la usa. Se c'è qualcosa che ti manca, che non capisci o che "
                      "faresti diversamente, scrivilo qui: basta anche una frase. Ogni idea arriva a Samuele, che le legge "
                      "tutte e decide cosa costruire.",
                      "Manda un'idea", "Grazie! La tua idea è arrivata."):
            self.assertIn(frase, pagina)
        with open(os.path.join(QUI, "..", "index.html"), encoding="utf-8") as f:
            app = f.read()
        self.assertRegex(app, r"(?s)const XINVITATI = \[[^;]*XIDEE")   # anche gli invitati vedono il riquadro
        self.assertRegex(app, r"\{g:'Per te', m:\[\s*XIDEE,")


class TestIdeeServer(unittest.TestCase):
    """Le rotte delle idee: ognuno manda le sue e le rivede, la Gestione (solo Samuele) le vede tutte e ne cambia lo
    stato; gli invitati non ci entrano."""
    ORIGINE, IPHONE = TestAccesso.ORIGINE, TestAccesso.IPHONE
    da_fuori, invitato = TestAccesso.da_fuori, TestAccesso.invitato
    biscotto = staticmethod(TestAccesso.biscotto)

    @classmethod
    def setUpClass(cls):
        cls.base = tempfile.mkdtemp(prefix="atlante-idee-server-")
        cls.c = prepara_cartelle(cls.base)
        with open(os.path.join(cls.c["app"], "x", "idee.html"), "w", encoding="utf-8") as f:
            f.write("<!doctype html><p>idee")
        cls.s = Server(cls.base, cls.c, ["--pubblico", "https://mac.esempio.ts.net:10000"]).avvia()
        sys.dont_write_bytecode = True
        spec = importlib.util.spec_from_file_location("musica_libreria_idee_server", SERVER)
        cls.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.m)
        cls.pers = cls.m.Persone(os.path.join(cls.c["cache"], "persone.json"))

    @classmethod
    def tearDownClass(cls):
        cls.s.ferma()
        shutil.rmtree(cls.base, ignore_errors=True)

    def test_idee(self):
        pid, v, _ = self.invitato("Gina", ip="203.0.113.130")

        def gina(metodo, p, corpo=None, origine=None):
            st, h, b = self.da_fuori(metodo, p, corpo, biscotto=v, extra={"Origin": origine or self.ORIGINE["Origin"]})
            return st, (json.loads(b.decode("utf-8")) if b.startswith(b"{") else b)

        def samuele(metodo, p, corpo=None, codice=200):
            return self.s.json(metodo, p, corpo, codice)

        self.assertEqual(gina("GET", "/x/idee.html")[0], 200)
        st, j = gina("GET", "/api/extra/idee")
        self.assertEqual((st, j["idee"], j["restano"], j["max_caratteri"]), (200, [], 10, 2000))
        st, j = gina("POST", "/api/extra/idee", {"testo": "<b>Più</b> playlist <script>x</script>"})
        self.assertEqual(st, 200, j)
        ig = j["idea"]
        self.assertEqual((ig["testo"], ig["stato"], j["restano"]), ("<b>Più</b> playlist <script>x</script>", "nuova", 9))
        self.assertEqual(gina("POST", "/api/extra/idee", {"testo": "x" * 2001})[0], 400)
        self.assertEqual(gina("POST", "/api/extra/idee", {"testo": "Da un altro sito"}, "https://altro.example")[0], 403)
        mia = samuele("POST", "/api/extra/idee", {"testo": "Un tasto per la radio"})["idea"]
        self.assertEqual([x["id"] for x in gina("GET", "/api/extra/idee")[1]["idee"]], [ig["id"]])   # solo le sue
        self.assertEqual([x["id"] for x in samuele("GET", "/api/extra/idee")["idee"]], [mia["id"]])
        # Gestione → Idee: tutte, con chi le ha scritte
        el = samuele("GET", "/api/extra/gestione/idee")
        self.assertEqual([(x["id"], x["nome"], x["da"]) for x in el["idee"]], [(mia["id"], "Samuele", "p"), (ig["id"], "Gina", pid)])
        self.assertEqual(el["nuove"], 2)
        self.assertEqual(samuele("GET", "/api/extra/io")["nuove"]["idee"], 2)   # il numero sul riquadro della Gestione
        self.assertNotIn("nuove", gina("GET", "/api/extra/io")[1])
        x = samuele("POST", "/api/extra/gestione/idee/" + ig["id"], {"stato": "accettata", "nota": "Fallo così"})["idea"]
        self.assertEqual((x["stato"], x["nota"]), ("accettata", "Fallo così"))
        self.assertEqual(samuele("GET", "/api/extra/io")["nuove"]["idee"], 1)
        samuele("POST", "/api/extra/gestione/idee/" + ig["id"], {"stato": "boh"}, codice=400)
        samuele("POST", "/api/extra/gestione/idee/0123456789abcdef", {"stato": "fatta"}, codice=404)
        samuele("POST", "/api/extra/gestione/idee/../persone", {"stato": "fatta"}, codice=404)
        mie = gina("GET", "/api/extra/idee")[1]["idee"]
        self.assertEqual(mie[0]["stato"], "accettata")
        self.assertNotIn("nota", mie[0])
        # la Gestione delle idee è solo di Samuele, e solo dalla sua app
        for metodo, p in (("GET", "gestione/idee"), ("POST", "gestione/idee/" + ig["id"])):
            st, j = gina(metodo, "/api/extra/" + p, {"stato": "fatta"} if metodo == "POST" else None)
            self.assertEqual((st, j["errore"]), (403, "Non disponibile per gli invitati"), p)
        st, h, b = richiesta(self.s.porta, "POST", "/api/extra/gestione/idee/" + ig["id"], {"stato": "fatta"},
                             intest={"Origin": "https://altro.example"})
        self.assertEqual(st, 403)
        self.assertEqual(samuele("GET", "/api/extra/gestione/idee")["idee"][1]["stato"], "accettata")
        # 10 al giorno per persona
        for k in range(9):
            self.assertEqual(gina("POST", "/api/extra/idee", {"testo": "Idea %d" % k})[0], 200)
        st, j = gina("POST", "/api/extra/idee", {"testo": "L'undicesima"})
        self.assertEqual(st, 429)
        self.assertIn("domani", j["errore"])
        self.assertEqual(gina("GET", "/api/extra/idee")[1]["restano"], 0)
        # una persona revocata: le sue idee restano, con il suo nome
        self.assertTrue(self.pers.revoca_persona(pid))
        self.assertIn("Gina", [x["nome"] for x in samuele("GET", "/api/extra/gestione/idee")["idee"]])
        with open(os.path.join(self.c["cache"], "idee.json"), encoding="utf-8") as f:
            self.assertEqual(os.stat(f.name).st_mode & 0o777, 0o600)
            self.assertIn("Un tasto per la radio", f.read())


# ----- classifiche e playlist: brani nuovi da soli (pagine e risposte finte salvate qui: nessuna richiesta a Internet) -----

# i server delle prove non leggono mai le classifiche vere, anche se queste variabili ci sono (sul Mac mini nel servizio)
for _v in ("ATLANTE_CLASSIFICHE", "ATLANTE_CLASSIFICHE_FINTE"):
    os.environ.pop(_v, None)

PAGINA_KWORB = """<!DOCTYPE html><html><body><table id="spotifyweekly" class="sortable">
<thead><tr><th class="np">Pos</th><th class="np">P+</th><th class="mp">Artist and Title</th><th>Wks</th></tr></thead><tbody>
<tr><td class="np">1</td><td class="np">=</td><td class="text mp"><div><a href="../artist/0Y5tJX1MQlPlqiwlOH1tJY.html">Travis Scott</a> - <a href="../track/42VsgItocQwOQC3XWZ8JNA.html">FE!N</a> (w/ <a href="../artist/699OTQXzgjhIYAHMy9RyPD.html">Playboi Carti</a>)</div></td><td>60</td></tr>
<tr><td class="np">2</td><td class="np">+1</td><td class="text mp"><div><a href="../artist/1.html">Sfera Ebbasta</a>, <a href="../artist/2.html">Shiva</a> - <a href="../track/2.html">Mamma mia</a></div></td><td>3</td></tr>
<tr><td class="np">3</td><td class="np">-1</td><td class="text mp"><div><a href="../artist/3.html">ROS&Eacute;</a> &amp; <a href="../artist/4.html">Bruno Mars</a> - <a href="../track/3.html">APT.</a></div></td><td>3</td></tr>
</tbody></table></body></html>"""

PAGINA_BILLBOARD = """<html><body><div class="chart-results-list">
<div class="o-chart-results-list-row-container"><ul class="o-chart-results-list-row // lrv-a-unstyle-list">
<li><span class="c-label  a-font-primary-bold-l u-font-size-32@tablet">1</span></li>
<li class="lrv-u-width-100p"><ul><li class="o-chart-results-list__item">
<h3 id="title-of-a-story" class="c-title  a-no-trucate a-font-primary-bold-s">
	Die With A Smile
</h3>
<span class="c-label  a-no-trucate a-font-primary-s lrv-u-font-size-14@mobile-max">
	Lady Gaga &amp; Bruno Mars
</span></li></ul></li></ul></div>
<div class="o-chart-results-list-row-container"><ul class="o-chart-results-list-row">
<li><span class="c-label a-font-primary-bold-l">2</span><span class="c-label">NEW</span></li>
<li><h3 id="title-of-a-story" class="c-title a-no-trucate">luther</h3>
<span class="c-label a-no-trucate">Kendrick Lamar &amp; SZA</span></li></ul></div>
</div>
<h3 id="title-of-a-story" class="c-title">Gains in Weekly Performance</h3><span class="c-label a-no-trucate">Articolo</span>
</body></html>"""

PAGINA_UK = """<html><body>
<div class="chart-item"><div class="chart-item-content"><div class="description block">
<p><a class="chart-name font-bold inline-block" href="/songs/sabrina-carpenter-espresso/"><span class="badge new">New</span><span>ESPRESSO</span></a></p>
<a class="chart-artist text-lg inline-block" href="/artist/sabrina-carpenter/"><span>SABRINA CARPENTER</span></a></div></div></div>
<div class="chart-item chart-ad"><div class="ad">Pubblicità</div></div>
<div class="chart-item"><div class="chart-item-content"><p><a class="chart-name font-bold" href="/songs/dua-lipa-new-rules/"><span>NEW RULES</span></a></p>
<a class="chart-artist" href="/artist/dua-lipa/"><span>DUA LIPA</span></a></div></div>
</body></html>"""

PAGINA_FIMI = """<html><body><nav><a href="/album">Album</a> <a href="/singoli">Singoli</a></nav>
<h2>Top Album</h2>
<div class="chart-item"><div class="title">ALBUM CHE NON E UN BRANO</div><div class="artist">Qualcuno</div></div>
<h2>Top Singoli</h2>
""" + "".join('<div class="chart-item"><div class="position">%d</div><div class="title">%s</div><div class="artist">%s</div>'
              '<div class="label">Etichetta</div></div>\n' % (i + 1, t, a) for i, (t, a) in enumerate((
                  ("BALORDA NOSTALGIA", "OLLY"), ("TU CON CHI FAI L'AMORE", "THE KOLORS"), ("CUORICINI", "COMA_COSE"),
                  ("FUORILEGGE", "ROSE VILLAIN"), ("NUN TE SCETÀ", "GEOLIER")))) + """
<h2>Top Vinili</h2><div class="chart-item"><div class="title">VINILE</div><div class="artist">Altro</div></div>
</body></html>"""

PAGINA_TABELLA = """<html><body><table><tr><th>Pos.</th><th>Titolo</th><th>Artista</th></tr>
""" + "".join("<tr><td>%d</td><td><a href='#'>%s</a></td><td>%s</td></tr>" % (i + 1, t, a) for i, (t, a) in enumerate((
    ("Brano uno", "Artista uno"), ("Brano due", "Artista due"), ("Brano tre", "Artista tre"), ("Brano quattro", "Artista quattro"),
    ("Brano cinque", "Artista cinque")))) + "</table></body></html>"

RSS_APPLE = json.dumps({"feed": {"title": "Top Songs", "author": {"name": "Apple"}, "results": [
    {"artistName": "Olly", "id": "1", "name": "Balorda nostalgia", "kind": "songs"},
    {"artistName": "Kendrick Lamar & SZA", "id": "2", "name": "luther", "kind": "songs"}]}})

PAGINA_APPLE = """<html><head><script type="application/ld+json">{"@type":"MusicPlaylist","name":"Top 100: Global"}</script></head>
<body><script type="application/json" id="serialized-server-data">[{"data":{"sections":[{"items":[
{"title":"Die With A Smile","artistName":"Lady Gaga & Bruno Mars","contentDescriptor":{"kind":"song"}},
{"title":"APT.","artistName":"ROSÉ & Bruno Mars"}]}]}}]</script></body></html>"""

PAGINA_SPOTIFY = """<html><body><script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"state":{"data":
{"entity":{"name":"RapCaviar","subtitle":"Spotify","trackList":[{"uri":"spotify:track:1","title":"Not Like Us","subtitle":"Kendrick Lamar",
"isExplicit":true},{"uri":"spotify:track:2","title":"Timeless (with Playboi Carti)","subtitle":"The Weeknd, Playboi Carti"}]}}}}}}</script></body></html>"""

PAGINA_CAMBIATA = "<html><body><h1>Manutenzione</h1><p>Torniamo presto.</p></body></html>"


def modulo_server(nome):
    """Il server caricato come modulo nelle prove (senza main(): nessun server in ascolto)."""
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location(nome, SERVER)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def risultato(vid, titolo, artista, album="", explicit=False, durata=200):
    """Un risultato della ricerca di YouTube Music com'è dopo _brano_ytm (prima di _ordina)."""
    return {"id": vid, "titolo": titolo, "artista": artista, "album": album, "durata": durata, "copertina": "",
            "explicit": explicit}


def ricerca_playlist(voci):
    """Risposta finta di youtubei/v1/search con playlist: (id, nome, sotto)."""
    return {"contents": {"tabbedSearchResultsRenderer": {"tabs": [{"tabRenderer": {"content": {"sectionListRenderer": {
        "contents": [{"musicShelfRenderer": {"contents": [{"musicResponsiveListItemRenderer": {
            "navigationEndpoint": {"browseEndpoint": {"browseId": vid}},
            "flexColumns": [
                {"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": [{"text": nome}]}}},
                {"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": [{"text": sotto}]}}}]}}
            for vid, nome, sotto in voci],
            "continuations": [{"nextContinuationData": {"continuation": "PAGINA2"}}]}}]}}}}]}}}


class Orologio:
    """Il tempo delle prove: parte da adesso e va avanti solo quando lo si sposta."""

    def __init__(self):
        self.t = time.time()

    def __call__(self):
        return self.t


class TestClassificheLettori(unittest.TestCase):
    """Un lettore per fonte, con pagine finte; una pagina cambiata dà un elenco vuoto (la fonte si salta)."""

    @classmethod
    def setUpClass(cls):
        cls.m = modulo_server("musica_libreria_cl_lettori")

    def test_kworb(self):
        self.assertEqual(self.m._cl_kworb(PAGINA_KWORB), [("Travis Scott", "FE!N"), ("Sfera Ebbasta, Shiva", "Mamma mia"),
                                                          ("ROSÉ, Bruno Mars", "APT.")])

    def test_billboard(self):
        self.assertEqual(self.m._cl_billboard(PAGINA_BILLBOARD), [("Lady Gaga & Bruno Mars", "Die With A Smile"),
                                                                  ("Kendrick Lamar & SZA", "luther")])

    def test_uk(self):
        # il segno «New» non entra nel titolo, «NEW RULES» resta intero
        self.assertEqual(self.m._cl_uk(PAGINA_UK), [("SABRINA CARPENTER", "ESPRESSO"), ("DUA LIPA", "NEW RULES")])

    def test_fimi(self):
        r = self.m._cl_fimi(PAGINA_FIMI)
        self.assertEqual(r[0], ("OLLY", "BALORDA NOSTALGIA"))
        self.assertEqual(len(r), 5, r)   # solo i singoli: né l'album prima né il vinile dopo
        self.assertEqual(r[4], ("GEOLIER", "NUN TE SCETÀ"))
        self.assertEqual(self.m._cl_fimi(PAGINA_TABELLA)[1], ("Artista due", "Brano due"))   # anche una tabella

    def test_apple(self):
        self.assertEqual(self.m._cl_apple(RSS_APPLE), [("Olly", "Balorda nostalgia"), ("Kendrick Lamar & SZA", "luther")])
        self.assertEqual(self.m._cl_apple(PAGINA_APPLE), [("Lady Gaga & Bruno Mars", "Die With A Smile"),
                                                          ("ROSÉ & Bruno Mars", "APT.")])

    def test_spotify(self):
        self.assertEqual(self.m._cl_spotify(PAGINA_SPOTIFY), [("Kendrick Lamar", "Not Like Us"),
                                                              ("The Weeknd, Playboi Carti", "Timeless (with Playboi Carti)")])

    def test_pagina_cambiata(self):
        m = self.m
        for lettore in (m._cl_kworb, m._cl_billboard, m._cl_uk, m._cl_fimi, m._cl_apple, m._cl_spotify):
            self.assertEqual(lettore(PAGINA_CAMBIATA), [], lettore.__name__)
            self.assertEqual(lettore(""), [], lettore.__name__)
        self.assertEqual(m._cl_apple("{non json"), [])

    def test_leggi_pagine_finte(self):
        m = self.m
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FintoWeb)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            with unittest.mock.patch.dict(os.environ, {"ATLANTE_CLASSIFICHE_FINTE": "http://127.0.0.1:%d" % srv.server_address[1]}):
                f = [x for x in m.CL_FONTI if x["id"] == "spotify-it"][0]
                self.assertEqual(m._cl_leggi(f), [("Eminem", "Without Me")])
                with self.assertRaises(RuntimeError) as e:
                    m._cl_leggi([x for x in m.CL_FONTI if x["id"] == "billboard"][0])
                self.assertIn("404", str(e.exception))
        finally:
            srv.shutdown()
            srv.server_close()

    def test_youtube_music(self):
        m = self.m
        classifiche = {"contents": {"singleColumnBrowseResultsRenderer": {"tabs": [{"tabRenderer": {"content": {
            "sectionListRenderer": {"contents": [{"musicCarouselShelfRenderer": {
                "header": {"musicCarouselShelfBasicHeaderRenderer": {"title": {"runs": [{"text": "Classifiche video"}]}}},
                "contents": [
                    {"musicTwoRowItemRenderer": {"title": {"runs": [{"text": "Top 100 video musicali Italia"}]},
                                                 "navigationEndpoint": {"browseEndpoint": {"browseId": "VLPLvideoIT"}}}},
                    {"musicTwoRowItemRenderer": {"title": {"runs": [{"text": "Top 100 brani Italia"}]},
                                                 "navigationEndpoint": {"browseEndpoint": {"browseId": "VLPLbraniIT"}}}},
                    {"musicTwoRowItemRenderer": {"title": {"runs": [{"text": "Rap italiano: novità"}]},
                                                 "navigationEndpoint": {"browseEndpoint": {"browseId": "VLPLaltro"}}}}]}}]}}}}]}}}
        pagina1 = {"contents": {"musicPlaylistShelfRenderer": {"contents": [
            _brano_ytm("Brano000001", "Balorda nostalgia", ["Olly"], "Balorda nostalgia", "3:10", False),
            _brano_ytm("Brano000002", "Cuoricini", ["Coma_Cose"], "Cuoricini", "2:58", False),
            {"continuationItemRenderer": {"continuationEndpoint": {"continuationCommand": {"token": "SEGUE"}}}}]}}}
        pagina2 = {"onResponseReceivedActions": [{"appendContinuationItemsAction": {"continuationItems": [
            _brano_ytm("Brano000003", "Fuorilegge", ["Rose Villain"], "Fuorilegge", "3:01", True)]}}]}
        chieste = []

        def ytm(percorso, corpo, extra=""):
            chieste.append(corpo.get("browseId") or corpo.get("continuation"))
            if corpo.get("browseId") == "FEmusic_charts":
                self.assertEqual(corpo["formData"], {"selectedValues": ["IT"]})
                return classifiche
            return {"VLPLbraniIT": pagina1, "SEGUE": pagina2}.get(chieste[-1], {"contents": {}})
        with unittest.mock.patch.object(m, "_cl_ytm", ytm):
            r = m._cl_ytm_classifica("IT")
        # prima la playlist dei brani (poi quella dei video); le altre playlist della pagina no
        self.assertEqual(r, [("Olly", "Balorda nostalgia"), ("Coma_Cose", "Cuoricini"), ("Rose Villain", "Fuorilegge")])
        self.assertEqual(chieste[:3], ["FEmusic_charts", "VLPLbraniIT", "SEGUE"])
        self.assertNotIn("VLPLaltro", chieste)

    def test_scopri_playlist(self):
        m = self.m
        risposta = ricerca_playlist([("VLRDCLAK5uy_a", "Pop italiano: le hit", "Playlist • YouTube Music • 250.000 visualizzazioni"),
                                     ("UCartista", "Un artista", "Artista"),
                                     ("VLPLcommunity", "Hit pop 2026", "Playlist • Mario • 1,2 Mln visualizzazioni"),
                                     ("VLPLpiccola", "Pop nuovo", "Playlist • Anna • 12K views")])
        corpi = []

        def ytm(percorso, corpo, extra=""):
            corpi.append((percorso, corpo, extra))
            return risposta
        with unittest.mock.patch.object(m, "_cl_ytm", ytm):
            trovate, seguito = m._cl_scopri("pop italiano", m.CL_FILTRI_PLAYLIST[0], "")
            m._cl_scopri("pop italiano", None, "PAGINA2")
        self.assertEqual([p["id"] for p in trovate], ["VLPLcommunity", "VLRDCLAK5uy_a", "VLPLpiccola"])   # dalla più vista
        self.assertEqual([p["viste"] for p in trovate], [1200000, 250000, 12000])
        self.assertEqual(seguito, "PAGINA2")
        self.assertEqual((corpi[0][0], corpi[0][1]["query"], corpi[0][1]["params"]), ("search", "pop italiano", m.CL_FILTRI_PLAYLIST[0]))
        self.assertEqual(corpi[1][1], {"continuation": "PAGINA2"})
        self.assertIn("ctoken=PAGINA2", corpi[1][2])

    def test_viste(self):
        for testo, n in (("1,2 Mln visualizzazioni", 1200000), ("350.000 visualizzazioni", 350000), ("1.5M views", 1500000),
                         ("12K views", 12000), ("2 Mld di visualizzazioni", 2000000000), ("Playlist • YouTube Music", 0)):
            self.assertEqual(self.m._cl_viste(testo), n, testo)


class TestClassificheScelta(unittest.TestCase):
    """Quale risultato di YouTube Music si scarica per un brano di una classifica, e quando un brano c'è già."""

    @classmethod
    def setUpClass(cls):
        cls.m = modulo_server("musica_libreria_cl_scelta")

    def scegli(self, artista, titolo, risultati):
        return self.m._cl_scegli(artista, titolo, self.m._ordina([dict(r) for r in risultati]))

    def test_sempre_la_versione_explicit(self):
        r, motivo = self.scegli("Eminem", "Without Me", [risultato("NoExplicit1", "Without Me", "Eminem"),
                                                         risultato("Explicit001", "Without Me", "Eminem", explicit=True)])
        self.assertEqual((r["id"], motivo), ("Explicit001", ""))
        # c'è solo la versione censurata (clean, edited, radio edit): niente, mai la censurata
        for t in ("Without Me (Clean)", "Without Me (Radio Edit)", "Without Me - Edited", "Without Me [Censored]"):
            r, motivo = self.scegli("Eminem", "Without Me", [risultato("Pulita00001", t, "Eminem")])
            self.assertEqual((r, motivo), (None, "censurata"), t)
        # un brano senza versione explicit (nessuna parolaccia): va bene quella che c'è
        r, motivo = self.scegli("Olly", "Balorda nostalgia", [risultato("Olly0000001", "Balorda nostalgia", "Olly")])
        self.assertEqual(r["id"], "Olly0000001")
        # «Clean» di Taylor Swift è il titolo, non una versione censurata
        self.assertEqual(self.scegli("Taylor Swift", "Clean", [risultato("TaylorClean", "Clean", "Taylor Swift")])[0]["id"],
                         "TaylorClean")

    def test_niente_tributi_cover_karaoke(self):
        falsi = [risultato("Tributo0001", "Lose Yourself", "Eminem Tribute Band"),
                 risultato("Karaoke0001", "Lose Yourself (Karaoke Version)", "Karaoke Hits"),
                 risultato("Cover000001", "Lose Yourself", "Piano Covers", album="Rap Hits on Piano"),
                 risultato("Stile000001", "Lose Yourself (In the Style of Eminem)", "Eminem"),
                 risultato("Altro000001", "Lose Yourself", "Altro Cantante")]
        self.assertEqual(self.scegli("Eminem", "Lose Yourself", falsi), (None, "non trovato"))
        r, _ = self.scegli("Eminem", "Lose Yourself", falsi + [risultato("Vero0000001", "Lose Yourself", "Eminem", explicit=True)])
        self.assertEqual(r["id"], "Vero0000001")

    def test_altre_versioni(self):
        versioni = [risultato("Remix000001", "Houdini (Remix)", "Dua Lipa"), risultato("Live0000001", "Houdini (Live)", "Dua Lipa"),
                    risultato("Veloce00001", "Houdini (Sped Up)", "Dua Lipa"), risultato("Acustica001", "Houdini - Acoustic", "Dua Lipa"),
                    risultato("Strument001", "Houdini (Instrumental)", "Dua Lipa")]
        self.assertEqual(self.scegli("Dua Lipa", "Houdini", versioni)[1], "non trovato")
        self.assertEqual(self.scegli("Dua Lipa", "Houdini", versioni + [risultato("Houdini0001", "Houdini", "Dua Lipa")])[0]["id"],
                         "Houdini0001")
        # se è la classifica a volere il remix, il remix va bene
        self.assertEqual(self.scegli("Dua Lipa", "Houdini (Remix)", versioni)[0]["id"], "Remix000001")

    def test_artista_principale_tra_gli_esecutori(self):
        self.assertEqual(self.scegli("Shiva", "Mamma mia", [risultato("Mamma000001", "Mamma mia", "Sfera Ebbasta e Shiva")])[0]["id"],
                         "Mamma000001")
        self.assertEqual(self.scegli("Travis Scott, Playboi Carti", "FE!N",
                                     [risultato("Fein0000001", "FE!N (feat. Playboi Carti)", "Travis Scott", explicit=True)])[0]["id"],
                         "Fein0000001")
        self.assertEqual(self.scegli("ROSÉ & Bruno Mars", "APT.", [risultato("Apt00000001", "APT.", "ROSÉ & Bruno Mars")])[0]["id"],
                         "Apt00000001")
        self.assertEqual(self.scegli("Adele", "Hello", [risultato("Hello000001", "Hello", "Lionel Richie")]), (None, "non trovato"))
        # durate assurde (un'ora di «mix», tre secondi)
        self.assertEqual(self.scegli("Adele", "Hello", [risultato("Hello000002", "Hello", "Adele", durata=3600)])[0], None)

    def test_stesso_brano_scritto_in_altro_modo(self):
        m = self.m
        for a, b in (("FE!N", "FE!N (feat. Playboi Carti)"), ("APT.", "Apt"), ("Perché", "perche"), ("N.Y. State", "NY State"),
                     ("Shake That", "Shake That [Explicit]"), ("ESPRESSO", "Espresso"), ("Rock & Roll", "Rock and Roll")):
            self.assertEqual(m._cl_titolo(a), m._cl_titolo(b), (a, b))
            self.assertEqual(m._cl_chiave("Eminem", a), m._cl_chiave("eminem feat. Nate Dogg", b))
        self.assertNotEqual(m._cl_titolo("사랑"), m._cl_titolo("노래"))   # titoli in altre scritture restano diversi
        self.assertTrue(m._cl_titolo("사랑"))


class TestClassificheLavoro(unittest.TestCase):
    """Il lavoro in sottofondo, passo per passo, con fonti, ricerca e scaricamento finti e il tempo spostato a mano."""

    @classmethod
    def setUpClass(cls):
        cls.m = modulo_server("musica_libreria_cl_lavoro")
        cls.m.CL_PAUSA = 0

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="atlante-classifiche-")
        self.ora = Orologio()
        self.pagine = {}          # id della fonte -> brani (o un'eccezione da sollevare)
        self.predefinito = []     # brani delle fonti che non sono in self.pagine
        self.risposte = {}        # ricerca -> risultati (o un'eccezione)
        self.ricerche, self.scaricati, self.letture = [], [], []
        self.libreria = []
        self.libero = 7 * 1024 ** 4
        self.playlist = {}        # (ricerca, filtro, seguito) -> ([playlist], seguito)
        self.scoperte = []

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def leggi(self, f):
        self.letture.append(f["id"])
        x = self.pagine.get(f["id"], self.predefinito)
        if isinstance(x, Exception):
            raise x
        return list(x)

    def cerca(self, q):
        self.ricerche.append(q)
        x = self.risposte.get(q, [])
        if isinstance(x, Exception):
            raise x
        return self.m._ordina([dict(r) for r in x])

    def scarica(self, r):
        self.scaricati.append(r["id"])
        x = self.risposte.get("scarica " + r["id"])
        if isinstance(x, Exception):
            raise x
        return {"nome": "%s - %s.mp3" % (r["artista"], r["titolo"]), "brano": "b%d" % len(self.scaricati), "percorso": ""}

    def scopri(self, q, filtro, seguito):
        self.scoperte.append((q, filtro, seguito))
        x = self.playlist.get((q, filtro, seguito), ([], ""))
        if isinstance(x, Exception):
            raise x
        return x

    def nuovo(self, fonti=None, spotify=(), generi=()):
        m = self.m
        b = m.BloccoYouTube(os.path.join(self.base, "youtube-blocco.json"))
        b.adesso = self.ora
        c = m.Classifiche(os.path.join(self.base, "classifiche"), b)
        c.adesso = self.ora
        c.fonti = fonti if fonti is not None else [{"id": "prova", "nome": "Classifica di prova", "tipo": "finta", "quanti": 100}]
        c.spotify, c.generi = list(spotify), list(generi)
        c.leggi, c.cerca, c.scarica, c.scopri = self.leggi, self.cerca, self.scarica, self.scopri
        c.libero = lambda: self.libero
        c.catalogo = lambda: (len(self.libreria), list(self.libreria))
        c.videoclip = lambda artista, titolo: ""
        return c

    def gira(self, c, massimo=200):
        """Passi finché il lavoro non chiede di aspettare: restituisce quanto."""
        for _ in range(massimo):
            attesa = c.passo()
            if attesa:
                return attesa
        self.fail("il lavoro non si ferma mai")

    def test_una_classifica(self):
        self.pagine["prova"] = [("Eminem", "Without Me"), ("Travis Scott, Playboi Carti", "FE!N"), ("Eminem", "Without Me"),
                                ("Eminem", "Lose Yourself"), ("Artista", "Solo Pulita"), ("Olly", "Balorda nostalgia")]
        self.risposte = {"Eminem Without Me": [risultato("NoExplicit1", "Without Me", "Eminem"),
                                               risultato("Explicit001", "Without Me", "Eminem", "The Eminem Show", True)],
                         "Eminem Lose Yourself": [risultato("Tributo0001", "Lose Yourself", "Eminem Tribute Band")],
                         "Artista Solo Pulita": [risultato("Pulita00001", "Solo Pulita (Clean)", "Artista")],
                         "Olly Balorda nostalgia": [risultato("Explicit001", "Without Me", "Eminem", explicit=True),
                                                    risultato("Olly0000001", "Balorda nostalgia", "Olly")]}
        self.libreria = [("Travis Scott", "FE!N (feat. Playboi Carti)")]   # c'è già, scritto in un altro modo
        c = self.nuovo()
        self.assertEqual(self.gira(c), 3600)   # tutto fatto: aspetta
        self.assertEqual(self.letture, ["prova"])
        # il brano che la libreria ha già e il doppione della classifica: nessuna ricerca
        self.assertEqual(self.ricerche, ["Eminem Without Me", "Eminem Lose Yourself", "Artista Solo Pulita", "Olly Balorda nostalgia"])
        self.assertEqual(self.scaricati, ["Explicit001", "Olly0000001"])
        r = c.riassunto()
        self.assertEqual((r["attivo"], r["oggi"], r["totale"], r["blocco"], r["pausa"]), (True, 2, 2, None, None))
        self.assertEqual([(u["titolo"], u["artista"], u["fonte"]) for u in r["ultimi"]],
                         [("Balorda nostalgia", "Olly", "Classifica di prova"), ("Without Me", "Eminem", "Classifica di prova")])
        self.assertEqual(r["classifiche"][0]["esito"], "ok")
        self.assertEqual(r["classifiche"][0]["brani"], 5)   # senza il doppione
        # la classifica si rilegge ogni giorno (i nuovi ingressi arrivano subito), ma i brani già cercati non si cercano più
        self.ora.t += 3600
        self.assertEqual(self.gira(c), 3600)
        self.assertEqual(self.letture, ["prova"])
        self.ora.t += 86400
        self.assertEqual(self.gira(c), 3600)
        self.assertEqual(self.letture, ["prova", "prova"])
        self.assertEqual(len(self.ricerche), 4)
        self.assertEqual(c.riassunto()["oggi"], 0)   # è un altro giorno
        self.assertEqual(c.riassunto()["totale"], 2)
        # dopo tre mesi un brano non trovato si cerca di nuovo (forse ora c'è)
        self.ora.t += 90 * 86400
        self.gira(c)
        self.assertEqual(self.ricerche[4:], ["Eminem Lose Yourself", "Artista Solo Pulita"])

    def test_ripresa_dopo_il_riavvio(self):
        self.pagine["prova"] = [("A%d" % i, "Brano %d" % i) for i in range(4)]
        self.risposte = {"A%d Brano %d" % (i, i): [risultato("Brano%06d" % i, "Brano %d" % i, "A%d" % i)] for i in range(4)}
        c = self.nuovo()
        c.passo()   # la classifica
        c.passo()   # il primo brano
        self.assertEqual(self.scaricati, ["Brano000000"])
        c = self.nuovo()   # il Mac mini si è riavviato
        self.assertEqual(c.riassunto()["totale"], 1)
        self.assertEqual(c.riassunto()["fonte"]["in_coda"], 3)
        self.gira(c)
        self.assertEqual(self.letture, ["prova"])
        self.assertEqual(self.scaricati, ["Brano%06d" % i for i in range(4)])
        self.assertEqual(self.ricerche.count("A0 Brano 0"), 1)

    def test_blocco_429_con_la_pausa_che_cresce(self):
        m = self.m
        self.pagine["prova"] = [("Eminem", "Without Me"), ("Olly", "Balorda nostalgia")]
        self.risposte = {"Eminem Without Me": m.Bloccato("YouTube ha risposto 429 Too Many Requests"),
                         "Olly Balorda nostalgia": [risultato("Olly0000001", "Balorda nostalgia", "Olly")]}
        c = self.nuovo()
        inizio = self.ora.t
        self.assertEqual(self.gira(c), 600)
        b = c.riassunto()["blocco"]
        self.assertEqual((b["tipo"], b["quando"], b["prossima"]), ("rallenta", inizio, inizio + 1800))
        self.assertIn("429", b["motivo"])
        self.assertEqual(c.riassunto()["fonte"]["in_coda"], 2)   # il brano resta in coda
        self.assertEqual(self.ricerche, ["Eminem Without Me"])
        self.assertEqual(c.passo(), 600)   # fermo: nessuna richiesta a YouTube
        self.assertEqual(self.ricerche, ["Eminem Without Me"])
        attese = []
        for pausa in (1800, 7200, 28800, 86400, 86400):   # ogni prova fallita allunga la pausa, poi una al giorno
            self.ora.t = c.riassunto()["blocco"]["prossima"]
            self.gira(c)
            attese.append(c.riassunto()["blocco"]["prossima"] - self.ora.t)
        self.assertEqual(attese, [7200, 28800, 86400, 86400, 86400])
        self.assertEqual(len(self.ricerche), 6)   # una ricerca sola per ogni prova
        self.assertEqual(c.riassunto()["blocco"]["quando"], inizio)   # l'ora del blocco resta quella della prima volta
        # riavvio durante il blocco: resta fermo
        c = self.nuovo()
        self.assertIsNotNone(c.riassunto()["blocco"])
        self.assertGreater(c.passo(), 0)
        self.assertEqual(len(self.ricerche), 6)
        # il blocco finisce: alla prova seguente si riparte e il brano rimasto in coda si fa
        self.risposte["Eminem Without Me"] = [risultato("Explicit001", "Without Me", "Eminem", explicit=True)]
        self.ora.t = c.riassunto()["blocco"]["prossima"]
        self.assertEqual(self.gira(c), 3600)
        self.assertIsNone(c.riassunto()["blocco"])
        self.assertEqual(self.scaricati, ["Explicit001", "Olly0000001"])

    def test_robot_e_riprova_adesso(self):
        m = self.m
        self.pagine["prova"] = [("Eminem", "Without Me")]
        self.risposte = {"Eminem Without Me": m.Bloccato("Sign in to confirm you’re not a bot")}
        c = self.nuovo()
        self.gira(c)
        b = c.riassunto()["blocco"]
        self.assertEqual((b["tipo"], b["prossima"] - b["quando"]), ("robot", 86400))   # una prova al giorno, da subito
        self.ora.t += 3600
        self.assertGreater(c.passo(), 0)
        self.assertEqual(len(self.ricerche), 1)
        # «Riprova adesso» in Gestione: una prova sola, subito
        self.risposte["Eminem Without Me"] = [risultato("Explicit001", "Without Me", "Eminem", explicit=True)]
        c.riprova_adesso()
        self.assertEqual(self.gira(c), 3600)
        self.assertIsNone(c.riassunto()["blocco"])
        self.assertEqual(self.scaricati, ["Explicit001"])
        self.assertEqual(len(self.ricerche), 2)   # la prova serve anche al brano: nessuna ricerca in più

    def test_blocco_durante_lo_scaricamento(self):
        m = self.m
        self.pagine["prova"] = [("Eminem", "Without Me"), ("Olly", "Balorda nostalgia")]
        self.risposte = {"Eminem Without Me": [risultato("Explicit001", "Without Me", "Eminem", explicit=True)],
                         "Olly Balorda nostalgia": [risultato("Olly0000001", "Balorda nostalgia", "Olly")],
                         "scarica Explicit001": m._cl_errore("ERROR: [youtube] Explicit001: Sign in to confirm you're not a bot. "
                                                             "Use --cookies-from-browser")}
        c = self.nuovo()
        self.gira(c)
        self.assertEqual(c.riassunto()["blocco"]["tipo"], "robot")
        self.assertEqual(c.riassunto()["fonte"]["in_coda"], 2)
        self.assertEqual(c.riassunto()["totale"], 0)
        # gli errori: blocco, rete o brano che non va (questo si salta)
        self.assertIsInstance(m._cl_errore("ERROR: unable to download video data: HTTP Error 429: Too Many Requests"), m.Bloccato)
        self.assertIsInstance(m._cl_errore("ERROR: [youtube] x: The current session has been rate-limited by YouTube"), m.Bloccato)
        self.assertIsInstance(m._cl_errore("ERROR: Unable to download webpage: <urlopen error [Errno 8] nodename nor servname "
                                           "provided, or not known>"), m.Rete)
        self.assertIsInstance(m._cl_errore("ERROR: [youtube] x: Video unavailable"), RuntimeError)
        self.assertNotIsInstance(m._cl_errore("ERROR: [youtube] x: Video unavailable"), (m.Bloccato, m.Rete))
        # un brano che non si scarica (video tolto) si salta, il lavoro continua
        self.risposte["scarica Explicit001"] = m._cl_errore("ERROR: [youtube] Explicit001: Video unavailable")
        c.riprova_adesso()
        self.assertEqual(self.gira(c), 3600)
        self.assertEqual(c.riassunto()["totale"], 1)
        self.assertEqual(self.scaricati[-1], "Olly0000001")

    def test_spazio_e_rete(self):
        m = self.m
        self.pagine["prova"] = [("Eminem", "Without Me")]
        self.risposte = {"Eminem Without Me": [risultato("Explicit001", "Without Me", "Eminem", explicit=True)]}
        self.libero = 150 * 1024 ** 3   # sotto i 200 GB: fermo
        c = self.nuovo()
        self.assertEqual(self.gira(c), 1800)
        self.assertIn("200 GB", c.riassunto()["pausa"]["motivo"])
        self.assertEqual((self.letture, self.ricerche), ([], []))
        self.libero = None   # disco scollegato
        self.ora.t += 1801
        self.assertEqual(self.gira(c), 1800)
        self.assertIn("raggiungibile", c.riassunto()["pausa"]["motivo"])
        self.libero = 7 * 1024 ** 4
        self.ora.t += 1801
        self.risposte["Eminem Without Me"] = m.Rete("timed out")
        self.assertEqual(self.gira(c), 600)   # Internet o YouTube che non rispondono: il brano resta, si riprova
        self.assertIn("non rispondono", c.riassunto()["pausa"]["motivo"])
        self.assertIsNone(c.riassunto()["blocco"])
        self.risposte["Eminem Without Me"] = [risultato("Explicit001", "Without Me", "Eminem", explicit=True)]
        self.ora.t += 601
        self.assertEqual(self.gira(c), 3600)
        self.assertEqual(self.scaricati, ["Explicit001"])

    def test_fonte_che_non_si_legge(self):
        fonti = [{"id": "uno", "nome": "Prima", "tipo": "finta"}, {"id": "due", "nome": "Seconda", "tipo": "finta"},
                 {"id": "tre", "nome": "Terza", "tipo": "finta", "quanti": 1}]
        self.pagine = {"uno": RuntimeError("il sito ha risposto 503"), "due": [],
                       "tre": [("Olly", "Balorda nostalgia"), ("Eminem", "Without Me")]}
        self.risposte = {"Olly Balorda nostalgia": [risultato("Olly0000001", "Balorda nostalgia", "Olly")]}
        c = self.nuovo(fonti)
        self.assertEqual(self.gira(c), 3600)
        self.assertEqual(self.ricerche, ["Olly Balorda nostalgia"])   # «quanti»: solo il primo della terza
        esiti = {f["id"]: f for f in c.riassunto()["classifiche"]}
        self.assertEqual((esiti["uno"]["esito"], esiti["due"]["esito"], esiti["tre"]["esito"]), ("errore", "errore", "ok"))
        self.assertIn("503", esiti["uno"]["errore"])
        self.assertIn("cambiato", esiti["due"]["errore"])
        # una fonte che non si è letta si riprova il giorno dopo (come le altre classifiche)
        self.pagine["uno"] = [("Olly", "Balorda nostalgia")]
        self.ora.t += 3600
        self.gira(c)
        self.assertEqual(self.letture, ["uno", "due", "tre"])
        self.ora.t += 86401
        self.gira(c)
        self.assertEqual(self.letture, ["uno", "due", "tre", "uno", "due", "tre"])

    def test_playlist_senza_fine(self):
        m = self.m
        f0, f1, f2 = m.CL_FILTRI_PLAYLIST
        spotify = [("RapCaviar", "rap e hip hop", "37i9dQZF1DX0XUsuxWHRQd"), ("Today's Top Hits", "pop", "37i9dQZF1DXcBWIGoYBM5M")]
        generi = [("pop", "pop hits"), ("rap italiano", "rap italiano")]
        self.playlist = {("pop hits", f0, ""): ([{"id": "VLpop1", "nome": "Pop 1", "viste": 0}], "SEGUITO"),
                         ("pop hits", f1, ""): ([{"id": "VLpop2", "nome": "Pop 2", "viste": 900}], ""),
                         ("rap italiano", f0, ""): ([{"id": "VLrap1", "nome": "Rap 1", "viste": 0}], ""),
                         ("pop hits", f0, "SEGUITO"): ([{"id": "VLpop3", "nome": "Pop 3", "viste": 0}], "")}
        self.predefinito = [("Olly", "Balorda nostalgia")]
        self.risposte = {"Olly Balorda nostalgia": [risultato("Olly0000001", "Balorda nostalgia", "Olly")]}
        c = self.nuovo(fonti=[], spotify=spotify, generi=generi)
        self.assertEqual(self.gira(c), 3600)
        # una di Spotify e una di YouTube Music a turno; su YouTube Music la prima di ogni genere, poi la seconda…
        self.assertEqual(self.letture, ["sp:37i9dQZF1DX0XUsuxWHRQd", "yt:VLpop1", "sp:37i9dQZF1DXcBWIGoYBM5M", "yt:VLrap1",
                                        "yt:VLpop2", "yt:VLpop3"])
        self.assertIn(("pop hits", f0, "SEGUITO"), self.scoperte)   # le pagine dopo la prima: senza un numero massimo
        self.assertIn(("rap italiano", f2, ""), self.scoperte)      # e anche la ricerca senza filtro
        # le playlist fatte si rileggono dopo un mese (per i brani nuovi), non prima
        self.ora.t += 10 * 86400
        self.gira(c)
        self.assertEqual(len(self.letture), 6)
        self.ora.t += 25 * 86400
        self.gira(c)
        self.assertEqual(len(self.letture), 12)

    def test_videoclip_nel_piano(self):
        m = self.m
        self.pagine["prova"] = [("Eminem", "Without Me")]
        self.risposte = {"Eminem Without Me": [risultato("Explicit001", "Without Me", "Eminem", explicit=True)]}
        c = self.nuovo()
        c.videoclip = lambda artista, titolo: "YVkUvmDQ3HY" if (artista, titolo) == ("Eminem", "Without Me") else ""
        with unittest.mock.patch.object(m, "CLIP", object()):
            self.gira(c)
        with open(c.piano_clip, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"Eminem - Without Me.mp3": {"id": "YVkUvmDQ3HY"}})
        # il piano dei videoclip lo legge insieme a quello principale (che vince se un nome c'è in tutti e due)
        principale = os.path.join(self.base, "piano.json")
        with open(principale, "w", encoding="utf-8") as f:
            json.dump({"Altro - Brano.mp3": {"id": "abcdefghijk"}}, f)
        v = m.Videoclip(principale, os.path.join(self.base, "clip"))
        with unittest.mock.patch.object(m, "PIANI_AGGIUNTI", [c.piano_clip]):
            self.assertEqual(v.leggi_piano(), {"Altro - Brano.mp3": "abcdefghijk", "Eminem - Without Me.mp3": "YVkUvmDQ3HY"})
            with open(principale, "w", encoding="utf-8") as f:
                json.dump({"Eminem - Without Me.mp3": {"id": "Principale1"}}, f)
            os.utime(principale, (time.time() + 5, time.time() + 5))
            self.assertEqual(v.leggi_piano(), {"Eminem - Without Me.mp3": "Principale1"})

    def test_videoclip_fermi_durante_il_blocco(self):
        m = self.m
        b = m.BloccoYouTube(os.path.join(self.base, "blocco.json"))
        b.adesso = self.ora
        with unittest.mock.patch.object(m, "BLOCCO", b), unittest.mock.patch.object(m, "CL", None):
            self.assertFalse(m._videoclip_fermo())
            b.segnala("HTTP Error 429: Too Many Requests")
            self.assertTrue(m._videoclip_fermo())
            self.ora.t += 1800
            self.assertFalse(m._videoclip_fermo())   # senza classifiche, la prova la fa il videoclip
            with unittest.mock.patch.object(m, "CL", object()):
                self.assertTrue(m._videoclip_fermo())   # con le classifiche la fa la ricerca
            b.riuscito()
            self.assertFalse(m._videoclip_fermo())

    def test_gestione_solo_samuele(self):
        m = self.m

        class H:
            _chi = {"ruolo": "casa", "persona": None, "chiave": None}
        for ruolo in ("casa", "invitato"):
            H._chi = dict(H._chi, ruolo=ruolo)
            with self.assertRaises(m.ErroreExtra) as e:
                m._gestione(H(), "GET", ["classifiche"], {})
            self.assertEqual(e.exception.codice, 403)
        for p in ("/api/extra/gestione/classifiche", "/api/extra/gestione/classifiche/riprova"):
            self.assertIsNone(m.PERMESSE_INVITATI.match(p), p)   # agli invitati non arriva nemmeno
        self.assertTrue(m.PERMESSE_INVITATI.match("/api/extra/classifiche"))   # le classifiche come playlist sì
        self.assertIsNone(m.PERMESSE_INVITATI.match("/api/extra/classifiche/x"))

    def brani(self, c, fid="prova"):
        el = {x["id"]: x for x in c.classifiche_app()["classifiche"]}[fid]
        return [(b["pos"], b["titolo"], b["segno"], b["di"], b["stato"], b["brano"]) for b in el["brani"]], el

    def test_classifiche_come_playlist(self):
        self.pagine["prova"] = [("Olly", "Balorda nostalgia"), ("Coma_Cose", "Cuoricini"), ("Geolier", "Nun te scetà")]
        self.risposte = {"Olly Balorda nostalgia": [risultato("Olly0000001", "Balorda nostalgia", "Olly")],
                         "Geolier Nun te scetà": [risultato("Altro000001", "Altra canzone", "Geolier")]}
        self.libreria = [("Coma_Cose", "Cuoricini", "lib-cuoricini")]   # c'è già nella libreria
        c = self.nuovo()
        self.assertEqual(c.classifiche_app(), {"classifiche": []})   # prima della prima lettura: niente
        self.gira(c)
        brani, el = self.brani(c)
        # la prima volta: le posizioni, senza segni (non c'è una lettura prima); il brano scaricato ha il suo id del catalogo
        self.assertEqual(brani, [(1, "Balorda nostalgia", "", 0, "libreria", "b1"), (2, "Cuoricini", "", 0, "libreria", "lib-cuoricini"),
                                 (3, "Nun te scetà", "", 0, "non disponibile", "")])
        self.assertEqual((el["nome"], el["usciti"]), ("Classifica di prova", []))
        # intanto una playlist lunga riempie la coda…
        c.s["coda"] = [{"a": "Artista %d" % i, "t": "Brano %d" % i, "f": "Una playlist"} for i in range(50)]
        # …la classifica nuova (il giorno dopo) passa davanti: i nuovi ingressi si scaricano subito
        self.pagine["prova"] = [("ANNA", "30°C"), ("Geolier", "Nun te scetà"), ("Olly", "Balorda nostalgia"), ("Coma_Cose", "Cuoricini")]
        self.risposte["ANNA 30°C"] = [risultato("Anna0000001", "30°C", "ANNA", explicit=True)]
        self.ora.t += 86400
        self.assertEqual(c.passo(), 0)
        self.assertEqual(self.letture[-1], "prova")
        self.assertEqual(c.s["coda"][0]["t"], "30°C")
        self.assertEqual(len(c.s["coda"]), 51)
        # un'altra classifica letta subito dopo va dietro a questa, ma sempre davanti alla playlist
        altra = {"id": "altra", "nome": "Altra classifica", "tipo": "finta"}
        c.fonti.append(altra)
        self.pagine["altra"] = [("Artista 7", "Brano 7"), ("ANNA", "30°C"), ("Sfera Ebbasta", "Nuova")]
        c.passo()
        self.assertEqual([v["t"] for v in c.s["coda"][:4]], ["30°C", "Brano 7", "Nuova", "Brano 0"])
        self.assertEqual(len(c.s["coda"]), 52)   # «Brano 7» spostato davanti, non ripetuto
        c.fonti.remove(altra)
        brani, el = self.brani(c)
        self.assertEqual(brani, [(1, "30°C", "nuovo", 0, "in arrivo", ""), (2, "Nun te scetà", "su", 1, "non disponibile", ""),
                                 (3, "Balorda nostalgia", "giu", 2, "libreria", "b1"), (4, "Cuoricini", "giu", 2, "libreria", "lib-cuoricini")])
        c.passo()
        self.assertEqual(self.scaricati[-1], "Anna0000001")
        self.assertEqual(self.brani(c)[0][0][4:], ("libreria", "b2"))
        # riletta uguale il giorno dopo: i segni restano quelli della classifica nuova
        self.ora.t += 86400
        c.passo()
        self.assertEqual(self.letture.count("prova"), 3)
        self.assertEqual([b[2] for b in self.brani(c)[0]], ["nuovo", "su", "giu", "giu"])
        # un brano esce: sparisce dalla classifica ma resta nella libreria, in fondo tra gli «Usciti di recente»
        self.pagine["prova"] = [("ANNA", "30°C"), ("Olly", "Balorda nostalgia"), ("Geolier", "Nun te scetà")]
        self.ora.t += 86400
        c.passo()
        brani, el = self.brani(c)
        self.assertEqual([b[1] for b in brani], ["30°C", "Balorda nostalgia", "Nun te scetà"])
        self.assertEqual([(u["titolo"], u["pos"], u["stato"], u["brano"]) for u in el["usciti"]],
                         [("Cuoricini", 4, "libreria", "lib-cuoricini")])
        self.assertEqual([b[2:4] for b in brani], [("uguale", 0), ("su", 1), ("giu", 1)])
        # dopo quattro settimane gli usciti non si mostrano più; chi rientra torna «nuovo» e non è più tra gli usciti
        self.ora.t += 29 * 86400
        self.assertEqual(self.brani(c)[1]["usciti"], [])
        self.pagine["prova"] = [("Coma_Cose", "Cuoricini"), ("ANNA", "30°C")]
        c.passo()
        brani, el = self.brani(c)
        self.assertEqual([b[1:3] for b in brani], [("Cuoricini", "nuovo"), ("30°C", "giu")])
        self.assertEqual([u["titolo"] for u in el["usciti"]], ["Balorda nostalgia", "Nun te scetà"])
        # dopo un riavvio le classifiche restano
        self.assertEqual(self.brani(self.nuovo())[0], brani)


class FintoWeb(http.server.BaseHTTPRequestHandler):
    """Le pagine delle classifiche (indirizzo del sito + percorso) e YouTube Music, finti."""
    blocca = False
    ricerche = []
    PAGINE = {"/kworb.net/spotify/country/it_weekly.html":
              '<table><tr><td class="text mp"><div><a href="../artist/1.html">Eminem</a> - '
              '<a href="../track/1.html">Without Me</a></div></td></tr></table>',
              "/kworb.net/spotify/country/global_weekly.html":
              '<table><tr><td class="text mp"><div><a href="../artist/2.html">Dua Lipa</a> - '
              '<a href="../track/2.html">Houdini</a></div></td></tr></table>'}

    def do_GET(self):
        dati = self.PAGINE.get(self.path, "").encode("utf-8")
        self.send_response(200 if dati else 404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(dati)))
        self.end_headers()
        self.wfile.write(dati)

    def do_POST(self):
        corpo = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        q = str(corpo.get("query") or "").lower()
        if "/search" in self.path:
            FintoWeb.ricerche.append(q)
        if "/search" in self.path and FintoWeb.blocca:
            stato, d = 429, {"error": {"code": 429, "message": "Too Many Requests"}}
        elif "/search" in self.path and q == "eminem without me":
            stato, d = 200, RISPOSTA_YTM
        elif "/search" in self.path and q == "dua lipa houdini":
            stato, d = 200, {"contents": {"tabbedSearchResultsRenderer": {"tabs": [{"tabRenderer": {"content": {
                "sectionListRenderer": {"contents": [{"musicShelfRenderer": {"contents": [
                    _brano_ytm("HoudiniRmx1", "Houdini (Remix)", ["Dua Lipa"], "Houdini", "3:10", True),
                    _brano_ytm("HoudiniExp1", "Houdini", ["Dua Lipa"], "Radical Optimism", "3:05", True)]}}]}}}}]}}}
        elif "/search" in self.path:
            stato, d = 200, {"contents": {}}
        else:
            stato, d = 404, {"error": {"code": 404}}
        dati = json.dumps(d).encode("utf-8")
        self.send_response(stato)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(dati)))
        self.end_headers()
        self.wfile.write(dati)

    def log_message(self, *x):
        pass


class TestClassificheServer(unittest.TestCase):
    """Il server vero con ATLANTE_CLASSIFICHE=1, pagine e YouTube finti, yt-dlp e ffmpeg finti: un brano entra nella
    libreria, YouTube blocca, Samuele lo vede in Gestione e sul riquadro (io), «Riprova adesso» e si riparte."""

    @classmethod
    def setUpClass(cls):
        cls.base = tempfile.mkdtemp(prefix="atlante-classifiche-srv-")
        cls.c = prepara_cartelle(cls.base)
        cls.web = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FintoWeb)
        threading.Thread(target=cls.web.serve_forever, daemon=True).start()
        finto = "http://127.0.0.1:%d" % cls.web.server_address[1]
        cls.c["youtubei"] = finto
        FintoWeb.blocca, FintoWeb.ricerche = False, []
        # copertine e tutto il resto che andrebbe su Internet: verso un indirizzo dove non risponde nessuno
        morto = "http://127.0.0.1:%d" % porta_libera()
        with unittest.mock.patch.dict(os.environ, {"ATLANTE_CLASSIFICHE": "1", "ATLANTE_CLASSIFICHE_FINTE": finto,
                                                   "http_proxy": morto, "https_proxy": morto, "no_proxy": "127.0.0.1,localhost"}):
            cls.s = Server(cls.base, cls.c).avvia()

    @classmethod
    def tearDownClass(cls):
        cls.s.ferma()
        cls.web.shutdown()
        cls.web.server_close()
        shutil.rmtree(cls.base, ignore_errors=True)

    def stato(self):
        return self.s.json("GET", "/api/extra/gestione/classifiche")

    def test_dalla_classifica_alla_libreria(self):
        r = aspetta(lambda: (lambda x: x if x["totale"] >= 1 else None)(self.stato()), 40, 0.1)
        self.assertIsNotNone(r, "%r\n%s" % (self.stato(), self.s.leggi_log()))
        FintoWeb.blocca = True   # dal prossimo brano YouTube risponde 429
        self.assertEqual((r["ultimi"][0]["titolo"], r["ultimi"][0]["artista"]), ("Without Me", "Eminem"))
        self.assertEqual(r["ultimi"][0]["fonte"], "Spotify Top 50 Italia")
        self.assertTrue(os.path.isfile(os.path.join(self.c["scaricati"], "Eminem - Without Me.mp3")))
        with open(os.path.join(self.c["finto"], "ffmpeg.log"), encoding="utf-8") as f:
            tag = json.loads(f.read().strip().splitlines()[-1])["tag"]
        self.assertEqual((tag["ITUNESADVISORY"], tag["purl"]), ("1", "https://music.youtube.com/watch?v=tqxRidAWER8"))
        cat = self.s.json("GET", "/api/musica/catalogo")["brani"]
        self.assertIn(("Without Me", "Eminem"), [(x["title"], x["artist"]) for x in cat])
        # YouTube blocca: fermo, avviso in Gestione e nel riquadro di «Altro»
        r = aspetta(lambda: (lambda x: x if x["blocco"] else None)(self.stato()), 40, 0.1)
        self.assertIsNotNone(r, self.s.leggi_log())
        self.assertEqual(r["blocco"]["tipo"], "rallenta")
        self.assertEqual(self.s.json("GET", "/api/extra/io")["nuove"]["youtube_bloccato"], 1)
        n = len(FintoWeb.ricerche)
        time.sleep(1)
        self.assertEqual(len(FintoWeb.ricerche), n)   # fermo davvero: nessuna richiesta
        # «Riprova adesso»: una prova sola; il blocco è finito, si riparte col brano rimasto in coda (la versione giusta)
        FintoWeb.blocca = False
        self.assertEqual(self.s.json("POST", "/api/extra/gestione/classifiche/riprova", {}), {"ok": True})
        r = aspetta(lambda: (lambda x: x if x["totale"] >= 2 else None)(self.stato()), 40, 0.1)
        self.assertIsNotNone(r, "%r\n%s" % (self.stato(), self.s.leggi_log()))
        self.assertIsNone(r["blocco"])
        self.assertTrue(os.path.isfile(os.path.join(self.c["scaricati"], "Dua Lipa - Houdini.mp3")))
        self.assertNotIn("youtube_bloccato", self.s.json("GET", "/api/extra/io").get("nuove", {}))
        # ogni classifica letta è una playlist per l'app: posizioni e il brano della libreria (id del catalogo)
        cl = {x["id"]: x for x in self.s.json("GET", "/api/extra/classifiche")["classifiche"]}
        self.assertEqual(cl["spotify-it"]["nome"], "Spotify Top 50 Italia")
        b = cl["spotify-it"]["brani"][0]
        self.assertEqual((b["pos"], b["titolo"], b["artista"], b["stato"]), (1, "Without Me", "Eminem", "libreria"))
        cat = {x["id"]: x for x in self.s.json("GET", "/api/musica/catalogo")["brani"]}
        self.assertEqual(cat[b["brano"]]["title"], "Without Me")
        self.assertEqual(cl["spotify-mondo"]["brani"][0]["titolo"], "Houdini")
        self.assertNotIn("billboard", cl)   # non letta: nessuna playlist
        self.s.json("POST", "/api/extra/classifiche", {}, codice=405)
        # le fonti che non esistono qui (404) si saltano e lo si dice
        esiti = {f["id"]: f["esito"] for f in self.stato()["classifiche"]}
        self.assertEqual((esiti["spotify-it"], esiti["spotify-mondo"]), ("ok", "ok"))
        aspetta(lambda: self.stato()["classifiche"][-1]["esito"], 20, 0.2)
        self.assertEqual({f["id"]: f["esito"] for f in self.stato()["classifiche"]}["billboard"], "errore")
        # le scritture della Gestione: solo dalla stessa origine
        self.s.json("POST", "/api/extra/gestione/classifiche/riprova", {}, codice=403, intest={"Origin": "https://altro.example"})
        self.s.json("GET", "/api/extra/gestione/classifiche/nessuna", codice=404)


# ----- brani ascoltati: classifiche e consigli tra le persone -----

BRANI_FINTI = ["%016x" % (0xa0000 + i) for i in range(41)]   # id del catalogo (16 cifre esadecimali)


class _Scritto:
    def __init__(self):
        self.b = b""

    def write(self, x):
        self.b += x


class FintaRisposta(FintaRichiesta):
    """FintaRichiesta che raccoglie anche la risposta (codice e JSON): le rotte si chiamano senza un server."""

    def __init__(self, ip, intest=None, percorso="/api/extra/ascoltati"):
        super().__init__(ip, intest, percorso)
        self.wfile, self.codice = _Scritto(), None

    def send_response(self, codice):
        self.codice = codice

    def send_header(self, k, v):
        pass

    def end_headers(self):
        pass

    def risposta(self):
        return json.loads(self.wfile.b.decode("utf-8"))


class TestAscoltati(unittest.TestCase):
    """Brani ascoltati per persona e giorno (Ascoltati), con persone e ascolti finti: soglia delle 3 persone, tetto per
    persona, nomi solo con la condivisione accesa e spariti allo spegnimento, revoca, cancellazione dei propri ascolti."""

    @classmethod
    def setUpClass(cls):
        sys.dont_write_bytecode = True
        spec = importlib.util.spec_from_file_location("musica_libreria_ascoltati", SERVER)
        cls.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.m)

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="atlante-ascoltati-")
        self.file = os.path.join(self.base, "cache", "ascoltati.json")
        self.nomi = {"p": "Samuele", "anna": "Anna", "bruno": "Bruno", "carla": "Carla", "dario": "Dario", "elena": "Elena"}
        self.cat = {b: {"id": b, "title": "Brano %d" % i, "artist": "Artista %d" % i, "album": "Album", "cover": "c%d.jpg" % i}
                    for i, b in enumerate(BRANI_FINTI)}
        self.a = self.nuovo()

    def tearDown(self):
        self.a.salva()
        shutil.rmtree(self.base, ignore_errors=True)

    def nuovo(self):
        return self.m.Ascoltati(self.file, persone=lambda: set(self.nomi), nome=lambda p: self.nomi.get(p, ""),
                                catalogo=lambda: self.cat)

    def senti(self, chi, n, volte=1, fa=0):
        """`chi` ha ascoltato il brano numero n `volte` volte, `fa` giorni fa."""
        return self.a.ascolta(chi, [{"brano": BRANI_FINTI[n], "giorno": self.m._asc_giorno(fa)}] * volte)

    def classifica(self, quale="settimana", chi="p"):
        return [(BRANI_FINTI.index(x["brano"]), x["ascolti"]) for x in self.a.classifiche(chi)[quale]]

    def per_te(self, chi="p"):
        return [(BRANI_FINTI.index(x["brano"]), x["da"]) for x in self.a.classifiche(chi)["per_te"]]

    def test_soglia_tre_persone(self):
        self.senti("anna", 1)
        self.senti("bruno", 1)
        self.assertEqual(self.classifica(), [])
        self.assertEqual(self.classifica("mese"), [])
        self.senti("carla", 1)
        self.assertEqual(self.classifica(), [(1, 3)])
        self.assertEqual(self.classifica("mese"), [(1, 3)])
        # tutti vedono la stessa classifica, anche Samuele (che il brano non l'ha ascoltato)
        for chi in self.nomi:
            c = self.a.classifiche(chi)
            self.assertEqual((c["settimana"], c["mese"]), (self.a.classifiche("anna")["settimana"], self.a.classifiche("anna")["mese"]))
        x = self.a.classifiche("p")["settimana"][0]
        # titolo e copertina per chi non ha il catalogo (la Gestione); chi l'ha ascoltato non si dice
        self.assertEqual(x, {"brano": BRANI_FINTI[1], "titolo": "Brano 1", "artista": "Artista 1", "album": "Album",
                             "copertina": "c1.jpg", "ascolti": 3})

    def test_tetto_per_persona(self):
        self.senti("anna", 2, volte=50)   # tutto il giorno lo stesso brano: conta 3
        self.senti("bruno", 2)
        self.senti("carla", 2)
        for chi in ("bruno", "carla", "dario"):
            self.senti(chi, 3, volte=2)
        self.assertEqual(self.classifica(), [(3, 6), (2, 5)])
        # nel mese il tetto vale settimana per settimana; più di 30 giorni fa non conta
        self.senti("anna", 2, volte=4, fa=10)
        self.senti("anna", 2, volte=4, fa=40)
        self.assertEqual(self.classifica(), [(3, 6), (2, 5)])
        self.assertEqual(self.classifica("mese"), [(2, 8), (3, 6)])

    def test_ascolti_scartati(self):
        g = self.m._asc_giorno
        voci = [{"brano": "nonvalido", "giorno": g(0)}, {"brano": BRANI_FINTI[1], "giorno": "2026-02-31"},
                {"brano": BRANI_FINTI[1], "giorno": g(self.m.ASCOLTATI_GIORNI)},   # più di 2 anni fa
                {"brano": "ffffffffffffffff", "giorno": g(0)},                     # non è nel catalogo
                {"brano": BRANI_FINTI[1], "giorno": g(-5)},                        # tra 5 giorni
                {"brano": BRANI_FINTI[1], "giorno": g(-1)},                        # domani (fuso orario diverso): conta oggi
                {"brano": BRANI_FINTI[1]},                                         # senza giorno: oggi
                "testo", None, {"brano": ["x"]}]
        self.assertEqual(self.a.ascolta("anna", voci), (2, 8))
        self.assertEqual(self.a.classifiche("anna")["io"]["ascolti"], 2)
        self.assertEqual(self.a.ascolta("zeta", [{"brano": BRANI_FINTI[1]}]), (0, 1))   # persona che non c'è (revocata)
        with unittest.mock.patch.object(self.m, "ASCOLTATI_GIORNO_MAX", 5):   # un'app impazzita non riempie il file
            self.assertEqual(self.senti("bruno", 4, volte=8), (5, 3))
        # catalogo vuoto (il Mac sta ancora leggendo il disco): si tengono, se l'id è valido
        self.cat.clear()
        self.assertEqual(self.a.ascolta("carla", [{"brano": "ffffffffffffffff"}]), (1, 0))

    def test_nomi_solo_con_condivisione(self):
        for chi in ("anna", "bruno", "carla"):
            self.senti(chi, 5)
        self.senti("anna", 6, volte=2)
        self.assertEqual(self.a.classifiche("p")["condivisi"], [])
        self.assertFalse(self.a.classifiche("anna")["io"]["condivido"])
        self.assertTrue(self.a.condividi("anna", True))
        c = self.a.classifiche("p")["condivisi"]
        self.assertEqual([x["nome"] for x in c], ["Anna"])
        # anche il brano che ha ascoltato solo lei: l'ha scelto lei di farlo vedere
        self.assertEqual([(BRANI_FINTI.index(x["brano"]), x["ascolti"]) for x in c[0]["brani"]], [(6, 2), (5, 1)])
        self.assertEqual(self.a.classifiche("anna")["condivisi"], [])   # «Cosa ascolta Anna» non serve ad Anna
        self.assertTrue(self.a.classifiche("anna")["io"]["condivido"])
        self.assertFalse(self.a.condividi("anna", False))
        self.assertEqual(self.a.classifiche("p")["condivisi"], [])
        self.assertNotIn("Anna", json.dumps(self.a.classifiche("p")))
        self.a.salva()
        self.assertFalse(self.nuovo().classifiche("anna")["io"]["condivido"])

    def test_per_te(self):
        for n in (1, 2, 3, 4, 5):
            self.senti("p", n)
        for n in (1, 2, 3, 4, 5, 6, 7):
            self.senti("anna", n)
        for n in (1, 2, 3, 4, 6, 9):
            self.senti("bruno", n)
        for n in (30, 31, 32, 33, 6, 8):    # gusti diversi da quelli di Samuele
            self.senti("carla", n)
        for chi in ("dario", "elena"):
            for n in (30, 31, 32, 33, 8, 9):
                self.senti(chi, n)
        # 6 (Anna, Bruno, Carla) e 9 (Bruno, Dario, Elena): da chi ha gusti simili e ascoltati da almeno 3 persone;
        # 7 l'ha sentito solo Anna, 8 solo chi non somiglia a Samuele
        self.assertEqual(self.per_te(), [(6, None), (9, None)])
        self.assertTrue(self.a.condividi("anna", True))
        self.assertEqual(self.per_te(), [(6, "Anna"), (7, "Anna"), (9, None)])
        self.a.condividi("anna", False)
        self.assertEqual(self.per_te(), [(6, None), (9, None)])
        # un brano sentito dopo il calcolo esce subito dai consigli
        self.senti("p", 6)
        self.assertEqual(self.per_te(), [(9, None)])
        # i consigli si ricalcolano di notte (e all'avvio): fino ad allora restano quelli
        for n in (1, 2, 3, 4, 5, 10):
            self.senti("dario", n)
        self.senti("carla", 10)
        self.senti("elena", 10)
        self.assertEqual(self.per_te(), [(9, None)])
        self.a.ricalcola()
        self.assertIn((10, None), self.per_te())
        # chi non ha ancora ascoltato niente: nessun consiglio
        self.nomi["fabio"] = "Fabio"
        self.assertEqual(self.per_te("fabio"), [])

    def test_anche(self):
        for chi in ("p", "anna", "bruno"):
            self.senti(chi, 1)
            self.senti(chi, 2)
        self.senti("p", 3)
        self.senti("anna", 3)       # con il brano 1 solo da 2 persone
        self.senti("carla", 3)
        self.senti("dario", 3)
        self.assertEqual([BRANI_FINTI.index(x["brano"]) for x in self.a.anche(BRANI_FINTI[1])], [2])
        self.assertEqual(self.a.anche(BRANI_FINTI[1])[0]["titolo"], "Brano 2")
        self.assertEqual(self.a.anche(BRANI_FINTI[3]), [])
        self.senti("elena", 4)
        self.senti("elena", 1)
        self.assertEqual(self.a.anche(BRANI_FINTI[4]), [])   # un brano di una persona sola non dice niente di lei

    def test_revoca(self):
        for chi in ("anna", "bruno", "carla"):
            self.senti(chi, 7)
        self.a.condividi("carla", True)
        self.assertEqual(self.classifica(), [(7, 3)])
        self.assertEqual([x["nome"] for x in self.a.classifiche("p")["condivisi"]], ["Carla"])
        del self.nomi["carla"]   # Samuele revoca Carla
        self.assertEqual(self.classifica(), [])
        self.assertEqual(self.a.classifiche("p")["condivisi"], [])
        # di notte i suoi ascolti escono anche dal file
        self.a.pulisci()
        self.a.salva()
        with open(self.file, encoding="utf-8") as f:
            self.assertNotIn("carla", f.read())

    def test_cancella_i_propri(self):
        for chi in ("anna", "bruno", "carla"):
            self.senti(chi, 8, volte=2)
            self.senti(chi, 9)
        self.senti("p", 9)
        self.senti("p", 11)
        self.assertIn((8, None), self.per_te())
        self.assertEqual(self.a.cancella("anna"), 3)
        self.assertEqual(self.a.classifiche("anna")["io"]["ascolti"], 0)
        self.assertEqual(self.classifica(), [(9, 3)])
        self.assertNotIn((8, None), self.per_te())   # subito, senza aspettare il calcolo della notte
        with open(self.file, encoding="utf-8") as f:   # e subito anche dal disco
            self.assertNotIn('"anna"', f.read())
        self.assertEqual(self.a.cancella("anna"), 0)

    def test_file(self):
        self.senti("anna", 1, volte=2)
        self.a.condividi("anna", True)
        self.a.salva()
        self.assertEqual(os.stat(self.file).st_mode & 0o777, 0o600)
        self.assertEqual(os.listdir(os.path.dirname(self.file)), ["ascoltati.json"])   # niente file temporanei rimasti
        self.assertEqual(self.nuovo().classifiche("anna")["io"], {"condivido": True, "ascolti": 2, "nome": "Anna"})
        # al più 2 anni: quello che è più vecchio esce
        with open(self.file, encoding="utf-8") as f:
            d = json.load(f)
        vecchio = self.m._asc_giorno(self.m.ASCOLTATI_GIORNI + 3)
        d["conti"]["anna"][vecchio] = {BRANI_FINTI[2]: 1}
        with open(self.file, "w", encoding="utf-8") as f:
            json.dump(d, f)
        b = self.nuovo()
        self.assertEqual(b.classifiche("anna")["io"]["ascolti"], 2)
        b.pulisci()
        b.salva()
        with open(self.file, encoding="utf-8") as f:
            self.assertNotIn(vecchio, f.read())
        # file rovinato: si mette da parte invece di sovrascriverlo con un archivio vuoto
        with open(self.file, "w") as f:
            f.write("{rotto")
        r = self.nuovo()
        self.assertEqual(r.classifiche("anna")["io"]["ascolti"], 0)
        self.assertTrue(os.path.exists(self.file + ".rotto"))

    def test_scritture_vicine_insieme(self):
        with unittest.mock.patch.object(self.m, "ASCOLTATI_SALVA", 0.3):
            self.senti("anna", 1)
            self.senti("anna", 2)   # subito dopo: va su disco poco dopo, con una scrittura sola
            def leggi():
                with open(self.file, encoding="utf-8") as f:
                    return f.read()
            self.assertIn(BRANI_FINTI[1], leggi())
            self.assertTrue(aspetta(lambda: BRANI_FINTI[2] in leggi(), 5))

    def test_casa_conta_come_samuele(self):
        m = self.m
        with unittest.mock.patch.object(m, "ACC", m.Accesso(os.path.join(self.base, "accesso.json"))), \
                unittest.mock.patch.object(m, "PERS", m.Persone(os.path.join(self.base, "persone.json"))), \
                unittest.mock.patch.object(m, "ASCOLTATI", self.a):
            def casa(metodo, resto, corpo=None):
                h = FintaRisposta("192.168.1.20")
                self.assertFalse(m.controlla_accesso(h))
                self.assertEqual(h._chi["ruolo"], "casa")
                m._ascoltati(h, metodo, resto, corpo or {})
                return h.codice, h.risposta()
            self.assertEqual(casa("POST", [], {"ascolti": [{"brano": BRANI_FINTI[1]}]}), (200, {"contati": 1, "scartati": 0}))
            self.assertEqual(self.a.classifiche("p")["io"]["ascolti"], 1)
            st, j = casa("GET", [])
            self.assertEqual((st, j["io"]["nome"], j["io"]["cambia"]), (200, "Samuele", False))
            # dalla rete di casa non si cambia niente degli ascolti di Samuele
            for metodo, resto in (("POST", ["condividi"]), ("DELETE", [])):
                with self.assertRaises(m.ErroreExtra) as e:
                    casa(metodo, resto, {"si": True})
                self.assertEqual(e.exception.codice, 403)
            self.assertEqual(self.a.classifiche("p")["io"], {"condivido": False, "ascolti": 1, "nome": "Samuele"})


class TestAscoltatiServer(unittest.TestCase):
    """Le rotte api/extra/ascoltati sul server di prova: invitati, Samuele, revoca, cancellazione, ascolti fatti senza rete."""

    ORIGINE = "https://mac.esempio.ts.net:10000"

    @classmethod
    def setUpClass(cls):
        cls.base = tempfile.mkdtemp(prefix="atlante-ascoltati-server-")
        cls.c = prepara_cartelle(cls.base)
        for i in range(1, 9):
            with open(os.path.join(cls.c["musica"], "Artista %d - Brano %d.mp3" % (i, i)), "wb") as f:
                f.write(b"\0" * 2000)
        cls.s = Server(cls.base, cls.c, ["--pubblico", cls.ORIGINE]).avvia()
        sys.dont_write_bytecode = True
        spec = importlib.util.spec_from_file_location("musica_libreria_ascoltati_server", SERVER)
        cls.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.m)
        cls.pers = cls.m.Persone(os.path.join(cls.c["cache"], "persone.json"))
        cat = aspetta(lambda: (lambda j: j if len(j["brani"]) == 8 else None)(cls.s.json("GET", "/api/musica/catalogo")))
        cls.b = {int(x["title"].split()[-1]): x["id"] for x in cat["brani"]}
        cls.ip = 10

    @classmethod
    def tearDownClass(cls):
        cls.s.ferma()
        shutil.rmtree(cls.base, ignore_errors=True)

    def invitato(self, nome):
        """(persona, biscotto) di una persona invitata entrata dal suo link."""
        a = self.pers.crea(nome)
        codice, _ = self.pers.invito(a["id"])
        TestAscoltatiServer.ip += 1
        st, h, b = richiesta(self.s.porta, "POST", "/accesso/invito/" + codice, b"", intest=dict(
            FUNNEL, **{"X-Forwarded-For": "203.0.113.%d" % self.ip, "Origin": self.ORIGINE,
                       "Content-Type": "application/x-www-form-urlencoded"}))
        self.assertEqual(st, 200, b[:200])
        v = [x for x in h.get_all("Set-Cookie") if x.startswith("am_accesso=")][0].split(";")[0].split("=", 1)[1]
        return a["id"], v

    def da(self, v, metodo, percorso, corpo=None, origine=None):
        st, h, b = richiesta(self.s.porta, metodo, percorso, corpo, intest=dict(
            FUNNEL, **{"Cookie": "am_accesso=" + v, "Origin": origine or self.ORIGINE}))
        return st, (json.loads(b.decode("utf-8")) if b else None)

    def samuele(self, metodo, percorso, corpo=None, codice=200):
        return self.s.json(metodo, percorso, corpo, codice)

    def voci(self, n, fa=0, volte=1):
        return {"ascolti": [{"brano": self.b[n], "giorno": self.m._asc_giorno(fa)}] * volte}

    def sett(self, j, n, quale="settimana"):
        return [x["ascolti"] for x in j[quale] if x["brano"] == self.b[n]]

    def test_1_invitati_e_samuele(self):
        tre = [self.invitato(n) for n in ("Anna", "Bruno", "Carla")]
        for _, v in tre:
            self.assertEqual(self.da(v, "POST", "/api/extra/ascoltati", self.voci(1)), (200, {"contati": 1, "scartati": 0}))
            # ascolti fatti senza rete 12 giorni fa, arrivati adesso: contano in quel giorno
            self.assertEqual(self.da(v, "POST", "/api/extra/ascoltati", self.voci(6, fa=12))[0], 200)
        self.assertEqual(self.samuele("POST", "/api/extra/ascoltati", {"brano": self.b[1]}), {"contati": 1, "scartati": 0})
        st, j = self.da(tre[0][1], "GET", "/api/extra/ascoltati")
        self.assertEqual(st, 200)
        self.assertEqual([(x["brano"], x["titolo"], x["artista"], x["ascolti"]) for x in j["settimana"]],
                         [(self.b[1], "Brano 1", "Artista 1", 4)])
        self.assertEqual((self.sett(j, 6), self.sett(j, 6, "mese")), ([], [3]))
        self.assertEqual(j["io"], {"condivido": False, "ascolti": 2, "nome": "Anna", "cambia": True})
        # Samuele vede le stesse classifiche di tutti e, di una persona, niente (anche se lo chiede)
        sam = self.samuele("GET", "/api/extra/ascoltati?persona=" + tre[0][0])
        self.assertEqual((sam["settimana"], sam["mese"]), (j["settimana"], j["mese"]))
        self.assertEqual(sam["io"], {"condivido": False, "ascolti": 1, "nome": "Samuele", "cambia": True})
        self.assertNotIn("ascolt", json.dumps(self.samuele("GET", "/api/extra/gestione/persone")))

    def test_2_rotte_permesse_agli_invitati(self):
        pid, v = self.invitato("Elisa")
        for metodo, p, corpo in (("GET", "/api/extra/ascoltati", None), ("POST", "/api/extra/ascoltati", {"ascolti": []}),
                                 ("HEAD", "/api/extra/ascoltati", None),
                                 ("GET", "/api/extra/ascoltati/anche/" + self.b[2], None),
                                 ("POST", "/api/extra/ascoltati/condividi", {"si": False}),
                                 ("DELETE", "/api/extra/ascoltati", None), ("GET", "/musica/api/extra/ascoltati", None)):
            st, j = self.da(v, metodo, p, corpo)
            self.assertEqual(st, 200, (metodo, p, j))
        for p in ("/api/extra/ascoltatix", "/api/extra/ascoltati/anche/x/y", "/api/extra/ascoltati/tutti"):
            self.assertEqual(self.da(v, "GET", p)[0], 403, p)
        self.assertEqual(self.da(v, "GET", "/api/extra/ascoltati/anche/nonvalido")[0], 400)
        self.assertEqual(self.samuele("GET", "/api/extra/ascoltati/tutti", codice=404)["errore"][:9], "Richiesta")
        # le scritture solo dall'app, come le altre
        self.assertEqual(self.da(v, "POST", "/api/extra/ascoltati/condividi", {"si": True}, origine="https://altro.example")[0], 403)
        self.assertEqual(self.da(v, "DELETE", "/api/extra/ascoltati", origine="https://altro.example")[0], 403)
        self.assertEqual(self.da(v, "POST", "/api/extra/ascoltati", {"ascolti": [{}] * 501})[0], 413)

    def test_3_condivisione_nomi_e_revoca(self):
        (gina, vg), (ugo, vu), (lia, vl) = (self.invitato(n) for n in ("<b>Gina</b>", "Ugo", "Lia"))
        for v in (vg, vu, vl):
            self.da(v, "POST", "/api/extra/ascoltati", self.voci(3))
        self.samuele("POST", "/api/extra/ascoltati", self.voci(3))
        self.assertEqual(self.sett(self.samuele("GET", "/api/extra/ascoltati"), 3), [4])
        self.assertEqual(self.da(vg, "POST", "/api/extra/ascoltati/condividi", {"si": True}), (200, {"condivido": True}))
        st, j = self.da(vu, "GET", "/api/extra/ascoltati")
        self.assertEqual([(x["nome"], [y["brano"] for y in x["brani"]]) for x in j["condivisi"]], [("<b>Gina</b>", [self.b[3]])])
        self.assertEqual([x["nome"] for x in self.samuele("GET", "/api/extra/ascoltati")["condivisi"]], ["<b>Gina</b>"])
        self.assertEqual(self.da(vg, "POST", "/api/extra/ascoltati/condividi", {"si": False}), (200, {"condivido": False}))
        self.assertEqual(self.da(vu, "GET", "/api/extra/ascoltati")[1]["condivisi"], [])
        # una persona revocata esce dalle classifiche
        self.assertTrue(self.pers.revoca_persona(lia))
        self.assertEqual(self.sett(self.samuele("GET", "/api/extra/ascoltati"), 3), [3])
        self.assertTrue(self.pers.revoca_persona(ugo))
        self.assertEqual(self.sett(self.samuele("GET", "/api/extra/ascoltati"), 3), [])
        self.assertEqual(self.da(vl, "GET", "/api/extra/ascoltati")[0], 401)

    def test_4_anche_e_per_te(self):
        tre = [self.invitato(n) for n in ("Olga", "Piero", "Rita")]
        for _, v in tre:
            self.da(v, "POST", "/api/extra/ascoltati", {"ascolti": self.voci(4)["ascolti"] + self.voci(5)["ascolti"]})
        pid, v = self.invitato("Sara")
        self.da(v, "POST", "/api/extra/ascoltati", self.voci(4))
        st, j = self.da(v, "GET", "/api/extra/ascoltati/anche/" + self.b[4])
        self.assertEqual((st, [x["brano"] for x in j["brani"]]), (200, [self.b[5]]))
        st, j = self.da(v, "GET", "/api/extra/ascoltati")
        self.assertEqual([(x["brano"], x["da"]) for x in j["per_te"]], [(self.b[5], None)])

    def test_5_cancella_i_miei(self):
        pid, v = self.invitato("Teo")
        self.da(v, "POST", "/api/extra/ascoltati", self.voci(7, volte=2))
        self.assertEqual(self.da(v, "GET", "/api/extra/ascoltati")[1]["io"]["ascolti"], 2)
        self.assertEqual(self.da(v, "DELETE", "/api/extra/ascoltati"), (200, {"ok": True, "cancellati": 2}))
        self.assertEqual(self.da(v, "GET", "/api/extra/ascoltati")[1]["io"]["ascolti"], 0)
        with open(os.path.join(self.c["cache"], "ascoltati.json"), encoding="utf-8") as f:
            self.assertNotIn(pid, f.read())
        self.assertEqual(os.stat(os.path.join(self.c["cache"], "ascoltati.json")).st_mode & 0o777, 0o600)
        # anche Samuele i suoi
        self.assertGreater(self.samuele("DELETE", "/api/extra/ascoltati")["cancellati"], 0)
        self.assertEqual(self.samuele("GET", "/api/extra/ascoltati")["io"]["ascolti"], 0)


# ----- Gestione → Rete: Tailscale sul Mac mini (sempre con il tailscale finto) -----

class TestRete(unittest.TestCase):
    """Gestione → Rete: stato di Tailscale, Funnel e uscita da casa, solo per Samuele; il campo «via» di api/extra/io."""

    SAMUELE_TS = {"X-Forwarded-For": "100.100.1.2", "Tailscale-User-Login": "samuele@example.com"}
    ALTRO_TS = {"X-Forwarded-For": "100.100.1.9", "Tailscale-User-Login": "anna@example.com"}   # rete Tailscale, non Samuele: «casa»
    RETE = "/api/extra/gestione/rete"

    @classmethod
    def setUpClass(cls):
        cls.base = tempfile.mkdtemp(prefix="atlante-rete-")
        cls.c = prepara_cartelle(cls.base)
        with open(os.path.join(cls.c["app"], "x", "rete.html"), "w", encoding="utf-8") as f:
            f.write("<!doctype html><title>Rete e VPN</title>")
        with unittest.mock.patch.dict(os.environ, {"ATLANTE_PROPRIETARI": "samuele@example.com"}):
            cls.s = Server(cls.base, cls.c, ["--pubblico", "https://mac-mini.tail1234.ts.net:10000"]).avvia()
        sys.dont_write_bytecode = True
        spec = importlib.util.spec_from_file_location("musica_libreria_rete", SERVER)
        cls.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.m)
        cls.pers = cls.m.Persone(os.path.join(cls.c["cache"], "persone.json"))

    @classmethod
    def tearDownClass(cls):
        cls.s.ferma()
        shutil.rmtree(cls.base, ignore_errors=True)

    def setUp(self):
        self.stato({})   # ogni prova parte con Tailscale collegato, senza Funnel e senza uscita da casa
        self.chiamate()

    def stato(self, st):
        with open(os.path.join(self.c["finto"], "tailscale-stato.json"), "w") as f:
            json.dump(st, f)

    def chiamate(self):
        """I comandi arrivati al tailscale finto dall'ultima volta (poi l'elenco riparte da zero)."""
        p = os.path.join(self.c["finto"], "tailscale.log")
        try:
            with open(p) as f:
                righe = [json.loads(x) for x in f if x.strip()]
            os.unlink(p)
        except OSError:
            righe = []
        return righe

    def samuele(self, metodo, p, corpo=None, codice=200, intest=None):
        return self.s.json(metodo, p, corpo, codice, intest=intest)

    def invitato(self, nome):
        a = self.pers.crea(nome)
        did = self.pers.usa_invito(self.pers.invito(a["id"])[0], "iphone")
        return dict(FUNNEL, Cookie="am_accesso=" + self.pers.biscotto(did))

    def test_1_stato(self):
        import calendar
        j = self.samuele("GET", self.RETE)
        self.assertTrue(j["installato"])
        self.assertTrue(j["collegato"])
        self.assertEqual((j["nome"], j["dns"], j["indirizzo"], j["rete"], j["errore"]),
                         ("mac-mini", "mac-mini.tail1234.ts.net", "100.100.1.1", "tail1234.ts.net", ""))
        self.assertEqual([(d["nome"], d["sistema"], d["online"], d["indirizzo"]) for d in j["dispositivi"]],
                         [("iphone-di-samuele", "iOS", True, "100.100.1.2"), ("macbook", "macOS", False, "100.100.1.3")])
        self.assertIsNone(j["dispositivi"][0]["visto"])   # «0001-01-01»: online adesso
        self.assertEqual(j["dispositivi"][1]["visto"], calendar.timegm((2026, 10, 26, 18, 30, 0)))
        f = j["funnel"]
        self.assertEqual((f["acceso"], f["porta"], f["porte"]), (False, None, [443, 8443, 10000]))
        # la porta predefinita è quella dell'indirizzo pubblico dei link d'invito, l'indirizzo è questo server
        self.assertEqual(f["predefiniti"], {"porta": 10000, "destino": "http://127.0.0.1:%d" % self.s.porta})
        self.assertEqual(j["uscita"], {"annunciata": False, "approvata": False})
        self.assertEqual(j["questo"], "casa")   # il Mac stesso
        # solo comandi che leggono
        self.assertEqual(sorted(map(tuple, self.chiamate())),
                         [("debug", "prefs"), ("funnel", "status", "--json"), ("status", "--json")])

    def test_2_funnel_acceso_e_spento(self):
        j = self.samuele("POST", self.RETE + "/funnel", {"acceso": True})
        self.assertEqual((j["funnel"]["acceso"], j["funnel"]["porta"], j["funnel"]["indirizzo"]),
                         (True, 10000, "https://mac-mini.tail1234.ts.net:10000/"))
        self.assertIn(["funnel", "--bg", "--https=10000", "http://127.0.0.1:%d" % self.s.porta], self.chiamate())
        self.assertTrue(aspetta(lambda: "Funnel acceso" in self.s.leggi_log(), 5), self.s.leggi_log())
        self.assertTrue(self.samuele("GET", self.RETE)["funnel"]["acceso"])
        self.chiamate()
        j = self.samuele("POST", self.RETE + "/funnel", {"acceso": False})
        self.assertFalse(j["funnel"]["acceso"])
        self.assertIn(["funnel", "--https=10000", "off"], self.chiamate())
        self.assertTrue(aspetta(lambda: "Funnel spento" in self.s.leggi_log(), 5))
        # già spento: nessun comando che cambia qualcosa
        self.samuele("POST", self.RETE + "/funnel", {"acceso": False})
        self.assertFalse([c for c in self.chiamate() if "off" in c or "--bg" in c])

    def test_3_porta_e_indirizzo_scelti(self):
        j = self.samuele("POST", self.RETE + "/funnel", {"acceso": True, "porta": 8443, "destino": "http://localhost:%d/" % self.s.porta})
        self.assertEqual((j["funnel"]["acceso"], j["funnel"]["porta"]), (True, 8443))
        self.assertIn(["funnel", "--bg", "--https=8443", "http://127.0.0.1:%d" % self.s.porta], self.chiamate())
        # acceso: per cambiare porta prima si spegne
        st, h, b = richiesta(self.s.porta, "POST", self.RETE + "/funnel", {"acceso": True, "porta": 10000})
        self.assertEqual(st, 409, b)
        self.assertFalse([c for c in self.chiamate() if "--bg" in c])
        # si spegne la porta dove è acceso, e la scelta resta per la prossima volta
        self.samuele("POST", self.RETE + "/funnel", {"acceso": False})
        self.assertIn(["funnel", "--https=8443", "off"], self.chiamate())
        self.assertEqual(self.samuele("GET", self.RETE)["funnel"]["predefiniti"]["porta"], 8443)
        self.samuele("POST", self.RETE + "/funnel", {"acceso": True, "porta": 10000})
        self.samuele("POST", self.RETE + "/funnel", {"acceso": False})
        self.chiamate()
        # nessun testo dell'utente arriva al comando: porte e indirizzi solo tra quelli permessi
        for corpo in ({"acceso": True, "porta": 1234}, {"acceso": True, "porta": "10000; ls"}, {"acceso": True, "porta": True},
                      {"acceso": True, "destino": "http://127.0.0.1:8790/;rm -rf ~"}, {"acceso": True, "destino": "http://192.168.1.5:80"},
                      {"acceso": True, "destino": "http://127.0.0.1:70000"}, {"acceso": True, "destino": "file:///etc/passwd"},
                      {"acceso": True, "destino": "http://utente@127.0.0.1:8790"}, {"acceso": True, "destino": 8790},
                      {"acceso": "sì"}, {}):
            st, h, b = richiesta(self.s.porta, "POST", self.RETE + "/funnel", corpo)
            self.assertEqual(st, 400, (corpo, b))
        self.assertEqual(self.chiamate(), [])

    def test_4_porta_della_rete_tailscale_non_si_tocca(self):
        # tailscale serve --bg 8790: l'indirizzo dell'app nella rete Tailscale (porta 443) non diventa pubblico per sbaglio
        self.stato({"serve": {"TCP": {"443": {"HTTPS": True}},
                              "Web": {"mac-mini.tail1234.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8790"}}}}}})
        j = self.samuele("GET", self.RETE)
        self.assertEqual(j["serve"], [{"porta": 443, "indirizzo": "https://mac-mini.tail1234.ts.net/",
                                       "destino": "http://127.0.0.1:8790", "funnel": False}])
        self.chiamate()
        st, h, b = richiesta(self.s.porta, "POST", self.RETE + "/funnel", {"acceso": True, "porta": 443})
        self.assertEqual(st, 409)
        self.assertIn("443", json.loads(b.decode("utf-8"))["errore"])
        self.assertFalse([c for c in self.chiamate() if "--bg" in c])

    def test_5_funnel_non_permesso(self):
        self.stato({"funnel_vietato": True})
        st, h, b = richiesta(self.s.porta, "POST", self.RETE + "/funnel", {"acceso": True})
        self.assertEqual(st, 409, b)
        j = json.loads(b.decode("utf-8"))
        self.assertTrue(j["pannello"])
        self.assertIn("pannello di Tailscale", j["errore"])
        self.assertIn("Funnel is not enabled", j["dettaglio"])
        self.assertEqual(j["link"], "https://login.tailscale.com/f/funnel?node=nFINTO123")
        self.assertFalse(j["rete"]["funnel"]["acceso"])
        self.assertTrue(aspetta(lambda: "Funnel non acceso" in self.s.leggi_log(), 5))

    def test_6_uscita_da_casa(self):
        j = self.samuele("POST", self.RETE + "/uscita", {"acceso": True})
        self.assertEqual(j["uscita"], {"annunciata": True, "approvata": False})   # la prima volta va approvata nel pannello
        self.assertIn(["set", "--advertise-exit-node=true"], self.chiamate())
        self.assertTrue(aspetta(lambda: "Uscita da casa accesa" in self.s.leggi_log(), 5))
        self.stato({"uscita": True, "approvata": True})
        self.assertEqual(self.samuele("GET", self.RETE)["uscita"], {"annunciata": True, "approvata": True})
        j = self.samuele("POST", self.RETE + "/uscita", {"acceso": False})
        self.assertEqual(j["uscita"]["annunciata"], False)
        self.assertIn(["set", "--advertise-exit-node=false"], self.chiamate())
        self.assertTrue(aspetta(lambda: "Uscita da casa spenta" in self.s.leggi_log(), 5))
        st, h, b = richiesta(self.s.porta, "POST", self.RETE + "/uscita", {"acceso": 1})
        self.assertEqual(st, 400)

    def test_7_solo_samuele(self):
        ospite = self.invitato("Ospite della rete")
        for metodo, p, corpo in (("GET", self.RETE, None), ("POST", self.RETE + "/funnel", {"acceso": True}),
                                 ("POST", self.RETE + "/uscita", {"acceso": True})):
            st, h, b = richiesta(self.s.porta, metodo, p, corpo, intest=ospite)
            self.assertEqual((st, json.loads(b.decode("utf-8"))["errore"]), (403, "Non disponibile per gli invitati"), p)
            st, h, b = richiesta(self.s.porta, metodo, p, corpo, intest=self.ALTRO_TS)
            self.assertEqual((st, json.loads(b.decode("utf-8"))["errore"]), (403, "Solo Samuele può usare la Gestione"), p)
            if metodo == "POST":   # Samuele, ma la richiesta parte da un altro sito aperto nel browser
                st, h, b = richiesta(self.s.porta, metodo, p, corpo, intest={"Origin": "https://altro.example"})
                self.assertEqual(st, 403, p)
        self.assertEqual(self.chiamate(), [])   # tailscale non è stato neanche chiamato
        self.assertEqual(self.samuele("GET", self.RETE, intest=self.SAMUELE_TS)["questo"], "tailscale")
        # la guida per il telefono la vedono tutti, anche gli invitati (spiega che a loro non serve)
        self.assertEqual(richiesta(self.s.porta, "GET", "/x/rete.html", intest=ospite)[0], 200)
        self.assertEqual(richiesta(self.s.porta, "GET", "/x/rete.html", intest=self.ALTRO_TS)[0], 200)

    def test_8_tailscale_spento_sul_mac(self):
        self.stato({"spento": True})
        j = self.samuele("GET", self.RETE)
        self.assertEqual((j["installato"], j["collegato"]), (True, False))
        self.assertIn("failed to connect", j["errore"])
        st, h, b = richiesta(self.s.porta, "POST", self.RETE + "/funnel", {"acceso": True})
        self.assertEqual(st, 409)
        self.assertIn("failed to connect", json.loads(b.decode("utf-8"))["dettaglio"])
        # per spegnere, se Tailscale non risponde si prova comunque sulla porta scelta (e si dice com'è andata)
        self.chiamate()
        st, h, b = richiesta(self.s.porta, "POST", self.RETE + "/funnel", {"acceso": False})
        self.assertEqual(st, 409)
        self.assertIn(["funnel", "--https=10000", "off"], self.chiamate())
        # Tailscale collegato e niente Funnel: spegnere non chiama niente
        self.stato({})
        self.samuele("POST", self.RETE + "/funnel", {"acceso": False})
        self.assertFalse([c for c in self.chiamate() if "off" in c])

    def test_9_da_dove_arriva(self):
        self.assertEqual(self.samuele("GET", "/api/extra/io")["via"], "casa")
        self.assertEqual(self.samuele("GET", "/api/extra/io", intest=self.SAMUELE_TS)["via"], "tailscale")
        io = self.samuele("GET", "/api/extra/io", intest=self.ALTRO_TS)
        self.assertEqual((io["ruolo"], io["via"]), ("casa", "tailscale"))
        io = self.samuele("GET", "/api/extra/io", intest=self.invitato("Ospite di passaggio"))
        self.assertEqual((io["ruolo"], io["via"]), ("invitato", "internet"))

    def test_9b_richieste_sbagliate(self):
        self.assertEqual(richiesta(self.s.porta, "GET", self.RETE + "/funnel")[0], 405)
        self.assertEqual(richiesta(self.s.porta, "DELETE", self.RETE)[0], 405)
        self.assertEqual(richiesta(self.s.porta, "POST", self.RETE, {})[0], 405)
        self.assertEqual(richiesta(self.s.porta, "GET", self.RETE + "/altro")[0], 404)
        self.assertEqual(self.chiamate(), [])


class TestReteInterna(unittest.TestCase):
    """Le parti di Gestione → Rete senza server: da dove arriva una richiesta, lettura delle risposte di tailscale,
    tempo massimo dei comandi. Il tailscale vero non si chiama mai: il programma è sempre quello finto (o nessuno)."""

    @classmethod
    def setUpClass(cls):
        sys.dont_write_bytecode = True
        spec = importlib.util.spec_from_file_location("musica_libreria_rete_interna", SERVER)
        cls.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.m)
        cls.base = tempfile.mkdtemp(prefix="atlante-rete-interna-")
        cls.finto = os.path.join(cls.base, "tailscale")
        with open(cls.finto, "w", encoding="utf-8") as f:
            f.write("#!%s\n%s" % (sys.executable, FINTO_TAILSCALE))
        os.chmod(cls.finto, 0o755)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.base, ignore_errors=True)

    def con_finto(self, prog):
        return unittest.mock.patch.object(self.m, "_tailscale_programma", return_value=prog)

    def test_via(self):
        for ip, intest, via in (("127.0.0.1", {}, "casa"), ("::1", {}, "casa"), ("192.168.1.20", {}, "casa"),
                                ("100.100.1.2", {}, "tailscale"), ("8.8.8.8", {}, "internet"),
                                ("127.0.0.1", FUNNEL, "internet"),
                                ("127.0.0.1", {"X-Forwarded-For": "100.64.0.1"}, "tailscale"),
                                ("127.0.0.1", {"X-Forwarded-For": "fd7a:115c:a1e0::5"}, "tailscale"),
                                ("127.0.0.1", {"X-Forwarded-For": "203.0.113.5, 100.64.0.1", "Tailscale-User-Login": "s@e.com"}, "tailscale"),
                                ("127.0.0.1", {"X-Forwarded-For": "100.64.0.1, 203.0.113.5"}, "internet"),
                                ("192.168.1.3", {"X-Forwarded-For": "100.64.0.1"}, "internet")):
            self.assertEqual(self.m._via(FintaRichiesta(ip, intest)), via, (ip, intest))

    def test_ore_di_tailscale(self):
        import calendar
        t = calendar.timegm((2026, 10, 26, 18, 30, 0))
        self.assertEqual(self.m._rete_ora("2026-10-26T18:30:00.123456789Z"), t)
        self.assertEqual(self.m._rete_ora("2026-10-26T20:30:00+02:00"), t)
        for x in ("0001-01-01T00:00:00Z", "", None, "ieri", 5):
            self.assertIsNone(self.m._rete_ora(x), x)

    def test_serve_e_funnel_letti(self):
        conf = {"TCP": {"443": {"HTTPS": True}, "10000": {"HTTPS": True}},
                "Web": {"mac.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8790"}}},
                        "mac.ts.net:10000": {"Handlers": {"/": {"Proxy": "http://localhost:8790"}}}},
                "AllowFunnel": {"mac.ts.net:10000": True},
                "Foreground": {"sessione": {"Web": {"mac.ts.net:8443": {"Handlers": {"/doc": {"Path": "/Users/x/doc"}}}},
                                            "AllowFunnel": {"mac.ts.net:8443": True}}}}
        self.assertEqual(self.m._rete_serve(conf), [
            {"porta": 443, "indirizzo": "https://mac.ts.net/", "destino": "http://127.0.0.1:8790", "funnel": False},
            {"porta": 8443, "indirizzo": "https://mac.ts.net:8443/", "destino": "/Users/x/doc", "funnel": True},
            {"porta": 10000, "indirizzo": "https://mac.ts.net:10000/", "destino": "http://localhost:8790", "funnel": True}])
        for strano in ({}, None, [], {"Web": []}, {"Web": {"senza-porta": {}}}, {"Web": {"x:1": {"Handlers": []}}}):
            self.assertIsInstance(self.m._rete_serve(strano), list)
        self.assertEqual([self.m._rete_porta_locale(x) for x in ("http://127.0.0.1:8790", "http://localhost:8790/", "http://[::1]:9",
                                                                  "https://127.0.0.1:8790", "http://127.0.0.1", "http://10.0.0.1:8790",
                                                                  "http://127.0.0.1:8790/x", "http://127.0.0.1:8790?a")],
                         [8790, 8790, 9, None, None, None, None, None])

    def test_senza_tailscale(self):
        with self.con_finto(None):
            r = self.m._rete_stato(8790, "casa")
            self.assertEqual((r["installato"], r["collegato"]), (False, False))
            self.assertIn("non è installato", r["errore"])
            with self.assertRaises(self.m.ErroreExtra) as e:
                self.m._tailscale(["status", "--json"], 5)
            self.assertEqual(e.exception.codice, 503)

    def test_tempo_massimo(self):
        # Funnel non permesso: il tailscale vero scrive il link del pannello e aspetta che Samuele lo abiliti
        finto = os.path.join(self.base, "finto")
        os.makedirs(finto, exist_ok=True)
        with open(os.path.join(finto, "tailscale-stato.json"), "w") as f:
            json.dump({"funnel_vietato": True, "attesa": True}, f)
        with self.con_finto(self.finto), unittest.mock.patch.dict(os.environ, {"FINTO_DIR": finto}), \
                unittest.mock.patch.object(self.m, "TEMPO_TAILSCALE_CAMBIO", 1), \
                unittest.mock.patch.dict(self.m.CONF, {"cache": os.path.join(self.base, "cache")}):
            t = time.time()
            with self.assertRaises(self.m.RifiutoTailscale) as e:
                self.m._rete_funnel(True, None, None, 8790)
            self.assertLess(time.time() - t, 15)
        self.assertEqual(e.exception.link, "https://login.tailscale.com/f/funnel?node=nFINTO123")
        self.assertTrue(e.exception.pannello)
        self.assertFalse(os.path.exists(os.path.join(self.base, "cache", "rete.json")))   # niente salvato se non è acceso


# ----- caricamenti, «Da controllare», film sul Cloud e contenuti (Gestione, parte 2) -----

ORIGINE_APP = "https://mac.esempio.ts.net:10000"
PNG = b"\x89PNG\r\n\x1a\n" + bytes(64)   # quanto basta perché il Mac la riconosca come immagine


class ProvaCaricamenti:
    """Server di prova con un invitato che entra dal suo link (come TestAccesso) e richieste da lui e da Samuele."""
    OPZIONI = ["--spazio-min-gb", "0", "--limite-giorno-gb", "1"]

    @classmethod
    def prepara(cls, prefisso):
        cls.base = tempfile.mkdtemp(prefix=prefisso)
        cls.c = prepara_cartelle(cls.base)
        with open(os.path.join(cls.c["musica"], "Eminem - Without Me.mp3"), "wb") as f:   # già nella libreria
            f.write(mp3_finto("Without Me", "Eminem", "The Eminem Show"))
        cls.s = Server(cls.base, cls.c, ["--pubblico", ORIGINE_APP] + cls.OPZIONI).avvia()
        sys.dont_write_bytecode = True
        spec = importlib.util.spec_from_file_location("musica_libreria_" + prefisso.strip("-"), SERVER)
        cls.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.m)
        cls.pers = cls.m.Persone(os.path.join(cls.c["cache"], "persone.json"))
        cls.ip = 10

    @classmethod
    def chiudi(cls):
        cls.s.ferma()
        shutil.rmtree(cls.base, ignore_errors=True)

    def invitato(self, nome):
        """(persona, biscotto) di una persona invitata, entrata dal suo link da Internet."""
        a = self.pers.crea(nome)
        codice, _ = self.pers.invito(a["id"])
        type(self).ip += 1
        st, h, b = richiesta(self.s.porta, "POST", "/accesso/invito/" + codice, b"",
                             intest=dict(FUNNEL, **{"X-Forwarded-For": "203.0.113.%d" % self.ip, "Origin": ORIGINE_APP,
                                                    "Content-Type": "application/x-www-form-urlencoded"}))
        self.assertEqual(st, 200, b[:300])
        v = [x for x in h.get_all("Set-Cookie") if x.startswith("am_accesso=o.")][0]
        return a["id"], v.split(";")[0].split("=", 1)[1]

    def da(self, biscotto, metodo, p, corpo=None, intest=None):
        """Richiesta: dall'invitato (con il suo biscotto, da Internet) o da Samuele (biscotto None: dal Mac)."""
        h = dict(FUNNEL, **{"Cookie": "am_accesso=" + biscotto, "Origin": ORIGINE_APP}) if biscotto else {}
        h.update(intest or {})
        return richiesta(self.s.porta, metodo, p, corpo, intest=h)

    def j(self, biscotto, metodo, p, corpo=None, codice=200, intest=None):
        st, h, b = self.da(biscotto, metodo, p, corpo, intest)
        if codice is not None:
            self.assertEqual(st, codice, (metodo, p, b[:300]))
        return json.loads(b.decode("utf-8")) if b else None

    def pezzo(self, biscotto, id_, da, dati, codice=200):
        return self.j(biscotto, "PUT", "/api/extra/caricamenti/%s?da=%d" % (id_, da), dati, codice,
                      {"Content-Type": "application/octet-stream"})

    def carica(self, biscotto, nome, dati, pezzi=2, modificato=1700000000000):
        s = self.j(biscotto, "POST", "/api/extra/caricamenti", {"tipo": "brano", "nome": nome, "dimensione": len(dati),
                                                                "modificato": modificato})
        passo = -(-len(dati) // pezzi)
        for da in range(s["ricevuti"], len(dati), passo):
            s = self.pezzo(biscotto, s["id"], da, dati[da:da + passo])
        return s

    def catalogo(self):
        return self.j(None, "GET", "/api/musica/catalogo")

    def controllo(self):
        return {x["id"]: x for x in self.j(None, "GET", "/api/extra/gestione/controllo")["brani"]}

    def miei(self, biscotto):
        return self.j(biscotto, "GET", "/api/extra/caricamenti")


class TestCaricamenti(ProvaCaricamenti, unittest.TestCase):
    """Brani caricati dal telefono, «Da controllare», «Cerca e scarica» degli invitati, film sul Cloud, contenuti."""

    @classmethod
    def setUpClass(cls):
        cls.prepara("atlante-carica-")

    @classmethod
    def tearDownClass(cls):
        cls.chiudi()

    def test_1_brano_caricato_controllato_e_approvato(self):
        pid, v = self.invitato("Anna")
        dati = mp3_finto("Lose Yourself", "Eminem", "8 Mile")
        s = self.j(v, "POST", "/api/extra/caricamenti", {"tipo": "brano", "nome": "lose.mp3", "dimensione": len(dati),
                                                         "modificato": 1700000000000})
        self.assertEqual((s["ricevuti"], s["stato"], s["pezzo"]), (0, "in arrivo", 8 * 1024 * 1024))
        meta = len(dati) // 2
        self.assertEqual(self.pezzo(v, s["id"], 0, dati[:meta])["ricevuti"], meta)
        s = self.pezzo(v, s["id"], meta, dati[meta:])
        self.assertEqual((s["stato"], s["ricevuti"]), ("fatto", len(dati)), s)
        eid = s["elemento"]
        # ffprobe ha controllato il file vero
        with open(os.path.join(self.c["finto"], "ffprobe.log"), encoding="utf-8") as f:
            self.assertIn("-show_streams", f.read())
        mie = {x["id"]: x for x in self.miei(v)["brani"]}
        self.assertEqual((mie[eid]["stato"], mie[eid]["titolo"], mie[eid]["origine"]), ("attesa", "Lose Yourself", "caricato"))
        # finché non è approvato non è nella libreria di nessuno
        self.assertNotIn("Lose Yourself", [b["title"] for b in self.catalogo()["brani"]])
        # Samuele lo vede in Gestione → Da controllare: chi, quando, i tag letti dal file; una scheda accanto al file
        x = self.controllo()[eid]
        self.assertEqual((x["chi_nome"], x["origine"], x["tag"]), ("Anna", "caricato", {"titolo": "Lose Yourself",
                                                                                      "artista": "Eminem", "album": "8 Mile"}))
        self.assertEqual(x["doppioni"], [])
        with open(os.path.join(self.c["controllo"], eid + ".json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["chi_nome"], "Anna")
        self.assertEqual(self.da(None, "GET", "/api/extra/gestione/controllo/%s/file" % eid)[2], dati)   # ascolto
        st, h, b = self.da(None, "GET", "/api/extra/gestione/controllo/%s/file" % eid, intest={"Range": "bytes=0-2"})
        self.assertEqual((st, b), (206, b"ID3"))
        # correzione e copertina nuova, poi Approva: un file nuovo nella cartella della musica scaricata con i tag giusti
        self.j(None, "POST", "/api/extra/gestione/controllo/" + eid, {"titolo": "Lose Yourself (Live)", "album": "Curtain Call"})
        self.j(None, "PUT", "/api/extra/gestione/controllo/%s/copertina" % eid, PNG, intest={"Content-Type": "image/png"})
        self.j(None, "PUT", "/api/extra/gestione/controllo/%s/copertina" % eid, b"non un'immagine", 400,
               intest={"Content-Type": "image/png"})
        r = self.j(None, "POST", "/api/extra/gestione/controllo/%s/approva" % eid, {})
        self.assertEqual(r["nome"], "Eminem - Lose Yourself (Live).mp3")
        self.assertTrue(os.path.isfile(os.path.join(self.c["scaricati"], r["nome"])))
        with open(os.path.join(self.c["finto"], "ffmpeg.log"), encoding="utf-8") as f:
            ff = json.loads(f.read().strip().splitlines()[-1])
        self.assertEqual((ff["tag"]["title"], ff["tag"]["artist"], ff["tag"]["album"]),
                         ("Lose Yourself (Live)", "Eminem", "Curtain Call"))
        self.assertEqual(ff["args"][ff["args"].index("-c:a") + 1], "copy")   # un MP3 resta l'MP3 di prima: niente perdita
        self.assertEqual(len([a for a in ff["args"] if a == "-i"]), 2)       # con la copertina nuova
        b = [x for x in self.catalogo()["brani"] if x["id"] == r["brano"]]
        self.assertEqual([(x["title"], x["artist"], x["album"]) for x in b], [("Lose Yourself (Live)", "Eminem", "Curtain Call")])
        self.assertEqual({x["id"]: x for x in self.miei(v)["brani"]}[eid]["stato"], "approvato")
        self.assertNotIn(eid, self.controllo())
        self.assertFalse(os.path.exists(os.path.join(self.c["controllo"], eid + ".mp3")))
        self.assertTrue(os.path.isfile(os.path.join(self.c["controllo"], "Approvati", eid + ".json")))
        self.j(None, "POST", "/api/extra/gestione/controllo/%s/approva" % eid, {}, 404)

    def test_2_doppione_e_scarta(self):
        pid, v = self.invitato("Bruno")
        # stesso artista e titolo (maiuscole a parte) di un brano della libreria
        eid = self.carica(v, "wm.mp3", mp3_finto("WITHOUT ME", "eminem", "Altro album"))["elemento"]
        self.assertEqual([(d["titolo"], d["artista"]) for d in self.controllo()[eid]["doppioni"]], [("Without Me", "Eminem")])
        r = self.j(None, "POST", "/api/extra/gestione/controllo/%s/approva" % eid, {}, 409)
        self.assertIn("già", r["errore"])
        self.assertEqual(r["doppioni"][0]["artista"], "Eminem")
        self.assertTrue(self.j(None, "POST", "/api/extra/gestione/controllo/%s/approva" % eid, {"forza": True})["ok"])
        # Scarta: va in «Scartati», non si cancella
        e2 = self.carica(v, "x.mp3", mp3_finto("Brano brutto", "Qualcuno"))["elemento"]
        self.assertEqual(self.j(None, "POST", "/api/extra/gestione/controllo/%s/scarta" % e2, {}), {"ok": True})
        self.assertTrue(os.path.isfile(os.path.join(self.c["controllo"], "Scartati", e2 + ".mp3")))
        self.assertEqual({x["id"]: x for x in self.miei(v)["brani"]}[e2]["stato"], "scartato")
        self.assertNotIn(e2, self.controllo())
        self.assertIn(e2, [x["id"] for x in self.j(None, "GET", "/api/extra/gestione/controllo")["scartati"]])
        self.assertNotIn("Brano brutto", [b["title"] for b in self.catalogo()["brani"]])

    def test_3_file_sbagliati_e_limiti(self):
        pid, v = self.invitato("Carla")
        s = self.carica(v, "finto.mp3", b"questo non e' un file audio " * 50)
        self.assertEqual(s["stato"], "errore")
        self.assertIn("non è un file audio", s["errore"])
        self.assertEqual([n for n in os.listdir(self.c["controllo"]) if n.startswith(s["id"])], [])
        self.assertEqual(os.listdir(os.path.join(self.c["controllo"], ".in arrivo")), [])
        nuovo = {"tipo": "brano", "nome": "a.mp3", "dimensione": 1000}
        self.j(v, "POST", "/api/extra/caricamenti", dict(nuovo, nome="programma.exe"), 400)
        self.assertIn("200 MB", self.j(v, "POST", "/api/extra/caricamenti", dict(nuovo, dimensione=200 * 1024 * 1024 + 1), 413)["errore"])
        self.j(v, "POST", "/api/extra/caricamenti", dict(nuovo, dimensione=0), 400)
        self.j(v, "POST", "/api/extra/caricamenti", dict(nuovo, tipo="altro"), 400)
        s = self.j(v, "POST", "/api/extra/caricamenti", dict(nuovo, modificato=1))
        self.assertEqual(self.pezzo(v, s["id"], 5, b"x" * 10, 409)["ricevuti"], 0)    # pezzo fuori posto: si dice da dove
        self.pezzo(v, s["id"], 0, b"x" * 1001, 400)                                    # più di quanto annunciato
        self.j(v, "PUT", "/api/extra/caricamenti/%s?da=0" % s["id"], b"x", 403, {"Origin": "https://altro.example"})
        # un altro invitato non tocca i caricamenti di Carla, Samuele nemmeno da qui
        _, v2 = self.invitato("Dario")
        self.pezzo(v2, s["id"], 0, b"x" * 10, 404)
        self.j(v2, "GET", "/api/extra/caricamenti/" + s["id"], None, 404)
        self.j(v2, "DELETE", "/api/extra/caricamenti/" + s["id"], None, 404)
        e = self.carica(v, "ok.mp3", mp3_finto("Di Carla", "Carla"))["elemento"]
        self.assertEqual(self.da(v, "GET", "/api/extra/caricamenti/%s/file" % e)[0], 200)
        self.assertEqual(self.da(v2, "GET", "/api/extra/caricamenti/%s/file" % e)[0], 404)
        # annullato: via il pezzo già arrivato
        self.assertEqual(self.j(v, "DELETE", "/api/extra/caricamenti/" + s["id"]), {"ok": True})
        self.j(v, "GET", "/api/extra/caricamenti/" + s["id"], None, 404)
        # troppi caricamenti a metà
        for i in range(10):
            self.j(v2, "POST", "/api/extra/caricamenti", dict(nuovo, nome="b%d.mp3" % i))
        self.assertIn("a metà", self.j(v2, "POST", "/api/extra/caricamenti", dict(nuovo, nome="b10.mp3"), 429)["errore"])
        # le rotte della Gestione: solo Samuele
        for metodo, rotta in (("GET", "controllo"), ("POST", "controllo/%s/approva" % e), ("POST", "controllo/%s/scarta" % e),
                              ("GET", "contenuti?q=a"), ("POST", "film/x/consenti")):
            self.assertEqual(self.da(v, metodo, "/api/extra/gestione/" + rotta, {} if metodo == "POST" else None)[0], 403, rotta)

    def test_4_ripresa_riscegliendo_lo_stesso_file(self):
        pid, v = self.invitato("Elena")
        dati = mp3_finto("Ripresa", "Elena", fotogrammi=30)
        corpo = {"tipo": "brano", "nome": "ripresa.mp3", "dimensione": len(dati), "modificato": 1700000000123}
        s = self.j(v, "POST", "/api/extra/caricamenti", corpo)
        self.pezzo(v, s["id"], 0, dati[:5000])
        # il telefono si è spento: si sceglie di nuovo lo stesso file, il Mac dice da dove riprendere
        s2 = self.j(v, "POST", "/api/extra/caricamenti", corpo)
        self.assertEqual((s2["id"], s2["ricevuti"]), (s["id"], 5000))
        self.assertEqual([x["id"] for x in self.miei(v)["caricamenti"]], [s["id"]])
        self.assertEqual(self.pezzo(v, s["id"], 5000, dati[5000:])["stato"], "fatto")
        # un file diverso con lo stesso nome (altra data) è un caricamento nuovo
        self.assertNotEqual(self.j(v, "POST", "/api/extra/caricamenti", dict(corpo, modificato=1))["id"], s["id"])

    def test_5_cerca_e_scarica_di_un_invitato(self):
        pid, v = self.invitato("Franca")
        brano = {"id": "Invitata001", "titolo": "Stan", "artista": "Eminem", "album": "The Marshall Mathers LP",
                 "copertina": "https://127.0.0.1/c.jpg", "explicit": True}
        id_ = self.j(v, "POST", "/api/extra/musica", brano)["id"]

        def lavoro():
            return {x["id"]: x for x in self.j(v, "GET", "/api/extra/musica")["lavori"]}.get(id_)
        l = aspetta(lambda: (lambda x: x if x and x["stato"] in ("fatto", "errore") else None)(lavoro()), 40)
        self.assertEqual(l["stato"], "fatto", (l, self.s.leggi_log()))
        self.assertEqual(l["brano"], "")   # non nella libreria: in «Da controllare»
        eid = l["caricamento"]
        self.assertTrue(eid)
        self.assertNotIn("Stan", [b["title"] for b in self.catalogo()["brani"]])
        x = self.controllo()[eid]
        self.assertEqual((x["origine"], x["chi_nome"], x["tag"]["titolo"]), ("scaricato", "Franca", "Stan"))
        self.assertEqual(x["url"], "https://music.youtube.com/watch?v=Invitata001")
        mie = {y["id"]: y for y in self.miei(v)["brani"]}
        self.assertEqual((mie[eid]["origine"], mie[eid]["stato"]), ("scaricato", "attesa"))
        # la copia per il suo telefono
        st, h, b = self.da(v, "GET", "/api/extra/caricamenti/%s/file" % eid)
        self.assertEqual((st, b[:3]), (200, b"ID3"))
        self.assertIn("Stan", h["Content-Disposition"])
        # gli scaricamenti degli altri non si vedono e non si annullano
        self.assertNotIn(id_, [y["id"] for y in self.j(None, "GET", "/api/extra/musica")["lavori"]])
        mio = self.j(None, "POST", "/api/extra/musica", dict(brano, id="DiSamuele01"))["id"]
        self.assertNotIn(mio, [y["id"] for y in self.j(v, "GET", "/api/extra/musica")["lavori"]])
        self.j(v, "POST", "/api/extra/musica/%s/annulla" % mio, {}, 404)
        self.assertTrue(aspetta(lambda: {y["id"]: y for y in self.j(None, "GET", "/api/extra/musica")["lavori"]}[mio]["stato"] == "fatto", 40))
        # un brano già nella libreria non si scarica di nuovo per «Da controllare»
        self.assertIn("già nella libreria", self.j(v, "POST", "/api/extra/musica", dict(brano, id="DiSamuele01"), 409)["errore"])

    def test_6_film(self):
        pid, v = self.invitato("Gina")
        dati = b"FILM" + bytes(range(256)) * 40
        richiesta_ = {"tipo": "film", "titolo": "Il mio film: la vacanza", "nome": "vacanza.mp4", "dimensione": len(dati),
                      "modificato": 1700000000000}
        f = self.j(v, "POST", "/api/extra/caricamenti", richiesta_)
        self.assertEqual((f["stato"], f["tipo"]), ("richiesta", "film"))
        self.assertIn("consenso", self.pezzo(v, f["id"], 0, dati[:100], 409)["errore"])   # prima il sì di Samuele
        g = {x["id"]: x for x in self.j(None, "GET", "/api/extra/gestione/controllo")["film"]}
        self.assertEqual((g[f["id"]]["titolo"], g[f["id"]]["chi_nome"], g[f["id"]]["dimensione"]),
                         ("Il mio film: la vacanza", "Gina", len(dati)))
        self.assertGreaterEqual(self.j(None, "GET", "/api/extra/io")["nuove"]["film"], 1)
        self.j(v, "POST", "/api/extra/gestione/film/%s/consenti" % f["id"], {}, 403)
        self.assertEqual(self.j(None, "POST", "/api/extra/gestione/film/%s/consenti" % f["id"], {}), {"ok": True})
        self.assertEqual({x["id"]: x for x in self.miei(v)["caricamenti"]}[f["id"]]["stato"], "consentito")
        s = None
        for da in range(0, len(dati), 4000):
            s = self.pezzo(v, f["id"], da, dati[da:da + 4000])
        self.assertEqual((s["stato"], s["finale"]), ("fatto", "Il mio film la vacanza.mp4"), s)
        with open(os.path.join(self.c["film"], s["finale"]), "rb") as fh:
            self.assertEqual(fh.read(), dati)   # direttamente nel Cloud, senza passare da «Da controllare»
        self.assertEqual(os.listdir(os.path.join(self.c["film"], ".in arrivo")), [])
        # Samuele li vede in «Video»; gli invitati no
        el = {x["nome"]: x for x in self.j(None, "GET", "/api/extra/film")["film"]}
        self.assertEqual(el[s["finale"]]["chi_nome"], "Gina")
        st, h, b = self.da(None, "GET", "/api/extra/film/file/" + quote(s["finale"]), intest={"Range": "bytes=0-3"})
        self.assertEqual((st, b, h["Content-Type"]), (206, b"FILM", "video/mp4"))
        self.assertEqual(self.da(None, "GET", "/api/extra/film/file/..%2Fx.mp4")[0], 404)
        self.assertEqual(self.da(v, "GET", "/api/extra/film")[0], 403)
        # e non entrano nella libreria della musica
        self.j(None, "GET", "/api/musica/aggiorna")
        self.assertFalse([b for b in self.catalogo()["brani"] if "vacanza" in b["title"].lower()])
        # non è un video: si toglie
        muto = b"MUTO" + bytes(500)
        f2 = self.j(v, "POST", "/api/extra/caricamenti", dict(richiesta_, nome="muto.mp4", dimensione=len(muto), titolo="Muto"))
        self.j(None, "POST", "/api/extra/gestione/film/%s/consenti" % f2["id"], {})
        s2 = self.pezzo(v, f2["id"], 0, muto)
        self.assertEqual(s2["stato"], "errore")
        self.assertIn("non è un video", s2["errore"])
        f4 = self.j(v, "POST", "/api/extra/caricamenti", dict(richiesta_, nome="foto.mp4", dimensione=len(PNG), titolo="Foto"))
        self.j(None, "POST", "/api/extra/gestione/film/%s/consenti" % f4["id"], {})
        self.assertIn("non è un video", self.pezzo(v, f4["id"], 0, PNG)["errore"])   # una foto non è un film
        self.assertEqual(sorted(n for n in os.listdir(self.c["film"]) if not n.startswith(".")), [s["finale"]])
        # rifiutato: non si carica
        f3 = self.j(v, "POST", "/api/extra/caricamenti", dict(richiesta_, titolo="No", modificato=5))
        self.assertEqual(self.j(None, "POST", "/api/extra/gestione/film/%s/rifiuta" % f3["id"], {}), {"ok": True})
        self.assertEqual({x["id"]: x for x in self.miei(v)["caricamenti"]}[f3["id"]]["stato"], "rifiutato")
        self.pezzo(v, f3["id"], 0, dati[:10], 409)
        self.j(None, "POST", "/api/extra/gestione/film/%s/consenti" % f3["id"], {}, 409)
        # limiti
        self.assertIn("80 GB", self.j(v, "POST", "/api/extra/caricamenti", dict(richiesta_, dimensione=80 * 1024 ** 3 + 1), 413)["errore"])
        self.j(v, "POST", "/api/extra/caricamenti", dict(richiesta_, nome="film.mp3"), 400)
        self.j(v, "POST", "/api/extra/caricamenti", dict(richiesta_, titolo="  "), 400)
        # il film di Samuele non chiede il consenso a nessuno
        self.assertEqual(self.j(None, "POST", "/api/extra/caricamenti", dict(richiesta_, titolo="Di Samuele"))["stato"], "consentito")

    def test_7_contenuti_correzioni_togli_rimetti(self):
        orig = os.path.join(self.c["musica"], "Eminem - Without Me.mp3")
        with open(orig, "rb") as f:
            prima = f.read()
        r = self.j(None, "GET", "/api/extra/gestione/contenuti?q=" + quote("without EMINEM"))["brani"]
        b = [x for x in r if x["file"] == "Eminem - Without Me.mp3"][0]
        bid = b["id"]
        self.assertEqual((b["titolo"], b["corretto"]), ("Without Me", False))
        self.j(None, "POST", "/api/extra/gestione/contenuti/" + bid, {"titolo": "Without Me (Corretto)", "album": ""})
        self.j(None, "PUT", "/api/extra/gestione/contenuti/%s/copertina" % bid, PNG, intest={"Content-Type": "image/png"})
        c = {x["id"]: x for x in self.catalogo()["brani"]}[bid]
        self.assertEqual((c["title"], c["artist"], c["album"]), ("Without Me (Corretto)", "Eminem", "The Eminem Show"))
        self.assertTrue(c["cover"])
        self.assertEqual(self.da(None, "GET", "/api/musica/copertina/" + c["cover"])[2], PNG)
        with open(orig, "rb") as f:
            self.assertEqual(f.read(), prima)   # il file originale non cambia
        with open(os.path.join(self.c["cache"], "correzioni.json"), encoding="utf-8") as f:
            self.assertIn(bid, json.load(f)["brani"])
        x = [y for y in self.j(None, "GET", "/api/extra/gestione/contenuti?q=corretto")["brani"] if y["id"] == bid][0]
        self.assertEqual((x["corretto"], x["originale"]["titolo"]), (True, "Without Me"))
        # riscrivere il valore del file non è una correzione
        r = self.j(None, "POST", "/api/extra/gestione/contenuti/" + bid, {"titolo": "Without Me", "artista": "Eminem"})["brano"]
        self.assertEqual((r["titolo"], r["corretto"]), ("Without Me", True))   # resta la copertina nuova
        self.j(None, "POST", "/api/extra/gestione/contenuti/" + bid, {"titolo": "Without Me (Corretto)"})
        self.j(None, "POST", "/api/extra/gestione/contenuti/%s/ripristina" % bid, {})
        c = {x["id"]: x for x in self.catalogo()["brani"]}[bid]
        self.assertEqual((c["title"], c.get("cover")), ("Without Me", None))
        self.j(None, "POST", "/api/extra/gestione/contenuti/nonesiste12345/ripristina", {}, 404)
        # Togli: sparisce dall'app di tutti, il file va in «Tolti»; Rimetti lo riporta con lo stesso id
        t = self.j(None, "POST", "/api/extra/gestione/contenuti/%s/togli" % bid, {})
        cat = self.catalogo()
        self.assertNotIn(bid, [x["id"] for x in cat["brani"]])
        self.assertIn(bid, cat["tolti"])
        self.assertFalse(os.path.exists(orig))
        tolti = self.j(None, "GET", "/api/extra/gestione/contenuti/tolti")["tolti"]
        self.assertEqual([(x["id"], x["titolo"]) for x in tolti], [(t["tolto"], "Without Me")])
        self.assertTrue(os.path.isfile(tolti[0]["dove"]) and tolti[0]["dove"].startswith(self.c["tolti"]))
        self.j(None, "GET", "/api/musica/aggiorna")
        self.assertNotIn(bid, [x["id"] for x in self.catalogo()["brani"]])   # una rilettura del disco non lo fa tornare
        r = self.j(None, "POST", "/api/extra/gestione/contenuti/tolti/%s/rimetti" % t["tolto"], {})
        self.assertEqual(r["brano"], bid)
        cat = self.catalogo()
        self.assertIn(bid, [x["id"] for x in cat["brani"]])
        self.assertNotIn(bid, cat.get("tolti", []))
        with open(orig, "rb") as f:
            self.assertEqual(f.read(), prima)
        self.j(None, "POST", "/api/extra/gestione/contenuti/tolti/%s/rimetti" % t["tolto"], {}, 404)

    def test_8_pagine_e_numeri(self):
        pid, v = self.invitato("Ivo")
        for f in ("x/carica.html", "x/cerca.html", "x/controllo.html", "x/contenuti.html"):
            with open(os.path.join(self.c["app"], f), "w", encoding="utf-8") as fh:
                fh.write("<!doctype html><p>ok")
        self.assertEqual(self.da(v, "GET", "/x/carica.html")[0], 200)
        self.assertEqual(self.da(v, "GET", "/x/cerca.html")[0], 200)
        self.assertEqual(self.da(v, "GET", "/x/controllo.html")[0], 403)
        self.assertEqual(self.da(v, "GET", "/x/contenuti.html")[0], 403)
        self.carica(v, "ivo.mp3", mp3_finto("Di Ivo", "Ivo"))
        io = self.j(None, "GET", "/api/extra/io")
        self.assertGreaterEqual(io["nuove"]["da_controllare"], 1)
        self.assertNotIn("nuove", self.j(v, "GET", "/api/extra/io"))
        # i caricamenti di Samuele entrano subito nella libreria (è lui che controlla)
        s = self.carica(None, "mio.mp3", mp3_finto("Di Samuele", "Samuele"))
        self.assertEqual(s["stato"], "fatto")
        self.assertIn("Di Samuele", [b["title"] for b in self.catalogo()["brani"]])
        self.assertIsNone(self.miei(None)["oggi"])                            # e non ha il limite giornaliero
        self.assertEqual(self.miei(v)["oggi"]["limite"], 1024 ** 3)


class TestCaricamentiLimiti(ProvaCaricamenti, unittest.TestCase):
    """Limite giornaliero per persona (qui piccolissimo) e disco quasi pieno."""
    OPZIONI = ["--spazio-min-gb", "0", "--limite-giorno-gb", "0.0001"]   # circa 107 KB al giorno

    @classmethod
    def setUpClass(cls):
        cls.prepara("atlante-limiti-")

    @classmethod
    def tearDownClass(cls):
        cls.chiudi()

    def test_limite_giornaliero(self):
        pid, v = self.invitato("Lia")
        dati = mp3_finto("Lungo", "Lia", fotogrammi=200)   # circa 83 KB
        s = self.carica(v, "lungo.mp3", dati, pezzi=1)
        self.assertEqual(s["stato"], "fatto")
        s = self.j(v, "POST", "/api/extra/caricamenti", {"tipo": "brano", "nome": "altro.mp3", "dimensione": len(dati)})
        r = self.pezzo(v, s["id"], 0, dati, 429)
        self.assertIn("di oggi: si riprende domani", r["errore"])
        self.assertEqual(self.j(v, "GET", "/api/extra/caricamenti/" + s["id"])["ricevuti"], 0)
        oggi = self.miei(v)["oggi"]
        self.assertEqual((oggi["usati"], oggi["limite"]), (len(dati), int(0.0001 * 1024 ** 3)))
        # anche «Cerca e scarica» conta: oltre il limite non parte
        self.assertIn("di oggi", self.j(v, "POST", "/api/extra/musica", {"id": "Limite00001"}, 429)["errore"])
        # un'altra persona ha il suo limite; Samuele non ne ha
        _, v2 = self.invitato("Mara")
        self.assertEqual(self.carica(v2, "lungo.mp3", dati)["stato"], "fatto")
        self.assertEqual(self.carica(None, "a.mp3", dati)["stato"], "fatto")
        self.assertEqual(self.carica(None, "b.mp3", mp3_finto("Lungo 2", "Lia", fotogrammi=200))["stato"], "fatto")

    def test_disco_quasi_pieno(self):
        # «--spazio-min-gb» enorme: il disco di prova è «quasi pieno»
        s = Server(self.base, self.c, ["--pubblico", ORIGINE_APP, "--spazio-min-gb", "100000000"]).avvia()
        try:
            r = richiesta(s.porta, "POST", "/api/extra/caricamenti", {"tipo": "brano", "nome": "a.mp3", "dimensione": 100})
            self.assertEqual(r[0], 507, r[2])
            self.assertIn("liberi", json.loads(r[2].decode("utf-8"))["errore"])
            st, h, b = richiesta(s.porta, "POST", "/api/extra/caricamenti",
                                 {"tipo": "film", "titolo": "Grande", "nome": "g.mp4", "dimensione": 1000})
            self.assertEqual(st, 507, b)   # Samuele: il suo film parte subito, ma lo spazio non c'è
        finally:
            s.ferma()


class TestQuote(unittest.TestCase):
    """Limite giornaliero per persona: brani, film e (in futuro) allegati della chat contano insieme."""

    @classmethod
    def setUpClass(cls):
        sys.dont_write_bytecode = True
        spec = importlib.util.spec_from_file_location("musica_libreria_quote", SERVER)
        cls.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.m)

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="atlante-quote-")
        self.file = os.path.join(self.base, "extra", "quote.json")

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def test_limite_e_giorno_dopo(self):
        q = self.m.Quote(self.file, 100)
        q.consuma("anna", 60)
        q.consuma("anna", 40)
        with self.assertRaises(self.m.ErroreExtra) as e:
            q.consuma("anna", 1)
        self.assertEqual(e.exception.codice, 429)
        self.assertIn("si riprende domani", e.exception.testo)
        q.consuma("bruno", 100)
        self.assertEqual((q.usati("anna"), q.resta("anna")), (100, 0))
        self.assertEqual(os.stat(self.file).st_mode & 0o777, 0o600)
        self.assertEqual(self.m.Quote(self.file, 100).usati("anna"), 100)   # resta dopo un riavvio
        with unittest.mock.patch.object(self.m.time, "time", return_value=time.time() + 86400):
            q.consuma("anna", 100)   # il giorno dopo si riparte
            self.assertEqual(q.usati("anna"), 100)
        q.consuma(None, 10 ** 12)    # Samuele: nessun limite

    def test_formato_del_brano_approvato(self):
        # MP3, M4A e FLAC: solo i tag nuovi; WAV e AIFF in FLAC (senza perdita); il resto in MP3 come il Cloud
        for formato, atteso in (("mp3", ("copia", ".mp3", "mp3")), ("mov,mp4,m4a,3gp,3g2,mj2", ("copia", ".m4a", "ipod")),
                                ("aac", ("copia", ".m4a", "ipod")), ("flac", ("copia", ".flac", "flac")),
                                ("wav", ("flac", ".flac", "flac")), ("aiff", ("flac", ".flac", "flac")),
                                ("ogg", ("mp3", ".mp3", "mp3")), ("", ("mp3", ".mp3", "mp3"))):
            self.assertEqual(self.m._uscita(formato), atteso, formato)


# ----- playlist di Atlante (tematiche) e playlist con l'IA -----

# audio finto come quello di un brano: un colpo forte su ogni battito e uno più debole a metà (le crome), su un fruscio.
# Lo usano sia queste prove sia l'ffmpeg finto qui sotto.
SINTESI = r'''
import array, math, random, struct, sys

def sintesi(bpm, ampiezza, rumore, secondi, da=0.0, hz=11025, seme=1):
    n = max(0, int(secondi * hz))
    x = array.array("h", bytes(2 * n))
    r = random.Random(seme)
    if bpm:
        passo = 30.0 / bpm
        k = int(math.ceil(da / passo))
        while k * passo - da < secondi:
            j = int((k * passo - da) * hz)
            a = ampiezza if k % 2 == 0 else ampiezza * 0.5
            for m in range(min(400, n - j)):
                x[j + m] = max(-32767, min(32767, x[j + m] + int(a * 32767 * (1 - m / 400.0) * r.uniform(-1, 1))))
            k += 1
    if rumore:
        for j in range(n):
            x[j] = max(-32767, min(32767, x[j] + int(rumore * 32767 * r.uniform(-1, 1))))
    return x

def wav(x, hz=11025):
    if sys.byteorder == "big":
        x = array.array("h", x)
        x.byteswap()
    d = x.tobytes()
    return (b"RIFF" + struct.pack("<I", 36 + len(d)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, hz, hz * 2, 2, 16)
            + b"data" + struct.pack("<I", len(d)) + d)
'''
exec(SINTESI)

# ffmpeg finto per le misure dell'audio: al posto del brano decodificato (WAV mono su pipe:1) l'audio di SINTESI con
# i valori scritti accanto al file (<brano>.finto.json: bpm, ampiezza, rumore, durata)
FINTO_FFMPEG_AUDIO = SINTESI + r'''
import json, os
a = sys.argv[1:]
if a[-1:] != ["pipe:1"] or "-i" not in a:
    sys.stderr.write("solo audio su pipe:1 in queste prove\n")
    sys.exit(1)
ingresso = a[a.index("-i") + 1]
da = float(a[a.index("-ss") + 1]) if "-ss" in a else 0.0
t = float(a[a.index("-t") + 1]) if "-t" in a else 30.0
with open(os.path.join(os.environ["FINTO_DIR"], "ffmpeg-audio.log"), "a") as f:
    f.write(json.dumps({"i": os.path.basename(ingresso), "da": da}) + "\n")
try:
    with open(ingresso + ".finto.json") as f:
        c = json.load(f)
except (OSError, ValueError):
    c = {}
t = min(t, c.get("durata", 600) - da)
if t <= 0:
    sys.exit(0)
sys.stdout.buffer.write(wav(sintesi(c.get("bpm", 0), c.get("ampiezza", 0.3), c.get("rumore", 0.01), t, da)))
'''


def mp3_finto_tematiche(percorso, titolo, artista, secondi, anno="", genere="", album=""):
    """MP3 minimo con i tag ID3 e un'intestazione Xing che dice la durata (la libreria la legge da lì)."""
    import struct

    def frame(fid, testo):
        d = b"\x01" + testo.encode("utf-16")
        return fid.encode() + struct.pack(">I", len(d)) + b"\x00\x00" + d
    corpo = b"".join(frame(f, v) for f, v in (("TIT2", titolo), ("TPE1", artista), ("TALB", album), ("TYER", anno),
                                               ("TCON", genere)) if v)
    n = len(corpo)
    testa = b"ID3\x03\x00\x00" + bytes([(n >> 21) & 127, (n >> 14) & 127, (n >> 7) & 127, n & 127]) + corpo
    xing = b"\xff\xfb\x90\x00" + bytes(32) + b"Xing" + struct.pack(">II", 1, int(secondi * 44100 / 1152))
    with open(percorso, "wb") as f:
        f.write(testa + xing + bytes(417 - len(xing)) + (b"\xff\xfb\x90\x00" + bytes(413)) * 3)


def _mb_reg(rid, titolo, artista, aid, data, tag, score=100):
    return {"id": rid, "score": score, "title": titolo, "first-release-date": data,
            "artist-credit": [{"name": artista, "artist": {"id": aid, "name": artista}}],
            "tags": [{"name": t, "count": 2} for t in tag], "releases": [{"id": "rel-" + rid, "title": "Album",
                                                                          "release-group": {"id": "rg-" + rid}}]}


class FintoMusicBrainz(http.server.BaseHTTPRequestHandler):
    """MusicBrainz finto: ricerca delle registrazioni e schede degli artisti, con l'ora e lo User-Agent di ogni richiesta."""
    richieste = []
    REGISTRAZIONI = {
        ("vasco rossi", "albachiara"): [
            _mb_reg("rec-vasco", "Albachiara", "Vasco Rossi", "art-vasco", "1979-04-10", ["rock"]),
            _mb_reg("rec-vasco-live", "Albachiara (live)", "Vasco Rossi", "art-vasco", "1990", [], 90),
            _mb_reg("rec-altro", "Albachiara", "Cover Band", "art-cover", "1975", ["metal"], 95)],
        ("vasco rossi", "vita spericolata"): [
            _mb_reg("rec-vasco2", "Vita spericolata", "Vasco Rossi", "art-vasco", "1983", ["rock"])],
        ("zara larsson", "ain't my fault"): [
            _mb_reg("rec-zara", "Ain't My Fault", "Zara Larsson", "art-zara", "2016-10-14", ["dance-pop", "electropop"])],
        ("mariah carey", "all i want for christmas is you"): [
            _mb_reg("rec-mariah", "All I Want for Christmas Is You", "Mariah Carey", "art-mariah", "1994-10-29",
                    ["christmas", "pop"])],
        ("nuovo artista", "brano nuovo"): [
            _mb_reg("rec-nuovo", "Brano nuovo", "Nuovo Artista", "art-nuovo", "2021", ["heavy metal"])],
        ("senza genere", "brano spoglio"): [
            _mb_reg("rec-spoglio", "Brano spoglio", "Senza Genere", "art-spoglio", "2001", [])]}
    ARTISTI = {"art-vasco": {"country": "IT", "genres": [{"name": "rock", "count": 3}, {"name": "pop rock", "count": 1}],
                             "tags": [{"name": "italian", "count": 2}]},
               "art-zara": {"country": "SE", "genres": [{"name": "pop", "count": 4}]},
               "art-mariah": {"country": "US", "genres": [{"name": "pop", "count": 4}, {"name": "r&b", "count": 3}]},
               "art-nuovo": {"area": {"iso-3166-1-codes": ["DE"]}},
               "art-spoglio": {"country": "FR"}}
    GRUPPI = {"rg-rec-spoglio": {"genres": [{"name": "synth-pop", "count": 2}]}}

    def do_GET(self):
        u = urlsplit(self.path)
        FintoMusicBrainz.richieste.append((time.time(), self.path, self.headers.get("User-Agent") or ""))
        q = {k: v[0] for k, v in __import__("urllib.parse").parse.parse_qs(u.query).items()}
        dati = None
        if u.path == "/ws/2/recording":
            m = re.match(r'^recording:"((?:[^"\\]|\\.)*)" AND artist:"((?:[^"\\]|\\.)*)"$', q.get("query", ""))
            if m and q.get("fmt") == "json":
                su = lambda s: re.sub(r"\\(.)", r"\1", s).lower()   # noqa: E731
                dati = {"recordings": self.REGISTRAZIONI.get((su(m.group(2)), su(m.group(1))), [])}
        elif u.path.startswith("/ws/2/artist/"):
            dati = self.ARTISTI.get(u.path.rsplit("/", 1)[1])
        elif u.path.startswith("/ws/2/release-group/"):
            dati = self.GRUPPI.get(u.path.rsplit("/", 1)[1], {})
        corpo = json.dumps(dati if dati is not None else {"error": "Not Found"}).encode("utf-8")
        self.send_response(200 if dati is not None else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def log_message(self, *x):
        pass


class FintaOllama(http.server.BaseHTTPRequestHandler):
    """Ollama finto che risponde (quello delle altre prove è una porta dove non ascolta nessuno): genere dei brani che
    MusicBrainz non conosce e criteri delle playlist, scelti dalle parole della richiesta."""
    richieste, insieme, massimo = [], 0, 0
    blocco = threading.Lock()

    def do_GET(self):
        self._rispondi({"models": [{"name": "llama3.2:latest"}]} if self.path == "/api/tags" else {"error": "?"})

    def do_POST(self):
        d = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        p = d.get("prompt", "")
        with FintaOllama.blocco:
            FintaOllama.richieste.append(p)
            FintaOllama.insieme += 1
            FintaOllama.massimo = max(FintaOllama.massimo, FintaOllama.insieme)
        try:
            if p.startswith("Brano:"):
                r = {"generi": ["pop", "genere inventato"], "italiano": True}
            else:
                richiesta = re.match(r"Richiesta: «(.*)»", p).group(1)
                if "lento" in richiesta:
                    time.sleep(1.5)
                if "rotto" in richiesta:
                    r = "Ecco a te una bella playlist!"
                elif "assurdo" in richiesta:
                    r = {"nome": "<b>x</b>" * 40, "generi": ["rap", "zzz", "Hip Hop"], "bpm": [0, 1000],
                         "anni": [3000, 1200], "durata_minuti": 99999, "italiano": "sì", "energia": "altissima",
                         "brani": ["Brano inventato - Nessuno"], "artisti": "Vasco Rossi"}
                elif "salita" in richiesta:
                    r = {"nome": "Salita pop", "generi": ["pop"], "bpm": [140, 180], "energia": "alta",
                         "attivita": ["handbike: salita e scatti"], "durata_minuti": 40}
                else:
                    r = {"nome": "Prova", "generi": ["pop"]}
            self._rispondi({"response": r if isinstance(r, str) else json.dumps(r), "done": True})
        finally:
            with FintaOllama.blocco:
                FintaOllama.insieme -= 1

    def _rispondi(self, o):
        corpo = json.dumps(o).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def log_message(self, *x):
        pass


def carica_modulo(nome):
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location(nome, SERVER)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class FintaLibreria:
    """Quanto basta di Libreria per le playlist: brani del catalogo (con le chiavi interne _p e _f)."""

    def __init__(self, brani):
        self.brani = {b["id"]: b for b in brani}
        self.blocco = threading.Lock()
        self.ultima, self.letture = 1.0, 1


def voce(i, titolo, artista, anno="", genere="", durata=200, added=0, album="", cover=None):
    return {"id": i, "title": titolo, "artist": artista, "album": album, "year": anno, "genre": genere,
            "duration": durata, "added": added, "cover": cover, "_p": "/x/" + i + ".mp3", "_f": "1:1"}


def scheda(m, **k):
    s = {"v": m.TEM_VERSIONE, "mb": "fatto", "audio": "fatto", "ia": "fatto", "f": "1:1", "generi": [], "etichette": [],
         "italiano": None, "anno": None, "bpm": None, "energia": None, "regolarita": None}
    s.update(k)
    return s


class TestRegoleAtlante(unittest.TestCase):
    """Le regole delle playlist di Atlante su brani con dati finti, le modifiche di Samuele e i criteri dell'IA."""

    @classmethod
    def setUpClass(cls):
        cls.m = carica_modulo("musica_libreria_tematiche")

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="atlante-tematiche-")
        m = self.m
        anno = time.localtime().tm_year
        m.LIB = FintaLibreria([
            voce("a", "Ain't My Fault", "Zara Larsson", added=5, cover="z.jpg"),
            voce("b", "Albachiara", "Vasco Rossi", anno="2004", added=4, cover="v.jpg"),
            voce("c", "Last Christmas", "Wham!", anno="1984", added=3),   # senza scheda: solo catalogo
            voce("d", "Brano di quest'anno", "Qualcuno", anno=str(anno), added=2, genere="Hip-Hop/Rap"),
            voce("e", "Lento", "Calmo", added=1)])
        self.t = m.Tematiche(os.path.join(self.base, "tematiche"))
        self.t.schede = {
            "a": scheda(m, generi=["pop", "dance"], italiano=False, anno=2016, bpm=150.2, energia=0.85, regolarita=0.9),
            "b": scheda(m, generi=["rock"], italiano=True, anno=1979, bpm=120.0, energia=0.55, regolarita=0.7),
            "e": scheda(m, mb="sconosciuto", generi_ia=["classica"], italiano_ia=False, bpm=70.0, energia=0.12,
                        regolarita=0.3)}

    def tearDown(self):
        self.m.LIB = None
        shutil.rmtree(self.base, ignore_errors=True)

    def playlist(self):
        el = self.t.elenco("p")
        return {p["id"]: p for g in el["gruppi"] for p in g["playlist"]}, [g["id"] for g in el["gruppi"]]

    def test_regole_predefinite(self):
        gruppi, nomi, errori = self.m.regole_atlante()
        self.assertEqual(errori, [])
        self.assertEqual([g["id"] for g in gruppi], ["generi", "umore", "attivita", "epoche", "momenti"])
        conta = {g["id"]: len(g["playlist"]) for g in gruppi}
        self.assertEqual((conta["generi"], conta["umore"], conta["attivita"]), (30, 12, 12))
        self.assertEqual(set(nomi), set(self.m.GENERI))   # i generi del file sono quelli del programma
        pl = {p["id"]: p for g in gruppi for p in g["playlist"]}
        self.assertEqual(pl["attivita-handbike-salita"]["nome"], "Handbike: salita e scatti")
        self.assertEqual(pl["attivita-handbike-salita"]["regola"]["bpm"], [140, 180])
        self.assertGreaterEqual(pl["attivita-handbike-salita"]["regola"]["energia"][0], 0.7)
        self.assertEqual(pl["attivita-handbike-ritmo"]["nome"], "Handbike: ritmo costante")
        self.assertIn("regolarita", pl["attivita-handbike-ritmo"]["regola"])
        self.assertEqual(len([p for p in pl if p.startswith("epoca-")]), 8)
        self.assertEqual(len([p for p in pl if p.startswith("momento-")]), 6)

    def test_un_brano_in_piu_playlist_e_vuote_nascoste(self):
        pl, gruppi = self.playlist()
        dove = lambda i: {p for p in pl if i in self.t.playlist(p, "p")["brani"]}   # noqa: E731
        self.assertTrue({"genere-pop", "genere-dance", "attivita-handbike-salita", "epoca-2010", "umore-energia"}
                        <= dove("a"), dove("a"))
        self.assertNotIn("genere-pop-italiano", dove("a"))
        self.assertTrue({"genere-rock", "genere-rock-italiano", "epoca-70", "umore-nostalgia"} <= dove("b"), dove("b"))
        self.assertNotIn("epoca-2000", dove("b"))   # 2004 è l'anno della raccolta: conta l'uscita originale
        # senza scheda (MusicBrainz e audio non ancora fatti) valgono già titolo e anno dei tag
        self.assertEqual(dove("c"), {"epoca-80", "momento-natale", "momento-inverno", "umore-nostalgia"})
        self.assertTrue({"genere-rap", "epoca-anno", "epoca-2020"} <= dove("d"), dove("d"))
        self.assertTrue({"genere-classica", "umore-calma", "attivita-sonno"} <= dove("e"), dove("e"))   # genere dall'IA
        # le playlist vuote non compaiono, e neanche un gruppo tutto vuoto
        for vuota in ("genere-metal", "genere-kpop", "epoca-60", "momento-estate"):
            self.assertNotIn(vuota, pl)
            self.assertIsNone(self.t.playlist(vuota, "p"))
        self.assertEqual(pl["attivita-handbike-salita"]["n"], 1)   # anche con un brano solo
        self.assertEqual(pl["genere-pop"]["copertine"], ["z.jpg"])
        self.t.schede = {}
        self.t.versione += 1
        self.t._cache = None
        pl, gruppi = self.playlist()
        self.assertNotIn("attivita", gruppi)   # senza misure dell'audio nessuna attività

    def test_regole_ritoccate(self):
        with open(os.path.join(self.base, "tematiche", "regole.json"), "w", encoding="utf-8") as f:
            json.dump({"gruppi": [
                {"id": "attivita", "playlist": [
                    {"id": "attivita-handbike-salita", "regola": {"bpm": [100, 125]}},
                    {"id": "attivita-mia", "nome": "La mia salita", "regola": {"bpm": [149, 151]}},
                    {"id": "attivita-rotta", "nome": "Rotta", "regola": {"bpm": [100], "colore": "blu"}}]},
                {"id": "generi", "playlist": [{"id": "genere-pop", "nascosta": True}]}]}, f)
        pl, _ = self.playlist()
        self.assertEqual(self.t.playlist("attivita-handbike-salita", "p")["brani"], ["b"])
        self.assertEqual(pl["attivita-handbike-salita"]["nome"], "Handbike: salita e scatti")   # il nome resta
        self.assertEqual(self.t.playlist("attivita-mia", "p")["brani"], ["a"])
        self.assertNotIn("genere-pop", pl)
        self.assertNotIn("attivita-rotta", pl)   # una regola sbagliata si salta, le altre restano
        errori = self.t.elenco("p")["stato"]["errori_regole"]
        self.assertTrue(any("Rotta" in e and "colore" in e for e in errori), errori)
        self.assertIn("genere-rock", pl)

    def test_prova_su_un_campione(self):
        # ATLANTE_TEMATICHE_PROVA=2: il lavoro in sottofondo fa solo due brani (sempre gli stessi), da controllare a mano
        self.t.prova = 2
        x = self.t._prossimo()
        self.assertIn(x[0], ("a", "b"))
        for i in ("a", "b"):
            self.t.schede[i].update(t=self.m.LIB.brani[i]["title"], a=self.m.LIB.brani[i]["artist"])
        self.assertIsNone(self.t._prossimo())   # c, d, e restano da fare, ma sono fuori dalla prova
        self.assertEqual(self.t.stato()["totale"], 2)
        righe = {r["titolo"]: r for r in self.t.campione()}
        self.assertEqual(set(righe), {"Ain't My Fault", "Albachiara"})
        z = righe["Ain't My Fault"]
        self.assertEqual((z["artista"], z["bpm"], z["energia"], z["italiano"], z["fonte"]),
                         ("Zara Larsson", 150.2, 0.85, False, "musicbrainz"))
        self.assertIn("Handbike: salita e scatti", z["playlist"])

    def test_niente_giri_a_vuoto(self):
        # senza ffmpeg, con l'IA spenta o con un errore imprevisto il lavoro non riprova subito lo stesso brano
        m, t = self.m, self.t
        t.schede = {}
        with unittest.mock.patch.object(t.mb, "brano", lambda *a: None), \
                unittest.mock.patch.object(m, "_misura_in_processo", lambda p, d: {"senza_ffmpeg": True}), \
                unittest.mock.patch.object(m, "_chiedi_ia", side_effect=m.ErroreExtra(503, "IA spenta")):
            for _ in range(5):
                t.un_brano(*t._prossimo())
            self.assertIsNone(t._prossimo())
        self.assertEqual({s["audio"] for s in t.schede.values()}, {"attesa"})
        self.assertEqual({s.get("ia") for i, s in t.schede.items() if i != "c"}, {"attesa"})
        t.schede = {}
        with unittest.mock.patch.object(t.mb, "brano", side_effect=KeyError("imprevisto")):
            for _ in range(5):
                t.un_brano(*t._prossimo())
            self.assertIsNone(t._prossimo())
            with unittest.mock.patch.object(m.time, "time", return_value=time.time() + m.TEM_RIPROVA + 1):
                self.assertIsNotNone(t._prossimo())   # dopo qualche ora si riprova

    def test_ritmi_e_buonumore(self):
        # per «Per ritmo» (anche senza rete, nell'app) e «Mettimi di buonumore»: i dati dell'audio di ogni brano misurato
        self.t.schede["a"]["modo"] = 1
        r = self.t.ritmi()
        self.assertEqual(r["ritmi"]["a"], [150.2, 0.9, 0.85, 1])
        self.assertEqual(set(r["ritmi"]), {"a", "b", "e"})   # c e d non hanno ancora l'audio misurato
        self.assertEqual(dict((i, p) for i, p in r["buonumore"]), {"a": 2})   # Energia e Buonumore; modo maggiore pesa di più
        self.t.schede["e"]["modo"] = -1
        self.t.versione += 1
        pl = {p["id"] for g in self.t.elenco("p")["gruppi"] for p in g["playlist"]}
        with open(os.path.join(self.base, "tematiche", "regole.json"), "w", encoding="utf-8") as f:
            json.dump({"gruppi": [{"id": "umore", "playlist": [{"id": "umore-minore", "nome": "In minore",
                                                                 "regola": {"modo": "minore"}}]}]}, f)
        self.assertEqual(self.t.playlist("umore-minore", "p")["brani"], ["e"])
        self.assertTrue(pl)

    def test_generi_dalle_etichette(self):
        g = self.m._generi_da_testo
        self.assertEqual(g("Hip-Hop/Rap"), {"rap"})
        self.assertEqual(g("trap italiana"), {"trap", "rap"})
        self.assertEqual(g("dance-pop"), {"dance", "pop"})
        self.assertEqual(g("R&B"), {"rnb"})
        self.assertEqual(g("K-Pop"), {"kpop", "pop"})
        self.assertEqual(g("Deep House"), {"house", "elettronica"})
        self.assertEqual(g("Colonne sonore"), {"classica"})
        self.assertEqual(g("dancehall"), {"reggae"})
        self.assertEqual(g("christmas"), set())

    def test_criteri_ia_e_valori_assurdi(self):
        m = self.m
        gruppi, nomi, _ = m.regole_atlante()
        c = m.criteri_da_ia(json.dumps({"nome": "<b>x</b>" * 40, "generi": ["rap", "zzz", "Hip Hop", "Reggaeton e latino"],
                                        "bpm": [0, 1000], "anni": [3000, 1200], "durata_minuti": 99999,
                                        "italiano": "sì", "energia": "altissima", "brani": ["Brano inventato"],
                                        "attivita": ["handbike: salita e scatti", "volare"], "artisti": "Vasco Rossi"}),
                            "x", gruppi, nomi)
        self.assertEqual(c["generi"], ["rap", "latino"])
        self.assertIsNone(c["bpm"])      # da 0 a 1000: nessun limite vero
        self.assertIsNone(c["anni"])
        self.assertEqual(c["durata_minuti"], 240)
        self.assertIs(c["italiano"], True)
        self.assertIsNone(c["energia"])
        self.assertEqual(c["temi"], ["attivita-handbike-salita"])
        self.assertEqual(c["artisti"], ["Vasco Rossi"])
        self.assertLessEqual(len(c["nome"]), 60)
        self.assertNotIn("brani", c)     # l'IA non sceglie brani
        c = m.criteri_da_ia('Ecco: {"bpm": [180, 140], "anni": [1985, 2999], "durata_minuti": -5, "energia": "Bassa"} ok',
                            "musica calma per cena", gruppi, nomi)
        self.assertEqual((c["bpm"], c["anni"], c["durata_minuti"], c["energia"]),
                         ([140, 180], [1985, time.localtime().tm_year], 60, [0.0, 0.45]))
        self.assertEqual(c["nome"], "Musica calma per cena")
        with self.assertRaises(m.ErroreExtra) as e:
            m.criteri_da_ia("non so", "x", gruppi, nomi)
        self.assertEqual(e.exception.codice, 502)

    def test_scelta_dei_brani(self):
        import random
        m = self.m
        anno = time.localtime().tm_year
        dati = []
        for i in range(60):
            d = m._dati_brano("r%02d" % i, voce("r%02d" % i, "Rap %d" % i, "Rapper %d" % (i % 10), durata=180),
                              scheda(m, generi=["rap"], italiano=i % 2 == 0, bpm=100.0 + i, energia=0.5 + i / 200), anno)
            dati.append(d)
        c = {"nome": "x", "generi": ["rap"], "escludi_generi": [], "temi": [], "bpm": [140, 180], "anni": None,
             "artisti": [], "italiano": True, "energia": None, "durata_minuti": 20}
        r = m.scegli_brani(c, dati, [], random.Random(1))
        scelti = [d for d in dati if d["id"] in r["brani"]]
        self.assertTrue(scelti)
        self.assertTrue(all(d["italiano"] and 140 <= d["bpm"] <= 180 for d in scelti))
        self.assertGreaterEqual(r["durata"], 9 * 60)   # 10 brani italiani tra 140 e 159 BPM: tutti
        self.assertLessEqual(max(collections.Counter(d["artista"] for d in scelti).values()), 3)
        c.update(bpm=[170, 175], durata_minuti=60)   # nessuno: si allarga con misura e lo si dice
        r = m.scegli_brani(c, dati, [], random.Random(1))
        self.assertEqual(sorted(r["brani"]), ["r56", "r58"])
        self.assertIn("Pochi brani con questi BPM: ho allargato a 155–190", r["note"])
        self.assertTrue(any("minuti" in n for n in r["note"]), r["note"])
        c.update(bpm=[195, 200])   # più veloci di tutti: niente brani lenti al loro posto
        self.assertEqual(m.scegli_brani(c, dati, [], random.Random(1))["brani"], [])
        senza = [dict(d, bpm=None, energia=None) for d in dati]   # audio non ancora misurato: BPM ignorati
        r = m.scegli_brani(c, senza, [], random.Random(1))
        self.assertTrue(r["brani"])
        self.assertTrue(any("non hanno ancora BPM" in n for n in r["note"]), r["note"])
        c.update(generi=["metal"], bpm=None)
        r = m.scegli_brani(c, dati, [], random.Random(1))
        self.assertEqual(r["brani"], [])
        self.assertIn("Nella libreria non ho trovato brani così", r["note"])
        c.update(generi=[], artisti=["Rapper 3", "Nessuno Mai"], italiano=None)
        r = m.scegli_brani(c, dati, [], random.Random(1))
        self.assertTrue(r["brani"] and all(d["artista"] == "rapper 3" for d in dati if d["id"] in r["brani"]))
        self.assertTrue(any("Nessuno Mai" in n for n in r["note"]), r["note"])

    def test_su_misura(self):
        import random
        m = self.m
        anno = time.localtime().tm_year
        dati = [m._dati_brano("p%d" % i, voce("p%d" % i, "Pop %d" % i, "Pop %d" % (i % 8)),
                              scheda(m, generi=["pop"], energia=0.7), anno) for i in range(30)]
        dati += [m._dati_brano("j%d" % i, voce("j%d" % i, "Jazz %d" % i, "Jazz %d" % i),
                               scheda(m, generi=["jazz"], energia=0.2), anno) for i in range(30)]
        r = m.su_misura([{"id": "p1", "n": 30}, {"id": "p2", "n": 5, "fav": True}, {"id": "inventato", "n": 99}],
                        dati, random.Random(2))
        scelti = set(r["brani"])
        self.assertTrue(scelti and all(i.startswith("p") for i in scelti), scelti)
        self.assertTrue(scelti - {"p1", "p2"})   # anche brani che non ha ancora ascoltato
        self.assertEqual(r["generi"], ["pop"])
        with self.assertRaises(m.ErroreExtra) as e:
            m.su_misura([], dati, random.Random(2))
        self.assertEqual(e.exception.codice, 409)


class TestMisuraAudio(unittest.TestCase):
    """BPM, energia e regolarità misurati sull'audio (beatGrid dell'app portato in Python), su audio finto."""

    @classmethod
    def setUpClass(cls):
        cls.m = carica_modulo("musica_libreria_audio")

    def misura(self, bpm, ampiezza, rumore, secondi=30.0, da=0.0):
        x = [v / 32768.0 for v in sintesi(bpm, ampiezza, rumore, secondi, da)]
        return self.m.griglia_battiti(x, 11025)

    def test_griglia_battiti(self):
        for bpm in (150, 128, 95):
            g = self.misura(bpm, 0.8, 0.05)
            self.assertAlmostEqual(g["bpm"], bpm, delta=1.0, msg=g)
            self.assertGreater(g["c"], 3, g)
        self.assertIsNone(self.misura(120, 0.8, 0.05, secondi=7))   # meno di 8 s: niente
        self.assertLess(self.misura(0, 0, 0.3)["c"], 1.5)          # solo fruscio: nessun battito sicuro

    def test_tonalita(self):
        import math

        def accordi(seq, secondi=16, hz=11025):   # accordi tenuti, uno ogni pochi secondi, con un piccolo battito
            x, n = [], int(secondi / len(seq) * hz)
            for note in seq:
                fr = [440 * 2 ** ((p - 69) / 12.0) for p in note]
                x += [sum(0.2 * math.sin(2 * math.pi * f * i / hz) for f in fr) * (1 - (i % 5512) / 11024.0) for i in range(n)]
            return x
        m = self.m
        do, fa, sol = [60, 64, 67, 72], [65, 69, 72, 77], [67, 71, 74, 79]
        la_m, re_m, mi = [57, 60, 64, 69], [62, 65, 69, 74], [64, 68, 71, 76]
        self.assertEqual(m.tonalita(m.griglia_battiti(accordi([do, fa, sol, do]), 11025)["spettro"], 11025), (1, "Do maggiore"))
        self.assertEqual(m.tonalita(m.griglia_battiti(accordi([la_m, re_m, mi, la_m]), 11025)["spettro"], 11025),
                         (-1, "La minore"))
        self.assertEqual(m.tonalita([0.0] * 464, 11025), (0, ""))

    def test_misura_brano(self):
        m = self.m
        chiesti = []

        def pezzo(percorso, da, durata, finto):
            chiesti.append(da)
            if da > finto.get("durata", 600):
                raise RuntimeError("ffmpeg: nessun audio")
            return wav(sintesi(finto["bpm"], finto["ampiezza"], finto["rumore"], min(durata, finto.get("durata", 600) - da), da))
        prove = {"forte": {"bpm": 150, "ampiezza": 0.9, "rumore": 0.3}, "piano": {"bpm": 80, "ampiezza": 0.15, "rumore": 0.005},
                 "fruscio": {"bpm": 0, "ampiezza": 0, "rumore": 0.3}}
        r = {}
        for (nome, finto), durata in zip(prove.items(), (200, 25, 70)):   # tre pezzi, uno, due
            with unittest.mock.patch.object(m, "_pezzo_wav", lambda p, da, d, f=finto: pezzo(p, da, d, f)):
                r[nome] = m.misura_audio("/x/" + nome + ".mp3", durata)
        self.assertEqual(chiesti, [35.0, 85.0, 135.0, 0.0, 9.5, 34.0])   # lontani da introduzione e finale
        self.assertEqual([r[x]["pezzi"] for x in prove], [3, 1, 2])
        self.assertAlmostEqual(r["forte"]["bpm"], 150, delta=1.0)
        self.assertGreaterEqual(r["forte"]["energia"], 0.7)
        self.assertGreaterEqual(r["forte"]["regolarita"], 0.8)
        self.assertLessEqual(r["piano"]["energia"], 0.45)
        self.assertLess(r["fruscio"]["regolarita"], 0.4)
        self.assertEqual(m._finestre(70), [9.5, 34.0])
        self.assertEqual(m._finestre(25), [0.0])
        self.assertEqual(m._finestre(5), [])
        with unittest.mock.patch.object(m, "_pezzo_wav", lambda p, da, d: None):
            self.assertEqual(m.misura_audio("/x/y.mp3", 200), {"senza_ffmpeg": True})


class TestMusicBrainz(unittest.TestCase):
    """Generi, paese e anno da un MusicBrainz finto: al più una richiesta al secondo, con il contatto nello User-Agent."""

    @classmethod
    def setUpClass(cls):
        cls.m = carica_modulo("musica_libreria_mb")
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FintoMusicBrainz)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_brani(self):
        m = self.m
        self.assertGreaterEqual(m.MB_INTERVALLO, 1.0)
        mb = m.MusicBrainz()
        FintoMusicBrainz.richieste = []
        with unittest.mock.patch.dict(os.environ, {"ATLANTE_MUSICBRAINZ": "http://127.0.0.1:%d" % self.srv.server_address[1],
                                                   "ATLANTE_CONTATTO": "prove@atlante.example"}):
            with unittest.mock.patch.object(m, "MB_INTERVALLO", 0.3):
                v = mb.brano("Albachiara (Official Video)", "Vasco Rossi feat. Nessuno", "")
                v2 = mb.brano("Vita spericolata", "Vasco Rossi")
                z = mb.brano("Ain't My Fault", "Zara Larsson")
                self.assertIsNone(mb.brano("Brano misterioso", "Sconosciuto"))
                s = mb.brano("Brano spoglio", "Senza Genere")
        self.assertEqual((v["generi"], v["italiano"], v["anno"], v["paese"]), (["pop", "rock"], True, 1979, "IT"))
        self.assertEqual(v2["anno"], 1983)
        self.assertEqual((z["generi"], z["italiano"], z["anno"]), (["dance", "elettronica", "pop"], False, 2016))
        self.assertIn("dance pop", z["etichette"])
        self.assertEqual(s["generi"], ["elettronica", "pop"])   # dall'album, quando registrazione e artista non dicono niente
        percorsi = [p for _, p, _ in FintoMusicBrainz.richieste]
        self.assertEqual(len([p for p in percorsi if p.startswith("/ws/2/artist/art-vasco")]), 1)   # artista in memoria
        self.assertTrue(all(ua == "AtlanteMusica/1.0 ( prove@atlante.example )" for _, _, ua in FintoMusicBrainz.richieste))
        tempi = [t for t, _, _ in FintoMusicBrainz.richieste]
        self.assertTrue(all(b - a >= 0.29 for a, b in zip(tempi, tempi[1:])), tempi)
        # MusicBrainz che non risponde: un errore da riprovare, non «brano sconosciuto»
        with unittest.mock.patch.dict(os.environ, {"ATLANTE_MUSICBRAINZ": "http://127.0.0.1:%d" % porta_libera()}):
            with self.assertRaises(m.ErroreMB):
                mb.brano("Albachiara", "Vasco Rossi")


class TestTematicheServer(unittest.TestCase):
    """Il lavoro in sottofondo sul server vero (MusicBrainz, IA e ffmpeg finti), la ripresa dopo un riavvio, «Crea con
    l'IA» (una richiesta alla volta, limite al giorno), le condivise e gli invitati."""

    @classmethod
    def setUpClass(cls):
        cls.base = tempfile.mkdtemp(prefix="atlante-tematiche-srv-")
        cls.c = prepara_cartelle(cls.base)
        p = os.path.join(cls.c["bin"], "ffmpeg")
        with open(p, "w", encoding="utf-8") as f:
            f.write("#!%s\n%s" % (sys.executable, FINTO_FFMPEG_AUDIO))
        os.chmod(p, 0o755)
        cls.mb = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FintoMusicBrainz)
        cls.ia = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FintaOllama)
        for s in (cls.mb, cls.ia):
            threading.Thread(target=s.serve_forever, daemon=True).start()
        cls.c["porta_ollama"] = cls.ia.server_address[1]
        FintoMusicBrainz.richieste = []
        for nome, (titolo, artista, secondi, anno, audio) in {
                "Vasco Rossi - Albachiara.mp3": ("Albachiara", "Vasco Rossi", 70, "", (120, 0.4, 0.05)),
                "Zara Larsson - Ain't My Fault.mp3": ("Ain't My Fault", "Zara Larsson", 200, "", (150, 0.9, 0.3)),
                "Sconosciuto - Brano misterioso.mp3": ("Brano misterioso", "Sconosciuto", 70, "2019", (95, 0.3, 0.02)),
                "Mariah Carey - All I Want for Christmas Is You.mp3":
                    ("All I Want for Christmas Is You", "Mariah Carey", 70, "2010", (128, 0.6, 0.15))}.items():
            cls.brano(nome, titolo, artista, secondi, anno, audio)
        cls.ambiente = {"ATLANTE_MUSICBRAINZ": "http://127.0.0.1:%d" % cls.mb.server_address[1],
                        "ATLANTE_CONTATTO": "prove@atlante.example"}
        cls.s = cls.avvia()
        cls.m = carica_modulo("musica_libreria_tematiche_srv")
        cls.pers = cls.m.Persone(os.path.join(cls.c["cache"], "persone.json"))

    @classmethod
    def brano(cls, nome, titolo, artista, secondi, anno, audio):
        p = os.path.join(cls.c["musica"], nome)
        mp3_finto_tematiche(p, titolo, artista, secondi, anno)
        with open(p + ".finto.json", "w") as f:
            json.dump({"bpm": audio[0], "ampiezza": audio[1], "rumore": audio[2], "durata": secondi}, f)

    @classmethod
    def avvia(cls):
        with unittest.mock.patch.dict(os.environ, cls.ambiente):
            return Server(cls.base, cls.c, ["--tematiche", "--pubblico", "https://mac.esempio.ts.net:10000"]).avvia()

    @classmethod
    def tearDownClass(cls):
        cls.s.ferma()
        cls.mb.shutdown()
        cls.ia.shutdown()
        shutil.rmtree(cls.base, ignore_errors=True)

    def finito(self, quanti, tempo=120):
        st = aspetta(lambda: (lambda j: j["stato"] if j["stato"]["totale"] == quanti and
                              j["stato"]["fatti"] == quanti else None)(self.s.json("GET", "/api/extra/tematiche")), tempo, 0.5)
        self.assertIsNotNone(st, "classificazione non finita: %r\n%s" % (self.s.json("GET", "/api/extra/tematiche")["stato"],
                                                                       self.s.leggi_log()))
        return st

    def id_di(self, titolo):
        return [b["id"] for b in self.s.json("GET", "/api/musica/catalogo")["brani"] if b["title"] == titolo][0]

    def dove(self, titolo):
        i, el = self.id_di(titolo), self.s.json("GET", "/api/extra/tematiche")
        return {p["id"] for g in el["gruppi"] for p in g["playlist"]
                if i in self.s.json("GET", "/api/extra/tematiche/" + p["id"])["brani"]}

    def audio_misurati(self):
        try:
            with open(os.path.join(self.c["finto"], "ffmpeg-audio.log")) as f:
                return [json.loads(r)["i"] for r in f]
        except OSError:
            return []

    def test_1_classificazione(self):
        self.finito(4)
        self.assertTrue({"genere-rock", "genere-rock-italiano", "epoca-70"} <= self.dove("Albachiara"))
        zara = self.dove("Ain't My Fault")
        self.assertTrue({"genere-pop", "genere-dance", "epoca-2010", "attivita-handbike-salita", "umore-energia"} <= zara, zara)
        self.assertNotIn("genere-pop-italiano", zara)
        # MusicBrainz non lo conosce: il genere lo propone l'IA (i generi inventati si scartano)
        self.assertTrue({"genere-pop", "genere-pop-italiano", "epoca-2010"} <= self.dove("Brano misterioso"))
        mariah = self.dove("All I Want for Christmas Is You")
        self.assertTrue({"momento-natale", "epoca-90", "genere-rnb"} <= mariah, mariah)   # 1994 (MusicBrainz), non 2010
        el = self.s.json("GET", "/api/extra/tematiche")
        self.assertNotIn("genere-metal", {p["id"] for g in el["gruppi"] for p in g["playlist"]})
        self.s.json("GET", "/api/extra/tematiche/genere-metal", codice=404)
        self.assertEqual(el["ia"]["rimaste"], 20)
        # MusicBrainz: al più una richiesta al secondo, sempre con il contatto
        r = FintoMusicBrainz.richieste
        self.assertTrue(all(ua == "AtlanteMusica/1.0 ( prove@atlante.example )" for _, _, ua in r), r)
        self.assertTrue(all(b - a >= 1.0 for (a, _, _), (b, _, _) in zip(r, r[1:])), [t for t, _, _ in r])
        misurati = collections.Counter(self.audio_misurati())
        self.assertEqual(misurati["Zara Larsson - Ain't My Fault.mp3"], 3)   # tre pezzi di un brano di 200 s
        self.assertEqual(misurati["Vasco Rossi - Albachiara.mp3"], 2)
        with open(os.path.join(self.c["cache"], "tematiche", "schede.json"), encoding="utf-8") as f:
            schede = json.load(f)["brani"]
        zs = schede[self.id_di("Ain't My Fault")]
        self.assertAlmostEqual(zs["bpm"], 150, delta=1.5)
        self.assertGreaterEqual(zs["energia"], 0.7)

    def test_2_ripresa_dopo_riavvio(self):
        self.finito(4)
        type(self).s.ferma()
        # come un riavvio a metà lavoro: Albachiara ha già MusicBrainz ma non l'audio; un brano nuovo arriva intanto
        file_schede = os.path.join(self.c["cache"], "tematiche", "schede.json")
        with open(file_schede, encoding="utf-8") as f:
            d = json.load(f)
        vasco = [k for k, s in d["brani"].items() if s.get("t") == "Albachiara"][0]
        for k in ("audio", "bpm", "energia", "regolarita", "f"):
            d["brani"][vasco].pop(k, None)
        with open(file_schede, "w", encoding="utf-8") as f:
            json.dump(d, f)
        self.brano("Nuovo Artista - Brano nuovo.mp3", "Brano nuovo", "Nuovo Artista", 70, "", (170, 0.8, 0.2))
        prima_mb, prima_audio = len(FintoMusicBrainz.richieste), len(self.audio_misurati())
        type(self).s = self.avvia()
        self.assertIn("genere-rock", {p["id"] for g in self.s.json("GET", "/api/extra/tematiche")["gruppi"]
                                      for p in g["playlist"]})   # le schede fatte ci sono subito
        self.finito(5)
        nuove = [p for _, p, _ in FintoMusicBrainz.richieste[prima_mb:]]
        self.assertTrue(nuove and all("Albachiara" not in unquote_(p) for p in nuove), nuove)
        self.assertTrue(any("Brano nuovo" in unquote_(p) for p in nuove), nuove)
        self.assertEqual(sorted(set(self.audio_misurati()[prima_audio:])),
                         ["Nuovo Artista - Brano nuovo.mp3", "Vasco Rossi - Albachiara.mp3"])
        self.assertIn("genere-metal", self.dove("Brano nuovo"))

    def test_3_crea_con_ia(self):
        self.finito(5)
        r = self.s.json("POST", "/api/extra/tematiche/ia", {"richiesta": "pop carico per la salita, 40 minuti"})
        self.assertEqual(r["nome"], "Salita pop")
        self.assertEqual(r["criteri"]["bpm"], [140, 180])
        # scelti dalla libreria con le regole: Zara (150 BPM) e, allargando un po', Mariah (128); i lenti no
        self.assertEqual(set(r["brani"]), {self.id_di("Ain't My Fault"), self.id_di("All I Want for Christmas Is You")})
        self.assertIn("Pochi brani con questi BPM: ho allargato a 125–195", r["note"])
        self.assertIn("BPM 140–180", r["riassunto"])
        self.assertEqual(r["rimaste"], 19)
        r = self.s.json("POST", "/api/extra/tematiche/ia", {"richiesta": "qualcosa di assurdo"})
        self.assertEqual((r["criteri"]["bpm"], r["criteri"]["anni"], r["criteri"]["durata_minuti"]), (None, None, 240))
        self.assertEqual(r["criteri"]["generi"], ["rap"])
        catalogo = {b["id"] for b in self.s.json("GET", "/api/musica/catalogo")["brani"]}
        self.assertTrue(set(r["brani"]) <= catalogo)
        j = self.s.json("POST", "/api/extra/tematiche/ia", {"richiesta": "una playlist rotto"}, codice=502)
        self.assertIn("non ha capito", j["errore"])
        self.s.json("POST", "/api/extra/tematiche/ia", {"richiesta": ""}, codice=400)
        # «Su misura per me»: dai propri ascolti, senza IA (non conta nel limite)
        r = self.s.json("POST", "/api/extra/tematiche/ia",
                        {"su_misura": True, "ascolti": [{"id": self.id_di("Ain't My Fault"), "n": 12}]})
        self.assertEqual(r["nome"], "Su misura per Samuele")
        self.assertIn(self.id_di("Brano misterioso"), r["brani"])   # pop anche lui
        self.assertEqual(r["rimaste"], 17)

    def test_4_una_richiesta_alla_volta(self):
        FintaOllama.massimo = 0
        esiti = []

        def chiedi():
            esiti.append(richiesta(self.s.porta, "POST", "/api/extra/tematiche/ia", {"richiesta": "pop lento"}, tempo=60)[0])
        fili = [threading.Thread(target=chiedi) for _ in range(3)]
        for f in fili:
            f.start()
        for f in fili:
            f.join()
        self.assertEqual(esiti, [200, 200, 200])
        self.assertEqual(FintaOllama.massimo, 1)

    def invitato(self, nome, ip):
        a = self.pers.crea(nome)
        codice, _ = self.pers.invito(a["id"])
        st, h, b = richiesta(self.s.porta, "POST", "/accesso/invito/" + codice, b"",
                             intest=dict(FUNNEL, **{"X-Forwarded-For": ip}, **TestAccesso.ORIGINE))
        self.assertEqual(st, 200, b[:200])
        v = [x for x in h.get_all("Set-Cookie") if x.startswith("am_accesso=")][0].split(";")[0].split("=", 1)[1]

        def chiedi(metodo, p, corpo=None):
            st, h, b = richiesta(self.s.porta, metodo, p, corpo, intest=dict(
                FUNNEL, **{"X-Forwarded-For": ip, "Cookie": "am_accesso=" + v, "Origin": TestAccesso.ORIGINE["Origin"]}))
            return st, json.loads(b.decode("utf-8")) if b else None
        return a["id"], chiedi

    def test_5_invitati_condivise_e_limite(self):
        self.finito(5)
        pid, anna = self.invitato("Anna", "203.0.113.150")
        _, bruno = self.invitato("Bruno", "203.0.113.151")
        st, el = anna("GET", "/api/extra/tematiche")
        self.assertEqual(st, 200)
        self.assertEqual(anna("GET", "/api/extra/tematiche/genere-pop")[0], 200)
        self.assertEqual(anna("POST", "/api/extra/ia", {"prompt": "x"})[0], 403)   # il resto dell'IA resta di Samuele
        self.assertEqual(anna("GET", "/api/extra/tematiche/campione")[0], 403)   # il controllo a campione è di Samuele
        st, j = anna("GET", "/api/extra/tematiche/ritmi")   # «Per ritmo» e «Buonumore» anche per gli invitati
        self.assertEqual(st, 200)
        self.assertAlmostEqual(j["ritmi"][self.id_di("Ain't My Fault")][0], 150, delta=1.5)
        self.assertIn(self.id_di("Ain't My Fault"), [i for i, _ in j["buonumore"]])
        self.assertEqual(len(self.s.json("GET", "/api/extra/tematiche/campione")["brani"]), 5)
        zara, vasco = self.id_di("Ain't My Fault"), self.id_di("Albachiara")
        st, j = anna("POST", "/api/extra/tematiche/condivise", {"nome": "Le mie salite", "brani": [zara, "inventato", vasco],
                                                                 "locale": "pl-1"})
        self.assertEqual(st, 200, j)
        self.assertEqual(j["n"], 2)
        cid = j["id"]
        # la vedono tutti tra le Playlist di Atlante; chi l'ha condivisa può toglierla, gli altri no (Samuele sì)
        el = self.s.json("GET", "/api/extra/tematiche")
        self.assertEqual(el["gruppi"][0]["id"], "condivise")
        cond = el["gruppi"][0]["playlist"][0]
        self.assertEqual((cond["nome"], cond["da_nome"], cond["mia"], cond["n"]), ("Le mie salite", "Anna", False, 2))
        self.assertEqual(anna("GET", "/api/extra/tematiche")[1]["gruppi"][0]["playlist"][0]["mia"], True)
        self.assertEqual(bruno("GET", "/api/extra/tematiche/c-" + cid)[1]["brani"], [zara, vasco])
        st, j = anna("POST", "/api/extra/tematiche/condivise", {"nome": "Le mie salite 2", "brani": [zara], "locale": "pl-1"})
        self.assertEqual(j["id"], cid)   # la stessa playlist condivisa di nuovo si aggiorna
        self.assertEqual(bruno("DELETE", "/api/extra/tematiche/condivise/" + cid)[0], 403)
        self.assertEqual(self.s.json("DELETE", "/api/extra/tematiche/condivise/" + cid), {"ok": True})
        self.assertNotEqual(self.s.json("GET", "/api/extra/tematiche")["gruppi"][0]["id"], "condivise")
        # al più 20 richieste all'IA al giorno per persona: quelle di Anna non toccano quelle di Bruno
        for i in range(20):
            st, j = anna("POST", "/api/extra/tematiche/ia", {"richiesta": "pop numero %d" % i})
            self.assertEqual(st, 200, j)
        self.assertEqual(j["rimaste"], 0)
        st, j = anna("POST", "/api/extra/tematiche/ia", {"richiesta": "ancora pop"})
        self.assertEqual(st, 429)
        self.assertIn("domani", j["errore"])
        st, j = bruno("POST", "/api/extra/tematiche/ia", {"richiesta": "pop per Bruno"})
        self.assertEqual((st, j["rimaste"]), (200, 19))
        with open(os.path.join(self.c["cache"], "tematiche", "limiti.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["conti"][pid], 20)   # il limite resta anche dopo un riavvio


def unquote_(s):
    from urllib.parse import unquote
    return unquote(s).replace("+", " ")


# ----- interfaccia: strato di modifiche (base di Samuele, personale di ciascuno) -----

class TestInterfaccia(unittest.TestCase):
    """Base di Samuele (bozza, Pubblica, ultime 10 versioni) e strato personale di ciascuno, con valori rigidi."""

    ORIGINE = "https://mac.esempio.ts.net:10000"
    # un altro login Tailscale: rete fidata ma non Samuele (ATLANTE_PROPRIETARI) = «casa»
    CASA = {"X-Forwarded-For": "100.100.1.2", "Tailscale-User-Login": "anna@example.com"}

    @classmethod
    def setUpClass(cls):
        cls.base = tempfile.mkdtemp(prefix="atlante-interfaccia-")
        cls.c = prepara_cartelle(cls.base)
        with unittest.mock.patch.dict(os.environ, {"ATLANTE_PROPRIETARI": "samuele@example.com"}):
            cls.s = Server(cls.base, cls.c).avvia()
        sys.dont_write_bytecode = True
        spec = importlib.util.spec_from_file_location("musica_libreria_interfaccia", SERVER)
        cls.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.m)
        cls.pers = cls.m.Persone(os.path.join(cls.c["cache"], "persone.json"))
        cls.cartella = os.path.join(cls.c["cache"], "interfaccia")

    @classmethod
    def tearDownClass(cls):
        cls.s.ferma()
        shutil.rmtree(cls.base, ignore_errors=True)

    @staticmethod
    def leggi(b):
        try:
            return json.loads(b.decode("utf-8"))
        except ValueError:
            return None

    def invitato(self, nome):
        """(persona, biscotto) di una persona appena entrata dal suo invito."""
        a = self.pers.crea(nome)
        codice, _ = self.pers.invito(a["id"])
        return a["id"], self.pers.biscotto(self.pers.usa_invito(codice, "iphone"))

    def da(self, biscotto, metodo, percorso, corpo=None):
        """Da Internet con il biscotto di un invitato: (codice, JSON)."""
        h = dict(FUNNEL, Cookie="am_accesso=" + biscotto, Origin=self.ORIGINE)
        st, _, b = richiesta(self.s.porta, metodo, "/api/extra/" + percorso, corpo, intest=h)
        return st, self.leggi(b)

    def samuele(self, metodo, percorso, corpo=None, intest=None):
        st, _, b = richiesta(self.s.porta, metodo, "/api/extra/" + percorso, corpo, intest=intest)
        return st, self.leggi(b)

    def casa(self, metodo, percorso, corpo=None):
        return self.samuele(metodo, percorso, corpo, intest=self.CASA)

    def test_1_personale_di_ciascuno(self):
        st, j = self.samuele("GET", "interfaccia")
        self.assertEqual(st, 200)
        self.assertEqual(j["base"], {"modifiche": {}, "versione": 0, "pubblicato": 0})
        self.assertEqual(j["personale"]["modifiche"], {})
        self.assertIsNone(j["bozza"])
        self.assertEqual(j["chi"], "p")
        mie = {"nav#tabs button[tab=home]": {"testo": "Casa", "colore": "#ff0000"},
               "main#view[v=lib] h1.lt": {"grandezza": 150, "nascosto": False}}
        st, j = self.samuele("POST", "interfaccia/personale", {"modifiche": mie})
        self.assertEqual(st, 200, j)
        self.assertEqual(j["personale"]["modifiche"], mie)
        self.assertGreater(j["personale"]["aggiornato"], 0)
        self.assertEqual(self.samuele("GET", "interfaccia")[1]["personale"]["modifiche"], mie)
        # un invitato ha il suo strato, non vede quello di Samuele né la bozza della base
        anna, v = self.invitato("Anna")
        st, j = self.da(v, "GET", "interfaccia")
        self.assertEqual(st, 200)
        self.assertEqual((j["chi"], j["personale"]["modifiche"]), (anna, {}))
        self.assertNotIn("bozza", j)
        sue = {"button[a=xopen][k=interfaccia]": {"sfondo": "viola", "ordine": -2}}
        self.assertEqual(self.da(v, "POST", "interfaccia/personale", {"modifiche": sue})[0], 200)
        self.assertEqual(self.samuele("GET", "interfaccia")[1]["personale"]["modifiche"], mie)
        # lo strato personale segue la persona su tutti i suoi dispositivi
        v2 = self.pers.biscotto(self.pers.usa_collegamento(self.pers.collegamento(anna)[0], "mac"))
        self.assertEqual(self.da(v2, "GET", "interfaccia")[1]["personale"]["modifiche"], sue)
        # dalla rete di casa si usa l'app come Samuele (come per i dispositivi), ma senza la bozza
        st, j = self.casa("GET", "interfaccia")
        self.assertEqual((st, j["chi"], j["personale"]["modifiche"]), (200, "p", mie))
        self.assertNotIn("bozza", j)
        # file del Mac mini: uno per persona, leggibili solo dal proprietario, scritti per intero
        f = os.path.join(self.cartella, "persone", anna + ".json")
        self.assertEqual(os.stat(f).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(os.path.join(self.cartella, "persone", "p.json")).st_mode & 0o777, 0o600)
        self.assertEqual([n for n in os.listdir(os.path.join(self.cartella, "persone")) if n.endswith(".tmp")], [])
        # strato vuoto: si torna all'originale
        self.assertEqual(self.da(v, "POST", "interfaccia/personale", {"modifiche": {}})[1]["personale"]["modifiche"], {})

    def test_2_valori_assurdi(self):
        prima = self.samuele("GET", "interfaccia")[1]["personale"]["modifiche"]
        brutte = [
            # colori: solo #rrggbb o un nome della tavolozza
            {"div.x": {"colore": "red"}}, {"div.x": {"colore": "#fff"}}, {"div.x": {"colore": "#12345g"}},
            {"div.x": {"sfondo": "url(javascript:alert(1))"}}, {"div.x": {"sfondo": "#123456;}body{display:none"}},
            {"div.x": {"colore": "var(--accent)"}}, {"div.x": {"colore": ""}}, {"div.x": {"colore": 255}},
            # indirizzi: la forma rigida, niente selettori liberi
            {"div{}": {"nascosto": True}}, {"body > div": {"nascosto": True}}, {'a[href="x"]': {"nascosto": True}},
            {"<script>": {"nascosto": True}}, {"#view": {"nascosto": True}}, {"main#view h2.h2:0": {"nascosto": True}},
            {"div." + "a" * 400: {"nascosto": True}}, {" ".join(["div"] * 8): {"nascosto": True}},
            {"div.a.b.c.d.e": {"nascosto": True}}, {"div[onclick=x]": {"nascosto": True}},
            {"div[a=x y]": {"nascosto": True}}, {"DIV": {"nascosto": True}}, {"div\n": {"nascosto": True}},
            {"main#view  h2": {"nascosto": True}}, {" div": {"nascosto": True}}, {"div[v=a][a=b]": {"nascosto": True}},
            {"div:has(x)": {"nascosto": True}}, {"div#1x": {"nascosto": True}}, {"": {"nascosto": True}},
            {"body": {"nascosto": True}}, {"html": {"colore": "rosso"}}, {"div.x body:2": {"nascosto": True}},
            {"main#view style": {"nascosto": True}}, {"head.x": {"nascosto": True}},
            # proprietà e valori
            {"div.x": {"testo": "x" * 81}}, {"div.x": {"testo": ["x"]}}, {"div.x": {"grandezza": 69}},
            {"div.x": {"grandezza": 201}}, {"div.x": {"grandezza": "120"}}, {"div.x": {"grandezza": True}},
            {"div.x": {"grandezza": 120.5}}, {"div.x": {"nascosto": "si"}}, {"div.x": {"nascosto": 1}},
            {"div.x": {"ordine": 21}}, {"div.x": {"ordine": 1.5}}, {"div.x": {"onclick": "x"}}, {"div.x": {}},
            {"div.x": "nascosto"}, [], "tutto",
        ]
        for m in brutte:
            st, j = self.samuele("POST", "interfaccia/personale", {"modifiche": m})
            self.assertEqual(st, 400, (m, j))
            self.assertTrue(j["errore"], m)
        self.assertEqual(self.samuele("POST", "interfaccia/personale", "non un oggetto")[0], 400)
        self.assertEqual(self.samuele("GET", "interfaccia")[1]["personale"]["modifiche"], prima)
        # al più 500 modifiche per livello (ogni proprietà di ogni elemento conta una)
        molte = {"div.x%d" % i: {"nascosto": True} for i in range(500)}
        self.assertEqual(self.samuele("POST", "interfaccia/personale", {"modifiche": molte})[0], 200)
        molte["div.y"] = {"grandezza": 100}
        self.assertEqual(self.samuele("POST", "interfaccia/personale", {"modifiche": molte})[0], 400)
        due = {"div.x%d" % i: {"nascosto": True, "grandezza": 120} for i in range(251)}
        self.assertEqual(self.samuele("POST", "interfaccia/personale", {"modifiche": due})[0], 400)
        st, _, b = richiesta(self.s.porta, "POST", "/api/extra/interfaccia/personale",
                             json.dumps({"modifiche": {}, "x": "y" * (2 * 1024 * 1024)}).encode("utf-8"))
        self.assertEqual(st, 413)
        # un testo con HTML resta testo: si salva com'è (l'app lo scrive come testo); spazi e caratteri di controllo no
        buone = {"main#view[v=home] h2.h2[sez=i-piu-ascoltati]": {"testo": "<img src=x onerror=alert(1)>"},
                 "span.l:2": {"testo": " Ciao\n\tmondo\u0000 \u202e! "}, "button#npFav": {"colore": "#FFAA00"},
                 "div.x": {"sfondo": "accento", "colore": "originale", "testo": ""}, "body main#view[v=home]": {"grandezza": 90},
                 "nav#tabs div.in button.bodyx": {"nascosto": True}}
        st, h, b = richiesta(self.s.porta, "POST", "/api/extra/interfaccia/personale", {"modifiche": buone})
        self.assertEqual(st, 200, b)
        self.assertIn("application/json", h["Content-Type"])
        j = self.leggi(b)["personale"]["modifiche"]
        self.assertEqual(j["main#view[v=home] h2.h2[sez=i-piu-ascoltati]"]["testo"], "<img src=x onerror=alert(1)>")
        self.assertEqual(j["span.l:2"]["testo"], "Ciao mondo !")
        self.assertEqual(j["button#npFav"]["colore"], "#ffaa00")
        self.assertEqual(j["div.x"], {"sfondo": "accento", "colore": "originale", "testo": ""})
        self.samuele("POST", "interfaccia/personale", {"modifiche": prima})

    def test_3_base_bozza_e_pubblica(self):
        anna, v = self.invitato("Bea")
        self.da(v, "POST", "interfaccia/personale", {"modifiche": {"span#miniT": {"colore": "verde"}}})
        base = {"main#view[v=home] h1.lt": {"testo": "Benvenuti"}, "nav#tabs button[tab=search]": {"nascosto": True}}
        st, j = self.samuele("POST", "interfaccia/bozza", {"modifiche": base})
        self.assertEqual((st, j["bozza"]["modifiche"]), (200, base), j)
        # la bozza la vede solo Samuele finché non la pubblica
        j = self.samuele("GET", "interfaccia")[1]
        self.assertEqual(j["bozza"]["modifiche"], base)
        self.assertEqual(self.samuele("GET", "interfaccia/bozza")[1]["bozza"]["modifiche"], base)
        prima = j["base"]["versione"]
        self.assertEqual(self.da(v, "GET", "interfaccia")[1]["base"]["versione"], prima)
        # bozza, pubblicazione e versioni: solo Samuele (gli invitati non arrivano nemmeno alla rotta)
        for metodo, p in (("GET", "interfaccia/bozza"), ("POST", "interfaccia/bozza"), ("DELETE", "interfaccia/bozza"),
                          ("POST", "interfaccia/pubblica"), ("GET", "interfaccia/versioni"),
                          ("POST", "interfaccia/versioni/1/ripristina"), ("GET", "interfaccia/altro")):
            st, j = self.da(v, metodo, p, {"modifiche": {}} if metodo == "POST" else None)
            self.assertEqual((st, j["errore"]), (403, "Non disponibile per gli invitati"), (metodo, p))
            st, j = self.casa(metodo, p, {"modifiche": {}} if metodo == "POST" else None)
            self.assertEqual(st, 403 if p != "interfaccia/altro" else 404, (metodo, p, j))
        # Pubblica: la bozza diventa la base di tutti, le personalizzazioni restano
        st, j = self.samuele("POST", "interfaccia/pubblica")
        self.assertEqual(st, 200, j)
        self.assertEqual((j["base"]["versione"], j["base"]["modifiche"]), (prima + 1, base))
        self.assertIsNone(self.samuele("GET", "interfaccia")[1]["bozza"])
        j = self.da(v, "GET", "interfaccia")[1]
        self.assertEqual((j["base"]["versione"], j["base"]["modifiche"]), (prima + 1, base))
        self.assertGreater(j["base"]["pubblicato"], 0)
        self.assertEqual(j["personale"]["modifiche"], {"span#miniT": {"colore": "verde"}})
        self.assertEqual(self.da(v, "GET", "interfaccia/base")[1]["base"]["modifiche"], base)
        st, j = self.samuele("POST", "interfaccia/pubblica")
        self.assertEqual(st, 409, j)
        # una bozza si può anche buttare
        self.samuele("POST", "interfaccia/bozza", {"modifiche": {"div.x": {"nascosto": True}}})
        self.assertEqual(self.samuele("DELETE", "interfaccia/bozza"), (200, {"ok": True}))
        self.assertIsNone(self.samuele("GET", "interfaccia")[1]["bozza"])
        self.assertEqual(self.samuele("GET", "interfaccia")[1]["base"]["modifiche"], base)
        # valori assurdi anche nella bozza
        self.assertEqual(self.samuele("POST", "interfaccia/bozza", {"modifiche": {"div.x": {"colore": "red"}}})[0], 400)
        # la scrittura chiede la stessa origine, come le altre
        st, j = self.samuele("POST", "interfaccia/bozza", {"modifiche": {}}, intest={"Origin": "https://altro.example"})
        self.assertEqual(st, 403)
        st, j = self.samuele("POST", "interfaccia/personale", {"modifiche": {}}, intest={"Origin": "https://altro.example"})
        self.assertEqual(st, 403)

    def test_4_ultime_dieci_versioni(self):
        self.samuele("DELETE", "interfaccia/bozza")
        for i in range(12):
            self.assertEqual(self.samuele("POST", "interfaccia/bozza", {"modifiche": {"div.v%d" % i: {"grandezza": 110}}})[0], 200)
            self.assertEqual(self.samuele("POST", "interfaccia/pubblica")[0], 200)
        st, j = self.samuele("GET", "interfaccia/versioni")
        self.assertEqual(st, 200)
        vv = j["versioni"]
        self.assertEqual(len(vv), 10)
        self.assertEqual([x["versione"] for x in vv], sorted((x["versione"] for x in vv), reverse=True))
        self.assertEqual(j["attuale"], vv[0]["versione"])
        self.assertEqual(vv[0]["n"], 1)
        vecchia = vv[-1]["versione"]
        # tornare a una versione vecchia la pubblica di nuovo (come versione nuova, così si può tornare anche indietro)
        self.samuele("POST", "interfaccia/bozza", {"modifiche": {}})
        st, j = self.samuele("POST", "interfaccia/versioni/%d/ripristina" % vecchia)
        self.assertEqual(st, 409, j)   # prima si pubblica o si butta la bozza
        self.samuele("DELETE", "interfaccia/bozza")
        st, j = self.samuele("POST", "interfaccia/versioni/%d/ripristina" % vecchia)
        self.assertEqual(st, 200, j)
        self.assertEqual(j["base"]["versione"], vv[0]["versione"] + 1)
        self.assertEqual(j["base"]["modifiche"], {"div.v2": {"grandezza": 110}})
        self.assertEqual(self.samuele("POST", "interfaccia/versioni/%d/ripristina" % (vecchia - 1))[0], 404)
        self.assertEqual(self.samuele("POST", "interfaccia/versioni/%d/ripristina" % j["base"]["versione"])[0], 409)
        self.assertEqual(self.samuele("POST", "interfaccia/versioni/x/ripristina")[0], 404)
        f = os.path.join(self.cartella, "base.json")
        self.assertEqual(os.stat(f).st_mode & 0o777, 0o600)

    def test_5_file_rovinato_o_scritto_a_mano(self):
        f = os.path.join(self.cartella, "base.json")
        with open(f, encoding="utf-8") as x:
            buono = x.read()
        try:
            # voci scritte a mano con valori assurdi: non arrivano all'app
            d = json.loads(buono)
            d["base"]["modifiche"] = {"div.ok": {"nascosto": True}, "div{}": {"nascosto": True},
                                      "div.no": {"colore": "red", "grandezza": 150}}
            with open(f, "w", encoding="utf-8") as x:
                json.dump(d, x)
            self.assertEqual(self.samuele("GET", "interfaccia")[1]["base"]["modifiche"],
                             {"div.ok": {"nascosto": True}, "div.no": {"grandezza": 150}})
            # un file che non si legge: l'app si apre com'è e il file non si sovrascrive
            with open(f, "w", encoding="utf-8") as x:
                x.write("{rotto")
            st, j = self.samuele("GET", "interfaccia")
            self.assertEqual((st, j["base"]["modifiche"]), (200, {}))
            self.assertEqual(self.samuele("POST", "interfaccia/bozza", {"modifiche": {}})[0], 503)
            with open(f, encoding="utf-8") as x:
                self.assertEqual(x.read(), "{rotto")
        finally:
            with open(f, "w", encoding="utf-8") as x:
                x.write(buono)

    def test_6_permessi_invitato(self):
        _, v = self.invitato("Carla")
        for metodo, p in (("GET", "/api/extra/interfaccia"), ("GET", "/api/extra/interfaccia/base"),
                          ("POST", "/api/extra/interfaccia/personale"), ("GET", "/musica/api/extra/interfaccia")):
            h = dict(FUNNEL, Cookie="am_accesso=" + v, Origin=self.ORIGINE)
            st, _, b = richiesta(self.s.porta, metodo, p, {"modifiche": {}} if metodo == "POST" else None, intest=h)
            self.assertEqual(st, 200, (metodo, p, b[:200]))


# ----- mix tra i brani dell'app: prove della logica di index.html con JavaScriptCore del Mac -----
class TestMixApp(unittest.TestCase):
    """Le funzioni del mix (anche il «mix al salto») e una simulazione dei due lettori girano in JavaScriptCore,
    che c'è su ogni Mac (osascript -l JavaScript): test_mix.js. Senza osascript (non è un Mac) si salta."""

    def test_mix(self):
        osa = shutil.which("osascript") or ("/usr/bin/osascript" if os.path.exists("/usr/bin/osascript") else None)
        if not osa:
            self.skipTest("osascript non c'è: non è un Mac")
        js, app = os.path.join(QUI, "test_mix.js"), os.path.abspath(os.path.join(QUI, "..", "index.html"))
        r = subprocess.run([osa, "-l", "JavaScript", js, app], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
        out, err = r.stdout.decode("utf-8", "replace").strip(), r.stderr.decode("utf-8", "replace").strip()
        if not out.startswith(("OK", "PROVA FALLITA")):   # JavaScriptCore non partito (non una prova sbagliata)
            self.skipTest("osascript non utilizzabile qui: " + (err or out)[-300:])
        self.assertTrue(out.startswith("OK"), out + "\n" + err)


if __name__ == "__main__":
    unittest.main(verbosity=2)
