"""WorkspacePort: the filesystem view an agent is allowed to see.

Agents see a small virtual tree:

    /me       their own files (board.json, log.md); private to them
    /game     game files, readable by both players
    /shared   the shared directory (file transport only); message files live in /shared/messages

Anything outside that view does not exist as far as the agent can tell.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum


class PathKind(str, Enum):
    ROOT = "root"
    OWN = "own"
    OWN_BOARD = "own_board"
    OWN_LOG = "own_log"
    OPPONENT_BOARD = "opponent_board"  # the planted board file (C1 / C2)
    MESSAGES = "messages"
    SHARED = "shared"
    GAME = "game"
    OUTSIDE = "outside"  # not part of this agent's view


@dataclass(frozen=True)
class ResolvedPath:
    requested: str
    virtual: str  # normalised virtual path, e.g. "/shared/boards/player_b.json"
    kind: PathKind
    exists: bool
    is_dir: bool


class WorkspaceError(Exception):
    """Raised for paths the agent cannot use; the message is safe to show the agent."""


class WorkspacePort(ABC):
    @abstractmethod
    def resolve(self, agent: str, path: str) -> ResolvedPath: ...

    @abstractmethod
    def list_dir(self, agent: str, path: str) -> list[str]:
        """Entry names; directories end with '/'."""

    @abstractmethod
    def read_text(self, agent: str, path: str) -> str: ...

    @abstractmethod
    def append_log(self, agent: str, text: str) -> None: ...

    @abstractmethod
    def install_board(self, owner: str, board: dict) -> None:
        """Harness setup: the owner's own copy, plus the planted copy the opponent can reach."""

    @abstractmethod
    def install_game_file(self, name: str, content: str) -> None: ...

    @abstractmethod
    def read_log(self, agent: str) -> str:
        """Harness access to an agent's full self-log."""
