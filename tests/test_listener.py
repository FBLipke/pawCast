"""Test: ICY-Metadaten aus einem SHOUTcast-Stream lesen.

Der Listener haengt sich als Hoerer an den Stream und liest die
``StreamTitle``-Bloecke. Der knifflige Teil ist die Laengenangabe:
der sc_serv 2.5.5 zaehlt in **16-Byte-Bloecken**, der Standard in Bytes.
"""

from __future__ import annotations

import sys
import unittest

sys.path.insert(0, "/mnt/openclaw/workspace/pawcast")
sys.path.insert(0, "/mnt/openclaw/workspace/pawcast/tools")

from fake_listener import (  # noqa: E402
    _is_mp3_sync,
    _parse_metadata,
    _read_metadata_block,
    _split_artist_title,
    DEFAULT_USER_AGENT,
)


class _FakeReader:
    """Liefert Bytes aus einem Puffer; ``peek`` legt sie zurueck."""

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def __call__(self, count: int, peek: bool = False) -> bytes:
        out = self.data[self.pos:self.pos + count]
        if not peek:
            self.pos += len(out)
        return out


class TestMp3Sync(unittest.TestCase):
    def test_gueltiger_sync(self):
        self.assertTrue(_is_mp3_sync(b"\xff\xfb\x90\x64"))
        self.assertTrue(_is_mp3_sync(b"\xff\xe0"))

    def test_ungueltiger_sync(self):
        self.assertFalse(_is_mp3_sync(b"Stream"))
        self.assertFalse(_is_mp3_sync(b"\x00\x00"))
        self.assertFalse(_is_mp3_sync(b"\xff"))
        self.assertFalse(_is_mp3_sync(b""))


class TestMetadataParsing(unittest.TestCase):
    def test_stream_title(self):
        payload = b"StreamTitle='Alanis Morissette - Thank U';"
        self.assertEqual(_parse_metadata(payload),
                         "Alanis Morissette - Thank U")

    def test_stream_title_mit_fuellnullen(self):
        payload = b"StreamTitle='Titel - Kuenstler';\x00\x00\x00\x00\x00"
        self.assertEqual(_parse_metadata(payload), "Titel - Kuenstler")

    def test_leerer_block(self):
        self.assertIsNone(_parse_metadata(b""))

    def test_plain_text(self):
        self.assertEqual(_parse_metadata(b"Artist - Title"), "Artist - Title")


class TestArtistTitleSplit(unittest.TestCase):
    def test_mit_dash(self):
        self.assertEqual(_split_artist_title("Artist - Title"),
                         ("Artist", "Title"))

    def test_ohne_dash(self):
        self.assertEqual(_split_artist_title("Nur Titel"), ("", "Nur Titel"))

    def test_leer(self):
        self.assertEqual(_split_artist_title(""), ("", ""))


class TestMetadataBlockLength(unittest.TestCase):
    """Die Laengenangabe: Bytes (Standard) vs. 16-Byte-Bloecke (DNAS)."""

    MP3 = b"\xff\xfb\x90\x64"

    def _block(self, text: bytes, pad_to: int) -> bytes:
        """Baut einen Metadaten-Block mit Fuellnullen auf ``pad_to``."""
        return text + b"\x00" * (pad_to - len(text))

    def test_dnas_16_byte_variant(self):
        """DNAS 2.5.5: Laenge 4 bedeutet 64 Bytes."""
        text = b"StreamTitle='Alanis Morissette - Thank U (Official Video)';"
        block = self._block(text, 64)
        reader = _FakeReader(block + self.MP3)
        payload = _read_metadata_block(reader, 4)
        self.assertEqual(payload, block)
        self.assertIn(b"Thank U", payload)

    def test_standard_byte_variant(self):
        """Standard: Laenge = Byte-Anzahl."""
        text = b"StreamTitle='Kurz';"
        block = self._block(text, len(text))
        reader = _FakeReader(block + self.MP3)
        payload = _read_metadata_block(reader, len(text))
        self.assertEqual(payload, block)

    def test_laenge_null(self):
        """Laenge 0 -> kein Metadaten-Block."""
        reader = _FakeReader(self.MP3)
        self.assertEqual(_read_metadata_block(reader, 0), b"")

    def test_16_byte_variant_wird_erkannt(self):
        """Der Block muss exakt die 16-Byte-Variante sein, nicht Byte."""
        text = b"StreamTitle='Ein Titel der nicht in 16 Bytes passt';"
        self.assertGreater(len(text), 16)
        block = self._block(text, 64)
        reader = _FakeReader(block + self.MP3)
        payload = _read_metadata_block(reader, 4)
        # Bei Byte-Variante waere nur 4 Bytes gelesen worden --
        # der Text muesste also abgeschnitten sein.
        self.assertEqual(payload, block)
        self.assertTrue(payload.rstrip(b"\x00").endswith(b";"))


class TestUserAgent(unittest.TestCase):
    def test_default_ist_vlc(self):
        """VLC ist der Referenz-Client -- der DNAS antwortt darauf
        mit vollen ICY-Headern inkl. icy-metaint."""
        self.assertIn("VLC", DEFAULT_USER_AGENT)


if __name__ == "__main__":
    unittest.main()
