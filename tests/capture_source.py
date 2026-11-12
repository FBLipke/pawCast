#!/usr/bin/env python3
"""Capture-Server: nimmt Source-Verbindungen an und protokolliert EXAKT
das, was der Client schickt.

Antwortet mit "HTTP/1.0 200 OK" plus ICY-Headern, damit der Client
(Original oder pawcast) denkt, er sei angemeldet, und den
ICY-Header-Block nachschickt.

Aufruf:  python3 capture.py <port> <logfile>
"""
import socket
import sys
import threading
import time

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 18000
LOG = sys.argv[2] if len(sys.argv) > 2 else "/root/sctest/capture.log"

lock = threading.Lock()


def log(msg):
    with lock:
        with open(LOG, "a") as fh:
            fh.write(msg + "\n")
            fh.flush()
        print(msg, flush=True)


def handle(conn, addr):
    log(f"=== VERBINDUNG von {addr} um {time.strftime('%H:%M:%S')} ===")
    conn.settimeout(10)
    data = b""
    try:
        # Erste Phase: der GET-Request (endet mit \r\n\r\n)
        while b"\r\n\r\n" not in data and len(data) < 8192:
            chunk = conn.recv(1)
            if not chunk:
                break
            data += chunk
    except socket.timeout:
        log("TIMEOUT in Phase 1")
    except Exception as exc:
        log(f"Fehler Phase 1: {exc}")

    log("--- PHASE 1: GET-Request (roh) ---")
    log(repr(data))

    # Antwort wie ein DNAS
    resp = (
        "HTTP/1.0 200 OK\r\n"
        "icy-notice2:SHOUTcast DNAS/posix(linux x64) v2.5.5.733<BR>\r\n"
        "Accept-Ranges:none\r\n"
        "Connection:close\r\n"
        "icy-name:Capture\r\n"
        "icy-genre:Test\r\n"
        "icy-br:128\r\n"
        "icy-sr:44100\r\n"
        "icy-url:http://capture/\r\n"
        "icy-pub:0\r\n"
        "content-type:audio/mpeg\r\n"
        "icy-metaint:16384\r\n"
        "\r\n"
    )
    try:
        conn.sendall(resp.encode())
    except Exception as exc:
        log(f"Fehler beim Antworten: {exc}")

    # Zweite Phase: der ICY-Header-Block
    try:
        conn.settimeout(10)
        data2 = b""
        while len(data2) < 4096:
            chunk = conn.recv(1)
            if not chunk:
                break
            data2 += chunk
            # Ende, wenn zwei Leerzeilen kommen
            if data2.endswith(b"\r\n\r\n") or data2.endswith(b"\n\n"):
                break
    except socket.timeout:
        log("TIMEOUT in Phase 2 (erwartet: Client sendet nur Header, keine Daten)")
    except Exception as exc:
        log(f"Fehler Phase 2: {exc}")

    log("--- PHASE 2: ICY-Header-Block (roh) ---")
    log(repr(data2))
    log("--- PHASE 2: als Text ---")
    log(data2.decode("utf-8", "replace"))
    log("=== ENDE VERBINDUNG ===\n")

    try:
        conn.close()
    except Exception:
        pass


def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", PORT))
    srv.listen(10)
    log(f"### Capture-Server auf Port {PORT} bereit ###")
    while True:
        conn, addr = srv.accept()
        threading.Thread(target=handle, args=(conn, addr), daemon=True).start()


if __name__ == "__main__":
    main()
