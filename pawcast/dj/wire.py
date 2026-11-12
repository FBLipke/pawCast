"""Gemeinsame Primitive des DJ-Protokolls.

Hier stehen die Dinge, die **beide** Dialekte (SHOUTcast und UVOX)
brauchen: die Fehlerklasse, das Zeilenlesen und das Zerlegen der
Anmeldedaten. Bewusst in einem eigenen Modul, damit ``protocol.py`` und
die Dialektmodule sich nicht gegenseitig importieren.

Die Namen und das Verhalten sind an das Original (``sc_trans`` 2.0.0.54)
angelehnt: das Original meldet bei einem kaputten Format
``Received password is in wrong format`` und splittet am ':'.
"""

from __future__ import annotations

import logging
import socket
from typing import Optional, Tuple

log = logging.getLogger("pawcast.dj.wire")

#: Timeout fuer eine einzelne Protokoll-Zeile (Sekunden).
LINE_TIMEOUT = 10.0

#: Maximale Laenge einer Protokoll-Zeile.
MAX_LINE = 4096


class DjProtocolError(Exception):
    """Das DJ-Protokoll wurde verletzt."""


class BufferedSocket:
    """Socket-Wrapper mit Vorschau-Puffer.

    Der Dispatcher muss die ersten Bytes ansehen, um den Dialekt zu
    erkennen (UVOX beginnt mit ``<``, SHOUTcast mit ``SOURCE``/``GET``).
    Diese Bytes gehen dabei aber **nicht** verloren -- sie stehen danach
    wieder im Puffer und werden als erste Bytes geliefert.

    Das ist kein Luxus: ein echter SAMBC-Client schickt Login und
    Metadaten in einem einzigen TCP-Segment::

        x-effekt:geheim\\r\\nicy-name:Mein Stream\\r\\n

    Ohne Puffer wuerde die Login-Zeile verschluckt und der Handler
    wartete vergeblich auf eine zweite Zeile.
    """

    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock
        self._buffer = bytearray()

    # -- Lesen ---------------------------------------------------------
    def recv(self, size: int = 4096) -> bytes:
        """Liefert Bytes aus dem Puffer, sonst direkt vom Socket."""
        if self._buffer:
            chunk = bytes(self._buffer[:size])
            del self._buffer[:size]
            return chunk
        return self._sock.recv(size)

    def peek_bytes(self, size: int = 1024, timeout: float = 10.0) -> bytes:
        """Liest bis zu ``size`` Bytes **ohne** sie zu verbrauchen."""
        if not self._buffer:
            old_timeout = self._sock.gettimeout()
            self._sock.settimeout(timeout)
            try:
                data = self._sock.recv(size)
            finally:
                self._sock.settimeout(old_timeout)
            self._buffer += data
        return bytes(self._buffer[:size])

    # -- Schreiben -----------------------------------------------------
    def sendall(self, data: bytes) -> None:
        self._sock.sendall(data)

    def shutdown(self, how) -> None:
        self._sock.shutdown(how)

    def close(self) -> None:
        self._sock.close()

    # -- Sonstiges -----------------------------------------------------
    def settimeout(self, value) -> None:
        self._sock.settimeout(value)

    def gettimeout(self):
        return self._sock.gettimeout()

    def fileno(self) -> int:
        return self._sock.fileno()

    def getpeername(self):
        return self._sock.getpeername()


def read_line(sock: socket.socket, timeout: float = LINE_TIMEOUT,
              limit: int = MAX_LINE) -> Optional[bytes]:
    """Liest eine Zeile (bis ``\\r\\n`` oder ``\\n``).

    Gibt ``None`` zurueck, wenn die Gegenstelle die Verbindung schliesst.
    Wirft ``socket.timeout`` bei Timeout.
    """
    buf = bytearray()
    old_timeout = sock.gettimeout()
    sock.settimeout(timeout)
    try:
        while len(buf) < limit:
            chunk = sock.recv(1)
            if not chunk:
                if not buf:
                    return None
                break
            if chunk == b"\n":
                break
            if chunk == b"\r":
                # Ein optionales following "\n" wird beim naechsten
                # read_line als Leerzeile gelesen und dort ignoriert.
                continue
            buf += chunk
    finally:
        sock.settimeout(old_timeout)
    return bytes(buf)


def parse_credentials(raw: str) -> Tuple[str, str]:
    """Zerlegt ``<login>:<passwort>``.

    Wirft ``DjProtocolError``, wenn das Format nicht stimmt -- exakt wie
    der Original-Transcaster ("Received password is in wrong format").
    """
    if ":" not in raw:
        raise DjProtocolError("Received password is in wrong format")
    login, _, password = raw.partition(":")
    login = login.strip()
    password = password.strip()
    if not login or not password:
        raise DjProtocolError("Received password is in wrong format")
    return login, password
