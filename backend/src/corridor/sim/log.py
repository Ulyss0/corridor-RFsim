"""Run log: one JSON object per line.

First line ``run`` (what was run, with file hashes), then every engine event (``event``) and
every command the engine refused (``rejected``), in order, each with the simulated time, and
last ``end`` (final time and mission statuses). The same scenario, script and options give the
same file, byte for byte: the engine has no randomness and no wall-clock time.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, Literal, TextIO

from pydantic import Field, TypeAdapter

from corridor.domain.commands import Command
from corridor.domain.events import Event
from corridor.domain.model import Model
from corridor.domain.state import MissionStatus

LOG_VERSION = 1


class RunStarted(Model):
    type: Literal["run"] = "run"
    version: int = LOG_VERSION
    scenario: str = Field(description="Scenario file name.")
    scenario_sha256: str
    script: str | None = Field(description="Command script file name, None without one.")
    script_sha256: str | None
    cell_m: float
    step_s: float
    until_s: float


class EventLogged(Model):
    type: Literal["event"] = "event"
    t: float = Field(description="Simulated time, seconds.")
    event: Event


class CommandRefused(Model):
    type: Literal["rejected"] = "rejected"
    t: float = Field(description="Simulated time, seconds.")
    command: Command
    reason: str


class RunEnded(Model):
    type: Literal["end"] = "end"
    t: float = Field(description="Simulated time, seconds.")
    missions: dict[str, MissionStatus]


LogEntry = Annotated[
    RunStarted | EventLogged | CommandRefused | RunEnded, Field(discriminator="type")
]
_ENTRY: TypeAdapter[LogEntry] = TypeAdapter(LogEntry)


def write_entry(out: TextIO, entry: LogEntry) -> None:
    out.write(entry.model_dump_json() + "\n")


def read_log(path: Path) -> Iterator[LogEntry]:
    with path.open(encoding="utf-8") as lines:
        for line in lines:
            if line.strip():
                yield _ENTRY.validate_json(line)
