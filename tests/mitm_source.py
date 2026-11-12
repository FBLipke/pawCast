#!/usr/bin/env python3
"""MITM-Proxy: schneidet den kompletten Source-Handshake mit.

Verbindet sich zum echten sc_serv und leitet alles durch, waehrend
BEIDE Richtungen protokolliert werden. Damit sehen wir exakt, was das
Original sendet und was der DNAS antwortet.

Aufruf: python3 mitm.py <listen_port> <ziel_port> <logfile>
"""
import socket
import sys
import threading
import time

LPORT = int(sys.argv[1])
ZPORT = int(sys.argv[2])
LOG = sys.argv[3]

lock = threading.Lock()


def log(msg):
    with lock:
        with open(LOG, "a") as fh:
            fh.write(msg + "\n")
            fh.flush()


def pump(src, dst, tag):
    try:
        while True:
            data = src.recv(4096)
            if not data:
                break
            log(f"<<< {tag} ({len(data)} bytes): {data!r}")
            dst.sendall(data)
    except Exception as exc:
        log(f"<<< {tag} Ende: {exc}")
    finally:
        try:
            dst.shutdown(socket.SHUT_WR)
        except Exception:
            pass


def handle(conn, addr):
    log(f"=== VERBINDUNG {addr} {time.strftime('%H:%M:%S')} ===")
    try:
        up = socket.create_connection(("127.0.0.1", ZPORT), timeout=10)
    except Exception as exc:
        log(f"Ziel nicht erreichbar: {exc}")
        conn.close()
        return

    t1 = threading.Thread(target=pump, args=(conn, up, "CLIENT->DNAS"), daemon=True)
    t2 = threading.Thread(target=pump, args=(up, conn, "DNAS->CLIENT"), daemon=True)
    t1.start()
    t2.start()
    t1.join(timeout=25)
    t2.join(timeout=5)
    log("=== ENDE ===\n")
    conn.close()
    up.close()


def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", LPORT))
    srv.listen(10)
    log(f"### MITM {LPORT} -> {ZPORT} bereit ###")
    while True:
        c, a = srv.accept()
        threading.Thread(target=handle, args=(c, a), daemon=True).start()


if __name__ == "__main__":
    main()
