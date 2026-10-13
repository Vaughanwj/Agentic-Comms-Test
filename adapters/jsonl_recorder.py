"""JsonlRecorder: one append-only JSON line per event."""
from __future__ import annotations

import base64
import dataclasses
import json
import threading
import time
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from core.coords import Coord
from ports.recorder import RecorderPort


def _default(value):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Coord):
        return value.label()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: getattr(value, f.name) for f in dataclasses.fields(value)}
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"cannot record {type(value).__name__}")


class JsonlRecorder(RecorderPort):
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a", encoding="utf-8")
        self._seq = 0
        self._lock = threading.Lock()

    def record(self, kind: str, **fields) -> None:
        with self._lock:
            line = {
                "seq": self._seq,
                "ts": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
                "mono": time.monotonic(),
                "kind": kind,
                **fields,
            }
            self._fh.write(json.dumps(line, default=_default, ensure_ascii=False) + "\n")
            self._fh.flush()
            self._seq += 1

    def close(self) -> None:
        with self._lock:
            if not self._fh.closed:
                self._fh.close()
