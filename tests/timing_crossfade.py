"""Timing: wie lange dauert es, bis der erste MP3-Byte aus dem Player kommt?

Der DNAS (sc_serv 2.5.5) trennt die Quelle, wenn er keine Stream-Daten
bekommt ("Timeout waiting for stream data"). Deshalb muss die Latenz
zwischen Player-Start und erstem Byte klein sein.

Dieses Skript misst die Zeit bis zum ersten Chunk und die Abstände
der folgenden Chunks.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pawcast.crossfade import CrossfadePlayer  # noqa: E402

SR = 44100


def make_tone_mp3(path: str, freq: float, seconds: float) -> None:
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
        "-ac", "2", "-ar", str(SR), "-b:a", "128k",
        "-id3v2_version", "0", "-write_xing", "0", path,
    ], check=True)


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="sctiming-")
    a = os.path.join(tmp, "a.mp3")
    b = os.path.join(tmp, "b.mp3")
    make_tone_mp3(a, 440.0, 8.0)
    make_tone_mp3(b, 880.0, 8.0)

    player = CrossfadePlayer(bitrate=128000, samplerate=SR, channels=2, xfade=2)
    t0 = time.monotonic()
    first = None
    stamps = []
    total = 0
    for chunk in player.stream(iter([a, b])):
        now = time.monotonic()
        stamps.append((now - t0, len(chunk)))
        if first is None:
            first = now - t0
            print(f"ERSTES Byte nach {first:.3f}s ({len(chunk)} Bytes)")
        total += len(chunk)
        if total > 400000:
            break
    player.stop()

    print(f"gesamt {total} Bytes in {stamps[-1][0]:.2f}s")
    print("erste 8 Chunks (t, Bytes):")
    for t, n in stamps[:8]:
        print(f"  {t:7.3f}s  {n}")
    print("letzte 5 Chunks:")
    for t, n in stamps[-5:]:
        print(f"  {t:7.3f}s  {n}")

    gaps = [stamps[i + 1][0] - stamps[i][0] for i in range(len(stamps) - 1)]
    if gaps:
        print(f"max. Luecke zwischen Chunks: {max(gaps):.3f}s")
        print(f"maessiger Rhythmus: {sum(gaps)/len(gaps):.3f}s")

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
