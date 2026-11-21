"""Route smoothing: only the points that matter, never into a zone.

SIPP moves between cell centres in 8 directions, so a route across open ground is a staircase
with a turn every few cells. Smoothing pulls the string: from each kept point it jumps to the
farthest later point it can reach in a straight line at cruise speed, through cells that are
free the whole time the drone is in them. The test uses the masks of the search, so the same
margins: a cell is free only if no part of it may touch a zone. A corner the line only touches
counts too, as in the search, which never cuts a forbidden corner.

A straight line is never longer than the grid path it replaces, so every point is reached no
later than planned:

- A hold (hover) is where the drone waits for a cut: it is never skipped and its departure
  keeps its time, so the crossing after it keeps its window. Arriving earlier makes the hover
  longer, which needs its cell free that much longer.
- Before the first hold the take-off is then pushed back by the time gained, if the shortened
  leg is still clear at the later times: waiting on the ground is free, hovering is not.
- If no later point can be reached in a straight line at the earlier time (a zone that is not
  open yet), the drone waits where it is until its planned departure, then goes on from the
  planned schedule, which is safe by construction. The take-off delay above then turns that
  wait into ground time when it can. If even waiting is not clear, the route stays as planned.

In 3D, lines are pulled within one flight level, on that level's masks. A climb or descent
stays as planned, like a hold: the point where it starts keeps its departure time.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace

from corridor.domain.events import RouteAction, RoutePoint
from corridor.domain.geo import Cell, Grid, Position
from corridor.domain.planner.masks import TimedMasks

EPS_M = 1e-6
"""Slack on cell edges: a line within this of an edge touches the cell on the other side."""

EPS_S = 1e-6
"""Times this close are equal (distances go through map projections)."""

XY = tuple[float, float]


@dataclass(frozen=True)
class _Node:
    """A point of the route: reached at ``arrive_s``, left at ``depart_s`` (later for a hold)."""

    xy: XY
    arrive_s: float
    depart_s: float
    level: int = 0
    fixed: bool = False
    """Its departure time cannot move: a hold, or the start of a climb or descent."""

    @property
    def holds(self) -> bool:
        return self.depart_s > self.arrive_s


def smooth_route(
    route: list[RoutePoint],
    grid: Grid,
    timed: TimedMasks | Sequence[TimedMasks],
    speed_mps: float,
    *,
    delay_takeoff: bool = True,
    altitudes: Sequence[float] | None = None,
) -> list[RoutePoint]:
    """``route`` with the fewest points the masks allow; the same route if nothing is gained.

    ``route`` is a planner route: an optional ground wait, the take-off point, then flown
    points, each hover as a ``hover`` point and a ``fly`` point at the same place, the target
    last. ``delay_takeoff`` is False for a drone already in the air. In 3D, ``timed`` has the
    masks of each level and ``altitudes`` their heights, which the route points carry.
    """
    layers = [timed] if isinstance(timed, TimedMasks) else list(timed)
    ground = route[0] if route[0].action == "ground" else None
    nodes = _nodes(route[1:] if ground else route, grid, altitudes)
    path = _Path(grid, layers, speed_mps)
    smoothed = path.pull(nodes)
    if smoothed is None:
        return route
    if delay_takeoff:
        smoothed = path.delay_takeoff(smoothed)
    if _same(smoothed, nodes):
        return route
    start_s = ground.t if ground else nodes[0].arrive_s
    return _points(smoothed, grid, start_s, altitudes)


def _same(a: list[_Node], b: list[_Node]) -> bool:
    """Same points, and times within ``EPS_S``: nothing gained, keep the planner's route."""
    return len(a) == len(b) and all(
        m.xy == n.xy
        and m.level == n.level
        and abs(m.arrive_s - n.arrive_s) < EPS_S
        and abs(m.depart_s - n.depart_s) < EPS_S
        for m, n in zip(a, b, strict=True)
    )


def _nodes(points: list[RoutePoint], grid: Grid, altitudes: Sequence[float] | None) -> list[_Node]:
    def level(p: RoutePoint) -> int:
        return 0 if altitudes is None or p.alt_m is None else list(altitudes).index(p.alt_m)

    nodes: list[_Node] = []
    i = 0
    while i < len(points):
        p = points[i]
        xy = grid.to_xy(Position(lat=p.lat, lon=p.lon))
        if p.action == "hover" and i + 1 < len(points):
            nodes.append(_Node(xy, p.t, points[i + 1].t, level(p)))
            i += 2
        else:
            nodes.append(_Node(xy, p.t, p.t, level(p)))
            i += 1
    climbs = {k for k in range(len(nodes) - 1) if nodes[k + 1].level != nodes[k].level}
    return [replace(n, fixed=n.holds or k in climbs) for k, n in enumerate(nodes)]


def _points(
    nodes: list[_Node], grid: Grid, start_s: float, altitudes: Sequence[float] | None
) -> list[RoutePoint]:
    """Back to route points, with a ground wait from ``start_s`` if the take-off is later."""

    def point(node: _Node, t: float, action: RouteAction, on_ground: bool = False) -> RoutePoint:
        p = grid.to_position(*node.xy)
        alt = None if altitudes is None else 0.0 if on_ground else altitudes[node.level]
        return RoutePoint(lat=p.lat, lon=p.lon, t=t, action=action, alt_m=alt)

    launch = nodes[0]
    points = [point(launch, start_s, "ground", True)] if launch.depart_s > start_s else []
    points.append(point(launch, launch.depart_s, "fly"))
    for node in nodes[1:]:
        if node.holds:
            points.append(point(node, node.arrive_s, "hover"))
        points.append(point(node, node.depart_s, "fly"))
    return points


class _Path:
    def __init__(self, grid: Grid, layers: list[TimedMasks], speed_mps: float) -> None:
        self.grid, self.layers, self.speed = grid, layers, speed_mps

    def pull(self, nodes: list[_Node]) -> list[_Node] | None:
        """String pulling between fixed points; None if the route has to stay as planned."""
        out = [nodes[0]]
        i = 0
        while i < len(nodes) - 1:
            here = out[-1]
            if nodes[i + 1].level != nodes[i].level:
                out.append(nodes[i + 1])  # a climb or descent, as planned from a fixed point
                i += 1
                continue
            stop = next((k for k in range(i + 1, len(nodes)) if nodes[k].fixed), len(nodes) - 1)
            reached = next(
                ((j, n) for j in range(stop, i, -1) if (n := self.reach(here, nodes[j]))), None
            )
            if reached is not None:
                i, there = reached
                out.append(there)
                continue
            planned = nodes[i].depart_s
            if here.depart_s >= planned or not self.free(
                self.cell(here.xy), here.arrive_s, planned, here.level
            ):
                return None
            out[-1] = replace(here, depart_s=planned, fixed=True)  # wait, then follow the plan
        return out

    def reach(self, here: _Node, there: _Node) -> _Node | None:
        """``there`` reached straight from ``here`` at cruise speed, or None if not clear.

        A hold keeps its departure time; its cell must stay free until then.
        """
        arrive = here.depart_s + math.dist(here.xy, there.xy) / self.speed
        if not self.clear(here.xy, there.xy, here.depart_s, arrive, here.level):
            return None
        depart = there.depart_s if there.fixed else arrive
        if depart > arrive and not self.free(self.cell(there.xy), arrive, depart, there.level):
            return None
        return replace(there, arrive_s=arrive, depart_s=depart)

    def delay_takeoff(self, nodes: list[_Node]) -> list[_Node]:
        """Take off later, so as to hover less before the first hold, if still clear."""
        hold = next((k for k, n in enumerate(nodes) if k > 0 and n.fixed), None)
        if hold is None:
            return nodes
        slack = nodes[hold].depart_s - nodes[hold].arrive_s
        if slack <= 0:
            return nodes
        for delay in (slack, slack / 2):
            later = [_shifted(n, delay) for n in nodes[:hold]]
            legs_clear = all(
                self.clear(a.xy, b.xy, a.depart_s, b.arrive_s, a.level)
                for a, b in itertools.pairwise(later)
            )
            reached = self.reach(later[-1], nodes[hold]) if legs_clear else None
            if reached is not None:
                return [*later, reached, *nodes[hold + 1 :]]
        return nodes

    def cell(self, xy: XY) -> Cell:
        cs = self.grid.cell_m
        return math.floor(xy[0] / cs), math.floor(xy[1] / cs)

    def free(self, cell: Cell, t1: float, t2: float, level: int = 0) -> bool:
        timed = self.layers[level]
        cols, rows = timed.shape
        if not (0 <= cell[0] < cols and 0 <= cell[1] < rows):
            return False
        return any(a <= t1 and t2 <= b for a, b in timed.safe_intervals(cell))

    def clear(self, p: XY, q: XY, t1: float, t2: float, level: int = 0) -> bool:
        """True if every cell the segment p -> q touches at ``level`` is free while the drone
        is in it, flying from ``p`` at ``t1`` to ``q`` at ``t2``."""
        for cell, s1, s2 in self.touched(p, q):
            if not self.free(cell, t1 + s1 * (t2 - t1), t1 + s2 * (t2 - t1), level):
                return False
        return True

    def touched(self, p: XY, q: XY) -> Iterator[tuple[Cell, float, float]]:
        """Cells whose closed square the segment touches, with the stretch of the segment
        inside each, as fractions of its length."""
        cs = self.grid.cell_m
        (x1, y1), (x2, y2) = p, q
        c_lo, c_hi = _span(min(x1, x2), max(x1, x2), cs)
        r_lo, r_hi = _span(min(y1, y2), max(y1, y2), cs)
        for c in range(c_lo, c_hi + 1):
            for r in range(r_lo, r_hi + 1):
                stretch = _clip(p, q, c * cs, r * cs, (c + 1) * cs, (r + 1) * cs)
                if stretch is not None:
                    yield (c, r), *stretch


def _shifted(node: _Node, delay_s: float) -> _Node:
    return replace(node, arrive_s=node.arrive_s + delay_s, depart_s=node.depart_s + delay_s)


def _span(lo: float, hi: float, cs: float) -> tuple[int, int]:
    """Indices of the closed cells of size ``cs`` that [lo, hi] touches."""
    return math.ceil((lo - EPS_M) / cs) - 1, math.floor((hi + EPS_M) / cs)


def _clip(p: XY, q: XY, x0: float, y0: float, x1: float, y1: float) -> tuple[float, float] | None:
    """Fractions of p -> q inside the box (edges included, widened by ``EPS_M``)."""
    s_in, s_out = 0.0, 1.0
    for start, delta, lo, hi in ((p[0], q[0] - p[0], x0, x1), (p[1], q[1] - p[1], y0, y1)):
        lo, hi = lo - EPS_M, hi + EPS_M
        if delta == 0:
            if not lo <= start <= hi:
                return None
            continue
        a, b = (lo - start) / delta, (hi - start) / delta
        s_in, s_out = max(s_in, min(a, b)), min(s_out, max(a, b))
        if s_in > s_out:
            return None
    return s_in, s_out
