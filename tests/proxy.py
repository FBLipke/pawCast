"""TCP-Proxy, der den Source-Request des Original-sc_trans mitschreibt.

Legt eine Proxy-Instanz zwischen pawcast und den DNAS. pawcast
verbindet sich zum Proxy, der Proxy verbindet zum DNAS und schreibt
alles mit, was pawcast schickt.

Aufruf:
    python3 proxy.py <listen_port> <ziel_host> <ziel_port> <logdatei>
"""

import socket
import sys
import threading
import time


def dump(fh, tag, data):
    fh.write(f"=== {tag} len={len(data)} t={time.strftime('%H:%M:%S')}\n")
    for i in range(0, len(data), 16):
        chunk = data[i:i + 16]
        hexpart = " ".join(f"{b:02x}" for b in chunk)
        text = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        fh.write(f"  {i:04x}  {hexpart:<47}  |{text}|\n")
    fh.flush()


def handle(client, target_host, target_port, logpath):
    with open(logpath, "a", buffering=1) as fh:
        try:
            up = socket.create_connection((target_host, target_port), timeout=10)
        except OSError as exc:
            fh.write(f"PROXY: upstream connect failed: {exc}\n")
            client.close()
            return

        def c2s():
            try:
                while True:
                    data = client.recv(4096)
                    if not data:
                        break
                    dump(fh, "CLIENT->DNAS", data)
                    up.sendall(data)
            except OSError:
                pass
            finally:
                try:
                    up.shutdown(socket.SHUT_WR)
                except OSError:
                    pass

        def s2c():
            try:
                while True:
                    data = up.recv(4096)
                    if not data:
                        break
                    dump(fh, "DNAS->CLIENT", data)
                    client.sendall(data)
            except OSError:
                pass
            finally:
                try:
                    client.shutdown(socket.SHUT_WR)
                except OSError:
                    pass

        t1 = threading.Thread(target=c2s, daemon=True)
        t2 = threading.Thread(target=s2c, daemon=True)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        up.close()
        client.close()


def main():
    listen_port = int(sys.argv[1])
    target_host = sys.argv[2]
    target_port = int(sys.argv[3])
    logpath = sys.argv[4]

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", listen_port))
    srv.listen(8)
    print(f"proxy 127.0.0.1:{listen_port} -> {target_host}:{target_port}, log={logpath}",
          flush=True)

    while True:
        client, _ = srv.accept()
        threading.Thread(
            target=handle, args=(client, target_host, target_port, logpath), daemon=True
        ).start()


if __name__ == "__main__":
    main()
