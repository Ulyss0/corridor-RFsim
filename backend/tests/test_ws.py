from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession
from starlette.websockets import WebSocketDisconnect

from corridor.adapters.ws.app import UNKNOWN_CALLSIGN, create_app
from corridor.domain.geo import Grid, Position
from corridor.domain.planner.plan import plan_mission
from corridor.domain.scenario import Scenario
from corridor.domain.state import Cut
from corridor.engine.replan import PlanDone, Replanner


def receive(ws: WebSocketTestSession) -> dict[str, Any]:
    message: dict[str, Any] = ws.receive_json()
    return message


def schedule(actor: str, jammer: str, req_id: str = "r1") -> dict[str, Any]:
    command = {"type": "cut.schedule", "actor": actor, "jammer": jammer}
    return {"req_id": req_id, "command": command | {"start_s": 900, "end_s": 1260}}


def test_snapshot_then_commands(scenario: Scenario) -> None:
    client = TestClient(create_app(scenario, clock_factor=20, cell_m=100))
    with client.websocket_connect("/ws?callsign=CMD") as ws:
        first = receive(ws)["payload"]
        assert first["type"] == "snapshot"
        assert first["viewer"] == {"callsign": "CMD", "role": "command"}
        (m1,) = first["missions"]
        assert m1["status"] == "planned"
        assert m1["constraints"]["grid"]["cell_m"] == 100
        assert {z["threat"] for z in m1["constraints"]["zones"]} == {"J3", "E1", "R1"}
        assert {z["threat"] for z in first["nominal_zones"]} == {"J3", "E1", "R1"}

        ws.send_json(schedule("CMD", "J3"))
        scheduled, notification, ack = receive(ws), receive(ws), receive(ws)
        assert scheduled["seq"] == 1
        assert scheduled["payload"]["type"] == "cut.scheduled"
        assert notification["payload"]["to"] == "EW-J3"
        assert ack["payload"] == {"type": "ack", "req_id": "r1"}

        cut_id = scheduled["payload"]["cut"]["id"]
        confirm = {"type": "cut.confirm", "actor": "CMD", "cut": cut_id}
        ws.send_json({"req_id": "r2", "command": confirm})
        error = receive(ws)["payload"]
        assert error["type"] == "error"
        assert error["req_id"] == "r2"
        assert "only the operator" in error["reason"]


def test_events_reach_every_client_and_bad_messages_get_an_error(scenario: Scenario) -> None:
    client = TestClient(create_app(scenario, clock_factor=20, cell_m=100))
    with (
        client.websocket_connect("/ws?callsign=CMD") as a,
        client.websocket_connect("/ws?callsign=PILOT-1") as b,
    ):
        receive(a), receive(b)
        command = {"type": "clock.set", "actor": "CMD", "factor": 10}
        a.send_json({"req_id": "r1", "command": command})
        assert receive(b)["payload"]["clock"]["factor"] == 10

        a.send_text("not json")
        receive(a)  # clock event
        receive(a)  # ack
        error = receive(a)["payload"]
        assert error["type"] == "error"
        assert error["req_id"] is None


def test_unknown_callsign_is_turned_away(scenario: Scenario) -> None:
    client = TestClient(create_app(scenario, clock_factor=20, cell_m=100))
    for url in ("/ws", "/ws?callsign=NOBODY"):
        with client.websocket_connect(url) as ws, pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code == UNKNOWN_CALLSIGN


def test_a_client_only_acts_as_itself(scenario: Scenario) -> None:
    client = TestClient(create_app(scenario, clock_factor=20, cell_m=100))
    with client.websocket_connect("/ws?callsign=PILOT-1") as ws:
        receive(ws)
        confirm = {"type": "cut.confirm", "actor": "EW-J3", "cut": "cut-1"}
        ws.send_json({"req_id": "r1", "command": confirm})
        error = receive(ws)["payload"]
        assert error["type"] == "error"
        assert "cannot act as EW-J3" in error["reason"]


def test_each_role_sees_its_own_part(two_missions: Scenario) -> None:
    client = TestClient(create_app(two_missions, clock_factor=20, cell_m=100))
    with (
        client.websocket_connect("/ws?callsign=CMD") as command,
        client.websocket_connect("/ws?callsign=PILOT-1") as pilot,
        client.websocket_connect("/ws?callsign=EW-J4") as operator,
    ):
        seen = {
            name: receive(ws)["payload"]
            for name, ws in [("cmd", command), ("pilot", pilot), ("ew", operator)]
        }
        assert [m["constraints"]["mission"] for m in seen["cmd"]["missions"]] == ["M1", "M2"]
        assert [m["constraints"]["mission"] for m in seen["pilot"]["missions"]] == ["M1"]
        assert [m["constraints"]["mission"] for m in seen["ew"]["missions"]] == ["M2"]

        pilot_scenario = seen["pilot"]["scenario"]
        assert [m["id"] for m in pilot_scenario["missions"]] == ["M1"]
        assert [j["id"] for j in pilot_scenario["friendly_jammers"]] == ["J3"]
        assert seen["pilot"]["nominal_zones"] == []
        assert [z["threat"] for z in seen["ew"]["nominal_zones"]] == ["J4"]
        assert seen["ew"]["scenario"]["enemy_threats"] == []

        # A cut of J4 reaches its operator and the command, not the pilot of M1.
        command.send_json(schedule("CMD", "J4"))
        assert receive(command)["payload"]["type"] == "cut.scheduled"
        to_operator = [receive(operator)["payload"] for _ in range(2)]
        assert [p["type"] for p in to_operator] == ["cut.scheduled", "notification"]

        # The next event the pilot gets is the clock, not the J4 cut.
        command.send_json({"req_id": "r2", "command": {"type": "clock.set", "actor": "CMD"}})
        assert receive(pilot)["payload"]["type"] == "clock"


def test_plans_reach_the_clients(scenario: Scenario) -> None:
    def inline(scenario: Scenario, grids: dict[str, Grid]) -> Replanner:
        class Inline:
            def request(
                self,
                mission: str,
                revision: int,
                cuts: list[Cut],
                earliest_s: float,
                done: PlanDone,
            ) -> None:
                m = scenario.mission(mission)
                assert m is not None
                done(revision, plan_mission(scenario, m, grids[mission], cuts, earliest_s))

            def request_fallback(
                self, mission: str, revision: int, position: Position, now_s: float, done: PlanDone
            ) -> None:
                raise AssertionError("no flight in this test")

            def close(self) -> None:
                pass

        return Inline()

    app = create_app(scenario, clock_factor=20, cell_m=100, replanner=inline)
    with TestClient(app) as client, client.websocket_connect("/ws?callsign=PILOT-1") as ws:
        (m1,) = receive(ws)["payload"]["missions"]
        assert m1["plan"]["revision"] == 1  # planned at startup
        assert m1["plan"]["status"] == "baseline"

        ws.send_json(schedule("PILOT-1", "J3"))
        # The pilot sees the cut, its ack and the plan; the notification goes to the operator only.
        payloads = [receive(ws)["payload"] for _ in range(3)]
        plan = next(p for p in payloads if p["type"] == "plan.updated")
        assert (plan["mission"], plan["revision"], plan["status"]) == ("M1", 2, "preview")
        assert plan["metrics"]["saved_pct"] > 5
        assert not any(p["type"] == "notification" for p in payloads)


def test_rf_coverage_endpoint(scenario: Scenario) -> None:
    client = TestClient(create_app(scenario, clock_factor=20, cell_m=100))
    response = client.get("/api/rf/coverage")
    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "FeatureCollection"
    assert len(data["features"]) > 0
    first = data["features"][0]
    assert first["geometry"]["type"] == "Polygon"
    assert "level_dbm" in first["properties"]
    assert "threat_id" in first["properties"]


def test_rf_heatmap_endpoint(scenario: Scenario) -> None:
    client = TestClient(create_app(scenario, clock_factor=20, cell_m=100))
    response = client.get("/api/rf/heatmap?step_m=500")
    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "FeatureCollection"
    assert len(data["features"]) > 0
    pt0 = data["features"][0]
    assert pt0["geometry"]["type"] == "Point"
    assert "weight" in pt0["properties"]
    assert "js_db" in pt0["properties"]
    assert "status" in pt0["properties"]

    # Test cells=true polygon quads
    resp_cells = client.get("/api/rf/heatmap?step_m=500&cells=true")
    assert resp_cells.status_code == 200
    data_cells = resp_cells.json()
    cell0 = data_cells["features"][0]
    assert cell0["geometry"]["type"] == "Polygon"
    assert "color" in cell0["properties"]
    assert "opacity" in cell0["properties"]


def test_rf_probe_endpoint(scenario: Scenario) -> None:
    client = TestClient(create_app(scenario, clock_factor=20, cell_m=100))
    response = client.get("/api/rf/probe?lat=48.145&lon=11.55")
    assert response.status_code == 200
    data = response.json()
    assert "js_db" in data
    assert "status" in data
    assert "elev_m" in data
    assert "p_pilot_dbm" in data
    assert "p_jammer_dbm" in data
    assert "details" in data
