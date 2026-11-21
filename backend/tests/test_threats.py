"""Threats added and removed by the command: zones recomputed, missions concerned replanned."""

from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.testclient import WebSocketTestSession

from corridor.adapters.ws.app import create_app
from corridor.domain.commands import AddThreat, LaunchMission, RemoveThreat
from corridor.domain.events import (
    Event,
    Notification,
    PlanMetrics,
    RoutePoint,
    ThreatAdded,
    ThreatRemoved,
)
from corridor.domain.geo import Grid, Position
from corridor.domain.planner.plan import MissionPlan, plan_mission
from corridor.domain.scenario import AvoidZone, Mission, Scenario
from corridor.domain.state import Cut
from corridor.domain.zones import mission_constraints
from corridor.engine.clock import SimClock
from corridor.engine.engine import CommandRejected, Engine
from corridor.engine.replan import PlanDone

NEAR_M2 = Position(lat=48.03, lon=11.567)
"""Halfway along M2, 12.7 km south of M1: a threat here concerns M2 only."""
ON_M1 = Position(lat=48.144966, lon=11.567)
"""Halfway along M1's straight line."""


@dataclass
class Request:
    scenario: Scenario
    mission: str
    revision: int
    done: PlanDone


@dataclass
class FakeReplanner:
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
        self.requests.append(Request(scenario, mission.id, revision, done))

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
        raise AssertionError("no fallback in these tests")

    def close(self) -> None:
        pass


@pytest.fixture
def replanner() -> FakeReplanner:
    return FakeReplanner()


@pytest.fixture
def engine(two_missions: Scenario, replanner: FakeReplanner) -> Engine:
    engine = Engine(two_missions, 100, SimClock(20, wall=lambda: 0.0), replanner)
    engine.request_plans()
    replanner.requests.clear()
    return engine


def radar(at: Position, radius_m: float = 500, actor: str = "CMD") -> AddThreat:
    return AddThreat(actor=actor, kind="radar", pos=at, radius_m=radius_m)


def notified(events: list[Event]) -> dict[str, str]:
    return {e.to: e.text for e in events if isinstance(e, Notification)}


def test_a_new_radar_replans_the_missions_it_concerns(
    engine: Engine, replanner: FakeReplanner
) -> None:
    events = engine.submit(radar(NEAR_M2))
    added = events[0]
    assert isinstance(added, ThreatAdded)
    assert (added.threat.id, added.missions) == ("R2", ["M2"])  # R1 is in the file
    assert "R2" in engine.missions["M2"].constraints.threats
    assert "R2" not in engine.missions["M1"].constraints.threats
    assert [(r.mission, r.revision) for r in replanner.requests] == [("M2", 2)]
    assert "R2" in {t.id for t in replanner.requests[0].scenario.enemy_threats}
    assert set(notified(events)) == {"CMD", "PILOT-2"}
    assert "replanning M2" in notified(events)["CMD"]


def test_ids_follow_the_kind(engine: Engine) -> None:
    jammer = AddThreat(actor="CMD", kind="jammer", pos=NEAR_M2, eirp_w=1, bands=["2.4GHz"])
    avoid = AddThreat(actor="CMD", kind="avoid", pos=NEAR_M2, radius_m=300)
    ids = []
    for command in (radar(NEAR_M2), jammer, avoid):
        added = engine.submit(command)[0]
        assert isinstance(added, ThreatAdded)
        ids.append(added.threat.id)
    assert ids == ["R2", "E2", "A1"]


def test_only_the_command_changes_threats(engine: Engine) -> None:
    with pytest.raises(CommandRejected, match="only the command"):
        engine.submit(radar(NEAR_M2, actor="PILOT-2"))
    with pytest.raises(CommandRejected, match="only the command"):
        engine.submit(RemoveThreat(actor="PILOT-1", threat="R1"))


def test_removing_a_threat_replans_the_missions_it_concerned(
    engine: Engine, replanner: FakeReplanner
) -> None:
    events = engine.submit(RemoveThreat(actor="CMD", threat="R1"))
    removed = events[0]
    assert isinstance(removed, ThreatRemoved)
    assert removed.missions == ["M1"]
    assert "R1" not in engine.missions["M1"].constraints.threats
    assert "R1" not in {t.id for t in engine.scenario.enemy_threats}
    assert [r.mission for r in replanner.requests] == ["M1"]
    with pytest.raises(CommandRejected, match="unknown enemy threat"):
        engine.submit(RemoveThreat(actor="CMD", threat="R1"))


def test_a_threat_without_zones_is_rejected_and_nothing_changes(
    engine: Engine, replanner: FakeReplanner
) -> None:
    before = engine.scenario
    launch = engine.missions["M1"].mission.launch
    on_pilot = AddThreat(actor="CMD", kind="jammer", pos=launch, eirp_w=1, bands=["2.4GHz"])
    with pytest.raises(CommandRejected, match="no zones"):
        engine.submit(on_pilot)
    assert engine.scenario is before
    assert replanner.requests == []


def test_a_drone_in_flight_keeps_its_route(engine: Engine, replanner: FakeReplanner) -> None:
    engine.request_plans(["M1"])
    route = [RoutePoint(lat=ON_M1.lat, lon=ON_M1.lon, t=600, action="fly")]
    metrics = PlanMetrics(
        baseline_battery_used_pct=40, optimised_battery_used_pct=40, saved_pct=0, saved_s=0
    )
    plan = MissionPlan(
        status="baseline", metrics=metrics, baseline_route=route, optimised_route=route
    )
    replanner.requests[-1].done(replanner.requests[-1].revision, plan)
    engine.submit(LaunchMission(actor="PILOT-1", mission="M1"))
    replanner.requests.clear()
    events = engine.submit(radar(ON_M1, radius_m=300))
    assert replanner.requests == []
    assert "check your route" in notified(events)["PILOT-1"]
    assert "in flight: M1" in notified(events)["CMD"]


def test_a_zone_to_avoid_is_shown_but_not_enforced_yet(scenario: Scenario) -> None:
    m1 = scenario.missions[0]
    grid = mission_constraints(scenario, m1, 100).grid
    planned = plan_mission(scenario, m1, grid, [])
    assert planned is not None
    middle = planned.optimised_route[len(planned.optimised_route) // 2]
    zone = AvoidZone(id="A1", pos=Position(lat=middle.lat, lon=middle.lon), radius_m=300)
    with_zone = scenario.model_copy(update={"enemy_threats": [*scenario.enemy_threats, zone]})
    zones = mission_constraints(with_zone, m1, 100).zones
    assert ("A1", "avoid") in {(z.threat, z.kind) for z in zones}
    again = plan_mission(with_zone, m1, grid, [])
    assert again is not None
    assert again.optimised_route == planned.optimised_route


@pytest.mark.parametrize(
    "fields",
    [
        {"kind": "radar"},
        {"kind": "radar", "radius_m": 500, "eirp_w": 1},
        {"kind": "jammer", "eirp_w": 1},
        {"kind": "jammer", "eirp_w": 1, "bands": ["2.4GHz"], "radius_m": 500},
        {"kind": "avoid", "radius_m": 300, "position_error_m": 50},
    ],
)
def test_threat_fields_must_match_the_kind(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        AddThreat.model_validate({"actor": "CMD", "pos": NEAR_M2.model_dump()} | fields)


# --- WebSocket ------------------------------------------------------------------------------


def receive(ws: WebSocketTestSession) -> dict[str, Any]:
    message: dict[str, Any] = ws.receive_json()
    return message["payload"]  # type: ignore[no-any-return]


def test_clients_concerned_get_a_new_snapshot(two_missions: Scenario) -> None:
    app = create_app(two_missions, clock_factor=20, cell_m=100)
    with (
        TestClient(app) as client,  # one event loop for every connection
        client.websocket_connect("/ws?callsign=CMD") as command,
        client.websocket_connect("/ws?callsign=PILOT-1") as pilot_1,
        client.websocket_connect("/ws?callsign=PILOT-2") as pilot_2,
    ):
        for ws in (command, pilot_1, pilot_2):
            receive(ws)
        add = {"type": "threat.add", "actor": "CMD", "kind": "radar", "radius_m": 500}
        command.send_json({"req_id": "r1", "command": add | {"pos": NEAR_M2.model_dump()}})

        assert receive(pilot_2)["type"] == "threat.added"
        snapshot = receive(pilot_2)
        assert snapshot["type"] == "snapshot"
        assert "R2" in {t["id"] for t in snapshot["scenario"]["enemy_threats"]}
        (m2,) = snapshot["missions"]
        assert "R2" in m2["constraints"]["threats"]
        assert receive(pilot_2)["type"] == "notification"

        assert receive(command)["type"] == "threat.added"
        assert "R2" in {z["threat"] for z in receive(command)["nominal_zones"]}

        # PILOT-1's mission is not concerned: the next thing it gets is the clock.
        command.send_json({"req_id": "r2", "command": {"type": "clock.set", "actor": "CMD"}})
        assert receive(pilot_1)["type"] == "clock"
