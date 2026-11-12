# DJ-Protokoll und Source-Login — Spezifikation

Stand: 12.11.2026.

Dieses Dokument ist die **Spezifikation der Wire-Protokolle**, die
`pawcast` spricht. Es beschreibt Byte-für-Byte, was gesendet und
erwartet wird. Es ist bewusst als *Spezifikation* formuliert: jede
Aussage ist eine Anforderung an die Implementierung, keine Herleitung.

Grundlage der Angaben sind Beobachtungen am laufenden `pawcast`
2.0.0.54 und am `sc_serv` 2.5.5.733, sowie Netzwerk-Mitschnitte gegen
eine eigene Test-Instanz. Der Source-Pfad ist empirisch verifiziert
(12.11.2026); die DJ-Dialekte sind implementiert, aber noch nicht
byteweise gegen einen echten DJ-Client geprüft.

---

## 1. Überblick und Port-Rollen

Der Transcaster spricht mit vier Gegenstellen:

| Rolle | Gegenstelle | Port | Protokoll |
|---|---|---|---|
| Quelle → DNAS | SHOUTcast-Server (DNAS) | `portbase` (Standard 8000) | Textprotokoll + ICY-Header |
| Quelle → DNAS (Legacy) | SHOUTcast-Server (DNAS) | `portlegacy` (Standard 8001) | nur Passwort-Zeile |
| Titel → DNAS | SHOUTcast-Server (DNAS) | `portbase` | HTTP |
| DJ ↔ Transcaster | Winamp / SHOUTcast-DJ-Client | 9020 | UVOX 2.1 oder SHOUTcast-Dialekt |
| Steuerung | Web-GUI / Automation | 7999 | HTTP (Weblet-API) |

Ports und Passwörter stehen in der Konfigurationsdatei.

**Der Server lauscht auf `portbase` sowohl für Clients als auch für
Sources**, und auf `portlegacy` zusätzlich für Legacy-Source-Verbindungen.
Beide Wege existieren:

- Der **Legacy-Port** akzeptiert ausschließlich das Passwort. Er antwortet
  *nicht* mit `OK2`, sondern direkt mit dem Stream bzw. `Invalid Password`.
- Der **normale Port** antwortet mit `OK2\r\nicy-caps:11\r\n\r\n`.

Ein normaler HTTP-GET mit `/?pass=<pw>` an `portbase` wird vom Server als
**Client**-Verbindung behandelt und liefert Client-ICY-Header
(`icy-name:...`). Quellen müssen sich deshalb über den Source-Login
anmelden, nicht über einen HTTP-GET.

---

## 2. Source-Login (Transcaster → DNAS)

Der Source-Login besteht aus **zwei Teilen**: erst die Anmeldung, dann
der ICY-Header-Block. Die beiden Teile laufen über **eine** TCP-Verbindung.

### 2.1 Zustandsmaschine

Die Verbindung zum DNAS durchläuft diese Zustände:

```
EMPTY(0) → SENDPASS(1) → SENDMSG(2) → SENDSTREAM(4) → GETREPLY(5) → SENDINFO(6)
```

`KILL(3)` ist der Abbruch (Verbindung wird beendet, kein Reconnect).

Der Header-Block aus 2.3 gehört zu `SENDINFO` — er wird also erst
**nach** Passwort (`SENDPASS`) und Server-Antwort (`GETREPLY`) gesendet.

### 2.2 Teil 1 — die Anmeldung (kein HTTP)

Der Transcaster sendet **nur das Passwort als eine Zeile**, gefolgt von
CRLF. Es wird kein HTTP-Request gesendet:

```
CLIENT → DNAS (13 bytes): b'my_src_pass\r\n'
DNAS   → CLIENT (20 bytes): b'OK2\r\nicy-caps:11\r\n\r\n'
```

Die Antwort besteht aus drei Teilen:

1. `OK2` — Erfolgscode (gegenüber `invalid password` bei Ablehnung),
2. `icy-caps:11\r\n` — Fähigkeits-Deskriptor des Servers,
3. eine abschließende Leerzeile `\r\n`.

Wird die Verbindung auf dem Legacy-Port aufgebaut, entfällt die
`OK2`/`icy-caps`-Antwort.

### 2.3 Teil 2 — der ICY-Header-Block

Der Block ist exakt **174 Bytes** und hat genau diese Reihenfolge:

```
icy-name:N/A\r\n
icy-genre:Misc\r\n
icy-url:http://www.shoutcast.com\r\n
icy-irc:N/A\r\n
icy-icq:N/A\r\n
icy-aim:N/A\r\n
icy-pub:0\r\n
content-type:audio/mpeg\r\n
icy-prebuffer:64000\r\n
icy-br:128\r\n
\r\n
```

Drei Regeln, die man leicht falsch macht:

1. **`content-type:audio/` steht zwischen `icy-pub:` und
   `icy-prebuffer:`** — nicht am Ende. Die Reihenfolge ist byte-relevant.
2. **Kein Leerzeichen nach dem Doppelpunkt.** `icy-name:N/A`, nicht
   `icy-name: N/A`. Mit Leerzeichen lehnt der DNAS ab:
   `Bad icy header string [icy-name:]`.
3. **`icy-pub`, `icy-prebuffer` und `icy-br` sind Zahlen**, kein Text.

Weitere Vorgaben:

- `icy-prebuffer:64000` (nicht 8000).
- `icy-br` ist die Bitrate in **kbps** (128000/1000 → `128`).
- `icy-pub:0` — Zahl, kein Boolean-Text.
- `icy-name`, `icy-irc`, `icy-icq`, `icy-aim` erhalten `N/A`, wenn kein
  Wert konfiguriert ist. Leere Werte werden zu `N/A` ersetzt.
- Der Header-Block endet mit einer Leerzeile (`\r\n\r\n`).

### 2.4 Antwort des DNAS und Log-Ausgaben

Bei Erfolg meldet der Transcaster:

```
[STREAM] Host server gave success (OK2)
[STREAM] Stream to <host> established
```

Der Server antwortet auf den Login mit `icy-caps:`; der Client wertet
diese Zeile im Zustand `GETREPLY` aus.

### 2.5 Log-Zeilen des Source-Pfads

Format `[STREAM] <Text>`:

```
 Creating stream socket
 Resolving stream host
 Error resolving stream host
 Connected to host server
 Negotiating
 Sending stream information
 Disconnected
 Host server gave success (
 Stream to
 established
 Got response from server[
 bytes sent=
icy-caps:
 Connecting
 Running
```

Der Präfix `[STREAM]` kennzeichnet die Source-Komponente.

---

## 3. Metadaten-Kanal (Titel → DNAS)

Der laufende Titel wird über den **Admin-Kanal** gemeldet — das ist eine
**eigene TCP-Verbindung**, getrennt von der Source-Verbindung und vom
DJ-Port. Das ist ein HTTP-Request:

```
GET /admin.cgi?pass=<adminpw>&mode=updinfo&song=<artist - title> HTTP/1.0\r\n
User-Agent:TRANScast Title Update(Mozilla)\r\n
\r\n
```

Antwort des DNAS:

```
HTTP/1.1 200 OK\r\nConnection:close\r\n\r\n
```

Vorgaben:

- Der Song-Titel ist **URL-encodiert** (Leerzeichen → `%20`,
  Klammern → `%28` / `%29`).
- `song` = `Artist - Title` (Bindestrich mit Leerzeichen).
- **Kein `mount=`-Parameter.**
- **Kein Leerzeichen nach `User-Agent:`** — der User-Agent lautet exakt
  `TRANScast Title Update(Mozilla)`.
- Der `pass`-Parameter trägt das Admin-Passwort des Endpoints.

### 3.1 Fehlerbehandlung des Metadaten-Kanals

Log-Format `[SHOUTCASTMETADATA] <Text>` bzw. ` <Text>`:

```
[SHOUTCASTMETADATA] - Fatal error
[SHOUTCASTMETADATA] Empty url string
 Could not resolve SHOUTcast server address:
 Could not connect to SHOUTcast server
 Socket error while sending title
[SHOUTCASTMETADATA] Metadata response read timeout
[SHOUTCASTMETADATA]  title response returned error.
[SHOUTCASTMETADATA] Could not parse 3901 metadata
[SHOUTCASTMETADATA] Could not parse 3901 metadata due to unknown exception
[SHOUTCASTMETADATA] Could not parse 3902 metadata
[SHOUTCASTMETADATA] Could not parse 3902 metadata due to unknown exception
[SHOUTCASTMETADATA] Metadata string [
[SHOUTCASTMETADATA] Dropped stale metadata
[SHOUTCASTMETADATA] Sending metadata
```

Die Codes **3901** und **3902** sind Metadaten-Statuscodes des DNAS.
Ihre genaue Bedeutung ist noch nicht geklärt.

`Dropped stale metadata` bedeutet: der Metadaten-Sender verwirft ältere
Titelupdates (Schutz gegen Race-Condition bei schnell wechselnder
Playlist).

---

## 4. Anforderungen an das MP3-Format

Der DNAS validiert den MP3-Stream und trennt die Quelle, wenn er keinen
gültigen Frame-Sync findet:

```
ERROR [SRC ...] SHOUTcast 1 source disconnected. Unable to sync to the
stream. Please check the source is valid and in a supported format.
```

Ursache sind in der Regel ein **ID3-Header** und ein **Xing/Info-Frame**
am Anfang des Streams. Der DNAS findet den ersten gültigen Sync-Byte
dann nicht.

Vorgabe für die Encodierung:

- **rohe MPEG-Frames**, d. h. ffmpeg mit `-id3v2_version 0 -write_xing 0`,
- MPEG1 Layer III, 128 kbps, 44100 Hz,
- kleine Chunks (damit der Encoder nicht den ganzen Titel puffert),
- der erste gültige Sync liegt typischerweise bei Offset 240.

---

## 5. Der DJ-Port

Der DJ-Port (9020) ist kein Weblet (das ist Port 7999). Es ist ein eigener
HTTP-artiger Handler, der **zwei Dialekte** kennt. Beide laufen über
denselben Dispatcher (`dj/protocol.py`).

### 5.1 Dialekt-Erkennung

Der Dispatcher liest bewusst in *einem* `recv`-Aufruf die ersten Bytes und
leitet daraus den Dialekt ab:

| Anfang der Bytes | Dialekt |
|---|---|
| `<` | UVOX (XML-Handshake) |
| `SOURCE` | SHOUTcast-v1-Quellprotokoll |
| `GET` / `POST` | HTTP-artig |

Die gelesenen Vorschau-Bytes gehen dabei nicht verloren: sie werden
gepuffert und danach als erste Bytes der Sitzung ausgeliefert. Das ist
nötig, weil ein echter SAMBC-Client Login und Metadaten in einem einzigen
TCP-Segment schickt:

```
djane:geheim\r\nicy-name:Mein Stream\r\n
```

### 5.2 SHOUTcast-Dialekt

Zustände:

```
EMPTY          → initial, warte auf die erste Anfrage
GETSTREAM      → warte auf den Stream-Request des DJs
GETICY         → lese die ICY-Metadaten des DJs
NEEDPASS       → Passwort wird angefordert
CHECKPASS      → Passwort wird geprüft
SENDREPLY      → Antwort wird gesendet
KILL           → Verbindung wird beendet
```

Log-Zeilen, Format `[DJPROTOCOLSHOUTCAST] <Text>`:

```
[DJPROTOCOLSHOUTCAST] State GETSTREAM
[DJPROTOCOLSHOUTCAST] Data arriving too fast for processing - some has been
                      lost in the last second.
[DJPROTOCOLSHOUTCAST] State EMPTY
[DJPROTOCOLSHOUTCAST] Unknown exception
[DJPROTOCOLSHOUTCAST] State GETICY
[DJPROTOCOLSHOUTCAST] State NEEDPASS
[DJPROTOCOLSHOUTCAST] Timeout while waiting to receive password
[DJPROTOCOLSHOUTCAST] Password length has been exceeded
[DJPROTOCOLSHOUTCAST] Remote socket was closed while waiting for password
[DJPROTOCOLSHOUTCAST] Received password is in wrong format
[DJPROTOCOLSHOUTCAST] State SENDREPLY
[DJPROTOCOLSHOUTCAST] State CHECKPASS
[DJPROTOCOLSHOUTCAST] Login denied from <IP>
[DJPROTOCOLSHOUTCAST] State KILL
```

Antworten:

| Situation | Antwort |
|---|---|
| Erfolg | `OK2\r\nicy-caps:11\r\n\r\n` |
| Ablehnung | `invalid password\r\n` |

Weitere Meldungen dieses Dialekts:

```
content-type:audio/
Timeout waiting for icy data
Empty ICY data line
Socket error
Timeout error in
invalid password
Connected
OK2
icy-caps:11
```

**Zugangsdaten.** Der Login wird am **ersten Doppelpunkt** getrennt:
`<login>:<passwort>`. Der Wert darf selbst Doppelpunkte enthalten; getrennt
wird nur am ersten. Das Passwort kommt entweder als Query-Parameter
(`pass=` bzw. `password=`) oder als Zeile der Form `login:passwort`.

Bei einem unbekannten Zustand wird die Verbindung abgebrochen, nicht nur
protokolliert — die Verarbeitung läuft also ausnahmebasiert.

### 5.3 UVOX-Dialekt (Ultravox 2.1)

Der UVOX-Handshake besteht aus **16 Zuständen**:

```
EMPTY
CIPHER                 → Cipher aushandeln (uvoxcipherkey)
MSGMIMETYPE            → Mime-Type der Broadcast-Quelle
BROADCASTSETUP         → <encoder type="..."/> + <network protocol="..."/>
AUTHENTICATIONREQUEST  → <password> prüfen
NEGOTIATEBUFFERSIZE    → Buffer-Größen aushandeln
SENDREPLY
ICYNAME                → ICY-Metadaten beim DJ abfragen
FILETRANSFERBEGIN      → Beginn des Datentransfers
MAX_PAYLOAD_SIZE
STANDBY
FLUSHCACHEDMETADATA
LISTENERAUTHENTICATION
TERMINATE
METADATAXMLNEW
METADATAXML
KILL                   → Verbindung wird beendet
```

**Nach der Authentifizierung fragt der Server die ICY-Metadaten beim DJ
ab** (Zustand `ICYNAME`). Danach folgt der Datentransfer
(`FILETRANSFERBEGIN`).

Log-Zeilen, Format `[DJPROTOCOLUVOX] <Text>`:

```
[DJPROTOCOLUVOX] Cipher request parse error (
[DJPROTOCOLUVOX] Cipher request version error (
[DJPROTOCOLUVOX] Authentication string parse error (
[DJPROTOCOLUVOX] Negotiate buffer size parse error (
[DJPROTOCOLUVOX] Bitrate parse error (
[DJPROTOCOLUVOX] Mime type parse error (
[DJPROTOCOLUVOX] Unsupported mime type (
[DJPROTOCOLUVOX] Login denied from `<IP>
[DJPROTOCOLUVOX] ICYPUB
[DJPROTOCOLUVOX] ICYPUB for
[DJPROTOCOLUVOX] ICYURL
[DJPROTOCOLUVOX] ICYURL for
[DJPROTOCOLUVOX] ICYGENRE
[DJPROTOCOLUVOX] ICYNAME for
[DJPROTOCOLUVOX] ICYGENRE for
```

⇒ **Der Server fragt im Zustand `ICYNAME` vier Werte ab: `ICYNAME`,
`ICYGENRE`, `ICYURL`, `ICYPUB`.** Die exakte Byte-Sequenz der Frage ist
noch nicht festgelegt.

### 5.4 UVOX-Tags und Identifikation

Der UVOX-Client identifiziert sich im User-Agent mit:

```
Ultravox/2.1 sc_trans/
```

Verwendete Tags:

```
<network protocol="
<shoutcast>
</shoutcast>
<uvox>
</uvox>
<encoder type="
mp3
</encoder>
</name>
```

Erlaubte `network protocol=`-Werte: `shoutcast`, `uvox`.
Erlaubte `encoder type=`-Werte: `mp3`, `aac`.

Authentifizierungs-Felder:

```
djcipher
uvoxcipherkey
uvoxauth
uvoxstreamid
uvoxuserid
authblob=
authtoken=
uid=
```

Der Broadcast-Setup-Block besteht aus `<encoder type="..."/>` und
`<network protocol="..."/>`. Die vollständige Elementreihenfolge des
Broadcast-Setups ist noch nicht festgelegt.

### 5.5 HTTP-Transport auf dem DJ-Port

Fehlerantworten des DJ-Handlers:

```
HTTP/1.0 401 Unauthorized
Server: Weblet
WWW-authenticate: basic realm="Weblet"
Content-type: text/html

HTTP/1.0 404 Not Found: Weblet
HTTP/1.0 400 Bad Request
HTTP/1.0 415 Unsupported Media Type
```

---

## 6. Admin-API (Weblet, Port 7999)

Der Weblet-Port 7999 ist die HTTP-Steuerschnittstelle. Die Actions:

```
listdjs | adddj | modifydj | deletedj
getstatus | getendpoints | setendpoint
getoptions | setoptions
playlistdata | logdata
```

Die internen Namen der API-Handler:

```
[API] API_adddj called
[API] API_listdjs called
[API] API_getstatus called
[API] API_getendpoints called
[API] API_setendpoint called
[API] API_getoptions called
[API] API_setoptions called
[API] API_playlistdata called
[API] API_logdata called
```

Pfade, die das Weblet bedient:

```
/api
/nextsong
/loadplaylist
/toggleshuffle
/kickdj
/restart
/timeshift
/writeconfig
/rotatelogs
/quit
/vumeterleft
/vumeterright
/vu.bin
/crossdomain.xml
```

Antwortform ist XML, umschlossen von `<response seq="...">`:

```xml
<response seq="...">
  <djlist>
    <dj><name>..</name><password>..</password><priority>..</priority>
        <enabled>..</enabled></dj>
  </djlist>
</response>

<response seq="...">
  <status>
    <device>..</device><input>..</input><samplerate>..</samplerate>
    <channels>..</channels><ip>..</ip><port>..</port>
    <sourcetype>..</sourcetype><url>..</url><bitrate>..</bitrate>
    <nexttrack>..</nexttrack><currenttrack>..</currenttrack><file>..</file>
    <activesource source=".."/>
    <endpointlist><bytessent>..</bytessent><status>..</status></endpointlist>
  </status>
</response>
```

**Achtung:** Die Weblet-API des Originals ist **defekt** — `/admin.cgi`,
`/djlogins` und `/status` liefern 404, unabhängig vom Passwort. Die
Actions erwarten `action=listdjs|adddj|modifydj|deletedj`. Die
Implementierung in `pawcast` ist funktionsfähig — das ist ein
bewusster Vorteil und keine Inkompatibilität.

---

## 7. Bekannte Eigenheiten des Originals

1. **Keine Inline-Kommentare** in `sc_trans.conf`: `password=geheim # Kommentar`
   ergibt den Wert `geheim # Kommentar` → der DNAS antwortet
   `[Invalid Password]`. Kommentare müssen **in eigener Zeile** stehen.
2. **`unlockkeyname`/`unlockkeycode`** müssen exakt stimmen, sonst:
   `Unknown encoder type mp3. Ensure you have entered the license key and
   name fully.`
3. **Kein Auto-Reconnect** nach Source-Verlust — der Stream bleibt tot,
   bis der Dienst neu gestartet wird. `pawcast` reconnectet mit
   exponentiellem Backoff (5 s → 10 s → 20 s → … → 60 s).
4. **`Playlist has run dry`** beendet den Transcaster (Exit 254).
   `pawcast` wartet 30 s und lädt die Playlist neu.

---

## 8. Verbindliche Regeln für die Implementierung

| # | Regel | Bereich |
|---|---|---|
| 1 | Source-Anmeldung als reine Zeile `<passwort>\r\n`, kein HTTP-Request | 2.2 |
| 2 | Erfolgsantwort des DNAS ist `OK2\r\nicy-caps:11\r\n\r\n` | 2.2 |
| 3 | ICY-Header in der vorgegebenen Reihenfolge, `content-type:audio/` in der Mitte | 2.3 |
| 4 | Kein Leerzeichen nach dem Doppelpunkt in ICY-Headern | 2.3 |
| 5 | `icy-pub`, `icy-prebuffer`, `icy-br` als Zahlen; `icy-prebuffer:64000` | 2.3 |
| 6 | Metadaten über eine eigene HTTP-Verbindung mit User-Agent `TRANScast Title Update(Mozilla)` | 3 |
| 7 | MP3 ohne ID3-Header und ohne Xing/Info-Frame | 4 |
| 8 | Dialekt-Erkennung an den ersten Bytes (`<`, `SOURCE`, `GET`/`POST`) | 5.1 |
| 9 | Zugangsdaten am ersten Doppelpunkt trennen (`<login>:<passwort>`) | 5.2 |
| 10 | UVOX: nach der Authentifizierung vier ICY-Werte abfragen (ICYNAME, ICYGENRE, ICYURL, ICYPUB) | 5.3 |
| 11 | Unbekannter Zustand beendet die Verbindung (Ausnahmebehandlung) | 5.2 |

---

## 9. Offene Punkte

1. **Die exakte Byte-Sequenz der ICYNAME-Frage** im UVOX-Handshake.
2. **Die vollständige Elementreihenfolge** des UVOX-Broadcast-Setups
   (Attribute inklusive).
3. **Die Bedeutung der DNAS-Metadaten-Codes 3901 / 3902.**
4. **Der User-Agent des Source-Logins** — der Wert ist frei wählbar; er
   wird in der ICY-Antwortverarbeitung nicht ausgewertet.
5. **Der Wert von `icy-prebuffer`**, wenn keine Prefetch-Größe konfiguriert
   ist (Standard 64000).
6. **Das Verhalten des DJ-Ports** ist noch nicht byteweise gegen einen
   echten DJ-Client geprüft — hier fehlt der empirische Test.
7. **Der SHOUTcast-2-Dialekt** des DJ-Protokolls (Log-Zeilen
   `Creating SHOUTcast 2 protocol object` / `Creating SHOUTcast 1
   protocol object`) gehört zum Source-Relay (`[SOURCERELAY]`), nicht zum
   DJ-Port.
