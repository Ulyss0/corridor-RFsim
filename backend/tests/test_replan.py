import asyncio
from dataclasses import dataclass, field

import pytest

from corridor.domain.commands import CancelCut, ConfirmCut, LaunchMission, ScheduleCut, SetClock
from corridor.domain.events import (
    CutScheduled,
    Event,
    Notification,
    PlanMetrics,
    PlanUpdated,
    RoutePoint,
)
from corridor.domain.geo import Position
from corridor.domain.planner.plan import MissionPlan
from corridor.domain.scenario import Scenario
from corridor.domain.state import Cut
from corridor.domain.zones import constraints_by_mission
from corridor.engine.clock import SimClock
from corridor.engine.engine import PLAN_LEAD_WALL_S, Engine
from corridor.engine.replan import PlanDone, ProcessReplanner

EW = "EW-OFFICER"


@dataclass
class Request:
    mission: str
    revision: int
    cuts: list[Cut]
    earliest_departure_s: float
    done: PlanDone


@dataclass
class FakeReplanner:
    """Records requests; the test decides when and with what each one finishes."""

    requests: list[Request] = field(default_factory=list)

    def request(
        self,
        mission: str,
        revision: int,
        cuts: list[Cut],
        earliest_departure_s: float,
        done: PlanDone,
    ) -> None:
        self.requests.append(Request(mission, revision, cuts, earliest_departure_s, done))

    def request_fallback(
        self, mission: str, revision: int, position: Position, now_s: float, done: PlanDone
    ) -> None:
        raise AssertionError("no flight in these tests")

    def close(self) -> None:
        pass


def stub_plan() -> MissionPlan:
    route = [RoutePoint(lat=48.0, lon=11.0, t=600, action="fly")]
    metrics = PlanMetrics(
        baseline_battery_used_pct=40,
        optimised_battery_used_pct=30,
        saved_pct=25,
        saved_s=180,
    )
    return MissionPlan(
        status="approved", metrics=metrics, baseline_route=route, optimised_route=route
    )


@pytest.fixture
def replanner() -> FakeReplanner:
    return FakeReplanner()


@pytest.fixture
def wall() -> list[float]:
    return [0.0]


def make_engine(scenario: Scenario, replanner: FakeReplanner, wall: list[float]) -> Engine:
    constraints = constraints_by_mission(scenario, 100)
    return Engine(scenario, constraints, SimClock(20, wall=lambda: wall[0]), replanner)


@pytest.fixture
def engine(scenario: Scenario, replanner: FakeReplanner, wall: list[float]) -> Engine:
    return make_engine(scenario, replanner, wall)


@pytest.fixture
def published(engine: Engine) -> list[Event]:
    events: list[Event] = []
    engine.subscribe(events.append)
    return events


def schedule(engine: Engine) -> str:
    scheduled = engine.submit(ScheduleCut(actor=EW, jammer="J3", start_s=900, end_s=1260))[0]
    assert isinstance(scheduled, CutScheduled)
    return scheduled.cut.id


def test_every_cut_change_requests_a_plan(engine: Engine, replanner: FakeReplanner) -> None:
    cut_id = schedule(engine)
    engine.submit(ConfirmCut(actor="EW-J3", cut=cut_id))
    engine.submit(SetClock(actor=EW, factor=10))
    engine.submit(CancelCut(actor=EW, cut=cut_id))
    assert [r.revision for r in replanner.requests] == [1, 2, 3]
    assert [c.status for c in replanner.requests[1].cuts] == ["confirmed"]
    assert replanner.requests[2].cuts == []


def test_plan_is_published_and_kept_for_the_snapshot(
    engine: Engine, replanner: FakeReplanner, published: list[Event]
) -> None:
    engine.request_plans()
    request = replanner.requests[0]
    request.done(request.revision, stub_plan())
    (event,) = published
    assert isinstance(event, PlanUpdated)
    assert (event.mission, event.revision) == ("M1", 1)
    assert event.status == "approved"
    assert engine.missions["M1"].plan == event


def test_stale_plan_is_dropped(
    engine: Engine, replanner: FakeReplanner, published: list[Event]
) -> None:
    engine.request_plans()
    schedule(engine)
    published.clear()
    old, new = replanner.requests
    old.done(old.revision, stub_plan())
    assert published == []
    new.done(new.revision, stub_plan())
    assert [e.revision for e in published if isinstance(e, PlanUpdated)] == [2]


def test_no_plan_once_the_drone_flies(
    engine: Engine, replanner: FakeReplanner, published: list[Event]
) -> None:
    # Launch needs a flyable plan: the first one comes back, a second one is still out.
    engine.request_plans()
    first = replanner.requests[0]
    first.done(first.revision, stub_plan())
    engine.request_plans()
    engine.submit(LaunchMission(actor="PILOT-1", mission="M1"))
    published.clear()
    late = replanner.requests[1]
    late.done(late.revision, stub_plan())
    schedule(engine)
    assert len(replanner.requests) == 2
    assert not any(isinstance(e, PlanUpdated) for e in published)


def test_no_route_notifies_the_pilot(
    engine: Engine, replanner: FakeReplanner, published: list[Event]
) -> None:
    engine.request_plans()
    replanner.requests[0].done(1, None)
    (event,) = published
    assert isinstance(event, Notification)
    assert event.to == "PILOT-1"
    assert engine.missions["M1"].plan is None


def test_replan_takes_off_after_the_plan_is_back(
    engine: Engine, replanner: FakeReplanner, wall: list[float]
) -> None:
    engine.request_plans()
    assert replanner.requests[0].earliest_departure_s == 0  # paused clock: no lead
    engine.submit(SetClock(actor=EW, paused=False))
    wall[0] = 25.0  # 500 simulated seconds
    engine.request_plans()
    assert replanner.requests[1].earliest_departure_s == pytest.approx(500 + 20 * PLAN_LEAD_WALL_S)


def test_process_replanner_runs_the_planner(scenario: Scenario) -> None:
    async def run() -> tuple[int, MissionPlan | None]:
        loop = asyncio.get_running_loop()
        result: asyncio.Future[tuple[int, MissionPlan | None]] = loop.create_future()
        grids = {m: c.grid for m, c in constraints_by_mission(scenario, 100).items()}
        replanner = ProcessReplanner(scenario, grids)
        try:
            replanner.request("M1", 7, [], 0.0, lambda r, p: result.set_result((r, p)))
            return await asyncio.wait_for(result, timeout=30)
        finally:
            replanner.close()

    revision, plan = asyncio.run(run())
    assert revision == 7
    assert plan is not None
    assert plan.status == "baseline"


def test_a_cut_replans_only_the_missions_it_concerns(
    two_missions: Scenario, replanner: FakeReplanner, wall: list[float]
) -> None:
    engine = make_engine(two_missions, replanner, wall)
    engine.request_plans()
    assert [(r.mission, r.revision) for r in replanner.requests] == [("M1", 1), ("M2", 1)]
    engine.submit(ScheduleCut(actor=EW, jammer="J4", start_s=900, end_s=1260))
    assert [(r.mission, r.revision) for r in replanner.requests[2:]] == [("M2", 2)]
    assert [c.jammer for c in replanner.requests[2].cuts] == ["J4"]


def test_plans_queued_together_take_off_later(
    two_missions: Scenario, replanner: FakeReplanner, wall: list[float]
) -> None:
    engine = make_engine(two_missions, replanner, wall)
    engine.submit(SetClock(actor=EW, paused=False))
    engine.request_plans()
    lead = 20 * PLAN_LEAD_WALL_S
    assert [r.earliest_departure_s for r in replanner.requests] == pytest.approx([lead, 2 * lead])


def test_plans_of_each_mission_are_kept_apart(
    two_missions: Scenario, replanner: FakeReplanner, wall: list[float]
) -> None:
    engine = make_engine(two_missions, replanner, wall)
    published: list[Event] = []
    engine.subscribe(published.append)
    engine.request_plans()
    for request in replanner.requests:
        request.done(request.revision, stub_plan())
    assert [(e.mission, e.revision) for e in published if isinstance(e, PlanUpdated)] == [
        ("M1", 1),
        ("M2", 1),
    ]
    assert engine.missions["M2"].plan is not None
