#!/usr/bin/env python3
"""Health-check the NaVILA endpoint (no inference). Standard library only."""

import json
import socket
import sys

HOST, PORT = "127.0.0.1", 54321


def recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise RuntimeError(f"connection closed after {len(buf)} of {n} bytes")
        buf += chunk
    return buf


req = json.dumps({"type": "health"}).encode()
with socket.create_connection((HOST, PORT), timeout=5) as sock:
    sock.sendall(len(req).to_bytes(8, "big"))
    sock.sendall(req)
    size = int.from_bytes(recv_exact(sock, 8), "big")
    resp = json.loads(recv_exact(sock, size).decode())

if resp.get("service") == "navila-vlm" and resp.get("status") == "ok":
    print(f"NaVILA endpoint healthy at {HOST}:{PORT} "
          f"(protocol_version={resp.get('protocol_version')})")
else:
    print(f"unexpected health response: {resp}", file=sys.stderr)
    sys.exit(2)
