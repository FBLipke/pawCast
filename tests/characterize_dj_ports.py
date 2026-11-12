#!/usr/bin/env python3
"""Charakterisierung der beiden Ports am Original-sc_trans.

Hypothese: BEIDE Ports (djport und portbase+1/"Microserver") sprechen
das DJ-Protokoll. Beweis: "GETSTREAM" schickt und schauen, ob der
Server nach dem Passwort fragt (NEEDPASS) -- ohne zu authentifizieren,
also ohne eine DJ-Session zu eroeffnen.

Nur gegen die isolierte Instanz auf CT 8002 (19020/19021), NICHT gegen
Produktiv (9020/9021).
"""
import socket
import sys
import time

HOST = "127.0.0.1"


def probe(port: int, label: str) -> None:
    print(f"\n=== {label}: Port {port} ===")
    try:
        s = socket.create_connection((HOST, port), timeout=5)
    except OSError as exc:
        print(f"  connect fehlgeschlagen: {exc}")
        return
    s.settimeout(4)
    try:
        # 1) GETSTREAM anfragen -> Server sollte nach dem Passwort fragen
        s.sendall(b"GETSTREAM\r\n")
        try:
            data = s.recv(4096)
            print(f"  nach GETSTREAM: {len(data)} Bytes -> {data[:160]!r}")
        except socket.timeout:
            print("  nach GETSTREAM: keine Antwort (Timeout)")

        # 2) Absichtlich FALSCHES Passwort -> Server muss ablehnen.
        s.sendall(b"wrongpassword\r\n")
        try:
            data = s.recv(4096)
            print(f"  nach falschem Passwort: {len(data)} Bytes -> {data[:160]!r}")
        except socket.timeout:
            print("  nach falschem Passwort: keine Antwort (Timeout)")
    finally:
        s.close()
        print("  getrennt (nicht authentifiziert -> keine Session)")


if __name__ == "__main__":
    time.sleep(0.5)
    probe(19020, "djport (config_file)")
    time.sleep(0.5)
    probe(19021, "portbase+1 (Microserver)")
    time.sleep(1)
    print("\n=== Log-Tail ===")
    with open("/tmp/orig_sc.log", "r", errors="replace") as fh:
        lines = fh.readlines()
    for line in lines[-12:]:
        print(" ", line.rstrip())
