#!/usr/bin/env python3
"""Prove di Copia audio e video: python3 test_copia_audio_video.py

Ogni prova prepara una finta memoria in una cartella temporanea (con doppioni, file nascosti del Mac, cartelle di
sistema, un video senza estensione) e controlla cosa viene copiato e dove."""
import errno
import http.client
import json
import os
import shutil
import struct
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import copia_audio_video as cav  # noqa: E402

LUGLIO_2021 = 1625140800   # 1/7/2021 12:00 UTC


def mp3(seme, kb=40):
    return b"ID3\x03\x00\x00\x00\x00\x00\x00" + bytes((seme + i) % 251 for i in range(kb * 1024))


def mov(quando=None, marca=b"qt  "):
    """Un piccolo MOV/MP4: ftyp, moov con mvhd (data di ripresa), mdat."""
    ftyp = struct.pack(">I4s4sI", 16, b"ftyp", marca, 0)
    sec = (quando + cav.SECONDI_1904) if quando else 0
    mvhd_corpo = bytes([0, 0, 0, 0]) + struct.pack(">II", sec, sec) + b"\x00" * 88
    mvhd = struct.pack(">I4s", 8 + len(mvhd_corpo), b"mvhd") + mvhd_corpo
    moov = struct.pack(">I4s", 8 + len(mvhd), b"moov") + mvhd
    dati = os.urandom(30000)
    return ftyp + moov + struct.pack(">I4s", 8 + len(dati), b"mdat") + dati


def scrivi(percorso, dati, mtime=None):
    os.makedirs(os.path.dirname(percorso), exist_ok=True)
    with open(percorso, "wb") as f:
        f.write(dati)
    if mtime:
        os.utime(percorso, (mtime, mtime))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="prova-copia-")
        self.mem = os.path.join(self.tmp, "CHIAVETTA")
        self.dest = os.path.join(self.tmp, "Copiati")
        m = self.mem
        scrivi(os.path.join(m, "Musica", "uno.mp3"), mp3(1), LUGLIO_2021 - 86400 * 400)
        scrivi(os.path.join(m, "Musica", "uno - Copia.mp3"), mp3(1))            # doppione con un altro nome
        scrivi(os.path.join(m, "Musica", "due.FLAC"), b"fLaC" + mp3(2)[4:])       # estensione maiuscola
        scrivi(os.path.join(m, "Musica", "._uno.mp3"), b"\x00\x05\x16\x07" * 1024)  # dati del Mac sulle chiavette
        scrivi(os.path.join(m, ".Trashes", "501", "cestinato.mp3"), mp3(3))
        scrivi(os.path.join(m, "System Volume Information", "x.wav"), b"RIFF0000WAVE" + b"\x00" * 100)
        scrivi(os.path.join(m, "DCIM", "100MEDIA", "VID_0001.MOV"), mov(LUGLIO_2021))
        scrivi(os.path.join(m, "DCIM", "100MEDIA", "senzanome"), mov(None, b"isom"))  # video senza estensione
        scrivi(os.path.join(m, "Documenti", "lettera.txt"), b"ciao" * 100)
        scrivi(os.path.join(m, "Documenti", "foto.jpg"), b"\xff\xd8\xff\xe0" + b"\x00" * 2000)
        scrivi(os.path.join(m, "Documenti", "registrazione.dat"), b"ID3\x03" + b"\x00" * 3000)  # mp3 travestito

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def nomi(self, file):
        return sorted(f["rel"].replace(os.sep, "/") + f["est"] for f in file)

    def tutti_i_file(self, cartella):
        trovati = []
        for dove, _, nomi in os.walk(cartella):
            for n in nomi:
                trovati.append(os.path.relpath(os.path.join(dove, n), cartella).replace(os.sep, "/"))
        return sorted(trovati)


class Riconoscere(unittest.TestCase):
    def test_firme(self):
        d = tempfile.mkdtemp()
        try:
            casi = {b"ID3\x04" + b"\x00" * 600: ("audio", ".mp3"), b"fLaC" + b"\x00" * 600: ("audio", ".flac"),
                    b"OggS" + b"\x00" * 30 + b"vorbis" + b"\x00" * 600: ("audio", ".ogg"),
                    b"OggS" + b"\x00" * 30 + b"theora" + b"\x00" * 600: ("video", ".ogv"),
                    b"RIFF\x00\x00\x00\x00WAVE" + b"\x00" * 600: ("audio", ".wav"),
                    b"RIFF\x00\x00\x00\x00AVI " + b"\x00" * 600: ("video", ".avi"),
                    b"FORM\x00\x00\x00\x00AIFF" + b"\x00" * 600: ("audio", ".aiff"),
                    struct.pack(">I4s4s", 20, b"ftyp", b"M4A ") + b"\x00" * 600: ("audio", ".m4a"),
                    struct.pack(">I4s4s", 20, b"ftyp", b"qt  ") + b"\x00" * 600: ("video", ".mov"),
                    struct.pack(">I4s4s", 20, b"ftyp", b"3gp5") + b"\x00" * 600: ("video", ".3gp"),
                    struct.pack(">I4s4s", 20, b"ftyp", b"mp42") + b"\x00" * 600: ("video", ".mp4"),
                    b"\x1a\x45\xdf\xa3" + b"\x00" * 20 + b"webm" + b"\x00" * 600: ("video", ".webm"),
                    b"\x1a\x45\xdf\xa3" + b"\x00" * 20 + b"matroska" + b"\x00" * 600: ("video", ".mkv"),
                    b"\xff\xfb\x90\x64" + b"\x00" * 600: ("audio", ".mp3"),
                    b"#!AMR\n" + b"\x00" * 600: ("audio", ".amr"),
                    b"\xff\xd8\xff\xe0" + b"\x00" * 600: (None, ""),          # una foto JPEG non è audio
                    b"PK\x03\x04" + b"\x00" * 600: (None, ""),                # uno zip nemmeno
                    b"ciao": (None, "")}
            for i, (dati, atteso) in enumerate(casi.items()):
                p = os.path.join(d, "f%d" % i)
                scrivi(p, dati)
                self.assertEqual(cav.tipo_da_contenuto(p), atteso, dati[:12])
        finally:
            shutil.rmtree(d)

    def test_nome(self):
        self.assertEqual(cav.tipo_da_nome("Canzone.MP3"), "audio")
        self.assertEqual(cav.tipo_da_nome("vacanze.mov"), "video")
        self.assertIsNone(cav.tipo_da_nome("foto.heic"))

    def test_data_ripresa(self):
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "v.mov")
            scrivi(p, mov(LUGLIO_2021))
            self.assertEqual(cav.data_ripresa(p, 123), LUGLIO_2021)
            scrivi(p, mov(None))                       # data a zero: vale la data del file
            self.assertEqual(cav.data_ripresa(p, 123), 123)
            scrivi(p, b"\x00\x00\x00\x08ftyp" + b"\xff" * 50)   # file rovinato: niente errori
            self.assertEqual(cav.data_ripresa(p, 123), 123)
        finally:
            shutil.rmtree(d)

    def test_misura(self):
        self.assertEqual(cav.misura(500), "500 byte")
        self.assertEqual(cav.misura(1536), "1,5 KB")
        self.assertEqual(cav.misura(5 * 1024 ** 3), "5,0 GB")


class Analisi(Base):
    def test_trova_solo_audio_e_video(self):
        file, problemi = cav.analizza(self.mem)
        self.assertEqual(self.nomi(file), ["DCIM/100MEDIA/VID_0001.MOV", "DCIM/100MEDIA/senzanome.mp4",
                                           "Musica/due.FLAC", "Musica/uno - Copia.mp3", "Musica/uno.mp3"])
        self.assertEqual(problemi, [])

    def test_solo_audio_solo_video(self):
        self.assertEqual(len(cav.analizza(self.mem, video=False)[0]), 3)
        self.assertEqual(len(cav.analizza(self.mem, audio=False)[0]), 2)

    def test_tutti_trova_le_estensioni_sbagliate(self):
        file, _ = cav.analizza(self.mem, tutti=True)
        self.assertIn("Documenti/registrazione.dat.mp3", self.nomi(file))
        self.assertNotIn("Documenti/foto.jpg.mp3", self.nomi(file))

    def test_minimo(self):
        file, _ = cav.analizza(self.mem, min_byte=35 * 1024)
        self.assertEqual(self.nomi(file), ["Musica/due.FLAC", "Musica/uno - Copia.mp3", "Musica/uno.mp3"])

    def test_destinazione_dentro_la_memoria_si_salta(self):
        dentro = os.path.join(self.mem, "Copie")
        scrivi(os.path.join(dentro, "vecchia.mp3"), mp3(9))
        file, _ = cav.analizza(self.mem, escludi=[dentro])
        self.assertFalse(any(f["rel"].startswith("Copie") for f in file))

    def test_riepilogo(self):
        r = cav.riepilogo(cav.analizza(self.mem)[0])
        self.assertEqual((r["audio"]["n"], r["video"]["n"]), (3, 2))
        self.assertEqual(r["formati"]["mp3"]["n"], 2)
        self.assertEqual(r["formati"]["flac"]["n"], 1)

    def test_memoria_che_non_esiste(self):
        with self.assertRaises(FileNotFoundError):
            cav.analizza(os.path.join(self.tmp, "nessuna"))


class Copia(Base):
    def test_stesse_cartelle_senza_doppioni(self):
        file, _ = cav.analizza(self.mem)
        r = cav.copia(file, self.mem, self.dest)
        self.assertEqual((r["copiati"], r["doppioni"], r["gia"], r["errori"]), (4, 1, 0, []))
        self.assertEqual(self.tutti_i_file(self.dest), [
            "CHIAVETTA/DCIM/100MEDIA/VID_0001.MOV", "CHIAVETTA/DCIM/100MEDIA/senzanome.mp4",
            "CHIAVETTA/Musica/due.FLAC", "CHIAVETTA/Musica/uno.mp3"])
        orig, copia = os.path.join(self.mem, "Musica", "uno.mp3"), os.path.join(self.dest, "CHIAVETTA", "Musica", "uno.mp3")
        with open(orig, "rb") as a, open(copia, "rb") as b:
            self.assertEqual(a.read(), b.read())
        self.assertAlmostEqual(os.path.getmtime(copia), os.path.getmtime(orig), delta=1)   # stessa data
        self.assertEqual(r["elenco_doppioni"][0][0].replace(os.sep, "/"), "Musica/uno - Copia.mp3")

    def test_rilanciare_non_ricopia(self):
        file, _ = cav.analizza(self.mem)
        cav.copia(file, self.mem, self.dest)
        r = cav.copia(file, self.mem, self.dest)
        self.assertEqual((r["copiati"], r["gia"], r["doppioni"]), (0, 4, 1))
        r = cav.copia(file, self.mem, self.dest, doppioni=True)
        self.assertEqual((r["copiati"], r["gia"]), (1, 4))   # solo «uno - Copia.mp3», che prima era un doppione
        r = cav.copia(file, self.mem, self.dest, doppioni=True)
        self.assertEqual((r["copiati"], r["gia"]), (0, 5))

    def test_in_ordine_di_data(self):
        file, _ = cav.analizza(self.mem)
        r = cav.copia(file, self.mem, self.dest, modo="data")
        self.assertEqual(r["copiati"], 4)
        anno_mp3 = time.strftime("%Y/%Y-%m", time.localtime(LUGLIO_2021 - 86400 * 400))
        trovati = self.tutti_i_file(self.dest)
        self.assertIn("Video/2021/2021-07/VID_0001.MOV", trovati)   # la data della ripresa, non quella del file
        self.assertIn("Audio/%s/uno.mp3" % anno_mp3, trovati)

    def test_nome_preso_da_un_file_diverso(self):
        scrivi(os.path.join(self.dest, "CHIAVETTA", "Musica", "uno.mp3"), mp3(77))
        file, _ = cav.analizza(self.mem, video=False)
        r = cav.copia(file, self.mem, self.dest)
        self.assertEqual(r["copiati"], 2)
        self.assertIn("CHIAVETTA/Musica/uno (2).mp3", self.tutti_i_file(self.dest))
        r = cav.copia(file, self.mem, self.dest)
        self.assertEqual((r["copiati"], r["gia"], r["doppioni"]), (0, 2, 1))
        self.assertNotIn("CHIAVETTA/Musica/uno (3).mp3", self.tutti_i_file(self.dest))

    def test_doppione_gia_nella_destinazione_con_altro_nome(self):
        scrivi(os.path.join(self.dest, "Vecchie", "la mia canzone.mp3"), mp3(1))
        file, _ = cav.analizza(self.mem, video=False)
        r = cav.copia(file, self.mem, self.dest)
        self.assertEqual((r["copiati"], r["doppioni"]), (1, 2))   # uno.mp3 e la sua copia c'erano già

    def test_fermare_non_lascia_file_a_meta(self):
        file, _ = cav.analizza(self.mem)
        ferma = threading.Event()

        def avanti(r, f, n, stato):
            if n:
                ferma.set()   # fermato a metà del primo file

        with self.assertRaises(cav.Fermato):
            cav.copia(file, self.mem, self.dest, ferma=ferma, avanzamento=avanti)
        self.assertEqual(self.tutti_i_file(self.dest), [])

    def test_copia_diversa_dall_originale(self):
        file, _ = cav.analizza(self.mem, video=False)
        with mock.patch.object(cav, "impronta", return_value="0" * 64):
            r = cav.copia(file, self.mem, self.dest, doppioni=True)
        self.assertEqual(r["copiati"], 0)
        self.assertEqual(len(r["errori"]), 3)
        self.assertIn("non è uguale", r["errori"][0][1])
        self.assertEqual(self.tutti_i_file(self.dest), [])

    def test_senza_controllo(self):
        file, _ = cav.analizza(self.mem, video=False)
        with mock.patch.object(cav, "impronta", side_effect=AssertionError("non doveva rileggere")):
            r = cav.copia(file, self.mem, self.dest, doppioni=True, verifica=False)
        self.assertEqual(r["copiati"], 3)

    def test_file_illeggibile(self):
        file, _ = cav.analizza(self.mem, video=False)
        vero = cav.copia_file

        def rotto(origine, *a, **k):
            if origine.endswith("due.FLAC"):
                raise OSError(errno.EIO, "Errore di input/output")
            return vero(origine, *a, **k)

        with mock.patch.object(cav, "copia_file", side_effect=rotto):
            r = cav.copia(file, self.mem, self.dest)
        self.assertEqual(r["copiati"], 1)
        self.assertEqual([e[1] for e in r["errori"]], ["Errore di input/output"])

    def test_destinazione_piena(self):
        file, _ = cav.analizza(self.mem)
        with mock.patch.object(cav, "copia_file", side_effect=OSError(errno.ENOSPC, "No space left on device")):
            with self.assertRaises(OSError) as e:
                cav.copia(file, self.mem, self.dest)
        self.assertIn("piena", e.exception.strerror)

    def test_memoria_scollegata(self):
        file, _ = cav.analizza(self.mem)
        shutil.rmtree(self.mem)
        with self.assertRaises(OSError) as e:
            cav.copia(file, self.mem, self.dest)
        self.assertIn("scollegata", e.exception.strerror)

    def test_rapporto(self):
        file, problemi = cav.analizza(self.mem)
        r = cav.copia(file, self.mem, self.dest)
        p = cav.scrivi_rapporto(self.dest, self.mem, file, r, problemi)
        with open(p, encoding="utf-8") as f:
            testo = f.read()
        self.assertIn("Copiati:               4", testo)
        self.assertIn("uno - Copia.mp3", testo)


class Terminale(Base):
    def test_prova_e_copia(self):
        with mock.patch("builtins.print"):
            self.assertEqual(cav.main([self.mem, "--prova"]), 0)
            self.assertFalse(os.path.exists(self.dest))
            self.assertEqual(cav.main([self.mem, self.dest, "--solo", "video", "--ordina", "data"]), 0)
        trovati = self.tutti_i_file(self.dest)
        self.assertIn("Video/2021/2021-07/VID_0001.MOV", trovati)
        self.assertTrue(any(t.startswith("Rapporto della copia") for t in trovati))

    def test_memorie(self):
        with mock.patch("builtins.print") as p:
            self.assertEqual(cav.main(["--memorie"]), 0)
        self.assertTrue(p.called)


class Pagina(Base):
    def setUp(self):
        super().setUp()
        self.lavoro, self.token, self.porta = cav.Lavoro(), "chiave-di-prova", [0]
        self.server = cav.ThreadingHTTPServer(("127.0.0.1", 0),
                                              cav.crea_gestore(self.lavoro, self.token, self.porta, lambda: None))
        self.porta[0] = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

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
        return r.status, (json.loads(dati) if r.getheader("Content-Type", "").startswith("application/json") else dati)

    def aspetta(self, fasi):
        for _ in range(200):
            s = self.chiedi("GET", "/api/stato")[1]
            if s["fase"] in fasi:
                return s
            time.sleep(0.05)
        self.fail("il lavoro non finisce: %s" % s)

    def test_protezioni(self):
        self.assertEqual(self.chiedi("GET", "/api/stato", token=False)[0], 403)        # senza chiave
        self.assertEqual(self.chiedi("GET", "/", host="sito-cattivo.com:%d" % self.porta[0])[0], 403)
        codice, pagina = self.chiedi("GET", "/")
        self.assertEqual(codice, 200)
        self.assertIn(b'content="chiave-di-prova"', pagina)

    def test_dalla_pagina(self):
        self.assertTrue(self.chiedi("GET", "/api/memorie")[1]["memorie"])
        cartelle = self.chiedi("GET", "/api/cartelle?p=" + self.mem)[1]
        self.assertEqual([c["nome"] for c in cartelle["cartelle"]], ["DCIM", "Documenti", "Musica"])
        self.assertEqual(self.chiedi("POST", "/api/analizza", {"sorgente": self.mem, "audio": True, "video": True})[0], 200)
        s = self.aspetta(("analizzato", "errore"))
        self.assertEqual((s["riepilogo"]["audio"]["n"], s["riepilogo"]["video"]["n"]), (3, 2))
        codice, d = self.chiedi("POST", "/api/copia", {"destinazione": self.mem})
        self.assertEqual((codice, d["errore"]), (400, "La destinazione non può essere la memoria stessa"))
        self.assertEqual(self.chiedi("POST", "/api/copia", {"destinazione": self.dest, "modo": "data"})[0], 200)
        s = self.aspetta(("fine", "errore"))
        self.assertEqual((s["fase"], s["copiati"], s["doppioni"], s["errori"]), ("fine", 4, 1, 0))
        self.assertTrue(os.path.isfile(s["rapporto"]))

    def test_nuova_cartella(self):
        codice, d = self.chiedi("POST", "/api/nuova-cartella", {"genitore": self.tmp, "nome": "Nuova"})
        self.assertEqual(codice, 200)
        self.assertTrue(os.path.isdir(d["percorso"]))
        self.assertEqual(self.chiedi("POST", "/api/nuova-cartella", {"genitore": self.tmp, "nome": "../fuori"})[0], 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
