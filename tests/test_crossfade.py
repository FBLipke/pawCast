"""Messbasierter Test fuer den Crossfade.

Der Test beweist **Amplitude**, nicht nur dass Code aufgerufen wurde:

1. Zwei Sinustoene mit verschiedenen Frequenzen werden zu PCM gebaut.
2. Der Tail des einen und der Head des anderen werden gemischt.
3. Die Amplitude wird ueber die Ueberblendzone gemessen.

Erwartung (equal power):
- Zu Beginn dominiert der ausblendende Ton, am Ende der einblendende.
- Die Gesamtleistung bleibt **konstant** -- es gibt keinen 3-dB-Einbruch
  in der Mitte (das waere der typische "Hohlklang" bei linearem Fade).
- Beide Frequenzen sind in der Mitte gleichzeitig vorhanden (echtes Mischen).

Zusaetzlich: der Test laeuft ohne ffmpeg, damit er schnell und
deterministisch ist. Ein separater Integrationstest deckt den echten
ffmpeg-Pfad ab.
"""

from __future__ import annotations

import math
import os
import sys
import unittest
from array import array

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pawcast.crossfade import mix_equal_power  # noqa: E402

SR = 44100
CHANNELS = 1
FRAME = 2


def tone(freq: float, seconds: float, amp: float = 0.5,
         samplerate: int = SR, channels: int = CHANNELS) -> bytes:
    """Baut einen Sinuston als s16le-PCM."""
    n = int(seconds * samplerate)
    out = array("h", bytes(n * channels * FRAME))
    for i in range(n):
        v = int(amp * 32767 * math.sin(2 * math.pi * freq * i / samplerate))
        for ch in range(channels):
            out[i * channels + ch] = v
    return out.tobytes()


def rms(buf: bytes) -> float:
    """RMS-Amplitude eines PCM-Blocks."""
    if not buf:
        return 0.0
    a = array("h")
    a.frombytes(buf[: len(buf) - (len(buf) % FRAME)])
    if not a:
        return 0.0
    return math.sqrt(sum(float(x) * x for x in a) / len(a))


def goertzel(buf: bytes, freq: float, samplerate: int = SR) -> float:
    """Betrag einer Frequenz im Block (Goertzel) -- ohne numpy."""
    a = array("h")
    a.frombytes(buf[: len(buf) - (len(buf) % FRAME)])
    n = len(a)
    if n == 0:
        return 0.0
    k = int(0.5 + n * freq / samplerate)
    w = 2.0 * math.pi * k / n
    coeff = 2.0 * math.cos(w)
    s0 = s1 = s2 = 0.0
    for x in a:
        s0 = x + coeff * s1 - s2
        s2 = s1
        s1 = s0
    real = s1 - s2 * math.cos(w)
    imag = s2 * math.sin(w)
    return math.sqrt(real * real + imag * imag) / n


class TestMixEqualPower(unittest.TestCase):
    """Die Mischfunktion selbst."""

    def test_ausgabe_hat_rueckwaerts_kompatibles_format(self):
        tail = tone(440, 0.2)
        head = tone(880, 0.2)
        out = mix_equal_power(tail, head)
        self.assertEqual(len(out), len(tail))
        self.assertEqual(len(out) % FRAME, 0)

    def test_kein_overflow(self):
        """Zwei volle Sinuskoerze duerfen nicht ueberlaufen."""
        tail = tone(440, 0.2, amp=0.99)
        head = tone(880, 0.2, amp=0.99)
        out = mix_equal_power(tail, head)
        a = array("h")
        a.frombytes(out)
        self.assertTrue(all(-32768 <= v <= 32767 for v in a))

    def test_leere_blaecke(self):
        self.assertEqual(mix_equal_power(b"", tone(440, 0.1)), b"")
        self.assertEqual(mix_equal_power(tone(440, 0.1), b""), b"")

    def test_verschiedene_laengen(self):
        """Kuerzere Block gewinnt -- kein Absturz."""
        out = mix_equal_power(tone(440, 0.5), tone(880, 0.1))
        self.assertEqual(len(out), len(tone(880, 0.1)))


class TestCrossfadeMessung(unittest.TestCase):
    """Die eigentliche Amplitude-Messung."""

    def test_leistung_bleibt_konstant(self):
        """Equal power: kein 3-dB-Einbruch in der Mitte."""
        tail = tone(440, 2.0, amp=0.5)
        head = tone(880, 2.0, amp=0.5)
        mixed = mix_equal_power(tail, head)

        n = len(mixed)
        seg = n // 10
        amps = [rms(mixed[i * seg:(i + 1) * seg]) for i in range(10)]

        for i, a in enumerate(amps):
            self.assertGreater(a, 100.0,
                               f"Segment {i} ist stumm ({a:.1f}) -- Mix kaputt")

        # Mitte darf nicht deutlich unter dem Anfang liegen (< 70 %).
        mid = min(amps[3:7])
        self.assertGreater(
            mid, amps[0] * 0.7,
            f"Einbruch in der Mitte: Anfang {amps[0]:.0f}, Mitte {mid:.0f}",
        )

    def test_beide_frequenzen_sind_gemischt(self):
        """In der Mitte muessen BEIDE Toene hörbar sein."""
        tail = tone(440, 2.0, amp=0.5)
        head = tone(880, 2.0, amp=0.5)
        mixed = mix_equal_power(tail, head)

        n = len(mixed)
        seg = n // 10
        mid = mixed[4 * seg:6 * seg]

        mag_440 = goertzel(mid, 440)
        mag_880 = goertzel(mid, 880)

        self.assertGreater(mag_440, 100.0, "440-Ton fehlt in der Ueberblendung")
        self.assertGreater(mag_880, 100.0, "880-Ton fehlt in der Ueberblendung")

    def test_ausblendender_ton_verliert(self):
        """Der alte Ton muss am Ende der Zone schwacher werden."""
        tail = tone(440, 2.0, amp=0.5)
        head = tone(880, 2.0, amp=0.5)
        mixed = mix_equal_power(tail, head)

        n = len(mixed)
        seg = n // 10
        early = mixed[0:seg]
        late = mixed[9 * seg:]

        mag_early = goertzel(early, 440)
        mag_late = goertzel(late, 440)
        self.assertLess(mag_late, mag_early,
                        f"440-Ton blendet nicht aus ({mag_early:.0f} -> {mag_late:.0f})")

    def test_einblendender_ton_waechst(self):
        """Der neue Ton muss am Ende der Zone staerker werden."""
        tail = tone(440, 2.0, amp=0.5)
        head = tone(880, 2.0, amp=0.5)
        mixed = mix_equal_power(tail, head)

        n = len(mixed)
        seg = n // 10
        early = mixed[0:seg]
        late = mixed[9 * seg:]

        mag_early = goertzel(early, 880)
        mag_late = goertzel(late, 880)
        self.assertGreater(mag_late, mag_early,
                           f"880-Ton blendet nicht ein ({mag_early:.0f} -> {mag_late:.0f})")

    def test_lineares_fade_haette_einbruch(self):
        """Gegenprobe: lineares Fade bricht ein -- unsere Kurve nicht.

        Das dokumentiert, warum equal power noetig ist.
        """
        tail = tone(440, 2.0, amp=0.5)
        head = tone(880, 2.0, amp=0.5)

        # lineare Mischung zum Vergleich
        a = array("h")
        a.frombytes(tail)
        b = array("h")
        b.frombytes(head)
        lin = array("h", bytes(len(a) * FRAME))
        n = len(a)
        for i in range(n):
            t = (i + 1) / n
            lin[i] = int(a[i] * (1 - t) + b[i] * t)
        lin_bytes = lin.tobytes()

        seg = len(lin_bytes) // 10
        lin_mid = min(rms(lin_bytes[i * seg:(i + 1) * seg]) for i in range(3, 7))
        eq_mid = min(
            rms(mix_equal_power(tail, head)[i * seg:(i + 1) * seg])
            for i in range(3, 7)
        )
        # Equal power liegt messbar ueber dem linearen Fade
        self.assertGreater(eq_mid, lin_mid * 1.05,
                           "Equal power ist nicht besser als linear -- Kurve pruefen")


if __name__ == "__main__":
    unittest.main(verbosity=2)
