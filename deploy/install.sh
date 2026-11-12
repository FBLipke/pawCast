#!/bin/bash
# Installation von pawcast auf einem Debian-System.
#
# WICHTIG: Das Zielsystem laeuft danach VOLLSTAENDIG AUTONOM.
# Kein Chat, kein OpenClaw, kein Vaultwarden-Lookup, kein Zutun von Alina.
#
# Aufruf:
#   sudo ./deploy/install.sh [--config /etc/pawcast/sc_trans.conf]
#
set -euo pipefail

PREFIX="${PREFIX:-/opt/pawcast}"
CONFDIR="${CONFDIR:-/etc/pawcast}"
LOGDIR="${LOGDIR:-/var/log/pawcast}"
DATADIR="${DATADIR:-/var/lib/pawcast}"
SERVICE_USER="${SERVICE_USER:-transcast}"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

log() { echo "[install] $*"; }

# ------------------------------------------------------------------
# 1. System-User
# ------------------------------------------------------------------
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    log "Lege System-User '$SERVICE_USER' an"
    useradd --system --no-create-home --home-dir "$DATADIR" \
            --shell /usr/sbin/nologin "$SERVICE_USER"
fi

# ------------------------------------------------------------------
# 2. Verzeichnisse
# ------------------------------------------------------------------
log "Installiere nach $PREFIX"
install -d -m 0755 "$PREFIX"
install -d -m 0755 "$PREFIX/pawcast" "$PREFIX/pawcast/dj" "$PREFIX/docs"
install -d -m 0750 -o "$SERVICE_USER" -g "$SERVICE_USER" "$CONFDIR"
install -d -m 0755 -o "$SERVICE_USER" -g "$SERVICE_USER" "$LOGDIR"
install -d -m 0755 -o "$SERVICE_USER" -g "$SERVICE_USER" "$DATADIR"

cp -a "$REPO_DIR/pawcast"/*.py "$PREFIX/pawcast/"
cp -a "$REPO_DIR/pawcast/dj"/*.py "$PREFIX/pawcast/dj/"
# WebPanel: HTML + Stylesheet (kein Build, keine externen Ressourcen)
cp -a "$REPO_DIR/pawcast"/*.html "$PREFIX/pawcast/"
cp -a "$REPO_DIR/pawcast"/*.css "$PREFIX/pawcast/"
cp -a "$REPO_DIR/docs"/*.md "$PREFIX/docs/" 2>/dev/null || true

# ------------------------------------------------------------------
# 3. Abhaengigkeiten
# ------------------------------------------------------------------
if ! command -v ffmpeg >/dev/null 2>&1; then
    log "ffmpeg fehlt -> installiere"
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ffmpeg
fi

# ------------------------------------------------------------------
# 4. Config
# ------------------------------------------------------------------
CONFIG="$CONFDIR/sc_trans.conf"
if [ ! -f "$CONFIG" ]; then
    log "Keine Config gefunden -- lege Beispiel an: $CONFIG"
    cat > "$CONFIG" <<'EOF'
# pawcast Konfiguration
# KOMMENTARE MUESSEN IN EIGENER ZEILE STEHEN (Original-Eigenheit).
streamtitle=My Radio Stream
streamurl=http://localhost/
genre=Misc

log=1
screenlog=0
logfile=/var/log/pawcast/pawcast.log
logdata=1

playlistfile=/var/lib/pawcast/playlists/main.lst
calendarfile=/var/lib/pawcast/calendar/dj_calendar.xml
xfade=2
shuffle=1

djport=9020
djport2=0
adminport=7999
adminuser=admin
adminpassword=CHANGE_ME

usemetadata=1
nextlookahead=1
displaymetadatapattern=%R[ - ]%N

outprotocol_1=1
serverip_1=127.0.0.1
serverport_1=8000
password_1=CHANGE_ME
encoder_1=mp3
bitrate_1=128000
samplerate_1=44100
channels_1=2
EOF
    log "WICHTIG: Passwoerter in $CONFIG anpassen!"
fi
chown "$SERVICE_USER:$SERVICE_USER" "$CONFIG"
chmod 0640 "$CONFIG"

# DJ-Logins in eine separate Datei (Include)
DJCONF="$CONFDIR/djlogins.conf"
if [ ! -f "$DJCONF" ]; then
    cat > "$DJCONF" <<'EOF'
# DJ-Logins. Format: djlogin_N / djpassword_N / djpriority_N
# Passwoerter hier eintragen (dann chmod 600).
djlogin_1=admin
djpassword_1=CHANGE_ME
djpriority_1=9
EOF
    chown "$SERVICE_USER:$SERVICE_USER" "$DJCONF"
    chmod 0600 "$DJCONF"
fi

# Include verknuepfen, falls nicht vorhanden
if ! grep -q "^include=" "$CONFIG"; then
    echo "include=$DJCONF" >> "$CONFIG"
fi

# Playlists + Kalender
install -d -m 0755 "$DATADIR/playlists" "$DATADIR/calendar"

# ------------------------------------------------------------------
# 5. systemd-Unit
# ------------------------------------------------------------------
log "Installiere systemd-Unit"
install -m 0644 "$REPO_DIR/deploy/pawcast.service" /etc/systemd/system/pawcast.service
systemctl daemon-reload
systemctl enable pawcast.service

# ------------------------------------------------------------------
# 6. Health-Check-Timer (autonome Ueberwachung)
# ------------------------------------------------------------------
install -m 0644 "$REPO_DIR/deploy/pawcast-healthcheck.service" /etc/systemd/system/
install -m 0644 "$REPO_DIR/deploy/pawcast-healthcheck.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now pawcast-healthcheck.timer

# ------------------------------------------------------------------
# 7. Start
# ------------------------------------------------------------------
log "Starte Dienst"
systemctl restart pawcast.service
sleep 3
systemctl --no-pager --lines=15 status pawcast.service || true

log ""
log "==============================================="
log " Installation fertig."
log " Status:   systemctl status pawcast"
log " Logs:     journalctl -u pawcast -f"
log " Health:   systemctl start pawcast-healthcheck.service"
log " Config:   $CONFIG"
log "==============================================="
