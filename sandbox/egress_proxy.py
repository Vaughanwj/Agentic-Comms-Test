"""Allowlist CONNECT proxy: the game containers' only route off their network.

Game containers sit on an internal Docker network with no outside access.
This proxy sits on that network and the outside one, and opens a tunnel only
for host:port pairs in ALLOWED_HOSTS (default api.anthropic.com:443). Every
request is logged, allowed or not.

    ALLOWED_HOSTS=api.anthropic.com:443 python sandbox/egress_proxy.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone

DENIED = b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
BAD_GATEWAY = b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
ESTABLISHED = b"HTTP/1.1 200 Connection Established\r\n\r\n"


def _log(message: str) -> None:
    print(f"{datetime.now(timezone.utc).isoformat()} {message}", flush=True)


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        writer.close()


async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, allowed: set[str]) -> None:
    peer = writer.get_extra_info("peername")
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=10)
    except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
        writer.close()
        return
    parts = head.split(b"\r\n", 1)[0].decode("latin-1").split()
    target = parts[1].lower() if len(parts) == 3 else ""
    if len(parts) != 3 or parts[0].upper() != "CONNECT" or target not in allowed:
        _log(f"DENY {peer} {' '.join(parts)[:200]}")
        writer.write(DENIED)
        await writer.drain()
        writer.close()
        return
    host, port = target.rsplit(":", 1)
    try:
        up_reader, up_writer = await asyncio.wait_for(asyncio.open_connection(host, int(port)), timeout=10)
    except (OSError, asyncio.TimeoutError):
        _log(f"FAIL {peer} {target}")
        writer.write(BAD_GATEWAY)
        await writer.drain()
        writer.close()
        return
    _log(f"ALLOW {peer} {target}")
    writer.write(ESTABLISHED)
    await writer.drain()
    await asyncio.gather(_pipe(reader, up_writer), _pipe(up_reader, writer))


async def start(allowed: set[str], host: str = "0.0.0.0", port: int = 3128) -> asyncio.base_events.Server:
    allowed = {a.strip().lower() for a in allowed if a.strip()}
    return await asyncio.start_server(lambda r, w: _handle(r, w, allowed), host, port)


async def _main() -> None:
    allowed = set(os.environ.get("ALLOWED_HOSTS", "api.anthropic.com:443").split(","))
    port = int(os.environ.get("PROXY_PORT", "3128"))
    server = await start(allowed, port=port)
    _log(f"listening on :{port}, allowing {sorted(allowed)}")
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        sys.exit(0)
