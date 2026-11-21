import pytest

from corridor.adapters.ws.audience import Audience, Viewer
from corridor.domain.events import (
    ClockChanged,
    CutCancelled,
    DroneState,
    Event,
    JammerRestarted,
    MissionStatusChanged,
    Notification,
)
from corridor.domain.geo import Position
from corridor.domain.scenario import Scenario
from corridor.domain.state import ClockState, Cut
from corridor.domain.zones import constraints_by_mission


@pytest.fixture
def audience(two_missions: Scenario) -> Audience:
    return Audience(two_missions, constraints_by_mission(two_missions, 100))


def viewer(audience: Audience, callsign: str) -> Viewer:
    found = audience.viewer(callsign)
    assert found is not None
    return found


def cut(jammer: str) -> Cut:
    return Cut(id="c", jammer=jammer, start_s=0, end_s=1, status="scheduled", requested_by="x")


EVENTS: dict[str, Event] = {
    "clock": ClockChanged(clock=ClockState(sim_time_s=0, factor=1, paused=True)),
    "cut J3": CutCancelled(cut=cut("J3")),
    "cut J4": CutCancelled(cut=cut("J4")),
    "restart J4": JammerRestarted(jammer="J4", cut="c"),
    "M1 status": MissionStatusChanged(mission="M1", status="flying"),
    "M2 drone": DroneState(
        mission="M2",
        position=Position(lat=48, lon=11),
        action="fly",
        battery_used_pct=1,
        delay_s=0,
        revision=1,
    ),
    "to PILOT-1": Notification(to="PILOT-1", text="x"),
    "to EW-J4": Notification(to="EW-J4", text="x"),
}


@pytest.mark.parametrize(
    ("callsign", "expected"),
    [
        ("CMD", set(EVENTS)),
        ("PILOT-1", {"clock", "cut J3", "M1 status", "to PILOT-1"}),
        ("PILOT-2", {"clock", "cut J4", "restart J4", "M2 drone"}),
        ("EW-J4", {"clock", "cut J4", "restart J4", "M2 drone", "to EW-J4"}),
    ],
)
def test_each_role_sees_only_its_events(
    audience: Audience, callsign: str, expected: set[str]
) -> None:
    who = viewer(audience, callsign)
    assert {name for name, event in EVENTS.items() if audience.sees(who, event)} == expected


def test_an_unknown_callsign_has_no_viewer(audience: Audience) -> None:
    assert audience.viewer("NOBODY") is None
