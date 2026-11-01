#!/usr/bin/env python3
"""Quanto aiuta la voce separata dagli strumenti (Demucs) prima di Whisper, su brani veri. Solo libreria standard:

    python3 misura_separa.py [--cache ~/.atlante-musica] [--json risultati.json] brano1.mp3 brano2.m4a ...

Per ogni brano: il testo (dal file o da LRCLIB, dalla cache dei testi del server: niente richieste se c'è già), Whisper
sul brano intero e sulla voce sola, e quante parole del testo riconosce ogni volta. Due misure: la quota del server
(allinea_parole, quella che decide se le parole si allineano) e difflib sulle parole normalizzate contro il testo. Più i
secondi di Whisper e di Demucs. Usa le stesse funzioni del server (Whisper, Separa, allinea_parole), lo stesso modello
di Whisper (quello in <cache>/testi/modelli) e lo stesso Demucs (ATLANTE_SEPARA, se no quello sul disco Cloud).
I file temporanei vanno in una cartella a parte, buttata alla fine. Non tocca il server né le schede dei brani.
"""
import argparse
import difflib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time

QUI = os.path.dirname(os.path.abspath(__file__))


def carica():
    spec = importlib.util.spec_from_file_location("musica_libreria_misura", os.path.join(QUI, "musica_libreria.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def parole_difflib(m, testo, sentite):
    """La quota di parole del testo (normalizzate) che difflib trova, in ordine, tra quelle sentite da Whisper."""
    a = [x for x in (m._chiave_parola(w) for riga in testo for w in riga.split()) if x]
    b = [x for x in (m._chiave_parola(w) for w in sentite) if x]
    if not a:
        return 0.0
    return sum(k.size for k in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks()) / len(a)


def ascolta(m, w, wav, r, lingua, prompt, a_tempo):
    """(quota del server, quota difflib, secondi di Whisper)."""
    t0 = time.time()
    j = w.trascrivi(wav, lingua, prompt)
    secondi = time.time() - t0
    sentite = [(t, f, x) for t, f, x, _p in m.parole_sentite(j, w.anticipo)]
    nuove = [{"t": x.get("t") if a_tempo else None, "f": x.get("fine") if a_tempo else None,
              "x": x.get("testo") or "", "p": [[None, None, p] for p in m._pezzi(x.get("testo"))]} for x in r["righe"]]
    quota = m.allinea_parole(nuove, sentite, a_tempo)
    return quota, parole_difflib(m, m._testi_della_risposta(r), [x for _t, _f, x in sentite]), secondi


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("brani", nargs="+")
    ap.add_argument("--cache", default="~/.atlante-musica", help="la cache del server (testi e modello di Whisper)")
    ap.add_argument("--json", help="dove scrivere i risultati")
    a = ap.parse_args()
    m = carica()
    m.CONF["cache"] = a.cache
    modelli = os.path.join(m._cart("cache"), "testi", "modelli")
    w = m.Whisper(modelli)
    os.environ.setdefault("ATLANTE_WHISPER_INSTALLA", "0")   # niente installazioni né scaricamenti da qui
    if not os.path.isfile(w.file_modello) or not w.prepara():
        sys.exit("Whisper non è pronto: %s" % (w.errore or w.stato or "manca il modello in " + modelli))
    sep = m.Separa()
    if not sep.pronta():
        sys.exit("Demucs non è pronto: %s" % sep.descrivi())
    print("Whisper %s (%s) · Demucs %s" % (w.cli, w.modello, sep.programma()), flush=True)
    tmp = tempfile.mkdtemp(prefix="misura-separa-")
    righe = []
    try:
        for p in a.brani:
            tag = m.leggi_tag(p)
            nm = m.da_nome(os.path.basename(p))
            titolo, artista = tag.get("title") or nm.get("title") or "", tag.get("artist") or nm.get("artist") or ""
            nome = "%s — %s" % (artista, titolo)
            try:
                r = m.testi_brano(titolo, artista, tag.get("album") or "", tag.get("duration") or 0, p, sfondo=True)
            except Exception as e:   # noqa: BLE001 — LRCLIB occupato: si passa al prossimo
                print("%s: testo non trovato (%s)" % (nome, e), flush=True)
                continue
            testo = m._testi_della_risposta(r)
            if r.get("stato") != "ok" or not testo:
                print("%s: senza testo" % nome, flush=True)
                continue
            lingua, prompt, a_tempo = m.lingua_testo(" ".join(testo)), " ".join(testo)[:400], bool(r.get("sincronizzato"))
            wav, voce = os.path.join(tmp, "brano.wav"), os.path.join(tmp, "voce.wav")
            try:
                m._wav16k(p, wav)
                q1, d1, s1 = ascolta(m, w, wav, r, lingua, prompt, a_tempo)
                t0 = time.time()
                sep.voce(p, voce, tmp, durata=tag.get("duration") or 0)
                sd = time.time() - t0
                q2, d2, s2 = ascolta(m, w, voce, r, lingua, prompt, a_tempo)
            except (m.ErroreTesti, OSError) as e:
                print("%s: non riuscito (%s)" % (nome, e), flush=True)
                continue
            finally:
                for x in (wav, voce):
                    m._cancella(x)
            x = {"brano": nome, "file": p, "parole": sum(len(t.split()) for t in testo), "lingua": lingua,
                 "durata": round(tag.get("duration") or 0), "quota": [round(q1, 3), round(q2, 3)],
                 "difflib": [round(d1, 3), round(d2, 3)], "secondi": {"whisper": round(s1, 1), "demucs": round(sd, 1),
                                                                      "whisper_voce": round(s2, 1)}}
            righe.append(x)
            print("%s: quota %d%% → %d%% · difflib %d%% → %d%% · Whisper %d s, Demucs %d s, Whisper voce %d s" % (
                nome, q1 * 100, q2 * 100, d1 * 100, d2 * 100, s1, sd, s2), flush=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if righe:
        med = lambda v: m._mediana(v)   # noqa: E731
        print("\n%d brani · quota mediana %d%% → %d%% · difflib mediano %d%% → %d%% · secondi mediani in più a brano %d" % (
            len(righe), med([x["quota"][0] for x in righe]) * 100, med([x["quota"][1] for x in righe]) * 100,
            med([x["difflib"][0] for x in righe]) * 100, med([x["difflib"][1] for x in righe]) * 100,
            med([x["secondi"]["demucs"] + x["secondi"]["whisper_voce"] for x in righe])))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(righe, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
