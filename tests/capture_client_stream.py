#!/usr/bin/env python3
"""Client-Mitschnitt: holt sich den Stream und speichert die ersten Bytes.

Damit vergleichen wir Original und pawcast byteweise.
"""
import socket
import sys
import time

HOST = "10.232.136.119"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8100
OUT = sys.argv[2] if len(sys.argv) > 2 else "/tmp/stream.bin"
SECS = float(sys.argv[3]) if len(sys.argv) > 3 else 12.0

s = socket.create_connection((HOST, PORT), timeout=15)
s.settimeout(8)
s.sendall(
    (
        "GET / HTTP/1.0\r\n"
        f"Host: {HOST}\r\n"
        "User-Agent: CaptureClient\r\n"
        "Icy-MetaData:1\r\n"
        "\r\n"
    ).encode()
)

data = bytearray()
t0 = time.time()
while time.time() - t0 < SECS:
    try:
        chunk = s.recv(65536)
    except socket.timeout:
        break
    if not chunk:
        break
    data.extend(chunk)
s.close()

with open(OUT, "wb") as fh:
    fh.write(bytes(data))

print(f"Empfangen: {len(data)} Bytes -> {OUT}")
# Header und der Bereich um den ersten MP3-Frame-Sync zeigen
print("--- erste 200 Bytes als Hex ---")
print(data[:200].hex())
print("--- als Text (druckbar) ---")
print(repr(bytes(data[:200])))
