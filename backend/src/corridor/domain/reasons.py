"""Why a route goes where it goes: the zones that shape it, and what it does about each.

A zone shapes a route when it lies on the straight line from launch to target (in 3D, at the
lowest level: where the drone would fly without climbing), or when the route crosses it. For
each, the route either:

- ``avoided`` it: goes around;
- ``climbed_over`` it (3D): its ground track passes under the zone's footprint at the lowest
  level, but at a level the zone does not reach;
- ``crossed_in_cut``: is inside a friendly jammer's zone only within a cut of that jammer;
- ``crossed``: is inside with the threat on (a radar the mission may cross, a zone to avoid,
  a jammer crossed anyway).

Zones are the ones the planner uses: shapes in 2D, the per-level cells in 3D (``LevelCells``,
``LINK`` for the cells out of the pilot's reach). Zones to avoid are not enforced, so they only
appear when crossed. Reasons come in order along the way. The extra time the detours cost is
in the plan metrics, as a whole: it is not split between zones.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

from corridor.domain.events import ReasonKind, RouteChoice, RoutePoint, RouteReason
from corridor.domain.geo import LocalFrame, Position
from corridor.domain.planner.layers import LINK
from corridor.domain.safety import LevelCells, position_at
from corridor.domain.zones import ThreatShape

LINE_STEP_M = 25.0
"""Sampling of the straight line from launch to target."""
ROUTE_STEP_S = 2.0
"""Sampling of the route in time."""
TOLERANCE_S = 2.0
"""Slack when matching a crossing with a cut window, as the safety rule."""

Window = tuple[str, float, float]
"""``(jammer, start_s, end_s)``: a friendly jammer off."""


@dataclass
class _Seen:
    kind: ReasonKind
    along_m: float
    on_line: bool = False
    under: bool = False
    times: list[float] = field(default_factory=list)


def route_reasons(
    route: Sequence[RoutePoint],
    launch: Position,
    target: Position,
    shapes: Sequence[ThreatShape],
    windows: Sequence[Window] = (),
    cells: LevelCells | None = None,
) -> list[RouteReason]:
    """Zones that shape ``route``, in order along the way. ``shapes`` are in the frame of
    ``LocalFrame(launch)``; ``windows``: the cuts the route may use."""
    if len(route) < 2:
        return []
    frame = LocalFrame(launch)
    tx, ty = frame.to_xy(target)
    length = math.hypot(tx, ty)
    ux, uy = (tx / length, ty / length) if length > 0 else (0.0, 0.0)
    kinds: dict[str, ReasonKind] = {z.threat: z.kind for z in shapes}
    kinds[LINK] = "link"
    seen: dict[str, _Seen] = {}

    def along(position: Position) -> float:
        x, y = frame.to_xy(position)
        return x * ux + y * uy

    def note(threat: str, at: float) -> _Seen:
        if threat not in seen:
            seen[threat] = _Seen(kinds.get(threat, "enemy_jammer"), at)
        return seen[threat]

    # The straight line, at the lowest level in 3D.
    steps = max(1, math.ceil(length / LINE_STEP_M))
    for i in range(steps + 1):
        d = length * i / steps
        position = frame.to_position(ux * d, uy * d)
        for threat in _zones_at(position, frame, shapes, cells, {0}, enforced_only=True):
            note(threat, d).on_line = True

    # The route: where it is inside a zone at its level, and in 3D where it flies over one.
    t, end = route[0].t, route[-1].t
    while t <= end:
        position, action = position_at(route, t)
        if action != "ground":
            levels = cells.levels_at(route, t) if cells is not None else {0}
            for threat in _zones_at(position, frame, shapes, cells, levels, enforced_only=False):
                note(threat, along(position)).times.append(t)
            if cells is not None and 0 not in levels:
                for threat in _zones_at(position, frame, shapes, cells, {0}, enforced_only=True):
                    if threat in seen:
                        seen[threat].under = True
        t += ROUTE_STEP_S

    reasons: list[tuple[float, RouteReason]] = []
    for threat, s in seen.items():
        if s.times:
            enter, exit_ = min(s.times), max(s.times)
            in_cut = s.kind == "friendly_jammer" and any(
                j == threat and a - TOLERANCE_S <= enter and exit_ <= b + TOLERANCE_S
                for j, a, b in windows
            )
            effect = "crossed_in_cut" if in_cut else "crossed"
            reason = RouteReason(
                threat=threat, kind=s.kind, effect=effect, enter_s=enter, exit_s=exit_
            )
        elif s.on_line:
            effect = "climbed_over" if s.under else "avoided"
            reason = RouteReason(threat=threat, kind=s.kind, effect=effect)
        else:
            continue
        reasons.append((s.along_m, reason))
    return [r for _, r in sorted(reasons, key=lambda item: item[0])]


def choice_reasons(
    choice: RouteChoice,
    launch: Position,
    target: Position,
    shapes: Sequence[ThreatShape],
    windows: Sequence[Window] = (),
    cells: LevelCells | None = None,
) -> list[RouteReason]:
    """``route_reasons`` of a route choice, its proposed cuts counting as cuts."""
    proposed = [(c.jammer, c.start_s, c.end_s) for c in choice.cuts]
    return route_reasons(choice.route, launch, target, shapes, [*windows, *proposed], cells)


def _zones_at(
    position: Position,
    frame: LocalFrame,
    shapes: Sequence[ThreatShape],
    cells: LevelCells | None,
    levels: set[int],
    *,
    enforced_only: bool,
) -> list[str]:
    """Zones at ``position`` on ``levels``. Zones to avoid (2D shapes in both modes) only
    unless ``enforced_only``."""
    x, y = frame.to_xy(position)
    if cells is None:
        return [
            z.threat
            for z in shapes
            if not (enforced_only and z.kind == "avoid") and z.contains(x, y)
        ]
    found = cells.jammers(position, levels) + cells.threats(position, levels)
    if not enforced_only:
        found += [z.threat for z in shapes if z.kind == "avoid" and z.contains(x, y)]
    return found
