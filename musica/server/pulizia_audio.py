#!/usr/bin/env python3
"""Pulizia audio di Atlante Musica: tutti i brani della libreria (il disco Cloud del Mac mini) puliti e allo stesso
volume, senza perdere niente. Lo carica musica_libreria.py (Gestione → Pulizia audio, API gestione/pulizia-audio).

Cosa fa a ogni brano, uno alla volta e a bassa priorità:
1. lo ASCOLTA tutto con ffmpeg (ebur128 + astats): volume percepito (LUFS, come Spotify e Apple Music), picchi veri
   (dBTP, quelli che distorcono dopo la conversione), scostamento DC, tagli (clipping);
2. DECIDE (vedi piano): un brano già giusto non si tocca; per gli altri l'obiettivo è -14 LUFS con i picchi sotto
   -1 dBTP (-1,5 dopo una codifica con perdita), mai più di +10 dB;
3. MP3 (quasi tutta la libreria): il volume cambia SENZA ricodificare, come mp3gain: in ogni frame si sposta il
   «global_gain» (passi da 1,5 dB). Niente perdita di qualità, stessa durata, stessi byte dei tag; il cambio si disfa
   al bit (il passo indietro si prova prima di scrivere). L'undo sta nel registro e nel giornale sul disco della musica;
4. FLAC e ALAC (senza perdita), AAC e gli MP3 con uno scostamento DC forte: si ricodificano nello stesso formato con
   filtro passa-alto a 15 Hz (via DC e rombi sotto l'udibile), ricostruzione dei picchi tagliati (adeclip, solo senza
   perdita), volume preciso e dither a 16 bit. L'originale resta, identico, in «.atlante-originali» nella cartella
   della musica (nascosta: la libreria non la legge), con lo stesso percorso;
5. CONTROLLA prima di mettere il file al suo posto: si decodifica tutto senza errori in più, stessa durata (al
   campione), volume e picchi come previsto, stessi tag e copertina. Se un controllo non passa l'originale non si tocca;
6. il file nuovo prende il posto del vecchio in un colpo (os.replace), con la data di prima più un secondo: così il
   Mac rilegge il brano (stesso id: preferiti, playlist e ascolti restano), l'iPhone ne riscarica la copia e
   «Aggiunti di recente» non cambia ordine. musica_libreria.py sposta sulla firma nuova testi, parole di Whisper e
   videoclip (il tempo del brano non cambia: niente da rifare) e rimisura solo il volume per il «volume uniforme».

Niente denoise a tappeto: su musica vera toglie aria e strumenti più del rumore. Solo libreria standard di Python;
ffmpeg e ffprobe (Homebrew) per ascoltare e codificare.
"""
import collections
import errno
import hashlib
import json
import math
import os
import re
import shutil
import stat
import subprocess
import threading
import time
import traceback

VERSIONE = 1                    # cambia quando cambia il modo di decidere: i brani «buoni» e «saltati» si riascoltano
OBIETTIVO = -14.0               # LUFS
TOLLERANZA = 1.0                # LU: entro ±1 dall'obiettivo il volume è già giusto
TOLLERANZA_RICODIFICA = 2.0     # LU: per ricodificare un file con perdita (AAC) solo per il volume ne serve di più
PICCO_SENZA_PERDITA = -1.0      # dBTP al più dopo un cambio senza perdita (guadagno MP3, FLAC, ALAC)
PICCO_CON_PERDITA = -1.5        # dBTP al più prima di una codifica con perdita (la codifica alza un po' i picchi)
PICCO_ROTTO = -0.1              # dBTP oltre i quali il brano distorce già: va abbassato
ALZA_MAX = 10.0                 # dB al più di aumento (oltre: un brano silenzioso apposta, o quasi silenzio)
PASSO_MP3 = 1.5                 # dB di un passo di global_gain (2 ** (1/4))
DC_CON_PERDITA = 0.01           # scostamento DC che vale una ricodifica di un MP3 (-40 dBFS: sotto non si sente)
DC_SENZA_PERDITA = 0.001
PASSA_ALTO = 15                 # Hz: sotto non c'è musica, solo DC e rombi (sui 30 Hz del basso: -0,3 dB)
CLIP_PICCO = -0.2               # dBFS: un file senza perdita è tagliato se tocca il massimo…
CLIP_VOLTE = 50                 # …tante volte…
CLIP_PIATTO = -20.0             # …e con campioni uguali di fila al massimo (flat factor di astats, in dB)
DECLIP_MARGINE = 3.0            # dB lasciati ai picchi ricostruiti da adeclip
DURATA_MIN = 5.0
DURATA_MAX = 3 * 3600
FILE_MAX = 400 * 1024 ** 2      # un file più grande non è un brano
MEMORIA_MAX = 120 * 1024 ** 2   # il guadagno MP3 tiene il file in memoria (tre copie): oltre si ricodifica
TEMPO_ANALISI = 1800
TEMPO_CODIFICA = 3600
ARCHIVIO = ".atlante-originali"
GIORNALE = "guadagni.jsonl"
SPAZIO_MIN = 100 * 1024 ** 3    # sotto i 100 GB liberi sul disco della musica ci si ferma (ATLANTE_PULIZIA_SPAZIO_MIN_GB)
SPAZIO_CACHE = 2 * 1024 ** 3    # e sotto i 2 GB sul disco della cache (i file di lavoro passano da lì)
PAUSA_MAC = 600                 # un errore del Mac (disco scollegato, ffmpeg fermato): si riprova fra 10 minuti
PAUSA_SPAZIO = 1800
RIPROVA_ERRORE = 86400          # un brano non riuscito si riprova il giorno dopo, al più ERRORI_MAX volte
ERRORI_MAX = 3
SALVA_OGNI = 60                 # secondi tra due scritture del registro (e subito a ogni pausa)
LOTTO = 50
RIPOSO = 300                    # niente da fare: si riguarda fra 5 minuti (brani nuovi scaricati)
TRA_BRANI = 1.0
ULTIMI = 40
CHIAVI_TAG = ("title", "artist", "album", "albumArtist", "track", "disc", "year", "genre", "compilation", "composer",
              "work", "movement")
# volume scritto nei tag da altri programmi: dopo la pulizia sarebbe sbagliato (un lettore che lo usa sbaglierebbe)
CHIAVI_VOLUME = ("REPLAYGAIN_TRACK_GAIN", "REPLAYGAIN_TRACK_PEAK", "REPLAYGAIN_ALBUM_GAIN", "REPLAYGAIN_ALBUM_PEAK",
                 "REPLAYGAIN_REFERENCE_LOUDNESS", "R128_TRACK_GAIN", "R128_ALBUM_GAIN", "iTunNORM")
FILTRO_ANALISI = "aformat=sample_fmts=fltp,astats,ebur128=peak=true:framelog=quiet"


class Fermato(Exception):
    """Il lavoro va in pausa o il server esce: il brano resta da fare, niente si segna."""


class ErroreMacchina(Exception):
    """Un problema del Mac, non del brano (disco scollegato o pieno, ffmpeg che non c'è o fermato dal sistema)."""


class ErroreBrano(Exception):
    """Questo brano non si riesce a pulire: resta com'è e il motivo va nel registro."""


class Cambiato(Exception):
    """Il file è cambiato mentre lo si puliva (o non c'è più): si riprova al prossimo giro."""


class NonSenzaPerdita(Exception):
    """Il guadagno MP3 senza ricodifica non si può fare su questo file (si passa alla ricodifica)."""


def _numero(v):
    try:
        x = float(str(v).strip().split()[0])
    except (ValueError, IndexError):
        return None
    return None if math.isnan(x) else x


def _finito(x, cifre=1):
    """Un numero da mettere in un JSON (niente inf né nan: JSON.parse non li conosce)."""
    return round(x, cifre) if isinstance(x, (int, float)) and math.isfinite(x) else None


def _intero(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def firma(st):
    """La firma di un file come la calcola la libreria: «dimensione:secondi della data»."""
    return "%d:%d" % (st.st_size, int(st.st_mtime))


def _cancella(p):
    try:
        os.unlink(p)
    except OSError:
        pass


def _sha1_file(p):
    h = hashlib.sha1()
    with open(p, "rb") as f:
        for blocco in iter(lambda: f.read(1 << 20), b""):
            h.update(blocco)
    return h.hexdigest()


def _coda(testo, n=300):
    righe = [r for r in testo.strip().splitlines() if r.strip()]
    return " · ".join(righe[-3:])[-n:]


# ---------- MP3: frame, tag, guadagno senza ricodifica ----------

_KBPS = {True: (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),    # MPEG-1 Layer III
         False: (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)}       # MPEG-2 e 2.5 Layer III
_HZ = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}


def _ss32(b, p):
    return ((b[p] & 127) << 21) | ((b[p + 1] & 127) << 14) | ((b[p + 2] & 127) << 7) | (b[p + 3] & 127)


def _testa_frame(b, i):
    """(lunghezza, mpeg1, canali, crc) del frame MPEG Layer III che comincia in i; None se lì non ce n'è uno."""
    if i + 4 > len(b) or b[i] != 0xFF or (b[i + 1] & 0xE0) != 0xE0:
        return None
    ver, layer = (b[i + 1] >> 3) & 3, (b[i + 1] >> 1) & 3
    bri, sri, pad = b[i + 2] >> 4, (b[i + 2] >> 2) & 3, (b[i + 2] >> 1) & 1
    if ver == 1 or layer != 1 or bri in (0, 15) or sri == 3:
        return None   # versione riservata, non Layer III, formato libero o valori vietati
    m1 = ver == 3
    lun = (144000 if m1 else 72000) * _KBPS[m1][bri] // _HZ[ver][sri] + pad
    return lun, m1, 1 if (b[i + 3] >> 6) == 3 else 2, not (b[i + 1] & 1)


def _chiave_frame(b, i):
    return b[i + 1] & 0xFE, (b[i + 2] >> 2) & 3   # versione, layer e frequenza: uguali in tutto lo stream


def inizio_audio(b):
    """Dove finiscono i tag ID3v2 in testa (anche più d'uno di fila) e cominciano i frame."""
    p = 0
    while p + 10 <= len(b) and b[p:p + 3] == b"ID3":
        p += 10 + _ss32(b, p + 6) + (10 if b[p + 5] & 0x10 else 0)
    return min(p, len(b))


def code_mp3(b):
    """(fine dell'audio, (inizio, fine) del tag APE o None): in coda possono esserci APE, Lyrics3 e ID3v1."""
    e, ape = len(b), None
    if e >= 128 and b[e - 128:e - 125] == b"TAG":
        e -= 128
    if e >= 15 and b[e - 9:e] == b"LYRICS200":
        try:
            e -= int(b[e - 15:e - 9]) + 15
        except ValueError:
            pass
    if e >= 32 and b[e - 32:e - 24] == b"APETAGEX":
        dim, fl = int.from_bytes(b[e - 20:e - 16], "little"), int.from_bytes(b[e - 12:e - 8], "little")
        inizio = e - dim - (32 if fl & 0x80000000 else 0)
        if 0 <= inizio < e:
            ape, e = (inizio, e), inizio
    return max(e, 0), ape


def frame_mp3(b):
    """[(posizione, lunghezza, mpeg1, canali, crc)] dei frame Layer III. I byte che non sono frame (spazzatura tra un
    frame e l'altro, un frame a metà in fondo) si saltano e restano com'erano. ValueError se di frame non ce ne sono."""
    i, (e, _) = inizio_audio(b), code_mp3(b)
    out, fissa, saltati = [], None, 0
    while i + 4 <= e:
        t = _testa_frame(b, i)
        if t and i + t[0] <= e:
            k, j = _chiave_frame(b, i), i + t[0]
            # agganciati allo stream basta un'intestazione coerente; per agganciarsi serve anche quella dopo
            if k == fissa or (j + 4 <= e and _testa_frame(b, j) and _chiave_frame(b, j) == k):
                out.append((i,) + t)
                fissa, i = k, j
                continue
        elif t:
            break   # l'ultimo frame è a metà: resta com'è
        fissa = None
        j = b.find(b"\xff", i + 1, e)
        if j < 0:
            saltati += e - i
            break
        saltati += j - i
        i = j
    if not out:
        raise ValueError("nessun frame MPEG Layer III")
    if saltati > max(4096, (e - inizio_audio(b)) // 20):
        raise ValueError("troppi byte che non sono frame (%d)" % saltati)
    return out


def _bit(b, pos, n):
    """n bit (al più 16) dal bit pos (0 = il più alto del primo byte)."""
    i, s = pos >> 3, pos & 7
    return (((b[i] << 16) | (b[i + 1] << 8) | b[i + 2]) >> (24 - s - n)) & ((1 << n) - 1)


def _scrivi8(b, pos, v):
    i, s = pos >> 3, pos & 7
    if not s:
        b[i] = v
        return
    m = (0xFF << (8 - s)) & 0xFFFF
    w = ((((b[i] << 8) | b[i + 1]) & ~m) | (v << (8 - s))) & 0xFFFF
    b[i], b[i + 1] = w >> 8, w & 0xFF


def crc16(dati, crc=0xFFFF):
    """CRC-16 dei frame MPEG audio (polinomio 0x8005, da 0xFFFF, senza riflessioni: CRC-16/CMS)."""
    for x in dati:
        crc ^= x << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x8005) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def _info_laterale(f):
    i, _, m1, can, crc = f
    return i + 4 + (2 if crc else 0), (17 if can == 1 else 32) if m1 else (9 if can == 1 else 17)


def _crc_frame(b, f):
    si, lsi = _info_laterale(f)
    return crc16(bytes(b[f[0] + 2:f[0] + 4]) + bytes(b[si:si + lsi]))


def _campi_guadagno(b, f):
    """Posizioni in bit dei global_gain dei granuli con audio (part2_3_length > 0) di un frame. Il frame Xing/Info/VBRI
    (il riassunto del file, senza musica) si lascia stare."""
    i, lun = f[0], f[1]
    si, lsi = _info_laterale(f)
    if si + lsi > i + lun or si + lsi + 2 > len(b):
        return []
    if bytes(b[si + lsi:si + lsi + 4]) in (b"Xing", b"Info") or bytes(b[i + 36:i + 40]) == b"VBRI":
        return []
    m1, can = f[2], f[3]
    if m1:   # main_data_begin 9, private_bits 5 o 3, scfsi 4 per canale; 2 granuli da 59 bit per canale
        inizio, granuli, passo = 9 + (5 if can == 1 else 3) + 4 * can, 2, 59
    else:    # main_data_begin 8, private_bits 1 o 2; 1 granulo da 63 bit per canale
        inizio, granuli, passo = 8 + (1 if can == 1 else 2), 1, 63
    pos, base = [], si * 8
    for g in range(granuli):
        for c in range(can):
            p = base + inizio + (g * can + c) * passo
            if _bit(b, p, 12):          # part2_3_length (12 bit), poi big_values (9), poi global_gain (8)
                pos.append(p + 21)
    return pos


def guadagno_mp3(b, passi, frames=None):
    """Cambia di `passi` × 1,5 dB il volume dell'MP3 nel bytearray b, senza ricodificarlo: ogni granulo con audio ha il
    suo global_gain spostato di `passi`; il CRC dei frame che ce l'hanno giusto si ricalcola (quelli sbagliati restano
    sbagliati uguali, così il cambio si disfa al bit). Prima si controlla tutto: se un granulo uscirebbe da 0–255
    ValueError e b non cambia. Restituisce quanti frame ha cambiato."""
    frames = frames if frames is not None else frame_mp3(b)
    cambi = []
    for f in frames:
        pos = _campi_guadagno(b, f)
        for p in pos:
            if not 0 <= _bit(b, p, 8) + passi <= 255:
                raise ValueError("un granulo uscirebbe dai valori possibili (global_gain %d)" % _bit(b, p, 8))
        if pos:
            cambi.append((f, pos))
    for f, pos in cambi:
        crc_ok = f[4] and _crc_frame(b, f) == int.from_bytes(b[f[0] + 4:f[0] + 6], "big")
        for p in pos:
            _scrivi8(b, p, _bit(b, p, 8) + passi)
        if crc_ok:
            b[f[0] + 4:f[0] + 6] = _crc_frame(b, f).to_bytes(2, "big")
    return len(cambi)


def sha_audio(b):
    """Impronta della sola parte audio di un MP3 (senza tag in testa e in coda): resta uguale se cambiano i tag."""
    e, _ = code_mp3(b)
    return hashlib.sha1(bytes(b[inizio_audio(b):e])).hexdigest()


def _descrizione(enc, d):
    """Il primo testo (fino al terminatore) di un frame ID3 con codifica enc."""
    if enc in (1, 2):
        k = 0
        while k + 1 < len(d) and d[k:k + 2] != b"\x00\x00":
            k += 2
        s = bytes(d[:k])
        if s[:2] in (b"\xff\xfe", b"\xfe\xff"):
            return s.decode("utf-16", "replace")
        return s.decode("utf-16-be" if enc == 2 else "utf-16-le", "replace")
    return bytes(d).split(b"\x00", 1)[0].decode("latin-1" if enc == 0 else "utf-8", "replace")


def _frame_di_volume(fid, dati):
    if fid in (b"RVA2", b"RVAD"):
        return True
    if fid == b"TXXX" and dati:
        return _descrizione(dati[0], dati[1:]).strip("\ufeff ").upper().startswith(("REPLAYGAIN_", "MP3GAIN_"))
    if fid == b"COMM" and len(dati) > 4:
        return _descrizione(dati[0], dati[4:]).strip("\ufeff ").lower() == "itunnorm"
    return False


def id3_senza_volume(testa):
    """I tag ID3v2 in testa senza i frame del volume scritti da altri programmi (ReplayGain, MP3Gain, RVA2, iTunNORM:
    dopo la pulizia sarebbero sbagliati), della stessa lunghezza (riempimento al posto dei frame tolti). Un tag che non
    si sa leggere con sicurezza (ID3v2.2, unsynchronisation, intestazione estesa) resta com'è."""
    if len(testa) < 10 or testa[:3] != b"ID3" or testa[3] not in (3, 4) or testa[5] & 0xC0:
        return testa
    v, dim = testa[3], _ss32(testa, 6)
    if 10 + dim > len(testa):
        return testa
    d, p, tenuti, tolti = testa[10:10 + dim], 0, [], 0
    while p + 10 <= len(d):
        fid = bytes(d[p:p + 4])
        sz = _ss32(d, p + 4) if v == 4 else int.from_bytes(d[p + 4:p + 8], "big")
        if not re.fullmatch(rb"[A-Z0-9]{4}", fid) or sz <= 0 or p + 10 + sz > len(d):
            break
        fl = d[p + 9]
        speciale = fl & (0x0F if v == 4 else 0xC0)   # compresso, cifrato, unsync, con lunghezza: non si guarda dentro
        if not speciale and _frame_di_volume(fid, d[p + 10:p + 10 + sz]):
            tolti += 10 + sz
        else:
            tenuti.append(bytes(d[p:p + 10 + sz]))
        p += 10 + sz
    if not tolti:
        return testa
    nuovo = b"".join(tenuti) + bytes(d[p:]) + bytes(tolti)
    return bytes(testa[:10]) + nuovo + bytes(testa[10 + dim:])


def _ape_solo_volume(b, ape):
    """Il tag APE (inizio, fine) contiene solo voci di volume (lo scrive mp3gain)? Allora si toglie."""
    inizio, fine = ape
    corpo = b[inizio:fine - 32]
    if corpo[:8] == b"APETAGEX":
        corpo = corpo[32:]
    p, chiavi = 0, []
    while p + 8 < len(corpo):
        dim = int.from_bytes(corpo[p:p + 4], "little")
        k = corpo.find(b"\x00", p + 8)
        if k < 0:
            return False
        chiavi.append(bytes(corpo[p + 8:k]).decode("latin-1").upper())
        p = k + 1 + dim
    return bool(chiavi) and all(c.startswith(("REPLAYGAIN_", "MP3GAIN_")) for c in chiavi)


def tag_mp3(b):
    """(testa, coda) dell'MP3 b da rimettere attorno all'audio nuovo: i tag in testa senza i frame del volume e quello
    che c'è in coda (APE, Lyrics3, ID3v1), meno un APE di solo volume."""
    i, (e, ape) = inizio_audio(b), code_mp3(b)
    testa = id3_senza_volume(bytes(b[:i]))
    if ape and _ape_solo_volume(b, ape):
        coda = bytes(b[ape[1]:])
    else:
        coda = bytes(b[e:])
    return testa, coda


# ---------- ffmpeg: sonda, ascolto, decisione, codifica ----------

def esegui_semplice(args, tempo, fermo=None):
    """Lancia un programma e aspetta: (codice, uscita, errori). Quello del server (musica_libreria._esegui_piano) in più
    va a bassa priorità, si ferma con la pausa e quando il server esce."""
    try:
        p = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as e:
        raise ErroreMacchina("%s non parte: %s" % (os.path.basename(args[0]), e))
    try:
        out, err = p.communicate(timeout=tempo)
    except subprocess.TimeoutExpired:
        p.kill()
        p.communicate()
        raise ErroreBrano("%s: oltre %d minuti, fermato" % (os.path.basename(args[0]), tempo // 60))
    return p.returncode, out, err


class Strumenti:
    """ffmpeg, ffprobe e il modo di lanciarli (con la pausa), più il lettore dei tag della libreria per i controlli."""

    def __init__(self, ffmpeg, ffprobe, esegui=None, leggi_tag=None, fermo=None):
        self.ffmpeg, self.ffprobe, self.leggi_tag, self.fermo = ffmpeg, ffprobe, leggi_tag, fermo
        self._esegui = esegui or esegui_semplice
        self._codificatori = None

    def esegui(self, args, tempo):
        return self._esegui(args, tempo, self.fermo)

    def ha_codificatore(self, nome):
        if self._codificatori is None:
            rc, out, _ = self.esegui([self.ffmpeg, "-hide_banner", "-encoders"], 60)
            self._codificatori = set(re.findall(r"^\s*[AVS][\w.]{5}\s+(\S+)", out.decode("utf-8", "replace"), re.M)) \
                if rc == 0 else set()
        return nome in self._codificatori


def sonda(s, p):
    """Cosa c'è nel file secondo ffprobe: codec, frequenza, canali, bit, kbps, durata, flussi video e copertine."""
    rc, out, err = s.esegui([s.ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", p], 120)
    if rc != 0:
        raise ErroreBrano("ffprobe non legge il file: %s" % _coda(err.decode("utf-8", "replace")))
    try:
        j = json.loads(out.decode("utf-8", "replace"))
    except ValueError:
        raise ErroreBrano("ffprobe ha risposto in un modo che non si legge")
    flussi, fmt = j.get("streams") or [], j.get("format") or {}
    audio = [x for x in flussi if x.get("codec_type") == "audio"]
    if not audio:
        raise ErroreBrano("nel file non c'è audio")
    a = audio[0]
    video = [x for x in flussi if x.get("codec_type") == "video"]
    copertine = [x for x in video if (x.get("disposition") or {}).get("attached_pic")]
    return {"codec": a.get("codec_name") or "", "hz": _intero(a.get("sample_rate")), "canali": _intero(a.get("channels")),
            "bit": _intero(a.get("bits_per_raw_sample")) or _intero(a.get("bits_per_sample")),
            "campioni": a.get("sample_fmt") or "", "kbps": round(_intero(a.get("bit_rate") or fmt.get("bit_rate")) / 1000),
            "durata": _numero(fmt.get("duration") or a.get("duration")) or 0.0,
            "video": len(video) - len(copertine), "copertine": len(copertine)}


_PREFISSO = re.compile(r"^\[([A-Za-z0-9_]+) @ [^\]]*\]\s?")
_ERRORE = re.compile(r"error|missing|invalid|corrupt|overread|too big|incomplete|exceed", re.I)


def leggi_misure(testo):
    """Le misure dal registro di ffmpeg con FILTRO_ANALISI: {i, lra, tp (LUFS, LU, dBTP: ebur128), picco (dBFS), dc
    (il più grande dei canali), piatto (flat factor, dB), volte (peak count), campioni (per canale), errori (righe di
    errore del decodificatore)}."""
    canali, tutto, sez, righe, errori = [], {}, None, [], 0
    for r in testo.splitlines():
        m = _PREFISSO.match(r)
        nome, corpo = (m.group(1), r[m.end():]) if m else ("", r)
        if nome.startswith("Parsed_astats"):
            corpo = corpo.strip()
            if corpo.startswith("Channel:"):
                sez = {}
                canali.append(sez)
            elif corpo == "Overall":
                sez = tutto
            elif sez is not None and ":" in corpo:
                k, v = corpo.split(":", 1)
                sez[k.strip()] = _numero(v)
            continue
        if (nome and not nome.startswith("Parsed_") and _ERRORE.search(corpo)) or corpo.startswith("Error while decoding"):
            errori += 1
        righe.append(corpo)
    pulito = "\n".join(righe)
    k = pulito.rfind("Summary:")
    riepilogo = pulito[k:] if k >= 0 else ""

    def cerca(rx):
        x = re.search(rx, riepilogo)
        return _numero(x.group(1)) if x else None
    dc = [abs(c["DC offset"]) for c in canali if c.get("DC offset") is not None]
    if not dc and tutto.get("DC offset") is not None:
        dc = [abs(tutto["DC offset"])]
    n = tutto.get("Number of samples")
    if n is None and canali:
        n = canali[0].get("Number of samples")
    return {"i": cerca(r"Integrated loudness:\s*I:\s*(\S+)\s*LUFS"), "lra": cerca(r"LRA:\s*(\S+)\s*LU\b"),
            "tp": cerca(r"True peak:\s*Peak:\s*(\S+)\s*dBFS"), "picco": tutto.get("Peak level dB"),
            "dc": max(dc) if dc else 0.0, "piatto": tutto.get("Flat factor"), "volte": tutto.get("Peak count"),
            "campioni": int(n) if n is not None and math.isfinite(n) else 0, "errori": errori}


def analizza(s, p):
    rc, _, err = s.esegui([s.ffmpeg, "-hide_banner", "-nostats", "-nostdin", "-i", p, "-map", "0:a:0",
                           "-af", FILTRO_ANALISI, "-f", "null", "-"], TEMPO_ANALISI)
    testo = err.decode("utf-8", "replace")
    if rc != 0:
        raise ErroreBrano("ffmpeg non riesce ad ascoltare il file: %s" % _coda(testo))
    m = leggi_misure(testo)
    if m["i"] is None:
        raise ErroreBrano("ffmpeg non ha dato il volume del brano")
    return m


def riassunto(m):
    return {"i": _finito(m.get("i")), "tp": _finito(m.get("tp")), "lra": _finito(m.get("lra")),
            "dc": _finito(m.get("dc"), 4)}


def formato_di(est, info):
    c = info.get("codec")
    if est == ".mp3" and c == "mp3":
        return "mp3"
    if est == ".flac" and c == "flac":
        return "flac"
    if est in (".m4a", ".mp4", ".alac") and c in ("aac", "alac"):
        return c
    return ""


def _testo_volume(i):
    return ("volume basso (%.1f LUFS)" if i < OBIETTIVO else "volume alto (%.1f LUFS)") % i


def piano(m, info, est, ricodifica=False):
    """Cosa fare di un brano: {"azione": "salta" | "nulla" | "guadagno" | "ricodifica", "motivo" o "motivi", …}.
    guadagno: MP3 senza ricodifica, `passi` × 1,5 dB. ricodifica: `guadagno` in dB (anche 0), `declip`, `picco_max`.
    Con ricodifica=True un MP3 si tratta come un file con perdita da ricodificare (il guadagno senza perdita non va)."""
    fmt = formato_di(est, info)
    if not fmt:
        return {"azione": "salta", "motivo": "formato non trattato (%s, %s)" % (est or "senza estensione",
                                                                            info.get("codec") or "?")}
    if info.get("video"):
        return {"azione": "salta", "motivo": "è un video"}
    dur = info.get("durata") or 0
    if 0 < dur < DURATA_MIN:
        return {"azione": "salta", "motivo": "troppo corto"}
    if dur > DURATA_MAX:
        return {"azione": "salta", "motivo": "troppo lungo"}
    i, tp = m.get("i"), m.get("tp")
    if tp is None or not math.isfinite(tp):
        tp = m.get("picco")
    if i is None or i <= -69.9 or tp is None or not math.isfinite(tp):
        return {"azione": "salta", "motivo": "silenzio, o volume che non si misura"}
    scarto, dc = OBIETTIVO - i, m.get("dc") or 0.0
    if fmt == "mp3" and not ricodifica and dc < DC_CON_PERDITA:
        if abs(scarto) <= TOLLERANZA and tp <= PICCO_ROTTO:
            return {"azione": "nulla", "motivo": "già al volume giusto"}
        passi = int(round(min(scarto, ALZA_MAX, PICCO_SENZA_PERDITA - tp) / PASSO_MP3))
        while passi > 0 and (passi * PASSO_MP3 > ALZA_MAX + 1e-9 or tp + passi * PASSO_MP3 > PICCO_SENZA_PERDITA):
            passi -= 1
        if tp > PICCO_ROTTO:
            while tp + passi * PASSO_MP3 > PICCO_SENZA_PERDITA:
                passi -= 1
        if not passi:
            return {"azione": "nulla", "motivo": "già al volume giusto" if abs(scarto) <= TOLLERANZA else
                    "più forte distorcerebbe: lasciato com'è"}
        motivi = [_testo_volume(i)] if abs(scarto) > TOLLERANZA else []
        if tp > PICCO_ROTTO:
            motivi.append("picchi oltre lo zero (%+.1f dBTP): distorcono" % tp)
        return {"azione": "guadagno", "formato": fmt, "passi": passi, "guadagno": passi * PASSO_MP3, "motivi": motivi}
    senza_perdita = fmt in ("flac", "alac")
    tolleranza = TOLLERANZA if senza_perdita else TOLLERANZA_RICODIFICA
    picco_max = PICCO_SENZA_PERDITA if senza_perdita else PICCO_CON_PERDITA
    clip = senza_perdita and (m.get("picco") if m.get("picco") is not None else -99) >= CLIP_PICCO and \
        (m.get("volte") or 0) >= CLIP_VOLTE and (m.get("piatto") if m.get("piatto") is not None else -99) >= CLIP_PIATTO
    g = min(scarto, ALZA_MAX, picco_max - tp - (DECLIP_MARGINE if clip else 0.0))
    motivi = []
    if abs(scarto) > tolleranza and abs(g) >= 0.5:
        motivi.append(_testo_volume(i))
    if tp > PICCO_ROTTO:
        motivi.append("picchi oltre lo zero (%+.1f dBTP): distorcono" % tp)
    if dc >= (DC_SENZA_PERDITA if senza_perdita else DC_CON_PERDITA):
        motivi.append("scostamento DC (%.3f)" % dc)
    if clip:
        motivi.append("picchi tagliati (clipping): li ricostruisco")
    if not motivi:
        return {"azione": "nulla", "motivo": "già al volume giusto" if abs(scarto) <= tolleranza else
                "più forte distorcerebbe: lasciato com'è"}
    if not (abs(scarto) > tolleranza or tp > PICCO_ROTTO or clip):
        g = 0.0   # solo il DC: il volume resta quello
    return {"azione": "ricodifica", "formato": fmt, "guadagno": round(g, 2), "declip": clip, "picco_max": picco_max,
            "motivi": motivi}


def _sedici_bit(info):
    if info.get("bit"):
        return info["bit"] <= 16
    return info.get("campioni") in ("s16", "s16p", "u8", "u8p")


def contenitore(fmt, est):
    return {"mp3": "mp3", "flac": "flac"}.get(fmt) or ("ipod" if est == ".m4a" else "mp4")


def codifica(s, p, pl, info, est, uscita):
    """Ricodifica il brano nello stesso formato con i filtri del piano. MP3: solo l'audio (i tag li rimette tag_mp3);
    gli altri con tag e copertina copiati, meno il volume scritto da altri programmi."""
    fmt, g = pl["formato"], pl.get("guadagno") or 0.0
    filtri = ["aformat=sample_fmts=fltp"]
    if pl.get("declip"):
        filtri.append("adeclip")
    filtri.append("highpass=f=%d:poles=2" % PASSA_ALTO)
    if abs(g) >= 0.01:
        filtri.append("volume=%.2fdB" % g)
    if fmt == "mp3":
        cod = ["-c:a", "libmp3lame", "-q:a", "0", "-compression_level", "0"]
    elif fmt == "aac":
        kbps = min(320, max(256, info.get("kbps") or 0))
        cod = ["-c:a", "aac_at" if s.ha_codificatore("aac_at") else "aac", "-b:a", "%dk" % kbps]
    else:
        sedici = _sedici_bit(info)
        osf = ("s16" if sedici else "s32") + ("p" if fmt == "alac" else "")
        filtri.append("aresample=osf=%s%s" % (osf, ":dither_method=triangular_hp" if sedici else ""))
        cod = ["-c:a", "flac", "-compression_level", "8"] if fmt == "flac" else ["-c:a", "alac"]
    args = [s.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", p, "-map", "0:a:0"]
    if fmt != "mp3" and info.get("copertine"):
        args += ["-map", "0:v", "-c:v", "copy", "-disposition:v", "attached_pic"]
    args += ["-af", ",".join(filtri)] + cod
    if fmt == "mp3":
        args += ["-map_metadata", "-1", "-id3v2_version", "0", "-write_xing", "1"]
    else:
        args += ["-map_metadata", "0"]
        for k in CHIAVI_VOLUME:
            args += ["-metadata", k + "="]
        if fmt in ("aac", "alac"):
            args += ["-movflags", "+faststart"]
    rc, _, err = s.esegui(args + ["-f", contenitore(fmt, est), uscita], TEMPO_CODIFICA)
    if rc != 0:
        _cancella(uscita)
        raise ErroreBrano("ffmpeg non è riuscito a codificare: %s" % _coda(err.decode("utf-8", "replace")))


def tag_diversi(a, b):
    """Il primo tag che non è uguale tra a e b (letti con leggi_tag della libreria), o None."""
    for k in CHIAVI_TAG:
        if str(a.get(k) or "").strip() != str(b.get(k) or "").strip():
            return k
    pa, pb = a.get("pic"), b.get("pic")
    if bool(pa and pa[0]) != bool(pb and pb[0]) or (pa and pa[0] and bytes(pa[0]) != bytes(pb[0])):
        return "copertina"
    return None


def verifica(s, nuovo, info, m, pl, tag_prima):
    """Il file nuovo si decodifica senza errori in più, dura uguale (al campione), ha il volume e i picchi previsti e gli
    stessi tag. ErroreBrano se no. Restituisce le sue misure."""
    info2, m2 = sonda(s, nuovo), analizza(s, nuovo)
    hz, hz2 = info.get("hz") or 0, info2.get("hz") or 0
    if hz and hz2 and m.get("campioni") and m2.get("campioni"):
        d1, d2 = m["campioni"] / hz, m2["campioni"] / hz2
        if abs(d1 - d2) > (0.0005 if pl["azione"] == "guadagno" else 0.1):
            raise ErroreBrano("dopo durerebbe %.3f s invece di %.3f s" % (d2, d1))
    g = pl.get("guadagno") or 0.0
    atteso = m["i"] + g
    if pl["azione"] == "guadagno":
        if abs(m2["i"] - atteso) > 0.3 or abs((m2["tp"] or 0) - ((m["tp"] or 0) + g)) > 0.3:
            raise ErroreBrano("il volume dopo non è quello previsto (%.1f LUFS invece di %.1f)" % (m2["i"], atteso))
        if m2["errori"] > m["errori"]:
            raise ErroreBrano("dopo ci sarebbero errori di decodifica che prima non c'erano")
    else:
        if abs(m2["i"] - atteso) > (1.5 if pl.get("declip") else 1.0):
            raise ErroreBrano("il volume dopo non è quello previsto (%.1f LUFS invece di %.1f)" % (m2["i"], atteso))
        if m2["tp"] is not None and m2["tp"] > pl["picco_max"] + 0.6:
            raise ErroreBrano("dopo i picchi resterebbero troppo alti (%+.1f dBTP)" % m2["tp"])
        if m2["errori"]:
            raise ErroreBrano("il file nuovo ha errori di decodifica")
    if s.leggi_tag and tag_prima is not None:
        diverso = tag_diversi(tag_prima, s.leggi_tag(nuovo))
        if diverso:
            raise ErroreBrano("il tag «%s» non resterebbe uguale: lascio l'originale" % diverso)
    return info2, m2


# ---------- dischi: archivio degli originali, giornale, sostituzione ----------

def percorso_archivio(radice, p):
    rel = os.path.relpath(p, radice)
    if rel.startswith(os.pardir + os.sep) or rel == os.pardir or os.path.isabs(rel):
        raise ValueError("%s non è dentro %s" % (p, radice))
    return os.path.join(radice, ARCHIVIO, rel)


def _nascosto(p):
    return os.path.join(os.path.dirname(p), "." + os.path.basename(p) + ".pulizia-tmp")


def _uguale_a_prima(p, st0):
    try:
        st = os.stat(p)
    except FileNotFoundError:
        raise Cambiato("il file non c'è più")
    if (st.st_size, st.st_mtime_ns) != (st0.st_size, st0.st_mtime_ns):
        raise Cambiato("il file è cambiato mentre lo pulivo")


def _copia(sorgente, dest):
    """Copia byte per byte, su disco davvero (fsync). Restituisce lo sha1."""
    h = hashlib.sha1()
    with open(sorgente, "rb") as a, open(dest, "wb") as b:
        for blocco in iter(lambda: a.read(1 << 20), b""):
            b.write(blocco)
            h.update(blocco)
        b.flush()
        os.fsync(b.fileno())
    return h.hexdigest()


def archivia(p, radice):
    """L'originale va in .atlante-originali con lo stesso percorso: un collegamento (niente copia: quando il file nuovo
    prende il suo posto l'originale resta qui) o, se il disco non li permette, una copia controllata. Mai sopra un
    originale che c'è già."""
    a = percorso_archivio(radice, p)
    if os.path.lexists(a):
        raise ErroreBrano("c'è già un originale in archivio: non lo sovrascrivo")
    os.makedirs(os.path.dirname(a), exist_ok=True)
    try:
        os.link(p, a)
        return a
    except OSError:
        pass
    tmp = a + ".tmp"
    try:
        sha = _copia(p, tmp)
        shutil.copystat(p, tmp)
        if sha != _sha1_file(p):
            raise ErroreMacchina("la copia dell'originale non è uguale")
        os.replace(tmp, a)
    finally:
        _cancella(tmp)
    return a


def metti_al_posto(p, st0, nuovo, sha, radice=None, salva_originale=False, sposta_ns=1000000000):
    """Il file `nuovo` prende il posto di p in un colpo: passa da un file nascosto accanto a p (stesso disco), si
    controlla che sia uguale (sha), prende i permessi di p e la sua data spostata di `sposta_ns` (un secondo: la
    libreria e l'iPhone vedono che è cambiato, «Aggiunti di recente» no). Con salva_originale l'originale va prima
    in archivio. Se p è cambiato nel frattempo, Cambiato e niente si tocca. Restituisce la firma nuova."""
    tmp, a = _nascosto(p), None
    try:
        if _copia(nuovo, tmp) != sha:
            raise ErroreMacchina("la copia sul disco della musica non è uguale al file pulito")
        os.chmod(tmp, stat.S_IMODE(st0.st_mode))
        os.utime(tmp, ns=(st0.st_atime_ns, st0.st_mtime_ns + sposta_ns))
        _uguale_a_prima(p, st0)
        if salva_originale:
            a = archivia(p, radice)
        _uguale_a_prima(p, st0)
        os.replace(tmp, p)
        a = None
    finally:
        _cancella(tmp)
        if a:   # non è andato al suo posto: l'archivio (uguale al file che resta) si toglie
            _cancella(a)
    return firma(os.stat(p))


def _giornale(radice):
    return os.path.join(radice, ARCHIVIO, GIORNALE)


def scrivi_giornale(radice, voce):
    """Una riga nel giornale dei guadagni sul disco della musica, prima di cambiare il file: l'undo resta anche se si
    perde la cache del server."""
    f = _giornale(radice)
    os.makedirs(os.path.dirname(f), exist_ok=True)
    with open(f, "a", encoding="utf-8") as g:
        g.write(json.dumps(voce, ensure_ascii=False) + "\n")
        g.flush()
        os.fsync(g.fileno())


def dal_giornale(radice, rel):
    """L'ultima riga del giornale per il file rel, o None."""
    trovata = None
    try:
        with open(_giornale(radice), encoding="utf-8") as g:
            for riga in g:
                try:
                    v = json.loads(riga)
                except ValueError:
                    continue
                if isinstance(v, dict) and v.get("p") == rel:
                    trovata = v
    except OSError:
        return None
    return trovata


def _sposta(a, p):
    """L'originale torna al suo posto (stesso disco: in un colpo; se no copia e poi via)."""
    try:
        os.replace(a, p)
        return
    except OSError as e:
        if e.errno != errno.EXDEV:
            raise
    tmp = _nascosto(p)
    try:
        _copia(a, tmp)
        shutil.copystat(a, tmp)
        os.replace(tmp, p)
    finally:
        _cancella(tmp)
    _cancella(a)


# ---------- il lavoro su tutta la libreria ----------

class Pulizia:
    """Il lavoro in sottofondo, il registro dei brani e il ripristino.

    brani(): [(id, voce del catalogo, percorso, cartella della libreria)] dei brani da pulire (disco principale).
    sostituito(id, percorso, firma vecchia, firma nuova): chiamata dopo ogni file cambiato (pulito o ripristinato).
    esegui(args, tempo, fermo): lancia ffmpeg/ffprobe; programma(nome): il percorso di un programma.
    pronta(): la libreria ha finito la prima lettura; occupato(id): un altro lavoro sta usando quel brano adesso."""

    def __init__(self, cartella, brani, sostituito=None, leggi_tag=None, esegui=None, programma=None,
                 acceso_di_serie=True, spazio_min=None, pronta=None, occupato=None, log=None, spazio_cache=None):
        self.dir, self.tmp = cartella, os.path.join(cartella, "tmp")
        os.makedirs(self.tmp, exist_ok=True)
        self.brani, self.sostituito, self.leggi_tag = brani, sostituito, leggi_tag
        self._esegui, self.programma = esegui, programma or shutil.which
        self.pronta, self.occupato = pronta, occupato
        self.log = log or (lambda x: print("Pulizia audio: " + x, flush=True))
        self.spazio_min = SPAZIO_MIN if spazio_min is None else int(spazio_min)
        self.spazio_cache = SPAZIO_CACHE if spazio_cache is None else int(spazio_cache)
        self.blocco, self.uno = threading.RLock(), threading.Lock()
        self.sveglia = threading.Event()
        reg = self._leggi("registro.json")
        self.registro = reg.get("brani") if isinstance(reg.get("brani"), dict) else {}
        lav = self._leggi("lavoro.json")
        self.acceso = lav["acceso"] if isinstance(lav.get("acceso"), bool) else bool(acceso_di_serie)
        if not isinstance(lav.get("acceso"), bool):
            self._scrivi("lavoro.json", {"acceso": self.acceso})
        self.ora, self.id_ora, self.pausa, self.pausa_fino = "", None, "", 0.0
        self.fine, self.filo, self.rip = False, None, None
        self._sporco, self._salvato = False, time.time()
        self._svuota_tmp()

    # --- file ---

    def _leggi(self, nome):
        try:
            with open(os.path.join(self.dir, nome), encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _scrivi(self, nome, dati):
        f = os.path.join(self.dir, nome)
        tmp = "%s.%d.tmp" % (f, threading.get_ident())
        try:
            with open(tmp, "w", encoding="utf-8") as g:
                json.dump(dati, g, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, f)
        finally:
            _cancella(tmp)

    def salva(self, subito=False):
        with self.blocco:
            if not self._sporco or (not subito and time.time() - self._salvato < SALVA_OGNI):
                return
            dati, self._sporco, self._salvato = dict(self.registro), False, time.time()
        try:
            self._scrivi("registro.json", {"v": VERSIONE, "brani": dati})
        except OSError as e:
            self._sporco = True
            self.log("registro non salvato: %s" % e)

    def _svuota_tmp(self, prefisso=""):
        """Via i file di lavoro: tutti all'avvio, poi solo quelli di un filo (il ripristino può lavorare intanto)."""
        try:
            for n in os.listdir(self.tmp):
                if n.startswith(prefisso):
                    _cancella(os.path.join(self.tmp, n))
        except OSError:
            pass

    def _segna(self, i, voce):
        with self.blocco:
            self.registro[i] = voce
            self._sporco = True

    # --- acceso, pausa, stato ---

    def avvia(self):
        with self.blocco:
            if self.filo and self.filo.is_alive():
                return
            self.filo = threading.Thread(target=self._lavora, daemon=True, name="pulizia-audio")
            self.filo.start()

    def imposta(self, acceso):
        with self.blocco:
            self.acceso = bool(acceso)
            if self.acceso:
                self.pausa, self.pausa_fino = "", 0.0
        self._scrivi("lavoro.json", {"acceso": self.acceso})
        self.sveglia.set()
        if self.acceso:
            self.avvia()

    def riprova(self):
        """I brani non riusciti si riprovano subito e una pausa per un errore del Mac finisce."""
        with self.blocco:
            for v in self.registro.values():
                if v.get("e") == "errore":
                    v["n"], v["q"] = 0, 0
            self._sporco = True
            self.pausa, self.pausa_fino = "", 0.0
        self.sveglia.set()

    def chiudi(self):
        """Il server esce: il registro sul disco (i programmi li ferma il server)."""
        self.fine = True
        self.sveglia.set()
        self.salva(True)

    def _fermo(self):
        return self.fine or not self.acceso

    def _da_fare(self, v, r, ora):
        if not r:
            return True
        e = r.get("e")
        if e in ("migliorato", "ripristinato", "gia_pulito"):
            return False   # mai due volte lo stesso brano; quelli ripristinati li ha voluti così Samuele
        if r.get("f") != v.get("_f") or r.get("v") != VERSIONE:
            return True    # file cambiato (un brano nuovo con lo stesso nome) o modo di decidere nuovo
        if e == "errore":
            return r.get("n", 1) < ERRORI_MAX and ora - r.get("q", 0) >= RIPROVA_ERRORE
        return False

    def _voci(self):
        try:
            return list(self.brani() or [])
        except Exception as e:   # noqa: BLE001 — libreria non pronta
            self.log("elenco dei brani non letto: %s" % e)
            return []

    def stato(self):
        voci, ora = self._voci(), time.time()
        with self.blocco:
            reg = dict(self.registro)
        conti, da_fare, peso = collections.Counter(), 0, 0
        radici = set()
        for i, v, p, radice in voci:
            radici.add(radice)
            r = reg.get(i)
            if self._da_fare(v, r, ora):
                da_fare += 1
                try:
                    peso += int(str(v.get("_f") or "0").split(":")[0])
                except ValueError:
                    pass
            elif r:
                conti[r.get("e")] += 1
        liberi = []
        for r in radici:
            try:
                liberi.append(shutil.disk_usage(r).free)
            except OSError:
                pass
        ultimi = sorted(({"id": i, "t": x.get("t") or "", "a": x.get("a") or "", "e": x.get("e"),
                          "modo": x.get("modo") or "", "g": x.get("g"), "m": x.get("m") or [],
                          "motivo": x.get("motivo") or x.get("err") or "", "prima": x.get("prima"),
                          "dopo": x.get("dopo"), "q": x.get("q", 0)}
                         for i, x in reg.items() if x.get("e") in ("migliorato", "errore", "ripristinato")),
                        key=lambda x: -x["q"])[:ULTIMI]
        return {"acceso": self.acceso, "lavora": bool(self.ora), "ora": self.ora,
                "pausa": self.pausa if self.pausa_fino > ora else "",
                "conti": {"migliorati": conti["migliorato"], "buoni": conti["buono"], "saltati": conti["saltato"],
                          "errori": conti["errore"], "gia_puliti": conti["gia_pulito"],
                          "ripristinati": conti["ripristinato"]},
                "da_fare": da_fare, "totale": len(voci), "da_fare_gb": round(peso / 1024 ** 3, 1),
                "spazio_libero_gb": round(min(liberi) / 1024 ** 3, 1) if liberi else None,
                "spazio_min_gb": round(self.spazio_min / 1024 ** 3), "obiettivo": OBIETTIVO,
                "ffmpeg": bool(self.programma("ffmpeg") and self.programma("ffprobe")),
                "ultimi": ultimi, "ripristino": dict(self.rip) if self.rip else None}

    def _strumenti(self):
        ff, fp = self.programma("ffmpeg"), self.programma("ffprobe")
        if not ff or not fp:
            raise ErroreMacchina("ffmpeg non è installato sul Mac: brew install ffmpeg")
        return Strumenti(ff, fp, self._esegui, self.leggi_tag, self._fermo)

    def _pausa_per(self, motivo, secondi):
        with self.blocco:
            self.pausa, self.pausa_fino = motivo, time.time() + secondi
        self.log("in pausa per %d minuti: %s" % (secondi // 60, motivo))

    def _dormi(self, secondi):
        self.sveglia.wait(secondi)
        self.sveglia.clear()

    # --- il lavoro ---

    def _prossimi(self, n):
        voci, ora = self._voci(), time.time()
        voci.sort(key=lambda x: -(x[1].get("added") or 0))   # i brani nuovi per primi
        with self.blocco:
            return [x for x in voci if self._da_fare(x[1], self.registro.get(x[0]), ora)][:n]

    def _lavora(self):
        self.log("lavoro avviato")
        while not self.fine:
            try:
                if not self.acceso:
                    self.salva(True)
                    self._dormi(3600)
                    continue
                if self.pausa_fino > time.time():
                    self._dormi(min(60.0, self.pausa_fino - time.time()))
                    continue
                self.pausa = ""
                if self.pronta and not self.pronta():
                    self._dormi(5)
                    continue
                lotto = self._prossimi(LOTTO)
                if not lotto:
                    self.salva(True)
                    self._dormi(RIPOSO)
                    continue
                for x in lotto:
                    if self._fermo() or self.pausa_fino > time.time():
                        break
                    self._uno(*x)
                    self.salva()
                    time.sleep(TRA_BRANI)
            except Exception:   # noqa: BLE001 — un baco non deve fermare il lavoro per sempre
                self.log("errore inatteso:\n%s" % traceback.format_exc())
                self._dormi(PAUSA_MAC)
        self.salva(True)

    def _spazio_ok(self, radice, dim):
        try:
            libero = shutil.disk_usage(radice).free
        except OSError:
            raise ErroreMacchina("la cartella della musica non si raggiunge (il disco è collegato?)")
        if libero < self.spazio_min + 2 * dim:
            self._pausa_per("sul disco della musica restano %.0f GB liberi: mi fermo sotto i %.0f GB" % (
                libero / 1024 ** 3, self.spazio_min / 1024 ** 3), PAUSA_SPAZIO)
            return False
        try:
            if shutil.disk_usage(self.tmp).free < self.spazio_cache + 2 * dim:
                self._pausa_per("il disco del Mac (cache) è quasi pieno", PAUSA_SPAZIO)
                return False
        except OSError:
            pass
        return True

    def _uno(self, i, v, p, radice):
        if self.occupato and self.occupato(i):
            return   # un altro lavoro (Whisper) lo sta ascoltando: al prossimo giro
        try:
            dim = os.stat(p).st_size
        except OSError:
            if not os.path.isdir(radice):
                self._pausa_per("la cartella della musica non si raggiunge (il disco è collegato?)", PAUSA_MAC)
            return   # file sparito: la libreria se ne accorgerà
        if not self._spazio_ok(radice, dim):
            return
        with self.blocco:   # nel frattempo Samuele può averlo ripristinato
            prima = dict(self.registro.get(i) or {})
            if not self._da_fare(v, prima, time.time()):
                return
            self.ora, self.id_ora = " — ".join(x for x in (v.get("title"), v.get("artist")) if x), i
        try:
            r = self.tratta(p, radice, v)
        except (Fermato, Cambiato):
            return
        except ErroreMacchina as e:
            self._pausa_per(str(e), PAUSA_MAC)
            return
        except ErroreBrano as e:
            r = {"e": "errore", "err": str(e)[:300]}
        except Exception as e:   # noqa: BLE001
            self.log("%s: errore inatteso:\n%s" % (p, traceback.format_exc()))
            r = {"e": "errore", "err": ("errore inatteso: %s" % e)[:300]}
        finally:
            self.ora, self.id_ora = "", None
            self._svuota_tmp(self._prefisso())
        f0, f1 = r.pop("_f0", None), r.pop("_f1", None)
        r.update(v=VERSIONE, q=int(time.time()), p=p, t=(v.get("title") or "")[:200], a=(v.get("artist") or "")[:200])
        r.setdefault("f", f1 or v.get("_f"))
        if r["e"] == "errore":
            r["n"] = (prima.get("n", 0) if prima.get("e") == "errore" and prima.get("f") == r["f"] else 0) + 1
        self._segna(i, r)
        if r["e"] == "migliorato":
            self.log("%s: %s" % (os.path.basename(p), ", ".join(r.get("m") or [])))
            if self.sostituito and f0 and f1:
                try:
                    self.sostituito(i, p, f0, f1)
                except Exception:   # noqa: BLE001 — il file è già pulito: il resto si sistema alla prossima lettura
                    self.log("dopo %s: %s" % (p, traceback.format_exc()))

    @staticmethod
    def _prefisso():
        return "%d-%d-" % (os.getpid(), threading.get_ident())

    def _file_tmp(self, est):
        return os.path.join(self.tmp, self._prefisso() + "lavoro" + est)

    def tratta(self, p, radice, v=None):
        """Un brano: ascolto, decisione, pulizia, controllo e sostituzione. La voce per il registro: e = migliorato |
        buono | saltato | gia_pulito, con le misure (le eccezioni dicono il resto)."""
        s = self._strumenti()
        try:
            st0 = os.lstat(p)
        except FileNotFoundError:
            raise Cambiato("il file non c'è più")
        if stat.S_ISLNK(st0.st_mode) or not stat.S_ISREG(st0.st_mode):
            return {"e": "saltato", "motivo": "collegamento a un altro file (si pulisce l'originale)"}
        if st0.st_size > FILE_MAX:
            return {"e": "saltato", "motivo": "file troppo grande per un brano"}
        if os.path.lexists(percorso_archivio(radice, p)):
            return {"e": "gia_pulito", "motivo": "pulito in passato: l'originale è in archivio"}
        est = os.path.splitext(p)[1].lower()
        info = sonda(s, p)
        m = analizza(s, p)
        _uguale_a_prima(p, st0)
        pl = piano(m, info, est)
        base = {"prima": riassunto(m), "f": firma(st0)}
        if pl["azione"] == "salta":
            return dict(base, e="saltato", motivo=pl["motivo"])
        if pl["azione"] == "nulla":
            return dict(base, e="buono", motivo=pl["motivo"])
        tag0 = self.leggi_tag(p) if self.leggi_tag else None
        if pl["azione"] == "guadagno":
            try:
                return self._con_guadagno(s, p, radice, st0, info, m, pl, tag0, base)
            except NonSenzaPerdita as e:
                self.log("%s: guadagno senza ricodifica non possibile (%s): ricodifico" % (os.path.basename(p), e))
                pl = piano(m, info, est, ricodifica=True)
                if pl["azione"] == "nulla":
                    return dict(base, e="buono", motivo=pl["motivo"])
        return self._con_ricodifica(s, p, radice, st0, info, m, pl, tag0, base, est)

    def _con_guadagno(self, s, p, radice, st0, info, m, pl, tag0, base):
        if st0.st_size > MEMORIA_MAX:
            raise NonSenzaPerdita("file grande")
        with open(p, "rb") as f:
            dati = f.read()
        b = bytearray(dati)
        try:
            fr = frame_mp3(b)
            guadagno_mp3(b, pl["passi"], fr)
            indietro = bytearray(b)
            guadagno_mp3(indietro, -pl["passi"], fr)
        except (ValueError, IndexError) as e:
            raise NonSenzaPerdita(str(e))
        if indietro != dati:
            raise NonSenzaPerdita("il cambio non si disfa uguale")
        del indietro
        tmp = self._file_tmp(".mp3")
        with open(tmp, "wb") as f:
            f.write(b)
        _, m2 = verifica(s, tmp, info, m, pl, tag0)
        sa = [sha_audio(dati), sha_audio(b)]
        sha = hashlib.sha1(b).hexdigest()
        del dati, b
        rel = os.path.relpath(p, radice)
        with self.uno:
            _uguale_a_prima(p, st0)
            scrivi_giornale(radice, {"p": rel, "passi": pl["passi"], "prima": sa[0], "dopo": sa[1],
                                     "q": int(time.time()), "v": VERSIONE})
            f1 = metti_al_posto(p, st0, tmp, sha)
        return dict(base, e="migliorato", modo="guadagno", passi=pl["passi"], g=pl["guadagno"], m=pl["motivi"],
                    dopo=riassunto(m2), sa=sa, f=f1, _f0=firma(st0), _f1=f1)

    def _con_ricodifica(self, s, p, radice, st0, info, m, pl, tag0, base, est):
        uscita = self._file_tmp(est)
        codifica(s, p, pl, info, est, uscita)
        if pl["formato"] == "mp3":   # i tag dell'originale attorno all'audio nuovo, byte per byte
            with open(p, "rb") as f:
                orig = f.read()
            testa, coda = tag_mp3(orig)
            del orig
            with open(uscita, "rb") as f:
                nuovo = f.read()
            e, _ = code_mp3(nuovo)
            finale = self._file_tmp(".finale.mp3")
            with open(finale, "wb") as f:
                f.write(testa)
                f.write(nuovo[inizio_audio(nuovo):e])
                f.write(coda)
            del nuovo
            _cancella(uscita)
            uscita = finale
        _, m2 = verifica(s, uscita, info, m, pl, tag0)
        sha = _sha1_file(uscita)
        with self.uno:
            f1 = metti_al_posto(p, st0, uscita, sha, radice, salva_originale=True)
        return dict(base, e="migliorato", modo="ricodifica", g=pl["guadagno"], declip=bool(pl.get("declip")),
                    m=pl["motivi"], dopo=riassunto(m2), f=f1, _f0=firma(st0), _f1=f1)

    # --- ripristino ---

    def ripristina(self, i):
        """Il brano torna com'era prima della pulizia: l'originale dall'archivio, o il guadagno MP3 disfatto al bit.
        Poi non si pulisce più (a meno di riaccenderlo a mano). ErroreBrano se non si può."""
        voci = {x[0]: x for x in self._voci()}
        if i not in voci:
            raise ErroreBrano("brano non trovato nella libreria")
        if self.id_ora == i:
            raise ErroreBrano("lo sto pulendo proprio adesso: riprova fra poco")
        _, v, p, radice = voci[i]
        r = dict(self.registro.get(i) or {})
        with self.uno:
            try:
                st0 = os.stat(p)
            except OSError:
                raise ErroreBrano("il file non si raggiunge (il disco è collegato?)")
            a = percorso_archivio(radice, p)
            if os.path.isfile(a):
                _sposta(a, p)
                st1 = os.stat(p)
                if st1.st_mtime_ns == st0.st_mtime_ns:   # stessa data: la libreria non lo rileggerebbe
                    os.utime(p, ns=(st1.st_atime_ns, st1.st_mtime_ns + 1000000000))
                f1 = firma(os.stat(p))
            else:
                g = r if r.get("modo") == "guadagno" and r.get("sa") else None
                if not g:
                    x = dal_giornale(radice, os.path.relpath(p, radice))
                    g = {"passi": x["passi"], "sa": [x["prima"], x["dopo"]]} if x and x.get("passi") else None
                if not g:
                    raise ErroreBrano("non c'è niente da ripristinare: il brano non è stato cambiato")
                with open(p, "rb") as f:
                    b = bytearray(f.read())
                if sha_audio(b) != g["sa"][1]:
                    raise ErroreBrano("l'audio è cambiato dopo la pulizia: non lo tocco")
                try:
                    guadagno_mp3(b, -int(g["passi"]))
                except ValueError as e:
                    raise ErroreBrano("il guadagno non si disfa: %s" % e)
                if sha_audio(b) != g["sa"][0]:
                    raise ErroreBrano("disfatto non torna uguale all'originale: lascio com'è")
                tmp = self._file_tmp(".ripristino.mp3")
                try:
                    with open(tmp, "wb") as f:
                        f.write(b)
                    f1 = metti_al_posto(p, st0, tmp, hashlib.sha1(b).hexdigest(), sposta_ns=-1000000000)
                finally:
                    _cancella(tmp)
        self._segna(i, {"e": "ripristinato", "f": f1, "q": int(time.time()), "v": VERSIONE, "p": p,
                        "t": (v.get("title") or "")[:200], "a": (v.get("artist") or "")[:200],
                        "prima": r.get("prima"), "modo": r.get("modo") or ""})
        self.salva(True)
        if self.sostituito:
            self.sostituito(i, p, firma(st0), f1)
        return {"ok": True, "firma": f1}

    def ripristina_tutti(self):
        """Tutti i brani puliti tornano com'erano, in un filo; il lavoro intanto si spegne."""
        with self.blocco:
            if self.rip and self.rip.get("stato") == "in corso":
                raise ErroreBrano("il ripristino è già in corso")
            ids = [i for i, x in self.registro.items() if x.get("e") == "migliorato"]
            self.rip = {"stato": "in corso", "totale": len(ids), "fatti": 0, "errori": 0, "ultimo_errore": ""}
        self.imposta(False)

        def lavora():
            for i in ids:
                while self.id_ora:   # il brano che si stava pulendo finisce o si ferma
                    time.sleep(0.5)
                try:
                    self.ripristina(i)
                except Exception as e:   # noqa: BLE001
                    with self.blocco:
                        self.rip["errori"] += 1
                        self.rip["ultimo_errore"] = str(e)[:200]
                with self.blocco:
                    self.rip["fatti"] += 1
            with self.blocco:
                self.rip["stato"] = "finito"
            self.salva(True)
        threading.Thread(target=lavora, daemon=True, name="pulizia-ripristino").start()
        return dict(self.rip)
