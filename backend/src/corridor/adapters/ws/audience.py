"""Who sees what: each connection only receives what its role needs to know.

- command: everything.
- pilot: its mission, the threats that concern it, the cuts of those jammers, its messages.
- operator: its jammers, their cuts, the missions they concern, its messages.

The filter runs on the server, so a pilot never receives another mission, even hidden. The
clock goes to everyone.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import assert_never

from corridor.domain.events import (
    ClockChanged,
    CutCancelled,
    CutConfirmed,
    CutScheduled,
    DroneState,
    Event,
    JammerRestarted,
    MissionStatusChanged,
    Notification,
    PlanUpdated,
)
from corridor.domain.model import Model
from corridor.domain.scenario import Role, Scenario
from corridor.domain.zones import MissionConstraints, Zone


class Viewer(Model):
    """Who is connected: the callsign the client gave, and its role in the scenario."""

    callsign: str
    role: Role


@dataclass(frozen=True)
class Scope:
    """Ids a viewer may see."""

    missions: frozenset[str]
    jammers: frozenset[str]
    """Friendly jammers."""
    threats: frozenset[str]
    """Enemy threats."""


class Audience:
    def __init__(self, scenario: Scenario, constraints: dict[str, MissionConstraints]) -> None:
        self._scenario = scenario
        self._constraints = constraints

    def viewer(self, callsign: str) -> Viewer | None:
        """None when the scenario does not know ``callsign``."""
        role = self._scenario.role_of(callsign)
        return None if role is None else Viewer(callsign=callsign, role=role)

    def scope(self, viewer: Viewer) -> Scope:
        scenario = self._scenario
        match viewer.role:
            case "command":
                return Scope(
                    missions=frozenset(m.id for m in scenario.missions),
                    jammers=frozenset(j.id for j in scenario.friendly_jammers),
                    threats=frozenset(t.id for t in scenario.enemy_threats),
                )
            case "pilot":
                mission = next(m for m in scenario.missions if m.pilot_callsign == viewer.callsign)
                constraints = self._constraints[mission.id]
                return Scope(
                    missions=frozenset([mission.id]),
                    jammers=frozenset(constraints.jammers),
                    threats=frozenset(constraints.threats),
                )
            case "operator":
                jammers = frozenset(
                    j.id
                    for j in scenario.friendly_jammers
                    if j.operator_callsign == viewer.callsign
                )
                missions = frozenset(
                    m for m, c in self._constraints.items() if jammers & set(c.jammers)
                )
                return Scope(missions=missions, jammers=jammers, threats=frozenset())
            case _:
                assert_never(viewer.role)

    def sees(self, viewer: Viewer, event: Event) -> bool:
        if viewer.role == "command":
            return True
        scope = self.scope(viewer)
        match event:
            case ClockChanged():
                return True
            case CutScheduled() | CutConfirmed() | CutCancelled():
                return event.cut.jammer in scope.jammers
            case JammerRestarted():
                return event.jammer in scope.jammers
            case Notification():
                return event.to == viewer.callsign
            case PlanUpdated() | DroneState() | MissionStatusChanged():
                return event.mission in scope.missions
            case _:
                assert_never(event)

    def scenario_for(self, viewer: Viewer) -> Scenario:
        """The scenario cut down to ``viewer``'s scope."""
        scope = self.scope(viewer)
        scenario = self._scenario
        # model_copy skips validation: an operator whose jammers concern no mission gets none.
        return scenario.model_copy(
            update={
                "friendly_jammers": [j for j in scenario.friendly_jammers if j.id in scope.jammers],
                "enemy_threats": [t for t in scenario.enemy_threats if t.id in scope.threats],
                "missions": [m for m in scenario.missions if m.id in scope.missions],
            }
        )

    def nominal_zones_for(self, viewer: Viewer, zones: list[Zone]) -> list[Zone]:
        """A pilot works with the zones of its mission, not with nominal ones."""
        if viewer.role == "pilot":
            return []
        scope = self.scope(viewer)
        return [z for z in zones if z.threat in scope.jammers | scope.threats]
