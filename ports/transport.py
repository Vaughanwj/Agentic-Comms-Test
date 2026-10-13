"""TransportPort: carries messages between the two agents.

The transport is the experimental variable, so the harness only ever talks
to this interface and the concrete channel is chosen by config.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Message:
    seq: int
    sender: str
    recipient: str
    body: str
    sent_at: float  # wall-clock seconds since the epoch
    wire: bytes = field(default=b"", compare=False, repr=False)  # exact bytes on the channel


class TransportPort(ABC):
    name: str

    @abstractmethod
    def open(self) -> None: ...

    @abstractmethod
    def send(self, sender: str, recipient: str, body: str) -> Message: ...

    @abstractmethod
    def receive(self, recipient: str) -> list[Message]:
        """Return every message for `recipient` not yet received, oldest first."""

    @abstractmethod
    def close(self) -> None: ...

    def describe(self) -> dict:
        return {"transport": self.name}
