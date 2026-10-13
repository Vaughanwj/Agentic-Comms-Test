"""Running games: in this process, or one fresh Docker container per game."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
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
    """Write the run manifest. A resumed run gets manifest_2.json and so on; nothing is overwritten."""
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
        "docker_image_id": _image_id(config) if config.sandbox.mode == "docker" else None,
        "config": config.snapshot(),
    }
    path = run_dir / "manifest.json"
    n = 1
    while path.exists():
        n += 1
        path = run_dir / f"manifest_{n}.json"
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path


def finished(path: Path) -> bool:
    """True if this record holds a game that ended normally (not aborted by an error)."""
    if not path.exists():
        return False
    for line in path.read_text(encoding="utf-8").splitlines():
        if '"kind": "game_end"' in line:
            return json.loads(line).get("end_reason") != "error"
    return False


def set_aside(path: Path) -> Path:
    """Keep an aborted game's record under a name the analysis ignores."""
    n = 1
    while (target := path.with_name(f"aborted_{path.stem}_{n}.jsonl")).exists():
        n += 1
    path.rename(target)
    return target


def play(config: Config, game_index: int, run_id: str, agent_kind: str, resume: bool = False) -> GameResult:
    """Play one game of config's cell, locally or in a fresh container.

    With resume, an aborted record for this game is set aside and the game replayed.
    """
    out = game_path(config.records_dir, run_id, config.cell, game_index)
    if out.exists():
        if not resume or finished(out):
            raise FileExistsError(f"{out} already exists; records are append-only, pick a new run id")
        set_aside(out)
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


def run_games(plan: list[tuple[Config, int]], run_id: str, agent_kind: str, parallel: int = 1,
              resume: bool = False) -> list[GameResult]:
    """Play every (cell config, game index), up to `parallel` at a time, starting them in plan order.

    Stops starting new games after an error that would repeat for every game. With resume,
    games that already finished in this run are skipped and aborted ones are replayed.
    """
    if resume:
        done = [(c, i) for c, i in plan if finished(game_path(c.records_dir, run_id, c.cell, i))]
        plan = [p for p in plan if p not in done]
        print(f"Resuming {run_id}: {len(done)} games already finished, {len(plan)} to play.", flush=True)
    if parallel > 1:
        # Games in one process cannot share the fixed port; each container has its own localhost.
        plan = [(c.with_overrides(socket_port=0) if c.sandbox.mode == "local" else c, i) for c, i in plan]
    results: list[GameResult] = []
    lock = threading.Lock()
    stop = threading.Event()

    def one(cell_config: Config, game_index: int) -> None:
        if stop.is_set():
            return
        result = play(cell_config, game_index, run_id, agent_kind, resume)
        with lock:
            results.append(result)
            status = result.end_reason + (f" ({result.error_type}: {result.error_message})" if result.error_type else "")
            print(f"[{len(results)}/{len(plan)}] {result.cell} game {game_index}: {status}; winner {result.winner}",
                  flush=True)
            if (result.error_type in FATAL_ERRORS or result.error_type == "DockerError") and not stop.is_set():
                stop.set()
                print("Stopping: this error would repeat for every game. Games already running will finish or "
                      "fail; continue later with --resume.", file=sys.stderr, flush=True)

    with ThreadPoolExecutor(max_workers=max(1, parallel)) as pool:
        for future in [pool.submit(one, c, i) for c, i in plan]:
            future.result()
    return results


def _image_id(config: Config) -> str | None:
    try:
        return subprocess.run(["docker", "image", "inspect", "--format", "{{.Id}}", config.sandbox.image],
                              capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _git_commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                              cwd=Path(__file__).resolve().parent.parent, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return os.environ.get("BATTLESHIP_GIT_COMMIT")
