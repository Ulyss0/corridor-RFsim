"""WebSocket wire format.

Client to server: ``{"req_id": "...", "command": {"type": "cut.schedule", ...}}``.
Server to client: ``{"seq": 42, "sim_time_s": 900.0, "payload": {"type": "...", ...}}``.

Domain events go out as they are, wrapped in the envelope, to the clients allowed to see them
(``corridor.adapters.ws.audience``). ``seq`` grows by one per broadcast event, seen or not, so a
client may see gaps; ``snapshot``, ``ack`` and ``error`` carry the current ``seq`` without
incrementing it.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from corridor.adapters.ws.audience import Viewer
from corridor.domain.commands import Command
from corridor.domain.events import DroneState, Event, PlanUpdated
from corridor.domain.model import Model
from corridor.domain.scenario import Scenario
from corridor.domain.state import ClockState, Cut, MissionStatus
from corridor.domain.zones import MissionConstraints, Zone


class MissionView(Model):
    constraints: MissionConstraints = Field(
        description="Grid, zones for this pilot, concerned jammers and threats."
    )
    status: MissionStatus
    plan: PlanUpdated | None = Field(description="Latest plan; None until the first one.")
    drone: DroneState | None = Field(description="Latest drone state; None before take-off.")


class Snapshot(Model):
    """State seen by one client, sent on connect. Enough to render any instant on it."""

    type: Literal["snapshot"] = "snapshot"
    viewer: Viewer
    scenario: Scenario = Field(description="Only the jammers, threats and missions in view.")
    nominal_zones: list[Zone] = Field(
        description="Mission-independent footprints, see domain.zones. None for a pilot."
    )
    missions: list[MissionView]
    clock: ClockState
    cuts: list[Cut] = Field(description="Cuts of the jammers in view.")


class Ack(Model):
    type: Literal["ack"] = "ack"
    req_id: str


class Error(Model):
    type: Literal["error"] = "error"
    req_id: str | None = Field(description="None when the message could not be parsed.")
    reason: str


Reply = Annotated[Snapshot | Ack | Error, Field(discriminator="type")]


class ClientMessage(Model):
    req_id: str
    command: Command


class ServerMessage(Model):
    seq: int
    sim_time_s: float
    payload: Event | Reply
