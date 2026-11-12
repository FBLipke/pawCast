#!/usr/bin/env python3
"""Live-Test: pawcast meldet sich am echten sc_serv an.

Verbindet sich mit dem Source-Login (nur Passwort) und schickt dann
etwas Audio. Getestet wird, ob der DNAS die Quelle akzeptiert.
"""
import logging
import sys
import time

sys.path.insert(0, "/mnt/openclaw/workspace/pawcast")

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

from pawcast.source import ShoutcastSource, SourceError  # noqa: E402

HOST = sys.argv[1] if len(sys.argv) > 1 else "10.232.136.119"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8100
PW = sys.argv[3] if len(sys.argv) > 3 else "my_src_pass"

src = ShoutcastSource(
    host=HOST,
    port=PORT,
    password=PW,
    name="The Radio Fire and Ice",
    genre="Misc",
    url="http://www.shoutcast.com",
    bitrate=128000,
    public=False,
)

print(f"=== Verbinde mit {HOST}:{PORT} als Source ===")
try:
    src.connect(retries=1, retry_delay=1)
    print("=== ANMELDUNG ERFOLGREICH ===")
    print("=== ICY-Block, den wir senden ===")
    print(src._build_info().decode())
    # Ein paar Bytes schicken (der DNAS akzeptiert Stille)
    src.send(b"\xff\xfb\x90\x00" + b"\x00" * 200)
    time.sleep(1)
    print("=== Audio gesendet, Verbindung steht ===")
    src.close()
except SourceError as exc:
    print(f"=== FEHLGESCHLAGEN: {exc} ===")
    sys.exit(1)
