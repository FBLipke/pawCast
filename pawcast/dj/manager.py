"""DJ-Server: nimmt DJ-Anmeldungen entgegen, relayed sie, regelt Fallback.

Das Original hat **zwei** DJ-Protokolle:

* **SHOUTCAST** (``djProtocolShoutcast``) -- vereinfacht, mit den Zustaenden
  GETSTREAM, GETICY, NEEDPASS, CHECKPASS, SENDREPLY, EMPTY, KILL.
* **UVOX** (``djProtocolUvox``) -- vollstaendig, mit Cipher, Buffer-Size-
  Negotiation und Broadcast-Setup.

Beide sind proprietaer; die Byte-Sequenzen wurden aus dem Binary
empirisch verifiziert (siehe docs/dj-protocol-spec.md).

Der Manager entscheidet, was passiert:

* **DJ sendet**   -> der DJ-Stream wird an den DNAS durchgereicht (Relay)
* **DJ hoert zu** -> der DJ bekommt den AutoDJ-Stream zuhoeren
* **DJ verschwindet** -> Fallback auf die Playlist (Original: "Playlist
  has run dry" vermeiden)
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

log = logging.getLogger("pawcast.dj")


@dataclass
class DjSession:
    """Eine aktive DJ-Verbindung."""

    name: str
    address: str
    priority: int = 8
    connected_at: float = field(default_factory=time.time)
    #: Bytes, die der DJ bisher geschickt hat.
    bytes_sent: int = 0
    #: True, wenn der DJ gerade sendet (und nicht nur zuhoert).
    is_live: bool = False
    #: Der Socket des DJs.
    sock: object = None
    #: Audio-Bytes des DJs. Der Handler-Thread liest den Socket und legt
    #: die Bytes hier hinein; der Relay holt sie ab und schickt sie zum
    #: DNAS. Nur EIN Thread darf den Socket lesen -- sonst frisst sich
    #: der eine Thread die Daten des anderen weg.
    audio_queue: "queue.Queue[bytes]" = field(
        default_factory=lambda: queue.Queue(maxsize=256))

    @property
    def uptime(self) -> float:
        return time.time() - self.connected_at


class DjManager:
    """Verwaltet die DJ-Sessions und entscheidet ueber das Relay.

    Der Manager ist die Schnittstelle zwischen dem DJ-Server (Netzwerk) und
    dem Stream-Loop (der den DNAS fuettert).
    """

    def __init__(
        self,
        djs: Optional[Dict[str, object]] = None,
        on_dj_connected: Optional[Callable[[DjSession], None]] = None,
        on_dj_disconnected: Optional[Callable[[DjSession], None]] = None,
    ):
        #: login -> (password, priority)
        self.djs: Dict[str, tuple] = {}
        for dj in (djs or []):
            self.add_dj(dj.login, dj.password, dj.priority)  # type: ignore[attr-defined]
        self.on_dj_connected = on_dj_connected
        self.on_dj_disconnected = on_dj_disconnected
        self._sessions: Dict[str, DjSession] = {}
        self._lock = threading.RLock()

    def add_dj(self, login: str, password: str, priority: int = 8) -> None:
        self.djs[login] = (password, priority)

    def remove_dj(self, login: str) -> None:
        self.djs.pop(login, None)

    def check_credentials(self, login: str, password: str) -> Optional[int]:
        """Prueft Login/Passwort. Gibt die Prioritaet zurueck oder None."""
        entry = self.djs.get(login)
        if entry is None:
            return None
        stored_password, priority = entry
        if stored_password == password:
            return priority
        return None

    def find_by_password(self, password: str) -> Optional[Tuple[str, int]]:
        """Findet einen DJ allein ueber sein Passwort.

        Der Original-Transcaster akzeptiert am Source-Port auch
        ``GET /admin.cgi?pass=<passwort>`` -- **ohne** Login-Namen. Der
        Login wird dann ueber das Passwort ermittelt. Gibt
        ``(login, prioritaet)`` zurueck oder None.
        """
        with self._lock:
            for login, (stored, priority) in self.djs.items():
                if stored == password:
                    return login, priority
        return None

    def register(self, name: str, address: str, priority: int, sock: object) -> DjSession:
        """Registriert eine neue DJ-Session."""
        with self._lock:
            existing = self._sessions.get(name)
            if existing is not None:
                log.info("[DJ] Session %s ersetzt (Re-Login)", name)
                self._drop(existing)
            session = DjSession(name=name, address=address, priority=priority, sock=sock)
            self._sessions[name] = session
        log.info("[DJ] %s verbunden von %s (Prioritaet %d)", name, address, priority)
        if self.on_dj_connected:
            try:
                self.on_dj_connected(session)
            except Exception:
                log.exception("[DJ] on_dj_connected fehlgeschlagen")
        return session

    def unregister(self, session: DjSession) -> None:
        with self._lock:
            current = self._sessions.get(session.name)
            if current is session:
                del self._sessions[session.name]
        log.info("[DJ] %s getrennt (nach %.1fs, %d Bytes gesendet)",
                 session.name, session.uptime, session.bytes_sent)
        if self.on_dj_disconnected:
            try:
                self.on_dj_disconnected(session)
            except Exception:
                log.exception("[DJ] on_dj_disconnected fehlgeschlagen")

    def _drop(self, session: DjSession) -> None:
        try:
            if session.sock is not None:
                session.sock.close()
        except OSError:
            pass

    def sessions(self) -> List[DjSession]:
        with self._lock:
            return list(self._sessions.values())

    def live_session(self) -> Optional[DjSession]:
        """Die Session, die gerade sendet (die hoechste Prioritaet gewinnt)."""
        with self._lock:
            live = [s for s in self._sessions.values() if s.is_live]
        if not live:
            return None
        return max(live, key=lambda s: s.priority)

    def mark_live(self, session: DjSession, live: bool = True) -> None:
        session.is_live = live
        if live:
            log.info("[DJ] %s ist jetzt live (Prioritaet %d)", session.name, session.priority)
        else:
            log.info("[DJ] %s sendet nicht mehr, Fallback auf Playlist", session.name)

    def highest_priority_connected(self) -> Optional[DjSession]:
        """Die hoechstpriorisierte verbundene Session (sendet oder nicht)."""
        with self._lock:
            if not self._sessions:
                return None
            return max(self._sessions.values(), key=lambda s: s.priority)

    def stats(self) -> dict:
        with self._lock:
            return {
                "connected": len(self._sessions),
                "live": sum(1 for s in self._sessions.values() if s.is_live),
                "sessions": [
                    {
                        "name": s.name,
                        "address": s.address,
                        "priority": s.priority,
                        "uptime": round(s.uptime, 1),
                        "bytes": s.bytes_sent,
                        "live": s.is_live,
                    }
                    for s in self._sessions.values()
                ],
            }
