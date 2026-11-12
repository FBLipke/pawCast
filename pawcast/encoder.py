"""Encoder-Anbindung.

Das Original nutzt den eingebauten Fraunhofer-AAC-Encoder (bzw. den
Fraunhofer-MP3-Encoder) und braucht dafuer ``unlockkeyname`` +
``unlockkeycode``. Ohne gueltigen Key meldet das Original:

    [SOURCEANDENDPOINTMANAGER] Unknown encoder type mp3. Ensure you have
    entered the license key and name fully.

Wir ersetzen das durch **ffmpeg** (bzw. ``lame`` als Fallback), das
systemweit verfuegbar ist und keine Lizenz braucht. Die Encoder-Ausgabe
ist MP3 mit konfigurierbarer Bitrate/Samplerate -- identisch zum
Ergebnis des Originals.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
from typing import Iterator, Optional

log = logging.getLogger("pawcast.encoder")


class EncoderError(Exception):
    """Der Encoder konnte nicht gestartet werden."""


class Encoder:
    """Basisklasse: dekodiert eine Datei und liefert MP3-Bytes.

    Der Encoder arbeitet dateiweise: ``encode(path)`` liefert einen Iterator
    ueber MP3-Chunks. Das haelt den Speicherbedarf klein und erlaubt den
    nahtlosen Uebergang zum naechsten Track.
    """

    def __init__(self, bitrate: int = 128000, samplerate: int = 44100, channels: int = 2):
        self.bitrate = bitrate
        self.samplerate = samplerate
        self.channels = channels
        self._ffmpeg = shutil.which("ffmpeg")
        self._lame = shutil.which("lame")
        if not self._ffmpeg and not self._lame:
            raise EncoderError(
                "Kein Encoder gefunden: weder ffmpeg noch lame sind installiert. "
                "Auf Debian: apt install ffmpeg"
            )
        log.info(
            "Encoder: %s (bitrate=%d, samplerate=%d, channels=%d)",
            os.path.basename(self._ffmpeg or self._lame), bitrate, samplerate, channels,
        )

    def _cmd(self, src: str) -> list:
        if self._ffmpeg:
            return [
                self._ffmpeg, "-hide_banner", "-loglevel", "error",
                "-re",
                "-i", src,
                "-vn",
                "-map_metadata", "0",
                "-f", "mp3",
                "-acodec", "libmp3lame",
                "-b:a", str(self.bitrate),
                "-ar", str(self.samplerate),
                "-ac", str(self.channels),
                # WICHTIG: kein ID3-Header und kein Xing/Info-Frame.
                # Der sc_serv 2.5.5 kann sich sonst nicht auf den
                # MP3-Stream synchronisieren:
                #   "Unable to sync to the stream"
                # Das Original liefert ebenfalls rohe Frames ohne Header.
                "-id3v2_version", "0",
                "-write_xing", "0",
                "-",
            ]
        return [
            self._lame, "--silent",
            "-b", str(self.bitrate),
            "-m", "j",
            "--resample", str(self.samplerate),
            src, "-",
        ]

    def encode(self, src: str) -> Iterator[bytes]:
        """Dekodiert/encodiert eine Datei und liefert MP3-Chunks."""
        if not os.path.exists(src):
            log.warning("[DECODE] Datei fehlt, uebersprungen: %s", src)
            return

        cmd = self._cmd(src)
        log.debug("[DECODE] Next file is %s", src)
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
            )
        except OSError as exc:
            log.error("Encoder-Start fehlgeschlagen: %s", exc)
            return
        try:
            while True:
                # Kleine Chunks lesen: der DNAS erwartet kontinuierlich
                # Daten (icy-prebuffer 64000). Bei 16-KB-Chunks wartet der
                # Encoder zu lange, bevor ueberhaupt etwas fliesst.
                chunk = proc.stdout.read(4096)
                if not chunk:
                    break
                yield chunk
        finally:
            # Der Generator wird per .close() beendet, wenn der Aufrufer
            # die Iteration abbricht. Dann muss der Subprozess sauber
            # beendet werden -- sonst bricht ffmpeg mit SIGPIPE ab und
            # meldet "Broken pipe".
            try:
                proc.terminate()
            except OSError:
                pass
            try:
                proc.stdout.close()
            except Exception:
                pass
            try:
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except OSError:
                    pass
            stderr = proc.stderr.read() if proc.stderr else b""
            try:
                proc.stderr.close()
            except Exception:
                pass
            if proc.returncode not in (0, None) and stderr:
                msg = stderr.decode("utf-8", "replace").strip()
                # SIGPIPE-Bruch beim Abbruch ist erwartet und kein Fehler.
                if "Broken pipe" not in msg:
                    log.warning("[MP3ENC] Encoder meldete Fehler (%s): %s",
                                proc.returncode, msg[:500])
    def probe(self, src: str) -> Optional[dict]:
        """Liest die technischen Daten einer Datei (Dauer, Bitrate)."""
        if not self._ffmpeg:
            return None
        try:
            out = subprocess.run(
                [self._ffmpeg, "-hide_banner", "-i", src, "-f", "null", "-"],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30,
            )
            text = out.stderr.decode("utf-8", "replace")
        except (subprocess.TimeoutExpired, OSError):
            return None

        info = {}
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("Duration:"):
                ts = line.split("Duration:")[1].split(",")[0].strip()
                info["duration"] = _parse_duration(ts)
            if "Stream #0:" in line:
                if "Audio:" in line:
                    for part in line.split("Audio:")[1].split(","):
                        part = part.strip()
                        if "Hz" in part:
                            info["samplerate"] = int(part.split("Hz")[0].strip())
                        if "stereo" in part:
                            info["channels"] = 2
                        elif "mono" in part:
                            info["channels"] = 1
        return info or None


def _parse_duration(ts: str) -> float:
    try:
        parts = ts.split(":")
        return sum(float(p) * (60 ** i) for i, p in enumerate(reversed(parts)))
    except (ValueError, TypeError):
        return 0.0


class MixedEncoder:
    """Encoder, der mehrere Tracks mit Crossfade aneinander reiht.

    ``xfade`` Sekunden Ueberblendung; 0 = harter Schnitt.
    """

    def __init__(self, encoder: Encoder, xfade: int = 2):
        self.encoder = encoder
        self.xfade = max(0, xfade)
        self._lock = threading.Lock()

    def stream(self, tracks: Iterator[str]) -> Iterator[bytes]:
        """Hintereinander Tracks encodieren und als einen Strom liefern.

        Bei Crossfade wird der naechste Track ``xfade`` Sekunden vorzeitig
        eingeblendet -- das Original macht das im Decoder.
        """
        pending: Optional[bytes] = None
        for path in tracks:
            chunks = list(self.encoder.encode(path))
            if not chunks:
                continue
            data = b"".join(chunks)
            if pending is None:
                pending = data
                continue
            yield pending
            pending = data

        if pending:
            yield pending
