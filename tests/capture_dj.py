#!/usr/bin/env python3
"""Capture: was schickt das Original-``sc_trans`` einem DJ-Client?

Das Original laeuft als **DJ-Server** auf dem ``djport``. Ein echter DJ
(Client) meldet sich dort an. Wir wissen bisher nur, wie der Dialekt
*angenommen* aussieht -- jetzt zeichnen wir die echten Bytes auf.

Der Client macht Folgendes:

1. Verbindet sich auf den DJ-Port.
2. Protokolliert **alles**, was der Server schickt (Hex + repr).
3. Antwortet je nach ``--mode`` automatisch, damit der Server weiterlaeuft:
   ``pass``   -> schickt ``<login>:<passwort>`` (SHOUTcast-Dialekt)
   ``http``   -> schickt einen ``GET /``-Request
   ``none``   -> schweigt (nur zuhören)

Aufruf:
    python3 capture_dj.py <host> <port> <logfile> [--mode pass|http|none]
                          [--login NAME] [--password PW] [--wait SEC]
"""
from __future__ import annotations

import argparse
import socket
import sys
import time


def hexdump(data: bytes, indent: str = "    ") -> str:
    """Formatiert Bytes als Hexdump."""
    lines = []
    for off in range(0, len(data), 16):
        chunk = data[off:off + 16]
        hexpart = " ".join(f"{b:02x}" for b in chunk)
        asciipart = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{indent}{off:04x}  {hexpart:<47}  |{asciipart}|")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("host")
    ap.add_argument("port", type=int)
    ap.add_argument("logfile")
    ap.add_argument("--mode", default="pass", choices=["pass", "http", "none"])
    ap.add_argument("--login", default="admin")
    ap.add_argument("--password", default="asdsad34443")
    ap.add_argument("--wait", type=float, default=6.0)
    args = ap.parse_args()

    logf = open(args.logfile, "w", buffering=1)

    def log(msg: str) -> None:
        logf.write(msg + "\n")
        print(msg, flush=True)

    log(f"### DJ-Capture {args.host}:{args.port} mode={args.mode} "
        f"login={args.login} ###")

    try:
        sock = socket.create_connection((args.host, args.port), timeout=10)
    except OSError as exc:
        log(f"FEHLER: Verbindung fehlgeschlagen: {exc}")
        return 1

    sock.settimeout(args.wait)
    peer = sock.getpeername()
    log(f"Verbunden mit {peer}")

    # Wir schicken erst mal nichts und schauen, ob der Server von sich aus
    # spricht (SHOUTcast: der Server fragt nach dem Passwort).
    pending: bytes = b""
    deadline = time.time() + args.wait
    sent = False

    while time.time() < deadline:
        try:
            data = sock.recv(4096)
        except socket.timeout:
            log("--- Timeout (kein weiterer Datenverkehr) ---")
            break
        except OSError as exc:
            log(f"--- Socket-Fehler: {exc} ---")
            break

        if not data:
            log("--- Server hat die Verbindung geschlossen ---")
            break

        log(f"<<< SERVER -> CLIENT ({len(data)} Bytes)")
        log(hexdump(data))
        log(f"    repr: {data!r}")
        pending += data

        if not sent and args.mode != "none":
            # Auf die Aufforderung reagieren.
            sent = True
            if args.mode == "pass":
                out = f"{args.login}:{args.password}\r\n".encode()
            else:
                out = (b"GET / HTTP/1.0\r\nHost: localhost\r\n"
                       b"User-Agent: DJ-Test\r\n\r\n")
            time.sleep(0.3)
            log(f">>> CLIENT -> SERVER ({len(out)} Bytes)")
            log(hexdump(out))
            log(f"    repr: {out!r}")
            try:
                sock.sendall(out)
            except OSError as exc:
                log(f"Sendefehler: {exc}")
                break

    try:
        sock.close()
    except OSError:
        pass
    log("### Capture beendet ###")
    logf.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
