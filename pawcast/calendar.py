"""Parser fuer dj_calendar.xml (Transcast-Kalender).

Das Format der Kalender-Datei::

    <?xml version="1.0" encoding="UTF-8" ?>
    <eventlist>
        <event type="dj">
            <dj archive="inherit" priority="9">admin</dj>
            <calendar />
        </event>
        <event type="playlist">
            <calendar starttime="00:00:00" repeat="128" />
            <playlist priority="1" shuffle="inherit" loopatend="1">main</playlist>
        </event>
    </eventlist>

Zusaetzlich unterstuetzt das Original ``<event type="relay">`` mit einem
``<relay>``-Untertag -- das wird hier ebenfalls geparst, damit ein Relay
konfigurierbar ist.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import List, Optional
from xml.etree import ElementTree

log = logging.getLogger("pawcast.calendar")


@dataclass
class DjEvent:
    """Ein DJ-Event: wer darf senden, mit welcher Prioritaet."""

    name: str
    priority: int = 8
    archive: str = "inherit"
    #: Slot-Nummer im Kalender (fuer die Reihenfolge).
    event_id: int = 0


@dataclass
class PlaylistEvent:
    """Ein Playlist-Event: wann welche Playlist laeuft."""

    playlist: str
    priority: int = 1
    shuffle: str = "inherit"
    loopatend: bool = True
    starttime: str = "00:00:00"
    repeat: int = 0
    event_id: int = 0


@dataclass
class RelayEvent:
    """Ein Relay-Event: ein externer Stream wird durchgereicht."""

    url: str
    priority: int = 8
    starttime: str = "00:00:00"
    repeat: int = 0
    event_id: int = 0


@dataclass
class Calendar:
    """Der geparste Kalender."""

    djs: List[DjEvent] = field(default_factory=list)
    playlists: List[PlaylistEvent] = field(default_factory=list)
    relays: List[RelayEvent] = field(default_factory=list)

    def dj_names(self) -> List[str]:
        return [d.name for d in self.djs]

    def dj(self, name: str) -> Optional[DjEvent]:
        for d in self.djs:
            if d.name == name:
                return d
        return None

    def priority_of(self, name: str) -> Optional[int]:
        ev = self.dj(name)
        return ev.priority if ev else None


def _parse_time(value: str) -> str:
    """Normalisiert eine Zeitangabe auf HH:MM:SS."""
    if not value:
        return "00:00:00"
    parts = value.split(":")
    while len(parts) < 3:
        parts.append("00")
    return ":".join(p.zfill(2) for p in parts[:3])


def parse(path: str) -> Calendar:
    """Liest und parst eine dj_calendar.xml."""
    cal = Calendar()

    if not os.path.exists(path):
        log.warning("Kalenderdatei nicht gefunden: %s", path)
        return cal

    try:
        tree = ElementTree.parse(path)
    except ElementTree.ParseError as exc:
        log.error("Kalenderdatei kaputt (%s): %s", path, exc)
        return cal

    root = tree.getroot()
    event_id = 0

    for event in root.findall("event"):
        event_id += 1
        etype = (event.get("type") or "").lower()

        if etype == "dj":
            for dj in event.findall("dj"):
                if dj is None or not (dj.text or "").strip():
                    continue
                cal.djs.append(DjEvent(
                    name=(dj.text or "").strip(),
                    priority=int(dj.get("priority") or 8),
                    archive=dj.get("archive") or "inherit",
                    event_id=event_id,
                ))

        elif etype == "playlist":
            cal_el = event.find("calendar")
            starttime = _parse_time(cal_el.get("starttime") if cal_el is not None else "")
            repeat = int(cal_el.get("repeat") or 0) if cal_el is not None else 0
            for pl in event.findall("playlist"):
                if pl is None or not (pl.text or "").strip():
                    continue
                cal.playlists.append(PlaylistEvent(
                    playlist=(pl.text or "").strip(),
                    priority=int(pl.get("priority") or 1),
                    shuffle=pl.get("shuffle") or "inherit",
                    loopatend=(pl.get("loopatend") or "1") not in ("0", "false", "no"),
                    starttime=starttime,
                    repeat=repeat,
                    event_id=event_id,
                ))

        elif etype == "relay":
            cal_el = event.find("calendar")
            starttime = _parse_time(cal_el.get("starttime") if cal_el is not None else "")
            repeat = int(cal_el.get("repeat") or 0) if cal_el is not None else 0
            for relay in event.findall("relay"):
                if relay is None:
                    continue
                url = (relay.get("url") or "").strip()
                if not url:
                    log.warning("Relay-Eintrag ohne URL uebersprungen")
                    continue
                cal.relays.append(RelayEvent(
                    url=url,
                    priority=int(relay.get("priority") or 8),
                    starttime=starttime,
                    repeat=repeat,
                    event_id=event_id,
                ))

    log.info(
        "Kalender geladen: %d DJs, %d Playlists, %d Relays",
        len(cal.djs), len(cal.playlists), len(cal.relays),
    )
    return cal


def write(cal: Calendar, path: str) -> None:
    """Schreibt den Kalender im Original-Format.
    """
    lines = ['<?xml version="1.0" encoding="UTF-8" ?>', "<eventlist>"]

    for dj in cal.djs:
        lines.append('\t<event type="dj">')
        lines.append(f'\t\t<dj archive="{dj.archive}" priority="{dj.priority}">{dj.name}</dj>')
        lines.append("\t\t<calendar />")
        lines.append("\t</event>")

    for pl in cal.playlists:
        lines.append('\t<event type="playlist">')
        lines.append(f'\t\t<calendar starttime="{pl.starttime}" repeat="{pl.repeat}" />')
        loopat = "1" if pl.loopatend else "0"
        lines.append(
            f'\t\t<playlist priority="{pl.priority}" shuffle="{pl.shuffle}" '
            f'loopatend="{loopat}">{pl.playlist}</playlist>'
        )
        lines.append("\t</event>")

    for relay in cal.relays:
        lines.append('\t<event type="relay">')
        lines.append(f'\t\t<calendar starttime="{relay.starttime}" repeat="{relay.repeat}" />')
        lines.append(f'\t\t<relay priority="{relay.priority}" url="{relay.url}" />')
        lines.append("\t</event>")

    lines.append("</eventlist>")

    tmp = f"{path}.tmp"
    # BOM! Das Original schreibt ein UTF-8-BOM vor die XML-Deklaration
    # (verifiziert am 11.11.2026 gegen die Original-Ausgabe von streamd.exe).
    with open(tmp, "w", encoding="utf-8-sig") as fh:
        fh.write("\n".join(lines) + "\n")
    os.replace(tmp, path)
