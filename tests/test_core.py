"""Tests fuer pawcast.

Ausfuehren:  python3 -m pytest tests/ -v
oder:         python3 tests/test_all.py
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pawcast import calendar, config, playlist  # noqa: E402


class TestConfig(unittest.TestCase):
    """Der Config-Parser muss kompatibel zur Original-sc_trans.conf sein."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False)
        self.tmp.write(
            "streamtitle=The Radio Fire and Ice\n"
            "streamurl=http://the-radio-fire-and-ice.de/\n"
            "genre=Misc\n"
            "log=1\n"
            "screenlog=0\n"
            "logfile=/var/log/sc_trans.log\n"
            "playlistfile=/usr/share/transcast/playlists/main.lst\n"
            "xfade=2\n"
            "calendarfile=/usr/share/transcast/calendar/dj_calendar.xml\n"
            "djport=9020\n"
            "adminport=7999\n"
            "adminuser=admin\n"
            "adminpassword=goaway\n"
            "shuffle=1\n"
            "outprotocol_1=1\n"
            "serverip_1=localhost\n"
            "serverport_1=8000\n"
            "password_1=main_src_pass\n"
            "encoder_1=mp3\n"
            "bitrate_1=128000\n"
            "djlogin_1=admin\n"
            "djpassword_1=asdsad34443\n"
            "djpriority_1=9\n"
            "djlogin_2=annalustig\n"
            "djpassword_2=324dffsfds\n"
            "djpriority_2=8\n"
            "bogus_option=whatever\n"
        )
        self.tmp.close()
        self.cfg = config.load(self.tmp.name)

    def tearDown(self):
        os.unlink(self.tmp.name)

    def test_globals(self):
        self.assertEqual(self.cfg.streamtitle, "The Radio Fire and Ice")
        self.assertEqual(self.cfg.genre, "Misc")
        self.assertEqual(self.cfg.djport, 9020)
        self.assertEqual(self.cfg.adminport, 7999)
        self.assertEqual(self.cfg.adminpassword, "goaway")
        self.assertTrue(self.cfg.shuffle)
        self.assertFalse(self.cfg.screenlog)
        self.assertEqual(self.cfg.xfade, 2)

    def test_endpoint(self):
        ep = self.cfg.endpoint(1)
        self.assertIsNotNone(ep)
        self.assertEqual(ep.serverip, "localhost")
        self.assertEqual(ep.serverport, 8000)
        self.assertEqual(ep.password, "main_src_pass")
        self.assertEqual(ep.encoder, "mp3")
        self.assertEqual(ep.bitrate, 128000)
        self.assertEqual(ep.bitrate_kbps, 128)

    def test_djs(self):
        self.assertEqual(len(self.cfg.djs), 2)
        admin = self.cfg.dj("admin")
        self.assertIsNotNone(admin)
        self.assertEqual(admin.password, "asdsad34443")
        self.assertEqual(admin.priority, 9)
        self.assertEqual(self.cfg.dj("annalustig").priority, 8)

    def test_include(self):
        inc = tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False)
        inc.write("djlogin_9=helmi\ndjpassword_9=ninusuraqawo\ndjpriority_9=8\n")
        inc.close()
        main = tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False)
        main.write(f"include={inc.name}\nadminport=7999\n")
        main.close()
        cfg = config.load(main.name)
        self.assertIsNotNone(cfg.dj("helmi"))
        self.assertEqual(cfg.adminport, 7999)
        os.unlink(inc.name)
        os.unlink(main.name)

    def test_kommentar_eigene_zeile(self):
        """Kommentare MUESSEN in eigener Zeile stehen (Original-Bug)."""
        c = tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False)
        c.write("# Kommentar\nadminpassword=geheim\n")
        c.close()
        cfg = config.load(c.name)
        self.assertEqual(cfg.adminpassword, "geheim")
        os.unlink(c.name)

    def test_unknown_keys(self):
        self.assertIn("bogus_option", self.cfg.unknown)


class TestCalendar(unittest.TestCase):
    """Der Kalender-Parser + Writer muss byteweise dem Original entsprechen."""

    ORIGINAL = (
        '<?xml version="1.0" encoding="UTF-8" ?>\n'
        "<eventlist>\n"
        '\t<event type="dj">\n'
        '\t\t<dj archive="inherit" priority="9">admin</dj>\n'
        "\t\t<calendar />\n"
        "\t</event>\n"
        '\t<event type="dj">\n'
        '\t\t<dj archive="inherit" priority="8">annalustig</dj>\n'
        "\t\t<calendar />\n"
        "\t</event>\n"
        '\t<event type="playlist">\n'
        '\t\t<calendar starttime="00:00:00" repeat="128" />\n'
        '\t\t<playlist priority="1" shuffle="inherit" loopatend="1">main</playlist>\n'
        "\t</event>\n"
        "</eventlist>\n"
    )

    def setUp(self):
        # Das Original hat ein UTF-8-BOM -- der Writer muss es auch setzen.
        self.tmp = tempfile.NamedTemporaryFile("wb", suffix=".xml", delete=False)
        self.tmp.write(b"\xef\xbb\xbf" + self.ORIGINAL.encode("utf-8"))
        self.tmp.close()

    def tearDown(self):
        os.unlink(self.tmp.name)

    def test_parse(self):
        cal = calendar.parse(self.tmp.name)
        self.assertEqual(cal.dj_names(), ["admin", "annalustig"])
        self.assertEqual(cal.priority_of("admin"), 9)
        self.assertEqual(cal.priority_of("annalustig"), 8)
        self.assertEqual(len(cal.playlists), 1)
        self.assertEqual(cal.playlists[0].playlist, "main")
        self.assertTrue(cal.playlists[0].loopatend)

    def test_write_byteidentisch(self):
        cal = calendar.parse(self.tmp.name)
        out = tempfile.NamedTemporaryFile(suffix=".xml", delete=False)
        out.close()
        calendar.write(cal, out.name)
        with open(out.name, "rb") as fh:
            written = fh.read()
        with open(self.tmp.name, "rb") as fh:
            original = fh.read()
        # BOM beachten: das Original hat UTF-8-BOM
        if original.startswith(b"\xef\xbb\xbf"):
            self.assertTrue(written.startswith(b"\xef\xbb\xbf"))
            original = original[3:]
            written = written[3:]
        self.assertEqual(original, written)
        os.unlink(out.name)


class TestPlaylist(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile("w", suffix=".lst", delete=False)
        self.tmp.write("# Kommentar\n\n/a.mp3\n/b.mp3\n/c.mp3\n")
        self.tmp.close()

    def tearDown(self):
        os.unlink(self.tmp.name)

    def test_read_lst(self):
        entries = playlist.read_lst(self.tmp.name)
        self.assertEqual(entries, ["/a.mp3", "/b.mp3", "/c.mp3"])

    def test_next_track_reihenfolge(self):
        pl = playlist.Playlist(self.tmp.name, shuffle=False)
        self.assertEqual(len(pl), 3)
        self.assertEqual(pl.next_track(), "/a.mp3")
        self.assertEqual(pl.next_track(), "/b.mp3")
        self.assertEqual(pl.next_track(), "/c.mp3")
        self.assertIsNone(pl.next_track())

    def test_shuffle_alle_vorhanden(self):
        pl = playlist.Playlist(self.tmp.name, shuffle=True, seed=42)
        seen = [pl.next_track() for _ in range(3)]
        self.assertEqual(sorted(seen), ["/a.mp3", "/b.mp3", "/c.mp3"])

    def test_crossfader(self):
        xf = playlist.Crossfader(2)
        self.assertTrue(xf.enabled)
        self.assertEqual(xf.fade_gain(0, 100), 1.0)
        self.assertEqual(xf.fade_gain(100, 100), 0.0)
        self.assertFalse(playlist.Crossfader(0).enabled)


if __name__ == "__main__":
    unittest.main(verbosity=2)
