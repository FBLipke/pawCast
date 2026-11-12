#!/usr/bin/env python3
"""Probe: Welcher GET-Pfad akzeptiert der sc_serv als Source?

Testet nacheinander mehrere Kandidaten gegen den echten DNAS und
protokolliert die Antwort. Es werden KEINE Audiodaten gesendet --
getestet wird nur der Login-Handshake.
"""
import socket
import sys

HOST = "10.232.136.119"
PORT = 8000

CANDIDATES = [
    ("/stream/test/?pass=test_src_pass", "test_src_pass", "stream/test"),
    ("/stream/main/?pass=main_src_pass", "main_src_pass", "stream/main"),
    ("/?pass=main_src_pass", "main_src_pass", "root"),
    ("/?pass=test_src_pass", "test_src_pass", "root"),
]

TEMPLATE = (
    "GET {path} HTTP/1.0\r\n"
    "Host: {host}\r\n"
    "User-Agent: SHOUTcast Server\r\n"
    "Accept: */*\r\n"
    "Icy-MetaData:1\r\n"
    "Connection: close\r\n"
    "\r\n"
)


def probe(path):
    req = TEMPLATE.format(path=path, host=HOST).encode()
    try:
        s = socket.create_connection((HOST, PORT), timeout=8)
    except Exception as exc:
        return f"CONNECT-FAIL: {exc}"
    try:
        s.sendall(req)
        s.settimeout(8)
        data = b""
        try:
            while len(data) < 2048:
                chunk = s.recv(1)
                if not chunk:
                    break
                data += chunk
                if b"\r\n\r\n" in data:
                    break
        except socket.timeout:
            data += b"<TIMEOUT>"
        return data.decode("utf-8", "replace")
    finally:
        s.close()


for path, pw, label in CANDIDATES:
    print("=" * 70)
    print(f"Kandidat: {label}   Pfad: {path}")
    print("-" * 70)
    resp = probe(path)
    # Nur die Header-Zeilen zeigen, nicht die Binaer-Daten danach
    head = resp.split("\r\n\r\n")[0]
    for line in head.splitlines():
        print("   ", line)
    if "\r\n\r\n" in resp:
        rest = resp.split("\r\n\r\n", 1)[1]
        if rest:
            print("    [Rest:", repr(rest[:120]), "]")
    print()
