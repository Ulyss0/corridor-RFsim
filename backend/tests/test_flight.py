"""Engine with the real planner, run synchronously: launch rules, flight, safety rule."""

import pytest

from corridor.domain.commands import (
    CancelCut,
    ConfirmCut,
    InjectDelay,
    LaunchMission,
    RequestMission,
    ScheduleCut,
    SetClock,
)
from corridor.domain.events import (
    DroneState,
    Event,
    JammerRestarted,
    MissionStatusChanged,
    Notification,
    PlanUpdated,
)
from corridor.domain.geo import Grid, Position
from corridor.domain.planner.plan import plan_fallback, plan_mission
from corridor.domain.scenario import Mission, Scenario
from corridor.domain.state import Cut
from corridor.engine.clock import SimClock
from corridor.engine.engine import CommandRejected, Engine
from corridor.engine.replan import PlanDone

PILOT, EW = "PILOT-1", "EW-J3"
CUT = (900.0, 1380.0)
"""8 min: with the demo powers, J3's zone takes about 300 s to cross, plus 30 s and 60 s."""


class SyncReplanner:
    """Runs the real planner at once, in the calling thread."""

    def __init__(self, scenario: Scenario) -> None:
        self.scenario = scenario

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
        done(revision, plan_mission(scenario, mission, grid, cuts, earliest_departure_s))

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
        done(revision, plan_fallback(scenario, mission, grid, position, now_s, level))

    def close(self) -> None:
        pass


class World:
    def __init__(self, scenario: Scenario) -> None:
        self.wall = 0.0
        self.engine = Engine(
            scenario,
            100,
            SimClock(1, wall=lambda: self.wall),
            SyncReplanner(scenario),
        )
        self.events: list[Event] = []
        self.engine.subscribe(self.events.append)
        self.engine.request_plans()
        self.m1 = self.engine.missions["M1"]

    def run_to(self, sim_s: float, step: float = 5.0) -> None:
        if self.engine.clock.state().paused:
            self.engine.submit(SetClock(actor="HQ", paused=False))
        while self.wall < sim_s:
            self.wall = min(sim_s, self.wall + step)
            self.engine.tick()

    def of[T](self, kind: type[T]) -> list[T]:
        return [e for e in self.events if isinstance(e, kind)]

    def cut(self, *, confirm: bool = True) -> str:
        start, end = CUT
        scheduled = self.engine.submit(
            ScheduleCut(actor=PILOT, jammer="J3", start_s=start, end_s=end)
        )[0]
        cut_id: str = scheduled.cut.id  # type: ignore[union-attr]
        if confirm:
            self.engine.submit(ConfirmCut(actor=EW, cut=cut_id))
        return cut_id


@pytest.fixture(scope="module")
def flown(scenario_module: Scenario) -> World:
    """One full flight on a confirmed cut, shared by the tests that only read it."""
    world = World(scenario_module)
    world.cut()
    world.engine.submit(LaunchMission(actor=PILOT, mission="M1"))
    world.run_to(1800)
    return world


def test_launch_waits_for_the_operator(scenario: Scenario) -> None:
    world = World(scenario)
    world.cut(confirm=False)
    assert world.m1.plan is not None and world.m1.plan.status == "preview"
    with pytest.raises(CommandRejected, match="not confirmed"):
        world.engine.submit(LaunchMission(actor=PILOT, mission="M1"))


def test_inject_delay_needs_a_flight(scenario: Scenario) -> None:
    with pytest.raises(CommandRejected, match="no drone"):
        World(scenario).engine.submit(InjectDelay(actor="HQ", mission="M1", delay_s=60))


def test_on_time_flight_reaches_the_target(flown: World) -> None:
    states = flown.of(DroneState)
    assert states[0].action == "ground"
    assert any(s.action == "fly" for s in states)
    assert all(s.delay_s == 0 for s in states)
    assert flown.engine.state.missions["M1"] == "arrived"
    assert MissionStatusChanged(mission="M1", status="arrived") in flown.events
    assert not any(p.status == "fallback" for p in flown.of(PlanUpdated))
    approved = next(p for p in flown.of(PlanUpdated) if p.status == "approved")
    assert states[-1].battery_used_pct == pytest.approx(
        approved.metrics.optimised_battery_used_pct, abs=1.0
    )


def test_operator_is_told_when_to_cut_and_restart(flown: World) -> None:
    (restart,) = flown.of(JammerRestarted)
    assert restart.jammer == "J3"
    texts = [n.text for n in flown.of(Notification) if n.to == EW]
    assert any(t.startswith("Cut J3 now") for t in texts)
    assert any(t.startswith("Restart J3 now") for t in texts)


def test_late_drone_switches_to_a_safe_route(scenario: Scenario) -> None:
    world = World(scenario)
    world.cut()
    world.engine.submit(LaunchMission(actor=PILOT, mission="M1"))
    takeoff = next(p.t for p in world.m1.plan.optimised_route if p.action != "ground")  # type: ignore[union-attr]
    world.run_to(takeoff + 30)  # in the air, before J3
    world.engine.submit(InjectDelay(actor="HQ", mission="M1", delay_s=240))
    world.run_to(takeoff + 40)
    fallback = [p for p in world.of(PlanUpdated) if p.status == "fallback"]
    assert len(fallback) == 1
    assert world.m1.flight is not None
    assert world.m1.flight.revision == fallback[0].revision
    world.run_to(4000)
    assert world.engine.state.missions["M1"] == "arrived"
    # The battery counts the held seconds before the switch, not the whole hold.
    assert world.of(DroneState)[-1].battery_used_pct < 60


def test_cancelling_a_used_cut_in_flight_falls_back(scenario: Scenario) -> None:
    world = World(scenario)
    cut_id = world.cut()
    world.engine.submit(LaunchMission(actor=PILOT, mission="M1"))
    takeoff = next(p.t for p in world.m1.plan.optimised_route if p.action != "ground")  # type: ignore[union-attr]
    world.run_to(takeoff + 30)
    world.engine.submit(CancelCut(actor=EW, cut=cut_id))
    world.run_to(takeoff + 40)
    assert world.m1.plan is not None and world.m1.plan.status == "fallback"


def test_stale_plan_is_refreshed_before_launch(scenario: Scenario) -> None:
    world = World(scenario)
    first = world.m1.plan
    assert first is not None
    world.run_to(first.optimised_route[0].t + 60)
    assert len(world.of(PlanUpdated)) >= 2


def test_missions_fly_independently(two_missions: Scenario) -> None:
    world = World(two_missions)
    assert world.engine.missions["M2"].plan is not None
    world.engine.submit(LaunchMission(actor=PILOT, mission="M1"))
    world.run_to(1800)
    assert world.engine.state.missions == {"M1": "arrived", "M2": "planned"}
    assert {s.mission for s in world.of(DroneState)} == {"M1"}


def test_a_requested_mission_is_planned_and_flies(scenario: Scenario) -> None:
    world = World(scenario)
    m1 = world.m1.mission
    world.engine.submit(
        RequestMission(actor=PILOT, launch=m1.launch, target=m1.target, departure_s=600)
    )
    (plan,) = [p for p in world.of(PlanUpdated) if p.mission == "M2"]
    assert plan.status == "baseline"
    world.engine.submit(LaunchMission(actor=PILOT, mission="M2"))
    world.run_to(1800)
    assert world.engine.state.missions["M2"] == "arrived"


# --- 3D ------------------------------------------------------------------------------------


def three_d(scenario: Scenario) -> Scenario:
    terrain = scenario.terrain.model_copy(update={"mode": "3d"})
    return scenario.model_copy(update={"terrain": terrain})


def test_3d_flight_on_a_confirmed_cut_needs_no_fallback(scenario: Scenario) -> None:
    world = World(three_d(scenario))
    assert world.m1.cells is not None  # the safety rule checks the zones per level
    world.cut()
    plan = world.m1.plan
    assert plan is not None and plan.status == "approved"
    assert {p.alt_m for p in plan.optimised_route} - {0.0} <= set(scenario.terrain.levels_m)
    world.engine.submit(LaunchMission(actor=PILOT, mission="M1"))
    world.run_to(1800)
    assert world.engine.state.missions["M1"] == "arrived"
    assert not any(p.status == "fallback" for p in world.of(PlanUpdated))


def test_3d_late_drone_falls_back_from_its_level(scenario: Scenario) -> None:
    world = World(three_d(scenario))
    world.cut()
    world.engine.submit(LaunchMission(actor=PILOT, mission="M1"))
    plan = world.m1.plan
    assert plan is not None
    takeoff = next(p.t for p in plan.optimised_route if p.action != "ground")
    world.run_to(takeoff + 60)
    world.engine.submit(InjectDelay(actor="HQ", mission="M1", delay_s=300))
    world.run_to(takeoff + 80)
    fallback = next(p for p in world.of(PlanUpdated) if p.status == "fallback")
    flight = world.m1.flight
    assert flight is not None and flight.route == fallback.optimised_route
    assert fallback.optimised_route[0].alt_m in scenario.terrain.levels_m
