"""Who sees what: each connection only receives what its role needs to know.

- command: everything.
- pilot: its missions, the threats that concern them, the cuts of those jammers, its messages.
  A pilot without a mission yet sees nothing but the clock and its messages.
- operator: its jammers, their cuts, the missions they concern, its messages.

The filter runs on the server, so a pilot never receives another mission, even hidden. The
clock goes to everyone. Missions are read from the engine: a requested mission widens the view
of its pilot and of the operators it concerns.
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
    MissionCreated,
    MissionStatusChanged,
    MissionUpdated,
    Notification,
    PlanUpdated,
    ThreatAdded,
    ThreatRemoved,
)
from corridor.domain.model import Model
from corridor.domain.scenario import Role, Scenario
from corridor.domain.zones import Zone
from corridor.engine.engine import Engine


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
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def viewer(self, callsign: str) -> Viewer | None:
        """None when the scenario does not know ``callsign``."""
        role = self._engine.scenario.role_of(callsign)
        return None if role is None else Viewer(callsign=callsign, role=role)

    def scope(self, viewer: Viewer) -> Scope:
        scenario = self._engine.scenario
        constraints = {m: r.constraints for m, r in self._engine.missions.items()}
        match viewer.role:
            case "command":
                return Scope(
                    missions=frozenset(m.id for m in scenario.missions),
                    jammers=frozenset(j.id for j in scenario.friendly_jammers),
                    threats=frozenset(t.id for t in scenario.enemy_threats),
                )
            case "pilot":
                own = [
                    constraints[m.id]
                    for m in scenario.missions
                    if m.pilot_callsign == viewer.callsign
                ]
                return Scope(
                    missions=frozenset(c.mission for c in own),
                    jammers=frozenset(j for c in own for j in c.jammers),
                    threats=frozenset(t for c in own for t in c.threats),
                )
            case "operator":
                jammers = frozenset(
                    j.id
                    for j in scenario.friendly_jammers
                    if j.operator_callsign == viewer.callsign
                )
                missions = frozenset(m for m, c in constraints.items() if jammers & set(c.jammers))
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
            case MissionCreated() | MissionUpdated():
                return event.mission.id in scope.missions
            case ThreatAdded() | ThreatRemoved():
                return not scope.missions.isdisjoint(event.missions)
            case _:
                assert_never(event)

    def scenario_for(self, viewer: Viewer) -> Scenario:
        """The scenario cut down to ``viewer``'s scope: pilots too, the command sees them all."""
        scope = self.scope(viewer)
        scenario = self._engine.scenario
        missions = [m for m in scenario.missions if m.id in scope.missions]
        # A pilot also sees itself before its first mission.
        pilots = {m.pilot_callsign for m in missions}
        if viewer.role == "pilot":
            pilots.add(viewer.callsign)
        # model_copy skips validation: an operator whose jammers concern no mission gets none.
        return scenario.model_copy(
            update={
                "friendly_jammers": [j for j in scenario.friendly_jammers if j.id in scope.jammers],
                "enemy_threats": [t for t in scenario.enemy_threats if t.id in scope.threats],
                "pilots": [
                    p for p in scenario.pilots if p.callsign in pilots or viewer.role == "command"
                ],
                "missions": missions,
            }
        )

    def nominal_zones_for(self, viewer: Viewer, zones: list[Zone]) -> list[Zone]:
        """A pilot works with the zones of its mission, not with nominal ones."""
        if viewer.role == "pilot":
            return []
        scope = self.scope(viewer)
        return [z for z in zones if z.threat in scope.jammers | scope.threats]
