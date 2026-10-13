"""Running games: in this process, or one fresh Docker container per game."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from harness.agents import factory_for
from harness.config import Config
from harness.game_runner import FATAL_ERRORS, GameResult, run_game

CONTAINER_RECORDS = "/records"
CONTAINER_CONFIG = "/config/run.toml"


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def game_path(records_dir: Path, run_id: str, cell: str, game_index: int) -> Path:
    return Path(records_dir) / run_id / cell / f"game_{game_index:03d}.jsonl"


def write_manifest(config: Config, run_id: str, agent_kind: str, cells: list[str]) -> Path:
    run_dir = Path(config.records_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "run_id": run_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "agent_kind": agent_kind,
        "sandbox": config.sandbox.mode,
        "cells": cells,
        "games_per_cell": config.games_per_cell,
        "git_commit": _git_commit(),
        "config": config.snapshot(),
    }
    path = run_dir / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path


def play(config: Config, game_index: int, run_id: str, agent_kind: str) -> GameResult:
    """Play one game of config's cell, locally or in a fresh container."""
    out = game_path(config.records_dir, run_id, config.cell, game_index)
    if out.exists():
        raise FileExistsError(f"{out} already exists; records are append-only, pick a new run id")
    if config.sandbox.mode == "local":
        return run_game(config, game_index, out, factory_for(agent_kind, config))
    return _play_in_docker(config, game_index, run_id, agent_kind, out)


def _play_in_docker(config: Config, game_index: int, run_id: str, agent_kind: str, out: Path) -> GameResult:
    if config.source_path is None:
        raise ValueError("docker mode needs a config file on disk")
    out.parent.mkdir(parents=True, exist_ok=True)
    container_out = f"{CONTAINER_RECORDS}/{run_id}/{config.cell}/{out.name}"
    cmd = [
        "docker", "run", "--rm",
        "--network", config.sandbox.network,
        "--read-only", "--tmpfs", "/tmp:rw,size=64m",
        "-e", "ANTHROPIC_API_KEY",
        "-e", "ANTHROPIC_WORKSPACE_ID",
        "-v", f"{Path(config.records_dir).resolve()}:{CONTAINER_RECORDS}",
        "-v", f"{config.source_path}:{CONTAINER_CONFIG}:ro",
    ]
    if config.sandbox.proxy:
        cmd += ["-e", f"BATTLESHIP_EGRESS_PROXY={config.sandbox.proxy}"]
    cmd += [
        config.sandbox.image,
        "python", "-m", "harness.play_game",
        "--config", CONTAINER_CONFIG,
        "--transport", config.transport,
        "--condition", config.condition,
        "--games-per-cell", str(config.games_per_cell),
        "--game-index", str(game_index),
        "--agent", agent_kind,
        "--out", container_out,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    result_line = next((l for l in reversed(proc.stdout.splitlines()) if l.startswith("{")), None)
    if result_line is None:
        return GameResult(config.cell, game_index, "error", None, {}, 0, "DockerError",
                          (proc.stderr or proc.stdout).strip()[-2000:])
    data = json.loads(result_line)
    return GameResult(**data)


def run_games(plan: list[tuple[Config, int]], run_id: str, agent_kind: str) -> list[GameResult]:
    """Play every (cell config, game index) in order. Stops early on errors that would repeat."""
    results = []
    for n, (cell_config, game_index) in enumerate(plan, 1):
        result = play(cell_config, game_index, run_id, agent_kind)
        results.append(result)
        status = result.end_reason + (f" ({result.error_type}: {result.error_message})" if result.error_type else "")
        print(f"[{n}/{len(plan)}] {result.cell} game {game_index}: {status}; winner {result.winner}", flush=True)
        if result.error_type in FATAL_ERRORS or result.error_type == "DockerError":
            print("Stopping: this error would repeat for every game.", file=sys.stderr)
            break
    return results


def _git_commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                              cwd=Path(__file__).resolve().parent.parent, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return os.environ.get("BATTLESHIP_GIT_COMMIT")
