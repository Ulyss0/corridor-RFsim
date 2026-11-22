import pytest

from corridor.domain.commands import (
    CancelCut,
    ConfirmCut,
    LaunchMission,
    ScheduleCut,
    SetClock,
)
from corridor.domain.events import (
    ClockChanged,
    CutCancelled,
    CutConfirmed,
    CutScheduled,
    Event,
    MissionStatusChanged,
    Notification,
)
from corridor.domain.scenario import Scenario
from corridor.engine.clock import SimClock
from corridor.engine.engine import CommandRejected, Engine

EW = "EW-OFFICER"


def make_engine(scenario: Scenario, wall: float = 0.0) -> Engine:
    return Engine(scenario, 100, SimClock(20, wall=lambda: wall))


@pytest.fixture
def engine(scenario: Scenario) -> Engine:
    return make_engine(scenario)


def schedule(engine: Engine, start: float = 900, end: float = 1260) -> str:
    events = engine.submit(ScheduleCut(actor=EW, jammer="J3", start_s=start, end_s=end))
    assert isinstance(events[0], CutScheduled)
    return events[0].cut.id


def test_schedule_cut_asks_the_command(engine: Engine) -> None:
    published: list[Event] = []
    engine.subscribe(published.append)
    events = engine.submit(ScheduleCut(actor=EW, jammer="J3", start_s=900, end_s=1260))
    assert published == events
    scheduled, notification = events
    assert isinstance(scheduled, CutScheduled)
    assert scheduled.cut.status == "scheduled"
    assert isinstance(notification, Notification)
    assert notification.to == "CMD"
    assert "14:15:00 to 14:21:00" in notification.text
    assert notification.action is not None
    assert notification.action.cut == scheduled.cut.id


@pytest.mark.parametrize(
    ("jammer", "start", "end", "reason"),
    [
        ("R1", 900, 1000, "unknown friendly jammer"),
        ("J3", 1000, 1100, "overlaps"),
    ],
)
def test_schedule_cut_rejections(
    engine: Engine, jammer: str, start: float, end: float, reason: str
) -> None:
    schedule(engine)
    with pytest.raises(CommandRejected, match=reason):
        engine.submit(ScheduleCut(actor=EW, jammer=jammer, start_s=start, end_s=end))


def test_schedule_cut_in_the_past_is_rejected(scenario: Scenario) -> None:
    wall = [0.0]
    engine = Engine(scenario, 100, SimClock(20, wall=lambda: wall[0]))
    engine.submit(SetClock(actor=EW, paused=False))
    wall[0] = 25.0  # 500 simulated seconds
    with pytest.raises(CommandRejected, match="past"):
        engine.submit(ScheduleCut(actor=EW, jammer="J3", start_s=400, end_s=600))


@pytest.mark.parametrize("actor", ["PILOT-1", "EW-J3"])
def test_only_the_command_confirms(engine: Engine, actor: str) -> None:
    cut_id = schedule(engine)
    with pytest.raises(CommandRejected, match="only the command"):
        engine.submit(ConfirmCut(actor=actor, cut=cut_id))
    assert engine.state.cuts[cut_id].status == "scheduled"

    event, notification = engine.submit(ConfirmCut(actor="CMD", cut=cut_id))
    assert isinstance(event, CutConfirmed)
    assert engine.state.cuts[cut_id].status == "confirmed"
    assert isinstance(notification, Notification)
    assert notification.to == "EW-J3"  # the operator carries the cut out
    with pytest.raises(CommandRejected, match="already confirmed"):
        engine.submit(ConfirmCut(actor="CMD", cut=cut_id))


def test_cancel_cut_tells_the_pilots_concerned(two_missions: Scenario) -> None:
    engine = make_engine(two_missions)
    cut_id = schedule(engine)
    cancelled, *notifications = engine.submit(CancelCut(actor=EW, cut=cut_id))
    assert isinstance(cancelled, CutCancelled)
    assert cancelled.cut.jammer == "J3"
    assert [n.to for n in notifications if isinstance(n, Notification)] == ["PILOT-1"]
    assert engine.state.cuts == {}
    with pytest.raises(CommandRejected, match="unknown cut"):
        engine.submit(CancelCut(actor=EW, cut=cut_id))


def test_cancel_cut_with_reason(two_missions: Scenario) -> None:
    engine = make_engine(two_missions)
    cut_id = schedule(engine)
    cancelled, notif = engine.submit(
        CancelCut(actor=EW, cut=cut_id, reason="Tirs d'artillerie / Prise d'obus")
    )
    assert isinstance(cancelled, CutCancelled)
    assert isinstance(notif, Notification)
    assert "Tirs d'artillerie / Prise d'obus" in notif.text
    assert notif.to == "PILOT-1"


def test_cut_remembers_its_mission(engine: Engine) -> None:
    (scheduled, notification) = engine.submit(
        ScheduleCut(actor="PILOT-1", jammer="J3", start_s=900, end_s=1260, mission="M1")
    )
    assert isinstance(scheduled, CutScheduled)
    assert scheduled.cut.requested_for == "M1"
    assert isinstance(notification, Notification)
    assert "for M1" in notification.text
    with pytest.raises(CommandRejected, match="unknown mission"):
        engine.submit(ScheduleCut(actor=EW, jammer="J3", start_s=2000, end_s=2100, mission="M9"))


def test_launch_mission_once(engine: Engine) -> None:
    (event,) = engine.submit(LaunchMission(actor="PILOT-1", mission="M1"))
    assert event == MissionStatusChanged(mission="M1", status="flying")
    assert engine.state.missions == {"M1": "flying"}
    with pytest.raises(CommandRejected, match="already flying"):
        engine.submit(LaunchMission(actor="PILOT-1", mission="M1"))


@pytest.mark.parametrize("actor", ["PILOT-1", "CMD"])
def test_only_its_pilot_launches_a_mission(two_missions: Scenario, actor: str) -> None:
    engine = make_engine(two_missions)
    with pytest.raises(CommandRejected, match="only the pilot of M2"):
        engine.submit(LaunchMission(actor=actor, mission="M2"))
    with pytest.raises(CommandRejected, match="unknown mission"):
        engine.submit(LaunchMission(actor="PILOT-2", mission="M9"))
    engine.submit(LaunchMission(actor="PILOT-2", mission="M2"))
    assert engine.state.missions == {"M1": "planned", "M2": "flying"}


def test_each_mission_sees_the_jammers_that_concern_it(two_missions: Scenario) -> None:
    engine = make_engine(two_missions)
    assert engine.missions["M1"].constraints.jammers == ["J3"]
    assert engine.missions["M2"].constraints.jammers == ["J4"]


def test_set_clock(engine: Engine) -> None:
    (event,) = engine.submit(SetClock(actor=EW, factor=30, paused=False))
    assert isinstance(event, ClockChanged)
    assert event.clock.factor == 30
    assert event.clock.paused is False
