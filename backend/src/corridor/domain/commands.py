"""Commands: requests to change the world, sent by any adapter.

``actor`` is the callsign of whoever sends the command. The engine checks it against the
domain rules (only the jammer operator confirms a cut), whatever the channel.
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from corridor.domain.geo import Position
from corridor.domain.model import Model


class ScheduleCut(Model):
    type: Literal["cut.schedule"] = "cut.schedule"
    actor: str
    jammer: str
    start_s: float = Field(ge=0)
    end_s: float
    mission: str | None = Field(default=None, description="Mission the cut is asked for.")

    @model_validator(mode="after")
    def _end_after_start(self) -> Self:
        if self.end_s <= self.start_s:
            raise ValueError("end_s must be after start_s")
        return self


class CancelCut(Model):
    type: Literal["cut.cancel"] = "cut.cancel"
    actor: str
    cut: str
    reason: str | None = Field(
        default=None, description="Reason for cancellation or refusal."
    )


class ConfirmCut(Model):
    type: Literal["cut.confirm"] = "cut.confirm"
    actor: str
    cut: str


class RequestMission(Model):
    """A pilot asks for a route: a new mission with its drone, planned right away."""

    type: Literal["mission.request"] = "mission.request"
    actor: str
    launch: Position = Field(description="Where the pilot stands and the drone takes off.")
    target: Position
    departure_s: float = Field(
        ge=0, description="Earliest departure; one in the past means as soon as possible."
    )


class UpdateMission(Model):
    """Move the launch or target of a mission still on the ground, or change its departure.

    Same rights as launch: its pilot or the command post.
    """

    type: Literal["mission.update"] = "mission.update"
    actor: str
    mission: str
    launch: Position
    target: Position
    departure_s: float = Field(
        ge=0, description="Earliest departure; one in the past means as soon as possible."
    )


class LaunchMission(Model):
    """Only the mission pilot or the command post may launch."""

    type: Literal["mission.launch"] = "mission.launch"
    actor: str
    mission: str


class SetClock(Model):
    type: Literal["clock.set"] = "clock.set"
    actor: str
    factor: float | None = Field(default=None, gt=0)
    paused: bool | None = None


class InjectDelay(Model):
    """Demo twist: hold the flying drone where it is for ``delay_s`` simulated seconds."""

    type: Literal["inject.delay"] = "inject.delay"
    actor: str
    mission: str
    delay_s: float = Field(gt=0, le=1800)


Command = Annotated[
    ScheduleCut
    | CancelCut
    | ConfirmCut
    | RequestMission
    | UpdateMission
    | LaunchMission
    | SetClock
    | InjectDelay,
    Field(discriminator="type"),
]
