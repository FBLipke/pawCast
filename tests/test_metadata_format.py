"""Test: Metadaten-XML in der Form des Original-sc_trans.

Verifiziert am Original-Binary ``/usr/share/transcast/sc_trans``
(Strings-Region 0x75ba20-0x75bb00, Builder-Funktion 0x4d8eb0,
12.11.2026).

Das Original liefert das Metadaten-XML ueber den Weblet-Server aus --
Pfade ``/metadata/song/artist``, ``/metadata/song/album``,
``/metadata/song/name`` (sowie das ganze Dokument unter ``/metadata``).
"""

from __future__ import annotations

import sys
import unittest
from unittest import mock

sys.path.insert(0, "/mnt/openclaw/workspace/pawcast")

from pawcast.metadata import (CurrentMetadata, IcyMetadataInjector,
                                MetadataSender, XML_DECLARATION)


class TestCurrentMetadataXml(unittest.TestCase):
    """Das XML-Dokument des Originals."""

    def setUp(self):
        self.md = CurrentMetadata(
            artist="DJ Bobo",
            title="Streetlife",
            album="Greatest Hits",
            comment="",
            genre="Pop",
            year="2005",
        )
        self.xml = self.md.to_xml()

    def test_xml_declaration(self):
        self.assertTrue(self.xml.startswith(XML_DECLARATION))
        self.assertEqual(XML_DECLARATION,
                         '<?xml version="1.0" encoding="UTF-8" ?>')

    def test_outer_tags(self):
        self.assertIn("<metadata>", self.xml)
        self.assertIn("</metadata>", self.xml)
        self.assertIn("\t<song>", self.xml)
        self.assertIn("\t</song>", self.xml)

    def test_length_element(self):
        # Das Original schreibt "<length>0</length>" mit Tab-Einrueckung
        self.assertIn("\t<length>0</length>", self.xml)

    def test_fields_present(self):
        self.assertIn("<name>Streetlife</name>", self.xml)
        self.assertIn("<artist>DJ Bobo</artist>", self.xml)
        self.assertIn("<album>Greatest Hits</album>", self.xml)
        self.assertIn("<genre>Pop</genre>", self.xml)
        self.assertIn("<year>2005</year>", self.xml)
        self.assertIn("<comment></comment>", self.xml)

    def test_field_order(self):
        """Die Reihenfolge im Original: name, artist, album, comment, genre, year."""
        order = [self.xml.index(t) for t in (
            "<name>", "<artist>", "<album>", "<comment>", "<genre>", "<year>")]
        self.assertEqual(sorted(order), order)

    def test_escaping(self):
        md = CurrentMetadata(artist="A & B", title="C < D > E")
        xml = md.to_xml()
        self.assertIn("A &amp; B", xml)
        self.assertIn("C &lt; D &gt; E", xml)

    def test_field_value_xpath(self):
        """Die XPath-artigen Feldnamen des Originals."""
        self.assertEqual(self.md.field_value("artist"), "DJ Bobo")
        self.assertEqual(self.md.field_value("album"), "Greatest Hits")
        self.assertEqual(self.md.field_value("name"), "Streetlife")
        self.assertEqual(self.md.field_value("unknown"), "")


class TestMetadataSender(unittest.TestCase):
    """Der MetadataSender schickt nur ``song=`` -- kein ``xml=``."""

    def _make(self):
        return MetadataSender("127.0.0.1", 8000, "pw")

    def test_uses_song_param_only(self):
        sender = self._make()
        with mock.patch("urllib.request.urlopen") as m:
            m.return_value.__enter__.return_value.read.return_value = b"OK"
            sender.send_title("A", "T")
            url = m.call_args[0][0].full_url
            self.assertIn("mode=updinfo", url)
            self.assertIn("song=", url)
            # Der Original kennt KEIN xml= an den DNAS
            self.assertNotIn("xml=", url)

    def test_user_agent(self):
        sender = self._make()
        with mock.patch("urllib.request.urlopen") as m:
            m.return_value.__enter__.return_value.read.return_value = b"OK"
            sender.send_title("A", "T")
            req = m.call_args[0][0]
            self.assertEqual(req.get_header("User-agent"),
                             "TRANScast Title Update(Mozilla)")

    def test_empty_returns_false(self):
        sender = self._make()
        self.assertFalse(sender.send_title("", ""))

    def test_set_current_updates_xml(self):
        sender = self._make()
        sender.set_current("Artist X", "Title Y")
        self.assertIn("<artist>Artist X</artist>", sender.metadata_xml())
        self.assertIn("<name>Title Y</name>", sender.metadata_xml())


class TestIcyMetadataInjector(unittest.TestCase):
    """Die Laengen-Semantik des Metadaten-Blocks.

    Der sc_serv 2.5.5 zaehlt in 16-Byte-Bloecken, der SHOUTcast-
    Standard in Bytes. Der Default muss der DNAS-Variante entsprechen,
    sonst laeuft der Stream auseinander.
    """

    def _injector(self, mode="dnas"):
        inj = IcyMetadataInjector(metaint=16384, length_mode=mode)
        inj.set_title("Alanis Morissette", "Thank U (Official Video)")
        return inj

    def test_dnas_modus_16_byte_raster(self):
        block = self._injector("dnas")._metadata_block()
        length = block[0]
        payload = block[1:1 + length * 16]
        # Der Block muss auf 16 Byte aufgefuellt sein
        self.assertEqual(len(payload) % 16, 0)
        # und der Text muss vollstaendig drin sein
        self.assertTrue(payload.rstrip(b"\x00").endswith(b";"))
        self.assertIn(b"Thank U (Official Video)", payload)

    def test_dnas_laenge_stimmt_zum_block(self):
        """Laengenbyte * 16 muss genau die Nutzlast abdecken."""
        block = self._injector("dnas")._metadata_block()
        length = block[0]
        payload = block[1:1 + length * 16]
        text = payload.rstrip(b"\x00")
        # Der Text ist kuerzer als der Block, aber nicht abgeschnitten
        self.assertLess(len(text), length * 16)
        self.assertGreater(len(text), (length - 1) * 16)

    def test_byte_modus_ist_standard(self):
        """Im Byte-Modus ist das Laengenbyte die Byte-Anzahl."""
        inj = IcyMetadataInjector(metaint=16384, length_mode="bytes")
        inj.set_title("Kurz", "Titel")
        block = inj._metadata_block()
        payload = block[1:1 + block[0]]
        self.assertEqual(payload.rstrip(b"\x00"),
                         b"StreamTitle='Kurz - Titel';")

    def test_unbekannter_modus_wirft(self):
        with self.assertRaises(ValueError):
            IcyMetadataInjector(length_mode="quatsch")

    def test_inject_zerlegt_den_strom(self):
        """Nach metaint Bytes kommt der Metadaten-Block."""
        inj = IcyMetadataInjector(metaint=1000, length_mode="dnas")
        inj.set_title("A", "B")
        out = inj.inject(b"\xff\xfb" * 500)   # 1000 Bytes
        self.assertGreater(len(out), 1000)
        # direkt nach 1000 Audio-Bytes muss der Block kommen
        self.assertEqual(out[1000], inj._metadata_block()[0])

    def test_metaint_null_ist_noop(self):
        inj = IcyMetadataInjector(metaint=0)
        self.assertEqual(inj.inject(b"abc"), b"abc")


if __name__ == "__main__":
    unittest.main()
