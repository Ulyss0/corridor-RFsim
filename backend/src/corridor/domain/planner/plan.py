"""Planning entry points: from a mission, its scenario and the cuts to timed routes and their
comparison. Each mission is planned on its own, on its own grid."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from corridor.domain.events import PlanMetrics, PlanStatus, RouteAction, RoutePoint
from corridor.domain.geo import Cell, Grid, LocalFrame, Position
from corridor.domain.planner.astar import Step, earliest_path
from corridor.domain.planner.masks import TimedMasks, timed_masks
from corridor.domain.scenario import Mission, Scenario
from corridor.domain.state import Cut
from corridor.domain.zones import ThreatShape, mission_shapes

ENTRY_MARGIN_S = 30.0
"""Enter a friendly zone no sooner than this after its cut starts."""
EXIT_MARGIN_S = 60.0
"""Leave a friendly zone at least this long before its jammer restarts."""
MIN_SAVING_S = 1.0
"""A route using cuts must save more flight time than this to be preferred."""
COARSE_TAKEOFF_STEP_S = 30.0
"""Takeoff times are swept at this step, then refined at 5 s around the best one."""


@dataclass(frozen=True)
class MissionPlan:
    """Content of ``plan.updated``, without the revision the engine adds."""

    status: PlanStatus
    metrics: PlanMetrics
    baseline_route: list[RoutePoint]
    optimised_route: list[RoutePoint]


def plan_mission(
    scenario: Scenario,
    mission: Mission,
    grid: Grid,
    cuts: Sequence[Cut],
    earliest_departure_s: float = 0.0,
) -> MissionPlan | None:
    """Baseline, best route on confirmed cuts, best route on every cut; pick and compare.

    - ``preview``: scheduled cuts give a better route than confirmed ones. Not flyable until
      the operator confirms.
    - ``approved``: confirmed cuts give a better route than the baseline.
    - ``baseline``: no cut helps; the optimised route is the baseline.

    Routes leave no sooner than the mission departure and ``earliest_departure_s`` (a replan
    during the run cannot take off in the past). None when even the baseline has no route.
    """
    if earliest_departure_s > mission.departure_s:
        mission = mission.model_copy(update={"departure_s": earliest_departure_s})
    baseline = plan_route(scenario, mission, grid)
    if baseline is None:
        return None
    status: PlanStatus = "baseline"
    optimised = baseline
    confirmed = [c for c in cuts if c.status == "confirmed"]
    if confirmed:
        approved = plan_route(scenario, mission, grid, confirmed)
        if approved is not None and _saves(approved, optimised):
            status, optimised = "approved", approved
    if len(confirmed) < len(cuts):
        preview = plan_route(scenario, mission, grid, cuts)
        if preview is not None and _saves(preview, optimised):
            status, optimised = "preview", preview
    return MissionPlan(
        status=status,
        metrics=_metrics(mission, baseline, optimised),
        baseline_route=baseline,
        optimised_route=optimised,
    )


def plan_route(
    scenario: Scenario, mission: Mission, grid: Grid, cuts: Iterable[Cut] = ()
) -> list[RoutePoint] | None:
    """Route with the least flight time found, friendly jammers off during ``cuts`` only.

    No cut gives the baseline. Each cut window first shrinks by the safety margins, so the
    route is safe by construction. Waiting on the ground before takeoff is free, hovering is
    not, so takeoff is a variable: the search runs for takeoff times swept every
    ``COARSE_TAKEOFF_STEP_S``, then every 5 s around the best, and keeps the least flight
    time, then the earliest arrival. A better takeoff narrower than the coarse step can be
    missed. None when no route fits the battery.
    """
    start, goal = grid.cell_of(mission.launch), grid.cell_of(mission.target)
    if start is None or goal is None:
        return None
    windows = [w for w in (_usable(c) for c in cuts) if w is not None]
    zones = mission_shapes(scenario, mission)
    latest = max([mission.departure_s, *(w.end_s for w in windows)])
    timed = timed_masks(
        grid,
        zones,
        LocalFrame(mission.launch),
        windows,
        mission.departure_s,
        latest + mission.battery_max_s,
    )

    def search(takeoffs: Iterable[float], best: list[RoutePoint] | None) -> list[RoutePoint] | None:
        for takeoff in takeoffs:
            limit_s = flight_s(best) if best else mission.battery_max_s
            route = _search(grid, mission, timed, start, goal, takeoff, takeoff + limit_s)
            if route is None or flight_s(route) > mission.battery_max_s:
                continue
            if best is None or (flight_s(route), route[-1].t) < (flight_s(best), best[-1].t):
                best = route
        return best

    best = search([mission.departure_s], None)
    if not windows:
        return best
    # A route that uses a window is in its zone after the window opens, so taking off more
    # than ``flight_s(best)`` before the first window cannot beat ``best``. A route that uses
    # no window does no better from a later takeoff. Exact, and it skips hopeless searches
    # when the cut is far ahead.
    earliest_useful = min(w.start_s for w in windows) - (flight_s(best) if best else math.inf)
    first = max(mission.departure_s, earliest_useful)
    latest_takeoff = _latest_useful_takeoff(mission, zones, windows, grid.cell_m)
    best = search(_steps(first, latest_takeoff, COARSE_TAKEOFF_STEP_S), best)
    if best is None:
        return None
    around = _takeoff(best)
    fine = _steps(around - COARSE_TAKEOFF_STEP_S, around + COARSE_TAKEOFF_STEP_S, 5.0)
    return search((t for t in fine if t >= mission.departure_s), best)


def plan_fallback(
    scenario: Scenario, mission: Mission, grid: Grid, position: Position, now_s: float
) -> MissionPlan | None:
    """Safe route for a drone already in the air at ``position``: no cut, take off now.

    The pilot stays at the launch point, so the zones are those of the mission; only the
    route starts elsewhere. Used when the drone can no longer keep its cut windows.
    """
    start, goal = grid.cell_of(position), grid.cell_of(mission.target)
    if start is None or goal is None:
        return None
    timed = timed_masks(
        grid,
        mission_shapes(scenario, mission),
        LocalFrame(mission.launch),
        [],
        now_s,
        now_s + mission.battery_max_s,
    )
    airborne = mission.model_copy(update={"launch": position, "departure_s": now_s})
    route = _search(grid, airborne, timed, start, goal, now_s, now_s + mission.battery_max_s)
    if route is None:
        return None
    return MissionPlan(
        status="fallback",
        metrics=_metrics(mission, route, route),
        baseline_route=route,
        optimised_route=route,
    )


def flight_s(route: list[RoutePoint]) -> float:
    """Time in the air: from takeoff to the target, hover included, ground wait excluded."""
    return route[-1].t - _takeoff(route)


def _takeoff(route: list[RoutePoint]) -> float:
    return next(p.t for p in route if p.action != "ground")


def _usable(cut: Cut) -> Cut | None:
    """The part of ``cut`` the drone may spend in the zone, or None if nothing is left."""
    start_s, end_s = cut.start_s + ENTRY_MARGIN_S, cut.end_s - EXIT_MARGIN_S
    if end_s <= start_s:
        return None
    return cut.model_copy(update={"start_s": start_s, "end_s": end_s})


def _latest_useful_takeoff(
    mission: Mission, zones: list[ThreatShape], windows: list[Cut], cell_m: float
) -> float:
    """Past this, even a straight flight reaches every cut zone after its window closed.

    For a directional jammer the zone lies within its main disc, so the distance to that disc
    is a lower bound: the sweep may go a little too far, never stop too early.
    """
    latest = mission.departure_s
    half_diagonal = cell_m * math.sqrt(2) / 2
    for window in windows:
        for zone in zones:
            if zone.kind != "friendly_jammer" or zone.threat != window.jammer:
                continue
            d = zone.disc
            to_centre = math.hypot(d.x, d.y)  # the mission frame is centred on the launch
            if d.forbidden == "inside":
                to_edge = to_centre - d.radius_m - half_diagonal
            else:
                to_edge = d.radius_m - half_diagonal - to_centre
            latest = max(latest, window.end_s - max(to_edge, 0.0) / mission.cruise_speed_mps)
    return latest


def _steps(first: float, last: float, step: float) -> list[float]:
    """``first``, ``first + step``, ... up to ``last`` included."""
    count = max(math.floor((last - first) / step), 0)
    return [first + i * step for i in range(count + 1)] + ([last] if last > first else [])


def _search(
    grid: Grid,
    mission: Mission,
    timed: TimedMasks,
    start: Cell,
    goal: Cell,
    takeoff: float,
    deadline_s: float,
) -> list[RoutePoint] | None:
    """Route taking off at ``takeoff``; None if it cannot reach the goal cell by
    ``deadline_s``."""
    speed = mission.cruise_speed_mps
    first_leg_s = math.dist(grid.to_xy(mission.launch), _centre(grid, start)) / speed
    steps = earliest_path(
        timed, start, goal, takeoff + first_leg_s, grid.cell_m / speed, deadline_s
    )
    if steps is None:
        return None
    first = steps[0]
    if first.depart_s > first.arrive_s:
        # Hovering over the launch cell: better spent on the ground.
        takeoff += first.depart_s - first.arrive_s
        steps = [Step(first.cell, first.depart_s, first.depart_s), *steps[1:]]
    return _route(grid, mission, steps, takeoff)


def _route(grid: Grid, mission: Mission, steps: list[Step], takeoff: float) -> list[RoutePoint]:
    """Ground wait, launch, the turning and hover points of ``steps``, target."""
    launch = grid.to_xy(mission.launch)
    points: list[tuple[tuple[float, float], float, RouteAction]] = []
    if takeoff > mission.departure_s:
        points.append((launch, mission.departure_s, "ground"))
    points.append((launch, takeoff, "fly"))
    for i, step in enumerate(steps):
        hovers = step.depart_s > step.arrive_s
        ends = i in (0, len(steps) - 1)
        if not (hovers or ends or _turns(steps[i - 1].cell, step.cell, steps[i + 1].cell)):
            continue
        centre = _centre(grid, step.cell)
        if hovers:
            points.append((centre, step.arrive_s, "hover"))
            points.append((centre, step.depart_s, "fly"))
        else:
            points.append((centre, step.arrive_s, "fly"))
    target = grid.to_xy(mission.target)
    last = steps[-1]
    leg_s = math.dist(_centre(grid, last.cell), target) / mission.cruise_speed_mps
    points.append((target, last.arrive_s + leg_s, "fly"))

    route: list[RoutePoint] = []
    previous: tuple[tuple[float, float], float] | None = None
    for xy, t, action in points:
        position = grid.to_position(*xy)
        point = RoutePoint(lat=position.lat, lon=position.lon, t=t, action=action)
        if previous == (xy, t):
            route[-1] = point  # launch or target exactly on a cell centre
        else:
            route.append(point)
        previous = (xy, t)
    return route


def _saves(candidate: list[RoutePoint], reference: list[RoutePoint]) -> bool:
    return flight_s(candidate) < flight_s(reference) - MIN_SAVING_S


def _metrics(
    mission: Mission, baseline: list[RoutePoint], optimised: list[RoutePoint]
) -> PlanMetrics:
    baseline_s, optimised_s = flight_s(baseline), flight_s(optimised)
    return PlanMetrics(
        baseline_battery_used_pct=100 * baseline_s / mission.battery_max_s,
        optimised_battery_used_pct=100 * optimised_s / mission.battery_max_s,
        saved_pct=100 * (baseline_s - optimised_s) / baseline_s,
        saved_s=baseline_s - optimised_s,
    )


def _turns(before: Cell, here: Cell, after: Cell) -> bool:
    return (here[0] - before[0], here[1] - before[1]) != (after[0] - here[0], after[1] - here[1])


def _centre(grid: Grid, cell: Cell) -> tuple[float, float]:
    return (cell[0] + 0.5) * grid.cell_m, (cell[1] + 0.5) * grid.cell_m
