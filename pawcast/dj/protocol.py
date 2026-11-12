"""Dispatcher fuer das DJ-Protokoll.

Der Original-Transcaster ``sc_trans`` 2.0.0.54 spricht auf dem DJ-Port
zwei Dialekte:

* **SHOUTCAST** -- vereinfachtes Protokoll (Zustaende ``GETSTREAM``,
  ``GETICY``, ``NEEDPASS``, ``CHECKPASS``, ``SENDREPLY``, ``KILL``).
* **UVOX** -- vollstaendiges Ultravox-2.1-Handshake mit Cipher- und
  Broadcast-Setup.

Beide teilen sich den Dispatcher dieses Moduls: gelesen werden die ersten
Bytes der Verbindung, daraus wird der Dialekt abgeleitet, dann uebernimmt
das jeweilige Fachmodul.

Protokoll-Referenz (siehe ``docs/dj-protocol-spec.md``):

=============================================  ==========
Befund                                        Status
=============================================  ==========
``OK2\\r\\nicy-caps:11\\r\\n\\r\\n``
``invalid password\\r\\n``
Trennung am ``:`` -> ``<login>:<passwort>``
``content-type:audio/``
UVOX-Tagnamen
Exakte UVOX-Elementreihenfolge                TEILWEISE
=============================================  ==========
"""

from __future__ import annotations

import logging
import socket
from typing import Optional

from . import shoutcast as shoutcast_mod
from . import uvox as uvox_mod
from .wire import (  # noqa: F401  (re-exportiert fuer Aufrufer)
    BufferedSocket,
    DjProtocolError,
    LINE_TIMEOUT,
    MAX_LINE,
    parse_credentials,
    read_line,
)

log = logging.getLogger("pawcast.dj.protocol")

#: Maximale Laenge des ersten Blocks, den wir zur Erkennung lesen.
PEEK_BYTES = 1024

#: Timeout fuer die Erkennung (Sekunden).
PEEK_TIMEOUT = 10.0


def detect_protocol(data: bytes) -> str:
    """Leitet den Dialekt aus den ersten empfangenen Bytes ab.

    * ``<`` am Anfang  -> UVOX (XML-artiger Handshake)
    * ``SOURCE``       -> SHOUTcast-v1-Quellprotokoll
    * ``GET``/``POST`` -> HTTP-artiger Dialekt (ebenfalls SHOUTcast)
    """
    head = data.lstrip()
    if head.startswith(b"<"):
        return uvox_mod.PROTOCOL_NAME
    upper = head[:16].upper()
    if upper.startswith(b"SOURCE"):
        return shoutcast_mod.PROTOCOL_NAME
    if upper.startswith((b"GET ", b"POST ", b"HEAD ")):
        return shoutcast_mod.PROTOCOL_NAME
    return shoutcast_mod.PROTOCOL_NAME


def peek(sock: BufferedSocket, timeout: float = PEEK_TIMEOUT) -> bytes:
    """Liest den ersten Block, ohne die Daten zu verbrauchen.

    Der Socket ist ein :class:`BufferedSocket` -- die gelesenen Bytes
    landen im Puffer und stehen dem Protokoll-Handler danach wieder zur
    Verfuegung. Sonst wuerde die Login-Zeile verschluckt.
    """
    return sock.peek_bytes(PEEK_BYTES, timeout)


def handle_connection(client: socket.socket, addr, dj_manager, transcaster) -> None:
    """Behandelt eine DJ-Verbindung von Ende zu Ende.

    Wird vom ``DjServer`` fuer jede angenommene Verbindung aufgerufen.
    """
    peer = addr[0] if addr else "?"
    log.info("[DJ] Verbindung von %s", peer)

    # Der Wrapper puffert: die Vorschau verbraucht nichts.
    client = BufferedSocket(client)

    try:
        first = peek(client)
    except socket.timeout:
        log.info("[DJ] Timeout beim Warten auf den ersten Block von %s", peer)
        return
    except OSError as exc:
        log.info("[DJ] Socket-Fehler von %s: %s", peer, exc)
        return

    if not first:
        log.info("[DJ] Gegenstelle %s hat sofort geschlossen", peer)
        return

    protocol = detect_protocol(first)
    log.info("[DJ] %s: Protokoll erkannt als %s (erste Bytes: %r)",
             peer, protocol, first[:32])

    if protocol == uvox_mod.PROTOCOL_NAME:
        handler = uvox_mod.handle_uvox
    else:
        handler = shoutcast_mod.handle_shoutcast

    try:
        handler(client, addr, dj_manager, transcaster)
    except DjProtocolError as exc:
        log.info("[DJ] Protokollfehler: %s", exc)
    except OSError as exc:
        log.info("[DJ] Verbindung zu %s verloren: %s", peer, exc)
