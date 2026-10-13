"""RecorderPort: append-only experiment records (tap, harness, referee, agent)."""
from __future__ import annotations

from abc import ABC, abstractmethod


class RecorderPort(ABC):
    @abstractmethod
    def record(self, kind: str, **fields) -> None: ...

    def close(self) -> None:
        pass
