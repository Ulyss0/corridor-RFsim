"""Runtime state: what changes during a run. Lives in memory, owned by the engine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import Field

from corridor.domain.model import Model
from corridor.domain.scenario import Scenario

CutStatus = Literal["scheduled", "confirmed"]
MissionStatus = Literal["planned", "flying", "arrived"]

SCENARIO_ACTOR = "scenario"
"""``requested_by`` of the cuts listed in the scenario file."""


class Cut(Model):
    """A window during which a friendly jammer is off.

    Only a confirmed cut may be used by the planner for the approved route.
    """

    id: str
    jammer: str
    start_s: float
    end_s: float
    status: CutStatus
    requested_by: str
    requested_for: str | None = Field(
        default=None, description="Mission the cut was asked for; None: from the scenario."
    )


class ClockState(Model):
    sim_time_s: float
    factor: float = Field(description="Simulated seconds per wall-clock second.")
    paused: bool


@dataclass
class WorldState:
    cuts: dict[str, Cut]
    missions: dict[str, MissionStatus]
    """Status of every mission, by id."""

    @classmethod
    def from_scenario(cls, scenario: Scenario) -> WorldState:
        cuts = [
            Cut(
                id=f"cut-{jammer.id}-{index}",
                jammer=jammer.id,
                start_s=planned.start_s,
                end_s=planned.end_s,
                status="scheduled",
                requested_by=SCENARIO_ACTOR,
            )
            for jammer in scenario.friendly_jammers
            for index, planned in enumerate(jammer.cuts, start=1)
        ]
        return cls(
            cuts={c.id: c for c in cuts},
            missions={m.id: "planned" for m in scenario.missions},
        )
