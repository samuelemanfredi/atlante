#!/usr/bin/env python3
"""Prove delle API dei moduli extra (api/extra/) di musica_libreria.py. Solo libreria standard:

    python3 -I test_extra.py

Avvia il server su una porta libera con una cartella di musica vuota, cartelle temporanee e programmi finti
(yt-dlp, say) in testa al PATH; l'IA è cercata su una porta dove non risponde nessuno (quindi «Ollama assente»
anche su un Mac dove Ollama c'è). Il proxy verso un sito pubblico si prova solo se la rete c'è.
"""
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
        self.assertEqual(json.loads(b.decode("utf-8")), {"ruolo": "invitato", "persona": {"id": pid, "nome": "Elena"}})
        st, h, b = richiesta(self.s.porta, "GET", "/api/extra/io")
        io = json.loads(b.decode("utf-8"))
        self.assertEqual((io["ruolo"], io["persona"], sorted(io["nuove"])),
                         ("proprietario", None, ["da_controllare", "film", "richieste"]))

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
