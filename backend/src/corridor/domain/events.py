"""Events: facts emitted by the engine, consumed by every adapter."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from corridor.domain.geo import Position
from corridor.domain.model import Model
from corridor.domain.state import ClockState, Cut, MissionStatus


class CutScheduled(Model):
    type: Literal["cut.scheduled"] = "cut.scheduled"
    cut: Cut


class CutCancelled(Model):
    type: Literal["cut.cancelled"] = "cut.cancelled"
    cut: Cut = Field(description="The cut as it was before cancellation.")


class CutConfirmed(Model):
    type: Literal["cut.confirmed"] = "cut.confirmed"
    cut: Cut


class ClockChanged(Model):
    type: Literal["clock"] = "clock"
    clock: ClockState


class MissionStatusChanged(Model):
    type: Literal["mission.status"] = "mission.status"
    mission: str
    status: MissionStatus


class NotificationAction(Model):
    """Command the recipient is expected to send back, shown as a button."""

    command: Literal["cut.confirm"]
    cut: str


class Notification(Model):
    type: Literal["notification"] = "notification"
    to: str = Field(description="Callsign of the pilot or of a jammer operator.")
    text: str
    action: NotificationAction | None = None


PlanStatus = Literal["preview", "approved", "fallback", "baseline"]
"""preview: uses a scheduled cut not confirmed yet, not flyable.
approved: uses confirmed cuts only. fallback: drone late, safe route from its position.
baseline: no usable cut, the optimised route is the baseline."""

RouteAction = Literal["fly", "hover", "ground"]


class RoutePoint(Model):
    """Position at time ``t``; ``action`` holds until the next point."""

    lat: float
    lon: float
    t: float
    action: RouteAction


class PlanMetrics(Model):
    baseline_battery_used_pct: float = Field(description="Of battery_max_s.")
    optimised_battery_used_pct: float = Field(description="Of battery_max_s.")
    saved_pct: float = Field(description="Relative to the baseline consumption.")
    saved_s: float = Field(description="Flight time saved, hover included, ground wait excluded.")


class PlanUpdated(Model):
    """Planner output, after every change to the cuts while the mission is planned."""

    type: Literal["plan.updated"] = "plan.updated"
    mission: str
    revision: int = Field(description="Grows by one per plan request of this mission.")
    status: PlanStatus
    metrics: PlanMetrics
    baseline_route: list[RoutePoint] = Field(description="As if every jammer stayed on.")
    optimised_route: list[RoutePoint]


class DroneState(Model):
    """Simulated drone, every engine tick (about 2 Hz) while it flies."""

    type: Literal["drone.state"] = "drone.state"
    mission: str
    position: Position
    action: RouteAction
    battery_used_pct: float = Field(description="Of battery_max_s, airborne time so far.")
    delay_s: float = Field(description="Behind the schedule of the route being flown.")
    revision: int = Field(description="Plan revision of the route being flown.")


class JammerRestarted(Model):
    """A confirmed cut ended: the jammer is back on."""

    type: Literal["jammer.restarted"] = "jammer.restarted"
    jammer: str
    cut: str


Event = Annotated[
    CutScheduled
    | CutCancelled
    | CutConfirmed
    | ClockChanged
    | MissionStatusChanged
    | Notification
    | PlanUpdated
    | DroneState
    | JammerRestarted,
    Field(discriminator="type"),
]
