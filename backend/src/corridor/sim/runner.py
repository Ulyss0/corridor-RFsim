"""Run a scenario and a command script through the engine, with no server and no waiting.

Simulated time advances in steps of ``step_s`` and stops exactly at every scripted command, so
a command is sent at its own time. At each stop the engine ticks (flights, cut start and end,
the scenario's own events), then the commands due are sent. Plans are computed in the calling
thread (``InlineReplanner``), so a plan is there before the next step.

The run ends at ``until_s``, or earlier once every mission has arrived and the script is done.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from corridor.domain.commands import SetClock
from corridor.domain.events import Event
from corridor.domain.scenario import Scenario
from corridor.domain.state import MissionStatus
from corridor.engine.clock import SimClock
from corridor.engine.engine import CommandRejected, Engine
from corridor.engine.replan import InlineReplanner
from corridor.sim.log import CommandRefused, EventLogged, LogEntry, RunEnded
from corridor.sim.script import CommandScript

DEFAULT_STEP_S = 5.0
DEFAULT_UNTIL_S = 3600.0


@dataclass(frozen=True)
class RunResult:
    end_s: float
    missions: dict[str, MissionStatus]
    events: int
    rejected: int


def run(
    scenario: Scenario,
    script: CommandScript,
    sink: Callable[[LogEntry], None],
    *,
    cell_m: float = 100.0,
    step_s: float = DEFAULT_STEP_S,
    until_s: float = DEFAULT_UNTIL_S,
) -> RunResult:
    """``sink`` gets every event and refused command, then the end entry (not the start
    entry: the caller knows what it runs)."""
    if step_s <= 0 or until_s < 0:
        raise ValueError("step_s must be positive and until_s not negative")
    wall = 0.0
    clock = SimClock(1.0, wall=lambda: wall)
    engine = Engine(scenario, cell_m, clock, InlineReplanner(scenario))
    counts = {"events": 0, "rejected": 0}

    def now() -> float:
        return round(clock.now(), 3)

    def logged(event: Event) -> None:
        counts["events"] += 1
        sink(EventLogged(t=now(), event=event))

    engine.subscribe(logged)
    engine.submit(SetClock(actor=scenario.command_callsign, paused=False))
    engine.request_plans()

    pending = script.in_order()
    while True:
        while pending and pending[0].at_s <= wall:
            command = pending.pop(0).command
            try:
                engine.submit(command)
            except CommandRejected as exc:
                counts["rejected"] += 1
                sink(CommandRefused(t=now(), command=command, reason=str(exc)))
        done = not pending and all(s == "arrived" for s in engine.state.missions.values())
        if wall >= until_s or (done and engine.state.missions):
            break
        stop = min(wall + step_s, until_s)
        if pending:
            stop = min(stop, pending[0].at_s)
        wall = stop
        engine.tick()

    missions = dict(engine.state.missions)
    sink(RunEnded(t=now(), missions=missions))
    return RunResult(
        end_s=now(), missions=missions, events=counts["events"], rejected=counts["rejected"]
    )
