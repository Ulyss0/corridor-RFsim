"""Route smoothing on hand-made masks: fewer points, never through a forbidden cell."""

import itertools
import math

import numpy as np
import pytest

from corridor.domain.events import RouteAction, RoutePoint
from corridor.domain.geo import Cell, Grid, Position
from corridor.domain.planner.masks import Mask, TimedMasks
from corridor.domain.planner.smooth import _Path, smooth_route

CELL_M = 100.0
SPEED = 10.0
FOREVER = 1e6
GRID = Grid(origin=Position(lat=48.0, lon=11.0), cell_m=CELL_M, cols=10, rows=10)

Leg = tuple[float, float, float, RouteAction]
"""x, y, t, action"""


def masks(*segments: tuple[float, list[Cell]]) -> TimedMasks:
    """Forbidden cells per time segment: ``(start_s, cells)``, the last one lasts forever."""
    edges = [start for start, _ in segments] + [FOREVER]
    stack: list[Mask] = []
    for _, cells in segments:
        mask = np.zeros((GRID.rows, GRID.cols), dtype=np.bool_)
        for col, row in cells:
            mask[row, col] = True
        stack.append(mask)
    return TimedMasks(edges=edges, masks=np.stack(stack))


def route(*legs: Leg) -> list[RoutePoint]:
    points = []
    for x, y, t, action in legs:
        p = GRID.to_position(x, y)
        points.append(RoutePoint(lat=p.lat, lon=p.lon, t=t, action=action))
    return points


def flown(xys: list[tuple[float, float]], t0: float) -> list[RoutePoint]:
    """Points flown at ``SPEED`` from ``t0``, through every ``xys``."""
    legs: list[Leg] = [(*xys[0], t0, "fly")]
    t = t0
    for a, b in itertools.pairwise(xys):
        t += math.dist(a, b) / SPEED
        legs.append((*b, t, "fly"))
    return route(*legs)


def xy(points: list[RoutePoint]) -> list[tuple[float, float]]:
    return [
        (round(x, 3), round(y, 3))
        for x, y in (GRID.to_xy(Position(lat=p.lat, lon=p.lon)) for p in points)
    ]


def cells_of(p: tuple[float, float], q: tuple[float, float]) -> set[Cell]:
    return {cell for cell, _, _ in _Path(GRID, [masks((0, []))], SPEED).touched(p, q)}


def test_staircase_on_open_ground_becomes_one_leg() -> None:
    stairs = [(50.0, 50.0), (150.0, 150.0), (150.0, 250.0), (250.0, 350.0), (750.0, 350.0)]
    original = flown(stairs, 0.0)
    smoothed = smooth_route(original, GRID, masks((0, [])), SPEED)
    assert xy(smoothed) == [(50, 50), (750, 350)]
    assert smoothed[-1].t == pytest.approx(math.dist((50, 50), (750, 350)) / SPEED)
    assert smoothed[-1].t < original[-1].t


def test_shortcuts_go_through_the_gap_never_the_wall() -> None:
    wall = [(5, row) for row in range(8)]  # column 5, open in rows 8 and 9
    around = [(50.0, 50.0), (450.0, 450.0), (450.0, 850.0), (950.0, 850.0), (950.0, 50.0)]
    smoothed = smooth_route(flown(around, 0.0), GRID, masks((0, wall)), SPEED)
    points = xy(smoothed)
    assert len(points) < len(around)
    for p, q in itertools.pairwise(points):
        assert not cells_of(p, q) & set(wall), (p, q)


def test_a_corner_the_line_touches_counts() -> None:
    assert cells_of((50, 50), (150, 150)) == {(0, 0), (0, 1), (1, 0), (1, 1)}
    assert cells_of((50, 50), (250, 50)) == {(0, 0), (1, 0), (2, 0)}


def test_hold_keeps_its_departure_and_the_take_off_moves_later() -> None:
    # Column 5 is closed until 200 s; the plan waits at (450, 450) for it.
    closed = masks((0, [(5, row) for row in range(10)]), (200, []))
    original = route(
        (50, 50, 0, "fly"),
        (450, 50, 40, "fly"),
        (450, 450, 80, "hover"),
        (450, 450, 200, "fly"),
        (950, 450, 250, "fly"),
    )
    smoothed = smooth_route(original, GRID, closed, SPEED)
    diagonal_s = math.dist((50, 50), (450, 450)) / SPEED
    assert [p.action for p in smoothed] == ["ground", "fly", "fly", "fly"]
    assert xy(smoothed) == [(50, 50), (50, 50), (450, 450), (950, 450)]
    assert smoothed[1].t == pytest.approx(200 - diagonal_s)  # no hover left
    assert smoothed[2].t == pytest.approx(200)  # leaves when planned
    assert smoothed[-1].t == pytest.approx(250)


def test_waits_instead_of_reaching_a_zone_before_it_opens() -> None:
    # Column 5 opens at 100 s. The straight line to the target would get there at 54 s.
    opens = masks((0, [(5, row) for row in range(10)]), (100, []))
    original = flown([(50, 50), (450, 50), (450, 650), (950, 650)], 0.0)
    smoothed = smooth_route(original, GRID, opens, SPEED)
    assert xy(smoothed) == [(50, 50), (50, 50), (450, 650), (950, 650)]
    assert [p.action for p in smoothed] == ["ground", "fly", "fly", "fly"]
    assert smoothed[2].t == pytest.approx(100)  # leaves when planned, after the opening
    assert smoothed[1].t == pytest.approx(100 - math.dist((50, 50), (450, 650)) / SPEED)
    assert smoothed[-1].t == pytest.approx(original[-1].t)


def test_airborne_route_does_not_move_its_take_off() -> None:
    opens = masks((0, [(5, row) for row in range(10)]), (100, []))
    original = flown([(50, 50), (450, 50), (450, 650), (950, 650)], 0.0)
    smoothed = smooth_route(original, GRID, opens, SPEED, delay_takeoff=False)
    assert smoothed[0].t == 0
    assert [p.action for p in smoothed] == ["fly", "hover", "fly", "fly"]


def test_straight_route_comes_back_unchanged() -> None:
    original = flown([(50, 50), (950, 50)], 0.0)
    assert smooth_route(original, GRID, masks((0, [])), SPEED) is original
