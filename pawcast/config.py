"""Parser fuer sc_trans.conf (kompatibel zum Original sc_trans 2.0.0.54).

Das Original-Format ist simpel: eine Option pro Zeile als ``key=value``.
Zusaetzlich gibt es ``include=<pfad>`` zum Einbinden weiterer Dateien
(z. B. die DJ-Logins) sowie Kommentare mit ``#``.

WICHTIG (verifiziert am 11.11.2026): ``sc_trans`` 2.0.0.54 strippt KEINE
Inline-Kommentare. Ein ``passwort=geheim # Kommentar`` liefert den Wert
``geheim # Kommentar`` -- der Encoder stirbt dann mit "Invalid Password".
Kommentare muessen deshalb IMMER in einer eigenen Zeile stehen.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

log = logging.getLogger("pawcast.config")

#: Ein Kommentar in einer eigenen Zeile.
_COMMENT_LINE = re.compile(r"^\s*#")

#: ``include=`` -- unterstuetzt die Original-Form (eine Datei pro Zeile).
_INCLUDE = "include"


@dataclass
class OutEndpoint:
    """Ein Ausgabe-Endpoint (wohin der Transcaster sendet)."""

    index: int
    protocol: int = 1
    serverip: str = "localhost"
    serverport: int = 8000
    password: str = ""
    encoder: str = "mp3"
    bitrate: int = 128000
    samplerate: int = 44100
    channels: int = 2

    @property
    def bitrate_kbps(self) -> int:
        return self.bitrate // 1000


@dataclass
class DjLogin:
    """Ein DJ-Account (aus djlogin_N/djpassword_N/djpriority_N)."""

    index: int
    login: str
    password: str
    priority: int = 8
    enabled: bool = True


@dataclass
class TranscastConfig:
    """Die geparste sc_trans.conf."""

    # --- Allgemein ---
    logfile: str = ""
    log: bool = True
    screenlog: bool = False
    logdata: bool = True
    pidfile: str = ""

    # --- Metadaten ---
    streamtitle: str = ""
    streamurl: str = ""
    genre: str = ""
    usemetadata: bool = True
    nextlookahead: int = 1
    displaymetadatapattern: str = "%R[ - ]%N"

    # --- Playlist ---
    playlistfile: str = ""
    xfade: int = 2
    shuffle: bool = False
    applyreplaygain: bool = False
    calculatereplaygain: bool = False

    # --- Kalender ---
    calendarfile: str = ""

    # --- DJ ---
    djport: int = 9020
    djport2: int = 0
    djcapture: int = 0
    djalways: int = 0
    djlogin: str = ""
    djpassword: str = ""

    @property
    def microserver_port(self) -> int:
        """Der Microserver-Port: ``djport + 1``.

        Der Original-Transcaster oeffnet neben dem DJ-Port immer noch
        einen zweiten Listener auf ``portbase + 1`` und loggt da
        "[MICROSERVER] Listening for connection on port ...".
        Empirisch bestaetigt an einer Original-Instanz mit
        ``djport=19020``: Listener auf 19020 **und** 19021.
        Der Microserver spricht dasselbe DJ-Protokoll.
        """
        return self.djport + 1

    # --- Admin-Weblet ---
    adminport: int = 7999
    adminuser: str = "admin"
    adminpassword: str = ""

    # --- Encoder-Lizenz (Original: Fraunhofer) ---
    unlockkeyname: str = ""
    unlockkeycode: str = ""

    # --- Endpunkte ---
    endpoints: List[OutEndpoint] = field(default_factory=list)

    # --- DJ-Accounts ---
    djs: List[DjLogin] = field(default_factory=list)

    #: Alle Keys, die wir nicht kennen (fuer Diagnose).
    unknown: Dict[str, str] = field(default_factory=dict)

    def endpoint(self, index: int = 1) -> Optional[OutEndpoint]:
        for ep in self.endpoints:
            if ep.index == index:
                return ep
        return None

    def dj(self, login: str) -> Optional[DjLogin]:
        for d in self.djs:
            if d.login == login:
                return d
        return None


def _as_bool(value: str) -> bool:
    """'1', 'yes', 'true' und 'on' sind wahr."""
    return value.strip().lower() in ("1", "yes", "true", "on")


def _as_int(value: str, default: int = 0) -> int:
    try:
        return int(value.strip())
    except (TypeError, ValueError):
        return default


def _parse_line(key: str, value: str, cfg: TranscastConfig) -> None:
    """Ordnet ein Key/Value-Paar dem Config-Objekt zu."""
    key = key.strip().lower()
    value = value.strip()

    # --- Endpunkt-spezifische Keys (outprotocol_1, password_1, ...) ---
    m = re.match(r"^(outprotocol|serverip|serverport|password|encoder|bitrate|samplerate|channels)_(\d+)$", key)
    if m:
        field_name, idx = m.group(1), int(m.group(2))
        ep = cfg.endpoint(idx)
        if ep is None:
            ep = OutEndpoint(index=idx)
            cfg.endpoints.append(ep)
        setattr(ep, field_name, {
            "outprotocol": int, "serverport": int, "bitrate": int,
            "samplerate": int, "channels": int,
        }.get(field_name, str)(value))
        return

    # --- DJ-Login-spezifische Keys (djlogin_1, djpassword_1, ...) ---
    m = re.match(r"^dj(login|password|priority)_(\d+)$", key)
    if m:
        what, idx = m.group(1), int(m.group(2))
        dj = next((d for d in cfg.djs if d.index == idx), None)
        if dj is None:
            dj = DjLogin(index=idx, login="", password="")
            cfg.djs.append(dj)
        if what == "login":
            dj.login = value
        elif what == "password":
            dj.password = value
        else:
            dj.priority = _as_int(value, 8)
        return

    # --- Bekannte globale Keys ---
    simple = {
        "logfile": str, "screenlog": _as_bool, "log": _as_bool,
        "logdata": _as_bool, "pidfile": str,
        "streamtitle": str, "streamurl": str, "genre": str,
        "usemetadata": _as_bool, "nextlookahead": int,
        "displaymetadatapattern": str,
        "playlistfile": str, "xfade": int, "shuffle": _as_bool,
        "applyreplaygain": _as_bool, "calculatereplaygain": _as_bool,
        "calendarfile": str,
        "djport": int, "djport2": int, "djcapture": int, "djalways": int,
        "djlogin": str, "djpassword": str,
        "adminport": int, "adminuser": str, "adminpassword": str,
        "unlockkeyname": str, "unlockkeycode": str,
    }
    if key in simple:
        setattr(cfg, key, simple[key](value))
        return

    cfg.unknown[key] = value


def load(path: str, _seen: Optional[set] = None) -> TranscastConfig:
    """Laedt eine sc_trans.conf samt ``include=``-Dateien.

    Die Include-Reihenfolge entspricht der Original-Implementierung: die
    Includedatei wird sofort beim Encounter verarbeitet, danach geht es mit
    der aufrufenden Datei weiter. Somit koennen Includedateien Werte setzen,
    die spaeter in der Hauptdatei ueberschrieben werden -- und umgekehrt.
    """
    cfg = TranscastConfig()
    seen = _seen if _seen is not None else set()

    real = os.path.realpath(path)
    if real in seen:
        log.warning("Include-Schleife erkannt: %s uebersprungen", path)
        return cfg
    seen.add(real)

    if not os.path.exists(path):
        log.error("Config-Datei nicht gefunden: %s", path)
        return cfg

    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.rstrip("\n").rstrip("\r")
            if _COMMENT_LINE.match(line):
                continue
            if not line.strip():
                continue
            if "=" not in line:
                log.warning("%s:%d: ungueltige Zeile (kein '='): %s", path, lineno, line)
                continue

            key, _, value = line.partition("=")
            key = key.strip().lower()
            value = value.strip()

            if key == _INCLUDE:
                inc = os.path.expanduser(value)
                if not os.path.isabs(inc):
                    inc = os.path.join(os.path.dirname(os.path.abspath(path)), inc)
                if os.path.exists(inc):
                    log.debug("Include: %s", inc)
                    _load_into(cfg, inc, seen)
                else:
                    log.warning("Include nicht gefunden: %s", inc)
                continue

            _parse_line(key, value, cfg)

    return cfg


def _load_into(cfg: TranscastConfig, path: str, seen: set) -> None:
    """Hängt eine Includedatei an eine bestehende Config an."""
    real = os.path.realpath(path)
    if real in seen:
        log.warning("Include-Schleife erkannt: %s uebersprungen", path)
        return
    seen.add(real)

    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.rstrip("\n").rstrip("\r")
            if _COMMENT_LINE.match(line):
                continue
            if not line.strip() or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip().lower()
            value = value.strip()
            if key == _INCLUDE:
                inc = os.path.expanduser(value)
                if not os.path.isabs(inc):
                    inc = os.path.join(os.path.dirname(os.path.abspath(path)), inc)
                if os.path.exists(inc):
                    _load_into(cfg, inc, seen)
                continue
            _parse_line(key, value, cfg)


def save(cfg: TranscastConfig, path: str) -> None:
    """Schreibt die Config zurueck -- im Original-Format.

    Kommentare werden NICHT geschrieben (wie beim Original). Reihenfolge:
    erst die globalen Optionen, dann Endpunkte, dann DJ-Accounts.
    """
    lines: List[str] = []

    def add(key: str, value) -> None:
        if isinstance(value, bool):
            value = 1 if value else 0
        lines.append(f"{key}={value}")

    add("streamtitle", cfg.streamtitle)
    add("streamurl", cfg.streamurl)
    add("genre", cfg.genre)
    add("screenlog", cfg.screenlog)
    add("log", cfg.log)
    add("logfile", cfg.logfile)
    add("playlistfile", cfg.playlistfile)
    add("xfade", cfg.xfade)
    add("calendarfile", cfg.calendarfile)
    add("djport", cfg.djport)
    add("djport2", cfg.djport2)
    add("djcapture", cfg.djcapture)
    add("usemetadata", cfg.usemetadata)
    add("nextlookahead", cfg.nextlookahead)
    add("displaymetadatapattern", cfg.displaymetadatapattern)

    for ep in sorted(cfg.endpoints, key=lambda e: e.index):
        add(f"outprotocol_{ep.index}", ep.protocol)
        add(f"serverip_{ep.index}", ep.serverip)
        add(f"serverport_{ep.index}", ep.serverport)
        add(f"password_{ep.index}", ep.password)
        add(f"encoder_{ep.index}", ep.encoder)
        add(f"bitrate_{ep.index}", ep.bitrate)
        add(f"samplerate_{ep.index}", ep.samplerate)
        add(f"channels_{ep.index}", ep.channels)

    add("applyreplaygain", cfg.applyreplaygain)
    add("calculatereplaygain", cfg.calculatereplaygain)
    add("shuffle", cfg.shuffle)
    add("adminpassword", cfg.adminpassword)
    add("adminuser", cfg.adminuser)
    add("adminport", cfg.adminport)

    for dj in sorted(cfg.djs, key=lambda d: d.index):
        add(f"djlogin_{dj.index}", dj.login)
        add(f"djpassword_{dj.index}", dj.password)
        add(f"djpriority_{dj.index}", dj.priority)

    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    os.replace(tmp, path)


# ----------------------------------------------------------------------
# --genconf: Beispielkonfiguration erzeugen
# ----------------------------------------------------------------------

#: Pfade, die der Installer anlegt. Der Generator braucht sie, damit die
#: erzeugte Config sofort auf eine installierte Instanz passt.
DEFAULT_PATHS = {
    "logfile": "/var/log/pawcast/sc_trans.log",
    "playlistfile": "/var/lib/pawcast/playlists/main.lst",
    "calendarfile": "/var/lib/pawcast/calendar/dj_calendar.xml",
}

#: Der Config-Template mit Kommentaren. Entsteht aus dem Original-Format
#: -- die Kommentare MUESSEN in eigenen Zeilen stehen, sonst wird der
#: Wert Teil des Passworts (Original-Eigenheit, siehe docs/ARCHITEKTUR.md).
CONFIG_TEMPLATE = """\
# pawcast Konfiguration
# Erzeugt mit: python3 -m pawcast --genconf <datei>
#
# KOMMENTARE MUESSEN IN EIGENER ZEILE STEHEN (Original-Eigenheit).
# Ein "password=geheim # Kommentar" ergibt den Wert "geheim # Kommentar".

# --- Allgemein ---
log=1
screenlog=0
logfile={logfile}
logdata=1

# --- Stream-Metadaten ---
streamtitle=The Radio Fire and Ice
streamurl=http://the-radio-fire-and-ice.de/
genre=Misc
usemetadata=1
nextlookahead=1
displaymetadatapattern=%R[ - ]%N

# --- AutoDJ ---
playlistfile={playlistfile}
calendarfile={calendarfile}
xfade=2
shuffle=1
applyreplaygain=0
calculatereplaygain=0

# --- DJ-Server ---
djport=9020
djport2=0
djcapture=0
djalways=0

# --- Admin-Weblet ---
adminport=7999
adminuser=admin
adminpassword=CHANGE_ME

# --- Endpunkt 1: der DNAS ---
outprotocol_1=1
serverip_1=127.0.0.1
serverport_1=8000
password_1=CHANGE_ME
encoder_1=mp3
bitrate_1=128000
samplerate_1=44100
channels_1=2
"""

#: Die DJ-Logins liegen in einer eigenen Datei (Include) -- dann muss die
#: Haupt-Config nicht die Passwoerter enthalten.
DJLOGINS_TEMPLATE = """\
# DJ-Logins. Format: djlogin_N / djpassword_N / djpriority_N
# Passwoerter hier eintragen, danach chmod 600.
djlogin_1=admin
djpassword_1=CHANGE_ME
djpriority_1=9
"""


def genconf(config_path: str, djlogins_path: Optional[str] = None,
            force: bool = False) -> Tuple[str, Optional[str]]:
    """Erzeugt eine Beispielkonfiguration.

    Legt ``config_path`` und -- falls angegeben -- ``djlogins_path`` an.
    Bestehende Dateien werden **nicht** ueberschrieben, ausser ``force``
    ist gesetzt.

    Wird eine DJ-Login-Datei erzeugt, traegt die Config am Ende ein
    ``include=`` -- sonst laedt pawcast die DJ-Accounts nicht.

    Rueckgabe: ``(config_path, djlogins_path)`` -- der zweite Wert ist
    ``None``, wenn keine DJ-Login-Datei erzeugt wurde.
    """
    written: List[str] = []

    if os.path.exists(config_path) and not force:
        raise FileExistsError(
            "Config existiert bereits (ueberschreiben mit --force): %s"
            % config_path)

    directory = os.path.dirname(os.path.abspath(config_path))
    if directory:
        os.makedirs(directory, exist_ok=True)

    content = CONFIG_TEMPLATE.format(**DEFAULT_PATHS)
    if djlogins_path:
        # Die Include-Zeile muss auf die DJ-Datei zeigen, sonst werden
        # die DJ-Accounts nicht geladen.
        content += f"\n# --- DJ-Accounts (eigene Datei, Passwoerter) ---\ninclude={djlogins_path}\n"

    with open(config_path, "w", encoding="utf-8") as fh:
        fh.write(content)
    written.append(config_path)

    if djlogins_path:
        if os.path.exists(djlogins_path) and not force:
            raise FileExistsError(
                "DJ-Login-Datei existiert bereits "
                "(ueberschreiben mit --force): %s" % djlogins_path)
        dj_dir = os.path.dirname(os.path.abspath(djlogins_path))
        if dj_dir:
            os.makedirs(dj_dir, exist_ok=True)
        with open(djlogins_path, "w", encoding="utf-8") as fh:
            fh.write(DJLOGINS_TEMPLATE)
        written.append(djlogins_path)

    return config_path, djlogins_path
