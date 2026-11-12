"""Playlist-Verwaltung: AutoDJ, Shuffle, Crossfade, Jingles.

Das Original (pawcast 2.0.0.54) liest eine Textdatei mit einem Pfad pro
Zeile. Kommentare mit ``#`` sind erlaubt, Leerzeilen werden ignoriert.

Die Log-Ausgaben:
    [PLAYLISTMGR] Playlist load from file <pfad>
    [PLAYLISTMGR] Caching playlist <pfad>
    [PLAYLISTMGR] Load complete
    [DECODE] Next file is <pfad>
    Playlist has run dry   (wenn nichts mehr da ist)

Playlists leben in ``<playlistdir>/<name>.lst``. ``main.lst`` ist die
AutoDJ-Playlist, ``jingles.lst`` die Ansagen, ``active.lst`` die
tatsaechlich gespielte Liste (im Original-Setup der aktive Zeiger).
"""

from __future__ import annotations

import json
import logging
import os
import random
import shutil
import subprocess
import threading
from dataclasses import dataclass
from typing import Iterator, List, Optional

log = logging.getLogger("pawcast.playlist")


@dataclass
class Track:
    """Ein Playlist-Eintrag mit optional gelesenen Metadaten."""

    path: str
    artist: str = ""
    title: str = ""

    @property
    def display(self) -> str:
        """Wie das Original es anzeigt: ``Artist - Titel``."""
        if self.artist and self.title:
            return f"{self.artist} - {self.title}"
        if self.title:
            return self.title
        return os.path.basename(self.path)


def read_lst(path: str) -> List[str]:
    """Liest eine .lst-Datei und liefert die Pfade (ohne Kommentare)."""
    entries: List[str] = []
    if not os.path.exists(path):
        return entries
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            entries.append(line)
    return entries


def write_lst(path: str, entries: List[str]) -> None:
    """Schreibt eine .lst-Datei (atomar)."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(f"{entry}\n")
    os.replace(tmp, path)


def read_id3(path: str) -> tuple:
    """Liest Artist/Title aus einer Audiodatei.

    Reihenfolge:

    1. **ID3v2** direkt aus der Datei -- schnell, ohne Prozess,
       und deckt MP3 (und MP3 mit ID3) ab.
    2. **ffprobe** als Fallback -- deckt alle uebrigen Formate ab
       (FLAC, Ogg Vorbis, WAV, M4A/AAC, Opus, WMA ...). ffmpeg
       koennte sie zwar auch dekodieren, aber ohne Tags bliebe der
       Stream namenlos.

    Gibt ``(artist, title)`` zurueck. Ein Fehler fuehrt zu leeren
    Strings -- der Original-Transcaster liest die Tags ebenfalls nur
    zur Anzeige.
    """
    artist, title = _read_id3v2(path)
    if artist or title:
        return (artist, title)
    return read_tags_ffprobe(path)


def _read_id3v2(path: str) -> tuple:
    """Liest ID3v2 TIT2/TPE1 ohne externe Abhaengigkeiten."""
    try:
        with open(path, "rb") as fh:
            header = fh.read(10)
            if len(header) < 10 or header[:3] != b"ID3":
                return ("", "")
            version = header[3]
            size = 0
            for byte in header[6:10]:
                size = (size << 7) | (byte & 0x7F)
            body = fh.read(size)
            return _parse_id3v2(body, version)
    except OSError:
        return ("", "")
    except Exception as exc:  # pragma: no cover - defensiv
        log.debug("ID3-Fehler bei %s: %s", path, exc)
        return ("", "")


#: Die Tag-Schluessel, die wir je Format erwarten (in dieser Reihenfolge).
#:
#: Bei MP4/M4A schreibt ffmpeg die Sonderzeichen "©ART" / "©nam" --
#: die sind unten als Fallback ergaenzt.
_TAG_KEYS = (
    ("artist", ("artist", "album_artist", "performer",
                "©art", "©alb")),
    ("title", ("title", "©nam")),
)


def read_tags_ffprobe(path: str) -> tuple:
    """Liest Artist/Title mit ffprobe -- fuer alle Formate ausser MP3/ID3.

    ffprobe kennt die Tag-Namen je Container unterschiedlich:

    * FLAC  -> "artist" / "title" (Format-Tags)
    * Ogg   -> "artist" / "title" (Stream-Tags!)
    * Opus  -> wie Ogg (Stream-Tags)
    * WAV   -> "artist" / "title" (Format-Tags)
    * M4A   -> "artist" / "title" (Format-Tags)
    * MP4   -> nur "©ART" / "©nam" -- die Sonderzeichen sind der
      Grund, warum hier bewusst ueber die Stream-Tags gelesen wird.

    Deshalb werden **beide** abgefragt: Format-Tags *und* Stream-Tags.

    Faellt ffprobe aus (nicht installiert, Timeout, kaputte Datei),
    wird leise ``("", "")`` geliefert -- die Anzeige ist nicht
    kritisch, der Stream soll laufen.
    """
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        return ("", "")
    try:
        out = subprocess.run(
            [ffprobe, "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", path],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15,
        )
        data = json.loads(out.stdout.decode("utf-8", "replace") or "{}")
    except (subprocess.TimeoutExpired, OSError, ValueError) as exc:
        log.debug("ffprobe-Fehler bei %s: %s", path, exc)
        return ("", "")

    tags = data.get("format", {}).get("tags") or {}
    # ffprobe klein-schreibt die Schluessel; die Original-Schreibweise
    # geht dabei verloren, deshalb wird zusaetzlich ueber die Streams
    # gelesen (dort bleiben die Grossbuchstaben bei MP4 erhalten).
    lowered = {str(k).lower(): v for k, v in tags.items()}
    for stream in data.get("streams", []):
        for k, v in (stream.get("tags") or {}).items():
            lowered.setdefault(str(k).lower(), v)

    result = []
    for _field, candidates in _TAG_KEYS:
        value = ""
        for key in candidates:
            raw = lowered.get(key.lower())
            if raw:
                value = str(raw).strip()
                if value:
                    break
        result.append(value)
    return (result[0], result[1])


def _decode_text(raw: bytes) -> str:
    for enc in ("utf-8", "latin-1"):
        try:
            text = raw.decode(enc)
            # BOM/Nullbytes entfernen
            return text.replace("\x00", "").strip().lstrip("\ufeff")
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace").replace("\x00", "").strip()


def _parse_id3v2(body: bytes, version: int) -> tuple:
    """Sucht TIT2 und TPE1 in einem ID3v2-Block."""
    artist = ""
    title = ""
    pos = 0
    while pos + 10 <= len(body):
        frame_id = body[pos:pos + 4]
        if frame_id == b"\x00\x00\x00\x00":
            break
        if version == 4:
            size = 0
            for byte in body[pos + 4:pos + 8]:
                size = (size << 7) | (byte & 0x7F)
        else:
            size = int.from_bytes(body[pos + 4:pos + 8], "big")
        data = body[pos + 10:pos + 10 + size]
        if frame_id == b"TIT2" and not title:
            title = _first_text(data)
        elif frame_id == b"TPE1" and not artist:
            artist = _first_text(data)
        pos += 10 + size
    return (artist, title)


def _first_text(data: bytes) -> str:
    """ID3-Textframes: Encoding-Byte + Text. Encoding 1 = UTF-16."""
    if not data:
        return ""
    enc = data[0]
    payload = data[1:]
    if enc == 1:
        try:
            return payload.decode("utf-16").replace("\x00", "").strip()
        except UnicodeDecodeError:
            return ""
    if enc == 2:
        return payload.decode("utf-16-be", errors="replace").replace("\x00", "").strip()
    if enc == 3:
        return payload.decode("utf-8", errors="replace").replace("\x00", "").strip()
    return _decode_text(payload)


class Playlist:
    """Eine geladene Playlist mit optionalem Shuffle.

    Thread-sicher: die AutoDJ-Schleife liest, der Reload schreibt.
    """

    def __init__(self, path: str, shuffle: bool = False, seed: Optional[int] = None):
        self.path = path
        self.shuffle = shuffle
        self._rng = random.Random(seed)
        self._lock = threading.Lock()
        self._entries: List[str] = []
        self._cursor = 0
        self._bag: List[str] = []
        self.load()

    def load(self) -> int:
        """Laedt die Playlist neu. Gibt die Anzahl der Eintraege zurueck."""
        entries = read_lst(self.path)
        with self._lock:
            self._entries = entries
            self._cursor = 0
            self._bag = []
        log.info(
            "[PLAYLISTMGR] Playlist load from file %s / Caching / Load complete (%d Eintraege)",
            self.path, len(entries),
        )
        return len(entries)

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def next_track(self) -> Optional[str]:
        """Naechster Pfad, oder ``None`` wenn die Playlist leer ist.

        Bei Shuffle wird ein Shuffle-Bag gefuellt und gemischt -- so wiederholt
        sich ein Titel erst, wenn alle anderen durch sind. Das entspricht dem
        Verhalten des Originals.
        """
        with self._lock:
            if not self._entries:
                return None

            if not self.shuffle:
                if self._cursor >= len(self._entries):
                    log.warning("Playlist has run dry")
                    return None
                path = self._entries[self._cursor]
                self._cursor += 1
                return path

            if not self._bag:
                self._bag = list(self._entries)
                self._rng.shuffle(self._bag)
            path = self._bag.pop()
            return path

    def peek(self) -> Optional[str]:
        """Der naechste Pfad, ohne ihn zu verbrauchen (fuer nextlookahead)."""
        with self._lock:
            if not self._entries:
                return None
            if self.shuffle:
                return self._bag[0] if self._bag else self._entries[0]
            if self._cursor < len(self._entries):
                return self._entries[self._cursor]
            return self._entries[0]

    def all(self) -> List[str]:
        with self._lock:
            return list(self._entries)


class JinglePlayer:
    """Ansagen (Jingles) -- werden zwischen Tracks eingespielt.

    Das Original nutzt ``jingles.lst`` als separate Quelle. Wir mischen die
    Jingles in die Hauptplaylist, damit der Encoder einen durchgehenden
    Stream liefert (das Original schaltet dafuer kurz um).
    """

    def __init__(self, path: str, enabled: bool = True):
        self.path = path
        self.enabled = enabled
        self._entries: List[str] = []
        self._cursor = 0
        self.load()

    def load(self) -> int:
        self._entries = read_lst(self.path)
        self._cursor = 0
        return len(self._entries)

    def next_jingle(self) -> Optional[str]:
        if not self.enabled or not self._entries:
            return None
        path = self._entries[self._cursor % len(self._entries)]
        self._cursor += 1
        return path


class Crossfader:
    """Crossfade-Unterstuetzung.

    ``xfade`` ist die Ueberblendzeit in Sekunden (0 = aus). Da wir den Stream
    segmentweise schreiben, geben wir dem Encoder die Information, wie lange
    der naechste Track vorzeitig gemischt werden soll.
    """

    def __init__(self, seconds: int = 2):
        self.seconds = max(0, seconds)

    @property
    def enabled(self) -> bool:
        return self.seconds > 0

    def overlap_bytes(self, bitrate: int, samplerate: int = 44100, channels: int = 2) -> int:
        """Wie viele Bytes des naechsten Tracks vorzeitig gemischt werden."""
        if not self.enabled:
            return 0
        # 1 Byte = 8 Bit; MP3-Frames sind grob genug ueber diese Naeherung.
        return int(self.seconds * bitrate / 8)

    def fade_gain(self, position: int, total: int) -> float:
        """Verstaerkungsfaktor fuer die Ueberblendung (0.0 .. 1.0)."""
        if total <= 0:
            return 1.0
        if position >= total:
            return 0.0
        return max(0.0, 1.0 - (position / total))
