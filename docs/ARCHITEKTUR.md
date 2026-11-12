# pawcast — Architektur & Quellcode-Erklärung

Stand: 11.11.2026. Dieses Dokument beschreibt, **was der Code tut und warum**,
so dass jemand ohne Vorkenntnisse ihn verstehen und weiterentwickeln kann.

---

## 1. Worum es geht

`pawcast` (Nullsoft, 2011) ist der SHOUTcast Transcaster v2: ein Programm,
das

1. sich bei einem **SHOUTcast-Server (DNAS)** als *Quelle* anmeldet,
2. Musik aus einer **Playlist** automatisch streamt (AutoDJ),
3. einem **DJ** erlaubt, sich anzumelden und live zu senden,
4. den **Titel** ("Artist - Song") laufend an den Server meldet.

Das Original ist ein geschlossenes ELF-Binary mit lizenzpflichtigem
Fraunhofer-Encoder. `pawcast` ersetzt es durch Python.

**Was wichtig ist:** Das Projekt ist *nicht* "irgendein Streaming-Tool".
Es muss **byte-kompatibel** zum Original sein — DNAS und DJ-Clients
erkennen den Unterschied nicht. Die Protokoll-Spezifikation steht in
`docs/dj-protocol-spec.md`.

---

## 2. Das große Bild: wer spricht mit wem

```
   ┌──────────────┐         ┌──────────────────────────────┐
   │  DJ-Client   │◄──TCP──►│        pawcast           │
   │ (Winamp,     │  DJ-Port│                              │
   │  SHOUTcast)  │         │  ┌────────────────────────┐  │
   └──────────────┘         │  │ dj/ (Protokoll-Schicht)│  │
                            │  └───────────┬────────────┘  │
   ┌──────────────┐         │              │               │
   │    DNAS      │◄─TCP───►│  ┌───────────▼────────────┐  │
   │ (SHOUTcast-  │  19020  │  │  source.py (Anmeldung)  │  │
   │  Server)     │         │  │  encoder.py (MP3)       │  │
   └──────────────┘         │  │  playlist.py (AutoDJ)   │  │
                            │  │  metadata.py (Titel)    │  │
   ┌──────────────┐         │  └────────────────────────┘  │
   │  Web-GUI /   │◄─HTTP──►│  api.py (Port 7999)          │
   │  Automation  │         └──────────────────────────────┘
   └──────────────┘
```

Vier Rollen, klar getrennt:

| Rolle | Modul | Richtung |
|---|---|---|
| **Quelle → DNAS** | `source.py` | wir senden Audio zum Server |
| **DJ ↔ Transcaster** | `dj/*` | DJ meldet sich an, wir nehmen Audio entgegen |
| **Titel → DNAS** | `metadata.py` | wir melden "Artist - Song" (eigene Verbindung) |
| **Steuerung** | `api.py` | HTTP-Schnittstelle für Web-GUIs |

---

## 3. Die Module im Detail

### 3.1 `config.py` — die Konfiguration lesen

Liest `sc_trans.conf` im Format des Originals: eine Option pro Zeile als
`key=value`.

```python
cfg = config_mod.load("/etc/transcast/sc_trans.conf")
ep = cfg.endpoint(1)      # der erste Ausgabe-Endpoint (Server, Port, Passwort)
```

**Wichtig (verifiziert am Original):** `sc_trans` strippt *keine*
Inline-Kommentare. Ein `password=geheim # Kommentar` ergibt den Wert
`geheim # Kommentar` — der Encoder stirbt dann mit "Invalid Password".
Kommentare müssen **allein auf einer Zeile** stehen.

`include=<pfad>` bindet weitere Dateien ein (z. B. die DJ-Logins).

### 3.2 `playlist.py` — der AutoDJ

Verwaltet die Liste der Titel, die ohne DJ laufen:

- `Playlist.next_track()` → nächster Pfad (Shuffle optional)
- `JinglePlayer` → Ansagen/Jingles zwischen den Titeln
- `Crossfader` → überblendet den Übergang zwischen zwei Titeln

Bei leerer Liste gibt das Original auf ("Playlist has run dry", exit 254).
`pawcast` wartet stattdessen 30 Sekunden und lädt neu — siehe
`__main__.py`.

### 3.3 `encoder.py` — MP3 erzeugen

Das Original nutzt den eingebauten Fraunhofer-Encoder und braucht dafür
`unlockkeyname` + `unlockkeycode`. Ohne Key:

> Unknown encoder type mp3. Ensure you have entered the license key and name fully.

Wir nutzen **ffmpeg** (Fallback: `lame`). Die API ist dateiweise:

```python
for chunk in self.encoder.encode(path):   # Iterator über MP3-Chunks
    self.source.send(chunk)
```

Das hält den Speicherbedarf klein — ein 5-Minuten-Titel wird nicht
komplett ins RAM geladen.

### 3.4 `source.py` — die Anmeldung am DNAS ⭐

Das ist das Modul, das derzeit am genauesten spezifiziert ist.
Der Ablauf hat **zwei** Teile:

**Teil 1 — nur das Passwort (kein HTTP!)**

```
<sourcepassword>\r\n
```

Antwort des DNAS:

```
OK2\r\n
icy-caps:11\r\n
\r\n
```

**Am 12.11.2026 empirisch verifiziert** gegen den echten `sc_serv`. Das
Original schickt **keinen** GET-Request — nur das Passwort als eine Zeile.
Die HTTP-Anfrage gilt für den *Metadaten*-Kanal.

**Teil 2 — der ICY-Header-Block** (empirisch gemessen, exakt 174 Bytes):

```
icy-name:<name>
icy-genre:<genre>
icy-url:<url>
icy-irc:<irc>
icy-icq:<icq>
icy-aim:<aim>
icy-pub:<0|1>
content-type:audio/<codec>     ← steht MITTEN drin, nicht am Ende!
icy-prebuffer:<ms>
icy-br:<kbps>

```

Vier Feinheiten, die man leicht falsch macht:

1. `content-type:audio/` steht **zwischen** `icy-pub:` und `icy-prebuffer:`.
2. `icy-pub`, `icy-prebuffer` und `icy-br` sind **Zahlen**, kein Text.
3. **Kein Leerzeichen nach dem Doppelpunkt** — `icy-name:N/A`, nicht
   `icy-name: N/A`. Sonst lehnt der DNAS ab:
   `Bad icy header string [icy-name:]`
4. `icy-prebuffer:64000`. Leere Werte werden zu `N/A`.

Die Zustandsmaschine des Originals:

```
EMPTY(0) → SENDPASS(1) → SENDMSG(2) → SENDSTREAM(4) → GETREPLY(5) → SENDINFO(6)
```

`KILL(3)` ist der Abbruch. Der Header-Block gehört zu `SENDINFO`.

### 3.5 `metadata.py` — der Titel

Schickt den laufenden Titel über den **Admin-Kanal** — eine eigene
HTTP-Verbindung, *nicht* die Source-Verbindung:

```
GET /admin.cgi?pass=<adminpw>&mode=updinfo&song=<artist - title> HTTP/1.0
User-Agent:TRANScast Title Update(Mozilla)

```

Beachte: **kein** `mount=`-Parameter, und **kein Leerzeichen** nach
`User-Agent:`.

### 3.6 `dj/` — die DJ-Schicht

Der DJ-Port ist der zweite große Protokoll-Block. Das Original kennt
**zwei Dialekte**, die über denselben Dispatcher laufen:

```
dj/
  wire.py       gemeinsame Primitive (Zeilenlesen, Credentials parsen)
  protocol.py   Dispatcher: erkennt den Dialekt an den ersten Bytes
  shoutcast.py  einfacher Dialekt (GETSTREAM/GETICY/NEEDPASS/CHECKPASS/…)
  uvox.py       voller Ultravox-2.1-Handshake (16 Zustände)
  server.py     TCP-Server, nimmt Verbindungen an
  manager.py    verwaltet Sessions, entscheidet über Relay/Fallback
  relay.py      reicht den DJ-Audio zum DNAS durch
```

**Der Dispatcher** (`protocol.py`) liest bewusst in *einem* `recv`-Aufruf
die ersten Bytes und leitet daraus ab:

| Anfang der Bytes | Dialekt |
|---|---|
| `<` | UVOX (XML-Handshake) |
| `SOURCE` | SHOUTcast-v1-Quellprotokoll |
| `GET` / `POST` | HTTP-artig |

**Die Zustände des Originals**:

SHOUTcast-Dialekt: `EMPTY`, `GETSTREAM`, `GETICY`, `NEEDPASS`, `CHECKPASS`,
`SENDREPLY`, `KILL`.

UVOX-Dialekt (16 Zustände): `EMPTY`, `KILL`, `CIPHER`, `SENDREPLY`,
`MSGMIMETYPE`, `BROADCASTSETUP`, `AUTHENTICATIONREQUEST`,
`NEGOTIATEBUFFERSIZE`, `ICYNAME`, `FILETRANSFERBEGIN`, `MAX_PAYLOAD_SIZE`,
`STANDBY`, `FLUSHCACHEDMETADATA`, `LISTENERAUTHENTICATION`, `TERMINATE`,
`METADATAXMLNEW`, `METADATAXML`.

**Der Manager** (`manager.py`) ist die Schnittstelle zwischen Netzwerk
und Stream-Loop und entscheidet:

- **DJ sendet** → sein Audio wird zum DNAS durchgereicht (Relay)
- **DJ hört zu** → er bekommt den AutoDJ-Stream zu hören
- **DJ verschwindet** → die Playlist übernimmt (Fallback)

Bei mehreren DJs gewinnt die **höchste Priorität**.

### 3.7 `api.py` — die Weblet-API

HTTP-Server auf Port 7999 mit den Actions des Originals:

```
listdjs | adddj | modifydj | deletedj
getstatus | getendpoints | setendpoint
getoptions | setoptions
playlistdata | logdata
```

Antwortform ist XML (`<response seq="...">`). Authentifizierung über
`pass=` als Query-Parameter oder HTTP-Basic-Auth.

**Das Original hat diese API, aber sie ist defekt** (`/admin.cgi`,
`/djlogins`, `/status` → 404). Unsere ist funktionsfähig — das ist ein
bewusster Vorteil, keine Inkompatibilität.

### 3.8 `__main__.py` — die Orchestrierung

Die Klasse `Transcaster` hält alles zusammen. Die Kernschleife:

```python
while not self._stop.is_set():
    live = self.dj_manager.live_session()
    if live is not None:
        self._relay_from_dj(live)      # DJ hat Vorrang
        continue
    path = self.playlist.next_track()
    if path is None:
        self._stop.wait(30)           # Playlist leer → warten, nicht sterben
        self.playlist.load()
        continue
    self._stream_file(path)           # encoden und zum DNAS schicken
```

Der Vorrang-Check liegt **zweimal** — einmal am Anfang der Schleife,
einmal **innerhalb** des Encoder-Iterators. Grund: Wenn ein DJ genau dann
live wird, während ein 5-Minuten-Titel encodiert wird, muss der
Playlist-Titel sofort abbrechen. Sonst würde der DJ bis zum Titelende
warten.

---

## 4. Die Reconnect-Logik

Das Original reconnectet nach einem Source-Verlust **nicht** von selbst.
Wir tun es — mit exponentiellem Backoff:

```python
backoff = 5
while not self._stop.is_set():
    try:
        self.source.connect(retries=3, retry_delay=backoff)
        backoff = 5
        self._stream_loop()
    except SourceError as exc:
        self._stop.wait(backoff)
        backoff = min(backoff * 2, 60)
```

---

## 5. Zwei Fehlerquellen, die man kennen muss

### 5.1 Zirkulärer Import

`dj/protocol.py` braucht `dj/shoutcast.py` und `dj/uvox.py` — und die
brauchen wieder `dj/protocol.py`. Lösung: die gemeinsamen Primitive
liegen in `dj/wire.py`, das **niemanden** importiert.

### 5.2 `socket.timeout` ohne Import

`socket.timeout` ist ein Alias für `TimeoutError`. In `__main__.py` wurde
es benutzt, ohne `socket` zu importieren → `NameError` zur Laufzeit.
Behoben durch `import socket`.

---

## 6. Tests

```bash
python3 -m unittest discover tests -v
```

| Datei | Inhalt |
|---|---|
| `tests/test_core.py` | Config, Playlist, Kalender, Encoder |
| `tests/test_dj.py` | DJ-Protokolle (SHOUTcast + UVOX) |
| `tests/fake_dnas.py` | ein Fake-DNAS zum Testen des Source-Logins |
| `tests/proxy.py` | TCP-Proxy zum Abgreifen des Wire-Formats |

**Der wichtigste Test** ist der gegen `fake_dnas.py`: er vergleicht den
Byte-Stream, den wir senden, mit dem, den das Original sendet.

---

## 7. Woher die Zahlen kommen

Jede Behauptung über das Original ist entweder:

- **verifiziert** — am laufenden `pawcast`/`sc_serv` beobachtet oder
  byteweise nachgemessen, oder
- **spezifiziert** — aus der dokumentierten Protokoll-Spezifikation
  abgeleitet, aber noch nicht empirisch geprüft.

Die Spezifikation steht in `docs/dj-protocol-spec.md`. Der Projektstatus
inkl. offener Punkte steht in `.alinainfo`.

### Der Stand der Verifikation

| Modul | Implementiert | Verifiziert? |
|---|---|---|
| `config.py`, `calendar.py` | ✅ | ✅ **byteweise gegen das Original** (md5 identisch) |
| `source.py` | ✅ | ✅ **empirisch am echten `sc_serv`** (12.11.2026) |
| `metadata.py` | ✅ | ✅ **empirisch** — DNAS-Log: `Title updated [...]` |
| `encoder.py` | ✅ | ✅ **empirisch** — 442963 Bytes beim Client empfangen |
| `dj/uvox.py`, `dj/shoutcast.py` | ✅ | ❌ **nur spezifiziert, nicht empirisch** |
| `dj/protocol.py` Dispatcher | ✅ | ❌ nur spezifiziert |

Der komplette **Source-Pfad** (Anmeldung, Header, Metadaten, MP3-Format)
ist am 12.11.2026 gegen das Original verifiziert. Die **DJ-Protokolle
sind es nicht** — dort fehlt der empirische Test noch.

Test-Werkzeuge liegen in `tests/` (`mitm_source.py`, `live_source_test.py`,
`e2e_stream_test.py`). **Niemals gegen den Produktivstream 8000/8001
testen** — immer gegen eine eigene `sc_serv`-Instanz.

---

## 8. Dateiübersicht

```
pawcast/
├── pawcast/
│   ├── __main__.py    Transcaster, Hauptschleife, Orchestrierung
│   ├── config.py      sc_trans.conf lesen/schreiben
│   ├── playlist.py    AutoDJ, Jingles, Crossfade
│   ├── calendar.py    dj_calendar.xml
│   ├── encoder.py     ffmpeg/lame → MP3
│   ├── source.py      Anmeldung am DNAS  ⭐
│   ├── metadata.py    Titel an den DNAS
│   ├── api.py         Weblet-API (7999)
│   ├── dnas.py       DNAS-Status auslesen (Zuhörer, Playing Now/Next)
│   ├── healthcheck.py autonomer Health-Check
│   ├── panel.html     WebPanel (SHOUTcast-DNAS-Look)
│   └── panel.css      Stylesheet des WebPanels
│   └── dj/
│       ├── wire.py       gemeinsame Primitive
│       ├── protocol.py   Dispatcher
│       ├── shoutcast.py  einfacher DJ-Dialekt
│       ├── uvox.py       UVOX-Dialekt
│       ├── server.py     TCP-Server
│       ├── manager.py    Sessions, Fallback
│       └── relay.py      Audio durchreichen
├── tests/            Unit-Tests, Fake-DNAS, TCP-Proxy
├── docs/
│   ├── dj-protocol-spec.md  Protokoll-Spezifikation ⭐
│   ├── ARCHITEKTUR.md       dieses Dokument
└── deploy/           systemd-Units, Installer
```
