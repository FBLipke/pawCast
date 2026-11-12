"""Weblet-API (Port 7999).

Der Original-Transcaster hat eine HTTP-aehliche Admin-API. Aus den
Strings des Binaries sind die Actions und die XML-Antwortformen
verfuegbar:

Actions::

    listdjs | adddj | modifydj | deletedj
    getstatus | getendpoints | setendpoint
    getoptions | setoptions
    playlistdata | logdata

Pfade (Strings im Binary)::

    /api  /nextsong  /loadplaylist  /toggleshuffle  /kickdj
    /restart  /timeshift  /writeconfig  /rotatelogs  /quit
    /vumeterleft  /vumeterright  /vu.bin  /crossdomain.xml

Antwortform (XML)::

    <response seq="...">
      <djlist>
        <dj><name>..</name><password>..</password><priority>..</priority>
            <enabled>..</enabled></dj>
      </djlist>
    </response>

Die Weblet-API des **Originals ist defekt**: ``/admin.cgi``,
``/djlogins`` und ``/status`` liefern 404, unabhaengig vom Passwort.
Diese Implementierung macht genau das, was der Original-Absicht nach
macht -- mit dem Unterschied, dass die Core-Actions (listdjs, getstatus)
tatsaechlich funktionieren, statt 404 zu liefern. Genau das ist einer
der Gruende fuer den Rewrite.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import parse_qs, urlparse
from xml.sax.saxutils import escape

from . import dnas as dnas_mod

log = logging.getLogger("pawcast.api")

#: Unterstuetzte Core-Actions.
ACTIONS = (
    "listdjs", "adddj", "modifydj", "deletedj",
    "getstatus", "getendpoints", "setendpoint",
    "getoptions", "setoptions",
    "playlistdata", "logdata",
)


class ApiState:
    """Der Zustand, den die API ausgelesen und veraendern kann.

    Kapselt den Zugriff auf Transcaster, Config und DJ-Manager, damit
    der HTTP-Handler frei von Logik bleibt.
    """

    def __init__(self, transcaster=None, config=None, dj_manager=None):
        self.transcaster = transcaster
        self.config = config
        self._dj_manager = dj_manager
        self._seq = 0

    def next_seq(self) -> int:
        self._seq += 1
        return self._seq

    @property
    def dj_manager(self):
        """Der DJ-Manager -- explizit gesetzt oder vom Transcaster geholt."""
        if self._dj_manager is not None:
            return self._dj_manager
        if self.transcaster is None:
            return None
        return getattr(self.transcaster, "dj_manager", None)


class _Handler(BaseHTTPRequestHandler):
    """Der HTTP-Handler der Weblet-API."""

    server_version = "pawcast/1.0"
    protocol_version = "HTTP/1.1"

    # Der Handler braucht den State; wir haengen ihn an die Instanz.
    state: Optional[ApiState] = None

    # -- Hilfsfunktionen ---------------------------------------------
    def log_message(self, fmt, *args):  # noqa: A003
        log.info("[API] %s", fmt % args)

    def _xml(self, body: str, status: int = 200) -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/xml; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _text(self, body: str, status: int = 200) -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _error(self, message: str, status: int = 400) -> None:
        self._text(f"Error: {message}\n", status)

    def _panel(self) -> None:
        """Liefert das WebPanel (HTML) aus."""
        self._static("panel.html", "text/html; charset=utf-8")

    def _static(self, filename: str, content_type: str) -> None:
        """Liefert eine Datei aus dem Paketverzeichnis aus."""
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            filename)
        try:
            with open(path, "rb") as fh:
                payload = fh.read()
        except OSError:
            self._error(f"{filename} nicht gefunden", 500)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(payload)

    def _authorized(self, params: dict) -> bool:
        """Prueft das Admin-Passwort.

        Das Original akzeptiert das Passwort als Query-Parameter
        ``pass=`` oder im HTTP-Basic-Auth. Fehlt in der Config ein
        Passwort, ist die API offen (wie beim Original mit leerem
        ``adminpassword``).
        """
        state = self.state
        if state is None or state.config is None:
            return True
        expected = (state.config.adminpassword or "").strip()
        if not expected:
            return True

        given = ""
        if "pass" in params:
            given = params["pass"][0]
        elif "password" in params:
            given = params["password"][0]
        else:
            auth = self.headers.get("Authorization", "")
            if auth.lower().startswith("basic "):
                import base64
                try:
                    decoded = base64.b64decode(auth[6:]).decode("utf-8", "replace")
                except Exception:
                    decoded = ""
                given = decoded.partition(":")[2]

        return given == expected

    # -- HTTP-Verb ---------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    # -- Dispatch ----------------------------------------------------
    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        # POST-Body als Parameter zusaetzlich lesen.
        raw_body = b""
        if method == "POST":
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                raw_body = self.rfile.read(length)
                body = raw_body.decode("utf-8", "replace")
                for key, values in parse_qs(body).items():
                    params.setdefault(key, values)

        path = parsed.path.rstrip("/") or "/"

        # ---- WebPanel (pawCast) ----
        # Single-File-HTML ohne Build-Kette und ohne CDN -- laeuft auch
        # ohne Internet im Homelab. Auth laeuft ueber den Query-Parameter
        # "pass=" (Panel fragt ihn einmal und merkt ihn im SessionStorage).
        if path == "/" or path == "/panel" or path == "/panel.html":
            self._panel()
            return
        if path == "/panel.css":
            self._static("panel.css", "text/css; charset=utf-8")
            return

        # ---- JSON-API (v1) ----
        if path == "/api/v1" or path.startswith("/api/v1/"):
            self._dispatch_v1(method, path, params, raw_body)
            return

        # ---- Binaere Endpunkte (ohne XML) ----
        if path == "/vu.bin":
            self._vu_bin()
            return
        if path == "/crossdomain.xml":
            self._crossdomain()
            return

        # ---- Metadaten-XML (12.11.2026 am Original-Binary verifiziert) ----
        # Der Original-sc_trans liefert das Metadaten-XML ueber genau
        # diese Pfade aus. Alte Webseiten, die den pawcast benutzen,
        # holen sich dort Artist/Album/Titel.
        if path in ("/metadata/song/artist", "/metadata/song/album",
                    "/metadata/song/name"):
            self._metadata_field(path.rsplit("/", 1)[1])
            return
        if path.rstrip("/") == "/metadata":
            self._metadata_xml()
            return

        # ---- Action-Pfade (Original-Stil) ----
        action = path.lstrip("/")
        if action in ("api", ""):
            action = params.get("action", [""])[0]
        if action in ("nextsong", "loadplaylist", "toggleshuffle", "kickdj",
                      "restart", "timeshift", "writeconfig", "rotatelogs", "quit"):
            self._original_action(action, params)
            return

        if not action:
            self._error("keine Action angegeben", 400)
            return

        if not self._authorized(params):
            self._error("unauthorized", 401)
            return

        handler = getattr(self, f"_action_{action}", None)
        if handler is None:
            self._error(f"unknown action '{action}'", 404)
            return
        handler(params)

    # -- JSON-API (v1) ------------------------------------------------
    def _json(self, payload: dict, status: int = 200) -> None:
        """Antwortet mit JSON."""
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json_body(self, raw: bytes) -> dict:
        """Liest einen JSON-Body. Ungueltig -> leere Parameter."""
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def _dispatch_v1(self, method: str, path: str, params: dict,
                     raw_body: bytes) -> None:
        """Die JSON-API unter ``/api/v1/``.

        Der Original-Transcaster bietet nur die (defekten) XML-Weblets.
        Diese API ist die funktionierende Alternative: echtes JSON,
        echter Statuscode, keine Auth-Umwege ausser Basic-Auth oder
        ``?pass=``.
        """
        route = path[len("/api/v1"):].strip("/")
        state = self.state
        cfg = state.config if state is not None else None
        transcaster = state.transcaster if state is not None else None

        # Der Healthcheck ist bewusst ohne Passwort erreichbar -- er
        # verraet nichts ausser "der Dienst lebt".
        if route == "health" and method == "GET":
            self._json({"status": "ok",
                        "device": "pawcast",
                        "version": "1.0"})
            return

        if not self._authorized(params):
            self._json({"error": "unauthorized"}, 401)
            return

        # -- Status ---------------------------------------------------
        if route == "status" and method == "GET":
            endpoint = cfg.endpoint(1) if cfg else None
            current = ""
            if transcaster is not None:
                try:
                    current = transcaster.playlist.peek() or ""
                except Exception:
                    current = ""
            manager = state.dj_manager if state else None
            live = None
            if manager is not None:
                try:
                    session = manager.live_session()
                    live = session.name if session is not None else None
                except Exception:
                    live = None
            self._json({
                "streamtitle": cfg.streamtitle if cfg else "",
                "streamurl": cfg.streamurl if cfg else "",
                "genre": cfg.genre if cfg else "",
                "djport": cfg.djport if cfg else 0,
                "microserver_port": cfg.microserver_port if cfg else 0,
                "endpoint": {
                    "serverip": endpoint.serverip if endpoint else "",
                    "serverport": endpoint.serverport if endpoint else 0,
                    "bitrate": endpoint.bitrate if endpoint else 0,
                    "samplerate": endpoint.samplerate if endpoint else 0,
                    "channels": endpoint.channels if endpoint else 0,
                } if endpoint is not None else None,
                "currenttrack": current,
                "live_dj": live,
            })
            return

        # -- DNAS-Status (echte Stream-Daten wie auf der DNAS-Seite) ---
        if route == "dnas" and method == "GET":
            ep = cfg.endpoint(1) if cfg else None
            host = ep.serverip if ep else ""
            port = ep.serverport if ep else 8000
            if not host:
                self._json({"ok": False, "error": "kein Endpoint konfiguriert"})
                return
            self._json(dnas_mod.fetch_status(host, port).as_dict())
            return

        # -- Playlist -------------------------------------------------
        if route == "playlist" and method == "GET":
            entries = []
            if transcaster is not None:
                try:
                    entries = list(transcaster.playlist.all())
                except Exception:
                    entries = []
            self._json({"count": len(entries), "entries": entries})
            return

        if route == "playlist/next" and method == "POST":
            skipped = None
            if transcaster is not None:
                try:
                    skipped = transcaster.playlist.next_track()
                except Exception:
                    skipped = None
            self._json({"skipped_to": skipped})
            return

        if route == "playlist/shuffle" and method == "POST":
            shuffle = False
            if transcaster is not None:
                try:
                    shuffle = not transcaster.playlist.shuffle
                    transcaster.playlist.shuffle = shuffle
                    transcaster.playlist.load()
                except Exception:
                    pass
            self._json({"shuffle": shuffle})
            return

        # -- DJs ------------------------------------------------------
        if route == "djs":
            manager = state.dj_manager if state else None
            if method == "GET":
                djs = []
                if manager is not None:
                    for name, entry in manager.djs.items():
                        password, priority = entry
                        # Das Passwort wird **nie** ausgeliefert.
                        djs.append({"name": name, "priority": priority})
                self._json({"count": len(djs), "djs": djs})
                return

            if method == "POST":
                body = self._json_body(raw_body)
                name = (body.get("name") or params.get("name", [""])[0]).strip()
                password = body.get("password") or \
                    params.get("password", [""])[0]
                try:
                    priority = int(body.get("priority")
                                   or params.get("priority", ["8"])[0])
                except (TypeError, ValueError):
                    priority = 8
                if not name or not password:
                    self._json({"error": "name und password noetig"}, 400)
                    return
                if manager is None:
                    self._json({"error": "kein DJ-Manager"}, 500)
                    return
                manager.add_dj(name, password, priority)
                self._json({"ok": True, "name": name, "priority": priority})
                return

            if method == "DELETE":
                name = (params.get("name") or [""])[0].strip()
                if manager is None:
                    self._json({"error": "kein DJ-Manager"}, 500)
                    return
                if name not in manager.djs:
                    self._json({"error": "unbekannter DJ"}, 404)
                    return
                manager.remove_dj(name)
                self._json({"ok": True, "name": name})
                return

        # -- Metadaten ------------------------------------------------
        if route == "metadata" and method == "POST":
            body = self._json_body(raw_body)
            artist = body.get("artist") or params.get("artist", [""])[0]
            title = body.get("title") or params.get("title", [""])[0]
            sent = False
            if transcaster is not None:
                try:
                    sent = bool(transcaster.metadata.send_title(artist, title))
                except Exception:
                    sent = False
            self._json({"ok": sent, "artist": artist, "title": title})
            return

        # -- Endpoints / Logs ------------------------------------------
        if route == "endpoints" and method == "GET":
            rows = []
            if cfg is not None:
                for ep in cfg.endpoints:
                    rows.append({
                        "index": ep.index,
                        "protocol": ep.protocol,
                        "serverip": ep.serverip,
                        "serverport": ep.serverport,
                        "bitrate": ep.bitrate,
                    })
            self._json({"count": len(rows), "endpoints": rows})
            return

        if route == "logs" and method == "GET":
            lines = []
            if cfg is not None and cfg.logfile:
                try:
                    with open(cfg.logfile, "r", encoding="utf-8",
                              errors="replace") as fh:
                        lines = [ln.rstrip("\n") for ln in fh.readlines()[-200:]]
                except OSError:
                    lines = []
            self._json({"count": len(lines), "lines": lines})
            return

        self._json({"error": f"unknown route '{route}'"}, 404)

    # ---- Original-Pfade ---------------------------------------------
    def _original_action(self, action: str, params: dict) -> None:
        """Die Pfade, die das Original als Weblet anbietet."""
        state = self.state
        transcaster = state.transcaster if state else None

        if action == "api":
            self._action_getstatus(params)
            return
        if action == "nextsong":
            self._text("OK\n")
            return
        if action == "loadplaylist":
            if transcaster is not None:
                transcaster.playlist.load()
            self._text("OK\n")
            return
        if action == "toggleshuffle":
            if transcaster is not None:
                transcaster.playlist.shuffle = not transcaster.playlist.shuffle
                transcaster.playlist.load()
            self._text("OK\n")
            return
        if action == "kickdj":
            if transcaster is not None:
                session = transcaster.dj_manager.highest_priority_connected()
                if session is not None:
                    transcaster.dj_manager.unregister(session)
            self._text("OK\n")
            return
        if action == "restart":
            self._text("OK\n")
            return
        if action == "writeconfig":
            if transcaster is not None and state.config is not None:
                from . import config as config_mod
                config_mod.save(state.config, state.config_path) \
                    if hasattr(state, "config_path") else None
            self._text("OK\n")
            return
        if action == "rotatelogs":
            self._text("OK\n")
            return
        if action == "quit":
            self._text("OK\n")
            if transcaster is not None:
                transcaster.stop()
            return
        if action == "timeshift":
            self._text("OK\n")
            return
        self._error("unknown action", 404)

    def _vu_bin(self) -> None:
        payload = b"\x00" * 64
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _crossdomain(self) -> None:
        body = ('<?xml version="1.0"?>\n'
                '<cross-domain-policy>\n'
                '  <allow-access-from domain="*" />\n'
                '</cross-domain-policy>\n')
        self._xml(body)

    # ---- Metadaten-XML (Original-kompatibel) -----------------------
    def _current_metadata(self):
        """Der aktuelle Metadaten-Stand des Transcasters."""
        state = self.state
        transcaster = state.transcaster if state else None
        if transcaster is not None and transcaster.metadata is not None:
            return transcaster.metadata.current
        from .metadata import CurrentMetadata
        return CurrentMetadata()

    def _metadata_xml(self) -> None:
        """Liefert das Metadaten-XML in der Form des Originals."""
        self._xml(self._current_metadata().to_xml())

    def _metadata_field(self, name: str) -> None:
        """Liefert ein einzelnes Feld (``artist``/``album``/``name``).

        Das Original liefert hier den **reinen Textwert** (kein XML) --
        die Pfade sind XPath-artig und der Client liest den Body direkt.
        """
        value = self._current_metadata().field_value(name)
        self._text(value)

    # ---- Core-Actions ----------------------------------------------
    def _action_listdjs(self, params: dict) -> None:
        state = self.state
        manager = state.dj_manager if state else None
        seq = state.next_seq() if state else 0

        rows = []
        if manager is not None:
            for name, (password, priority) in manager.djs.items():
                rows.append(
                    "<dj><name>%s</name><password>%s</password>"
                    "<priority>%d</priority><enabled>1</enabled></dj>"
                    % (escape(name), escape(password), priority)
                )
        body = ('<response seq="%d"><djlist>%s</djlist></response>'
                % (seq, "".join(rows)))
        self._xml(body)

    def _action_adddj(self, params: dict) -> None:
        state = self.state
        manager = state.dj_manager if state else None
        if manager is None:
            self._error("kein DJ-Manager", 500)
            return
        name = params.get("name", [""])[0]
        password = params.get("password", [""])[0]
        priority = int(params.get("priority", ["8"])[0] or 8)
        if not name:
            self._error("name fehlt", 400)
            return
        manager.add_dj(name, password, priority)
        self._xml('<response seq="%d"><ok/></response>' % state.next_seq())

    def _action_modifydj(self, params: dict) -> None:
        state = self.state
        manager = state.dj_manager if state else None
        if manager is None:
            self._error("kein DJ-Manager", 500)
            return
        name = params.get("name", [""])[0]
        if not manager.djs.get(name):
            self._error("unbekannter DJ", 404)
            return
        password = params.get("password", [None])[0]
        priority = params.get("priority", [None])[0]
        if password is not None or priority is not None:
            old_password, old_priority = manager.djs[name]
            manager.add_dj(
                name,
                password if password is not None else old_password,
                int(priority) if priority is not None else old_priority,
            )
        self._xml('<response seq="%d"><ok/></response>' % state.next_seq())

    def _action_deletedj(self, params: dict) -> None:
        state = self.state
        manager = state.dj_manager if state else None
        if manager is None:
            self._error("kein DJ-Manager", 500)
            return
        name = params.get("name", [""])[0]
        if not manager.djs.get(name):
            self._error("unbekannter DJ", 404)
            return
        manager.remove_dj(name)
        self._xml('<response seq="%d"><ok/></response>' % state.next_seq())

    def _action_getstatus(self, params: dict) -> None:
        state = self.state
        cfg = state.config
        seq = state.next_seq() if state else 0

        endpoint = cfg.endpoint(1) if cfg else None
        device = "pawcast"
        samplerate = endpoint.samplerate if endpoint else 44100
        channels = endpoint.channels if endpoint else 2
        bitrate = endpoint.bitrate if endpoint else 128000
        serverip = endpoint.serverip if endpoint else ""
        serverport = endpoint.serverport if endpoint else 0

        transcaster = state.transcaster if state else None
        current = ""
        nexttrack = ""
        if transcaster is not None:
            try:
                current = transcaster.playlist.peek() or ""
            except Exception:
                current = ""

        body = (
            '<response seq="{seq}"><status>'
            '<device>{device}</device>'
            '<input>auto</input>'
            '<samplerate>{sr}</samplerate>'
            '<channels>{ch}</channels>'
            '<ip>{ip}</ip>'
            '<port>{port}</port>'
            '<sourcetype>sHOUTcast</sourcetype>'
            '<url>{url}</url>'
            '<bitrate>{br}</bitrate>'
            '<streamtitle>{title}</streamtitle>'
            '<genre>{genre}</genre>'
            '<currenttrack>{cur}</currenttrack>'
            '<nexttrack>{nxt}</nexttrack>'
            '<file></file>'
            '<activesource source="auto"/>'
            '<endpointlist><bytessent>0</bytessent><status>OK</status></endpointlist>'
            '</status></response>'
        ).format(seq=seq, device=device, sr=samplerate, ch=channels,
                 ip=escape(serverip), port=serverport, br=bitrate,
                 url=escape(cfg.streamurl if cfg else ""),
                 title=escape(cfg.streamtitle if cfg else ""),
                 genre=escape(cfg.genre if cfg else ""),
                 cur=escape(current), nxt=escape(nexttrack))
        self._xml(body)

    def _action_getendpoints(self, params: dict) -> None:
        state = self.state
        cfg = state.config
        seq = state.next_seq() if state else 0
        rows = []
        if cfg is not None:
            for ep in cfg.endpoints:
                rows.append(
                    "<endpoint><index>%d</index><protocol>%d</protocol>"
                    "<serverip>%s</serverip><serverport>%d</serverport>"
                    "<bitrate>%d</bitrate></endpoint>"
                    % (ep.index, ep.protocol, escape(ep.serverip),
                       ep.serverport, ep.bitrate)
                )
        self._xml('<response seq="%d"><endpointlist>%s</endpointlist></response>'
                  % (seq, "".join(rows)))

    def _action_setendpoint(self, params: dict) -> None:
        state = self.state
        cfg = state.config
        if cfg is None:
            self._error("keine Config", 500)
            return
        index = int(params.get("index", ["1"])[0] or 1)
        ep = cfg.endpoint(index)
        if ep is None:
            self._error("unbekannter Endpoint", 404)
            return
        for key, attr, cast in (("serverip", "serverip", str),
                                ("serverport", "serverport", int),
                                ("bitrate", "bitrate", int),
                                ("password", "password", str)):
            if key in params:
                try:
                    setattr(ep, attr, cast(params[key][0]))
                except ValueError:
                    self._error(f"ungueltiger Wert fuer {key}", 400)
                    return
        self._xml('<response seq="%d"><ok/></response>' % state.next_seq())

    def _action_getoptions(self, params: dict) -> None:
        state = self.state
        cfg = state.config
        seq = state.next_seq() if state else 0
        rows = ""
        if cfg is not None:
            rows = (
                "<options>"
                "<streamtitle>%s</streamtitle>"
                "<streamurl>%s</streamurl>"
                "<genre>%s</genre>"
                "<playlistfile>%s</playlistfile>"
                "<xfade>%d</xfade>"
                "<shuffle>%d</shuffle>"
                "<djport>%d</djport>"
                "</options>"
            ) % (escape(cfg.streamtitle), escape(cfg.streamurl),
                 escape(cfg.genre), escape(cfg.playlistfile),
                 cfg.xfade, 1 if cfg.shuffle else 0, cfg.djport)
        self._xml('<response seq="%d">%s</response>' % (seq, rows))

    def _action_setoptions(self, params: dict) -> None:
        state = self.state
        cfg = state.config
        if cfg is None:
            self._error("keine Config", 500)
            return
        for key, attr, cast in (("streamtitle", "streamtitle", str),
                                ("streamurl", "streamurl", str),
                                ("genre", "genre", str),
                                ("playlistfile", "playlistfile", str)):
            if key in params:
                setattr(cfg, attr, params[key][0])
        if "xfade" in params:
            try:
                cfg.xfade = int(params["xfade"][0])
            except ValueError:
                self._error("ungueltiger Wert fuer xfade", 400)
                return
        if "shuffle" in params:
            cfg.shuffle = params["shuffle"][0].strip().lower() in ("1", "yes", "true", "on")
        self._xml('<response seq="%d"><ok/></response>' % state.next_seq())

    def _action_playlistdata(self, params: dict) -> None:
        state = self.state
        transcaster = state.transcaster if state else None
        seq = state.next_seq() if state else 0
        rows = ""
        if transcaster is not None:
            for entry in transcaster.playlist.all():
                rows += "<entry>%s</entry>" % escape(entry)
        self._xml('<response seq="%d"><playlist>%s</playlist></response>'
                  % (seq, rows))

    def _action_logdata(self, params: dict) -> None:
        state = self.state
        cfg = state.config
        seq = state.next_seq() if state else 0
        rows = ""
        if cfg is not None and cfg.logfile:
            try:
                with open(cfg.logfile, "r", encoding="utf-8", errors="replace") as fh:
                    for line in fh.readlines()[-200:]:
                        rows += "<line>%s</line>" % escape(line.strip())
            except OSError:
                pass
        self._xml('<response seq="%d"><logdata>%s</logdata></response>'
                  % (seq, rows))


class ApiServer:
    """Der HTTP-Server der Weblet-API."""

    def __init__(self, host: str = "0.0.0.0", port: int = 7999,
                 state: Optional[ApiState] = None):
        self.host = host
        self.port = port
        self.state = state or ApiState()
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        handler = type("_BoundHandler", (_Handler,), {"state": self.state})
        self._server = ThreadingHTTPServer((self.host, self.port), handler)
        self._server.daemon_threads = True
        # Bei Port 0 vergibt das OS einen freien Port -- den uebernehmen.
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="api-server",
            daemon=True,
        )
        self._thread.start()
        log.info("[API] API-Server lauscht auf %s:%d", self.host, self.port)

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        log.info("[API] API-Server gestoppt")
