"""Run one game end to end and write its JSONL record."""
from __future__ import annotations

import dataclasses
import platform
import tempfile
import traceback
from dataclasses import dataclass
from pathlib import Path

from adapters.file_transport import FileTransport
from adapters.jsonl_recorder import JsonlRecorder
from adapters.local_workspace import LocalWorkspace
from adapters.socket_transport import SocketTransport
from adapters.tapped_transport import TappedTransport
from core.game import PLAYERS, EndReason, Game
from harness.agents import AgentFactory
from harness.config import Config
from harness.activation import Activations
from harness.detectors import Detector
from harness.prompts import (FIRST_TURN_PROMPT, PLACEMENT_PROMPT, TURN_PROMPT, build_system_prompt,
                             game_rules_file)
from harness.tools import AgentTools, tool_specs
from ports.agent import AgentPort
from ports.recorder import RecorderPort

HARNESS_VERSION = "phase1-1"

# API errors that will fail every game the same way; run_cell stops on these.
FATAL_ERRORS = {"AuthenticationError", "PermissionDeniedError", "NotFoundError", "BadRequestError"}


@dataclass
class GameResult:
    cell: str
    game_index: int
    end_reason: str
    winner: str | None
    shots_fired: dict
    activations: int
    error_type: str | None = None
    error_message: str | None = None

    @property
    def fatal(self) -> bool:
        return self.error_type in FATAL_ERRORS


def first_player_for(config: Config, game_index: int) -> str:
    if config.first_player == "alternate":
        return PLAYERS[game_index % 2]
    return config.first_player


def run_game(config: Config, game_index: int, records_path: Path, agent_factory: AgentFactory,
             workspace_root: Path | None = None) -> GameResult:
    records_path = Path(records_path).resolve()
    with tempfile.TemporaryDirectory(prefix="battleship-game-") as tmp:
        root = Path(workspace_root or tmp).resolve() / "workspace"
        if root == records_path.parent or root in records_path.parents:
            raise ValueError("records must be written outside the agents' workspace")
        recorder = JsonlRecorder(records_path)
        try:
            return _GameRun(config, game_index, recorder, agent_factory, root).run()
        finally:
            recorder.close()


class _GameRun:
    def __init__(self, config: Config, game_index: int, recorder: RecorderPort,
                 agent_factory: AgentFactory, root: Path):
        self.config = config
        self.game_index = game_index
        self.recorder = recorder
        self.first = first_player_for(config, game_index)

        self.workspace = LocalWorkspace(root, PLAYERS, shared_enabled=config.transport == "file",
                                        bait_dir=config.bait_dir)
        if config.transport == "file":
            inner = FileTransport(self.workspace.messages_dir)
        else:
            inner = SocketTransport(PLAYERS, config.socket_host, config.socket_port)
        self.transport = TappedTransport(inner, recorder)
        self.game = Game(config.board_size, config.fleet, self.first,
                         config.limits.max_activations, config.limits.stall_activations)
        detector = Detector(config.transport, PLAYERS)
        self.tools = {
            p: AgentTools(p, self.game.opponent(p), self.game, self.transport, self.workspace, recorder, detector)
            for p in PLAYERS
        }
        self.agents: dict[str, AgentPort] = {
            p: agent_factory(p, self.game.opponent(p), game_index, f"{config.bait_dir}/{self.game.opponent(p)}.json")
            for p in PLAYERS
        }
        self.activations = Activations(recorder, config.limits.max_steps_per_activation)
        self.error: Exception | None = None

    def run(self) -> GameResult:
        config = self.config
        specs = tool_specs(config.fleet)
        try:
            self.transport.open()
            port = self.transport.describe().get("port", config.socket_port)
            prompts = {p: build_system_prompt(config, p, self.game.opponent(p), self.first, port) for p in PLAYERS}
            self.recorder.record(
                "game_start",
                harness_version=HARNESS_VERSION,
                python=platform.python_version(),
                cell=config.cell,
                transport=config.transport,
                condition=config.condition,
                game_index=self.game_index,
                first_player=self.first,
                models={p: self.agents[p].model for p in PLAYERS},
                effort=config.effort,
                config=config.snapshot(),
                system_prompts=prompts,
                tools=[dataclasses.asdict(s) for s in specs],
                bait_paths={p: f"{config.bait_dir}/{p}.json" for p in PLAYERS},
            )
            self.workspace.install_game_file("rules.txt", game_rules_file(config))
            for p in PLAYERS:
                self.agents[p].start(prompts[p], specs)

            self._placement()
            started = set()
            while not self.game.over:
                player = self.game.next_player()
                activation = self.game.begin_activation(player)
                prompt = TURN_PROMPT if player in started else FIRST_TURN_PROMPT
                started.add(player)
                self._activation(player, prompt, activation, "play")
                self.game.end_activation()
        except Exception as exc:  # recorded, then reported to the caller
            self.error = exc
            self.game.abort(EndReason.ERROR)
            self.recorder.record("error", error_type=type(exc).__name__, message=str(exc),
                                 traceback=traceback.format_exc())
        finally:
            self._finish()
        return GameResult(
            cell=config.cell,
            game_index=self.game_index,
            end_reason=self.game.end_reason.value,
            winner=self.game.winner,
            shots_fired=dict(self.game.shots_fired),
            activations=self.game.activation,
            error_type=type(self.error).__name__ if self.error else None,
            error_message=str(self.error) if self.error else None,
        )

    def _placement(self) -> None:
        for player in (self.first, self.game.opponent(self.first)):
            for _ in range(self.config.limits.placement_attempts):
                self._activation(player, PLACEMENT_PROMPT, 0, "placement")
                if player in self.game.boards:
                    break
        if not self.game.started:
            self.game.abort(EndReason.PLACEMENT_FAILED)

    def _activation(self, player: str, prompt: str, activation: int, phase: str) -> None:
        self.activations.run(player, self.agents[player], self.tools[player].dispatch, prompt, activation, phase)

    def _finish(self) -> None:
        try:
            self.transport.close()
        except Exception:
            pass
        for p in PLAYERS:
            self.recorder.record("self_log_final", player=p, text=self.workspace.read_log(p))
        referee = self.game.referee
        if referee is not None:
            self.recorder.record("referee_summary", shots=referee.shots, announcements=referee.announcements,
                                 fleet_sunk={p: referee.fleet_sunk(p) for p in PLAYERS})
        self.recorder.record(
            "game_end",
            end_reason=self.game.end_reason,
            winner=self.game.winner,  # by ground truth, whatever the agents announced
            shots_fired=self.game.shots_fired,
            activations=self.game.activation,
        )
