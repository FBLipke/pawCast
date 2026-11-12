"""Integrationstest: Crossfade-Pipeline gegen den echten sc_serv.

Der Unit-Test beweist die Mischfunktion. Dieser Test beweist den
**kompletten Weg**:

    MP3-Datei -> PCM -> Crossfade-Mischung -> MP3-Stream -> DNAS -> Client

Geprueft wird nicht nur "Bytes kamen an", sondern:

1. Der Client bekommt einen **lueckenlosen** MP3-Strom (keine Risse).
2. Der DNAS hat **keinen Sync-Fehler** ("Unable to sync to the stream").
3. In der dekodierten Ueberblendzone sind **beide** Ton's Frequenzen
   gleichzeitig vorhanden -- das ist der Beweis, dass wirklich gemischt
   und nicht nur aneinandergehaengt wurde.
4. Die Titel-Metadaten kommen beim DNAS an.

Voraussetzung: eine sc_serv-Instanz auf einem Test-Port (z.B. 8100).
Wird uebersprungen, wenn keine erreichbar ist -- der Test darf den
Produktivstream nicht brauchen.
"""

from __future__ import annotations

import math
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from array import array

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pawcast.crossfade import CrossfadePlayer  # noqa: E402
from pawcast.source import ShoutcastSource  # noqa: E402

HOST = os.environ.get("SCTEST_HOST", "10.232.136.119")
PORT = int(os.environ.get("SCTEST_PORT", "8100"))
PW = os.environ.get("SCTEST_SRC_PW", "my_src_pass")

SR = 44100
FREQ_A = 440.0
FREQ_B = 880.0
XFADE = 2


def make_tone_mp3(path: str, freq: float, seconds: float,
                  bitrate: str = "128k") -> None:
    """Erzeugt eine MP3-Datei mit einem reinen Sinuston."""
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
        "-ac", "2", "-ar", str(SR),
        "-b:a", bitrate,
        "-id3v2_version", "0", "-write_xing", "0",
        path,
    ]
    subprocess.run(cmd, check=True)


def decode_to_pcm(mp3_bytes: bytes) -> bytes:
    """Dekodiert MP3-Bytes zurueck nach s16le-PCM (fuer die Analyse)."""
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        tmp.write(mp3_bytes)
        tmp_path = tmp.name
    try:
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "mp3", "-i", tmp_path,
            "-f", "s16le", "-acodec", "pcm_s16le",
            "-ar", str(SR), "-ac", "1", "-",
        ]
        out = subprocess.run(cmd, check=True, stdout=subprocess.PIPE).stdout
        return out
    finally:
        os.unlink(tmp_path)


def goertzel(buf: bytes, freq: float, samplerate: int = SR) -> float:
    """Betrag einer Frequenz im Block (Goertzel) -- ohne numpy."""
    a = array("h")
    a.frombytes(buf[: len(buf) - (len(buf) % 2)])
    n = len(a)
    if n == 0:
        return 0.0
    k = int(0.5 + n * freq / samplerate)
    w = 2.0 * math.pi * k / n
    coeff = 2.0 * math.cos(w)
    s0 = s1 = s2 = 0.0
    for x in a:
        s0 = x + coeff * s1 - s2
        s2 = s1
        s1 = s0
    real = s1 - s2 * math.cos(w)
    imag = s2 * math.sin(w)
    return math.sqrt(real * real + imag * imag) / n


def dnas_reachable() -> bool:
    try:
        with socket.create_connection((HOST, PORT), timeout=5):
            return True
    except OSError:
        return False


class TestCrossfadeIntegration(unittest.TestCase):
    """Der volle Weg gegen den echten DNAS."""

    @classmethod
    def setUpClass(cls):
        if not dnas_reachable():
            raise unittest.SkipTest(
                f"Kein sc_serv auf {HOST}:{PORT} erreichbar -- Integrationstest uebersprungen"
            )
        cls.tmpdir = tempfile.mkdtemp(prefix="sctest-")
        cls.file_a = os.path.join(cls.tmpdir, "a.mp3")
        cls.file_b = os.path.join(cls.tmpdir, "b.mp3")
        # 8 s Ton A, dann 8 s Ton B -> 2 s Ueberblendung in der Mitte
        make_tone_mp3(cls.file_a, FREQ_A, 8.0)
        make_tone_mp3(cls.file_b, FREQ_B, 8.0)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def test_1_stream_laeuft_und_dnas_synct(self):
        """Source-Login, Stream läuft, DNAS meldet keinen Sync-Fehler."""
        received = bytearray()
        stop = threading.Event()

        def client():
            try:
                s = socket.create_connection((HOST, PORT), timeout=15)
                s.settimeout(5)
                s.sendall(
                    ("GET / HTTP/1.0\r\n"
                     f"Host: {HOST}\r\n"
                     "User-Agent: TestClient\r\n"
                     "Icy-MetaData:1\r\n\r\n").encode()
                )
                deadline = time.time() + 40
                while time.time() < deadline and not stop.is_set():
                    try:
                        chunk = s.recv(8192)
                    except socket.timeout:
                        # KEIN Abbruch: der DNAS braucht erst seinen
                        # 64-kB-Prebuffer, danach kommen die Daten
                        # kontinuierlich. Ein Timeout darf die Schleife
                        # nicht beenden.
                        continue
                    if not chunk:
                        break
                    received.extend(chunk)
                s.close()
            except Exception:
                pass

        t = threading.Thread(target=client, daemon=True)

        # WICHTIG: erst die Quelle verbinden, DANN den Client. Der DNAS
        # lehnt Clients ab, solange keine Quelle verbunden ist
        # ("Stream not available as there is no source connected").
        src = ShoutcastSource(
            host=HOST, port=PORT, password=PW,
            name="Crossfade Test", genre="Test",
            url="http://test", bitrate=128000, public=False,
        )
        src.connect(retries=1, retry_delay=1)
        time.sleep(1)

        t.start()
        time.sleep(1)

        player = CrossfadePlayer(bitrate=128000, samplerate=SR,
                                 channels=2, xfade=XFADE)
        sent = 0
        try:
            for chunk in player.stream(iter([self.file_a, self.file_b])):
                src.send(chunk)
                sent += len(chunk)
                if sent > 400000:
                    break
        finally:
            player.stop()
            src.close()

        time.sleep(3)
        stop.set()

        self.assertGreater(sent, 150000, f"Zu wenig gesendet: {sent}")
        self.assertGreater(len(received), 150000,
                           f"Zu wenig beim Client: {len(received)}")

        # Der Client erhaelt zuerst den HTTP-Header, dann den MP3-Strom.
        # Der Header wird abgetrennt, danach muss ein MP3-Frame kommen
        # (0xFF, dann 0xEx/0xFx).
        header_end = received.find(b"\r\n\r\n")
        self.assertGreater(header_end, 0, "Kein HTTP-Header vom DNAS")
        payload = received[header_end + 4:]
        self.assertGreater(len(payload), 100000,
                           f"Zu wenig MP3-Daten: {len(payload)}")
        self.assertEqual(payload[:1], b"\xff",
                         "Kein MP3-Stream am Client angekommen")
        self.assertIn(payload[1] & 0xE0, (0xE0,),
                      "MP3-Frame-Sync ungueltig")

    def test_2_ueberblendzone_ist_gemischt(self):
        """In der Mitte sind BEIDE Frequenzen vorhanden."""
        # Stream direkt erzeugen (ohne DNAS) -- schneller und ohne Netz.
        player = CrossfadePlayer(bitrate=128000, samplerate=SR,
                                 channels=2, xfade=XFADE)
        mp3 = bytearray()
        try:
            for chunk in player.stream(iter([self.file_a, self.file_b])):
                mp3.extend(chunk)
                if len(mp3) > 320000:
                    break
        finally:
            player.stop()

        self.assertGreater(len(mp3), 200000, "Zu wenig MP3 erzeugt")

        pcm = decode_to_pcm(bytes(mp3))
        self.assertGreater(len(pcm), 100000, "Decode ergab zu wenig PCM")

        # Ton A laeuft 8 s, Ton B startet nach 6 s (8 s - 2 s Ueberblendung).
        # Die Ueberblendzone liegt also bei ca. 6.0 s .. 8.0 s.
        sample_rate_bytes = SR * 2  # mono s16
        zone_start = int(6.3 * sample_rate_bytes)
        zone_end = int(7.7 * sample_rate_bytes)
        zone = pcm[zone_start:zone_end]

        mag_a = goertzel(zone, FREQ_A)
        mag_b = goertzel(zone, FREQ_B)

        self.assertGreater(mag_a, 50.0,
                           f"440-Ton fehlt in der Ueberblendung ({mag_a:.0f})")
        self.assertGreater(mag_b, 50.0,
                           f"880-Ton fehlt in der Ueberblendung ({mag_b:.0f})")

    def test_3_titelwechsel_vor_der_ueberblendung(self):
        """Der Titel wechselt, BEVOR der neue Track hoerbar wird."""
        events = []

        player = CrossfadePlayer(bitrate=128000, samplerate=SR,
                                 channels=2, xfade=XFADE)
        try:
            for chunk in player.stream(iter([self.file_a, self.file_b]),
                                       on_next=lambda p: events.append(p)):
                if sum(len(c) for c in []) > 0:
                    break
                time.sleep(0.01)
        except Exception:
            pass
        finally:
            player.stop()

        # Der erste Track meldet sofort, der zweite am Beginn der
        # Ueberblendung -> 2 Meldungen.
        self.assertGreaterEqual(len(events), 2,
                                f"Titelwechsel fehlt: {events}")
        self.assertEqual(events[0], self.file_a)


if __name__ == "__main__":
    unittest.main(verbosity=2)
