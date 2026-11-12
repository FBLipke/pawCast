#!/bin/bash
# Testlauf auf dem DEV-Container: Original-sc_trans Source-Request mitschreiben.
set -x
pkill -f "python3 -m pawcast" 2>/dev/null
pkill -f "pawcast" 2>/dev/null
pkill -f "proxy.py" 2>/dev/null
sleep 2

cd /usr/share/transcast || exit 1

# 1. Leere Playlist -> gueltige Datei mit einem existierenden MP3
mkdir -p /tmp/devmusic
if [ ! -f /tmp/devmusic/track.mp3 ]; then
  ffmpeg -f lavfi -i "sine=frequency=440:duration=60" -c:a libmp3lame -b:a 128k -y /tmp/devmusic/track.mp3
fi
printf "/tmp/devmusic/track.mp3\n" > /tmp/active.lst

cp sc_trans.conf /tmp/sc_trans.proxy.conf
sed -i 's|^serverport_1=.*|serverport_1=18000|' /tmp/sc_trans.proxy.conf
sed -i 's|^serverip_1=.*|serverip_1=127.0.0.1|' /tmp/sc_trans.proxy.conf
sed -i 's|^playlistfile=.*|playlistfile=/tmp/active.lst|' /tmp/sc_trans.proxy.conf
sed -i 's|^adminport=.*|adminport=17999|' /tmp/sc_trans.proxy.conf
sed -i 's|^djport=.*|djport=19020|' /tmp/sc_trans.proxy.conf
sed -i 's|^logfile=.*|logfile=/tmp/orig_sc.log|' /tmp/sc_trans.proxy.conf
grep -E "serverport_1|serverip_1|password_1|playlistfile|logfile" /tmp/sc_trans.proxy.conf

: > /tmp/proxy.log
nohup python3 /opt/pawcast/tests/proxy.py 18000 127.0.0.1 8000 /tmp/proxy.log \
  > /tmp/proxy.out 2>&1 &
sleep 2

nohup ./pawcast /tmp/sc_trans.proxy.conf > /tmp/orig2.log 2>&1 &
sleep 18

echo "--- proxy.log (DER REQUEST) ---"
cat /tmp/proxy.log
echo "--- orig_sc.log ---"
tail -15 /tmp/orig_sc.log 2>/dev/null
