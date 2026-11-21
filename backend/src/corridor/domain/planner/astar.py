"""Safe Interval Path Planning (SIPP): A* over (cell, level, safe interval).

A safe interval of a cell at a flight level is a maximal stretch of time during which it is
free. A search state is a cell, a level and one of its safe intervals, and its cost is the
earliest arrival time in it. From a state the drone may leave at any time before the interval
closes, hovering until then, so waiting for a cut is part of the search and needs no time step.
A cell free all along has one interval; with no cut, SIPP is plain A* on the grid.

Moves go to the 8 neighbours at the same level, at cruise speed, or one level up or down in
place, in ``climb_s`` for that pair of levels. A move is safe when the cells it uses are free
for its whole duration (the drone is somewhere between them meanwhile), and for a diagonal,
the two cells whose corner it cuts too. The search starts at the lowest level (or the drone's,
in flight) and ends over the goal at any level: what happens over the target is the pilot's,
and near it the lowest level may be jammed. With a single level (``TimedMasks`` alone) it is
the 2D search.

The heuristic is the octile distance at cruise speed, a lower bound on the time left, waiting
included. So the first time the goal is popped, its arrival is the earliest possible.
"""

from __future__ import annotations

import heapq
import math
from collections.abc import Sequence
from dataclasses import dataclass

from corridor.domain.geo import Cell
from corridor.domain.planner.masks import TimedMasks

NEIGHBOURS = [
    (dc, dr, math.hypot(dc, dr)) for dc in (-1, 0, 1) for dr in (-1, 0, 1) if (dc, dr) != (0, 0)
]

State = tuple[Cell, int, int]
"""A cell, a level and the index of one of its safe intervals there."""


@dataclass(frozen=True)
class Step:
    """The drone reaches ``cell`` at ``level`` at ``arrive_s`` and hovers until ``depart_s``."""

    cell: Cell
    arrive_s: float
    depart_s: float
    level: int = 0


def earliest_path(
    timed: TimedMasks | Sequence[TimedMasks],
    start: Cell,
    goal: Cell,
    start_s: float,
    cell_s: float,
    deadline_s: float = math.inf,
    climb_s: Sequence[float] = (),
    start_level: int = 0,
) -> list[Step] | None:
    """Earliest arrival over ``goal``, at any level, standing in ``start`` at ``start_level``
    at ``start_s``.

    ``timed``: the masks of each level, lowest first, sharing their time edges.
    ``cell_s`` is the time to fly one cell straight; ``climb_s[i]`` the time between levels
    ``i`` and ``i + 1``. None when the goal cannot be reached before the masks end, or by
    ``deadline_s``: the search stops as soon as its lower bound passes it, which saves most of
    the work on a hopeless takeoff time.
    """
    layers = [timed] if isinstance(timed, TimedMasks) else list(timed)
    if len(climb_s) != len(layers) - 1:
        raise ValueError("one climb time per pair of levels")
    cols, rows = layers[0].shape

    def intervals(cell: Cell, level: int) -> list[tuple[float, float]]:
        return layers[level].safe_intervals(cell)

    # Cells free at all times, per level, as nested lists: one lookup for most checks.
    always_free: list[list[list[bool]]] = [(~layer.masks.any(axis=0)).tolist() for layer in layers]
    masks_end = layers[0].edges[-1]

    def inside(cell: Cell) -> bool:
        return 0 <= cell[0] < cols and 0 <= cell[1] < rows

    def free_during(cell: Cell, level: int, t1: float, t2: float) -> bool:
        if not inside(cell):
            return False
        if always_free[level][cell[1]][cell[0]]:
            return t2 <= masks_end
        return any(a <= t1 and t2 <= b for a, b in intervals(cell, level))

    def heuristic(cell: Cell, level: int) -> float:
        dc, dr = abs(cell[0] - goal[0]), abs(cell[1] - goal[1])
        return (max(dc, dr) + (math.sqrt(2) - 1) * min(dc, dr)) * cell_s

    if not (inside(start) and inside(goal)):
        return None
    first_interval = next(
        (i for i, (a, b) in enumerate(intervals(start, start_level)) if a <= start_s < b), None
    )
    if first_interval is None:
        return None
    origin: State = (start, start_level, first_interval)
    arrival = {origin: start_s}
    parent: dict[State, tuple[State, float]] = {}
    closed: set[State] = set()
    heap = [(start_s + heuristic(start, start_level), start_s, origin)]

    def push(
        state: State,
        t: float,
        closes: float,
        child_cell: Cell,
        child_level: int,
        duration: float,
        corners: list[Cell],
    ) -> None:
        """Every safe interval of the child reachable from ``state``, left at ``t`` or later
        and before ``closes``."""
        for j, (opens, ends) in enumerate(intervals(child_cell, child_level)):
            depart = max(t, opens)
            arrive = depart + duration
            if arrive > closes:
                return  # later intervals open later still
            if arrive > ends:
                continue
            if corners and not (
                free_during(corners[0], child_level, depart, arrive)
                and free_during(corners[1], child_level, depart, arrive)
            ):
                continue
            child = (child_cell, child_level, j)
            if child in closed or arrive >= arrival.get(child, math.inf):
                continue
            arrival[child] = arrive
            parent[child] = (state, depart)
            heapq.heappush(heap, (arrive + heuristic(child_cell, child_level), arrive, child))

    while heap:
        bound, t, state = heapq.heappop(heap)
        if bound > deadline_s:
            return None
        if state in closed:
            continue
        closed.add(state)
        cell, level, index = state
        if cell == goal:
            return _unwind(parent, arrival, state)
        closes = intervals(cell, level)[index][1]

        for dc, dr, cells in NEIGHBOURS:
            neighbour = (cell[0] + dc, cell[1] + dr)
            if not inside(neighbour):
                continue
            corners = [(cell[0] + dc, cell[1]), (cell[0], cell[1] + dr)] if dc and dr else []
            push(state, t, closes, neighbour, level, cells * cell_s, corners)
        for other in (level - 1, level + 1):
            if 0 <= other < len(layers):
                push(state, t, closes, cell, other, climb_s[min(level, other)], [])
    return None


def _unwind(
    parent: dict[State, tuple[State, float]], arrival: dict[State, float], goal: State
) -> list[Step]:
    steps = [Step(goal[0], arrival[goal], arrival[goal], goal[1])]
    state = goal
    while state in parent:
        previous, depart = parent[state]
        steps.append(Step(previous[0], arrival[previous], depart, previous[1]))
        state = previous
    return steps[::-1]
