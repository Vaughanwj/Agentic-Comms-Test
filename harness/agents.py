"""Build the agents for a game from config."""
from __future__ import annotations

import os
import zlib
from typing import Callable

from adapters.llm_agent import LlmAgent
from adapters.scripted_agent import Misbehaviour, ScriptedAgent
from harness.config import Config
from ports.agent import AgentPort

# (player, opponent, game_index, bait_path) -> agent
AgentFactory = Callable[[str, str, int, str], AgentPort]


def llm_factory(config: Config) -> AgentFactory:
    proxy = os.environ.get("BATTLESHIP_EGRESS_PROXY") or None
    workspace_id = os.environ.get("ANTHROPIC_WORKSPACE_ID") or None

    def make(player: str, opponent: str, game_index: int, bait_path: str) -> AgentPort:
        return LlmAgent(
            model=config.model_for(player),
            effort=config.effort,
            max_tokens=config.limits.max_tokens,
            proxy=proxy,
            workspace_id=workspace_id,
        )

    return make


def scripted_factory(config: Config, misbehaviour: dict[str, Misbehaviour] | None = None) -> AgentFactory:
    misbehaviour = misbehaviour or {}

    def make(player: str, opponent: str, game_index: int, bait_path: str) -> AgentPort:
        seed = zlib.crc32(f"{config.seed}:{game_index}:{player}".encode())
        return ScriptedAgent(player, opponent, config.board_size, config.fleet, bait_path, seed,
                             misbehaviour.get(player, Misbehaviour()))

    return make


def factory_for(kind: str, config: Config) -> AgentFactory:
    if kind == "llm":
        return llm_factory(config)
    if kind == "scripted":
        return scripted_factory(config)
    raise ValueError(f"unknown agent kind: {kind}")
