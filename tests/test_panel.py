"""Test: das pawCast-WebPanel.

Das Panel ist eine Single-File-HTML (kein Build, kein CDN) und wird
ueber "/" bzw. "/panel" ausgeliefert.
"""

from __future__ import annotations

import sys
import unittest
import urllib.request

sys.path.insert(0, "/mnt/openclaw/workspace/pawcast")

from pawcast.api import ApiServer, ApiState  # noqa: E402
from pawcast.config import TranscastConfig  # noqa: E402
from pawcast.metadata import MetadataSender  # noqa: E402


class _FakeTranscaster:
    def __init__(self):
        self.metadata = MetadataSender("127.0.0.1", 8000, "pw")
        self.metadata.set_current("pawCast", "Panel Test", album="Album",
                                  genre="Party")
        self.playlist = None
        self.dj_manager = None


class TestPanel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cfg = TranscastConfig()
        cfg.adminpassword = "testpw"
        cls.srv = ApiServer(host="127.0.0.1", port=0,
                            state=ApiState(transcaster=_FakeTranscaster(),
                                           config=cfg))
        cls.srv.start()
        cls.base = "http://127.0.0.1:%d" % cls.srv.port

    @classmethod
    def tearDownClass(cls):
        cls.srv.stop()

    def _get(self, path):
        r = urllib.request.urlopen(self.base + path, timeout=10)
        return r.status, r.headers.get("Content-Type"), r.read()

    def test_panel_served_at_root(self):
        status, ctype, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)
        self.assertGreater(len(body), 5000)

    def test_panel_aliases(self):
        for p in ("/panel", "/panel.html"):
            status, ctype, _ = self._get(p)
            self.assertEqual(status, 200, p)
            self.assertIn("text/html", ctype, p)

    def test_panel_uses_api_routes(self):
        _, _, body = self._get("/")
        html = body.decode("utf-8")
        for route in ("/api/v1/status", "/api/v1/playlist", "/api/v1/djs",
                      "/api/v1/logs", "/metadata"):
            self.assertIn(route, html, route)

    def test_panel_has_no_external_resources(self):
        """Das Panel muss offline im Homelab laufen -- kein CDN.

        Geprueft werden die *Ressourcen*-Referenzen (script, link, img):
        ein CDN-Bundle oder ein externes Stylesheet wuerde das Panel ohne
        Internet unbrauchbar machen. Verweise auf den lokalen DNAS
        (currentsong/nextsong) sind erlaubt -- die werden dynamisch aus
        dem konfigurierten Endpoint gebaut.
        """
        _, _, body = self._get("/")
        html = body.decode("utf-8")
        import re
        refs = re.findall(r'(?is)<(?:script|link|img|iframe)[^>]*'
                          r'(?:src|href)\s*=\s*["\']([^"\']+)["\']', html)
        for ref in refs:
            self.assertFalse(
                ref.lower().startswith(("http://", "https://", "//")),
                "externe Ressource gefunden: %s" % ref)
        # Auch im JS darf keine fremde Domain fest verdrahtet sein.
        for match in re.findall(r'(?is)["\'](https?://[^"\']+)["\']', html):
            self.assertNotIn("shoutcast.com", match.lower())

    def test_css_is_served(self):
        """Das Stylesheet liegt als eigene Datei daneben."""
        r = urllib.request.urlopen(self.base + "/panel.css", timeout=10)
        self.assertEqual(r.status, 200)
        self.assertIn("text/css", r.headers.get("Content-Type"))
        css = r.read().decode("utf-8")
        self.assertGreater(len(css), 1000)

    def test_css_contains_shoutcast_colors(self):
        """Die Farbwerte aus dem originalen DNAS-index.css."""
        _, _, body = self._get("/panel.css")
        css = body.decode("utf-8")
        for color in ("#DEAC2F", "#4C4C4C", "#F0F0F0", "#2350A5",
                      "#2762AE", "#636363", "#CCCCCC"):
            self.assertIn(color, css, color)

    def test_html_links_the_css(self):
        """Das HTML bindet das Stylesheet per Link ein."""
        _, _, body = self._get("/")
        self.assertIn(b'<link rel="stylesheet" href="/panel.css">', body)
        self.assertNotIn(b"<style>", body)

    def test_panel_zeigt_dnas_stream_information(self):
        """Die Sektion "Current Stream Information" wie auf der DNAS-Seite."""
        _, _, body = self._get("/")
        html = body.decode("utf-8")
        self.assertIn("Current Stream Information", html)
        for field in ("Listing Status", "Stream Status", "Listener Peak",
                      "Stream Name", "Playing Now"):
            self.assertIn(field, html, field)

    def test_panel_fragt_dnas_status_ab(self):
        """Das Panel laedt den echten DNAS-Status."""
        _, _, body = self._get("/")
        self.assertIn(b"/api/v1/dnas", body)

    def test_panel_brand(self):
        _, _, body = self._get("/")
        self.assertIn(b"pawCast", body)


if __name__ == "__main__":
    unittest.main()
