"""Test-DNAS: ein minimaler SHOUTcast-Server-Attrappen.

Nimmt Source-Anmeldungen entgegen, antwortet mit ``ICY 200 OK`` und
zaehlt die empfangenen Bytes. Damit laesst sich der Source-Handshake
von pawcast ohne echten DNAS verifizieren.

Nur fuer Tests -- nicht produktiv verwenden.
"""

from __future__ import annotations

import socket
import threading
from typing import Optional


class FakeDnas:
    """Ein DNAS-Attrapp auf localhost."""

    def __init__(self, host: str = "127.0.0.1", port: int = 18000,
                 password: str = "main_src_pass", reject: bool = False):
        self.host = host
        self.port = port
        self.password = password
        self.reject = reject
        self.bytes_received = 0
        self.request_raw = b""
        self.connected = threading.Event()
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def start(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, self.port))
        sock.listen(4)
        sock.settimeout(0.5)
        self._sock = sock
        self._running = True
        self._thread = threading.Thread(target=self._accept, daemon=True)
        self._thread.start()

    def _accept(self) -> None:
        while self._running:
            try:
                client, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._serve, args=(client,), daemon=True).start()

    def _serve(self, client: socket.socket) -> None:
        client.settimeout(10)
        data = b""
        try:
            while b"\r\n\r\n" not in data:
                chunk = client.recv(1024)
                if not chunk:
                    return
                data += chunk
        except socket.timeout:
            return

        self.request_raw = data
        text = data.decode("utf-8", "replace")

        if self.reject or f"pass={self.password}" not in text:
            client.sendall(b"HTTP/1.0 401 Unauthorized\r\n\r\n")
            client.close()
            return

        client.sendall(
            b"ICY 200 OK\r\n"
            b"icy-name:Test DNAS\r\n"
            b"icy-br:128\r\n"
            b"icy-genre:Test\r\n"
            b"icy-metaint:16000\r\n"
            b"\r\n"
        )
        self.connected.set()

        try:
            while self._running:
                chunk = client.recv(16384)
                if not chunk:
                    break
                self.bytes_received += len(chunk)
        except OSError:
            pass
        finally:
            client.close()

    def stop(self) -> None:
        self._running = False
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
