"""UVOX-2.1-DJ-Protokoll (Server-Seite).

Diese Variante wird mit den Zustaenden
``EMPTY``, ``CIPHER``, ``MSGMIMETYPE``, ``BROADCASTSETUP``,
``AUTHENTICATIONREQUEST``, ``NEGOTIATEBUFFERSIZE``, ``SENDREPLY`` und
``KILL``.

Die vollstaendige Zustaende-Liste:

```
State EMPTY
State KILL
State CIPHER
State SENDREPLY
State MSGMIMETYPE
State BROADCASTSETUP
State AUTHENTICATIONREQUEST
State NEGOTIATEBUFFERSIZE
State ICYNAME
State FILETRANSFERBEGIN
State MAX_PAYLOAD_SIZE
State STANDBY
State FLUSHCACHEDMETADATA
State LISTENERAUTHENTICATION
State TERMINATE
State METADATAXMLNEW
State METADATAXML
```

⇒ Nach der Authentifizierung fragt der Server die ICY-Metadaten beim DJ ab
(``ICYNAME``). Danach folgt der Datentransfer (``FILETRANSFERBEGIN``).

Die Tags:

    <network protocol="
    <shoutcast>
    </shoutcast>
    <uvox>
    </uvox>
    <encoder type="
    mp3
    </encoder>
    </name>
    djcipher
    uvoxcipherkey
    uvoxauth
    uvoxstreamid
    uvoxuserid
    authblob=
    authtoken=
    uid=
    Ultravox/2.1 sc_trans/

Der Client identifiziert sich mit ``Ultravox/2.1 sc_trans/`` im
User-Agent -- das ist die Signatur des UVOX-Clients, den auch der DNAS
akzeptiert.

Im Zustand ``ICYNAME`` fragt der Server die Stream-Metadaten beim DJ ab.
Die Log-Strings dafuer:

```
[DJPROTOCOLUVOX] ICYPUB
[DJPROTOCOLUVOX] ICYPUB for
[DJPROTOCOLUVOX] ICYURL
[DJPROTOCOLUVOX] ICYURL for
[DJPROTOCOLUVOX] ICYGENRE
[DJPROTOCOLUVOX] ICYNAME for
[DJPROTOCOLUVOX] ICYGENRE for
```

⇒ Der Server fragt vier Werte ab: ``ICYNAME``, ``ICYGENRE``, ``ICYURL``,
``ICYPUB``.

Die exakte Elementreihenfolge des Broadcast-Setups ist nur teilweise
festgelegt; siehe ``docs/dj-protocol-spec.md``.
"""

from __future__ import annotations

import logging
import socket
import xml.etree.ElementTree as ET
from typing import Optional

from .wire import DjProtocolError, LINE_TIMEOUT, read_line

log = logging.getLogger("pawcast.dj.uvox")

#: Name dieses Dialekts.
PROTOCOL_NAME = "UVOX"

#: Der User-Agent, mit dem sich ein UVOX-Client identifiziert.
USER_AGENT = "Ultravox/2.1 sc_trans/"

#: Unterstuetzte Broadcast-Protokolle.
NETWORK_PROTOCOLS = ("shoutcast", "uvox")

#: Unterstuetzte Encoder-Typen.
ENCODER_TYPES = ("mp3", "aac")

#: Timeout fuer den kompletten Handshake.
HANDSHAKE_TIMEOUT = 20.0

#: Erfolgsantwort (analog zur SHOUTcast-Variante).
OK2_RESPONSE = b"OK2\r\nicy-caps:11\r\n\r\n"

#: Ablehnungsantwort.
INVALID_PASSWORD_RESPONSE = b"invalid password\r\n"


def _send(sock: socket.socket, data: bytes) -> None:
    try:
        sock.sendall(data)
    except OSError as exc:
        raise DjProtocolError(f"Socket-Fehler beim Senden: {exc}") from exc


def _read_xml(sock: socket.socket) -> Optional[ET.Element]:
    """Liest einen XML-Block (mit/abschliessendem Tag).

    Der UVOX-Handshake besteht aus mehreren kleinen XML-Fragmenten.
    Wir sammeln Zeichen fuer Zeichen, bis der Fragmentschluss da ist.
    """
    buf = []
    depth = 0
    started = False

    while True:
        try:
            raw = read_line(sock, timeout=LINE_TIMEOUT)
        except socket.timeout:
            raise DjProtocolError("Timeout while waiting for icy data")
        if raw is None:
            raise DjProtocolError("Remote socket was closed")

        text = raw.decode("utf-8", "replace").strip()
        if not text:
            continue
        buf.append(text)

        joined = " ".join(buf)
        if "<" not in joined:
            continue
        started = True
        depth = joined.count("<") - joined.count("</") - joined.count("/>")
        # Selbstschliessende Tags mit Inhalt (z. B. <foo/>) neutralisieren.
        depth += joined.count("/>") - joined.count("</")
        if depth <= 0 and started:
            break

    try:
        return ET.fromstring(" ".join(buf))
    except ET.ParseError as exc:
        raise DjProtocolError(f"Cipher request parse error ({exc})") from exc


def _find(root: ET.Element, name: str) -> Optional[ET.Element]:
    """Findet ein Element (case-insensitiv, auch verschachtelt)."""
    for el in root.iter():
        if el.tag.lower() == name.lower():
            return el
    return None


def _find_text(root: ET.Element, name: str) -> Optional[str]:
    el = _find(root, name)
    if el is None:
        return None
    return (el.text or "").strip()


def handle_uvox(client: socket.socket, addr, dj_manager, transcaster) -> None:
    """Behandelt eine DJ-Verbindung im UVOX-Dialekt."""
    peer = addr[0] if addr else "?"
    session = None

    try:
        # --- Schritt 1: Cipher aushandeln ---
        log.info("[DJPROTOCOLUVOX] State CIPHER")
        cipher = _read_xml(client)
        if cipher is None:
            raise DjProtocolError("Cipher request parse error (leer)")

        cipher_key = _find_text(cipher, "uvoxcipherkey") or \
            _find_text(cipher, "djcipher") or ""
        if not cipher_key:
            raise DjProtocolError("Cipher request version error (kein uvoxcipherkey)")
        log.info("[DJPROTOCOLUVOX] Cipher akzeptiert (Schluessel gesetzt)")

        # --- Schritt 2: Mime-Type / Broadcast-Setup ---
        log.info("[DJPROTOCOLUVOX] State MSGMIMETYPE")
        setup = _read_xml(client)
        if setup is None:
            raise DjProtocolError("Authentication string parse error (leer)")

        network = _find_text(setup, "network") or ""
        protocol = ""
        net_el = _find(setup, "network")
        if net_el is not None:
            protocol = (net_el.get("protocol") or "").strip().lower()
        if not protocol:
            protocol = "shoutcast" if "shoutcast" in network.lower() else ""
        if protocol and protocol not in NETWORK_PROTOCOLS:
            raise DjProtocolError(f"Unsupported mime type ({protocol})")

        encoder_type = ""
        enc_el = _find(setup, "encoder")
        if enc_el is not None:
            encoder_type = (enc_el.get("type") or "").strip().lower()
        if encoder_type and encoder_type not in ENCODER_TYPES:
            raise DjProtocolError(f"Unsupported mime type ({encoder_type})")

        log.info("[DJPROTOCOLUVOX] State BROADCASTSETUP")
        log.info("[DJPROTOCOLUVOX] Broadcast-Setup akzeptiert (Protokoll=%s, Encoder=%s)",
                 protocol or "?", encoder_type or "?")

        # --- Schritt 3: Authentifizierung ---
        log.info("[DJPROTOCOLUVOX] State AUTHENTICATIONREQUEST")
        auth = _read_xml(client)
        if auth is None:
            raise DjProtocolError("Authentication string parse error (leer)")

        login = _find_text(auth, "uvoxauth") or _find_text(auth, "username") or ""
        password = _find_text(auth, "password") or ""
        stream_id = _find_text(auth, "uvoxstreamid") or ""
        user_id = _find_text(auth, "uvoxuserid") or ""

        if not login or not password:
            raise DjProtocolError("Authentication string parse error (fehlende Werte)")

        priority = dj_manager.check_credentials(login, password)
        if priority is None:
            log.info("[DJPROTOCOLUVOX] Login denied from `%s`", peer)
            log.info("[DJPROTOCOLUVOX] State KILL")
            _send(client, INVALID_PASSWORD_RESPONSE)
            return

        log.info("[DJPROTOCOLUVOX] Angemeldet: %s (streamid=%s userid=%s)",
                 login, stream_id or "-", user_id or "-")

        # --- Schritt 4: Buffer-Groesse aushandeln ---
        log.info("[DJPROTOCOLUVOX] State NEGOTIATEBUFFERSIZE")
        negotiation = _read_xml(client)
        if negotiation is not None:
            _find_text(negotiation, "buffersize")

        # --- Schritt 5: Antwort ---
        log.info("[DJPROTOCOLUVOX] State SENDREPLY")
        _send(client, OK2_RESPONSE)

        session = dj_manager.register(login, peer, priority, client)

        # --- Schritt 6: Auf Audio warten ---
        from .shoutcast import _wait_for_audio
        _wait_for_audio(client, session, dj_manager, transcaster)

    except DjProtocolError as exc:
        log.info("[DJPROTOCOLUVOX] %s", exc)
        log.info("[DJPROTOCOLUVOX] State KILL")
    except OSError as exc:
        log.info("[DJPROTOCOLUVOX] Socket error: %s", exc)
        log.info("[DJPROTOCOLUVOX] State KILL")
    finally:
        if session is not None:
            dj_manager.unregister(session)
