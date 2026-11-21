"""Flight layers of a mission: the masks of each level over time, and the time to change level.

2D (``Terrain.mode == "2d"``): one layer, the masks of the 2D zones (``timed_masks``), and
route points carry no height.

3D: one layer per flight level of ``Terrain.levels_m``. A cell is forbidden at a level by:

- a jammer's 3D zone (``corridor.domain.zones3d``), widened by one cell all around: zones are
  computed at cell centres, and a cell is forbidden as soon as any part of it may be in a zone,
  as in 2D. A friendly jammer's zone is off during its cut windows (shrunk by the margins);
- an enemy jammer's zone, widened further for its position error by the 2D margin
  e * k / |k - 1| (``corridor.domain.zones``), in whole cells;
- a radar: its 2D disc at every level (masking radars by the terrain is still to do).

Zones to avoid are left out, as in 2D. Climbing or descending between two levels takes their
height difference at ``CLIMB_MPS``.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
from scipy.ndimage import binary_dilation

from corridor.domain.geo import Grid, LocalFrame
from corridor.domain.planner.masks import (
    TimedMasks,
    layered_timed_masks,
    shape_mask,
    timed_masks,
)
from corridor.domain.scenario import EnemyJammer, Mission, Scenario
from corridor.domain.state import Cut
from corridor.domain.zones import forbidden_shapes
from corridor.domain.zones3d import forbidden, jamming_zones

CLIMB_MPS = 3.0
"""Climb and descent speed between levels (open decision: climb speed and its battery cost)."""

Cells = npt.NDArray[np.bool_]
"""``[level, row, col]``."""


@dataclass(frozen=True)
class Layers:
    timed: list[TimedMasks]
    """Lowest level first, same time edges."""
    climb_s: list[float]
    """Time between levels ``i`` and ``i + 1``."""
    altitudes: tuple[float, ...] | None
    """Height of each level above the ground; None in 2D."""


def mission_layers(
    scenario: Scenario,
    mission: Mission,
    grid: Grid,
    windows: list[Cut],
    start_s: float,
    end_s: float,
) -> Layers:
    """``windows``: cuts already shrunk by the margins."""
    if scenario.terrain.mode == "2d":
        zones = forbidden_shapes(scenario, mission)
        timed = timed_masks(grid, zones, LocalFrame(mission.launch), windows, start_s, end_s)
        return Layers([timed], [], None)
    levels = scenario.terrain.levels_m
    friendly, always = forbidden_cells(scenario, mission, grid)
    timed_by_level = layered_timed_masks(always, friendly, windows, start_s, end_s)
    climb = [(b - a) / CLIMB_MPS for a, b in itertools.pairwise(levels)]
    return Layers(timed_by_level, climb, levels)


def forbidden_cells(
    scenario: Scenario, mission: Mission, grid: Grid
) -> tuple[dict[str, Cells], Cells]:
    """Cells each friendly jammer forbids, and those forbidden whatever the cuts."""
    shape = (len(scenario.terrain.levels_m), grid.rows, grid.cols)
    always: Cells = np.zeros(shape, dtype=np.bool_)
    friendly: dict[str, Cells] = {}
    friendly_ids = {j.id for j in scenario.friendly_jammers}
    enemies = {t.id: t for t in scenario.enemy_threats if isinstance(t, EnemyJammer)}
    for jammer, js_db in jamming_zones(scenario, mission, grid).items():
        cells = grow(forbidden(scenario, js_db), 1)
        if jammer in friendly_ids:
            friendly[jammer] = cells
        else:
            margin = error_cells(scenario, mission, enemies[jammer], grid.cell_m)
            always |= grow(cells, margin)
    frame = LocalFrame(mission.launch)
    for zone in forbidden_shapes(scenario, mission):
        if zone.kind == "radar":
            always |= shape_mask(grid, zone, frame)[np.newaxis]
    return friendly, always


def grow(cells: Cells, n: int) -> Cells:
    """``cells`` widened by ``n`` cells in every horizontal direction, at each level."""
    if n <= 0 or not cells.any():
        return cells
    n = min(n, max(cells.shape[1:]))
    structure = np.ones((1, 2 * n + 1, 2 * n + 1), dtype=np.bool_)
    grown: Cells = binary_dilation(cells, structure=structure)
    return grown


def error_cells(scenario: Scenario, mission: Mission, jammer: EnemyJammer, cell_m: float) -> int:
    """Cells to add for the jammer's position error: the 2D margin e * k / |k - 1|."""
    if jammer.position_error_m == 0:
        return 0
    k = math.sqrt(10 ** (scenario.js_threshold_db / 10) * mission.link_eirp_w / jammer.eirp_w)
    if abs(k - 1) < 1e-9:
        return 10**6  # the zone could be anywhere: every cell
    return math.ceil(jammer.position_error_m * k / abs(k - 1) / cell_m)
