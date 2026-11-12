"""Liest den Live-Status des SHOUTcast-DNAS.

Das pawCast-WebPanel sieht aus wie die DNAS-Admin-Seite -- dann soll es
auch die *echten* Stream-Daten zeigen: Zuhuererzahl, Peak, Avg. Play
Time, "Playing Next" und den Listing-Status.

Der DNAS liefert diese Angaben in seiner HTML-Statusseite
(``index.html``). Die Seite ist **ohne** Admin-Passwort abrufbar -- sie
zeigt genau die Oeffentlichen Stream-Informationen.

Verifiziert an beiden Instanzen:
  * dnas0   10.232.136.119:8000  (DNAS v2.5.5.733, posix/linux x64)
  * alt     45.83.107.188:8000    (DNAS v2.6.0.750, win64)

Beide liefern dieselben Feldnamen; 2.5.5 kennt allerdings kein
"Playing Next" -- das bleibt dann leer.
"""

from __future__ import annotations

import html as html_mod
import re
import urllib.request
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class DnasStatus:
    """Der ausgelesene Stream-Status eines DNAS."""

    ok: bool = False
    error: str = ""
    #: "Stream is up (MP3 @ 192 kbps, 44.1 kHz) with 0 of 50 listeners"
    stream_status: str = ""
    listing_status: str = ""
    stream_name: str = ""
    genre: str = ""
    website: str = ""
    now_playing: str = ""
    next_playing: str = ""
    listener_peak: str = ""
    avg_play_time: str = ""
    listeners: Optional[int] = None
    max_listeners: Optional[int] = None
    bitrate: Optional[int] = None
    samplerate: Optional[str] = None
    codec: str = ""
    raw: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        d = asdict(self)
        return d


def _text(fragment: str) -> str:
    """HTML-Fragment -> reiner Text."""
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", fragment)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = html_mod.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def _rows(page: str):
    """Liefert (Label, Wert, href) je Tabellenzeile der DNAS-Statusseite."""
    for m in re.finditer(r"(?is)<tr[^>]*>(.*?)</tr>", page):
        cells = re.findall(r"(?is)<td[^>]*>(.*?)</td>", m.group(1))
        if len(cells) >= 2:
            label = _text(cells[0]).rstrip(":").strip()
            href = ""
            hm = re.search(r'(?is)<a[^>]*href="([^"]+)"', cells[1])
            if hm:
                href = html_mod.unescape(hm.group(1)).strip()
            yield label, _text(cells[1]), href


def _to_int(value: str) -> Optional[int]:
    m = re.search(r"\d+", value or "")
    return int(m.group(0)) if m else None


def parse_status(page: str) -> DnasStatus:
    """Parst die HTML-Statusseite des DNAS."""
    st = DnasStatus(ok=True)

    for label, value, href in _rows(page):
        st.raw[label] = value
        if label == "Listing Status":
            st.listing_status = value
        elif label == "Stream Status":
            st.stream_status = value
        elif label == "Listener Peak":
            st.listener_peak = value
        elif label == "Avg. Play Time":
            st.avg_play_time = value
        elif label == "Stream Name":
            st.stream_name = value
        elif label in ("Stream Genre(s)", "Stream Genre"):
            st.genre = value
        elif label == "Stream Website":
            # Der Link ist die eigentliche Angabe -- der Text ist nur
            # die Kurzform ohne Schema.
            st.website = href or value
        elif label == "Playing Now":
            st.now_playing = value
        elif label == "Playing Next":
            st.next_playing = value

    # "Stream is up (MP3 @ 192 kbps, 44.1 kHz) with 3 of 50 listeners"
    m = re.search(r"with\s+(\d+)\s+of\s+(\d+)\s+listeners", st.stream_status)
    if m:
        st.listeners = int(m.group(1))
        st.max_listeners = int(m.group(2))
    else:
        # Aelterer DNAS nennt die Zahl nur in "Listener Peak".
        st.listeners = _to_int(st.listener_peak) if st.listener_peak else None

    m = re.search(r"\(([A-Za-z0-9]+)\s*@\s*(\d+)\s*kbps(?:,\s*([\d.]+)\s*kHz)?",
                  st.stream_status)
    if m:
        st.codec = m.group(1)
        st.bitrate = int(m.group(2))
        st.samplerate = m.group(3)

    # Ohne erkennbare Statusangaben ist die Seite nicht auswertbar --
    # dann lieber ein Fehler als leere, aber "erfolgreiche" Daten.
    if not st.stream_status and not st.stream_name:
        return DnasStatus(ok=False, error="Statusseite nicht auswertbar")

    return st


def fetch_status(host: str, port: int = 8000, timeout: float = 5.0) -> DnasStatus:
    """Holt die Statusseite des DNAS und parst sie."""
    url = "http://%s:%d/index.html" % (host, port)
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "SHOUTcast DNAS/1.0",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            page = resp.read().decode("utf-8", "replace")
    except Exception as exc:  # pragma: no cover - Netzwerkfehler
        return DnasStatus(ok=False, error=str(exc))

    if "<html" not in page.lower():
        return DnasStatus(ok=False, error="unerwartete Antwort vom DNAS")

    st = parse_status(page)
    if not st.stream_status and not st.stream_name:
        return DnasStatus(ok=False, error="Statusseite nicht auswertbar")
    return st
