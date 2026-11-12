#!/usr/bin/env python3
"""Hört den laufenden pawcast-Testdienst ab und prueft den Crossfade.

Der Dienst laeuft auf CT 8002 und streamt zur Test-Instanz des sc_serv
auf dnas0:8100. Dieser Client holt sich den Stream und analysiert die
Ueberblendzone: dort muessen beide Track-Frequenzen gleichzeitig
vorhanden sein (beweis fuer echtes Mischen).
"""
from __future__ import annotations

import math
import os
import socket
import subprocess
import sys
import tempfile
import time
from array import array

HOST = "10.232.136.119"
PORT = 8100
SR = 44100


def goertzel(buf: bytes, freq: float, samplerate: int = SR) -> float:
    a = array("h")
    a.frombytes(buf[: len(buf) - (len(buf) % 2)])
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


def main() -> int:
    s = socket.create_connection((HOST, PORT), timeout=15)
    s.settimeout(5)
    s.sendall((f"GET / HTTP/1.0\r\nHost: {HOST}\r\nUser-Agent: LiveCheck\r\n"
               "Icy-MetaData:1\r\n\r\n").encode())
    data = bytearray()
    deadline = time.time() + 45
    while time.time() < deadline:
        try:
            chunk = s.recv(8192)
        except socket.timeout:
            continue
        if not chunk:
            break
        data.extend(chunk)
        if len(data) > 700000:
            break
    s.close()

    header_end = data.find(b"\r\n\r\n")
    payload = bytes(data[header_end + 4:])
    print(f"HTTP-Header {header_end} Bytes, MP3-Nutzlast {len(payload)} Bytes")
    if len(payload) < 100000:
        print("FEHLER: zu wenig Daten angekommen")
        return 1

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        tmp.write(payload)
        path = tmp.name
    try:
        pcm = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "mp3",
             "-i", path, "-f", "s16le", "-acodec", "pcm_s16le",
             "-ar", str(SR), "-ac", "1", "-"],
            check=True, stdout=subprocess.PIPE).stdout
    finally:
        os.unlink(path)

    print(f"dekodiert: {len(pcm)} Bytes PCM = {len(pcm)/(SR*2):.1f} s")

    # Track 1 = ~130 s, Track 2 = Stille-Buffer (~30 s).
    # Die Ueberblendung liegt am Ende von Track 1.
    # Wir suchen die Zone, in der BEIDE Frequenzen vorkommen.
    seg_len = int(SR * 2 * 2)  # 2 s Bloecke
    best = None
    for start in range(0, max(1, len(pcm) - seg_len), seg_len // 2):
        seg = pcm[start:start + seg_len]
        m_track = goertzel(seg, 440.0)
        m_silence = goertzel(seg, 1000.0)
        score = min(m_track, m_silence)
        if best is None or score > best[0]:
            best = (score, start, m_track, m_silence)

    if best is None:
        print("FEHLER: keine Zone gefunden")
        return 1
    score, start, m_track, m_silence = best
    print(f"beste Zone bei {start/(SR*2):.1f}s: 440Hz={m_track:.0f} "
          f"1000Hz={m_silence:.0f}")

    if m_track > 30 and m_silence > 30:
        print("ERGEBNIS: Crossfade BEWIESEN -- beide Quellen gleichzeitig "
              "in der Ueberblendzone")
        return 0
    print("ERGEBNIS: Crossfade nicht nachweisbar")
    return 1


if __name__ == "__main__":
    sys.exit(main())
