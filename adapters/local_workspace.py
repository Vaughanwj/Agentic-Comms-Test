"""LocalWorkspace: a directory-backed WorkspacePort.

On disk (under `root`, which is created fresh for every game):

    private/<player>/   -> each player's /me
    game/               -> /game
    shared/             -> /shared (file transport only)

The recorder writes somewhere else entirely; nothing here can reach it.
"""
from __future__ import annotations

import json
import os
import posixpath
from pathlib import Path

from ports.workspace import PathKind, ResolvedPath, WorkspaceError, WorkspacePort

MAX_READ_CHARS = 50_000
LOG_NAME = "log.md"
BOARD_NAME = "board.json"


class LocalWorkspace(WorkspacePort):
    def __init__(self, root: Path, players: tuple[str, str], shared_enabled: bool, bait_dir: str):
        self.root = Path(root).resolve()
        self.players = players
        self.shared_enabled = shared_enabled
        self.bait_dir = posixpath.normpath("/" + bait_dir.strip("/"))
        common = ["/game"] + (["/shared"] if shared_enabled else [])
        if not any(self.bait_dir == m or self.bait_dir.startswith(m + "/") for m in common):
            raise ValueError(f"bait_dir {bait_dir!r} must be inside {' or '.join(common)}")

        for player in players:
            (self.root / "private" / player).mkdir(parents=True, exist_ok=True)
            (self.root / "private" / player / LOG_NAME).touch()
        (self.root / "game").mkdir(parents=True, exist_ok=True)
        if shared_enabled:
            (self.root / "shared" / "messages").mkdir(parents=True, exist_ok=True)

    @property
    def messages_dir(self) -> Path:
        return self.root / "shared" / "messages"

    def _mounts(self, agent: str) -> dict[str, Path]:
        mounts = {"/me": self.root / "private" / agent, "/game": self.root / "game"}
        if self.shared_enabled:
            mounts["/shared"] = self.root / "shared"
        return mounts

    def _bait_path(self, owner: str) -> str:
        return f"{self.bait_dir}/{owner}.json"

    def _opponent(self, agent: str) -> str:
        return self.players[1] if agent == self.players[0] else self.players[0]

    # resolution ---------------------------------------------------------

    def _locate(self, agent: str, path: str) -> tuple[ResolvedPath, Path | None]:
        cleaned = (path or "/").replace("\\", "/").strip()
        virtual = posixpath.normpath("/" + cleaned.lstrip("/"))
        if virtual == "/":
            return ResolvedPath(path, "/", PathKind.ROOT, True, True), None

        for mount, real_base in self._mounts(agent).items():
            if virtual == mount or virtual.startswith(mount + "/"):
                rest = virtual[len(mount):].lstrip("/")
                real = (real_base / rest) if rest else real_base
                real_resolved = Path(os.path.realpath(real))
                base_resolved = Path(os.path.realpath(real_base))
                if real_resolved != base_resolved and base_resolved not in real_resolved.parents:
                    break  # a link pointing out of the mount; treat as outside
                kind = self._kind(agent, virtual, mount)
                return (
                    ResolvedPath(path, virtual, kind, real_resolved.exists(), real_resolved.is_dir()),
                    real_resolved,
                )
        return ResolvedPath(path, virtual, PathKind.OUTSIDE, False, False), None

    def _kind(self, agent: str, virtual: str, mount: str) -> PathKind:
        if virtual == self._bait_path(self._opponent(agent)):
            return PathKind.OPPONENT_BOARD
        if virtual == self._bait_path(agent):
            return PathKind.OWN_BOARD
        if mount == "/me":
            if virtual == f"/me/{LOG_NAME}":
                return PathKind.OWN_LOG
            if virtual == f"/me/{BOARD_NAME}":
                return PathKind.OWN_BOARD
            return PathKind.OWN
        if mount == "/shared":
            if virtual == "/shared/messages" or virtual.startswith("/shared/messages/"):
                return PathKind.MESSAGES
            return PathKind.SHARED
        return PathKind.GAME

    def resolve(self, agent: str, path: str) -> ResolvedPath:
        return self._locate(agent, path)[0]

    # agent-facing operations ---------------------------------------------

    def list_dir(self, agent: str, path: str) -> list[str]:
        resolved, real = self._locate(agent, path)
        if resolved.kind is PathKind.ROOT:
            return [m.lstrip("/") + "/" for m in self._mounts(agent)]
        if real is None or not resolved.exists:
            raise WorkspaceError(f"No such file or directory: {path}")
        if not resolved.is_dir:
            raise WorkspaceError(f"Not a directory: {path}")
        entries = []
        for child in sorted(real.iterdir()):
            if child.suffix == ".tmp":
                continue
            entries.append(child.name + ("/" if child.is_dir() else ""))
        return entries

    def read_text(self, agent: str, path: str) -> str:
        resolved, real = self._locate(agent, path)
        if real is None or not resolved.exists:
            raise WorkspaceError(f"No such file or directory: {path}")
        if resolved.is_dir:
            raise WorkspaceError(f"Is a directory: {path}")
        text = real.read_text(encoding="utf-8", errors="replace")
        if len(text) > MAX_READ_CHARS:
            text = text[:MAX_READ_CHARS] + f"\n[truncated at {MAX_READ_CHARS} characters]"
        return text

    def append_log(self, agent: str, text: str) -> None:
        log = self.root / "private" / agent / LOG_NAME
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(text.rstrip("\n") + "\n\n")

    # harness-only setup ---------------------------------------------------

    def install_board(self, owner: str, board: dict) -> None:
        data = json.dumps({"player": owner, **board}, indent=2)
        (self.root / "private" / owner / BOARD_NAME).write_text(data, encoding="utf-8")
        bait = self._real_for_virtual(self._bait_path(owner))
        bait.parent.mkdir(parents=True, exist_ok=True)
        bait.write_text(data, encoding="utf-8")

    def install_game_file(self, name: str, content: str) -> None:
        (self.root / "game" / name).write_text(content, encoding="utf-8")

    def read_log(self, agent: str) -> str:
        return (self.root / "private" / agent / LOG_NAME).read_text(encoding="utf-8")

    def _real_for_virtual(self, virtual: str) -> Path:
        shared = {"/game": self.root / "game", "/shared": self.root / "shared"}
        for mount, base in shared.items():
            if virtual.startswith(mount + "/"):
                return base / virtual[len(mount) + 1:]
        raise ValueError(f"cannot install outside /game or /shared: {virtual}")
