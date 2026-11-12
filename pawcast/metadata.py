"""ICY-Metadaten (StreamTitle) fuer den DNAS.

Das Original injiziert die Metadaten als ICY-Block zwischen den MP3-Frames
(``icy-metaint``). Die Log-Ausgaben:

    [SHOUTCASTMETADATA] Metadata string [<artist> - <title>]
    [SHOUTCASTMETADATA] Sending metadata
    [SHOUTCASTMETADATA] Empty url string
    [SHOUTCASTMETADATA] Metadata response read timeout
    [SHOUTCASTMETADATA] Dropped stale metadata
    [SHOUTCASTMETADATA] waitToSend - sendTitle Could not connect to
                            SHOUTcast server

Der Original-Transcaster schickt die Metadaten **zusaetzlich** ueber den
Admin-Kanal (``GET /admin.cgi?pass=***<adminpw>&mode=updinfo&song=...``) --
der DNAS uebernimmt sie dann in die Stream-Seite. Das ist wichtig, weil der
DNAS die ICY-Metadaten des Sources uebernimmt, aber der SHOUTcast-Weblet-
Status sie erst anzeigt, wenn der Admin sie gesetzt hat.

⭐ Das **Metadaten-XML** (12.11.2026 am Original-Binary verifiziert)
---------------------------------------------------------------------
Der Original-Transcaster erzeugt ein XML-Dokument und liefert es ueber
seinen Weblet-Server (Port ``adminport``) aus. Die Strings dafuer stehen
woertlich im Binary (``/usr/share/transcast/sc_trans``):

    <?xml version="1.0" encoding="UTF-8" ?>
    <metadata>
    <song>
    <album>   </album>
    <artist>  </artist>
    <name>    </name>
    </song>
    </metadata>

Die Feldnamen werden ueber XPath-artige Pfade adressiert:

    /metadata/song/artist
    /metadata/song/album
    /metadata/song/name

Das ist das Format, das alte Webseiten konsumieren, die den pawcast
benutzen -- sie holen sich das XML vom Weblet-Port und lesen daraus
Artist/Album/Titel.

WICHTIG: Der Original schickt an den DNAS **ausschliesslich**
``&mode=updinfo&song=<artist - title>``. Es gibt keinen ``xml=``-Parameter
im pawcast. Das XML wird **serviert**, nicht gesendet.
"""

from __future__ import annotations

import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Optional
from xml.sax.saxutils import escape

log = logging.getLogger("pawcast.metadata")


#: Der User-Agent fuer Metadaten-Updates.
#: " HTTP/1.0\\r\\nUser-Agent:TRANScast Title Update(Mozilla)\\r\\n\\r\\n"
#: Beachte: kein Leerzeichen nach "User-Agent:".
METADATA_USER_AGENT = "TRANScast Title Update(Mozilla)"


#: Die XML-Deklaration des Originals (woertlich aus dem Binary).
XML_DECLARATION = '<?xml version="1.0" encoding="UTF-8" ?>'


@dataclass
class CurrentMetadata:
    """Der aktuell laufende Titel samt den Feldern des Original-XMLs.

    Das Original fuehrt ``name`` (Titel), ``artist``, ``album``,
    ``comment``, ``genre`` und ``year`` als Felder. ``name`` ist der
    Dateiname ohne Endung bzw. der ID3-Titel.
    """

    artist: str = ""
    title: str = ""
    album: str = ""
    comment: str = ""
    genre: str = ""
    year: str = ""

    def to_xml(self) -> str:
        """Erzeugt das Metadaten-XML **exakt** in der Form des Originals.

        Die Einrueckung (Tabs) und die Tag-Reihenfolge stammen aus dem
        Original-Binary (Region ``0x4d8eb0`` in ``sc_trans``).
        """
        lines = [
            XML_DECLARATION,
            "<metadata>",
            "\t<length>0</length>",
            "\t<song>",
            "\t\t<name>%s</name>" % escape(self.title),
            "\t\t<artist>%s</artist>" % escape(self.artist),
            "\t\t<album>%s</album>" % escape(self.album),
            "\t\t<comment>%s</comment>" % escape(self.comment),
            "\t\t<genre>%s</genre>" % escape(self.genre),
            "\t\t<year>%s</year>" % escape(self.year),
            "\t</song>",
            "</metadata>",
        ]
        return "\n".join(lines)

    def field_value(self, field_name: str) -> str:
        """Der Wert eines XPath-Feldes (``artist``/``album``/``name``)."""
        return {
            "artist": self.artist,
            "album": self.album,
            "name": self.title,
        }.get(field_name, "")


class MetadataSender:
    """Setzt den aktuellen Titel beim DNAS (Admin-Kanal).

    Das Original nutzt dafuer eine **eigene** TCP-Verbindung -- nicht die
    Source-Verbindung. Der Request lautet exakt::

        GET /admin.cgi?pass=***<adminpw>&mode=updinfo&song=<artist - title> HTTP/1.0
        User-Agent:TRANScast Title Update(Mozilla)

    Es wird **kein** ``mount=``-Parameter gesendet und **kein** ``xml=``-
    Parameter -- der Original kennt nur den ``song=``-Pfad.
    """

    def __init__(self, host: str, port: int, admin_password: str,
                 timeout: float = 5.0):
        self.host = host
        self.port = port
        self.admin_password = admin_password
        self.timeout = timeout
        #: Der aktuelle Stand -- fuer die Weblet-XML-Ausgabe.
        self.current = CurrentMetadata()

    def _url_pass(self, **params) -> str:
        """Baut die Admin-URL mit dem Original-Parameter ``pass=``.

        ``pass`` ist ein Python-Keyword, deshalb kann es nicht direkt
        als Keyword-Argument uebergeben werden. Diese Variante setzt
        den Parameter-Text direkt -- das ist der Weg, den das Original
        nimmt.
        """
        query = urllib.parse.urlencode(params)
        return (f"http://{self.host}:{self.port}/admin.cgi"
                f"?pass={self.admin_password}&{query}")

    def _build_request(self, song: str) -> bytes:
        """Baut den Request exakt in der Form des Originals."""
        query = urllib.parse.urlencode({
            "pass": self.admin_password,
            "mode": "updinfo",
            "song": song,
        })
        lines = [
            f"GET /admin.cgi?{query} HTTP/1.0",
            f"User-Agent:{METADATA_USER_AGENT}",
            "",
            "",
        ]
        return "\r\n".join(lines).encode("utf-8", "replace")

    def send_title(self, artist: str, title: str) -> bool:
        """Setzt den StreamTitle beim DNAS. Gibt True zurueck bei Erfolg."""
        if not artist and not title:
            log.debug("[SHOUTCASTMETADATA] Empty url string")
            return False

        song = f"{artist} - {title}".strip(" -")

        try:
            with urllib.request.urlopen(
                urllib.request.Request(
                    self._url_pass(mode="updinfo", song=song),
                    headers={"User-Agent": METADATA_USER_AGENT},
                ),
                timeout=self.timeout,
            ) as resp:
                body = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            log.warning("[SHOUTCASTMETADATA] DNAS Admin antwortete HTTP %s", exc.code)
            return False
        except (urllib.error.URLError, OSError) as exc:
            log.warning(
                "[SHOUTCASTMETADATA] waitToSend - sendTitle Could not connect to "
                "SHOUTcast server: %s", exc,
            )
            return False

        if "OK" in body.upper() or not body.strip():
            log.info("[SHOUTCASTMETADATA] Metadata string [%s]", song)
            log.info("[SHOUTCASTMETADATA] Sending metadata")
            return True

        log.warning("[SHOUTCASTMETADATA] DNAS Admin-Antwort unerwartet: %s",
                    body.strip()[:200])
        return False

    def set_current(self, artist: str, title: str, album: str = "",
                    comment: str = "", genre: str = "", year: str = "") -> None:
        """Merkt sich den aktuellen Stand fuer die XML-Ausgabe."""
        self.current = CurrentMetadata(
            artist=artist or "",
            title=title or "",
            album=album or "",
            comment=comment or "",
            genre=genre or "",
            year=year or "",
        )

    def metadata_xml(self) -> str:
        """Das Metadaten-XML in der Form des Originals."""
        return self.current.to_xml()


class IcyMetadataInjector:
    """Injiziert ICY-Metadaten in einen MP3-Stream.

    Das Standardverfahren: alle ``metaint`` Bytes Audio folgen ein Byte
    Laenge (L). Ist L > 0, folgen L Bytes Metadaten
    (``StreamTitle='...';``), aufgefuellt mit Nullen.

    ⭐ Die Laengen-Semantik (12.11.2026 am DNAS 2.5.5 verifiziert)
    ----------------------------------------------------------------
    Der **sc_serv 2.5.5 zaehlt die Laenge in 16-Byte-Bloecken**, nicht
    in Bytes. Ein Laengenbyte von 4 bedeutet dort 64 Bytes.

    Belegt am laufenden Stream:

        b"StreamTitle='Alanis Morissette - Thank U (Official Video)';"
        + 5 Fuellnullen = 64 Bytes = 4 * 16

    Bei der ueblichen Byte-Semantik waere der Block 59 Bytes lang --
    der DNAS haette dann 59 * 16 = 944 Bytes gelesen und den Stream
    desynchronisiert.

    Deshalb gibt es zwei Modi:

    * ``length_mode="dnas"`` (Default) -- Laenge in 16-Byte-Bloecken.
      Richtig fuer den sc_serv 2.5.5.
    * ``length_mode="bytes"`` -- Laenge in Bytes. Das ist der
      SHOUTcast-Standard und wird von aelteren DNAS-Versionen und
      Icecast erwartet.

    WICHTIG: Dieser Injector wird im Source-Pfad **nicht** benutzt.
    Das Original setzt die Metadaten ueber den Admin-Kanal
    (``admin.cgi?mode=updinfo``) -- siehe ``MetadataSender``. Der
    Injector ist damit Reserve fuer Clients, die den Titel direkt
    inline setzen wollen.
    """

    #: Die vom DNAS 2.5.5 verwendete Blockgroesse.
    DNAS_BLOCK = 16

    def __init__(self, metaint: int = 8192, length_mode: str = "dnas"):
        self.metaint = metaint
        if length_mode not in ("dnas", "bytes"):
            raise ValueError(
                "length_mode muss 'dnas' oder 'bytes' sein, nicht %r"
                % length_mode)
        self.length_mode = length_mode
        self._counter = 0
        self._pending = b""
        self.title = ""

    def set_title(self, artist: str, title: str) -> None:
        self.title = f"{artist} - {title}".strip(" -")

    def _metadata_block(self) -> bytes:
        payload = f"StreamTitle='{self.title}';".encode("utf-8", "replace")
        if self.length_mode == "dnas":
            # Laenge in 16-Byte-Bloecken, auf das Blockraster aufgefüllt.
            blocks = max(1, -(-len(payload) // self.DNAS_BLOCK))
            length = blocks
            pad = blocks * self.DNAS_BLOCK - len(payload)
        else:
            # Standard: Laenge in Bytes, auf 16 Byte aufgefuellt.
            length = len(payload)
            if length > 255:
                payload = payload[:255]
                length = len(payload)
            pad = (-length) % self.DNAS_BLOCK
        block = bytes([length]) + payload + (b"\x00" * pad)
        return block

    def inject(self, data: bytes) -> bytes:
        """Schiebt Metadaten in den Datenstrom."""
        if self.metaint <= 0:
            return data

        out = bytearray()
        buf = self._pending + data
        pos = 0

        while pos < len(buf):
            remaining = self.metaint - self._counter
            take = min(remaining, len(buf) - pos)
            out += buf[pos:pos + take]
            pos += take
            self._counter += take

            if self._counter >= self.metaint:
                out += self._metadata_block()
                self._counter = 0

        self._pending = b""
        return bytes(out)
