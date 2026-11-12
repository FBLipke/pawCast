"""Startet die pawCast-API mit echtem DNAS-Endpoint (nur Panel/API).

Der Endpoint zeigt auf den DNAS0 (10.232.136.119:8000), damit das
Panel die echten Live-Daten des Streams anzeigt.
"""

import sys

sys.path.insert(0, "/mnt/openclaw/workspace/pawcast")

from pawcast.api import ApiServer, ApiState
from pawcast.config import OutEndpoint, TranscastConfig
from pawcast.metadata import MetadataSender

cfg = TranscastConfig()
cfg.adminpassword = "pawcast"
cfg.adminport = 7999
cfg.streamtitle = "The Radio Fire and Ice"
cfg.streamurl = "http://the-radio-fire-and-ice.de/"
cfg.genre = "Misc"
cfg.djport = 9020
cfg.endpoints.append(OutEndpoint(
    index=1, protocol=1, serverip="10.232.136.119", serverport=8000,
    password="", encoder="mp3", bitrate=128000, samplerate=44100, channels=2))

sender = MetadataSender("10.232.136.119", 8000, "main_admin_pass")
sender.set_current("Alanis Morissette", "Thank U (Official Video)",
                   album="So Called Chaos", genre="Pop", year="2004")


class _Transcaster:
    """Minimaler Transcaster-Ersatz: liefert Metadaten + Playlist."""

    def __init__(self):
        self.metadata = sender
        self.playlist = self
        self.dj_manager = None

    def peek(self):
        return "Alanis Morissette - Thank U (Official Video)"

    def all(self):
        return [
            "/var/lib/pawcast/playlists/04 - Thank U.mp3",
            "/var/lib/pawcast/playlists/05 - The Right Stuff.mp3",
            "/var/lib/pawcast/playlists/06 - Head over Feet.mp3",
        ]

    def shuffle(self):
        pass


srv = ApiServer(host="0.0.0.0", port=7999,
                state=ApiState(transcaster=_Transcaster(), config=cfg))
srv.start()
print("pawCast läuft auf http://0.0.0.0:7999/ (Passwort: pawcast)", flush=True)

import time
while True:
    time.sleep(3600)
