"""FastAPI application exposing the engine on ``/ws``, and route export over HTTP.

GET  /api/export/inav       INAV mission (MW XML) of the current plan
GET  /api/export/ardupilot  ArduPilot mission (QGC WPL 110)
GET  /api/export/briefing   the pilot's briefing, plain text
POST /api/export/atak       send the route to the pilot's ATAK through OpenTAKServer

Every export takes ``altitude_m`` (above launch) and refuses a ``preview`` plan: it relies
on cuts nobody confirmed.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Callable
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Query, Response, WebSocket, WebSocketDisconnect
from pydantic import Field, ValidationError

from corridor.adapters.ws.hub import Hub, Outbox
from corridor.adapters.ws.schemas import Ack, ClientMessage, Error, Snapshot
from corridor.domain.geo import Grid
from corridor.domain.model import Model
from corridor.domain.scenario import Scenario
from corridor.domain.zones import Zone, mission_zones, planning_grid
from corridor.engine.clock import SimClock
from corridor.engine.engine import CommandRejected, Engine
from corridor.engine.replan import Replanner
from corridor.export.atak import TakTarget, deliver
from corridor.export.firmware import ardupilot_waypoints, inav_mission
from corridor.export.route import DEFAULT_ALTITUDE_M, FlightRoute, briefing, flight_route

ReplannerFactory = Callable[[Scenario, Grid], Replanner]

TICK_S = 0.5
log = logging.getLogger(__name__)

Altitude = Annotated[float, Query(gt=0, le=1000, description="Metres above launch.")]
FileFormat = Literal["inav", "ardupilot", "briefing"]


class AtakSent(Model):
    route_uid: str
    pilot_callsign: str
    chat_sent: bool = Field(description="False when the pilot's uid was not seen in time.")
    revision: int


def export_route(engine: Engine, altitude_m: float) -> FlightRoute:
    plan = engine.plan
    if plan is None:
        raise HTTPException(409, "no plan yet")
    if plan.status == "preview":
        raise HTTPException(409, "the plan relies on unconfirmed cuts")
    return flight_route(
        engine.scenario,
        plan.optimised_route,
        altitude_m,
        cuts=list(engine.state.cuts.values()),
        status=plan.status,
        revision=plan.revision,
        battery_used_pct=plan.metrics.optimised_battery_used_pct,
    )


def export_file(route: FlightRoute, fmt: FileFormat) -> Response:
    stem = route.mission_id
    if fmt == "inav":
        content, name, media = inav_mission(route), f"{stem}.mission", "application/xml"
    elif fmt == "ardupilot":
        content, name, media = ardupilot_waypoints(route), f"{stem}.waypoints", "text/plain"
    else:
        content, name, media = "\n".join(briefing(route)) + "\n", f"{stem}.txt", "text/plain"
    headers = {"Content-Disposition": f'attachment; filename="{name}"'}
    return Response(content, media_type=media, headers=headers)


async def _tick_forever(engine: Engine) -> None:
    while True:
        try:
            engine.tick()
        except Exception:
            log.exception("engine tick failed")
        await asyncio.sleep(TICK_S)


def snapshot(engine: Engine, grid: Grid, zones: list[Zone]) -> Snapshot:
    return Snapshot(
        scenario=engine.scenario,
        grid=grid,
        zones=zones,
        clock=engine.clock.state(),
        cuts=list(engine.state.cuts.values()),
        mission_status=engine.state.mission_status,
        plan=engine.plan,
        drone=engine.drone,
    )


def handle_message(raw: str, engine: Engine, hub: Hub, outbox: Outbox) -> None:
    try:
        message = ClientMessage.model_validate_json(raw)
    except ValidationError as exc:
        hub.reply(outbox, Error(req_id=None, reason=str(exc)))
        return
    try:
        engine.submit(message.command)
    except CommandRejected as exc:
        hub.reply(outbox, Error(req_id=message.req_id, reason=str(exc)))
        return
    hub.reply(outbox, Ack(req_id=message.req_id))


async def _pump(websocket: WebSocket, outbox: Outbox) -> None:
    while True:
        await websocket.send_text(await outbox.get())


def create_app(
    scenario: Scenario,
    clock_factor: float,
    cell_m: float,
    replanner: ReplannerFactory | None = None,
    tak: TakTarget | None = None,
) -> FastAPI:
    """Without ``replanner``, nothing is planned. The first plan is requested at startup.

    Without ``tak``, the ATAK export answers 503.
    """
    grid = planning_grid(scenario, cell_m)
    zones = mission_zones(scenario)
    planner = replanner(scenario, grid) if replanner else None
    engine = Engine(scenario, SimClock(clock_factor), planner)
    hub = Hub(engine.clock.now)
    engine.subscribe(hub.publish)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        engine.request_plan()
        ticker = asyncio.create_task(_tick_forever(engine))
        yield
        ticker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await ticker
        if planner:
            planner.close()

    app = FastAPI(title="Corridor", lifespan=lifespan)

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        outbox = hub.connect(snapshot(engine, grid, zones))
        sender = asyncio.create_task(_pump(websocket, outbox))
        try:
            while True:
                handle_message(await websocket.receive_text(), engine, hub, outbox)
        except WebSocketDisconnect:
            pass
        finally:
            hub.disconnect(outbox)
            sender.cancel()
            # The pump may already have died sending to a closed socket: nothing to report.
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await sender

    @app.get("/api/export/{fmt}")
    def get_export(fmt: FileFormat, altitude_m: Altitude = DEFAULT_ALTITUDE_M) -> Response:
        return export_file(export_route(engine, altitude_m), fmt)

    @app.post("/api/export/atak")
    async def post_atak(altitude_m: Altitude = DEFAULT_ALTITUDE_M) -> AtakSent:
        if tak is None:
            raise HTTPException(503, "no OpenTAKServer: start corridor-server with --tak-host")
        route = export_route(engine, altitude_m)
        try:
            sent = await deliver(tak, route)
        except OSError as exc:
            raise HTTPException(502, f"OpenTAKServer {tak.host}:{tak.port}: {exc}") from exc
        engine.notify(
            route.pilot_callsign,
            f"Route rev {route.revision} sent to your ATAK: take-off not before "
            f"{route.label(route.takeoff_s, up=True)}.",
        )
        assert route.revision is not None
        return AtakSent(
            route_uid=sent.route_uid,
            pilot_callsign=sent.pilot_callsign,
            chat_sent=sent.chat_sent,
            revision=route.revision,
        )

    return app
