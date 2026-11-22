"""Zones a mission may not enter, the planning grid around them, and nominal footprints.

Jammer zones come from the J/S ratio at the drone, in free space, with EIRP taken toward the
drone and the jammer on the link band:

    J/S = (Pj / dj^2) / (Ps / ds^2)

Pj is the jammer EIRP, Ps the pilot link EIRP, dj and ds the distances from the drone to the
jammer and to the pilot. The link is lost where J/S >= t, t = 10^(threshold_db / 10), that is
where ds / dj >= k with k = sqrt(t * Ps / Pj). The boundary ds / dj = k is a circle of
Apollonius:

- k > 1: a disc around the jammer, pushed away from the pilot. Forbidden inside.
- k < 1: the jammer wins everywhere but a bubble around the pilot. Forbidden outside.

A directional jammer (``Antenna``) has two EIRPs: the main beam one inside a horizontal sector
with a hard edge, the sidelobe one elsewhere. Its zone is the main disc inside the beam and the
sidelobe disc outside it. Both discs contain the jammer, so the zone is star-shaped around the
jammer. Only k > 1 is supported: with k < 1 the zone would be an unbounded wedge, which a ring
cannot describe.

The pilot stands at the launch point, so the zones are fixed for a mission, and differ from one
mission to the next. Whether a friendly jammer zone is active at a given time depends on its
cuts, not on this module.

Enemy positions are estimates, within ``position_error_m`` (e). The zone of an enemy threat
covers its zone for every position that close, so the route stays clear wherever the threat
really is. A radar disc grows by e. A J/S disc moves by k^2 / |k^2 - 1| times the jammer
displacement and its radius changes by at most k / |k^2 - 1| times it, so every possible zone
lies within the estimated disc grown by e * k / |k - 1|; a bubble (k < 1) shrinks by as much.
For a disc, this is exact on the side away from the pilot and a little generous on the pilot
side.

A threat concerns a mission when its zone touches the mission planning grid. The grid covers
launch and target, then grows to cover the concerned threats too, until nothing changes
(``mission_constraints``).

The command view sees no mission: it gets a nominal footprint per threat (``nominal_zones``),
where a drone flying ``reference_link.range_m`` from its pilot would lose a link of
``reference_link.eirp_w``. That is J/S >= t at a distance from the jammer below
``range_m * sqrt(Pj / (t * Ps))``: a disc centred on the jammer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from pydantic import Field

from corridor.domain.geo import Grid, LocalFrame, Position
from corridor.domain.model import Model
from corridor.domain.scenario import (
    Antenna,
    AvoidZone,
    EnemyJammer,
    EnemyRadar,
    Mission,
    Scenario,
)

RING_VERTICES = 64
"""Vertices of a full circle; a directional zone uses as many across its main beam."""
GRID_MARGIN_M = 3000.0
"""Room around the mission and the threats that concern it, so detours fit inside the grid.
Must exceed the largest radar radius plus its position error, or the grid edge closes detours
the jury could rightly ask about."""
MAX_ZONE_RADIUS_M = 100_000.0
"""Beyond this, k is so close to 1 that the boundary is nearly a straight line."""

ZoneKind = Literal["friendly_jammer", "enemy_jammer", "radar", "avoid"]
ForbiddenSide = Literal["inside", "outside"]


class Zone(Model):
    threat: str = Field(description="Id of the jammer or radar.")
    kind: ZoneKind
    forbidden: ForbiddenSide = Field(description="Side of the ring the mission may not enter.")
    ring: list[Position] = Field(description="Closed boundary; the first vertex is not repeated.")


@dataclass(frozen=True)
class Disc:
    """Circle in a local frame, metres."""

    x: float
    y: float
    radius_m: float
    forbidden: ForbiddenSide


@dataclass(frozen=True)
class Beam:
    """Horizontal sector of a directional jammer, in a local frame.

    ``axis_rad`` is a mathematical angle: counter-clockwise from east.
    """

    x: float
    y: float
    axis_rad: float
    half_width_rad: float

    def contains(self, x: float, y: float) -> bool:
        """True if (x, y) lies inside the beam, edges included."""
        return angle_off_axis(x - self.x, y - self.y, self.axis_rad) <= self.half_width_rad

    def edges(self) -> tuple[float, float]:
        """Directions of the two edge rays, mathematical angles."""
        return self.axis_rad - self.half_width_rad, self.axis_rad + self.half_width_rad


def azimuth_to_angle(azimuth_deg: float) -> float:
    """Azimuth (degrees clockwise from north) to a mathematical angle (radians from east)."""
    return math.radians(90.0 - azimuth_deg)


def angle_off_axis(dx: float, dy: float, axis_rad: float) -> float:
    """Absolute angle between the direction (dx, dy) and ``axis_rad``, in [0, pi]."""
    a = math.atan2(dy, dx) - axis_rad
    return abs(math.atan2(math.sin(a), math.cos(a)))


def js_disc(
    pilot: tuple[float, float],
    jammer: tuple[float, float],
    link_eirp_w: float,
    jammer_eirp_w: float,
    threshold_db: float,
    position_error_m: float = 0.0,
) -> Disc:
    """Boundary of J/S >= threshold, as a circle of Apollonius.

    With ``position_error_m``, the jammer may be anywhere that close to ``jammer``: the disc
    covers every possible zone (forbidden inside), or only the area free from all of them
    (forbidden outside).
    """
    (px, py), (jx, jy) = pilot, jammer
    distance = math.hypot(jx - px, jy - py)
    if distance < 1.0:
        raise ValueError("jammer at the pilot position")
    k2 = 10 ** (threshold_db / 10) * link_eirp_w / jammer_eirp_w
    if k2 == 1.0 or math.sqrt(k2) * distance / abs(k2 - 1) > MAX_ZONE_RADIUS_M:
        raise ValueError("J/S boundary is nearly a straight line: adjust the powers")
    k = math.sqrt(k2)
    radius = k * distance / abs(k2 - 1)
    margin = position_error_m * k / abs(k - 1)
    if k2 > 1:
        radius += margin
    else:
        radius -= margin
        if radius <= 0:
            raise ValueError("jammer position too uncertain: the link may be lost everywhere")
    return Disc(
        x=(k2 * jx - px) / (k2 - 1),
        y=(k2 * jy - py) / (k2 - 1),
        radius_m=radius,
        forbidden="inside" if k2 > 1 else "outside",
    )


@dataclass(frozen=True)
class ThreatShape:
    """Zone of one threat in the mission frame (``LocalFrame(mission.launch)``).

    Without ``beam``, ``disc`` is the zone. With ``beam``, the zone is ``disc`` inside the beam
    and ``sidelobe`` outside it; both are then forbidden inside and contain the jammer.
    """

    threat: str
    kind: ZoneKind
    disc: Disc
    beam: Beam | None = None
    sidelobe: Disc | None = None

    def contains(self, x: float, y: float) -> bool:
        """True if (x, y) lies on the forbidden side of the zone (exact, no cell margin)."""
        d = self.disc
        if self.beam is not None and not self.beam.contains(x, y):
            if self.sidelobe is None:
                return False
            d = self.sidelobe
        inside = math.hypot(x - d.x, y - d.y) < d.radius_m
        return inside == (d.forbidden == "inside")


class MissionConstraints(Model):
    """What constrains one mission: its grid, its zones, and the threats behind them."""

    mission: str
    grid: Grid = Field(description="Planning grid of the mission.")
    zones: list[Zone] = Field(description="Zones of the concerned threats, for this pilot.")
    jammers: list[str] = Field(description="Friendly jammers that concern the mission.")
    threats: list[str] = Field(description="Enemy threats that concern the mission.")


def mission_shapes(scenario: Scenario, mission: Mission) -> list[ThreatShape]:
    """Zones of the jammers on the mission band and of every radar, in the mission frame.

    Includes threats far from the mission; ``mission_constraints`` keeps those that touch its
    grid. A zone outside the grid forbids no cell, so the planner may use them all. Enemy zones
    include the position error.

    A pilot standing inside the full zone of an omnidirectional friendly jammer (its reference
    footprint, ``nominal_zones``) starts the mission in the jamming: that full zone is then the
    mission's zone for this jammer, and the drone leaves it only during a cut.
    """
    frame = LocalFrame(mission.launch)

    def disc(pos: Position, eirp_w: float, position_error_m: float = 0.0) -> Disc:
        return js_disc(
            (0.0, 0.0),
            frame.to_xy(pos),
            mission.link_eirp_w,
            eirp_w,
            scenario.js_threshold_db,
            position_error_m,
        )

    zones: list[ThreatShape] = []
    for j in scenario.friendly_jammers:
        if mission.band not in j.bands:
            continue
        main = disc(j.pos, j.eirp_w)
        if j.antenna is None:
            x, y = frame.to_xy(j.pos)
            full = nominal_radius(scenario, j.eirp_w)
            if math.hypot(x, y) < full:
                main = Disc(x, y, full, "inside")
            zones.append(ThreatShape(j.id, "friendly_jammer", main))
        else:
            sidelobe = disc(j.pos, j.eirp_w * 10 ** (j.antenna.sidelobe_db / 10))
            zones.append(_directional(j.id, frame.to_xy(j.pos), j.antenna, main, sidelobe))
    for threat in scenario.enemy_threats:
        match threat:
            case EnemyJammer() if mission.band in threat.bands:
                main = disc(threat.pos, threat.eirp_w, threat.position_error_m)
                zones.append(ThreatShape(threat.id, "enemy_jammer", main))
            case EnemyRadar():
                zones.append(_radar(threat, frame))
            case AvoidZone():
                zones.append(_avoid(threat, frame))
    return zones


def forbidden_shapes(scenario: Scenario, mission: Mission) -> list[ThreatShape]:
    """``mission_shapes`` the planner may not enter: all but the zones to avoid, and but the
    radars when the pilot chose to fly through them (``Mission.cross_radars``). Jammers are
    always forbidden."""
    return [
        z
        for z in mission_shapes(scenario, mission)
        if z.kind != "avoid" and not (mission.cross_radars and z.kind == "radar")
    ]


def mission_constraints(scenario: Scenario, mission: Mission, cell_m: float) -> MissionConstraints:
    """Grid of the mission and the threats whose zone touches it.

    The grid starts around launch and target with ``GRID_MARGIN_M`` on each side, then also
    covers the position of every concerned threat, so the detours around it fit; repeated
    until the set of concerned threats stops growing. Zones may reach past the grid: the
    planner treats cells outside it as unreachable.
    """
    frame = LocalFrame(mission.launch)
    shapes = mission_shapes(scenario, mission)
    positions = {t.id: t.pos for t in [*scenario.friendly_jammers, *scenario.enemy_threats]}
    concerned: list[ThreatShape] = []
    while True:
        points = [mission.launch, mission.target, *(positions[z.threat] for z in concerned)]
        grid = Grid.covering(points, GRID_MARGIN_M, cell_m)
        touching = [z for z in shapes if _touches(z, _grid_box(grid, frame))]
        if len(touching) == len(concerned):
            break
        concerned = touching
    return MissionConstraints(
        mission=mission.id,
        grid=grid,
        zones=[_zone(z, frame) for z in concerned],
        jammers=[z.threat for z in concerned if z.kind == "friendly_jammer"],
        threats=[z.threat for z in concerned if z.kind != "friendly_jammer"],
    )


def constraints_by_mission(scenario: Scenario, cell_m: float) -> dict[str, MissionConstraints]:
    return {m.id: mission_constraints(scenario, m, cell_m) for m in scenario.missions}


def nominal_radius(scenario: Scenario, eirp_w: float) -> float:
    """Radius of a jammer's full zone, against ``scenario.reference_link``."""
    link = scenario.reference_link
    t = 10 ** (scenario.js_threshold_db / 10)
    return link.range_m * math.sqrt(eirp_w / (t * link.eirp_w))


def starts_inside(scenario: Scenario, mission: Mission) -> list[str]:
    """Omnidirectional friendly jammers on the mission band whose full zone holds the launch."""
    frame = LocalFrame(mission.launch)
    return [
        j.id
        for j in scenario.friendly_jammers
        if mission.band in j.bands
        and j.antenna is None
        and math.hypot(*frame.to_xy(j.pos)) < nominal_radius(scenario, j.eirp_w)
    ]


def nominal_zones(scenario: Scenario) -> list[Zone]:
    """Footprint of every threat against ``scenario.reference_link``, for the command view.

    The footprint is centred on the jammer, so a position error widens it by as much.
    """

    def disc(eirp_w: float, position_error_m: float = 0.0) -> Disc:
        radius = nominal_radius(scenario, eirp_w)
        return Disc(0.0, 0.0, radius + position_error_m, "inside")

    zones: list[Zone] = []
    for j in scenario.friendly_jammers:
        frame = LocalFrame(j.pos)
        if j.antenna is None:
            shape = ThreatShape(j.id, "friendly_jammer", disc(j.eirp_w))
        else:
            sidelobe = disc(j.eirp_w * 10 ** (j.antenna.sidelobe_db / 10))
            shape = _directional(j.id, (0.0, 0.0), j.antenna, disc(j.eirp_w), sidelobe)
        zones.append(_zone(shape, frame))
    for threat in scenario.enemy_threats:
        frame = LocalFrame(threat.pos)
        match threat:
            case EnemyJammer():
                shape = ThreatShape(
                    threat.id, "enemy_jammer", disc(threat.eirp_w, threat.position_error_m)
                )
                zones.append(_zone(shape, frame))
            case EnemyRadar():
                zones.append(_zone(_radar(threat, frame), frame))
            case AvoidZone():
                zones.append(_zone(_avoid(threat, frame), frame))
    return zones


def _radar(radar: EnemyRadar, frame: LocalFrame) -> ThreatShape:
    x, y = frame.to_xy(radar.pos)
    radius = radar.radius_m + radar.position_error_m
    return ThreatShape(radar.id, "radar", Disc(x=x, y=y, radius_m=radius, forbidden="inside"))


def _avoid(zone: AvoidZone, frame: LocalFrame) -> ThreatShape:
    x, y = frame.to_xy(zone.pos)
    disc = Disc(x=x, y=y, radius_m=zone.radius_m, forbidden="inside")
    return ThreatShape(zone.id, "avoid", disc)


def _zone(shape: ThreatShape, frame: LocalFrame) -> Zone:
    return Zone(
        threat=shape.threat,
        kind=shape.kind,
        forbidden=shape.disc.forbidden,
        ring=[frame.to_position(x, y) for x, y in _ring(shape)],
    )


Box = tuple[float, float, float, float]
"""x min, y min, x max, y max in a local frame."""


def _grid_box(grid: Grid, frame: LocalFrame) -> Box:
    """The grid in ``frame``, one cell larger on each side: a zone just past the edge may still
    forbid an edge cell, through the half-diagonal margin of the masks."""
    x, y = frame.to_xy(grid.origin)
    pad = grid.cell_m
    return (x - pad, y - pad, x + grid.cols * grid.cell_m + pad, y + grid.rows * grid.cell_m + pad)


def _touches(shape: ThreatShape, box: Box) -> bool:
    """True if the forbidden side of ``shape`` may overlap ``box``.

    A directional zone lies within the union of its two discs: may say yes too often, never
    too rarely.
    """
    discs = [shape.disc] if shape.sidelobe is None else [shape.disc, shape.sidelobe]
    return any(_disc_touches(d, box) for d in discs)


def _disc_touches(disc: Disc, box: Box) -> bool:
    x0, y0, x1, y1 = box
    if disc.forbidden == "inside":
        nearest_x, nearest_y = min(max(disc.x, x0), x1), min(max(disc.y, y0), y1)
        return math.hypot(nearest_x - disc.x, nearest_y - disc.y) < disc.radius_m
    farthest_x = max(abs(disc.x - x0), abs(disc.x - x1))
    farthest_y = max(abs(disc.y - y0), abs(disc.y - y1))
    return math.hypot(farthest_x, farthest_y) > disc.radius_m


def _directional(
    threat: str, jammer: tuple[float, float], antenna: Antenna, main: Disc, sidelobe: Disc
) -> ThreatShape:
    if main.forbidden != "inside":
        raise ValueError(
            f"{threat}: directional jammer stronger than the link (k < 1) is not supported"
        )
    beam = Beam(
        x=jammer[0],
        y=jammer[1],
        axis_rad=azimuth_to_angle(antenna.azimuth_deg),
        half_width_rad=math.radians(antenna.beamwidth_deg) / 2,
    )
    return ThreatShape(threat, "friendly_jammer", main, beam, sidelobe)


def _ring(zone: ThreatShape) -> list[tuple[float, float]]:
    d, beam = zone.disc, zone.beam
    if beam is None:
        return [
            (
                d.x + d.radius_m * math.cos(2 * math.pi * i / RING_VERTICES),
                d.y + d.radius_m * math.sin(2 * math.pi * i / RING_VERTICES),
            )
            for i in range(RING_VERTICES)
        ]
    # Star-shaped around the jammer: walk the main beam, then the rest of the circle on the
    # sidelobe disc. At each beam edge the boundary steps radially from one disc to the other.
    start, end = beam.edges()
    ring = _arc(beam, d, start, end, RING_VERTICES)
    rest = 2 * math.pi - (end - start)
    if zone.sidelobe is not None and rest > 0:
        steps = max(2, math.ceil(RING_VERTICES * rest / (2 * math.pi)))
        ring += _arc(beam, zone.sidelobe, end, end + rest, steps)
    return ring


def _arc(beam: Beam, disc: Disc, start: float, end: float, steps: int) -> list[tuple[float, float]]:
    """Points where rays from the jammer leave ``disc``, ``steps + 1`` angles from ``start`` to
    ``end`` included."""
    points = []
    for i in range(steps + 1):
        a = start + (end - start) * i / steps
        r = _exit_distance(beam.x, beam.y, math.cos(a), math.sin(a), disc)
        points.append((beam.x + r * math.cos(a), beam.y + r * math.sin(a)))
    return points


def _exit_distance(x: float, y: float, ux: float, uy: float, disc: Disc) -> float:
    """Distance along the unit ray from (x, y), inside ``disc``, to its boundary."""
    wx, wy = x - disc.x, y - disc.y
    along = wx * ux + wy * uy
    return -along + math.sqrt(along**2 - (wx**2 + wy**2 - disc.radius_m**2))
