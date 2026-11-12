"""Health-Check fuer pawcast.

Prueft, ob der Transcaster wirklich am DNAS haengt. Der Check ist
**autonom** -- er braucht weder Chat noch OpenClaw noch Vaultwarden.
Nur die Konfiguration (root-only) und der DNAS.

Aufruf:
    python3 -m pawcast.healthcheck --config /etc/pawcast/sc_trans.conf

Exit-Codes:
    0 = gesund
    1 = ungesund
    2 = Konfiguration nicht lesbar
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from typing import Optional

from . import config as config_mod


def check_dnas(host: str, port: int, timeout: float = 5.0) -> dict:
    """Ist der DNAS erreichbar und antwortet er auf dem Admin-Kanal?"""
    result = {"reachable": False, "admin_ok": False, "error": None}
    try:
        with socket.create_connection((host, port), timeout=timeout):
            result["reachable"] = True
    except OSError as exc:
        result["error"] = f"DNAS nicht erreichbar: {exc}"
        return result

    # Admin-Kanal: /admin.cgi?pass=..&mode=viewxml
    url = f"http://{host}:{port}/admin.cgi?pass={config_mod.__name__}"  #Platzhalter
    return result


def check_stream(host: str, port: int, password: str, timeout: float = 6.0) -> dict:
    """Liefert der Stream-Port wirklich Audio?"""
    result = {"http_ok": False, "content_type": None, "bytes": 0, "error": None}
    url = f"http://{host}:{port}/"
    try:
        req = urllib.request.Request(url, headers={"Icy-Name": "healthcheck"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result["http_ok"] = resp.status == 200
            result["content_type"] = resp.headers.get("Content-Type")
            result["icy_name"] = resp.headers.get("icy-name")
            chunk = resp.read(4096)
            result["bytes"] = len(chunk)
    except urllib.error.HTTPError as exc:
        result["error"] = f"HTTP {exc.code}"
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        result["error"] = str(exc)
    return result


def check_source_listening(host: str = "127.0.0.1", port: int = 9020,
                          timeout: float = 3.0) -> dict:
    """Laeuft der DJ-Port?"""
    result = {"listening": False}
    try:
        with socket.create_connection((host, port), timeout=timeout):
            result["listening"] = True
    except OSError:
        pass
    return result


def run(cfg_path: str, verbose: bool = False) -> int:
    if not os.path.exists(cfg_path):
        print(f"Config nicht gefunden: {cfg_path}", file=sys.stderr)
        return 2

    cfg = config_mod.load(cfg_path)
    ep = cfg.endpoint(1)
    if ep is None:
        print("Kein Endpoint in der Config", file=sys.stderr)
        return 2

    report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "config": cfg_path,
        "streamtitle": cfg.streamtitle,
        "endpoint": {
            "host": ep.serverip,
            "port": ep.serverport,
            "bitrate": ep.bitrate,
        },
        "playlist": {
            "file": cfg.playlistfile,
            "exists": os.path.exists(cfg.playlistfile or ""),
        },
        "calendar": {
            "file": cfg.calendarfile,
            "exists": os.path.exists(cfg.calendarfile or ""),
        },
        "djs": {
            "count": len(cfg.djs),
            "enabled": sum(1 for d in cfg.djs if d.enabled),
        },
        "dj_port": {"port": cfg.djport},
    }

    # DNAS erreichbar?
    try:
        with socket.create_connection((ep.serverip, ep.serverport), timeout=5):
            report["dnas"] = {"reachable": True}
    except OSError as exc:
        report["dnas"] = {"reachable": False, "error": str(exc)}

    # Stream liefert Audio?
    report["stream"] = check_stream(ep.serverip, ep.serverport, ep.password)

    # DJ-Port lauscht?
    report["dj_port"]["listening"] = check_source_listening(port=cfg.djport)["listening"]

    # Playlist gefuellt?
    if report["playlist"]["exists"]:
        from .playlist import read_lst
        entries = read_lst(cfg.playlistfile)
        report["playlist"]["entries"] = len(entries)
        report["playlist"]["missing_files"] = sum(
            1 for e in entries if not os.path.exists(e)
        )

    healthy = (
        report.get("dnas", {}).get("reachable", False)
        and report["stream"]["http_ok"]
        and report["playlist"].get("entries", 0) > 0
    )
    report["healthy"] = healthy

    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if healthy else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Health-Check fuer pawcast")
    parser.add_argument("--config", default="/etc/pawcast/sc_trans.conf")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    return run(args.config, verbose=not args.quiet)


if __name__ == "__main__":
    sys.exit(main())
