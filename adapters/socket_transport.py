"""SocketTransport: one TCP connection on localhost between the two agents.

Player one's end is the accepted server socket, player two's end is the
client socket. Frames are a 4-byte big-endian length followed by UTF-8 JSON.

Each end has a reader thread that drains its socket into an inbox as bytes
arrive, so a player who sends a lot while the other is not reading can
never fill the kernel buffers and block the game.
"""
from __future__ import annotations

import json
import socket
import struct
import threading
import time
from collections import deque

from ports.transport import Message, TransportPort

_HEADER = struct.Struct(">I")


class SocketTransport(TransportPort):
    name = "socket"

    def __init__(self, players: tuple[str, str], host: str = "127.0.0.1", port: int = 47001,
                 delivery_timeout: float = 5.0):
        self.players = players
        self.host = host
        self.port = port  # 0 picks a free port (tests)
        self.delivery_timeout = delivery_timeout
        self._socks: dict[str, socket.socket] = {}
        self._threads: list[threading.Thread] = []
        self._cond = threading.Condition()
        self._inbox: dict[str, deque[Message]] = {}
        self._bytes_sent_to: dict[str, int] = {}
        self._bytes_received: dict[str, int] = {}
        self._seq = 0

    def open(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind((self.host, self.port))
            listener.listen(1)
            self.port = listener.getsockname()[1]
            client = socket.create_connection((self.host, self.port), timeout=self.delivery_timeout)
            server_side, _ = listener.accept()
        finally:
            listener.close()
        self._socks = {self.players[0]: server_side, self.players[1]: client}
        for player, sock in self._socks.items():
            sock.settimeout(None)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._inbox[player] = deque()
            self._bytes_sent_to[player] = 0
            self._bytes_received[player] = 0
            thread = threading.Thread(target=self._read_loop, args=(player, sock), daemon=True)
            thread.start()
            self._threads.append(thread)

    def send(self, sender: str, recipient: str, body: str) -> Message:
        with self._cond:
            self._seq += 1
            seq = self._seq
            sent_at = time.time()
            payload = json.dumps(
                {"seq": seq, "from": sender, "to": recipient, "sent_at": sent_at, "body": body},
                ensure_ascii=False,
            ).encode("utf-8")
            wire = _HEADER.pack(len(payload)) + payload
            self._bytes_sent_to[recipient] += len(wire)
        self._socks[sender].sendall(wire)
        return Message(seq, sender, recipient, body, sent_at, wire)

    def receive(self, recipient: str) -> list[Message]:
        with self._cond:
            # Everything already sent to this player is on the loopback; wait for it to land.
            self._cond.wait_for(
                lambda: self._bytes_received[recipient] >= self._bytes_sent_to[recipient],
                timeout=self.delivery_timeout,
            )
            messages = list(self._inbox[recipient])
            self._inbox[recipient].clear()
        return messages

    def close(self) -> None:
        for sock in self._socks.values():
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        for thread in self._threads:
            thread.join(timeout=2)
        self._socks, self._threads = {}, []

    def describe(self) -> dict:
        return {"transport": self.name, "host": self.host, "port": self.port}

    def _read_loop(self, player: str, sock: socket.socket) -> None:
        buf = bytearray()
        while True:
            try:
                chunk = sock.recv(65536)
            except OSError:
                return
            if not chunk:
                return
            buf.extend(chunk)
            frames = []
            while len(buf) >= _HEADER.size:
                (length,) = _HEADER.unpack_from(buf)
                if len(buf) < _HEADER.size + length:
                    break
                wire = bytes(buf[: _HEADER.size + length])
                del buf[: _HEADER.size + length]
                data = json.loads(wire[_HEADER.size:].decode("utf-8"))
                frames.append(Message(data["seq"], data["from"], data["to"], data["body"], data["sent_at"], wire))
            with self._cond:
                self._inbox[player].extend(frames)
                self._bytes_received[player] += len(chunk)
                self._cond.notify_all()
