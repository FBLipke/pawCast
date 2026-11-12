"""Relay: reicht den Audio-Strom eines DJs an den DNAS durch.

Der Original-Transcaster hat dafuer eine eigene Komponente
(``[SOURCERELAY]`` in den Log-Strings). Der Ablauf ist:

1. Der DJ meldet sich an und sendet Audio.
2. Der Transcaster markiert die Session als ``live``.
3. Die Bytes des DJs gehen an den DNAS, **nicht** die Playlist.

Solange kein DJ live ist, laeuft die Playlist (Fallback). Faellt der DJ
aus, uebernimmt die Playlist wieder -- das entspricht der Original-
Logik ("Playlist has run dry" vermeiden).

Die Log-Strings ``[SOURCERELAY]`` sind bekannt, die
genauen Logtexte der Relay-Komponente sind TEILWEISE.
"""

from __future__ import annotations

import logging
import queue
import socket
import threading
import time
from typing import Optional

log = logging.getLogger("pawcast.dj.relay")

#: Groesse des Relay-Puffers.
CHUNK_SIZE = 16384

#: Socket-Timeout beim Warten auf Daten.
RECV_TIMEOUT = 1.0

#: Wie lange ein DJ ohne Daten als "weg" gilt (Sekunden).
#:
#: Muss **kleiner** sein als der DNAS-Stream-Timeout (~9s, am
#: Test-DNAS verifiziert: "Timeout waiting for stream data"). Sonst
#: kappt der DNAS die Source-Verbindung zuerst und der DJ-Relay
#: endet mitten im Lauf -- der Stream bricht dann ab.
#:
#: Bei 6s haben wir noch ~3s Reserve, um geordnet auf die Playlist
#: zurueckzufallen, bevor der DNAS die Leitung zieht.
SILENCE_TIMEOUT = 6.0

#: Mindest-Reserve zwischen dem DJ-Stille-Timeout und dem
#: DNAS-Stream-Timeout. Wird im Test verankert.
RELAY_TIMEOUT_MARGIN = 2.0

#: Intervall, in dem der Relay-Status geloggt wird.
STATUS_INTERVAL = 30.0


class RelayError(Exception):
    """Der Relay-Vorgang ist gescheitert."""


class DjRelay:
    """Relayed den Audio-Strom einer DJ-Session zum DNAS."""

    def __init__(self, dj_manager, source, transcaster=None):
        self.dj_manager = dj_manager
        self.source = source
        self.transcaster = transcaster
        self._running = False
        self._thread = None
        #: Signal zum Abbruch einer laufenden ``relay_session``.
        self._halt = threading.Event()

    # ------------------------------------------------------------------
    def start(self) -> None:
        """Startet den Relay-Loop als eigenen Thread."""
        if self._running:
            return
        self._halt.clear()
        self._running = True
        self._thread = threading.Thread(target=self.run, name="dj-relay", daemon=True)
        self._thread.start()
        log.info("[SOURCERELAY] Relay gestartet")

    def stop(self) -> None:
        self._running = False
        self._halt.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        log.info("[SOURCERELAY] Relay gestoppt")

    # ------------------------------------------------------------------
    def run(self) -> None:
        """Der Relay-Loop: wartet auf eine live Session und relayed sie."""
        last_status = 0.0

        while self._running:
            session = self.dj_manager.live_session()

            if session is None:
                # Kein DJ live -> kurz warten. Der Transcaster-Thread
                # kümmert sich in dieser Zeit um die Playlist.
                time.sleep(0.5)
                continue

            now = time.time()
            if now - last_status > STATUS_INTERVAL:
                log.info("[SOURCERELAY] Relaying %s (%s, %d Bytes bisher)",
                         session.name, session.address, session.bytes_sent)
                last_status = now

            try:
                self.relay_session(session)
            except RelayError as exc:
                log.info("[SOURCERELAY] Relay zu %s beendet: %s", session.name, exc)
            except OSError as exc:
                log.info("[SOURCERELAY] Socket-Fehler bei %s: %s", session.name, exc)
            finally:
                self.dj_manager.mark_live(session, False)
                self.dj_manager.unregister(session)

    # ------------------------------------------------------------------
    def relay_session(self, session, halt: Optional[threading.Event] = None) -> None:
        """Relayed eine einzelne Session, bis sie endet.

        Die Bytes kommen aus ``session.audio_queue`` -- der Handler-Thread
        liest den DJ-Socket. Ein zweiter Zugriff auf denselben Socket
        wuerde die Daten wegschnappen und den DJ blockieren.
        """
        if session.sock is None:
            raise RelayError("Session hat keinen Socket")

        # ``relay_session`` wird auch direkt aufgerufen (ohne den
        # Relay-Thread). Deshalb laeuft die Schleife ueber ein
        # Abbruch-Event und nicht ueber ``_running`` -- sonst wuerde sie
        # nie betreten. Im Direktmodul bekommt jeder Aufruf ein eigenes
        # Event, damit ein ``stop()`` des Threads nicht den
        # Direkt-Aufruf abwaescht.
        if halt is None:
            halt = self._halt
        last_data = time.time()

        while not halt.is_set():
            try:
                data = session.audio_queue.get(timeout=RECV_TIMEOUT)
            except queue.Empty:
                if time.time() - last_data > SILENCE_TIMEOUT:
                    raise RelayError("Timeout: DJ sendet seit %.0fs nichts"
                                     % (time.time() - last_data))
                continue

            if not data:
                raise RelayError("DJ hat die Verbindung geschlossen")

            last_data = time.time()
            session.bytes_sent += len(data)

            try:
                self.source.send(data)
            except Exception as exc:  # SourceError & Co.
                raise RelayError(f"DNAS hat die Verbindung getrennt: {exc}") from exc
