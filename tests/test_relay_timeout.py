"""Test: der DJ-Stille-Timeout muss unter dem DNAS-Stream-Timeout liegen.

Am Test-DNAS verifiziert: der DNAS trennt eine Source, wenn laenger
als ~9s keine Daten kommen ("Timeout waiting for stream data").

Der DJ-Relay darf diese Grenze nicht ueberschreiten -- sonst kappt
der DNAS die Verbindung zuerst, der Stream bricht ab, und pawcast
merkt es erst, wenn der naechste Send() fehlschlaegt.
"""

from __future__ import annotations

import queue
import sys
import threading
import unittest

sys.path.insert(0, "/mnt/openclaw/workspace/pawcast")

from pawcast.dj.relay import (  # noqa: E402
    RELAY_TIMEOUT_MARGIN,
    SILENCE_TIMEOUT,
    DjRelay,
    RelayError,
)


#: Am Test-DNAS gemessen: DNAS trennt nach ~9s ohne Stream-Daten.
DNAS_STREAM_TIMEOUT = 9.0


class _FakeSource:
    """Nimmt die gesendeten Bytes auf."""

    def __init__(self):
        self.chunks = []

    def send(self, data):
        self.chunks.append(data)
        return len(data)


class _Session:
    """DJ-Session, die nichts sendet (Simuliert einen haengenden DJ)."""

    def __init__(self):
        self.audio_queue = queue.Queue()
        self.bytes_sent = 0
        self.name = "stummer-dj"
        self.address = "10.0.0.1"
        self.sock = object()


class _Manager:
    pass


class TestSilenceTimeout(unittest.TestCase):
    def test_silence_timeout_unter_dnas_timeout(self):
        """Der Stille-Timeout muss kleiner als der DNAS-Timeout sein."""
        self.assertLess(
            SILENCE_TIMEOUT, DNAS_STREAM_TIMEOUT,
            "DJ-Stille-Timeout (%ss) muss kleiner sein als der "
            "DNAS-Stream-Timeout (%ss), sonst kappt der DNAS zuerst"
            % (SILENCE_TIMEOUT, DNAS_STREAM_TIMEOUT))

    def test_reserve_zum_dnas_timeout(self):
        """Es muss Reserve bleiben, um geordnet zurueckzufallen."""
        reserve = DNAS_STREAM_TIMEOUT - SILENCE_TIMEOUT
        self.assertGreaterEqual(
            reserve, RELAY_TIMEOUT_MARGIN,
            "zu wenig Reserve (%ss) zum geordneten Fallback" % reserve)

    def test_relay_bricht_nach_silence_timeout_ab(self):
        """Ein stiller DJ fuehrt zum Relay-Abbruch -- nicht zum Stream-Crash."""
        source = _FakeSource()
        relay = DjRelay(_Manager(), source)
        session = _Session()

        with self.assertRaises(RelayError) as ctx:
            relay.relay_session(session, halt=threading.Event())
        self.assertIn("Timeout", str(ctx.exception))

    def test_relay_sendet_daten_durch(self):
        """Solange der DJ sendet, kommen die Daten beim DNAS an."""
        source = _FakeSource()
        relay = DjRelay(_Manager(), source)
        session = _Session()
        session.audio_queue.put(b"A" * 1024)
        session.audio_queue.put(b"B" * 1024)
        halt = threading.Event()

        def _feed():
            # Nach kurzer Zeit den DJ beenden.
            threading.Event().wait(0.4)
            halt.set()

        threading.Thread(target=_feed, daemon=True).start()
        # Ein gesetztes ``halt`` beendet das Relay geordnet -- ohne Fehler.
        relay.relay_session(session, halt=halt)
        self.assertEqual(b"".join(source.chunks), b"A" * 1024 + b"B" * 1024)


if __name__ == "__main__":
    unittest.main()
