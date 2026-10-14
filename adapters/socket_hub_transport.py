"""SocketHubTransport: every agent has its own TCP connection to a switchboard.

The switchboard listens on localhost. Each agent's end is a client socket;
the switchboard keeps the matching accepted socket. A frame an agent sends is
read by the switchboard and written, byte for byte, to the recipient's
connection, or to every other agent's connection when it is addressed to
"all". Frames are a 4-byte big-endian length followed by UTF-8 JSON.

Reader threads drain every socket as bytes arrive, so no one can fill a
buffer and block the game.
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
EVERYONE = "all"


def _frames(sock: socket.socket):
    """Yield complete frames read from sock until it closes."""
    buf = bytearray()
    while True:
        try:
            chunk = sock.recv(65536)
        except OSError:
            return
        if not chunk:
            return
        buf.extend(chunk)
        while len(buf) >= _HEADER.size:
            (length,) = _HEADER.unpack_from(buf)
            if len(buf) < _HEADER.size + length:
                break
            frame = bytes(buf[: _HEADER.size + length])
            del buf[: _HEADER.size + length]
            yield frame


class SocketHubTransport(TransportPort):
    name = "socket"

    def __init__(self, players: tuple[str, ...], host: str = "127.0.0.1", port: int = 47101,
                 delivery_timeout: float = 5.0):
        self.players = tuple(players)
        self.host = host
        self.port = port  # 0 picks a free port (tests, parallel local games)
        self.delivery_timeout = delivery_timeout
        self._client: dict[str, socket.socket] = {}  # agent ends
        self._hub: dict[str, socket.socket] = {}  # switchboard ends
        self._hub_locks: dict[str, threading.Lock] = {}
        self._threads: list[threading.Thread] = []
        self._cond = threading.Condition()
        self._inbox: dict[str, deque[Message]] = {}
        self._expected: dict[str, int] = {}
        self._received: dict[str, int] = {}
        self._seq = 0

    def recipients(self, sender: str, recipient: str) -> list[str]:
        if recipient == EVERYONE:
            return [p for p in self.players if p != sender]
        if recipient not in self.players or recipient == sender:
            raise ValueError(f"cannot send from {sender} to {recipient}")
        return [recipient]

    def open(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind((self.host, self.port))
            listener.listen(len(self.players))
            self.port = listener.getsockname()[1]
            for player in self.players:
                # Connect then accept straight away, so each accepted socket belongs to this player.
                self._client[player] = socket.create_connection((self.host, self.port), timeout=self.delivery_timeout)
                self._hub[player], _ = listener.accept()
        finally:
            listener.close()
        for player in self.players:
            for sock in (self._client[player], self._hub[player]):
                sock.settimeout(None)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._hub_locks[player] = threading.Lock()
            self._inbox[player] = deque()
            self._expected[player] = 0
            self._received[player] = 0
        for player in self.players:
            for target in (self._route_loop, self._deliver_loop):
                thread = threading.Thread(target=target, args=(player,), daemon=True)
                thread.start()
                self._threads.append(thread)

    def send(self, sender: str, recipient: str, body: str) -> Message:
        targets = self.recipients(sender, recipient)
        with self._cond:
            self._seq += 1
            seq = self._seq
            sent_at = time.time()
            payload = json.dumps(
                {"seq": seq, "from": sender, "to": recipient, "sent_at": sent_at, "body": body}, ensure_ascii=False
            ).encode("utf-8")
            wire = _HEADER.pack(len(payload)) + payload
            for target in targets:
                self._expected[target] += len(wire)
        self._client[sender].sendall(wire)
        return Message(seq, sender, recipient, body, sent_at, wire)

    def receive(self, recipient: str) -> list[Message]:
        """Everything sent to `recipient` so far, in the order it was sent.

        Frames from different senders pass through separate switchboard threads and can
        arrive in either order; waiting for every expected byte and sorting by sequence
        number gives the true send order.
        """
        with self._cond:
            self._cond.wait_for(lambda: self._received[recipient] >= self._expected[recipient],
                                timeout=self.delivery_timeout)
            messages = sorted(self._inbox[recipient], key=lambda m: m.seq)
            self._inbox[recipient].clear()
        return messages

    def close(self) -> None:
        for sock in list(self._client.values()) + list(self._hub.values()):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        for thread in self._threads:
            thread.join(timeout=2)
        self._client, self._hub, self._threads = {}, {}, []

    def describe(self) -> dict:
        return {"transport": self.name, "topology": "switchboard", "host": self.host, "port": self.port}

    # switchboard and agent-side threads -----------------------------------------

    def _route_loop(self, sender: str) -> None:
        """Switchboard side: forward each frame from `sender` to its recipients."""
        for frame in _frames(self._hub[sender]):
            data = json.loads(frame[_HEADER.size:].decode("utf-8"))
            for target in self.recipients(data["from"], data["to"]):
                with self._hub_locks[target]:
                    try:
                        self._hub[target].sendall(frame)
                    except OSError:
                        return

    def _deliver_loop(self, player: str) -> None:
        """Agent side: collect frames arriving on `player`'s connection."""
        for frame in _frames(self._client[player]):
            data = json.loads(frame[_HEADER.size:].decode("utf-8"))
            message = Message(data["seq"], data["from"], data["to"], data["body"], data["sent_at"], frame)
            with self._cond:
                self._inbox[player].append(message)
                self._received[player] += len(frame)
                self._cond.notify_all()
