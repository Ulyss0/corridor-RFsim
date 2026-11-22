"""Events: facts emitted by the engine, consumed by every adapter."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from corridor.domain.geo import Position
from corridor.domain.model import Model
from corridor.domain.scenario import EnemyThreat, Mission
from corridor.domain.scenario import LostLinkAction as LostLinkAction
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


class RelaySwitched(Model):
    """The command switched a relay on or off. Every client gets a new snapshot right after:
    zones, grids and scopes may all have changed."""

    type: Literal["relay.switched"] = "relay.switched"
    relay: str
    on: bool
    missions: list[str] = Field(description="Missions on its bands.")


class MissionStatusChanged(Model):
    type: Literal["mission.status"] = "mission.status"
    mission: str
    status: MissionStatus


class NotificationAction(Model):
    """Command the recipient is expected to send back, shown as a button.

    ``cut.confirm`` names a cut; ``route.decide`` names a mission whose route request awaits
    an answer (accept or refuse).
    """

    command: Literal["cut.confirm", "route.decide"]
    cut: str | None = None
    mission: str | None = None


class Notification(Model):
    type: Literal["notification"] = "notification"
    to: str = Field(description="Callsign of the pilot or of a jammer operator.")
    text: str
    action: NotificationAction | None = None


PlanStatus = Literal["preview", "approved", "fallback", "baseline", "straight"]
"""preview: uses a scheduled cut not confirmed yet, not flyable.
approved: uses confirmed cuts only. fallback: drone late, safe route from its position.
baseline: no usable cut, the optimised route is the baseline.
straight: the cut was refused (or not answered) and the pilot's plan B is "straight anyway":
the drone flies the direct route through the jammer still on, control link at risk."""

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


ChoiceKind = Literal["fastest", "one_cut", "safe"]
"""fastest: cuts as many friendly jammers as it needs. one_cut: the fastest cutting a single
jammer. safe: no new cut, flyable as is. The front end calls safe the detour and the others
direct routes."""


class ProposedCut(Model):
    """A cut a route needs: jammer off on [start, end], safety margins included."""

    jammer: str
    start_s: float
    end_s: float


ReasonKind = Literal["friendly_jammer", "enemy_jammer", "radar", "avoid", "link"]
"""A zone kind, or link: where the pilot's signal is too weak (3D)."""

ReasonEffect = Literal["avoided", "climbed_over", "crossed_in_cut", "crossed"]
"""What the route does about a zone. avoided: around it. climbed_over (3D): over it, at a level
above the zone. crossed_in_cut: through a friendly jammer's zone while it is cut. crossed:
through the zone with the threat on (a radar the mission may cross, a zone to avoid, a jammer
crossed anyway)."""


class RouteReason(Model):
    """One zone that shapes the route: on the straight line from launch to target, or crossed."""

    threat: str = Field(description="Threat id; LINK for the cells out of the pilot's reach.")
    kind: ReasonKind
    effect: ReasonEffect
    enter_s: float | None = Field(
        default=None, description="Crossings: when the route enters the zone."
    )
    exit_s: float | None = Field(default=None, description="Crossings: when it leaves it.")


class RouteChoice(Model):
    """One route the pilot may pick. Only ``safe`` is flyable without the command's answer."""

    kind: ChoiceKind
    cuts: list[ProposedCut] = Field(description="New cuts the route needs; none for safe.")
    route: list[RoutePoint]
    flight_s: float = Field(description="Time in the air, hover included, ground wait excluded.")
    battery_used_pct: float = Field(description="Of battery_max_s.")
    saved_s: float = Field(description="Flight time saved against the safe choice.")
    saved_pct: float = Field(description="Relative to the safe choice's flight time.")
    radars: list[str] = Field(
        default_factory=list,
        description="Radars whose zone the route crosses (only when the mission lets it).",
    )
    worth_asking: bool = Field(
        default=True,
        description="False: shown for comparison, but saves too little to ask the command a cut.",
    )
    reasons: list[RouteReason] = Field(
        default_factory=list, description="Zones that shape this route, in order along the way."
    )
    lost_link: LostLinkAction = Field(
        default="rth",
        description="Failsafe instruction if control link is lost (rth, continue, land).",
    )


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
    choices: list[RouteChoice] = Field(
        default_factory=list,
        description="Routes the pilot may pick, fastest first, safe last. Empty once it flies.",
    )
    reasons: list[RouteReason] = Field(
        default_factory=list,
        description="Zones that shape the optimised route, in order along the way.",
    )
    lost_link: LostLinkAction = Field(
        default="rth",
        description="Failsafe instruction if control link is lost (rth, continue, land).",
    )


OnTimeout = Literal["cross", "avoid"]
"""The pilot's plan B if the command refuses the cuts or has not answered by the deadline:
cross (fly the direct route anyway, straight through the jammers still on, control link at
risk) or avoid (fly the detour, the ``safe`` choice). Only the command ever cuts a jammer."""

RequestStatus = Literal["pending", "accepted", "refused"]


class RouteRequest(Model):
    """The route a pilot picked and, if it needs cuts, the command's answer."""

    mission: str
    kind: ChoiceKind
    cuts: list[str] = Field(description="Ids of the cuts asked for; none for safe.")
    saved_s: float = Field(description="Flight time the route saves against the safe one.")
    saved_pct: float
    deadline_s: float | None = Field(
        description="Last moment for the command to answer; None when nothing is asked."
    )
    on_timeout: OnTimeout
    status: RequestStatus
    route: list[RoutePoint] = Field(
        default_factory=list, description="The route picked: flown as is if refused and cross."
    )
    jammers: list[str] = Field(
        default_factory=list, description="Friendly jammers the route goes through."
    )
    decided_by: str | None = Field(
        default=None, description="Callsign that answered, or 'timeout' or 'launch'."
    )


class RouteRequested(Model):
    """A route request was made or answered."""

    type: Literal["route.request"] = "route.request"
    request: RouteRequest


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
    | RelaySwitched
    | MissionStatusChanged
    | Notification
    | PlanUpdated
    | RouteRequested
    | DroneState
    | JammerRestarted,
    Field(discriminator="type"),
]
