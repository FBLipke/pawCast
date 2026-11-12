"""Test der JSON-API (``/api/v1``).

Die Weblet-API des **Originals ist defekt**: jeder Aufruf auf ``/api``
laeuft in einen Timeout (gemessen 8 s ohne Antwort), alle anderen
Pfade liefern 404 "Resource Not Found (Weblet)". pawcast bietet
deshalb eine funktionierende JSON-API.

Diese Tests laufen gegen eine echte HTTP-Server-Instanz -- nicht
gegen Mocks -- damit Routing, Auth und Antwortformat wirklich
geprueft werden.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pawcast.api import ApiServer, ApiState  # noqa: E402
from pawcast.config import TranscastConfig  # noqa: E402


class _FakeTranscaster:
    """Minimaler Transcaster-Ersatz fuer die API-Tests."""

    def __init__(self):
        self.source = type("S", (), {"_connected": True})()
        self.metadata = type("M", (), {
            "sent": [],
            "send_title": lambda self, artist, title: self.sent.append(
                (artist, title)) or True,
        })()

        class _Playlist:
            shuffle = False

            def all(self):
                return ["/music/a.mp3", "/music/b.mp3"]

            def peek(self):
                return "/music/a.mp3"

            def next_track(self):
                return "/music/b.mp3"

            def load(self):
                return 2

        self.playlist = _Playlist()
        self.dj_manager = type("M", (), {
            "djs": {"admin": ("secret", 9)},
            "stats": lambda self: {"connected": 0, "live": 0, "sessions": []},
            "sessions": lambda self: [],
            "live_session": lambda self: None,
            "highest_priority_connected": lambda self: None,
            "add_dj": lambda self, n, p, pr=8: self.djs.__setitem__(
                n, (p, pr)),
            "remove_dj": lambda self, n: self.djs.pop(n, None),
        })()


class TestJsonApi(unittest.TestCase):
    """Die JSON-API unter /api/v1."""

    @classmethod
    def setUpClass(cls):
        cls.cfg = TranscastConfig()
        cls.cfg.adminpassword = "testpw"
        cls.cfg.djport = 9020
        cls.state = ApiState(transcaster=_FakeTranscaster(), config=cls.cfg)
        cls.server = ApiServer(host="127.0.0.1", port=0, state=cls.state)
        cls.server.start()
        cls.base = f"http://127.0.0.1:{cls.server.port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    # -- Hilfsfunktionen ---------------------------------------------
    def _req(self, path, method="GET", params=None, auth=True, body=None):
        url = self.base + path
        if params:
            url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
        data = None
        headers = {}
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if auth:
            token = base64.b64encode(b"admin:testpw").decode()
            headers["Authorization"] = "Basic " + token
        req = urllib.request.Request(url, data=data, headers=headers,
                                     method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode()
            try:
                return exc.code, json.loads(raw)
            except ValueError:
                return exc.code, {"raw": raw}

    # -- Tests --------------------------------------------------------
    def test_1_health_ist_ohne_passwort_erreichbar(self):
        """Der Healthcheck muss ohne Passwort funktionieren."""
        status, body = self._req("/api/v1/health", auth=False)
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")

    def test_2_status_liefert_den_zustand(self):
        status, body = self._req("/api/v1/status")
        self.assertEqual(status, 200)
        self.assertEqual(body["streamtitle"], self.cfg.streamtitle)
        self.assertIn("endpoint", body)
        self.assertIn("microserver_port", body)

    def test_3_playlist_ist_abrufbar(self):
        status, body = self._req("/api/v1/playlist")
        self.assertEqual(status, 200)
        self.assertEqual(body["count"], 2)
        self.assertIn("/music/a.mp3", body["entries"])

    def test_4_playlist_next_springt_weiter(self):
        status, body = self._req("/api/v1/playlist/next", method="POST")
        self.assertEqual(status, 200)
        self.assertEqual(body["skipped_to"], "/music/b.mp3")

    def test_5_shuffle_lschaltet_um(self):
        status, body = self._req("/api/v1/playlist/shuffle", method="POST")
        self.assertEqual(status, 200)
        self.assertIn("shuffle", body)

    def test_6_djs_auflisten(self):
        status, body = self._req("/api/v1/djs")
        self.assertEqual(status, 200)
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["djs"][0]["name"], "admin")
        # Das Passwort darf NICHT ausgeliefert werden.
        self.assertNotIn("password", body["djs"][0])

    def test_7_dj_anlegen_und_loeschen(self):
        status, body = self._req("/api/v1/djs", method="POST",
                                 body={"name": "tester", "password": "x",
                                       "priority": 5})
        self.assertEqual(status, 200)
        self.assertIn("tester", self.state.dj_manager.djs)

        status, body = self._req("/api/v1/djs", method="DELETE",
                                 params={"name": "tester"})
        self.assertEqual(status, 200)
        self.assertNotIn("tester", self.state.dj_manager.djs)

    def test_8_unbekannter_dj_wird_abgelehnt(self):
        status, body = self._req("/api/v1/djs", method="DELETE",
                                 params={"name": "gibtsnicht"})
        self.assertEqual(status, 404)

    def test_9_metadata_senden(self):
        status, body = self._req("/api/v1/metadata", method="POST",
                                 body={"artist": "Test", "title": "Titel"})
        self.assertEqual(status, 200)
        self.assertEqual(self.state.transcaster.metadata.sent[-1],
                         ("Test", "Titel"))

    def test_10_ohne_passwort_401(self):
        status, body = self._req("/api/v1/status", auth=False)
        self.assertEqual(status, 401)

    def test_11_unbekannte_route_404(self):
        status, body = self._req("/api/v1/gibtsnicht")
        self.assertEqual(status, 404)

    def test_12_endpoints_und_logs(self):
        status, body = self._req("/api/v1/endpoints")
        self.assertEqual(status, 200)
        self.assertIn("endpoints", body)

        status, body = self._req("/api/v1/logs")
        self.assertEqual(status, 200)
        self.assertIn("lines", body)


if __name__ == "__main__":
    unittest.main()
