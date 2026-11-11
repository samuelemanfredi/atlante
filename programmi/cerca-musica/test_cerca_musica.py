#!/usr/bin/env python3
"""Prove di Cerca musica (senza Internet): python3 test_cerca_musica.py

Le risposte dei siti sono copie ridotte di risposte vere; lo scaricamento usa un piccolo server su questo computer."""
import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cerca_musica as cm  # noqa: E402

ARCHIVIO_CERCA = {"response": {"numFound": 55, "docs": [
    {"identifier": "NS050", "title": "Another Day, Another Way", "creator": "No-Source Netlabel",
     "licenseurl": "http://creativecommons.org/licenses/by-nc-sa/4.0/", "year": 2012},
    {"identifier": "senza_licenza", "title": "Niente", "creator": "X", "licenseurl": "http://esempio.it/mia"},
    {"identifier": "../cattivo", "title": "Nome strano", "licenseurl": "http://creativecommons.org/licenses/by/4.0/"},
    {"identifier": ["dominio_pubblico"], "title": ["Vecchi dischi"], "creator": ["Orchestra"],
     "licenseurl": "http://creativecommons.org/publicdomain/mark/1.0/"}]}}
ARCHIVIO_ALBUM = {"metadata": {"title": "Another Day, Another Way", "creator": "No-Source Netlabel",
                               "licenseurl": "http://creativecommons.org/licenses/by-nc-sa/4.0/"},
                  "files": [
    {"name": "02-Prior-To.mp3", "source": "original", "format": "VBR MP3", "title": "Prior To (alt. ver.)",
     "track": "2/15", "length": "167.97", "creator": "Full Source"},
    {"name": "02-Prior-To.ogg", "source": "derivative", "format": "Ogg Vorbis", "original": "02-Prior-To.mp3",
     "length": "160.11"},
    {"name": "02-Prior-To.png", "source": "derivative", "format": "PNG", "original": "02-Prior-To.mp3"},
    {"name": "01-Christmas.flac", "source": "original", "format": "Flac", "title": "Christmas with Mr. Rice",
     "track": "1/15", "length": "4:01"},
    {"name": "01-Christmas.mp3", "source": "derivative", "format": "VBR MP3", "original": "01-Christmas.flac",
     "length": "240.9"},
    {"name": "03-Bonus.m4a", "source": "original", "format": "MPEG-4 Audio", "title": "Bonus", "track": "3"},
    {"name": "video.mp4", "source": "original", "format": "MPEG4"},
    {"name": "copertina.jpg", "source": "original", "format": "JPEG"},
    {"name": "NS050_meta.xml", "source": "original", "format": "Metadata"}]}
CCMIXTER = [
    {"upload_id": 71304, "upload_name": "STURM STRUM SUTRA", "user_name": "Zenboy1955", "user_real_name": "Zen Boy",
     "license_url": "http://creativecommons.org/licenses/by-nc/3.0/", "file_page_url": "https://ccmixter.org/files/Z/71304",
     "files": [
         {"download_url": "https://ccmixter.org/content/Z/Z_-_STURM.zip",
          "file_format_info": {"media-type": "archive", "default-ext": "zip"}},
         {"download_url": "https://ccmixter.org/content/Z/Z_-_STURM.mp3",
          "file_format_info": {"media-type": "audio", "default-ext": "mp3", "ps": "5:42"}},
         {"download_url": "https://ccmixter.org/content/Z/Z_-_STURM.flac",
          "file_format_info": {"media-type": "audio", "default-ext": "flac", "ps": "5:42"}}]},
    {"upload_id": 2, "upload_name": "Solo zip", "user_name": "y", "license_url": "http://creativecommons.org/licenses/by/3.0/",
     "files": [{"download_url": "https://ccmixter.org/content/y/a.zip", "file_format_info": {"media-type": "archive"}}]}]
WIKIMEDIA = {"continue": {"gsroffset": 20, "continue": "gsroffset||"}, "query": {"pages": [
    {"pageid": 22, "ns": 6, "title": "File:Bach - Cello Suite No. 1 - Prelude.ogg", "index": 2, "imageinfo": [{
        "size": 3000000, "url": "https://upload.wikimedia.org/wikipedia/commons/a/ab/Bach_-_Cello_Suite_No._1_-_Prelude.ogg",
        "descriptionurl": "https://commons.wikimedia.org/wiki/File:Bach_-_Cello_Suite_No._1_-_Prelude.ogg",
        "mediatype": "AUDIO", "extmetadata": {
            "LicenseShortName": {"value": "Public domain"},
            "Artist": {"value": "<a href=\"//commons.wikimedia.org/wiki/User:X\" title=\"User:X\">Jan &amp; Co.</a>"}}}]},
    {"pageid": 11, "ns": 6, "title": "File:Sonata_in_do.mp3", "index": 1, "imageinfo": [{
        "url": "https://upload.wikimedia.org/wikipedia/commons/1/11/Sonata_in_do.mp3",
        "descriptionurl": "https://commons.wikimedia.org/wiki/File:Sonata_in_do.mp3", "mediatype": "AUDIO",
        "extmetadata": {"LicenseShortName": {"value": "CC BY-SA 4.0"},
                        "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0"},
                        "ObjectName": {"value": "Sonata in do maggiore"}}}]},
    {"pageid": 33, "ns": 6, "title": "File:Spartito.mid", "index": 3, "imageinfo": [{
        "url": "https://upload.wikimedia.org/wikipedia/commons/3/33/Spartito.mid", "mediatype": "AUDIO",
        "extmetadata": {}}]}]}}
OPENVERSE = {"result_count": 240, "page_count": 12, "results": [
    {"id": "5d08", "title": "Old Movie Ragtime Piano", "creator": "Akashic Records",
     "url": "https://prod-1.storage.jamendo.com/?trackid=923463&format=mp32", "license": "by-nc-nd",
     "license_version": "3.0", "license_url": "https://creativecommons.org/licenses/by-nc-nd/3.0/", "source": "jamendo",
     "duration": 72000, "filetype": "mp32", "foreign_landing_url": "https://www.jamendo.com/track/923463",
     "thumbnail": "https://api.openverse.org/v1/audio/5d08/thumb/", "audio_set": {"title": "Ragtime"}},
    {"id": "fuori", "title": "Da un altro sito", "url": "https://esempio.com/a.mp3", "license": "by", "source": "x"}]}


class Licenze(unittest.TestCase):
    def test_da_url(self):
        self.assertEqual(cm.licenza_da_url("http://creativecommons.org/licenses/by-nc-sa/4.0/"), "CC BY-NC-SA 4.0")
        self.assertEqual(cm.licenza_da_url("https://creativecommons.org/licenses/by/3.0"), "CC BY 3.0")
        self.assertEqual(cm.licenza_da_url("https://creativecommons.org/publicdomain/zero/1.0/"), "CC0")
        self.assertEqual(cm.licenza_da_url("http://creativecommons.org/publicdomain/mark/1.0/"), "Pubblico dominio")
        self.assertEqual(cm.licenza_da_url("http://creativecommons.org/licenses/publicdomain/"), "Pubblico dominio")
        self.assertEqual(cm.licenza_da_url("https://esempio.it/tutti-i-diritti-riservati"), "")
        self.assertEqual(cm.licenza_da_url(None), "")

    def test_openverse_e_uso(self):
        self.assertEqual(cm.licenza_openverse("by-nc", "3.0"), "CC BY-NC 3.0")
        self.assertEqual(cm.licenza_openverse("cc0", "1.0"), "CC0")
        self.assertEqual(cm.uso("CC BY-NC-SA 4.0"), "personale")
        self.assertEqual(cm.uso("CC BY-SA 4.0"), "libero")
        self.assertEqual(cm.uso("Pubblico dominio"), "libero")
        self.assertEqual(cm.uso(""), "")

    def test_siti_liberi(self):
        self.assertTrue(cm.sito_libero("https://archive.org/download/x/a.mp3"))
        self.assertTrue(cm.sito_libero("https://ia800.us.archive.org/1/items/x/a.mp3"))
        self.assertTrue(cm.sito_libero("https://prod-1.storage.jamendo.com/?trackid=1"))
        self.assertTrue(cm.sito_libero("https://upload.wikimedia.org/wikipedia/commons/a/ab/x.ogg"))
        self.assertFalse(cm.sito_libero("https://archive.org.cattivo.com/a.mp3"))
        self.assertFalse(cm.sito_libero("https://falsoarchive.org/a.mp3"))
        self.assertFalse(cm.sito_libero("https://cattivo.com/archive.org/a.mp3"))
        self.assertFalse(cm.sito_libero("file:///etc/passwd"))
        self.assertFalse(cm.sito_libero("ftp://archive.org/a.mp3"))

    def test_durata(self):
        self.assertEqual(cm._durata("240.9"), 240)
        self.assertEqual(cm._durata("4:01"), 241)
        self.assertEqual(cm._durata("1:02:03"), 3723)
        self.assertEqual(cm._durata(None), 0)
        self.assertEqual(cm._durata("boh"), 0)


class Nomi(unittest.TestCase):
    def test_pulisci(self):
        self.assertEqual(cm.pulisci('AC/DC: "Back" <in> black?'), "AC DC Back in black")
        self.assertEqual(cm.pulisci("..."), "Senza titolo")
        self.assertEqual(len(cm.pulisci("a" * 500)), 120)

    def test_nome_file(self):
        self.assertEqual(cm.nome_file({"artista": "Zen Boy", "titolo": "Sutra", "est": ".mp3", "fonte": "ccMixter"}),
                         "Zen Boy - Sutra.mp3")
        self.assertEqual(cm.nome_file({"titolo": "Prelude", "est": ".ogg"}), "Prelude.ogg")
        self.assertEqual(cm.nome_file({"artista": "Full Source", "titolo": "Prior To", "album": "Another Day",
                                       "traccia": 2, "est": ".mp3", "fonte": "Internet Archive"}),
                         os.path.join("Full Source - Another Day", "02 Prior To.mp3"))
        # l'artista già nel titolo non si ripete
        self.assertEqual(cm.nome_file({"artista": "Frank Tischer", "titolo": "Kailash", "est": ".mp3", "traccia": 1,
                                       "album": "(P414) Frank Tischer - Kailash", "fonte": "Internet Archive"}),
                         os.path.join("(P414) Frank Tischer - Kailash", "01 Kailash.mp3"))
        self.assertEqual(cm.nome_file({"artista": "OnClassical", "titolo": "OnClassical - Gavotte", "est": ".mp3"}),
                         "OnClassical - Gavotte.mp3")


class Fonti(unittest.TestCase):
    def test_archivio(self):
        with mock.patch.object(cm, "leggi_json", return_value=ARCHIVIO_CERCA) as lj:
            risultati, altri = cm.cerca_archivio("jazz", 1)
        url = lj.call_args[0][0]
        self.assertIn("licenseurl", url)          # solo licenze libere
        self.assertIn("librivoxaudio", url)       # niente audiolibri
        self.assertIn("78rpm", url)               # niente dischi a 78 giri
        self.assertIn("format%3A%28MP3", url)     # solo album con file audio
        self.assertEqual([r["ia"] for r in risultati], ["NS050", "dominio_pubblico"])
        self.assertEqual(risultati[0]["licenza"], "CC BY-NC-SA 4.0")
        self.assertEqual(risultati[0]["uso"], "personale")
        self.assertEqual(risultati[1]["titolo"], "Vecchi dischi")
        self.assertTrue(altri)

    def test_album_archivio(self):
        with mock.patch.object(cm, "leggi_json", return_value=ARCHIVIO_ALBUM):
            brani = cm.brani_archivio("NS050")
        self.assertEqual([(b["traccia"], b["titolo"], b["audio"].rsplit("/", 1)[1], b["durata"]) for b in brani], [
            (1, "Christmas with Mr. Rice", "01-Christmas.mp3", 240),   # l'MP3 al posto del FLAC originale
            (2, "Prior To (alt. ver.)", "02-Prior-To.mp3", 167),       # l'MP3, non l'Ogg
            (3, "Bonus", "03-Bonus.m4a", 0)])                          # il video MP4 no
        self.assertEqual(brani[1]["artista"], "Full Source")
        self.assertEqual(brani[0]["artista"], "No-Source Netlabel")
        with self.assertRaises(cm.ErroreFonte):
            cm.brani_archivio("../../etc")

    def test_album_senza_licenza(self):
        d = json.loads(json.dumps(ARCHIVIO_ALBUM))
        d["metadata"]["licenseurl"] = ""
        with mock.patch.object(cm, "leggi_json", return_value=d), self.assertRaises(cm.ErroreFonte):
            cm.brani_archivio("NS050")

    def test_ccmixter(self):
        with mock.patch.object(cm, "leggi_json", return_value=CCMIXTER) as lj:
            risultati, altri = cm.cerca_ccmixter("piano", 2)
        self.assertIn("f=js", lj.call_args[0][0])
        self.assertIn("offset=20", lj.call_args[0][0])
        self.assertEqual(len(risultati), 1)   # il secondo ha solo uno zip
        r = risultati[0]
        self.assertEqual((r["titolo"], r["artista"], r["audio"].rsplit("/", 1)[1], r["est"], r["durata"], r["licenza"]),
                         ("STURM STRUM SUTRA", "Zen Boy", "Z_-_STURM.mp3", ".mp3", 342, "CC BY-NC 3.0"))
        self.assertFalse(altri)

    def test_wikimedia(self):
        with mock.patch.object(cm, "leggi_json", return_value=WIKIMEDIA) as lj:
            risultati, altri = cm.cerca_wikimedia("sonata", 1)
        self.assertIn("filetype%3Aaudio", lj.call_args[0][0])
        self.assertEqual([r["titolo"] for r in risultati], ["Sonata in do maggiore", "Bach - Cello Suite No. 1 - Prelude"])
        self.assertEqual([r["licenza"] for r in risultati], ["CC BY-SA 4.0", "Pubblico dominio"])
        self.assertEqual(risultati[1]["artista"], "Jan & Co.")
        self.assertEqual(risultati[1]["est"], ".ogg")
        self.assertTrue(altri)

    def test_wikimedia_errore(self):
        with mock.patch.object(cm, "leggi_json", return_value={"error": {"info": "troppe richieste"}}):
            with self.assertRaises(cm.ErroreFonte):
                cm.cerca_wikimedia("x", 1)

    def test_openverse(self):
        with mock.patch.object(cm, "leggi_json", return_value=OPENVERSE):
            risultati, altri = cm.cerca_openverse("ragtime", 1)
        self.assertEqual(len(risultati), 1)   # l'altro non è su un sito di musica libera
        r = risultati[0]
        self.assertEqual((r["fonte"], r["est"], r["durata"], r["licenza"], r["album"]),
                         ("Jamendo", ".mp3", 72, "CC BY-NC-ND 3.0", "Ragtime"))
        self.assertTrue(altri)

    def test_jamendo(self):
        with mock.patch.dict(os.environ, {"JAMENDO_CLIENT_ID": "", "ATLANTE_JAMENDO_CLIENT_ID": ""}):
            self.assertNotIn("jamendo", cm.fonti_attive())
            with self.assertRaises(cm.ErroreFonte):
                cm.cerca_jamendo("x")
        risposta = {"headers": {"status": "success", "results_count": 2}, "results": [
            {"id": 1, "name": "Uno", "artist_name": "A", "album_name": "B", "duration": 200,
             "audiodownload": "https://prod-1.storage.jamendo.com/download/track/1/mp32/",
             "audiodownload_allowed": True, "license_ccurl": "http://creativecommons.org/licenses/by-sa/3.0/"},
            {"id": 2, "name": "Non scaricabile", "audiodownload": "https://prod-1.storage.jamendo.com/x",
             "audiodownload_allowed": False, "license_ccurl": "http://creativecommons.org/licenses/by/3.0/"}]}
        with mock.patch.dict(os.environ, {"JAMENDO_CLIENT_ID": "segreto123"}):
            self.assertIn("jamendo", cm.fonti_attive())
            with mock.patch.object(cm, "leggi_json", return_value=risposta):
                risultati, _ = cm.cerca_jamendo("x")
            self.assertEqual([r["titolo"] for r in risultati], ["Uno"])
            errore = {"headers": {"status": "failed", "error_message": "client segreto123 non valido"}}
            with mock.patch.object(cm, "leggi_json", return_value=errore):
                with self.assertRaises(cm.ErroreFonte) as e:
                    cm.cerca_jamendo("x")
            self.assertNotIn("segreto123", str(e.exception))

    def test_cerca(self):
        with self.assertRaises(cm.ErroreFonte):
            cm.cerca("archivio", "   ")
        with self.assertRaises(cm.ErroreFonte):
            cm.cerca("sconosciuta", "x")
        with mock.patch.object(cm, "leggi_json", return_value={"response": {"numFound": 0, "docs": []}}):
            self.assertEqual(cm.cerca("archivio", "x"), {"risultati": [], "altri": False})


class FintoSito(BaseHTTPRequestHandler):
    """Un sito di musica finto: /brano.mp3, /pagina (HTML), /corto (meno byte del dichiarato), /fuori (verso un
    altro sito)."""
    AUDIO = b"ID3" + bytes(range(256)) * 400

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/brano.mp3"):
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Content-Length", str(len(self.AUDIO)))
            self.end_headers()
            self.wfile.write(self.AUDIO)
        elif self.path == "/pagina":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html>accedi</html>")
        elif self.path == "/corto":
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Content-Length", "999999")
            self.end_headers()
            self.wfile.write(b"ID3" * 10)
        elif self.path == "/vietato":
            self.send_response(403)
            self.end_headers()
        elif self.path == "/fuori":
            self.send_response(302)
            self.send_header("Location", "https://cattivo.com/virus.exe")
            self.end_headers()
        elif self.path == "/dentro":
            self.send_response(302)
            self.send_header("Location", "/brano.mp3?dopo")
            self.end_headers()


class Scaricare(unittest.TestCase):
    def setUp(self):
        self.cartella = tempfile.mkdtemp(prefix="prova-musica-")
        self.sito = ThreadingHTTPServer(("127.0.0.1", 0), FintoSito)
        threading.Thread(target=self.sito.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.sito.server_address[1]
        self.siti = mock.patch.object(cm, "SITI_LIBERI", cm.SITI_LIBERI + ("127.0.0.1",))
        self.siti.start()

    def tearDown(self):
        self.siti.stop()
        self.sito.shutdown()
        self.sito.server_close()
        shutil.rmtree(self.cartella, ignore_errors=True)

    def brano(self, percorso="/brano.mp3", **altro):
        b = {"id": "cc:1", "titolo": "Sutra", "artista": "Zen Boy", "fonte": "ccMixter", "licenza": "CC BY 3.0",
             "licenza_url": "http://creativecommons.org/licenses/by/3.0/", "pagina": "https://ccmixter.org/files/1",
             "audio": self.base + percorso, "est": ".mp3"}
        b.update(altro)
        return b

    def test_scarica_e_annota_la_licenza(self):
        visti = []
        percorso, nuovo = cm.scarica(self.brano(), self.cartella, lambda f, t: visti.append((f, t)))
        self.assertTrue(nuovo)
        self.assertEqual(os.path.basename(percorso), "Zen Boy - Sutra.mp3")
        with open(percorso, "rb") as f:
            self.assertEqual(f.read(), FintoSito.AUDIO)
        self.assertEqual(visti[-1], (len(FintoSito.AUDIO), len(FintoSito.AUDIO)))
        with open(os.path.join(self.cartella, "LICENZE.txt"), encoding="utf-8") as f:
            testo = f.read()
        self.assertIn("Sutra — Zen Boy | CC BY 3.0", testo)
        self.assertEqual(cm.scarica(self.brano(), self.cartella)[1], False)   # la seconda volta c'era già
        self.assertEqual(sorted(os.listdir(self.cartella)), ["LICENZE.txt", "Zen Boy - Sutra.mp3"])

    def test_reindirizzamenti(self):
        self.assertTrue(cm.scarica(self.brano("/dentro"), self.cartella)[1])
        with self.assertRaises(cm.ErroreFonte):
            cm.scarica(self.brano("/fuori", titolo="Fuori"), self.cartella)

    def test_rifiuti(self):
        for percorso, titolo in (("/pagina", "Pagina"), ("/corto", "Corto")):
            with self.assertRaises(cm.ErroreFonte):
                cm.scarica(self.brano(percorso, titolo=titolo), self.cartella)
        with self.assertRaises(cm.ErroreFonte):
            cm.scarica(self.brano(audio="https://cattivo.com/a.mp3"), self.cartella)
        with self.assertRaises(cm.ErroreFonte) as e:
            cm.scarica(self.brano("/vietato", titolo="Vietato"), self.cartella)
        self.assertIn("https://ccmixter.org/files/1", str(e.exception))   # dice dove scaricarlo a mano
        self.assertEqual(os.listdir(self.cartella), [])   # nessun file a metà, nessuna riga in LICENZE.txt

    def test_coda(self):
        coda = cm.Coda(self.cartella)
        try:
            id1 = coda.aggiungi(self.brano())
            self.assertEqual(coda.aggiungi(self.brano()), id1)   # lo stesso brano non entra due volte
            coda.aggiungi(self.brano("/pagina", id="cc:2", titolo="Pagina"))
            with self.assertRaises(cm.ErroreFonte):
                coda.aggiungi(self.brano(audio="https://cattivo.com/a.mp3", id="cc:3"))
            for _ in range(100):
                stati = {l["titolo"]: l["stato"] for l in coda.elenco()}
                if "in coda" not in stati.values() and "scarico" not in stati.values():
                    break
                time.sleep(0.05)
            self.assertEqual(stati, {"Sutra": "fatto", "Pagina": "errore"})
        finally:
            coda.ferma.set()


class Pagina(unittest.TestCase):
    def setUp(self):
        self.cartella = tempfile.mkdtemp(prefix="prova-musica-")
        self.coda, self.token, self.porta = cm.Coda(self.cartella), "chiave", [0]
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), cm.crea_gestore(self.coda, self.token, self.porta,
                                                                            lambda: None))
        self.porta[0] = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.coda.ferma.set()
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.cartella, ignore_errors=True)

    def chiedi(self, metodo, percorso, corpo=None, token=True, host=None):
        c = http.client.HTTPConnection("127.0.0.1", self.porta[0], timeout=10)
        h = {"Content-Type": "application/json"}
        if token:
            h["X-Token"] = self.token
        if host:
            h["Host"] = host
        c.request(metodo, percorso, json.dumps(corpo) if corpo is not None else None, h)
        r = c.getresponse()
        dati = r.read()
        c.close()
        return r.status, (json.loads(dati) if "json" in r.getheader("Content-Type", "") else dati)

    def test_protezioni(self):
        self.assertEqual(self.chiedi("GET", "/api/fonti", token=False)[0], 403)
        self.assertEqual(self.chiedi("GET", "/", host="cattivo.com")[0], 403)
        self.assertEqual(self.chiedi("GET", "/")[0], 200)
        codice, d = self.chiedi("POST", "/api/scarica", {"id": "x", "audio": "https://cattivo.com/a.mp3"})
        self.assertEqual(codice, 400)

    def test_cerca_dalla_pagina(self):
        fonti = self.chiedi("GET", "/api/fonti")[1]
        self.assertEqual(fonti["cartella"], self.cartella)
        self.assertEqual([f["id"] for f in fonti["fonti"]], list(cm.FONTI))
        with mock.patch.dict(cm.CERCA, {"ccmixter": lambda q, p: ([{"titolo": q, "pagina": p}], False)}):
            self.assertEqual(self.chiedi("GET", "/api/cerca?fonte=ccmixter&q=jazz%20piano&pagina=2")[1],
                             {"risultati": [{"titolo": "jazz piano", "pagina": 2}], "altri": False})
        with mock.patch.dict(cm.CERCA, {"ccmixter": mock.Mock(side_effect=cm.ErroreFonte("il sito non risponde"))}):
            self.assertEqual(self.chiedi("GET", "/api/cerca?fonte=ccmixter&q=x"),
                             (502, {"errore": "il sito non risponde"}))


class Terminale(unittest.TestCase):
    def test_cerca_e_scegli(self):
        cartella = tempfile.mkdtemp(prefix="prova-musica-")
        brano = {"id": "cc:1", "tipo": "brano", "fonte": "ccMixter", "titolo": "Sutra", "artista": "Zen",
                 "durata": 61, "licenza": "CC BY 3.0", "audio": "https://ccmixter.org/a.mp3", "est": ".mp3"}
        album = {"id": "ia:A", "tipo": "album", "fonte": "Internet Archive", "titolo": "Album", "ia": "A",
                 "licenza": "CC0"}
        scaricati = []
        try:
            with mock.patch.dict(cm.CERCA, {"ccmixter": lambda q, p: ([brano], False),
                                            "archivio": lambda q, p: ([album], False)}), \
                    mock.patch.object(cm, "brani_archivio", return_value=[dict(brano, titolo="T1"), dict(brano, titolo="T2")]), \
                    mock.patch.object(cm, "scarica", side_effect=lambda b, c, a=None: (
                        scaricati.append(b["titolo"]) or os.path.join(c, b["titolo"] + ".mp3"), True)), \
                    mock.patch("builtins.input", return_value="1-2"), mock.patch("builtins.print"):
                codice = cm.main(["jazz", "--fonti", "archivio,ccmixter", "--cartella", cartella])
            self.assertEqual(codice, 0)
            self.assertEqual(sorted(scaricati), ["Sutra", "T1", "T2"])   # l'album intero e il brano
        finally:
            shutil.rmtree(cartella, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
