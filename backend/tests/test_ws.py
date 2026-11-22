from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession
from starlette.websockets import WebSocketDisconnect

from corridor.adapters.ws.app import UNKNOWN_CALLSIGN, create_app
from corridor.domain.geo import Grid, Position
from corridor.domain.planner.plan import plan_mission
from corridor.domain.scenario import Mission, Scenario
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
        assert notification["payload"]["to"] == "CMD"
        assert ack["payload"] == {"type": "ack", "req_id": "r1"}

        cut_id = scheduled["payload"]["cut"]["id"]
        confirm = {"type": "cut.confirm", "actor": "CMD", "cut": cut_id}
        ws.send_json({"req_id": "r2", "command": confirm})
        confirmed, order, ack = receive(ws), receive(ws), receive(ws)
        assert confirmed["payload"]["type"] == "cut.confirmed"
        assert order["payload"]["to"] == "EW-J3"
        assert ack["payload"] == {"type": "ack", "req_id": "r2"}


def test_events_reach_every_client_and_bad_messages_get_an_error(scenario: Scenario) -> None:
    app = create_app(scenario, clock_factor=20, cell_m=100)
    with (
        TestClient(app) as client,  # one event loop for every connection
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
    app = create_app(two_missions, clock_factor=20, cell_m=100)
    with (
        TestClient(app) as client,  # one event loop for every connection
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
        # Reference footprints of what concerns M1, drawn under the pilot's own zones.
        assert sorted(z["threat"] for z in seen["pilot"]["nominal_zones"]) == ["E1", "J3", "R1"]
        assert [p["callsign"] for p in pilot_scenario["pilots"]] == ["PILOT-1"]
        assert [p["callsign"] for p in seen["ew"]["scenario"]["pilots"]] == ["PILOT-2"]
        assert [p["callsign"] for p in seen["cmd"]["scenario"]["pilots"]] == ["PILOT-1", "PILOT-2"]
        assert [z["threat"] for z in seen["ew"]["nominal_zones"]] == ["J4"]
        assert seen["ew"]["scenario"]["enemy_threats"] == []

        # A cut of J4 reaches its operator and the command, not the pilot of M1. The command
        # confirms it; only then does the operator get the order to carry it out.
        command.send_json(schedule("CMD", "J4"))
        scheduled = receive(command)["payload"]
        assert scheduled["type"] == "cut.scheduled"
        assert receive(operator)["payload"]["type"] == "cut.scheduled"
        confirm = {"type": "cut.confirm", "actor": "CMD", "cut": scheduled["cut"]["id"]}
        command.send_json({"req_id": "r1", "command": confirm})
        to_operator = [receive(operator)["payload"] for _ in range(2)]
        assert [p["type"] for p in to_operator] == ["cut.confirmed", "notification"]

        # The next event the pilot gets is the clock, not the J4 cut.
        command.send_json({"req_id": "r2", "command": {"type": "clock.set", "actor": "CMD"}})
        assert receive(pilot)["payload"]["type"] == "clock"


def test_plans_reach_the_clients(scenario: Scenario) -> None:
    def inline(_: Scenario) -> Replanner:
        class Inline:
            def request(
                self,
                scenario: Scenario,
                mission: Mission,
                grid: Grid,
                revision: int,
                cuts: list[Cut],
                earliest_s: float,
                done: PlanDone,
            ) -> None:
                done(revision, plan_mission(scenario, mission, grid, cuts, earliest_s))

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


def test_rf_heatmap_endpoint_returns_a_png_image_once_per_configuration(
    scenario: Scenario,
) -> None:
    client = TestClient(create_app(scenario, clock_factor=20, cell_m=100))
    response = client.get("/api/rf/heatmap?step_m=500")
    assert response.status_code == 200
    data = response.json()
    assert data["image"].startswith("data:image/png;base64,")
    (w, n), (e, n2), (e2, s), (w2, s2) = data["coordinates"]
    assert (w, e, n, s) == (w2, e2, n2, s2)
    assert w < e and s < n
    assert data["step_m"] == 500

    assert client.get("/api/rf/heatmap?step_m=500").json() == data


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


def test_a_requested_mission_resyncs_who_sees_it(two_missions: Scenario) -> None:
    """PILOT-1 starts with no mission: its request brings M1's jammer into its view."""
    data = two_missions.model_dump(mode="json")
    m1 = data["missions"].pop(0)
    app = create_app(Scenario.model_validate(data), clock_factor=20, cell_m=100)
    with (
        TestClient(app) as client,  # one event loop for every connection
        client.websocket_connect("/ws?callsign=PILOT-1") as pilot,
        client.websocket_connect("/ws?callsign=PILOT-2") as other,
        client.websocket_connect("/ws?callsign=EW-J3") as operator,
    ):
        before = receive(pilot)["payload"]
        assert (before["missions"], before["scenario"]["friendly_jammers"]) == ([], [])
        assert [p["callsign"] for p in before["scenario"]["pilots"]] == ["PILOT-1"]
        receive(other), receive(operator)

        request = {"type": "mission.request", "actor": "PILOT-1", "departure_s": 600}
        pilot.send_json(
            {"req_id": "r1", "command": request | {k: m1[k] for k in ("launch", "target")}}
        )
        created, after, notification, ack = (receive(pilot)["payload"] for _ in range(4))
        assert created["type"] == "mission.created"
        assert (created["mission"]["id"], created["mission"]["drone_id"]) == ("M1", "UAV-1")
        assert after["type"] == "snapshot"
        assert [m["constraints"]["mission"] for m in after["missions"]] == ["M1"]
        assert [j["id"] for j in after["scenario"]["friendly_jammers"]] == ["J3"]
        assert notification["type"] == "notification"
        assert ack == {"type": "ack", "req_id": "r1"}

        # The operator of J3 sees the new mission; the other pilot does not.
        assert [receive(operator)["payload"]["type"] for _ in range(2)] == [
            "mission.created",
            "snapshot",
        ]
        other.send_json({"req_id": "r2", "command": {"type": "clock.set", "actor": "PILOT-2"}})
        assert receive(other)["payload"]["type"] == "clock"


def test_rf_endpoints_without_any_mission(scenario: Scenario) -> None:
    data = scenario.model_dump(mode="json")
    data["missions"] = []
    client = TestClient(create_app(Scenario.model_validate(data), clock_factor=20, cell_m=100))
    assert client.get("/api/rf/coverage").status_code == 200
    assert client.get("/api/rf/probe?lat=48.145&lon=11.55").status_code == 404


def test_an_updated_mission_resyncs_every_client(two_missions: Scenario) -> None:
    """M2 moves from J4 onto J3: J4's operator loses it, J3's operator gains it."""
    app = create_app(two_missions, clock_factor=20, cell_m=100)
    m1 = two_missions.missions[0].model_dump(mode="json")
    with (
        TestClient(app) as client,  # one event loop for every connection
        client.websocket_connect("/ws?callsign=PILOT-2") as pilot,
        client.websocket_connect("/ws?callsign=EW-J4") as old_operator,
        client.websocket_connect("/ws?callsign=EW-J3") as new_operator,
    ):
        receive(pilot), receive(old_operator), receive(new_operator)
        update = {"type": "mission.update", "actor": "PILOT-2", "mission": "M2"}
        points = {"launch": m1["launch"], "target": m1["target"], "departure_s": 600}
        pilot.send_json({"req_id": "r1", "command": update | points})

        updated, after = receive(pilot)["payload"], receive(pilot)["payload"]
        assert updated["type"] == "mission.updated"
        (m2,) = after["missions"]
        assert m2["constraints"]["jammers"] == ["J3"]
        assert m2["plan"] is None

        # The old operator gets no event but a snapshot without M2.
        lost = receive(old_operator)["payload"]
        assert lost["type"] == "snapshot"
        assert [m["constraints"]["mission"] for m in lost["missions"]] == []
        gained = [receive(new_operator)["payload"] for _ in range(2)]
        assert [p["type"] for p in gained] == ["mission.updated", "snapshot"]
        assert [m["constraints"]["mission"] for m in gained[1]["missions"]] == ["M1", "M2"]


def test_buildings_endpoint_is_empty_without_buildings(scenario: Scenario) -> None:
    client = TestClient(create_app(scenario, clock_factor=20, cell_m=100))
    response = client.get("/api/buildings")
    assert response.status_code == 200
    assert response.json() == {"type": "FeatureCollection", "features": []}


def test_zones3d_endpoint_gives_outlines_per_level_in_3d(scenario: Scenario) -> None:
    flat = TestClient(create_app(scenario, clock_factor=20, cell_m=100))
    assert flat.get("/api/zones3d", params={"mission_id": "M1"}).json()["features"] == []
    assert flat.get("/api/zones3d", params={"mission_id": "M9"}).status_code == 404
    terrain = scenario.terrain.model_copy(update={"mode": "3d"})
    deep = TestClient(create_app(scenario.model_copy(update={"terrain": terrain}), 20, 100))
    features = deep.get("/api/zones3d", params={"mission_id": "M1"}).json()["features"]
    assert {f["properties"]["threat"] for f in features} >= {"J3", "E1", "R1"}
    levels = {f["properties"]["level_m"] for f in features}
    assert levels == set(scenario.terrain.levels_m)
    assert all(f["geometry"]["type"] == "Polygon" for f in features)
