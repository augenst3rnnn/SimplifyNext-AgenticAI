#!/usr/bin/env python3
"""Verify a NaVILA endpoint without running model inference."""

from __future__ import annotations

import argparse
import json
import socket
import sys
from typing import Any


HEALTH_REQUEST = {"type": "health"}
EXPECTED_SERVICE = "navila-vlm"
EXPECTED_STATUS = "ok"
PROTOCOL_VERSION = 1
MAX_RESPONSE_BYTES = 4096


class EndpointHealthError(RuntimeError):
    """Raised when the peer is unavailable or fails the health protocol."""


def recv_exact(connection: socket.socket, size: int) -> bytes:
    """Read exactly ``size`` bytes or fail on an early EOF."""

    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = connection.recv(remaining)
        if not chunk:
            received = size - remaining
            raise EndpointHealthError(
                f"connection closed after {received} of {size} bytes"
            )
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def check_endpoint(host: str, port: int, *, timeout_s: float = 5.0) -> dict[str, Any]:
    """Return the validated health response from one NaVILA endpoint."""

    if not host:
        raise ValueError("host must not be empty")
    if not 1 <= int(port) <= 65535:
        raise ValueError("port must be between 1 and 65535")
    if timeout_s <= 0:
        raise ValueError("timeout must be positive")

    request_bytes = json.dumps(HEALTH_REQUEST, separators=(",", ":")).encode("utf-8")
    try:
        connection = socket.create_connection((host, int(port)), timeout=timeout_s)
        with connection:
            connection.settimeout(timeout_s)
            connection.sendall(len(request_bytes).to_bytes(8, "big"))
            connection.sendall(request_bytes)
            response_size = int.from_bytes(recv_exact(connection, 8), "big")
            if not 0 < response_size <= MAX_RESPONSE_BYTES:
                raise EndpointHealthError(
                    "invalid health response size "
                    f"{response_size}; maximum is {MAX_RESPONSE_BYTES}"
                )
            response_bytes = recv_exact(connection, response_size)
    except EndpointHealthError:
        raise
    except OSError as exc:
        raise EndpointHealthError(
            f"cannot exchange health data with {host}:{port}: {exc}"
        ) from exc

    try:
        response = json.loads(response_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EndpointHealthError("health response is not valid UTF-8 JSON") from exc
    if not isinstance(response, dict):
        raise EndpointHealthError("health response must be a JSON object")
    if response.get("service") != EXPECTED_SERVICE:
        raise EndpointHealthError("health response has an unexpected service identity")
    if response.get("status") != EXPECTED_STATUS:
        raise EndpointHealthError("NaVILA endpoint did not report status=ok")
    version = response.get("protocol_version")
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version != PROTOCOL_VERSION
    ):
        raise EndpointHealthError(
            f"unsupported NaVILA protocol version; expected {PROTOCOL_VERSION}"
        )
    return response


def _port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("port must be an integer") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def _positive_timeout(value: str) -> float:
    try:
        timeout_s = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be a number") from exc
    if timeout_s <= 0:
        raise argparse.ArgumentTypeError("timeout must be positive")
    return timeout_s


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check a NaVILA TCP endpoint without running inference."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=_port, default=54321)
    parser.add_argument("--timeout", type=_positive_timeout, default=5.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        response = check_endpoint(args.host, args.port, timeout_s=args.timeout)
    except (EndpointHealthError, ValueError) as exc:
        print(f"NaVILA endpoint health check failed: {exc}", file=sys.stderr)
        return 2

    print(
        f"NaVILA endpoint is healthy at {args.host}:{args.port} "
        f"(protocol_version={response['protocol_version']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
