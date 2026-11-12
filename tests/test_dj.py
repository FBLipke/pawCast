"""Tests fuer das DJ-Protokoll und die Weblet-API.

Ausfuehren:
    python3 -m unittest discover -s tests -v
oder:
    python3 tests/test_dj.py
"""

import os
import socket
import sys
import threading
import time
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pawcast.dj.manager import DjManager  # noqa: E402
from pawcast.dj.protocol import (  # noqa: E402
    detect_protocol,
    parse_credentials,
    DjProtocolError,
)
from pawcast.dj import shoutcast as shoutcast_mod  # noqa: E402
from pawcast.dj import uvox as uvox_mod  # noqa: E402
from pawcast.api import ApiServer, ApiState  # noqa: E402
from pawcast import config as config_mod  # noqa: E402


class TestDetectProtocol(unittest.TestCase):
    """Die Dialekterkennung muss die richtige Variante waehlen."""

    def test_uvox_wird_erkannt(self):
        self.assertEqual(detect_protocol(b"<uvoxauth>foo</uvoxauth>"),
                         uvox_mod.PROTOCOL_NAME)

    def test_source_wird_erkannt(self):
        self.assertEqual(detect_protocol(b"SOURCE pass\r\n"),
                         shoutcast_mod.PROTOCOL_NAME)

    def test_http_wird_erkannt(self):
        self.assertEqual(detect_protocol(b"GET /admin.cgi?pass=x HTTP/1.0\r\n"),
                         shoutcast_mod.PROTOCOL_NAME)

    def test_leer_falls_auf_shoutcast(self):
        self.assertEqual(detect_protocol(b""),
                         shoutcast_mod.PROTOCOL_NAME)


class TestParseCredentials(unittest.TestCase):
    """``<login>:<passwort>`` -- wie im Original an ':' gesplittet."""

    def test_normales_format(self):
        self.assertEqual(parse_credentials("admin:geheim"),
                         ("admin", "geheim"))

    def test_leerzeichen_werden_entfernt(self):
        self.assertEqual(parse_credentials("  admin : geheim  "),
                         ("admin", "geheim"))

    def test_passwort_mit_doppelpunkt(self):
        # Der Split passiert am ERSTEN ':' -- der Rest ist das Passwort.
        self.assertEqual(parse_credentials("admin:a:b"), ("admin", "a:b"))

    def test_ohne_doppelpunkt_ist_es_ein_fehler(self):
        with self.assertRaises(DjProtocolError):
            parse_credentials("adminonly")

    def test_leerer_login_ist_ein_fehler(self):
        with self.assertRaises(DjProtocolError):
            parse_credentials(":geheim")

    def test_leeres_passwort_ist_ein_fehler(self):
        with self.assertRaises(DjProtocolError):
            parse_credentials("admin:")


class TestDjManager(unittest.TestCase):
    """Die Anmeldepruefung des DJ-Managers."""

    def setUp(self):
        self.manager = DjManager()
        self.manager.add_dj("admin", "geheim", 10)
        self.manager.add_dj("chrisenk", "pass2", 9)

    def test_gueltige_anmeldung_gibt_prio(self):
        self.assertEqual(self.manager.check_credentials("admin", "geheim"), 10)
        self.assertEqual(self.manager.check_credentials("chrisenk", "pass2"), 9)

    def test_falsches_passwort(self):
        self.assertIsNone(self.manager.check_credentials("admin", "falsch"))

    def test_unbekannter_dj(self):
        self.assertIsNone(self.manager.check_credentials("nobody", "x"))

    def test_remove_dj(self):
        self.manager.remove_dj("admin")
        self.assertIsNone(self.manager.check_credentials("admin", "geheim"))


class _FakeDjClient:
    """Ein minimaler DJ-Client fuer die Protokolltests."""

    def __init__(self, port):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.sock.settimeout(5)

    def send(self, data: bytes):
        if isinstance(data, str):
            data = data.encode("utf-8")
        self.sock.sendall(data)

    def recv(self, size: int = 4096) -> bytes:
        try:
            return self.sock.recv(size)
        except socket.timeout:
            return b""

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class TestShoutcastProtocol(unittest.TestCase):
    """Der SHOUTcast-Dialekt gegen einen echten TCP-Server."""

    def setUp(self):
        self.manager = DjManager()
        self.manager.add_dj("admin", "geheim", 10)
        self.port = 0
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(("127.0.0.1", self.port))
        self.port = self.server.getsockname()[1]
        self.server.listen(4)
        self.server.settimeout(0.5)
        self._running = True
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def tearDown(self):
        self._running = False
        try:
            self.server.close()
        except OSError:
            pass

    def _serve(self):
        while self._running:
            try:
                client, addr = self.server.accept()
            except (socket.timeout, OSError):
                continue
            threading.Thread(
                target=self._handle, args=(client, addr), daemon=True
            ).start()

    def _handle(self, client, addr):
        try:
            shoutcast_mod.handle_shoutcast(client, addr, self.manager, None)
        except Exception:
            pass
        finally:
            try:
                client.close()
            except OSError:
                pass

    def test_erfolgreiche_anmeldung_antwortet_ok2(self):
        client = _FakeDjClient(self.port)
        client.send("admin:geheim\r\n")
        answer = client.recv()
        client.close()
        self.assertIn(b"OK2", answer)
        self.assertIn(b"icy-caps:11", answer)

    def test_falsches_passwort_wird_abgelehnt(self):
        client = _FakeDjClient(self.port)
        client.send("admin:falsch\r\n")
        answer = client.recv()
        client.close()
        self.assertIn(b"invalid password", answer)
        self.assertNotIn(b"OK2", answer)

    def test_falsches_format_wird_abgelehnt(self):
        client = _FakeDjClient(self.port)
        client.send("nurlogin\r\n")
        answer = client.recv()
        client.close()
        self.assertNotIn(b"OK2", answer)

    def test_unbekannter_dj_wird_abgelehnt(self):
        client = _FakeDjClient(self.port)
        client.send("nobody:x\r\n")
        answer = client.recv()
        client.close()
        self.assertIn(b"invalid password", answer)


class TestApi(unittest.TestCase):
    """Die Weblet-API gegen einen echten HTTP-Server."""

    def setUp(self):
        self.manager = DjManager()
        self.manager.add_dj("admin", "geheim", 10)

        cfg = config_mod.TranscastConfig()
        cfg.adminpassword = ""
        cfg.streamtitle = "Test Stream"
        cfg.playlistfile = ""

        self.state = ApiState(transcaster=None, config=cfg)
        # Der DJ-Manager wird direkt in den State gehaengt, weil es
        # keinen Transcaster gibt.
        self.state._dj_manager = self.manager

        self.port = 0
        self.server = ApiServer(host="127.0.0.1", port=self.port, state=self.state)
        self.server.start()
        self.port = self.server.port
        time.sleep(0.2)

    def tearDown(self):
        self.server.stop()

    def _get(self, path):
        url = f"http://127.0.0.1:{self.port}{path}"
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")

    def test_listdjs_zeigt_die_djs(self):
        status, body = self._get("/api?action=listdjs")
        self.assertEqual(status, 200)
        self.assertIn("admin", body)
        self.assertIn("geheim", body)

    def test_getstatus_liefert_xml(self):
        status, body = self._get("/api?action=getstatus")
        self.assertEqual(status, 200)
        self.assertIn("<status>", body)
        self.assertIn("Test Stream", body)

    def test_unbekannte_action_404(self):
        try:
            self._get("/api?action=quatsch")
            self.fail("404 erwartet")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 404)

    def test_adddj_und_deletedj(self):
        self._get("/api?action=adddj&name=neuer&password=geheim&priority=7")
        self.assertEqual(self.manager.check_credentials("neuer", "geheim"), 7)
        self._get("/api?action=deletedj&name=neuer")
        self.assertIsNone(self.manager.check_credentials("neuer", "geheim"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
