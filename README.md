# pawcast

Python-Ersatz für **SHOUTcast Transcaster v2** (`sc_trans` 2.0.0.54, Nullsoft 2011).

Der Original-Transcaster ist ein closed-source ELF-Binary mit eingebautem
Fraunhofer-AAC/MP3-Encoder (Lizenzkey-pflichtig). Dieses Projekt baut eine
moderne, wartbare Alternative, die die **betrieblich relevanten Features**
des Originals nachbildet:

| Feature | Original | pawcast |
|---|---|---|
| AutoDJ-Playlist (Shuffle, Crossfade) | ✅ | `playlist.py` |
| Jingles / Ansagen | ✅ | `playlist.py` |
| ICY-Metadaten (`StreamTitle`) | ✅ | `metadata.py` |
| Source-Anmeldung am DNAS (SHOUTcast v1) | ✅ | `source.py` |
| **DJ-Login (UVOX-Protokoll)** | ✅ | `dj/uvox.py` |
| **DJ-Login (SHOUTcast-Protokoll)** | ✅ | `dj/shoutcast.py` |
| **DJ-Relay** (DJ-Audio durchreichen) | ✅ | `dj/relay.py` |
| **Fallback** (DJ weg → Playlist läuft weiter) | ✅ | `dj/manager.py` |
| Kalender/Scheduling (`dj_calendar.xml`) | ✅ | `calendar.py` |
| Fraunhofer-AAC-Encoder | ✅ (Lizenz) | `encoder.py` (ffmpeg/lame) |
| Weblet-API (7999) | buggy | `api.py` |
| WebPanel im DNAS-Like-Look | – | `panel.html` + `panel.css` |
| Beliebige Audio-Formate in der Playlist | ✅ | `encoder.py` + `playlist.py` |

## Warum?

* `sc_trans` ist von 2011, closed source, nur als Binary verfügbar.
* Der eingebaute Encoder braucht einen Lizenzkey (`unlockkeyname`/`unlockkeycode`), ffmpeg braucht das nicht (mehr).
* Die Weblet-API im sc_trans ist defekt (`/admin.cgi`, `/djlogins`, `/status` → 404) und muss wie ein rohes Ei behandelt werden.
* pawCast nutzt ffmpeg und kann also in jedes Format konvertieren, in was ffmpeg als Audio-Output unterstützt.

## DJ-Protokoll

Das DJ-Protokoll ist **proprietär** und nicht öffentlich dokumentiert. Die
Implementierung folgt dem beobachteten Verhalten des DNAS- und
DJ-Ports; die Byte-Sequenzen sind in
[`docs/dj-protocol-spec.md`](docs/dj-protocol-spec.md) spezifiziert und
gegen das Original verifiziert.

Zwei Varianten existieren:

* **UVOX** (`<uvox>`-XML-Handshake) — das vollständige Protokoll mit
  Cipher, Buffer-Size-Negotiation und Broadcast-Setup.
* **SHOUTCAST** (vereinfacht) — für simple Encoder ohne UVOX.

## Audio-Formate

Die Playlist darf **beliebige** Formate enthalten. ffmpeg dekodiert
alles nach MP3 (128 kbps / 44,1 kHz / stereo) -- der DNAS kann nur
MP3:

| Format | Decodiert | Tags gelesen |
|---|---|---|
| MP3 | ✅ | ID3v2 (direkt, ohne Prozess) |
| FLAC | ✅ | ffprobe |
| Ogg Vorbis | ✅ | ffprobe (Stream-Tags) |
| Opus | ✅ | ffprobe (Stream-Tags) |
| WAV | ✅ | ffprobe |
| M4A / AAC | ✅ | ffprobe |
| AIFF | ✅ | ffprobe (nur `title` — AIFF kennt kein Artist-Feld) |

Die Tags werden **ohne ffprobe** nur fuer MP3 gelesen (ID3v2 direkt aus
der Datei). Fehlt ffprobe, laeuft der Stream trotzdem -- der Titel
ist dann nur leer.

## Werkzeuge

### `tools/fake_listener.py`

Hängt sich als **Hörer** an den Stream und protokolliert jeden
`StreamTitle`-Wechsel mit Zeitstempel:

```bash
python3 tools/fake_listener.py --host <host> --port 8000 \
    --duration 120 --interval 60
```

```
12:53:44  Erster Titel - Testkünstler
12:53:49  Zweiter Titel - Anderer Künstler
```

Mit `--json` gibt es JSONL statt lesbarer Zeilen, mit `--user-agent`
einen anderen Client (Default: `VLC/3.0.20 LibVLC/3.0.20`).

**Fallstrick:** Der sc_serv 2.5.5 gibt die Länge des Metadaten-Blocks
in **16-Byte-Blöcken** an, nicht in Bytes (wie der SHOUTcast-Standard).
Ein Längenbyte von 4 bedeutet also 64 Bytes. Der Listener erkennt die
Variante automatisch über den MP3-Frame-Sync.

### `tools/fake_silent_dj.py`

Simuliert einen DJ, der sich anmeldet, echtes MP3 sendet und dann
**in Stille fällt** — damit lässt sich der Stille-Timeout testen:

```bash
python3 tools/fake_silent_dj.py 'user:pass' 4 25
```

## Status

Siehe [`STATUS.md`](STATUS.md) für den aktuellen Stand.

## Nutzung

```bash
python3 -m pawcast --config /etc/transcast/sc_trans.conf
```

Die Konfiguration ist **kompatibel zur Original-`sc_trans.conf`** — dieselben
Schlüssel, dieselben Namen.

### Beispielkonfiguration erzeugen

pawcast erzeugt die Beispiel-Config selbst — der Installer hat kein eigenes
Template, es gibt nur eine Quelle:

```bash
python3 -m pawcast --genconf /etc/pawcast/sc_trans.conf \
                   --genconf-djlogins /etc/pawcast/djlogins.conf
```

Die DJ-Logins landen in einer eigenen Datei; die Config verweist mit
`include=` darauf. Vorhandene Dateien werden **nicht** überschrieben
(außer mit `--force`). Danach die Passwörter anpassen:
`adminpassword`, `password_1`, `djpassword_1`.

```bash
python3 -m pawcast --config /etc/pawcast/sc_trans.conf --dry-run
```

zeigt die geladene Config ohne den Dienst zu starten.
