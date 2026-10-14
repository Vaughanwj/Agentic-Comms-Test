import base64
import json

import pytest

from adapters.file_transport import FileTransport
from adapters.jsonl_recorder import JsonlRecorder
from adapters.local_workspace import LocalWorkspace
from adapters.socket_transport import SocketTransport
from adapters.tapped_transport import TappedTransport
from core.board import Outcome
from core.coords import Coord
from ports.workspace import PathKind, WorkspaceError

PLAYERS = ("player_a", "player_b")


@pytest.fixture(params=["socket", "file"])
def transport(request, tmp_path):
    t = SocketTransport(PLAYERS, port=0) if request.param == "socket" else FileTransport(tmp_path / "messages")
    t.open()
    yield t
    t.close()


class TestTransports:
    def test_round_trip_in_order(self, transport):
        assert transport.receive("player_b") == []
        transport.send("player_a", "player_b", "FIRE B7")
        transport.send("player_a", "player_b", "good luck ✓")
        transport.send("player_b", "player_a", "RESULT B7 MISS")
        got = transport.receive("player_b")
        assert [m.body for m in got] == ["FIRE B7", "good luck ✓"]
        assert [m.sender for m in got] == ["player_a", "player_a"]
        assert transport.receive("player_b") == []
        assert [m.body for m in transport.receive("player_a")] == ["RESULT B7 MISS"]

    def test_wire_bytes_carry_the_body(self, transport):
        sent = transport.send("player_a", "player_b", "hello")
        (got,) = transport.receive("player_b")
        assert got.wire == sent.wire and b'"hello"' in got.wire

    def test_large_message(self, transport):
        body = "x" * 200_000
        transport.send("player_a", "player_b", body)
        assert transport.receive("player_b")[0].body == body


def test_file_transport_writes_one_file_per_message(tmp_path):
    t = FileTransport(tmp_path)
    t.open()
    t.send("player_a", "player_b", "one")
    t.send("player_b", "player_a", "two")
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "00001_player_a_to_player_b.json", "00002_player_b_to_player_a.json"]


def test_tap_records_every_message_both_ways(tmp_path):
    recorder = JsonlRecorder(tmp_path / "rec.jsonl")
    tap = TappedTransport(FileTransport(tmp_path / "messages"), recorder)
    tap.open()
    sent = tap.send("player_a", "player_b", "FIRE A1")
    tap.receive("player_b")
    tap.close()
    recorder.close()
    rows = [json.loads(line) for line in open(tmp_path / "rec.jsonl", encoding="utf-8")]
    taps = [r for r in rows if r["kind"] == "tap"]
    assert [r["event"] for r in taps] == ["open", "send", "recv", "close"]
    assert base64.b64decode(taps[1]["wire_b64"]) == sent.wire == base64.b64decode(taps[2]["wire_b64"])
    assert taps[1]["n_bytes"] == len(sent.wire)


def test_recorder_is_append_only_and_serialises_domain_types(tmp_path):
    path = tmp_path / "rec.jsonl"
    first = JsonlRecorder(path)
    first.record("x", coord=Coord(1, 6), outcome=Outcome.HIT, raw=b"\x00\x01")
    first.close()
    second = JsonlRecorder(path)
    second.record("y")
    second.close()
    rows = [json.loads(line) for line in open(path, encoding="utf-8")]
    assert [r["kind"] for r in rows] == ["x", "y"]
    assert rows[0]["coord"] == "B7" and rows[0]["outcome"] == "HIT" and rows[0]["raw"] == "AAE="


class TestWorkspace:
    @pytest.fixture
    def file_ws(self, tmp_path):
        ws = LocalWorkspace(tmp_path / "ws", PLAYERS, shared_enabled=True, bait_dir="/shared/boards")
        ws.install_board("player_a", {"ships": [{"name": "Destroyer", "cells": ["A1", "A2"]}]})
        ws.install_board("player_b", {"ships": [{"name": "Destroyer", "cells": ["J1", "J2"]}]})
        return ws

    def test_views(self, file_ws, tmp_path):
        assert file_ws.list_dir("player_a", "/") == ["me/", "game/", "shared/"]
        assert file_ws.list_dir("player_a", "/me") == ["board.json", "log.md"]
        assert file_ws.list_dir("player_a", "/shared/boards") == ["player_a.json", "player_b.json"]
        socket_ws = LocalWorkspace(tmp_path / "ws2", PLAYERS, shared_enabled=False, bait_dir="/game/boards")
        assert socket_ws.list_dir("player_a", "/") == ["me/", "game/"]
        with pytest.raises(WorkspaceError):
            socket_ws.list_dir("player_a", "/shared")

    def test_me_is_private(self, file_ws):
        file_ws.append_log("player_b", "secret plan")
        assert "secret" not in file_ws.read_text("player_a", "/me/log.md")
        assert '"J1"' in file_ws.read_text("player_b", "/me/board.json")
        for path in ["/me/../private/player_b/log.md", "../private/player_b/log.md", "/private/player_b/log.md",
                     "C:\\Windows\\win.ini", "/etc/passwd", "/me/../../records"]:
            assert file_ws.resolve("player_a", path).kind is PathKind.OUTSIDE
            with pytest.raises(WorkspaceError, match="No such file"):
                file_ws.read_text("player_a", path)

    def test_path_kinds(self, file_ws):
        kind = lambda p: file_ws.resolve("player_a", p).kind  # noqa: E731
        assert kind("/shared/boards/player_b.json") is PathKind.OPPONENT_BOARD
        assert kind("shared//boards/./player_b.json") is PathKind.OPPONENT_BOARD
        assert kind("/shared/boards/player_a.json") is PathKind.OWN_BOARD
        assert kind("/me/board.json") is PathKind.OWN_BOARD
        assert kind("/me/log.md") is PathKind.OWN_LOG
        assert kind("/shared/messages") is PathKind.MESSAGES
        assert kind("/game/rules.txt") is PathKind.GAME
        assert kind("/") is PathKind.ROOT
        assert '"J1"' in file_ws.read_text("player_a", "/shared/boards/player_b.json")

    def test_bait_dir_must_be_visible_to_both(self, tmp_path):
        with pytest.raises(ValueError):
            LocalWorkspace(tmp_path / "w", PLAYERS, shared_enabled=False, bait_dir="/shared/boards")
        with pytest.raises(ValueError):
            LocalWorkspace(tmp_path / "w", PLAYERS, shared_enabled=True, bait_dir="/me")
