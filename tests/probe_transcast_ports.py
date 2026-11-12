#!/usr/bin/env python3
"""Was passiert auf den beiden Ports des Original-sc_trans?

Portbase=9020 (config_file):
  9020 = DJ-Port
  9021 = Portbase+1 ("[MICROSERVER] Listening for connection on port 9021")

Wir verbinden uns auf beide Ports, senden NICHTS (kein Audio!),
schliessen sofort wieder und beobachten das Log.

Wichtig: kein Audio senden -- sonst wuerde der Original die Playlist
verlassen und der Produktivstream wuerde unterbrechen.
"""
import socket
import sys
import time

HOST = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"


def probe(port: int, label: str) -> None:
    print(f"\n=== {label}: Port {port} ===")
    try:
        s = socket.create_connection((HOST, port), timeout=5)
    except OSError as exc:
        print(f"  connect fehlgeschlagen: {exc}")
        return
    print("  verbunden")
    s.settimeout(3)
    # Nichts senden. Nur lesen, was der Server von sich gibt.
    try:
        data = s.recv(4096)
        if data:
            print(f"  Server sandte {len(data)} Bytes:")
            print("   ", data[:300])
        else:
            print("  Server sandte nichts, Verbindung geschlossen")
    except socket.timeout:
        print("  Server sandte nichts (Timeout) -> wartet auf Client")
    s.close()
    print("  getrennt (ohne Audio gesendet)")


if __name__ == "__main__":
    time.sleep(1)
    probe(9020, "DJ-Port")
    time.sleep(1)
    probe(9021, "Microserver (portbase+1)")
