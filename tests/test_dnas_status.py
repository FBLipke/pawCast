"""Test: Auslesen des DNAS-Stream-Status.

Der DNAS liefert die Live-Daten (Zuhuerer, Peak, Playing Now/Next) in
seiner HTML-Statusseite. pawCast liest sie aus, damit das WebPanel
dieselben Angaben zeigt wie die DNAS-Seite selbst.
"""

from __future__ import annotations

import sys
import unittest

sys.path.insert(0, "/mnt/openclaw/workspace/pawcast")

from pawcast.dnas import DnasStatus, parse_status  # noqa: E402


#: Statusseite wie beim DNAS v2.6.0.750 (win64) -- mit "Playing Next".
PAGE_260 = """<!DOCTYPE html><html><head><title>SHOUTcast Server</title></head>
<body><table><tr><td class="logo">SHOUTcast Stream Status</td></tr></table>
<table><tr><td class="tsp" align="center">Current Stream Information</td></tr></table>
<table cellpadding="2" cellspacing="0" align="center">
<tr valign="top"><td>Listing Status: </td><td><b>Stream is currently up and private (not listed)</b></td></tr>
<tr valign="top"><td>Stream Status: </td><td><b>Stream is up (MP3 @ 192 kbps, 44.1 kHz) with 3 of 50 listeners</b></td></tr>
<tr valign="top"><td>Listener Peak: </td><td><b>50</b></td></tr>
<tr valign="top"><td>Avg. Play Time: </td><td><b>3 hours 5 minutes 23 seconds</b></td></tr>
<tr valign="top"><td>Stream Name: </td><td><b>The Radio and Ice - Livestream</b></td></tr>
<tr valign="top"><td>Stream Genre(s): </td><td><b>Misc</b></td></tr>
<tr valign="top"><td>Stream Website: </td><td><b><a href="http://the-radio-fire-and-ice.de">the-radio-fire-and-ice.de</a></b></td></tr>
<tr valign="top"><td>Playing Now: </td><td><b><a href="currentsong?sid=1">Bad Boys Blue - Kisses &amp; Tears</a></b></td></tr>
<tr valign="top"><td>Playing Next: </td><td><b><a href="nextsong?sid=1">Chico &amp; Tom Feat. Lee - Where</a></b></td></tr>
</table></body></html>"""

#: Aelterer DNAS v2.5.5.733 -- ohne "Playing Next" und ohne Avg. Play Time.
PAGE_255 = """<!DOCTYPE html><html><head><title>SHOUTcast Server</title></head>
<body><table><tr><td class="tsp">Current Stream Information</td></tr></table>
<table>
<tr valign="top"><td>Listing Status: </td><td><b>Stream is currently up and private (not listed)</b></td></tr>
<tr valign="top"><td>Stream Status: </td><td><b>Stream is up (MP3 @ 128 kbps, 44.1 kHz) with 1 of 50 listeners</b></td></tr>
<tr valign="top"><td>Listener Peak: </td><td><b>2</b></td></tr>
<tr valign="top"><td>Avg. Play Time: </td><td><b>3 hours 5 minutes 48 seconds</b></td></tr>
<tr valign="top"><td>Stream Name: </td><td><b>The Radio Fire and Ice</b></td></tr>
<tr valign="top"><td>Stream Genre(s): </td><td><b>Misc</b></td></tr>
<tr valign="top"><td>Playing Now: </td><td><b><a href="currentsong?sid=1">Alanis Morissette - Thank U (Official Video)</a></b></td></tr>
</table></body></html>"""


class TestParseStatus(unittest.TestCase):
    def test_dnas_260_fields(self):
        st = parse_status(PAGE_260)
        self.assertTrue(st.ok)
        self.assertEqual(st.listeners, 3)
        self.assertEqual(st.max_listeners, 50)
        self.assertEqual(st.listener_peak, "50")
        self.assertEqual(st.bitrate, 192)
        self.assertEqual(st.codec, "MP3")
        self.assertEqual(st.samplerate, "44.1")
        self.assertEqual(st.stream_name, "The Radio and Ice - Livestream")
        self.assertEqual(st.genre, "Misc")
        self.assertEqual(st.now_playing, "Bad Boys Blue - Kisses & Tears")
        self.assertEqual(st.next_playing, "Chico & Tom Feat. Lee - Where")
        self.assertIn("private", st.listing_status)
        self.assertEqual(st.avg_play_time, "3 hours 5 minutes 23 seconds")

    def test_dnas_255_ohne_next(self):
        st = parse_status(PAGE_255)
        self.assertTrue(st.ok)
        self.assertEqual(st.listeners, 1)
        self.assertEqual(st.bitrate, 128)
        self.assertEqual(st.next_playing, "")
        self.assertEqual(st.now_playing,
                         "Alanis Morissette - Thank U (Official Video)")
        self.assertEqual(st.stream_name, "The Radio Fire and Ice")

    def test_website_wird_ententities_entschluesselt(self):
        st = parse_status(PAGE_260)
        self.assertEqual(st.website, "http://the-radio-fire-and-ice.de")

    def test_unbrauchbare_seite(self):
        st = parse_status("<html><body>nix</body></html>")
        self.assertFalse(st.ok)
        self.assertTrue(st.error)


if __name__ == "__main__":
    unittest.main()
