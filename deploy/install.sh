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
DJCONF="$CONFDIR/djlogins.conf"

# Die Config erzeugt pawcast selbst -- der Installer hat kein eigenes
# Template. So gibt es genau eine Quelle fuer die Beispiel-Config.
if [ ! -f "$CONFIG" ]; then
    log "Keine Config gefunden -- erzeuge Beispiel: $CONFIG"
    python3 -m pawcast --genconf "$CONFIG" --genconf-djlogins "$DJCONF" \
        || { log "FEHLER: --genconf fehlgeschlagen"; exit 1; }
    log "WICHTIG: Passwoerter in $CONFIG und $DJCONF anpassen!"
fi
chown "$SERVICE_USER:$SERVICE_USER" "$CONFIG"
chmod 0640 "$CONFIG"

if [ -f "$DJCONF" ]; then
    chown "$SERVICE_USER:$SERVICE_USER" "$DJCONF"
    chmod 0600 "$DJCONF"
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
