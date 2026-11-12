"""Fake-DJ: sendet kurz Audio, dann schweigt.

Das reproduziert exakt die Situation aus den Produktivlogs:

  12:24:16  Relay zu x-effekt beendet: Timeout: DJ sendet seit 30s nichts
  12:24:17  STREAM Verbindung verloren: DNAS hat die Verbindung getrennt

Ablauf:
  1. Login am DJ-Port
  2. ICY-Header senden
  3. Audio senden -> der DJ wird "live", der Relay startet
  4. Stille -> der Stille-Timeout muss greifen

Erwartet mit dem Fix (SILENCE_TIMEOUT=6s):
  * pawCast erkennt die Stille nach ~6s
  * pawCast faellt geordnet auf die Playlist zurueck
  * die Source-Verbindung zum DNAS bleibt LEBEND
    (kein "Verbindung verloren", kein "Timeout waiting for stream data")
"""

import socket
import sys
import time

HOST = "127.0.0.1"
PORT = 9030
PASSWORD = sys.argv[1] if len(sys.argv) > 1 else "alina-test:alina_test_pw"
AUDIO_SECONDS = float(sys.argv[2]) if len(sys.argv) > 2 else 3.0
SILENCE_SECONDS = float(sys.argv[3]) if len(sys.argv) > 3 else 25.0

# Echtes MP3 verwenden -- der DNAS muss den Stream sonst syncen
# ("Unable to sync to the stream"), sonst bricht er die Verbindung ab.
MP3_PATH = "/var/lib/sc-trans/music/tone1000.mp3"

with open(MP3_PATH, "rb") as fh:
    MP3 = fh.read()
print("MP3 geladen: %d Bytes" % len(MP3), flush=True)

s = socket.create_connection((HOST, PORT), timeout=15)
print("verbunden", flush=True)

s.sendall((PASSWORD + "\r\n").encode())
time.sleep(1)

try:
    data = s.recv(4096)
    print("Server-Antwort:", data[:120], flush=True)
except Exception as exc:
    print("keine Antwort:", exc, flush=True)

s.sendall(b"icy-name:TestDJ\r\nicy-genre:Test\r\nicy-br:128\r\n"
          b"icy-pub:0\r\n\r\n")
print("ICY-Header gesendet", flush=True)

# --- Phase 1: Audio senden, damit der DJ live wird ---
# Echte MP3-Frames, in Echtzeit getaktet (128 kbps = 16 kB/s).
BYTES_PER_SEC = 16000
sent = 0
end = time.time() + AUDIO_SECONDS
while time.time() < end:
    s.sendall(MP3[sent % len(MP3):(sent % len(MP3)) + 4000])
    sent += 4000
    time.sleep(4000 / BYTES_PER_SEC)
print("Phase 1: %d Bytes MP3 gesendet -> DJ ist live" % sent, flush=True)

# --- Phase 2: Stille ---
print("Phase 2: jetzt %ds Stille (kein Byte mehr)" % SILENCE_SECONDS, flush=True)
time.sleep(SILENCE_SECONDS)
print("ende", flush=True)
s.close()
