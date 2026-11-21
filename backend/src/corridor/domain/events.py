"""Events: facts emitted by the engine, consumed by every adapter."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from corridor.domain.geo import Position
from corridor.domain.model import Model
from corridor.domain.scenario import EnemyThreat, Mission
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


class MissionCreated(Model):
    """A pilot's request became a mission. Clients that see it get a new snapshot right after:
    the mission may bring jammers and threats into their view."""

    type: Literal["mission.created"] = "mission.created"
    mission: Mission


class MissionUpdated(Model):
    """A mission on the ground got new points or a new departure; its plan is dropped and a
    new one requested. Every client gets a new snapshot right after: the mission may enter or
    leave its view."""

    type: Literal["mission.updated"] = "mission.updated"
    mission: Mission


class ThreatAdded(Model):
    """The command added an enemy threat or a zone to avoid. Clients that see it get a new
    snapshot right after: zones, grids and scopes may all have changed."""

    type: Literal["threat.added"] = "threat.added"
    threat: EnemyThreat
    missions: list[str] = Field(description="Missions it concerns.")


class ThreatRemoved(Model):
    """The command removed a threat. Clients that see it get a new snapshot right after."""

    type: Literal["threat.removed"] = "threat.removed"
    threat: str
    missions: list[str] = Field(description="Missions it concerned.")


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
    alt_m: float | None = Field(
        default=None,
        description=(
            "Height above the clearance ground (highest ground within a cell around), metres;"
            " 0 on the ground; None in 2D."
        ),
    )
    amsl_m: float | None = Field(
        default=None, description="Altitude above sea level, metres; None in 2D."
    )


class ProfilePoint(Model):
    """A sample of the route in 3D, for the altitude profile and a 3D view."""

    distance_m: float = Field(description="Along the route from take-off.")
    lat: float
    lon: float
    ground_m: float = Field(description="Ground surface above sea level; 0 without a map.")
    alt_m: float | None = Field(description="As RoutePoint.alt_m, interpolated; None in 2D.")
    amsl_m: float | None = Field(description="Drone altitude above sea level; None in 2D.")


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
    profile: list[ProfilePoint] = Field(
        default_factory=list, description="Ground and drone height under the optimised route."
    )


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
    | MissionCreated
    | MissionUpdated
    | ThreatAdded
    | ThreatRemoved
    | MissionStatusChanged
    | Notification
    | PlanUpdated
    | DroneState
    | JammerRestarted,
    Field(discriminator="type"),
]
