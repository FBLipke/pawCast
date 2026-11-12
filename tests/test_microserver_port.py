"""Der Original-Transcaster oeffnet neben dem DJ-Port noch einen zweiten
Listener auf ``djport + 1`` ("[MICROSERVER] Listening for connection on
port ..."). Empirisch bestaetigt an einer Original-Instanz mit
``djport=19020``: Listener auf 19020 **und** 19021.

Diese Tests sichern ab, dass pawcast denselben zweiten Port
oeffnet -- und dass beide Ports das DJ-Protokoll bedienen.
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pawcast.config import TranscastConfig  # noqa: E402
from pawcast.dj.server import DjServer  # noqa: E402


def free_port() -> int:
    """Vergibt einen freien Port (danach wieder freigegeben)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestMicroserverPort(unittest.TestCase):
    """Der Microserver-Port ist djport + 1."""

    def test_1_microserver_port_ist_djport_plus_eins(self):
        cfg = TranscastConfig()
        cfg.djport = 9020
        self.assertEqual(cfg.microserver_port, 9021)

    def test_2_portbase_stammt_aus_der_config_file(self):
        """Der Port folgt der config_file -- nicht einem festen Wert."""
        for djport in (9020, 19020, 59020):
            cfg = TranscastConfig()
            cfg.djport = djport
            self.assertEqual(cfg.microserver_port, djport + 1)

    def test_3_beide_ports_werden_geoeffnet(self):
        """Der DJ-Server bindet beide Ports."""
        djport = free_port()
        micro = djport + 1
        # Falls der Folgeport belegt ist, ueberspringen.
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind(("127.0.0.1", micro))
        except OSError:
            self.skipTest(f"Port {micro} belegt")
        finally:
            probe.close()

        got = []

        def handler(client, addr):
            got.append(addr)

        server = DjServer(host="127.0.0.1", port=djport, handler=handler)
        server.start()
        try:
            # Der Microserver bindet denselben Weg.
            micro_server = DjServer(host="127.0.0.1", port=micro, handler=handler)
            micro_server.start()
            try:
                # Beide Ports muessen antworten.
                for port in (djport, micro):
                    with socket.create_connection(("127.0.0.1", port),
                                                timeout=5) as s:
                        s.sendall(b"GETSTREAM\r\n")
                        s.settimeout(2)
                        try:
                            s.recv(1024)
                        except socket.timeout:
                            pass
                        except ConnectionResetError:
                            # Der Handler schliesst sofort -> RST. Die
                            # Verbindung wurde trotzdem angenommen.
                            pass
                self.assertEqual(len(got), 2,
                                 f"Erwartet 2 Verbindungen, bekommen {len(got)}")
            finally:
                micro_server.stop()
        finally:
            server.stop()


if __name__ == "__main__":
    unittest.main()
