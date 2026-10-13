import asyncio
import socket
import threading

import pytest

from sandbox.egress_proxy import start


@pytest.fixture
def proxy():
    """An echo server, and a proxy that allows only that server."""
    loop = asyncio.new_event_loop()
    ready = threading.Event()
    ports = {}

    async def echo(reader, writer):
        while data := await reader.read(1024):
            writer.write(data)
            await writer.drain()
        writer.close()

    async def setup():
        echo_server = await asyncio.start_server(echo, "127.0.0.1", 0)
        ports["echo"] = echo_server.sockets[0].getsockname()[1]
        proxy_server = await start({f"127.0.0.1:{ports['echo']}"}, host="127.0.0.1", port=0)
        ports["proxy"] = proxy_server.sockets[0].getsockname()[1]
        ready.set()

    thread = threading.Thread(target=lambda: (loop.run_until_complete(setup()), loop.run_forever()), daemon=True)
    thread.start()
    ready.wait(5)
    yield ports
    loop.call_soon_threadsafe(loop.stop)
    thread.join(5)


def connect(proxy_port, target):
    sock = socket.create_connection(("127.0.0.1", proxy_port), timeout=5)
    sock.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
    head = b""
    while b"\r\n\r\n" not in head:
        head += sock.recv(1024)
    return sock, head.split(b"\r\n", 1)[0]


def test_allowed_target_is_tunnelled(proxy):
    sock, status = connect(proxy["proxy"], f"127.0.0.1:{proxy['echo']}")
    assert b"200" in status
    sock.sendall(b"ping")
    assert sock.recv(4) == b"ping"
    sock.close()


@pytest.mark.parametrize("target", ["example.com:443", "127.0.0.1:1", "api.anthropic.com:80"])
def test_everything_else_is_refused(proxy, target):
    sock, status = connect(proxy["proxy"], target)
    assert b"403" in status
    sock.close()


def test_plain_http_requests_are_refused(proxy):
    sock = socket.create_connection(("127.0.0.1", proxy["proxy"]), timeout=5)
    sock.sendall(b"GET http://example.com/ HTTP/1.1\r\nHost: example.com\r\n\r\n")
    assert b"403" in sock.recv(1024).split(b"\r\n", 1)[0]
    sock.close()
