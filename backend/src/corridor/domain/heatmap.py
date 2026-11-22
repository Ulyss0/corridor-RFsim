"""J/S heatmap of a mission, on its planning grid: the map layer the planner goes by.

The values and the denied cells come from the planner's own inputs, so the map and the planner
cannot disagree:

- 3D: J/S per jammer and per level from ``corridor.domain.zones3d.jamming_zones``; denied cells
  from ``corridor.domain.planner.layers.cells_by_threat`` (widened by one cell, enemy position
  margins included).
- 2D: J/S on flat ground at the top level (the free-space values the 2D discs come from); denied
  cells from the 2D zones (``corridor.domain.planner.masks.shape_mask``), as ``timed_masks``.

A friendly jammer whose cut is running (``off``) adds nothing. Radars are a mask of their own,
never mixed into J/S: a pilot may choose to fly through radars (``Mission.cross_radars``), never
through a jammer. They are shown even then.

The image is smoothed for display: J/S is interpolated between cell centres and the denied
cells are traced with rounded edges, so the outline follows the planner's cells without their
100 m steps. Denied cells reach about one cell past the J/S threshold: the planner forbids a
cell as soon as any part of it may be in a zone.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
from scipy.ndimage import gaussian_filter, zoom

from corridor.domain.geo import Grid, LocalFrame
from corridor.domain.planner.layers import cells_by_threat
from corridor.domain.planner.masks import shape_mask
from corridor.domain.rf.coverage import encode_png_rgba
from corridor.domain.scenario import Mission, Scenario, Terrain
from corridor.domain.zones import forbidden_shapes, mission_shapes
from corridor.domain.zones3d import jamming_zones

Cells = npt.NDArray[np.bool_]
"""``[level, row, col]``, row 0 is the southern row."""
Field = npt.NDArray[np.float32]
"""``[level, row, col]``, row 0 is the southern row."""

UPSAMPLE = 4
"""Image pixels per cell side."""
CAUTION_DB = 6.0
"""J/S this far below the threshold is shown as caution: the link holds, with little margin."""

SAFE_RGBA = (0x20, 0xC9, 0x97, 70)
CAUTION_RGBA = (0xFA, 0xB0, 0x05, 130)
DENIED_RGBA = (0xE0, 0x31, 0x31, 170)
RADAR_RGBA = (0xFD, 0x7E, 0x14, 170)


@dataclass(frozen=True)
class MissionHeatmap:
    js_db: Field
    """J/S of the strongest jammer still on, dB; ``-inf`` where no jammer reaches."""
    denied: Cells
    """Cells the planner forbids for the jammers still on."""
    radar: Cells
    """Cells a radar covers (the planner avoids them unless the pilot chose to cross)."""


def mission_heatmap(
    scenario: Scenario, mission: Mission, grid: Grid, off: frozenset[str] = frozenset()
) -> MissionHeatmap:
    """``off``: friendly jammers whose cut is running."""
    if scenario.terrain.mode == "3d":
        js_scenario = scenario
        denied = _denied_3d(scenario, mission, grid, off)
    else:
        top = scenario.terrain.levels_m[-1]
        js_scenario = scenario.model_copy(update={"terrain": Terrain(levels_m=(top,))})
        denied = _denied_2d(scenario, mission, grid, off)
    levels = denied.shape[0]
    js = np.full(denied.shape, -np.inf, dtype=np.float32)
    for jammer, field in jamming_zones(js_scenario, mission, grid).items():
        if jammer not in off:
            js = np.maximum(js, field)
    frame = LocalFrame(mission.launch)
    radar = np.zeros((grid.rows, grid.cols), dtype=np.bool_)
    for shape in mission_shapes(scenario, mission):
        if shape.kind == "radar":
            radar |= shape_mask(grid, shape, frame)
    return MissionHeatmap(js, denied, np.repeat(radar[np.newaxis], levels, axis=0))


def _denied_3d(scenario: Scenario, mission: Mission, grid: Grid, off: frozenset[str]) -> Cells:
    shape = (len(scenario.terrain.levels_m), grid.rows, grid.cols)
    denied = np.zeros(shape, dtype=np.bool_)
    for threat, (kind, cells) in cells_by_threat(scenario, mission, grid).items():
        if kind != "radar" and threat not in off:
            denied |= cells
    return denied


def _denied_2d(scenario: Scenario, mission: Mission, grid: Grid, off: frozenset[str]) -> Cells:
    frame = LocalFrame(mission.launch)
    denied = np.zeros((grid.rows, grid.cols), dtype=np.bool_)
    for shape in forbidden_shapes(scenario, mission):
        if shape.kind in ("friendly_jammer", "enemy_jammer") and shape.threat not in off:
            denied |= shape_mask(grid, shape, frame)
    return denied[np.newaxis]


def heatmap_rgba(heatmap: MissionHeatmap, level: int, threshold_db: float) -> npt.NDArray[np.uint8]:
    """One level as RGBA pixels, ``UPSAMPLE`` per cell side, north row first (image order)."""
    js = heatmap.js_db[level]
    # Interpolating -inf gives NaN: far below any class boundary is as good.
    js = np.where(np.isfinite(js), js, threshold_db - 100.0)
    smooth_js = zoom(js, UPSAMPLE, order=1, mode="nearest", grid_mode=True)
    denied = _rounded(heatmap.denied[level])
    radar = _rounded(heatmap.radar[level])
    rgba = np.empty((*smooth_js.shape, 4), dtype=np.uint8)
    rgba[...] = SAFE_RGBA
    rgba[smooth_js >= threshold_db - CAUTION_DB] = CAUTION_RGBA
    rgba[radar] = RADAR_RGBA
    rgba[denied] = DENIED_RGBA  # a jammer is never crossed, a radar may be
    return np.ascontiguousarray(rgba[::-1])


def heatmap_png(heatmap: MissionHeatmap, level: int, threshold_db: float) -> bytes:
    return encode_png_rgba(heatmap_rgba(heatmap, level, threshold_db))


def _rounded(cells: npt.NDArray[np.bool_]) -> npt.NDArray[np.bool_]:
    """``cells`` at image resolution with rounded edges: each cell stays mostly covered, the
    corners of a staircase are cut."""
    pixels = np.kron(cells, np.ones((UPSAMPLE, UPSAMPLE))).astype(np.float32)
    blurred = gaussian_filter(pixels, sigma=UPSAMPLE / 2, mode="nearest")
    rounded: npt.NDArray[np.bool_] = blurred >= 0.5
    return rounded
