"""Test: Metadaten aus beliebigen Audio-Formaten lesen.

Der Encoder dekodiert ueber ffmpeg ohnehin jedes Format nach MP3.
Damit das im Stream auch *benannt* ist, muessen die Tags gelesen
werden -- bei MP3 ueber ID3v2, bei allen anderen ueber ffprobe.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, "/mnt/openclaw/workspace/pawcast")

from pawcast.encoder import Encoder  # noqa: E402
from pawcast.playlist import read_id3, read_tags_ffprobe  # noqa: E402

HAVE_FFMPEG = shutil.which("ffmpeg") is not None
HAVE_FFPROBE = shutil.which("ffprobe") is not None

#: Format -> ffmpeg-Encoder-Argumente.
#: AIFF kann nur "title" (das Format kennt kein Artist-Feld).
FORMATS = [
    ("flac", ["-c:a", "flac"], True),
    ("ogg", ["-c:a", "libvorbis"], True),
    ("opus", ["-c:a", "libopus"], True),
    ("wav", ["-c:a", "pcm_s16le"], True),
    ("m4a", ["-c:a", "aac", "-b:a", "128k"], True),
    ("mp3", ["-c:a", "libmp3lame", "-b:a", "192k"], True),
    ("aiff", ["-c:a", "pcm_s16be"], False),
]


def _make(path: str, args: list, artist: str, title: str) -> bool:
    """Erzeugt eine Testdatei mit Tags. False, wenn ffmpeg fehlt."""
    if not HAVE_FFMPEG:
        return False
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-f", "lavfi", "-i", "sine=f=440:d=1"]
    cmd += args
    cmd += ["-metadata", "title=" + title, "-metadata", "artist=" + artist,
            path]
    return subprocess.run(cmd, check=False).returncode == 0


class TestFormatTags(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pawcast-fmt-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _path(self, name):
        return os.path.join(self.tmp, "tagged.%s" % name)

    def test_ffprobe_liest_artist_und_title(self):
        """FLAC, Ogg, Opus, WAV und M4A liefern Artist und Title."""
        if not HAVE_FFPROBE:
            self.skipTest("ffprobe nicht installiert")
        for name, args, _ in FORMATS:
            if name in ("mp3",):
                continue  # MP3 laeuft ueber ID3v2
            path = self._path(name)
            if not _make(path, args, "Künstler", "Titel %s" % name):
                self.skipTest("ffmpeg kann %s nicht erzeugen" % name)
            artist, title = read_tags_ffprobe(path)
            self.assertEqual(title, "Titel %s" % name, name)
            if name != "aiff":
                # AIFF kennt im Format selbst kein Artist-Feld.
                self.assertEqual(artist, "Künstler", name)

    def test_read_id3_faellt_auf_ffprobe_zurueck(self):
        """Ohne ID3v2 kommt der Titel trotzdem aus ffprobe."""
        if not HAVE_FFPROBE:
            self.skipTest("ffprobe nicht installiert")
        path = self._path("flac")
        if not _make(path, ["-c:a", "flac"], "Künstler", "FLAC Titel"):
            self.skipTest("ffmpeg kann flac nicht erzeugen")
        artist, title = read_id3(path)
        self.assertEqual(title, "FLAC Titel")
        self.assertEqual(artist, "Künstler")

    def test_mp3_nutzt_id3v2(self):
        """MP3 wird weiterhin ueber ID3v2 gelesen -- ohne ffprobe."""
        path = self._path("mp3")
        if not _make(path, ["-c:a", "libmp3lame", "-b:a", "192k"],
                     "MP3 Künstler", "MP3 Titel"):
            self.skipTest("ffmpeg kann mp3 nicht erzeugen")
        artist, title = read_id3(path)
        self.assertEqual(artist, "MP3 Künstler")
        self.assertEqual(title, "MP3 Titel")

    def test_alle_formate_werden_zu_mp3_encodiert(self):
        """Jedes Format erzeugt einen gueltigen MP3-Stream."""
        if not HAVE_FFMPEG:
            self.skipTest("ffmpeg nicht installiert")
        enc = Encoder(bitrate=128000, samplerate=44100, channels=2)
        for name, args, _ in FORMATS:
            path = self._path(name)
            if not _make(path, args, "Künstler", "Titel"):
                self.skipTest("ffmpeg kann %s nicht erzeugen" % name)
            chunks = list(enc.encode(path))
            self.assertTrue(chunks, "%s: keine Daten" % name)
            # MP3-Frame-Sync: 11 Bits gesetzt -> 0xFF, dann 0xEx/0xFx
            self.assertEqual(chunks[0][0], 0xFF, name)
            self.assertIn(chunks[0][1] & 0xE0, (0xE0,), name)

    def test_fehlende_datei_liefert_leere_tags(self):
        """Eine fehlende Datei darf nichts crashen."""
        self.assertEqual(read_id3("/nicht/da.flac"), ("", ""))


if __name__ == "__main__":
    unittest.main()
