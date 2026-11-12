#!/bin/bash
# Findet heraus, welcher Source-Login vom DNAS als SOURCE akzeptiert wird.
#
# Der DNAS unterscheidet Source (SRC) von Client (DST). Ein 401 oder
# "HTTP client connection rejected" heisst: der DNAS hat den Request als
# Client-Aufruf behandelt. Wir probieren Varianten durch und werten
# danach das sc_serv.log aus.

LOG=/usr/share/shoutcast/logs/sc_serv.log
OUT=/tmp/source_variants.log
: > "$OUT"

try() {
    local label="$1"; shift
    local request="$1"
    local before
    before=$(grep -c "" "$LOG" 2>/dev/null || echo 0)
    printf "%b" "$request" | timeout 5 python3 -c "
import socket,sys
s=socket.create_connection(('127.0.0.1',8000),timeout=5)
s.sendall(sys.stdin.buffer.read())
try:
    s.settimeout(3)
    d=s.recv(500)
    print(repr(d[:160]))
except Exception as e:
    print('TIMEOUT/ERR',e)
s.close()
" >> "$OUT" 2>&1
    sleep 1
    local new
    new=$(tail -n +$((before+1)) "$LOG" 2>/dev/null | grep -iE "SRC|DST|source connection|rejected|401|Invalid" | head -2)
    echo "--- $label"
    echo "    Antwort: $(tail -1 "$OUT")"
    echo "    Log:     $new"
}

echo "=== Varianten des Source-Logins ==="

try "A: GET /?pass= (ohne UA)" \
  "GET /?pass=main_src_pass HTTP/1.0\r\n\r\n"

try "B: mit User-Agent pawcast" \
  "GET /?pass=main_src_pass HTTP/1.0\r\nUser-Agent: pawcast/2.0.0.54\r\n\r\n"

try "C: mit User-Agent SHOUTcast Transcoder" \
  "GET /?pass=main_src_pass HTTP/1.0\r\nUser-Agent: SHOUTcast Transcoder\r\n\r\n"

try "D: mit Icy-Headern" \
  "GET /?pass=main_src_pass HTTP/1.0\r\nUser-Agent: SHOUTcast Transcoder\r\nIcy-Name: Test\r\nIcy-Genre: Misc\r\nIcy-Url: http://x/\r\nIcy-Pub: 0\r\nIcy-Br: 128\r\n\r\n"

try "E: /stream/main/?pass=" \
  "GET /stream/main/?pass=main_src_pass HTTP/1.0\r\nUser-Agent: SHOUTcast Transcoder\r\nIcy-Name: Test\r\n\r\n"

try "F: mit Host-Header" \
  "GET /?pass=main_src_pass HTTP/1.0\r\nHost: localhost\r\nUser-Agent: SHOUTcast Transcoder\r\n\r\n"

try "G: HTTP/1.1 mit Host" \
  "GET /?pass=main_src_pass HTTP/1.1\r\nHost: localhost:8000\r\nUser-Agent: SHOUTcast Transcoder\r\nIcy-Name: Test\r\nIcy-Br: 128\r\n\r\n"

try "H: mit Source-Pfad-Prefix /?mode=source" \
  "GET /?pass=main_src_pass&mode=source HTTP/1.0\r\nUser-Agent: SHOUTcast Transcoder\r\n\r\n"

echo
echo "=== Log-Auszug (neu) ==="
grep -iE "SRC|DST|source connection|rejected|401|Invalid" "$LOG" | tail -12
