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

from pawcast.metadata import CurrentMetadata, MetadataSender, XML_DECLARATION


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


if __name__ == "__main__":
    unittest.main()
