"""TappedTransport: records every byte that crosses the wrapped transport.

Agents never get a handle on this object or its recorder; it sits between
the harness tools and the real channel.
"""
from __future__ import annotations

import base64

from ports.recorder import RecorderPort
from ports.transport import Message, TransportPort


class TappedTransport(TransportPort):
    def __init__(self, inner: TransportPort, recorder: RecorderPort):
        self.inner = inner
        self.recorder = recorder
        self.name = inner.name

    def open(self) -> None:
        self.inner.open()
        self.recorder.record("tap", event="open", **self.inner.describe())

    def send(self, sender: str, recipient: str, body: str) -> Message:
        message = self.inner.send(sender, recipient, body)
        self._tap("send", message)
        return message

    def receive(self, recipient: str) -> list[Message]:
        messages = self.inner.receive(recipient)
        for message in messages:
            self._tap("recv", message, receiver=recipient)
        return messages

    def close(self) -> None:
        self.inner.close()
        self.recorder.record("tap", event="close", transport=self.name)

    def describe(self) -> dict:
        return self.inner.describe()

    def _tap(self, direction: str, message: Message, receiver: str | None = None) -> None:
        extra = {"receiver": receiver} if receiver else {}
        self.recorder.record(
            "tap",
            event=direction,
            transport=self.name,
            msg_seq=message.seq,
            sender=message.sender,
            recipient=message.recipient,
            sent_at=message.sent_at,
            n_bytes=len(message.wire),
            wire_b64=base64.b64encode(message.wire).decode("ascii"),
            body=message.body,
            **extra,
        )
