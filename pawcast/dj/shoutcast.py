"""SHOUTcast-DJ-Protokoll (Server-Seite).

Der Original-Transcaster implementiert diese Variante mit den Zustaenden
``GETSTREAM``, ``GETICY``, ``NEEDPASS``, ``CHECKPASS``, ``SENDREPLY``,
``EMPTY`` und ``KILL``.

Ablauf::

    EMPTY  -> warten auf die erste Anfrage
    GETSTREAM -> die Verbindung wird als Stream angefragt
    GETICY -> ICY-Metadaten werden erwartet
    NEEDPASS -> das Passwort wird angefordert
    CHECKPASS -> das Passwort wird geprueft
    SENDREPLY -> "OK2\\r\\nicy-caps:11\\r\\n\\r\\n" wird gesendet
    KILL -> die Verbindung wird beendet

Siehe ``docs/dj-protocol-spec.md``.
"""

from __future__ import annotations

import logging
import queue
import socket
import time
from typing import Optional, Tuple
from urllib.parse import parse_qs, urlparse

from .wire import DjProtocolError, LINE_TIMEOUT, read_line, parse_credentials

log = logging.getLogger("pawcast.dj.shoutcast")

#: Name dieses Dialekts.
PROTOCOL_NAME = "SHOUTCAST"

#: Erfolgsantwort an einen DJ.
OK2_RESPONSE = b"OK2\r\nicy-caps:11\r\n\r\n"

#: Ablehnungsantwort.
INVALID_PASSWORD_RESPONSE = b"invalid password\r\n"

#: Wie lange ein DJ ohne Audio connected bleiben darf (Sekunden).
#: SAMBC connectet sofort, sendet aber erst MP3-Frames, wenn wirklich
#: Material in der Playlist liegt. Der Wert muss grosszuegig sein, sonst
#: trennt der Transcaster den Client, bevor er senden kann.
IDLE_TIMEOUT = 300.0

#: Upperlimit fuer die Passwortzeile.
MAX_PASSWORD_LENGTH = 1024

#: Unterstuetzte Content-Types fuer den Audio-Strom.
CONTENT_TYPES = (
    b"content-type:audio/",
    b"content-type:application/ogg",
    b"content-type:video/",
)


def _send(sock: socket.socket, data: bytes) -> None:
    """Sendet Bytes und behandelt Broken-Pipe sauber."""
    try:
        sock.sendall(data)
    except OSError as exc:
        raise DjProtocolError(f"Socket-Fehler beim Senden: {exc}") from exc


def _read_request_line(sock: socket.socket) -> Optional[str]:
    """Liest die erste Zeile (Request-Zeile oder Passwortzeile).

    Gibt ``None`` zurueck, wenn die Gegenstelle schliesst.
    """
    try:
        raw = read_line(sock, timeout=LINE_TIMEOUT)
    except socket.timeout:
        raise DjProtocolError("Timeout while waiting to receive password")
    if raw is None:
        raise DjProtocolError("Remote socket was closed while waiting for password")
    if len(raw) > MAX_PASSWORD_LENGTH:
        raise DjProtocolError("Password length has been exceeded")
    return raw.decode("utf-8", "replace").strip()


def _credentials_from_request(request_line: str) -> Optional[str]:
    """Zieht das Passwort aus einem HTTP-Request.

    SAMBC (und das Original) schicken das Passwort als Query-Parameter::

        GET /admin.cgi?pass=<login>:<passwort> HTTP/1.0

    Der Wert kann selbst einen Doppelpunkt enthalten -- deshalb wird nur
    am **ersten** Doppelpunkt getrennt (Login : Passwort).
    """
    parts = request_line.split(" ")
    if len(parts) < 2:
        return None
    query = parse_qs(urlparse(parts[1]).query)
    values = query.get("pass") or query.get("password")
    if not values:
        return None
    return values[0]


def handle_shoutcast(client: socket.socket, addr, dj_manager, transcaster) -> None:
    """Behandelt eine DJ-Verbindung im SHOUTcast-Dialekt.

    Der Ablauf ist tolerant: je nachdem, ob der DJ zuerst ein Passwort
    oder zuerst einen Stream-Request schickt, wird die Reihenfolge
    erkannt. Das entspricht dem Original, das je nach Client
    unterschiedliche Zustaende durchlaeuft.
    """
    peer = addr[0] if addr else "?"
    login: Optional[str] = None
    password: Optional[str] = None
    session = None

    try:
        # --- Schritt 1: Passwort oder Stream-Request ---
        first = _read_request_line(client)
        if not first:
            raise DjProtocolError("Leere erste Zeile")

        if ":" in first and " " not in first:
            # Direkt das Passwort: "<login>:<passwort>"
            login, password = parse_credentials(first)
            log.info("[DJPROTOCOLSHOUTCAST] State NEEDPASS")
            log.info("[DJPROTOCOLSHOUTCAST] State CHECKPASS")
        elif first.upper().startswith(("GET ", "POST ", "SOURCE ")):
            # Das Passwort kann im Request selbst stecken (Query-Parameter
            # ``?pass=<login>:<passwort>``) -- das macht SAMBC so.
            embedded = _credentials_from_request(first)
            if embedded:
                log.info("[DJPROTOCOLSHOUTCAST] State GETSTREAM")
                # Im Query-Parameter akzeptiert der Original-Transcaster
                # **nur das Passwort** -- kein "<login>:<passwort>".
                # Der Login wird ueber das Passwort ermittelt.
                password = embedded.strip()
                found = dj_manager.find_by_password(password)
                if found is None:
                    log.info("[DJPROTOCOLSHOUTCAST] Login denied from %s", peer)
                    _send(client, INVALID_PASSWORD_RESPONSE)
                    return
                login = found[0]
            else:
                log.info("[DJPROTOCOLSHOUTCAST] State GETSTREAM")
                # Der Stream-Request kam zuerst -> jetzt das Passwort.
                second = _read_request_line(client)
                if second is None:
                    raise DjProtocolError(
                        "Remote socket was closed while waiting for password")
                login, password = parse_credentials(second)
            log.info("[DJPROTOCOLSHOUTCAST] State NEEDPASS")
            log.info("[DJPROTOCOLSHOUTCAST] State CHECKPASS")
        else:
            raise DjProtocolError("Received password is in wrong format")

        # --- Schritt 2: Passwort pruefen ---
        priority = dj_manager.check_credentials(login, password)
        if priority is None:
            log.info("[DJPROTOCOLSHOUTCAST] Login denied from %s", peer)
            log.info("[DJPROTOCOLSHOUTCAST] State KILL")
            _send(client, INVALID_PASSWORD_RESPONSE)
            return

        log.info("[DJPROTOCOLSHOUTCAST] State SENDREPLY")
        _send(client, OK2_RESPONSE)
        log.info("[DJPROTOCOLSHOUTCAST] Connected")

        # --- Schritt 3: Session registrieren ---
        session = dj_manager.register(login, peer, priority, client)

        # --- Schritt 4: Auf Audio warten ---
        log.info("[DJPROTOCOLSHOUTCAST] State GETICY")
        _wait_for_audio(client, session, dj_manager, transcaster)

    except DjProtocolError as exc:
        log.info("[DJPROTOCOLSHOUTCAST] %s", exc)
        log.info("[DJPROTOCOLSHOUTCAST] State KILL")
    except OSError as exc:
        log.info("[DJPROTOCOLSHOUTCAST] Socket error: %s", exc)
        log.info("[DJPROTOCOLSHOUTCAST] State KILL")
    finally:
        if session is not None:
            dj_manager.unregister(session)


def _wait_for_audio(client: socket.socket, session, dj_manager, transcaster) -> None:
    """Wartet auf den Audio-Strom des DJs.

    Nach dem ``OK2`` schickt ein echter Client (z. B. SAMBC) zuerst
    seinen Header-Block (``icy-name:``, ``icy-genre:``, ``icy-br:`` usw.)
    und danach erst die MP3-Frames. Der Header-Block endet mit einer
    Leerzeile -- die ueberspringen wir, sonst wuerden die Header als
    Audio gezaehlt.

    Dieser Thread ist der **einzige**, der den DJ-Socket liest. Die
    Bytes wandern in ``session.audio_queue``; der Relay holt sie von
    dort und schickt sie zum DNAS.

    Das ist entscheidend: wenn zwei Threads denselben Socket lesen,
    frisst sich einer die Daten des anderen weg -- und der DJ bleibt
    im TCP-Fenster stecken ("unable to send data fast enough").
    """
    client.settimeout(1.0)
    # Der DJ darf sich Zeit lassen: SAMBC connectet sofort, aber schickt
    # erst Audio, wenn wirklich etwas in der Playlist liegt. 30 s waren
    # dafuer zu knapp -- der Client wurde getrennt, bevor er senden
    # konnte, und reconnectete endlos.
    deadline = time.time() + IDLE_TIMEOUT
    got_audio = False
    in_header = True

    while True:
        try:
            data = client.recv(16384)
        except socket.timeout:
            if time.time() > deadline:
                raise DjProtocolError(
                    "Timeout while waiting to receive password")
            continue
        except OSError as exc:
            raise DjProtocolError(f"Socket error: {exc}") from exc

        if not data:
            raise DjProtocolError("Socket closed by DJ")

        if in_header:
            # Header-Block: bis zur Leerzeile verwerfen.
            if b"\r\n\r\n" in data:
                data = data.split(b"\r\n\r\n", 1)[1]
                in_header = False
            else:
                continue
            if not data:
                continue

        if not got_audio:
            got_audio = True
            dj_manager.mark_live(session, True)
            deadline = time.time() + 3600.0

        # In die Queue legen. Bei voller Queue verwerfen wir den
        # aeltesten Chunk -- Audio darf nicht rueckstandig werden.
        try:
            session.audio_queue.put_nowait(data)
        except queue.Full:
            try:
                session.audio_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                session.audio_queue.put_nowait(data)
            except queue.Full:
                pass
