#!/usr/bin/env python3
"""Härtetest: pawcast streamt ECHTES Audio zum sc_serv.

Wir nehmen eine MP3-Datei, schicken sie als Source zum DNAS und
lauschen parallel als Client auf dem Stream. Damit ist der komplette
Pfad getestet:

    Datei -> Encoder -> Source-Login -> DNAS -> Client
"""
import socket
import sys
import threading
import time

sys.path.insert(0, "/mnt/openclaw/workspace/pawcast")

from pawcast.encoder import Encoder  # noqa: E402
from pawcast.source import ShoutcastSource  # noqa: E402

HOST = "10.232.136.119"
PORT = 8100
PW = "my_src_pass"
CLIENT_PORT = 8100  # der Client holt sich den Stream auf portbase

received = bytearray()
stop = threading.Event()


def listen_client():
    """Holt sich den Stream als normaler Client und sammelt die Bytes."""
    try:
        s = socket.create_connection((HOST, CLIENT_PORT), timeout=15)
        s.settimeout(10)
        req = (
            "GET / HTTP/1.0\r\n"
            f"Host: {HOST}\r\n"
            "User-Agent: TestClient\r\n"
            "Icy-MetaData:1\r\n"
            "\r\n"
        )
        s.sendall(req.encode())
        deadline = time.time() + 20
        while time.time() < deadline and not stop.is_set():
            try:
                chunk = s.recv(8192)
            except socket.timeout:
                break
            if not chunk:
                break
            received.extend(chunk)
        s.close()
    except Exception as exc:
        print(f"[CLIENT] Fehler: {exc}")


def main():
    mp3 = sys.argv[1] if len(sys.argv) > 1 else \
        "/mnt/music-fs/music/Alanis Morissette Thank U Official Video.mp3"

    src = ShoutcastSource(
        host=HOST, port=PORT, password=PW,
        name="The Radio Fire and Ice", genre="Misc",
        url="http://www.shoutcast.com", bitrate=128000, public=False,
    )

    print("=== 1. Source-Login ===")
    src.connect(retries=1, retry_delay=1)
    print("    -> OK2 erhalten, verbunden")

    print("=== 2. Client verbinden (lauscht auf dem Stream) ===")
    t = threading.Thread(target=listen_client, daemon=True)
    t.start()
    time.sleep(3)

    print(f"=== 3. Audio encoden und senden ({mp3}) ===")
    enc = Encoder(128000, 44100, 2)
    total = 0
    t0 = time.time()
    for chunk in enc.encode(mp3):
        src.send(chunk)
        total += len(chunk)
        if time.time() - t0 > 15:
            break
    print(f"    -> {total} Bytes gesendet")

    time.sleep(2)
    stop.set()
    print(f"=== 4. Client hat {len(received)} Bytes empfangen ===")
    if len(received) > 1000:
        print("=== ERGEBNIS: STREAM FUNKTIONIERT ===")
        print("    MP3-Header:", received[:4].hex())
    else:
        print("=== ERGEBNIS: KEIN AUDIO ANGEKOMMEN ===")
    src.close()


if __name__ == "__main__":
    main()
