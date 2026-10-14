"""FileTransport: messages are files in a shared directory both agents can read.

Each message is one JSON file named "<seq>_<sender>_to_<recipient>.json".
Receiving reads the directory itself, so what an agent gets is exactly what
is on disk.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from ports.transport import Message, TransportPort


class FileTransport(TransportPort):
    name = "file"

    def __init__(self, directory: Path, virtual_dir: str = "/shared/messages"):
        self.directory = Path(directory)
        self.virtual_dir = virtual_dir
        self._seq = 0
        self._last_seen: dict[str, int] = {}

    def open(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)

    def send(self, sender: str, recipient: str, body: str) -> Message:
        self._seq += 1
        sent_at = time.time()
        wire = json.dumps(
            {"seq": self._seq, "from": sender, "to": recipient, "sent_at": sent_at, "body": body},
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
        final = self.directory / f"{self._seq:05d}_{sender}_to_{recipient}.json"
        tmp = final.with_suffix(".tmp")
        tmp.write_bytes(wire)
        os.replace(tmp, final)
        return Message(self._seq, sender, recipient, body, sent_at, wire)

    def receive(self, recipient: str) -> list[Message]:
        last = self._last_seen.get(recipient, 0)
        messages = []
        for path in sorted(self.directory.glob(f"*_to_{recipient}.json")):
            seq = int(path.name.split("_", 1)[0])
            if seq <= last:
                continue
            wire = path.read_bytes()
            data = json.loads(wire.decode("utf-8"))
            messages.append(Message(data["seq"], data["from"], data["to"], data["body"], data["sent_at"], wire))
        if messages:
            self._last_seen[recipient] = messages[-1].seq
        return messages

    def close(self) -> None:
        pass

    def describe(self) -> dict:
        return {"transport": self.name, "directory": self.virtual_dir}
