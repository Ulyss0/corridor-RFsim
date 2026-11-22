import asyncio
from dataclasses import dataclass, field, replace

import pytest

from corridor.domain.commands import (
    CancelCut,
    ChooseRoute,
    ConfirmCut,
    DecideRoute,
    LaunchMission,
    RequestMission,
    ScheduleCut,
    SetClock,
    UpdateMission,
)
from corridor.domain.events import (
    ChoiceKind,
    CutCancelled,
    CutScheduled,
    Event,
    MissionCreated,
    MissionUpdated,
    Notification,
    OnTimeout,
    PlanMetrics,
    PlanUpdated,
    ProposedCut,
    RouteChoice,
    RoutePoint,
    RouteRequest,
    RouteRequested,
)
from corridor.domain.geo import Grid, Position
from corridor.domain.planner.plan import MissionPlan
from corridor.domain.scenario import Mission, Scenario
from corridor.domain.state import Cut
from corridor.domain.zones import mission_constraints
from corridor.engine.clock import SimClock
from corridor.engine.engine import (
    DECISION_LEAD_S,
    PLAN_LEAD_WALL_S,
    CommandRejected,
    Engine,
)
from corridor.engine.replan import PlanDone, ProcessReplanner

EW = "EW-OFFICER"


@dataclass
class Request:
    mission: str
    revision: int
    cuts: list[Cut]
    earliest_departure_s: float
    done: PlanDone
    scenario: Scenario


@dataclass
class FakeReplanner:
    """Records requests; the test decides when and with what each one finishes."""

    requests: list[Request] = field(default_factory=list)

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
        self.requests.append(
            Request(mission.id, revision, cuts, earliest_departure_s, done, scenario)
        )

    def request_fallback(
        self,
        scenario: Scenario,
        mission: Mission,
        grid: Grid,
        revision: int,
        position: Position,
        now_s: float,
        done: PlanDone,
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
    return Engine(scenario, 100, SimClock(20, wall=lambda: wall[0]), replanner)


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
    engine.submit(ConfirmCut(actor="CMD", cut=cut_id))
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


def test_plan_carries_its_route_choices(
    engine: Engine, replanner: FakeReplanner, published: list[Event]
) -> None:
    route = stub_plan().optimised_route
    choice = RouteChoice(
        kind="safe",
        cuts=[],
        route=route,
        flight_s=0,
        battery_used_pct=30,
        saved_s=0,
        saved_pct=0,
    )
    engine.request_plans()
    request = replanner.requests[0]
    request.done(request.revision, replace(stub_plan(), choices=[choice]))
    (event,) = published
    assert isinstance(event, PlanUpdated)
    assert event.choices == [choice]
    assert engine.missions["M1"].plan == event  # in the snapshot of a client joining later


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
        m1 = scenario.missions[0]
        grid = mission_constraints(scenario, m1, 100).grid
        replanner = ProcessReplanner(scenario)
        try:
            done = lambda r, p: result.set_result((r, p))  # noqa: E731
            replanner.request(scenario, m1, grid, 7, [], 0.0, done)
            return await asyncio.wait_for(result, timeout=30)
        finally:
            replanner.close()

    revision, plan = asyncio.run(run())
    assert revision == 7
    assert plan is not None
    assert plan.status == "baseline"
    assert [c.kind for c in plan.choices] == ["one_cut", "safe"]  # computed in the worker too


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


def request_mission(engine: Engine, pilot: str) -> MissionCreated:
    m1 = engine.scenario.missions[0]
    command = RequestMission(actor=pilot, launch=m1.launch, target=m1.target, departure_s=600)
    created = engine.submit(command)[0]
    assert isinstance(created, MissionCreated)
    return created


def test_a_pilot_request_creates_a_mission_and_plans_it(
    two_missions: Scenario, replanner: FakeReplanner, wall: list[float]
) -> None:
    engine = make_engine(two_missions, replanner, wall)
    published: list[Event] = []
    engine.subscribe(published.append)
    first = request_mission(engine, "PILOT-1").mission
    second = request_mission(engine, "PILOT-1").mission

    # M1 and M2 come from the file: new ids skip them. Drone fields come from the pilot.
    assert (first.id, second.id) == ("M3", "M4")
    assert (first.drone_id, first.band, first.departure_s) == ("UAV-1", "2.4GHz", 600)
    assert engine.scenario.mission("M4") == second
    assert engine.state.missions["M3"] == "planned"
    assert engine.missions["M3"].constraints.jammers == ["J3"]
    assert [(r.mission, r.revision) for r in replanner.requests] == [("M3", 1), ("M4", 1)]
    assert any(isinstance(e, Notification) and e.to == "PILOT-1" for e in published)

    replanner.requests[0].done(1, stub_plan())
    assert engine.missions["M3"].plan is not None


@pytest.mark.parametrize("actor", ["CMD", "EW-J3", "NOBODY"])
def test_only_a_pilot_requests_a_mission(
    two_missions: Scenario, replanner: FakeReplanner, wall: list[float], actor: str
) -> None:
    engine = make_engine(two_missions, replanner, wall)
    with pytest.raises(CommandRejected, match="only a pilot"):
        request_mission(engine, actor)
    assert set(engine.missions) == {"M1", "M2"}


def test_a_request_that_gives_no_zones_leaves_the_state_unchanged(
    two_missions: Scenario, replanner: FakeReplanner, wall: list[float]
) -> None:
    engine = make_engine(two_missions, replanner, wall)
    j3 = engine.scenario.friendly_jammers[0].pos
    command = RequestMission(actor="PILOT-1", launch=j3, target=j3, departure_s=600)
    with pytest.raises(CommandRejected, match="jammer at the pilot position"):
        engine.submit(command)
    assert [m.id for m in engine.scenario.missions] == ["M1", "M2"]
    assert set(engine.missions) == set(engine.state.missions) == {"M1", "M2"}
    assert replanner.requests == []


def update_m2_onto_m1(engine: Engine, actor: str = "PILOT-2") -> list[Event]:
    """Move M2 (J4, to the south) onto M1's points (J3)."""
    m1 = engine.scenario.missions[0]
    command = UpdateMission(
        actor=actor, mission="M2", launch=m1.launch, target=m1.target, departure_s=900
    )
    return engine.submit(command)


def test_updating_a_mission_replans_it_and_drops_the_old_plan(
    two_missions: Scenario, replanner: FakeReplanner, wall: list[float]
) -> None:
    engine = make_engine(two_missions, replanner, wall)
    engine.request_plans(["M2"])
    old = replanner.requests[0]
    events = update_m2_onto_m1(engine)

    updated = next(e.mission for e in events if isinstance(e, MissionUpdated))
    assert (updated.launch, updated.departure_s) == (engine.scenario.missions[0].launch, 900)
    assert engine.scenario.mission("M2") == updated
    runtime = engine.missions["M2"]
    assert runtime.constraints.jammers == ["J3"]
    assert runtime.plan is None
    assert [(r.mission, r.revision) for r in replanner.requests] == [("M2", 1), ("M2", 2)]

    old.done(old.revision, stub_plan())  # computed for the old points: dropped
    assert runtime.plan is None
    replanner.requests[1].done(2, stub_plan())
    assert runtime.plan is not None and runtime.plan.revision == 2


def test_only_its_pilot_updates_a_mission_on_the_ground(
    two_missions: Scenario, replanner: FakeReplanner, wall: list[float]
) -> None:
    engine = make_engine(two_missions, replanner, wall)
    for actor in ("PILOT-1", "CMD"):
        with pytest.raises(CommandRejected, match="only the pilot of M2"):
            update_m2_onto_m1(engine, actor=actor)
    update_m2_onto_m1(engine)
    engine.state.missions["M2"] = "flying"
    with pytest.raises(CommandRejected, match="already flying"):
        update_m2_onto_m1(engine)


# Route request workflow: the pilot picks a route, the command answers until the deadline.

PILOT, COMMAND = "PILOT-1", "CMD"
J3_CUT = ProposedCut(jammer="J3", start_s=900, end_s=1260)


def choices_plan() -> MissionPlan:
    """A plan whose safe route takes off at 600, its one_cut route at 700 with J3 cut."""
    safe = [RoutePoint(lat=48.0, lon=11.0, t=600, action="fly")]
    fast = [RoutePoint(lat=48.0, lon=11.0, t=700, action="fly")]

    def choice(kind: ChoiceKind, cuts: list[ProposedCut], route: list[RoutePoint]) -> RouteChoice:
        return RouteChoice(
            kind=kind,
            cuts=cuts,
            route=route,
            flight_s=500,
            battery_used_pct=30,
            saved_s=0 if kind == "safe" else 140,
            saved_pct=0 if kind == "safe" else 20,
        )

    return replace(
        stub_plan(),
        status="baseline",
        choices=[choice("one_cut", [J3_CUT], fast), choice("safe", [], safe)],
    )


def planned(engine: Engine, replanner: FakeReplanner, plan: MissionPlan | None = None) -> None:
    """Every mission has its plan back."""
    engine.request_plans()
    for request in replanner.requests[-len(engine.missions) :]:
        request.done(request.revision, plan or choices_plan())


def requests_of(events: list[Event]) -> list[RouteRequest]:
    return [e.request for e in events if isinstance(e, RouteRequested)]


def notified(events: list[Event], to: str) -> list[Notification]:
    return [e for e in events if isinstance(e, Notification) and e.to == to]


def test_safe_choice_stands_at_once(engine: Engine, replanner: FakeReplanner) -> None:
    planned(engine, replanner)
    events = engine.submit(ChooseRoute(actor=PILOT, mission="M1", kind="safe"))
    (request,) = requests_of(events)
    assert (request.status, request.cuts, request.deadline_s) == ("accepted", [], None)
    assert notified(events, COMMAND) == []
    assert engine.state.cuts == {}


def test_choice_needing_a_cut_asks_the_command(engine: Engine, replanner: FakeReplanner) -> None:
    planned(engine, replanner)
    asked = len(replanner.requests)
    events = engine.submit(ChooseRoute(actor=PILOT, mission="M1", kind="one_cut"))
    (cut,) = engine.state.cuts.values()
    assert (cut.jammer, cut.start_s, cut.end_s, cut.status) == ("J3", 900, 1260, "scheduled")
    assert (cut.requested_by, cut.requested_for) == (PILOT, "M1")
    (request,) = requests_of(events)
    assert request.status == "pending"
    assert request.cuts == [cut.id]
    assert request.deadline_s == 700 - DECISION_LEAD_S  # its take-off comes before the cut
    (ask,) = notified(events, COMMAND)
    assert ask.action is not None
    assert (ask.action.command, ask.action.mission) == ("route.decide", "M1")
    assert "the pilot flies the detour" in ask.text
    assert len(replanner.requests) == asked + 1  # the route is replanned on the new cut


def test_command_accepts(engine: Engine, replanner: FakeReplanner) -> None:
    planned(engine, replanner)
    engine.submit(ChooseRoute(actor=PILOT, mission="M1", kind="one_cut"))
    with pytest.raises(CommandRejected, match="only the command"):
        engine.submit(DecideRoute(actor=PILOT, mission="M1", accept=True))
    events = engine.submit(DecideRoute(actor=COMMAND, mission="M1", accept=True))
    (cut,) = engine.state.cuts.values()
    assert cut.status == "confirmed"
    (request,) = requests_of(events)
    assert (request.status, request.decided_by) == ("accepted", COMMAND)
    assert notified(events, "EW-J3")  # the operator carries the cut out
    assert "accepted" in notified(events, PILOT)[0].text
    with pytest.raises(CommandRejected, match="awaits an answer"):
        engine.submit(DecideRoute(actor=COMMAND, mission="M1", accept=False))


def test_command_refuses(engine: Engine, replanner: FakeReplanner) -> None:
    planned(engine, replanner)
    engine.submit(ChooseRoute(actor=PILOT, mission="M1", kind="one_cut"))
    events = engine.submit(DecideRoute(actor=COMMAND, mission="M1", accept=False))
    assert engine.state.cuts == {}
    (request,) = requests_of(events)
    assert (request.status, request.decided_by) == ("refused", COMMAND)
    assert "detour" in notified(events, PILOT)[0].text


@pytest.mark.parametrize("on_timeout", ["avoid", "cross"])
def test_no_answer_by_the_deadline_is_a_refusal(
    engine: Engine,
    replanner: FakeReplanner,
    wall: list[float],
    on_timeout: OnTimeout,
) -> None:
    """Only the command cuts: without its answer, nothing is cut, whatever the plan B."""
    planned(engine, replanner)
    engine.submit(ChooseRoute(actor=PILOT, mission="M1", kind="one_cut", on_timeout=on_timeout))
    engine.submit(SetClock(actor=COMMAND, paused=False))
    wall[0] = (700 - DECISION_LEAD_S - 10) / 20  # 10 s before the deadline
    assert requests_of(engine.tick()) == []
    wall[0] = (700 - DECISION_LEAD_S) / 20
    events = engine.tick()
    (request,) = requests_of(events)
    assert (request.status, request.decided_by) == ("refused", "timeout")
    assert notified(events, COMMAND)  # told what happened without its answer
    assert engine.state.cuts == {}


def test_launch_without_an_answer_flies_the_safe_route(
    engine: Engine, replanner: FakeReplanner
) -> None:
    planned(engine, replanner)
    engine.submit(ChooseRoute(actor=PILOT, mission="M1", kind="one_cut"))
    planned(engine, replanner, replace(choices_plan(), status="preview"))
    events = engine.submit(LaunchMission(actor=PILOT, mission="M1"))
    (request,) = requests_of(events)
    assert (request.status, request.decided_by) == ("refused", "launch")
    assert engine.state.cuts == {}
    flight = engine.missions["M1"].flight
    assert flight is not None
    assert flight.route == choices_plan().choices[-1].route  # the safe one


def test_launch_after_acceptance_waits_for_the_new_route(
    engine: Engine, replanner: FakeReplanner
) -> None:
    planned(engine, replanner)
    engine.submit(ChooseRoute(actor=PILOT, mission="M1", kind="one_cut"))
    engine.submit(DecideRoute(actor=COMMAND, mission="M1", accept=True))
    with pytest.raises(CommandRejected, match="being updated"):
        engine.submit(LaunchMission(actor=PILOT, mission="M1"))
    planned(engine, replanner, replace(choices_plan(), status="approved"))
    engine.submit(LaunchMission(actor=PILOT, mission="M1"))


def test_picking_again_withdraws_the_cuts_asked_before(
    engine: Engine, replanner: FakeReplanner
) -> None:
    planned(engine, replanner)
    engine.submit(ChooseRoute(actor=PILOT, mission="M1", kind="one_cut"))
    events = engine.submit(ChooseRoute(actor=PILOT, mission="M1", kind="safe"))
    assert engine.state.cuts == {}
    assert any(isinstance(e, CutCancelled) for e in events)
    request = engine.missions["M1"].request
    assert request is not None
    assert request.status == "accepted"


def test_choice_rejections(engine: Engine, replanner: FakeReplanner, wall: list[float]) -> None:
    with pytest.raises(CommandRejected, match="no plan yet"):
        engine.submit(ChooseRoute(actor=PILOT, mission="M1", kind="safe"))
    planned(engine, replanner)
    for actor in (COMMAND, "EW-J3"):  # the command answers cuts, it does not pick routes
        with pytest.raises(CommandRejected, match="only the pilot"):
            engine.submit(ChooseRoute(actor=actor, mission="M1", kind="safe"))
    with pytest.raises(CommandRejected, match="no fastest route"):
        engine.submit(ChooseRoute(actor=PILOT, mission="M1", kind="fastest"))
    engine.submit(SetClock(actor=COMMAND, paused=False))
    wall[0] = (700 - DECISION_LEAD_S + 1) / 20
    with pytest.raises(CommandRejected, match="too late"):
        engine.submit(ChooseRoute(actor=PILOT, mission="M1", kind="one_cut"))


@pytest.mark.parametrize("answer", [None, True], ids=["pending", "accepted"])
def test_moving_a_mission_drops_its_route_request_and_cuts(
    engine: Engine, replanner: FakeReplanner, answer: bool | None
) -> None:
    planned(engine, replanner)
    engine.submit(ChooseRoute(actor=PILOT, mission="M1", kind="one_cut"))
    if answer is not None:
        engine.submit(DecideRoute(actor=COMMAND, mission="M1", accept=answer))
    m1 = engine.scenario.missions[0]
    events = engine.submit(
        UpdateMission(
            actor=PILOT, mission="M1", launch=m1.launch, target=m1.target, departure_s=900
        )
    )
    assert any(isinstance(e, CutCancelled) for e in events)
    assert engine.state.cuts == {}
    assert engine.missions["M1"].request is None


def test_a_route_saving_too_little_cannot_be_asked(
    engine: Engine, replanner: FakeReplanner
) -> None:
    plan = choices_plan()
    small = [
        c.model_copy(update={"worth_asking": False}) if c.kind == "one_cut" else c
        for c in plan.choices
    ]
    planned(engine, replanner, replace(plan, choices=small))
    with pytest.raises(CommandRejected, match="saves too little"):
        engine.submit(ChooseRoute(actor=PILOT, mission="M1", kind="one_cut"))
    assert engine.state.cuts == {}


def test_without_a_detour_the_drone_waits_for_the_answer(
    engine: Engine, replanner: FakeReplanner
) -> None:
    """The pilot starts inside a jammer zone: the only route needs the cut."""
    plan = choices_plan()
    only_cut = replace(plan, status="preview", choices=[c for c in plan.choices if c.cuts])
    planned(engine, replanner, only_cut)
    engine.submit(ChooseRoute(actor=PILOT, mission="M1", kind="one_cut"))
    with pytest.raises(CommandRejected, match="wait for the command"):
        engine.submit(LaunchMission(actor=PILOT, mission="M1"))
    events = engine.submit(DecideRoute(actor=COMMAND, mission="M1", accept=False))
    assert "cannot start" in notified(events, PILOT)[0].text


def test_after_a_refusal_the_drone_flies_the_detour(
    engine: Engine, replanner: FakeReplanner
) -> None:
    planned(engine, replanner)
    engine.submit(ChooseRoute(actor=PILOT, mission="M1", kind="one_cut"))
    engine.submit(DecideRoute(actor=COMMAND, mission="M1", accept=False))
    # The cut is gone: the replan comes back on the detour, flown at launch.
    planned(engine, replanner, replace(choices_plan(), status="baseline"))
    engine.submit(LaunchMission(actor=PILOT, mission="M1"))
    flight = engine.missions["M1"].flight
    assert flight is not None
    assert flight.route == choices_plan().optimised_route


@pytest.mark.parametrize("answer", ["refuse", "none"])
def test_plan_b_straight_anyway_flies_through_the_jammer_on(
    engine: Engine, replanner: FakeReplanner, answer: str
) -> None:
    planned(engine, replanner)
    engine.submit(ChooseRoute(actor=PILOT, mission="M1", kind="one_cut", on_timeout="cross"))
    if answer == "refuse":
        events = engine.submit(DecideRoute(actor=COMMAND, mission="M1", accept=False))
        assert "straight through J3 anyway" in notified(events, PILOT)[0].text
        planned(engine, replanner, replace(choices_plan(), status="baseline"))
    events = engine.submit(LaunchMission(actor=PILOT, mission="M1"))
    assert engine.state.cuts == {}  # nothing cut without the command
    flight = engine.missions["M1"].flight
    assert flight is not None
    assert flight.route == choices_plan().choices[0].route  # the direct route, not the detour
    assert flight.through_jammers == {"J3"}
    # The plan published at launch is the route flown: every view shows the straight route.
    (published,) = [e for e in events if isinstance(e, PlanUpdated)]
    assert published.status == "straight"
    assert published.optimised_route == flight.route
    assert published.revision == flight.revision
    assert engine.missions["M1"].plan == published
