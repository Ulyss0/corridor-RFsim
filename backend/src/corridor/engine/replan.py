"""Plans run off the event loop, in a process pool.

A pure Python plan takes up to a couple of seconds; on the event loop it would freeze every
WebSocket client, and in a thread it would hold the GIL (Python's global interpreter lock).
The result comes back on the event loop thread, where the engine checks its revision.
Each mission is planned on its own grid; requests of different missions queue up on the single
worker. Each request carries the scenario as it is then: the command adds and removes threats.
"""

from __future__ import annotations

import asyncio
import logging
import multiprocessing
from collections.abc import Callable
from concurrent.futures import Future, ProcessPoolExecutor
from typing import Protocol

from corridor.domain.geo import Grid, Position
from corridor.domain.planner.plan import MissionPlan, plan_fallback, plan_mission
from corridor.domain.scenario import Mission, Scenario
from corridor.domain.state import Cut

log = logging.getLogger(__name__)

PlanDone = Callable[[int, MissionPlan | None], None]
"""Called with the revision and the plan (None: no route), on the event loop thread."""


class Replanner(Protocol):
    """Missions come with their grid, and the scenario with its current threats: both may
    change after the replanner is created."""

    def request(
        self,
        scenario: Scenario,
        mission: Mission,
        grid: Grid,
        revision: int,
        cuts: list[Cut],
        earliest_departure_s: float,
        done: PlanDone,
    ) -> None: ...

    def request_fallback(
        self,
        scenario: Scenario,
        mission: Mission,
        grid: Grid,
        revision: int,
        position: Position,
        now_s: float,
        done: PlanDone,
        level: int = 0,
    ) -> None:
        """``level``: the drone's flight level in 3D."""
        ...

    def close(self) -> None: ...


class ProcessReplanner:
    """One worker process: plans run one at a time. A pending plan of a mission is dropped for
    a newer one of the same mission; other missions keep theirs.

    The worker is spawned, not forked: a forked worker would inherit the server's signal
    handlers, ignore SIGTERM and outlive the server.
    """

    def __init__(self, scenario: Scenario) -> None:
        """``scenario`` is unused: each request brings the current one."""
        del scenario
        self._pool = ProcessPoolExecutor(
            max_workers=1, mp_context=multiprocessing.get_context("spawn")
        )
        self._pending: dict[str, Future[MissionPlan | None]] = {}

    def request(
        self,
        scenario: Scenario,
        mission: Mission,
        grid: Grid,
        revision: int,
        cuts: list[Cut],
        earliest_departure_s: float,
        done: PlanDone,
    ) -> None:
        future = self._pool.submit(
            plan_mission, scenario, mission, grid, cuts, earliest_departure_s
        )
        self._submit(mission.id, revision, done, future)

    def request_fallback(
        self,
        scenario: Scenario,
        mission: Mission,
        grid: Grid,
        revision: int,
        position: Position,
        now_s: float,
        done: PlanDone,
        level: int = 0,
    ) -> None:
        future = self._pool.submit(plan_fallback, scenario, mission, grid, position, now_s, level)
        self._submit(mission.id, revision, done, future)

    def _submit(
        self, mission: str, revision: int, done: PlanDone, future: Future[MissionPlan | None]
    ) -> None:
        loop = asyncio.get_running_loop()
        pending = self._pending.get(mission)
        if pending is not None:
            pending.cancel()  # only succeeds if it has not started: its result is stale
        self._pending[mission] = future

        def finished(future: Future[MissionPlan | None]) -> None:
            if future.cancelled():
                return
            try:
                plan = future.result()
            except Exception:
                log.exception("plan of %s revision %d failed", mission, revision)
                return
            loop.call_soon_threadsafe(done, revision, plan)

        future.add_done_callback(finished)

    def close(self) -> None:
        """Drop pending plans, wait for the running one (a couple of seconds at most)."""
        self._pool.shutdown(wait=True, cancel_futures=True)
