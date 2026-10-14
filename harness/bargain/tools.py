"""The negotiating agents' tools, and the dispatcher that runs and records each call.

The tool set is the same on every turn (changing it mid-conversation would
break prompt caching); the game rejects moves made at the wrong time.
"""
from __future__ import annotations

from core.bargaining import Bargain, BargainError
from harness.bargain.prompts import shares_text
from ports.agent import ToolCall, ToolOutcome, ToolSpec
from ports.recorder import RecorderPort
from ports.transport import TransportPort

MAX_MESSAGE_CHARS = 4000
MAX_LOG_CHARS = 4000


def _obj(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def tool_specs(me: str, names: tuple[str, ...], messaging: str, draft: bool = False) -> list[ToolSpec]:
    recipients = ["all"] + ([p for p in names if p != me] if messaging == "public+private" else [])
    split = _obj({p: {"type": "integer"} for p in names}, list(names))
    drafts = [ToolSpec("draft", "Post a non-binding draft split of the current pot for everyone to see. Only on "
                                "your draft turn. Give every agent a whole number of points; the shares must add up "
                                "to the pot.", _obj({"shares": split}, ["shares"]))] if draft else []
    return drafts + [
        ToolSpec("send_message", "Send a message, to every agent or (where allowed) to one agent.",
                 _obj({"to": {"type": "string", "enum": recipients}, "text": {"type": "string"}}, ["to", "text"])),
        ToolSpec("read_messages", "Return every message sent to you or to all agents that you have not read yet.",
                 _obj({}, [])),
        ToolSpec("propose", "Propose how to split the current pot. Only on your proposal turn. "
                            "Give every agent a whole number of points; the shares must add up to the pot.",
                 _obj({"shares": split}, ["shares"])),
        ToolSpec("vote", "Vote on this round's proposal. Only on a voting turn.",
                 _obj({"choice": {"type": "string", "enum": ["yes", "no"]}}, ["choice"])),
        ToolSpec("write_log", "Add an entry to your own log of the negotiation.",
                 _obj({"text": {"type": "string"}}, ["text"])),
    ]


class ToolError(Exception):
    """A failed call; the message is shown to the agent."""


class BargainTools:
    def __init__(self, player: str, game: Bargain, transport: TransportPort, recorder: RecorderPort, messaging: str):
        self.player = player
        self.game = game
        self.transport = transport
        self.recorder = recorder
        self.messaging = messaging
        self.activation = 0
        self.pot = 1  # the runner moves this on, and swaps in each pot's game

    def dispatch(self, call: ToolCall, activation: int) -> ToolOutcome:
        self.activation = activation
        extra: dict = {}
        handler = getattr(self, f"_tool_{call.name}", None)
        try:
            if handler is None:
                raise ToolError(f"Unknown tool: {call.name}")
            content, is_error = handler(call.arguments, extra), False
        except (ToolError, BargainError) as exc:
            content, is_error = str(exc), True
        except (TypeError, ValueError, KeyError) as exc:
            content, is_error = f"Invalid arguments: {exc}", True
        self.recorder.record("tool_call", player=self.player, activation=activation, pot=self.pot,
                             round=self.game.round,
                             phase=self.game.phase, call_id=call.call_id, name=call.name, arguments=call.arguments,
                             result=content, is_error=is_error, **extra)
        return ToolOutcome(call.call_id, content, is_error)

    def _tool_send_message(self, args: dict, extra: dict) -> str:
        to, text = str(args["to"]), str(args["text"])
        if to != "all" and self.messaging == "public":
            raise ToolError('Messages can only go to "all" in this negotiation.')
        if to != "all" and (to not in self.game.players or to == self.player):
            raise ToolError(f"Unknown recipient: {to}")
        if not text.strip():
            raise ToolError("Message is empty.")
        if len(text) > MAX_MESSAGE_CHARS:
            raise ToolError(f"Message is longer than {MAX_MESSAGE_CHARS} characters.")
        extra["msg_seq"] = self.transport.send(self.player, to, text).seq
        return "Message sent."

    def _tool_read_messages(self, args: dict, extra: dict) -> str:
        messages = self.transport.receive(self.player)
        extra["msg_seqs"] = [m.seq for m in messages]
        if not messages:
            return "No new messages."
        return "\n\n".join(
            f"[message {m.seq}] {m.sender} to {'everyone' if m.recipient == 'all' else 'you only'}:\n{m.body}"
            for m in messages
        )

    def _tool_propose(self, args: dict, extra: dict) -> str:
        shares = args["shares"]
        if not isinstance(shares, dict):
            raise ToolError("shares must map each agent to a number of points.")
        proposal = self.game.propose(self.player, shares)
        self.recorder.record("proposal", player=self.player, pot=self.pot, round=proposal.round,
                             size=self.game.pot, shares=proposal.shares)
        return f"Proposal submitted: {shares_text(proposal.shares)}."

    def _tool_draft(self, args: dict, extra: dict) -> str:
        shares = args["shares"]
        if not isinstance(shares, dict):
            raise ToolError("shares must map each agent to a number of points.")
        draft = self.game.post_draft(self.player, shares)
        self.recorder.record("draft", player=self.player, pot=self.pot, round=draft.round, size=self.game.pot,
                             shares=draft.shares)
        return f"Draft posted: {shares_text(draft.shares)}. Everyone will see it before your final proposal."

    def _tool_vote(self, args: dict, extra: dict) -> str:
        choice = str(args["choice"])
        self.game.vote(self.player, choice)
        self.recorder.record("ballot", player=self.player, pot=self.pot, round=self.game.round, choice=choice)
        return "Vote recorded."

    def _tool_write_log(self, args: dict, extra: dict) -> str:
        text = str(args["text"])
        if len(text) > MAX_LOG_CHARS:
            raise ToolError(f"Log entry is longer than {MAX_LOG_CHARS} characters.")
        self.recorder.record("self_log", player=self.player, activation=self.activation, pot=self.pot,
                             round=self.game.round, text=text)
        return "Logged."
