"""Source-Client: Anmeldung am SHOUTcast-DNAS (sc_serv).

**Der Source-Login** besteht aus zwei Teilen:

1. Der GET-Request mit Pfad::

       GET %s HTTP/1.0\\r\\n
       Host: %s\\r\\n
       User-Agent: %s\\r\\n
       Accept: */*\\r\\n
       Icy-MetaData:1\\r\\n
       Connection: close\\r\\n\\r\\n

2. Der ICY-Header-Block (exakte Reihenfolge)::

       icy-name: <name>\\r\\n
       icy-genre: <genre>\\r\\n
       icy-url: <url>\\r\\n
       icy-irc: <irc>\\r\\n
       icy-icq: <icq>\\r\\n
       icy-aim: <aim>\\r\\n
       icy-pub: <0|1>\\r\\n
       content-type:audio/<codec>\\r\\n
       icy-prebuffer: <ms>\\r\\n
       icy-br: <bitrate>\\r\\n
       \\r\\n

   Achtung: ``content-type:audio/`` steht **zwischen** ``icy-pub:`` und
   ``icy-prebuffer:``.

Die Zustandsmaschine::

    EMPTY(0) -> SENDPASS(1) -> SENDMSG(2) -> SENDSTREAM(4)
             -> GETREPLY(5) -> SENDINFO(6)

Die Log-Ausgaben::

    [STREAM] Creating stream socket
    [STREAM] Connected to host server
    [STREAM] Host server gave success (OK2)
    [STREAM] Stream to <host> established
"""

from __future__ import annotations

import logging
import socket
import time
from typing import Optional

log = logging.getLogger("pawcast.source")


class SourceError(Exception):
    """Die Anmeldung am DNAS schlug fehl."""


#: Der User-Agent, den der Original-Transcaster benutzt.
DEFAULT_USER_AGENT = "SHOUTcast Server"

#: Der Codec, fuer den der Original sendet (content-type:audio/).
DEFAULT_CONTENT_TYPE = "mpeg"


class ShoutcastSource:
    """Verbindet sich als Source bei einem SHOUTcast-DNAS an."""

    def __init__(
        self,
        host: str,
        port: int,
        password: str,
        name: str = "",
        genre: str = "",
        url: str = "",
        bitrate: int = 128000,
        public: bool = False,
        timeout: float = 15.0,
        user_agent: str = DEFAULT_USER_AGENT,
        content_type: str = DEFAULT_CONTENT_TYPE,
        prebuffer: int = 64000,
        irc: str = "N/A",
        icq: str = "N/A",
        aim: str = "N/A",
    ):
        self.host = host
        self.port = port
        self.password = password
        self.name = name
        self.genre = genre
        self.url = url
        self.bitrate = bitrate
        self.public = public
        self.timeout = timeout
        self.user_agent = user_agent
        self.content_type = content_type
        self.prebuffer = prebuffer
        self.irc = irc
        self.icq = icq
        self.aim = aim
        self._sock: Optional[socket.socket] = None
        self._buffer = b""

    # ------------------------------------------------------------------
    # Request-Bau
    # ------------------------------------------------------------------
    def _build_pass(self) -> bytes:
        """Schritt 1: nur das Passwort, mit CRLF.

        Verifiziert gegen einen laufenden sc_serv
        (MITM-Capture auf dnas0, Port 8100). Das Original schickt
        **keinen** HTTP-Request -- nur:

            b'my_src_pass\\r\\n'

        Antwort des DNAS:

            b'OK2\\r\\nyc-caps:11\\r\\n\\r\\n'

        Die Vorlage "GET %s HTTP/1.0..." ist die Vorlage fuer den
        *Admin-/Metadaten*-Kanal, nicht fuer den Source-Login.
        """
        return f"{self.password}\r\n".encode("utf-8", "replace")

    def _build_info(self) -> bytes:
        """Der ICY-Header-Block -- exakte Reihenfolge.

        Die Reihenfolge der ``operator<<``-
        Aufrufe. ``icy-pub``, ``icy-prebuffer`` und ``icy-br`` sind
        Zahlen.
        """
        # Der sc_serv lehnt die Quelle ab, wenn icy-name leer ist:
        #   "Bad icy header string [icy-name:]"
        # In dem Fall wird "N/A" geschickt.
        name = self.name.strip() or "N/A"
        genre = self.genre.strip() or "N/A"
        url = self.url.strip() or "N/A"

        lines = [
            f"icy-name:{name}",
            f"icy-genre:{genre}",
            f"icy-url:{url}",
            f"icy-irc:{self.irc}",
            f"icy-icq:{self.icq}",
            f"icy-aim:{self.aim}",
            f"icy-pub:{1 if self.public else 0}",
            f"content-type:audio/{self.content_type}",
            f"icy-prebuffer:{self.prebuffer}",
            f"icy-br:{self.bitrate // 1000}",
            "",
            "",
        ]
        return "\r\n".join(lines).encode("utf-8", "replace")

    # ------------------------------------------------------------------
    # Verbindungsaufbau
    # ------------------------------------------------------------------
    def connect(self, retries: int = 3, retry_delay: float = 5.0) -> None:
        """Verbindet und meldet sich an. Wirft ``SourceError`` bei Fehlschlag."""
        last_error = None

        for attempt in range(1, retries + 1):
            try:
                self._connect_once()
                return
            except SourceError as exc:
                last_error = exc
                log.warning(
                    "[STREAM] Error connecting to stream host [waiting %ds] (%d/%d): %s",
                    int(retry_delay), attempt, retries, exc,
                )
                if attempt < retries:
                    time.sleep(retry_delay)

        raise SourceError(f"Keine Verbindung zum DNAS {self.host}:{self.port}: {last_error}")

    def _connect_once(self) -> None:
        sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        sock.settimeout(self.timeout)

        log.info("[STREAM] Creating stream socket -> %s:%d (bitrate=%d)",
                 self.host, self.port, self.bitrate)

        # --- Schritt 1: nur das Passwort schicken ---
        sock.sendall(self._build_pass())
        log.debug("[STREAM] Passwort gesendet (%d Bytes)", len(self._build_pass()))

        # --- Schritt 2: Antwort lesen (erwartet: "OK2\r\nicy-caps:11\r\n\r\n") ---
        header = b""
        while b"\r\n\r\n" not in header:
            try:
                chunk = sock.recv(1)
            except socket.timeout as exc:
                sock.close()
                raise SourceError("Timeout beim Warten auf die DNAS-Antwort") from exc
            if not chunk:
                sock.close()
                raise SourceError("DNAS hat die Verbindung vor der Antwort geschlossen")
            header += chunk
            if len(header) > 8192:
                sock.close()
                raise SourceError("Antwort-Header vom DNAS zu gross")

        head, _, rest = header.partition(b"\r\n\r\n")
        text = head.decode("utf-8", "replace")
        first_line = text.splitlines()[0] if text else ""
        log.info("[STREAM] Got response from server[%s]", first_line)

        lowered = text.lower()
        if "invalid password" in lowered or "stream in use" in lowered:
            sock.close()
            raise SourceError(f"DNAS hat die Anmeldung abgelehnt: {first_line}")

        if "ok2" not in lowered:
            sock.close()
            raise SourceError(f"Unerwartete DNAS-Antwort: {first_line or '(leer)'}")

        for line in text.splitlines():
            if line.lower().startswith("icy-caps:"):
                log.info("[STREAM] Server meldet %s", line.strip())

        # --- Schritt 3: die Stream-Informationen schicken (SENDINFO) ---
        sock.sendall(self._build_info())
        log.info("[STREAM] Sending stream information")

        self._sock = sock
        self._buffer = rest
        log.info("[STREAM] Host server gave success (OK2)")
        log.info("[STREAM] Stream to %s established", self.host)

    # ------------------------------------------------------------------
    # Daten senden
    # ------------------------------------------------------------------
    def send(self, data: bytes) -> int:
        """Sendet Audio-Daten an den DNAS. Gibt die gesendete Menge zurueck."""
        if self._sock is None:
            raise SourceError("Nicht verbunden")
        try:
            self._sock.sendall(data)
            return len(data)
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise SourceError("DNAS hat die Verbindung getrennt") from exc
        except socket.timeout as exc:
            raise SourceError("Timeout beim Senden an den DNAS") from exc

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
            log.info("[STREAM] Disconnected")

    @property
    def connected(self) -> bool:
        return self._sock is not None
