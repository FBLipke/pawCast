"""Crossfade-Pipeline: PCM mischen, dauerhaft encoden, streamen.

Das Original (``sc_trans``) blendet mit ``xfade`` Sekunden ueber. Ein
Transcoder kann das nicht durch "zwei MP3-Dateien aneinander haengen" --
dafuer muessen die **Samples** gemischt werden.

Diese Pipeline:

1. Jede Datei wird zu rohem PCM (``s16le``) dekodiert.
2. Die letzten ``xfade`` Sekunden eines Tracks werden zurueckgehalten.
3. Beim Uebergang werden diese mit den ersten ``xfade`` Sekunden des
   naechsten Tracks ueberlagert -- **equal power** (cos/sin), damit die
   perceived loudness konstant bleibt und nicht einbrummt (-3 dB).
4. Ein **dauerhaft laufender** ffmpeg-Prozess encodiert den PCM-Strom
   zu MP3. Es gibt also keine Encoder-Frame-Grenzen im Stream.

Warum ein dauerhafter Encoder: pro Track ein eigener ffmpeg-Prozess
erzeugt Glue-Luecken, und jeder Prozess startet mit einem Encoder-Delay
(~100 ms). Ein durchgehender Prozess liefert einen **lueckenlosen**
MP3-Strom -- Voraussetzung dafuer, dass der ``sc_serv`` sich
synchronisiert und der Client keine Hoerverlust-Risse hoert.

Realtime-Pacing: der PCM-Strom wird in Echtzeit in den Encoder
geschrieben. Der MP3-Ausgang folgt automatisch.
"""

from __future__ import annotations

import logging
import math
import os
import queue
import shutil
import subprocess
import threading
import time
from array import array
from typing import Callable, Iterator, Optional

log = logging.getLogger("pawcast.crossfade")

# 100 ms pro Schritt: klein genug fuer weiche Ueberblendung, gross genug
# fuer geringen Syscall-Overhead.
_CHUNK_MS = 100


class CrossfadeError(Exception):
    """Der Crossfade-Stream konnte nicht gestartet werden."""


def probe_duration(path: str) -> Optional[float]:
    """Dauer einer Datei in Sekunden (ffprobe), oder ``None``.

    Wird gebraucht, um zu wissen, **wo** der Fade-Tail beginnt -- ohne
    die Datei vorher komplett dekodieren zu muessen. Ohne diese Info
    muesste der Player den kompletten Track puffern, bevor er senden
    kann; der DNAS trennt die Quelle dann nach 7 s Stille
    ("Timeout waiting for stream data").
    """
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ).stdout.decode().strip()
        value = float(out)
        return value if value > 0 else None
    except (subprocess.CalledProcessError, ValueError, OSError):
        return None


# ----------------------------------------------------------------------
# Dekodieren
# ----------------------------------------------------------------------
class TrackDecoder:
    """Dekodiert **eine** Datei nach rohem PCM (``s16le``).

    Der Decoder bleibt offen: der Kopf (die ersten ``xfade`` Sekunden)
    kann vorab gelesen werden, der Rest folgt spaeter aus demselben
    Prozess. So wird der naechste Track waehrend des laufenden Tracks
    vorgepackt -- der Uebergang muss nicht warten.

    ``pos`` zaehlt die bereits gelesenen Bytes (absolute Position im
    Track) -- damit weiss der Pump, wie viel vom Track noch kommt.
    """

    def __init__(self, path: str, samplerate: int = 44100, channels: int = 2):
        self.path = path
        self.samplerate = samplerate
        self.channels = channels
        self.pos = 0
        self._ffmpeg = shutil.which("ffmpeg")
        if not self._ffmpeg:
            raise CrossfadeError("ffmpeg nicht gefunden")
        self._proc: Optional[subprocess.Popen] = None
        self._buf = bytearray()
        self._eof = False

    def _start(self) -> None:
        if self._proc is not None:
            return
        cmd = [
            self._ffmpeg, "-hide_banner", "-loglevel", "error",
            "-i", self.path,
            "-vn",
            "-f", "s16le",
            "-acodec", "pcm_s16le",
            "-ar", str(self.samplerate),
            "-ac", str(self.channels),
            "-",
        ]
        try:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
            )
        except OSError as exc:
            raise CrossfadeError(f"Decoder-Start fehlgeschlagen: {exc}") from exc

    def read(self, nbytes: int) -> bytes:
        """Liest bis zu ``nbytes`` Bytes; am Ende weniger.

        Blockiert **nicht** auf ``nbytes`` -- es liefert, was gerade
        verfuegbar ist. Nur wenn ueberhaupt nichts da ist und EOF noch
        nicht erreicht ist, wartet es auf Daten.
        """
        if nbytes <= 0:
            return b""
        self._start()
        assert self._proc is not None and self._proc.stdout is not None
        if not self._buf and not self._eof:
            # Warten, bis ueberhaupt etwas da ist.
            chunk = self._proc.stdout.read(8192)
            if chunk:
                self._buf.extend(chunk)
            else:
                self._eof = True
        out = bytes(self._buf[:nbytes])
        del self._buf[:nbytes]
        self.pos += len(out)
        return out

    def read_exact(self, nbytes: int) -> bytes:
        """Liest **genau** ``nbytes`` (oder weniger am EOF). Blockiert."""
        if nbytes <= 0:
            return b""
        self._start()
        assert self._proc is not None and self._proc.stdout is not None
        while not self._eof and len(self._buf) < nbytes:
            chunk = self._proc.stdout.read(8192)
            if not chunk:
                self._eof = True
                break
            self._buf.extend(chunk)
        out = bytes(self._buf[:nbytes])
        del self._buf[:nbytes]
        self.pos += len(out)
        return out

    def close(self) -> None:
        if self._proc is None:
            return
        try:
            self._proc.terminate()
            self._proc.wait(timeout=5)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass
        for stream in (self._proc.stdout, self._proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except Exception:
                pass
        self._proc = None


# ----------------------------------------------------------------------
# Mischen
# ----------------------------------------------------------------------
def mix_equal_power(tail: bytes, head: bytes) -> bytes:
    """Ueberlagert ``tail`` (ausblendend) mit ``head`` (einblendend).

    Equal-Power-Kurve: ``cos``/``sin``. Anders als eine lineare
    Ueberblendung bleibt die Summe der Leistung konstant -- ein
    lineares Fade laesst die Lautheit in der Mitte um 3 dB einbrechen,
    was als "Hohlklang" in der Ueberblendung auffaellt.

    Beide Bloecke werden auf die kleinere Samplezahl gekuerzt.
    """
    frame = 2  # s16 = 2 Bytes je Sample
    n = min(len(tail) // frame, len(head) // frame)
    if n <= 0:
        return b""

    a = array("h")
    a.frombytes(tail[: n * frame])
    b = array("h")
    b.frombytes(head[: n * frame])

    out = array("h", bytes(n * frame))
    half_pi = math.pi / 2.0
    for i in range(n):
        t = (i + 1) / n
        g_out = math.cos(t * half_pi)
        g_in = math.sin(t * half_pi)
        v = int(a[i] * g_out + b[i] * g_in)
        if v > 32767:
            v = 32767
        elif v < -32768:
            v = -32768
        out[i] = v
    return out.tobytes()


# ----------------------------------------------------------------------
# Track-Pump
# ----------------------------------------------------------------------
class _TrackPump:
    """Pumpt **einen** Track und haelt dabei den Crossfade-Tail zurueck.

    ``body()`` liefert alles ausser den letzten ``tail_bytes``; die
    werden in ``tail`` bereitgehalten und beim Uebergang gemischt.

    Wichtig: der Decoder fuer den **naechsten** Track laeuft bereits,
    waehrend der aktuelle noch spielt (``prepack_head=True``). Der
    Uebergang wartet damit nie auf einen ffmpeg-Start.
    """

    def __init__(self, path: str, samplerate: int, channels: int,
                 tail_bytes: int, chunk_bytes: int, prepack_head: bool = True,
                 expected_bytes: Optional[int] = None):
        self.path = path
        self.tail_bytes = tail_bytes
        self.chunk_bytes = chunk_bytes
        self.expected_bytes = expected_bytes
        self.dec = TrackDecoder(path, samplerate, channels)
        self._buf = bytearray()
        self._tail = b""
        self._body_done = False
        self._read = 0
        self._head = b""
        self._head_ready = threading.Event()
        self._prep_error: Optional[Exception] = None
        self._prep_thread: Optional[threading.Thread] = None
        if prepack_head:
            self._prep_thread = threading.Thread(
                target=self._prepack, daemon=True,
                name=f"prepack-{os.path.basename(path)[:24]}",
            )
            self._prep_thread.start()

    def _prepack(self) -> None:
        """Dekodiert den Kopf vorab (im Hintergrund)."""
        try:
            self._head = self.dec.read_exact(self.tail_bytes)
        except Exception as exc:  # pragma: no cover - defensiv
            self._prep_error = exc
        finally:
            self._head_ready.set()

    def head(self) -> bytes:
        """Wartet auf den vorab dekodierten Kopf."""
        self._head_ready.wait(timeout=120)
        if self._prep_error is not None:
            raise self._prep_error
        return self._head

    def body(self) -> Iterator[bytes]:
        """Yieldet den Track-Body (ohne den zurueckgehaltenen Tail).

        Ist die Tracklaenge bekannt (``expected_bytes``), wird **sofort**
        gestreamt -- nur die letzten ``tail_bytes`` werden zurueckgehalten.
        Das haelt die Latenz bis zum ersten Byte niedrig (der DNAS
        trennt die Quelle nach 7 s Stille).
        """
        if self._body_done:
            return
        self._body_done = True

        if self.expected_bytes:
            # Bekannte Laenge: genau bis zur Fade-Grenze streamen.
            boundary = max(0, self.expected_bytes - self.tail_bytes)
            while self._read < boundary:
                want = min(self.chunk_bytes, boundary - self._read)
                chunk = self.dec.read(want)
                if not chunk:
                    break
                self._read += len(chunk)
                yield chunk

            # Jetzt kommt der Tail.
            while True:
                chunk = self.dec.read(self.chunk_bytes)
                if not chunk:
                    break
                self._read += len(chunk)
                self._tail += chunk
            # Falls der Track laenger war als erwartet: der Ueberhang
            # gehoert zeitlich noch vor die Ueberblendung.
            if len(self._tail) > self.tail_bytes:
                overflow = self._tail[:-self.tail_bytes] if self.tail_bytes else self._tail
                self._tail = self._tail[-self.tail_bytes:] if self.tail_bytes else b""
                if overflow:
                    yield overflow
            return

        # Unbekannte Laenge: alles puffern, bis EOF, dann den Tail
        # abtrennen. (Seltener Fall: Datei ohne lesbare Dauer.)
        while True:
            chunk = self.dec.read(self.chunk_bytes)
            if not chunk:
                break
            self._buf.extend(chunk)
            while len(self._buf) > self.tail_bytes + self.chunk_bytes:
                out = bytes(self._buf[: self.chunk_bytes])
                del self._buf[: self.chunk_bytes]
                yield out

        if self.tail_bytes and len(self._buf) > self.tail_bytes:
            self._tail = bytes(self._buf[-self.tail_bytes:])
            rest = bytes(self._buf[:-self.tail_bytes])
        else:
            self._tail = b""
            rest = bytes(self._buf)
        self._buf = bytearray()
        if rest:
            yield rest

    def tail(self) -> bytes:
        return self._tail

    def close(self) -> None:
        self.dec.close()


# ----------------------------------------------------------------------
# Stream-Encoder
# ----------------------------------------------------------------------
class StreamEncoder:
    """Dauerhaft laufender ffmpeg-Prozess: PCM (stdin) -> MP3 (stdout)."""

    def __init__(self, bitrate: int = 128000, samplerate: int = 44100,
                 channels: int = 2):
        self.bitrate = bitrate
        self.samplerate = samplerate
        self.channels = channels
        self.bytes_per_sec = samplerate * channels * 2
        self._ffmpeg = shutil.which("ffmpeg")
        if not self._ffmpeg:
            raise CrossfadeError("ffmpeg nicht gefunden")
        cmd = [
            self._ffmpeg, "-hide_banner", "-loglevel", "error",
            "-f", "s16le",
            "-ar", str(samplerate),
            "-ac", str(channels),
            # WICHTIG: ohne diese Grenzen sammelt ffmpeg bis zu 5 s
            # (analyzeduration) Input, um den Stream zu analysieren --
            # bei Realtime-Feed sind das 5 s WANDBREITE, in denen der
            # DNAS keine Daten bekommt und die Quelle nach 7 s trennt
            # ("Timeout waiting for stream data"). Das Format ist durch
            # -f s16le bereits festgelegt, also ist keine Analyse noetig.
            "-probesize", "32",
            "-analyzeduration", "0",
            "-i", "pipe:0",
            "-vn",
            "-f", "mp3",
            "-acodec", "libmp3lame",
            "-b:a", str(bitrate),
            # WICHTIG: kein ID3-Header und kein Xing/Info-Frame, sonst
            # kann sich der sc_serv 2.5.5 nicht synchronisieren
            # ("Unable to sync to the stream").
            "-id3v2_version", "0",
            "-write_xing", "0",
            # Ohne das puffert ffmpeg in Blöcken -- der Stream käme
            # dann stoßweise beim Hörer an statt laufend.
            "-flush_packets", "1",
            "pipe:1",
        ]
        try:
            self._proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, bufsize=0,
            )
        except OSError as exc:
            raise CrossfadeError(f"Stream-Encoder-Start fehlgeschlagen: {exc}") from exc

    def write(self, pcm: bytes) -> None:
        assert self._proc is not None and self._proc.stdin is not None
        try:
            self._proc.stdin.write(pcm)
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise CrossfadeError(f"Encoder-Stream gebrochen: {exc}") from exc

    def read(self) -> bytes:
        assert self._proc is not None and self._proc.stdout is not None
        return self._proc.stdout.read(8192)

    def close(self) -> None:
        if self._proc is None:
            return
        try:
            if self._proc.stdin is not None:
                self._proc.stdin.close()
        except Exception:
            pass
        try:
            self._proc.terminate()
            self._proc.wait(timeout=5)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass
        for stream in (self._proc.stdout, self._proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except Exception:
                pass
        self._proc = None


# ----------------------------------------------------------------------
# Player
# ----------------------------------------------------------------------
class CrossfadePlayer:
    """Streamt Tracks mit echter Ueberblendung.

    Der Aufrufer bekommt einen Iterator ueber **MP3-Chunks** -- fertig
    fuer den ``shoutcast.Source``.
    """

    def __init__(self, bitrate: int = 128000, samplerate: int = 44100,
                 channels: int = 2, xfade: int = 2):
        self.bitrate = bitrate
        self.samplerate = samplerate
        self.channels = channels
        self.xfade = max(0, int(xfade))
        self.frame = channels * 2
        self.bytes_per_sec = samplerate * self.frame
        self.tail_bytes = self.xfade * self.bytes_per_sec
        self.chunk_bytes = max(1, (samplerate * self.frame * _CHUNK_MS) // 1000)
        self._stop = threading.Event()
        self._mp3: "queue.Queue[bytes]" = queue.Queue()
        self._encoder: Optional[StreamEncoder] = None
        self._lock = threading.Lock()

    # -- PCM-Erzeugung ------------------------------------------------
    def _pcm_stream(self, paths: Iterator[str],
                    on_next: Optional[Callable[[str], None]] = None) -> Iterator[bytes]:
        """Erzeugt den PCM-Strom mit Ueberblendung.

        ``on_next`` wird aufgerufen, sobald ein Track **zu hoeren
        beginnt** (beim ersten Track sofort, bei den Folge-Tacks am
        Beginn der Ueberblendung) -- das ist der Moment, in dem der
        Titel wechseln muss.
        """
        it = iter(paths)
        try:
            first_path = next(it)
        except StopIteration:
            return

        def make_pump(path: str, prepack: bool) -> _TrackPump:
            """Baut einen Pump und ermittelt die erwartete Laenge.

            Die Laenge wird per ffprobe bestimmt, damit der Player
            **sofort** streamen kann: er muss nur die letzten
            ``tail_bytes`` zurueckhalten und nicht den ganzen Track.
            Ohne das wartet der DNAS vergeblich auf Daten und trennt
            die Quelle nach 7 s ("Timeout waiting for stream data").
            """
            duration = probe_duration(path)
            expected = None
            if duration is not None:
                expected = int(duration * self.bytes_per_sec)
            return _TrackPump(
                path, self.samplerate, self.channels,
                self.tail_bytes, self.chunk_bytes,
                prepack_head=prepack, expected_bytes=expected,
            )

        # Der ERSTE Track braucht keinen vorab dekodierten Kopf -- er
        # wird nur als Tail-Donor benutzt. Sonst wuerde sein Anfang
        # wegfallen.
        pump = make_pump(first_path, prepack=False)
        pending: Optional[_TrackPump] = None
        try:
            if on_next is not None:
                on_next(first_path)

            while True:
                # Naechsten Track vorpacken, waehrend der aktuelle laeuft.
                try:
                    nxt_path = next(it)
                except StopIteration:
                    nxt_path = None
                if nxt_path is not None:
                    pending = make_pump(nxt_path, prepack=self.tail_bytes > 0)

                # Body des aktuellen Tracks senden
                for chunk in pump.body():
                    if self._stop.is_set():
                        return
                    yield chunk

                tail = pump.tail()
                pump.close()

                if pending is None:
                    # Letzter Track: sein Fade-Out-Tail muss noch
                    # gesendet werden, sonst geht der Track-Ende
                    # verloren (xfade Sekunden).
                    if tail:
                        if self._stop.is_set():
                            return
                        yield tail
                    return

                if self.tail_bytes > 0:
                    # Kopf des naechsten Tracks (bereits vorab dekodiert)
                    head = pending.head()
                    if not head:
                        # Leerer Track -> nichts zu mischen
                        pending.close()
                        return

                    if self._stop.is_set():
                        pending.close()
                        return

                    # Der neue Track wird jetzt hoerbar -> Titel wechselt
                    # **vor** der Ueberblendung, nicht danach.
                    if on_next is not None:
                        on_next(pending.path)

                    yield mix_equal_power(tail, head)
                else:
                    # xfade = 0 -> harter Schnitt, kein Mischen noetig.
                    if on_next is not None:
                        on_next(pending.path)

                # Der naechste Track ist jetzt der aktuelle. Sein Body
                # wird im naechsten Durchlauf gepumpt; der Decoder steht
                # an der richtigen Stelle (Kopf bereits gelesen).
                pump = pending
                pending = None
        finally:
            try:
                pump.close()
            except Exception:
                pass
            if pending is not None:
                try:
                    pending.close()
                except Exception:
                    pass

    # -- oeffentliche API ----------------------------------------------
    def stream(self, paths: Iterator[str],
               on_next: Optional[Callable[[str], None]] = None) -> Iterator[bytes]:
        """Liefert MP3-Chunks. Blockiert, bis die Playlist endet."""
        with self._lock:
            self._stop.clear()
            self._encoder = StreamEncoder(self.bitrate, self.samplerate, self.channels)

        writer_done = threading.Event()

        def writer() -> None:
            try:
                enc = self._encoder
                if enc is None:
                    return
                next_write = time.monotonic()
                for pcm in self._pcm_stream(paths, on_next):
                    if self._stop.is_set():
                        break
                    # Realtime-Pacing: der PCM-Strom muss in Echtzeit in
                    # den Encoder laufen, sonst spuelt der DNAS die Daten
                    # in Sekunden statt Minuten.
                    now = time.monotonic()
                    if next_write > now:
                        time.sleep(next_write - now)
                    next_write += len(pcm) / self.bytes_per_sec
                    enc.write(pcm)
            except Exception as exc:
                # Beim Stoppen schliesst ``close()`` den ffmpeg-Encoder,
                # waehrend der Writer noch schreibt -- das ergibt einen
                # BrokenPipeError. Das ist im Normalbetrieb (DJ
                # uebernimmt, Track-Wechsel) erwartet und kein Fehler.
                if isinstance(exc, (BrokenPipeError, CrossfadeError)) and \
                        self._stop.is_set():
                    log.debug("[CROSSFADE] Writer beim Stoppen beendet: %s", exc)
                else:
                    log.error("[CROSSFADE] Writer-Fehler: %s", exc)
            finally:
                writer_done.set()

        def reader() -> None:
            try:
                enc = self._encoder
                if enc is None:
                    return
                while not self._stop.is_set():
                    data = enc.read()
                    if not data:
                        break
                    self._mp3.put(data)
            except Exception as exc:
                log.debug("[CROSSFADE] Reader beendet: %s", exc)

        t_writer = threading.Thread(target=writer, daemon=True, name="xfade-writer")
        t_reader = threading.Thread(target=reader, daemon=True, name="xfade-reader")
        t_writer.start()
        t_reader.start()

        try:
            while True:
                try:
                    data = self._mp3.get(timeout=0.5)
                except queue.Empty:
                    if writer_done.is_set() and self._mp3.empty():
                        break
                    if self._stop.is_set():
                        break
                    continue
                yield data
        finally:
            self.stop()

    def stop(self) -> None:
        """Stoppt den Player (Thread-sicher, idempotent)."""
        self._stop.set()
        enc = self._encoder
        if enc is not None:
            enc.close()
            self._encoder = None
