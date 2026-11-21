"""Commands: requests to change the world, sent by any adapter.

``actor`` is the callsign of whoever sends the command. The engine checks it against the
domain rules (only the jammer operator confirms a cut), whatever the channel.
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

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


class ConfirmCut(Model):
    type: Literal["cut.confirm"] = "cut.confirm"
    actor: str
    cut: str


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
    ScheduleCut | CancelCut | ConfirmCut | LaunchMission | SetClock | InjectDelay,
    Field(discriminator="type"),
]
