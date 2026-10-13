import json
import subprocess

import pytest

from harness import cells
from harness.config import Sandbox


def test_docker_mode_runs_each_game_in_a_fresh_sealed_container(config, tmp_path, monkeypatch):
    config = config.with_overrides(records_dir=tmp_path / "records", transport="file", condition="C",
                                   sandbox=Sandbox("docker", "img:tag", "battleship_sandbox", "http://egress-proxy:3128"))
    seen = {}

    def fake_run(cmd, capture_output, text):
        seen["cmd"] = cmd
        result = {"cell": "file-C", "game_index": 3, "end_reason": "fleet_sunk", "winner": "player_a",
                  "shots_fired": {"player_a": 40, "player_b": 39}, "activations": 80,
                  "error_type": None, "error_message": None}
        return subprocess.CompletedProcess(cmd, 0, stdout="log line\n" + json.dumps(result) + "\n", stderr="")

    monkeypatch.setattr(cells.subprocess, "run", fake_run)
    result = cells.play(config, 3, "run1", "llm")
    cmd = seen["cmd"]

    assert result.winner == "player_a" and result.end_reason == "fleet_sunk"
    assert cmd[:3] == ["docker", "run", "--rm"]
    assert cmd[cmd.index("--network") + 1] == "battleship_sandbox"
    assert "--read-only" in cmd
    assert "ANTHROPIC_API_KEY" in cmd and "ANTHROPIC_WORKSPACE_ID" in cmd  # passed by name, never by value
    assert "BATTLESHIP_EGRESS_PROXY=http://egress-proxy:3128" in cmd
    mounts = [cmd[i + 1] for i, part in enumerate(cmd) if part == "-v"]
    assert any(m.endswith(":/records") for m in mounts)
    assert any(m.endswith(":/config/run.toml:ro") for m in mounts)
    assert cmd[cmd.index("--out") + 1] == "/records/run1/file-C/game_003.jsonl"
    assert cmd[cmd.index("--transport") + 1] == "file" and cmd[cmd.index("--condition") + 1] == "C"


def test_existing_record_is_never_overwritten(config, tmp_path):
    config = config.with_overrides(records_dir=tmp_path, sandbox=Sandbox("local", "", "", None))
    path = cells.game_path(tmp_path, "run1", config.cell, 0)
    path.parent.mkdir(parents=True)
    path.write_text("{}\n")
    with pytest.raises(FileExistsError):
        cells.play(config, 0, "run1", "scripted")
