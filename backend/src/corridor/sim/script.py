"""Command script: the human actions of a run, at given simulated times.

The scenario's ``events`` already script what the world does (a jammer back on early, a
threat, a late drone). A script adds what people do: a pilot asks for a cut or launches, the
command confirms a cut or answers a route request. Each entry is a WebSocket command, sent as
its ``actor``.

Ids the engine gives out are predictable within a run: cuts are ``cut-1``, ``cut-2``... in
the order they are scheduled, so a script can confirm the cut it asked for.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import Field

from corridor.domain.commands import Command
from corridor.domain.model import Model


class TimedCommand(Model):
    at_s: float = Field(ge=0, description="Simulated time, seconds.")
    command: Command


class CommandScript(Model):
    commands: list[TimedCommand] = Field(
        default_factory=list,
        description="Sent in time order; commands at the same time keep their file order.",
    )

    def in_order(self) -> list[TimedCommand]:
        return sorted(self.commands, key=lambda c: c.at_s)


def load_script(path: Path) -> CommandScript:
    return CommandScript.model_validate_json(path.read_bytes())


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
