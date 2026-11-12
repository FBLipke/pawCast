"""pawcast -- Python-Ersatz fuer den SHOUTcast Transcaster v2.

Hauptprogramm: verbindet den DNAS-Source, kombiniert AutoDJ-Playlist und
DJ-Relay und haelt den Stream am Leben.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import sys
import threading
import time
from typing import Iterator, Optional

from . import api as api_mod
from . import calendar as cal_mod
from . import config as config_mod
from .dj.manager import DjManager
from .dj.relay import DjRelay
from .crossfade import CrossfadePlayer
from .dj.server import DjServer
from .encoder import Encoder, EncoderError
from .metadata import MetadataSender
from .playlist import JinglePlayer, Playlist, read_id3
from .source import ShoutcastSource, SourceError

log = logging.getLogger("pawcast")


class Transcaster:
    """Der laufende Transcaster."""

    def __init__(self, cfg: config_mod.TranscastConfig):
        self.cfg = cfg
        self._stop = threading.Event()

        self.playlist = Playlist(
            cfg.playlistfile,
            shuffle=cfg.shuffle,
        )
        self.jingles = JinglePlayer(
            os.path.join(os.path.dirname(cfg.playlistfile or "."), "jingles.lst")
        )
        self.cal = cal_mod.parse(cfg.calendarfile) if cfg.calendarfile else cal_mod.Calendar()

        self.dj_manager = DjManager(
            djs=list(cfg.djs),
            on_dj_connected=self._on_dj_connected,
            on_dj_disconnected=self._on_dj_disconnected,
        )

        self.source: Optional[ShoutcastSource] = None
        self.metadata: Optional[MetadataSender] = None
        self.encoder: Optional[Encoder] = None
        self.player: Optional[CrossfadePlayer] = None
        self.dj_server: Optional[DjServer] = None
        self.microserver: Optional[DjServer] = None
        self.api_server: Optional[api_mod.ApiServer] = None
        self.relay: Optional[DjRelay] = None

    # ------------------------------------------------------------------
    # Aufbau
    # ------------------------------------------------------------------
    def setup(self) -> None:
        ep = self.cfg.endpoint(1)
        if ep is None:
            raise RuntimeError("Kein Ausgabe-Endpoint in der Config (outprotocol_1 fehlt)")

        try:
            self.encoder = Encoder(ep.bitrate, ep.samplerate, ep.channels)
        except EncoderError as exc:
            raise RuntimeError(str(exc)) from exc

        # Der Crossfade-Player ist der eigentliche Stream-Erzeuger:
        # er dekodiert zu PCM, mischt die Ueberblendung und encodiert
        # mit einem **durchgehenden** ffmpeg-Prozess. Der dateiweise
        # Encoder (``self.encoder``) bleibt nur als Fallback fuer
        # xfade=0 und fuer die alten Code-Pfade.
        self.player = CrossfadePlayer(
            bitrate=ep.bitrate,
            samplerate=ep.samplerate,
            channels=ep.channels,
            xfade=self.cfg.xfade,
        )
        log.info("Crossfade aktiv: %d Sekunden (equal power)",
                 self.cfg.xfade)

        self.source = ShoutcastSource(
            host=ep.serverip,
            port=ep.serverport,
            password=ep.password,
            name=self.cfg.streamtitle,
            genre=self.cfg.genre,
            url=self.cfg.streamurl,
            bitrate=ep.bitrate,
            public=False,
        )
        self.metadata = MetadataSender(ep.serverip, ep.serverport, self.cfg.adminpassword)

        if self.cfg.djport:
            self.dj_server = DjServer(port=self.cfg.djport, handler=self._handle_dj)
            self.dj_server.start()

            # Der Original-Transcaster oeffnet neben dem DJ-Port noch
            # einen zweiten Listener auf ``djport + 1`` ("MICROSERVER").
            # Beide Ports sprechen dasselbe DJ-Protokoll.
            microserver_port = self.cfg.microserver_port
            try:
                self.microserver = DjServer(
                    port=microserver_port, handler=self._handle_dj,
                )
                self.microserver.start()
                log.info("[MICROSERVER] Listening for connection on port %d",
                         microserver_port)
            except OSError as exc:
                # Darf den Start nicht verhindern -- der DJ-Port
                # ist der wichtigere.
                log.warning("[MICROSERVER] Port %d nicht bindbar (%s)",
                            microserver_port, exc)
                self.microserver = None

        if self.cfg.adminport:
            state = api_mod.ApiState(transcaster=self, config=self.cfg)
            self.api_server = api_mod.ApiServer(port=self.cfg.adminport, state=state)
            self.api_server.start()

    def run(self) -> None:
        """Hauptschleife: streamen, bis gestoppt."""
        log.info("=== pawcast startet ===")
        self.setup()

        backoff = 5
        while not self._stop.is_set():
            try:
                assert self.source is not None
                self.source.connect(retries=3, retry_delay=backoff)
                backoff = 5
                self._stream_loop()
            except SourceError as exc:
                log.error("[STREAM] Verbindung verloren: %s", exc)
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 60)
            except AssertionError:
                break

        self.shutdown()

    def _stream_loop(self) -> None:
        """Der Kern: Playlist -> Crossfade-Player -> DNAS.

        Unterbrochen vom DJ-Relay: sobald sich ein DJ anmeldet, hat er
        Vorrang und der AutoDJ-Stream wird abgebrochen.
        """
        assert self.source is not None and self.player is not None

        while not self._stop.is_set():
            live = self.dj_manager.live_session()

            if live is not None:
                self._relay_from_dj(live)
                continue

            path = self.playlist.next_track()
            if path is None:
                # "Playlist has run dry" -- das Original stirbt hier.
                # Wir warten und versuchen es erneut (die Liste kann neu
                # geladen werden).
                log.warning("[PLAYLISTMGR] Playlist has run dry, warte 30s")
                self._stop.wait(30)
                self.playlist.load()
                continue

            # Der Player uebernimmt die Playlist von hier an.
            self._stream_with_crossfade()

    def _playlist_iter(self) -> Iterator[str]:
        """Liefert die naechsten Tracks; endet, wenn die Liste leer ist."""
        while not self._stop.is_set():
            path = self.playlist.next_track()
            if path is None:
                return
            yield path

    def _on_track(self, path: str) -> None:
        """Titel setzen, wenn ein Track zu hoeren beginnt."""
        artist, title = read_id3(path)
        if artist or title:
            log.info("[DECODE] Next file is %s -> %s - %s", path, artist, title)
            if self.metadata is not None:
                self.metadata.set_current(artist, title)
                self.metadata.send_title(artist, title)
        else:
            log.info("[DECODE] Next file is %s", path)
            if self.metadata is not None:
                self.metadata.set_current("", os.path.basename(path))
                self.metadata.send_title("", os.path.basename(path))

    def _stream_with_crossfade(self) -> None:
        """Streamt mit echter Ueberblendung zum DNAS."""
        assert self.source is not None and self.player is not None

        try:
            for chunk in self.player.stream(self._playlist_iter(),
                                             on_next=self._on_track):
                if self._stop.is_set():
                    return
                # Ein DJ, der jetzt live geht, hat Vorrang.
                if self.dj_manager.live_session() is not None:
                    log.info("[DJ] DJ hat Vorrang -- AutoDJ-Stream abgebrochen")
                    return
                try:
                    self.source.send(chunk)
                except SourceError:
                    raise
        finally:
            self.player.stop()

    def _stream_file(self, path: str) -> None:
        """Fallback-Pfad ohne Ueberblendung (xfade=0, ohne ffmpeg-Pipeline)."""
        assert self.encoder is not None and self.source is not None

        artist, title = read_id3(path)
        if artist or title:
            log.info("[DECODE] Next file is %s -> %s - %s", path, artist, title)
            if self.metadata is not None:
                self.metadata.send_title(artist, title)
        else:
            log.info("[DECODE] Next file is %s", path)
            if self.metadata is not None:
                self.metadata.send_title("", os.path.basename(path))

        for chunk in self.encoder.encode(path):
            if self._stop.is_set():
                return
            # Ein DJ, der jetzt live geht, hat Vorrang.
            if self.dj_manager.live_session() is not None:
                return
            try:
                self.source.send(chunk)
            except SourceError:
                raise

    def _relay_from_dj(self, session) -> None:
        """Relayed den DJ-Stream zum DNAS.

        Die eigentliche Arbeit macht der ``DjRelay``; er wird hier nur
        fuer eine einzelne Session aufgerufen, damit der Transcaster-
        Thread die Kontrolle ueber den Fallback behält.
        """
        if self.relay is None:
            self.relay = DjRelay(self.dj_manager, self.source, self)
        log.info("[DJ] Relaying %s (%s)", session.name, session.address)
        # Eigener Abbruch-Event: der Direkt-Aufruf darf nicht am
        # globalen Relay-Stopp haengen.
        halt = threading.Event()
        try:
            self.relay.relay_session(session, halt=halt)
        except Exception as exc:
            log.info("[DJ] Relay zu %s beendet: %s", session.name, exc)
        finally:
            self.dj_manager.mark_live(session, False)
            self.dj_manager.unregister(session)

    # ------------------------------------------------------------------
    # DJ-Ereignisse
    # ------------------------------------------------------------------
    def _on_dj_connected(self, session) -> None:
        log.info("[DJ] %s verbunden -- warte auf Audio", session.name)

    def _on_dj_disconnected(self, session) -> None:
        log.info("[DJ] %s weg -- Fallback auf Playlist", session.name)

    def _handle_dj(self, client, addr) -> None:
        """Wird vom DjServer fuer jede Verbindung aufgerufen."""
        from .dj.protocol import handle_connection
        handle_connection(client, addr, self.dj_manager, self)

    # ------------------------------------------------------------------
    # Steuerung
    # ------------------------------------------------------------------
    def stop(self) -> None:
        self._stop.set()

    def shutdown(self) -> None:
        log.info("=== pawcast beendet ===")
        if self.player is not None:
            self.player.stop()
        if self.dj_server is not None:
            self.dj_server.stop()
        if self.microserver is not None:
            self.microserver.stop()
        if self.source is not None:
            self.source.close()


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="pawcast -- Python-Ersatz fuer den SHOUTcast Transcaster v2"
    )
    parser.add_argument("config", nargs="?", help="Pfad zur sc_trans.conf")
    parser.add_argument("--config", dest="config_opt",
                        help="Pfad zur sc_trans.conf (alternativ zur Positionsoption)")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--dry-run", action="store_true",
                        help="Config laden und anzeigen, nicht starten")
    parser.add_argument("--genconf", action="store_true",
                        help="Beispielkonfiguration erzeugen (statt zu starten)")
    parser.add_argument("--genconf-djlogins", metavar="DATEI",
                        help="zusaetzlich eine DJ-Login-Datei erzeugen")
    parser.add_argument("--force", action="store_true",
                        help="beim --genconf vorhandene Dateien ueberschreiben")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    config_path = args.config_opt or args.config

    # ---- --genconf: Config erzeugen, nicht starten ----
    if args.genconf:
        if not config_path:
            print("Usage: pawcast --genconf <sc_trans.conf>",
                  file=sys.stderr)
            return 2
        try:
            written, djlogins = config_mod.genconf(
                config_path, args.genconf_djlogins, force=args.force)
        except FileExistsError as exc:
            print("FEHLER: %s" % exc, file=sys.stderr)
            return 1
        print("Config erzeugt: %s" % written)
        if djlogins:
            print("DJ-Logins erzeugt: %s" % djlogins)
        print("Passwoerter anpassen: adminpassword, password_1, "
              "djpassword_1")
        return 0

    if not config_path:
        print("Usage: pawcast <sc_trans.conf>", file=sys.stderr)
        return 2

    cfg = config_mod.load(config_path)
    log.info("Config geladen: %s", config_path)
    if cfg.unknown:
        log.debug("Unbekannte Keys: %s", sorted(cfg.unknown))
    log.info("Endpoint: %s", cfg.endpoint(1))
    log.info("DJs: %d", len(cfg.djs))

    if args.dry_run:
        print(f"streamtitle = {cfg.streamtitle}")
        print(f"playlist   = {cfg.playlistfile} ({len(Playlist(cfg.playlistfile))} Titel)")
        print(f"calendar   = {cfg.calendarfile}")
        print(f"djport     = {cfg.djport}")
        print(f"adminport  = {cfg.adminport}")
        for dj in cfg.djs:
            print(f"  DJ {dj.index}: {dj.login} (Prio {dj.priority})")
        return 0

    transcaster = Transcaster(cfg)

    def _sig(signum, frame):
        log.info("Signal %s empfangen, stoppe", signum)
        transcaster.stop()

    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)

    try:
        transcaster.run()
    except RuntimeError as exc:
        log.error("Start fehlgeschlagen: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
