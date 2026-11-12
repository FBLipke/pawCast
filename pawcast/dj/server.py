"""DJ-Server: nimmt Verbindungen auf dem DJ-Port entgegen.

Der Server laeuft in einem Thread, nimmt Verbindungen an und uebergibt
jede an einen ``protocol_handler``. Die Protokoll-Details (UVOX bzw.
SHOUTCAST) stecken in ``protocol.py``.
"""

from __future__ import annotations

import logging
import socket
import threading
from typing import Optional

log = logging.getLogger("pawcast.dj.server")


class DjServer:
    """TCP-Server fuer DJ-Anmeldungen."""

    def __init__(self, host: str = "0.0.0.0", port: int = 9020, handler=None):
        self.host = host
        self.port = port
        self.handler = handler
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._clients: list = []

    def start(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, self.port))
        sock.listen(16)
        sock.settimeout(0.5)
        self._sock = sock
        self._running = True
        self._thread = threading.Thread(target=self._accept_loop, name="dj-server", daemon=True)
        self._thread.start()
        log.info("[DJ] Server lauscht auf %s:%d", self.host, self.port)

    def _accept_loop(self) -> None:
        while self._running:
            try:
                client, addr = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                if self._running:
                    log.exception("[DJ] Fehler beim Annehmen")
                break

            log.info("[DJ] Verbindung von %s", addr[0])
            t = threading.Thread(target=self._serve, args=(client, addr), daemon=True)
            t.start()
            self._clients.append(t)
            self._clients = [c for c in self._clients if c.is_alive()]

    def _serve(self, client: socket.socket, addr) -> None:
        try:
            if self.handler is not None:
                self.handler(client, addr)
        except Exception:
            log.exception("[DJ] Fehler in der Verbindungsbehandlung")
        finally:
            try:
                client.close()
            except OSError:
                pass

    def stop(self) -> None:
        self._running = False
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        log.info("[DJ] Server gestoppt")
