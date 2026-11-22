"""Why the route goes where it goes: the zones that shape it and what it does about each."""

import numpy as np

from corridor.domain.events import RouteAction, RoutePoint
from corridor.domain.geo import Grid, LocalFrame, Position
from corridor.domain.reasons import route_reasons
from corridor.domain.safety import LevelCells
from corridor.domain.zones import Disc, ThreatShape, ZoneKind

LAUNCH = Position(lat=48.0, lon=11.0)
FRAME = LocalFrame(LAUNCH)
TARGET = FRAME.to_position(4000, 0)
SPEED = 20.0


def route(*xy: tuple[float, float], alt: float | None = None) -> list[RoutePoint]:
    """Timed at SPEED along the given points (metres east and north of the launch)."""
    points: list[RoutePoint] = []
    t = 0.0
    for i, (x, y) in enumerate(xy):
        if i:
            px, py = xy[i - 1]
            t += float(np.hypot(x - px, y - py)) / SPEED
        p = FRAME.to_position(x, y)
        action: RouteAction = "fly"
        points.append(RoutePoint(lat=p.lat, lon=p.lon, t=t, action=action, alt_m=alt))
    return points


def disc(threat: str, kind: ZoneKind, x: float, y: float, r: float) -> ThreatShape:
    return ThreatShape(threat, kind, Disc(x, y, r, "inside"))


STRAIGHT = route((0, 0), (4000, 0))
AROUND = route((0, 0), (1000, 900), (3000, 900), (4000, 0))


def test_a_zone_on_the_straight_line_that_the_route_goes_around_is_avoided() -> None:
    shapes = [disc("E1", "enemy_jammer", 2000, 0, 500), disc("R1", "radar", 2000, 3000, 400)]
    (reason,) = route_reasons(AROUND, LAUNCH, TARGET, shapes)
    assert (reason.threat, reason.kind, reason.effect) == ("E1", "enemy_jammer", "avoided")
    assert reason.enter_s is None


def test_a_friendly_zone_crossed_within_a_cut_or_not() -> None:
    shapes = [disc("J1", "friendly_jammer", 2000, 0, 500)]
    (crossed,) = route_reasons(STRAIGHT, LAUNCH, TARGET, shapes)
    assert crossed.effect == "crossed"
    assert crossed.enter_s is not None and crossed.exit_s is not None
    assert 1500 / SPEED - 2 <= crossed.enter_s < crossed.exit_s <= 2500 / SPEED + 2
    (in_cut,) = route_reasons(STRAIGHT, LAUNCH, TARGET, shapes, [("J1", 60, 140)])
    assert in_cut.effect == "crossed_in_cut"
    (other_jammer,) = route_reasons(STRAIGHT, LAUNCH, TARGET, shapes, [("J2", 0, 1000)])
    assert other_jammer.effect == "crossed"


def test_zones_to_avoid_only_count_when_crossed_and_come_in_order() -> None:
    shapes = [
        disc("A1", "avoid", 3000, 0, 200),
        disc("A2", "avoid", 2000, 900, 200),  # on the detour, off the straight line
        disc("E1", "enemy_jammer", 1500, 0, 400),
    ]
    assert [r.threat for r in route_reasons(STRAIGHT, LAUNCH, TARGET, shapes[:1])] == ["A1"]
    reasons = route_reasons(AROUND, LAUNCH, TARGET, shapes)
    assert [(r.threat, r.effect) for r in reasons] == [("E1", "avoided"), ("A2", "crossed")]


def test_3d_a_zone_below_the_route_level_is_climbed_over() -> None:
    grid = Grid(origin=FRAME.to_position(-500, -1000), cell_m=100, cols=50, rows=20)
    zone = np.zeros((2, grid.rows, grid.cols), dtype=np.bool_)
    zone[0, :, 22:28] = True  # a band across the way at 30 m only
    cells = LevelCells(grid, (30.0, 60.0), {"J1": zone})
    shapes = [disc("J1", "friendly_jammer", 2000, 0, 300)]
    (low,) = route_reasons(route((0, 0), (4000, 0), alt=30.0), LAUNCH, TARGET, shapes, (), cells)
    assert low.effect == "crossed"
    (high,) = route_reasons(route((0, 0), (4000, 0), alt=60.0), LAUNCH, TARGET, shapes, (), cells)
    assert high.effect == "climbed_over"
