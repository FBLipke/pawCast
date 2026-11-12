"""fake_listener.py -- hört auf den Stream und protokolliert die ICY-Metadaten.

Hängt sich als *Hörer* an einen SHOUTcast-Stream (Port 8000) und schreibt
jeden Wechsel des ``StreamTitle`` mit Zeitstempel mit. Damit laesst sich
beobachten, was der Transcaster tatsaechlich sendet -- ohne den DNAS zu
fragen und ohne die Logs des Transcasters zu lesen.

So sieht der Ablauf aus::

    GET / HTTP/1.0
    Icy-MetaData: 1

Der DNAS antwortet mit den ICY-Headern (``icy-name``, ``icy-br`` ...) und
einem ``icy-metaint``. Ab dann kommt der Datenstrom so::

    <metaint Bytes Audio>
    <1 Byte Laenge L>
    <L Bytes Metadaten, auf 16 Byte aufgefuellt>
    <metaint Bytes Audio>
    ...

Der Laengenbyte ist 0, wenn sich der Titel nicht geaendert hat.

Beispiel::

    python3 tools/fake_listener.py --host 10.232.136.119 --port 8000 \\
        --duration 120 --interval 60

Mit ``--json`` kommt JSONL (ein Objekt pro Titelwechsel), sonst eine
lesbare Zeile pro Wechsel plus Zusammenfassung am Ende.
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

#: Der Standard-User-Agent. VLC ist der Referenz-Client, mit dem die
#: meisten Hoerer den Stream abnehmen -- der DNAS liefert ihm die
#: vollen ICY-Header inkl. ``icy-metaint``. Ueber ``--user-agent``
#: anpassbar (z. B. "Winamp/5.9" oder "Mozilla/5.0").
DEFAULT_USER_AGENT = "VLC/3.0.20 LibVLC/3.0.20"

#: StreamTitle='...';  -- der uebliche ICY-Aufbau.
_STREAM_TITLE_RE = re.compile(rb"StreamTitle='(.*?)';", re.IGNORECASE)

#: Fallback, wenn der DNAS kein StreamTitle schickt, sondern direkt
#: "artist - title" (das macht der sc_trans per admin.cgi/updinfo).
_PLAIN_RE = re.compile(r"^(.*?)\s*-\s*(.*)$")


@dataclass
class TitleEvent:
    """Ein Titelwechsel."""

    at: str
    raw: str
    artist: str = ""
    title: str = ""

    def as_line(self) -> str:
        if self.artist and self.title:
            return "%s  %s - %s" % (self.at, self.artist, self.title)
        return "%s  %s" % (self.at, self.raw)

    def as_dict(self) -> dict:
        return {
            "at": self.at,
            "raw": self.raw,
            "artist": self.artist,
            "title": self.title,
        }


@dataclass
class Stats:
    """Was der Listener gesehen hat."""

    bytes_total: int = 0
    audio_bytes: int = 0
    metadata_blocks: int = 0
    title_changes: int = 0
    events: list = field(default_factory=list)
    started: float = 0.0

    @property
    def duration(self) -> float:
        return time.time() - self.started if self.started else 0.0

    def summary(self) -> str:
        return (
            "\n--- Zusammenfassung ---\n"
            "Laufzeit:        %.1f s\n"
            "Audio empfangen: %.1f kB\n"
            "Metadaten-Bloecke: %d\n"
            "Titelwechsel:   %d\n"
            "Bytes gesamt:    %.1f kB"
            % (
                self.duration,
                self.audio_bytes / 1024,
                self.metadata_blocks,
                self.title_changes,
                self.bytes_total / 1024,
            )
        )


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _parse_metadata(payload: bytes) -> Optional[str]:
    """Zieht den Titel aus einem Metadaten-Block."""
    if not payload:
        return None
    match = _STREAM_TITLE_RE.search(payload)
    if match:
        return match.group(1).decode("utf-8", "replace").strip()
    # Manche Server liefern nur den rohen Text.
    text = payload.strip(b"\x00").decode("utf-8", "replace").strip()
    return text or None


def _split_artist_title(raw: str) -> tuple:
    """Teilt "artist - title" in seine beiden Haelften."""
    if not raw:
        return ("", "")
    match = _PLAIN_RE.match(raw)
    if not match:
        return ("", raw)
    artist, title = match.group(1).strip(), match.group(2).strip()
    if not artist:
        return ("", title)
    return (artist, title)


def _is_mp3_sync(chunk: bytes) -> bool:
    """Prueft, ob ein Chunk an einem MP3-Frame-Sync beginnt.

    MP3-Frame: 11 Bits Synchronisation -> erstes Byte 0xFF, zweites
    Byte mit den Bits 0xE0 gesetzt (also 0xE0..0xFF).
    """
    return (len(chunk) >= 2 and chunk[0] == 0xFF
            and (chunk[1] & 0xE0) == 0xE0)


def _read_metadata_block(reader, length_byte: int) -> bytes:
    """Liest einen Metadaten-Block und gibt dessen Payload zurueck.

    **Die Laengenangabe ist bei DNAS-Versionen unterschiedlich:**

    * Der Standard (SHOUTcast, Winamp) nennt die Laenge in **Bytes**.
    * Der sc_serv 2.5.5 (DNAS) zaehlt in **16-Byte-Bloecken** -- ein
      Laengenbyte von 4 bedeutet 64 Bytes, nicht 4. Verifiziert an
      "StreamTitle='Alanis Morissette - Thank U (Official Video)'":
      59 Bytes Text + 5 Fuellnullen = 64 = 4 * 16.

    Weil sich die beiden Varianten nicht am Laengenbyte unterscheiden
    lassen, wird die Variante **empirisch** ermittelt:

    1. Der Metadaten-Text endet auf ``;`` (bei ``StreamTitle='...'``).
    2. Danach muss der MP3-Stream mit einem Frame-Sync beginnen.

    ``reader(n, peek=True)`` liest n Bytes, legt sie aber wieder zurueck
    -- so kann man beide Kandidaten pruefen, ohne den Datenstrom zu
    verschieben.
    """
    if length_byte == 0:
        return b""

    cand_bytes = length_byte
    cand_blocks = length_byte * 16

    # Genug lesen, um beide Kandidaten zu pruefen (inkl. 2 Bytes Sync).
    buf = reader(max(cand_bytes, cand_blocks) + 2, peek=True)

    def ends_with_semicolon(data: bytes) -> bool:
        return data.rstrip(b"\x00").endswith(b";")

    def sync_at(offset: int) -> bool:
        return _is_mp3_sync(buf[offset:offset + 2])

    choice = None
    if sync_at(cand_bytes) and not sync_at(cand_blocks):
        choice = cand_bytes
    elif sync_at(cand_blocks) and not sync_at(cand_bytes):
        choice = cand_blocks
    elif ends_with_semicolon(buf[:cand_bytes]) and \
            not ends_with_semicolon(buf[:cand_blocks]):
        choice = cand_bytes
    elif ends_with_semicolon(buf[:cand_blocks]) and \
            not ends_with_semicolon(buf[:cand_bytes]):
        choice = cand_blocks
    else:
        # Beides oder nichts passt -- dann nehmen wir die Variante,
        # deren Text sauber auf ';' endet, sonst die Byte-Variante.
        if ends_with_semicolon(buf[:cand_blocks]):
            choice = cand_blocks
        elif ends_with_semicolon(buf[:cand_bytes]):
            choice = cand_bytes
        else:
            choice = cand_blocks

    payload = reader(choice)
    return payload


class IcyListener:
    """Lauscht auf einem SHOUTcast-Stream und sammelt die Metadaten."""

    def __init__(self, host: str, port: int, timeout: float = 15.0,
                 user_agent: str = DEFAULT_USER_AGENT):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.user_agent = user_agent
        self.sock: Optional[socket.socket] = None
        self.metaint = 0
        self.headers: dict = {}
        self.stats = Stats()
        self._last_raw: Optional[str] = None
        #: Rest-Bytes aus dem Header-Read -- sonst verschiebt sich die
        #: Block-Grenze und man liest Audio als Metadaten.
        self._buffer = b""

    def connect(self) -> None:
        """Verbindet und liest die ICY-Header."""
        self.sock = socket.create_connection((self.host, self.port),
                                             timeout=self.timeout)
        self.sock.settimeout(self.timeout)
        request = (
            "GET / HTTP/1.0\r\n"
            "Icy-MetaData: 1\r\n"
            "User-Agent: %s\r\n"
            "\r\n"
            % self.user_agent
        )
        self.sock.sendall(request.encode("utf-8", "replace"))

        # Der DNAS antwortet mit Headern + "\r\n\r\n" und dann erst mit
        # Audio. Die Header holen wir komplett ab, danach beginnt der
        # Audio-Stream.
        raw = b""
        while b"\r\n\r\n" not in raw:
            chunk = self.sock.recv(1)
            if not chunk:
                raise ConnectionError(
                    "Server hat die Verbindung vor den Headern geschlossen")
            raw += chunk
            if len(raw) > 16384:
                raise ConnectionError("Header zu gross")

        head, _, rest = raw.partition(b"\r\n\r\n")
        # WICHTIG: der Rest sind die ersten Audio-/Metadaten-Bytes.
        # Werden sie verworfen, sitzt der Block-Zaehler an der
        # falschen Stelle und man liest Audio als Metadaten.
        self._buffer = rest
        for line in head.decode("latin-1").splitlines()[1:]:
            if ":" in line:
                key, _, value = line.partition(":")
                self.headers[key.strip().lower()] = value.strip()

        try:
            self.metaint = int(self.headers.get("icy-metaint", "0"))
        except ValueError:
            self.metaint = 0

        if not self.metaint:
            raise ConnectionError(
                "Server sendet kein icy-metaint -- "
                "Icy-MetaData: 1 wurde nicht ausgehandelt")

    def _recv_exact(self, count: int) -> bytes:
        """Liest genau ``count`` Bytes."""
        if len(self._buffer) >= count:
            out, self._buffer = self._buffer[:count], self._buffer[count:]
            return out
        buf = self._buffer
        self._buffer = b""
        while len(buf) < count:
            chunk = self.sock.recv(max(count - len(buf), 4096))
            if not chunk:
                raise ConnectionError("Verbindung vom Server geschlossen")
            buf += chunk
        out, self._buffer = buf[:count], buf[count:]
        return out

    def _reader(self):
        """Liefert eine Lesefunktion fuer ``_read_metadata_block``.

        Die Funktion unterstuetzt ``peek=True``: dann werden die Bytes
        gelesen, aber wieder in den Puffer zurueckgelegt. Das erlaubt
        die Pruefung des MP3-Sync, ohne den Datenstrom zu verschieben.
        """
        def read(count: int, peek: bool = False) -> bytes:
            out = self._recv_exact(count)
            if peek:
                self._buffer = out + self._buffer
            return out
        return read

    def run(self, duration: Optional[float] = None,
            interval: Optional[float] = None,
            on_event=None) -> Stats:
        """Hört zu und sammelt Metadaten.

        ``duration`` -- Gesamtlaufzeit in Sekunden (None = endlos).
        ``interval`` -- Fortschritt alle X Sekunden ausgeben.
        """
        self.stats.started = time.time()
        deadline = None if duration is None else self.stats.started + duration
        next_report = self.stats.started + (interval or 0)

        while True:
            now = time.time()
            if deadline is not None and now >= deadline:
                break
            if interval and now >= next_report:
                print("[%s] %d Titelwechsel, %.1f kB Audio" % (
                    _now(), self.stats.title_changes,
                    self.stats.audio_bytes / 1024), file=sys.stderr)
                next_report = now + interval

            try:
                audio = self._recv_exact(self.metaint)
            except socket.timeout:
                continue
            except ConnectionError:
                break

            self.stats.audio_bytes += len(audio)
            self.stats.bytes_total += len(audio)

            # Danach kommt das Metadaten-Block: 1 Byte Laenge + Daten.
            try:
                length_byte = self._recv_exact(1)
            except (socket.timeout, ConnectionError):
                break
            length = length_byte[0]
            self.stats.metadata_blocks += 1
            self.stats.bytes_total += 1

            payload = _read_metadata_block(self._reader(), length)
            if payload:
                self.stats.bytes_total += len(payload)

            raw = _parse_metadata(payload)
            if raw is None or raw == self._last_raw:
                continue

            self._last_raw = raw
            self.stats.title_changes += 1
            artist, title = _split_artist_title(raw)
            event = TitleEvent(at=_now(), raw=raw, artist=artist, title=title)
            self.stats.events.append(event)
            if on_event:
                on_event(event)

        return self.stats

    def close(self) -> None:
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Lauscht auf einem SHOUTcast-Stream und protokolliert "
                    "die ICY-Metadaten (StreamTitle).")
    parser.add_argument("--host", default="127.0.0.1",
                        help="DNAS-Host (Default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000,
                        help="Stream-Port (Default: 8000)")
    parser.add_argument("--duration", type=float, default=None,
                        help="Laufzeit in Sekunden (Default: endlos)")
    parser.add_argument("--interval", type=float, default=None,
                        help="Fortschrittsausgabe alle X Sekunden")
    parser.add_argument("--json", action="store_true",
                        help="JSONL-Ausgabe statt lesbarer Zeilen")
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT,
                        help="User-Agent des HTTP-Requests "
                             "(Default: VLC/3.0.20 LibVLC/3.0.20)")
    args = parser.parse_args(argv)

    listener = IcyListener(args.host, args.port, user_agent=args.user_agent)
    try:
        listener.connect()
    except (OSError, ConnectionError) as exc:
        print("Verbindung zu %s:%d fehlgeschlagen: %s"
              % (args.host, args.port, exc), file=sys.stderr)
        return 1

    print("Verbunden mit %s:%d" % (args.host, args.port), file=sys.stderr)
    print("  %-14s %s" % ("user-agent:", args.user_agent), file=sys.stderr)
    for key in ("icy-name", "icy-genre", "icy-br", "icy-sr", "icy-metaint",
                "icy-url"):
        if key in listener.headers:
            print("  %-14s %s" % (key + ":", listener.headers[key]),
                  file=sys.stderr)
    print("  %-14s %d" % ("metaint:", listener.metaint), file=sys.stderr)

    def on_event(event: TitleEvent):
        if args.json:
            print(json.dumps(event.as_dict(), ensure_ascii=False), flush=True)
        else:
            print(event.as_line(), flush=True)

    try:
        stats = listener.run(duration=args.duration, interval=args.interval,
                             on_event=on_event)
    except KeyboardInterrupt:
        print("\nAbgebrochen.", file=sys.stderr)
        stats = listener.stats
    finally:
        listener.close()

    if not args.json:
        print(stats.summary(), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
