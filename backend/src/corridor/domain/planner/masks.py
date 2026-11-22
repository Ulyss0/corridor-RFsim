"""Zones rasterised on the planning grid, over time.

A mask is a boolean array indexed ``[row, col]`` (row grows north, as in ``Grid``), True where
the drone may not be. A cell is forbidden as soon as any part of it may lie on the forbidden
side of a boundary: the test runs on the cell centre, with the boundary pushed out by half the
cell diagonal. A route that stays in free cells therefore never grazes a zone. For a directional
jammer, the edges of its beam are pushed out the same way, on both sides.

Cuts are piecewise constant in time, so the masks are too: one mask per segment between cut
boundaries, not one per time step.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Iterable
from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

from corridor.domain.geo import Cell, Grid, LocalFrame
from corridor.domain.state import Cut
from corridor.domain.zones import Beam, Disc, ThreatShape

Mask = npt.NDArray[np.bool_]


@dataclass(frozen=True)
class TimedMasks:
    """Forbidden cells over time: ``masks[k]`` holds on ``[edges[k], edges[k + 1])``.

    ``masks`` has shape (segments, rows, cols). Nothing is known past ``edges[-1]``.
    """

    edges: list[float]
    masks: Mask
    _intervals: dict[Cell, list[tuple[float, float]]] = field(
        default_factory=dict, compare=False, repr=False
    )

    @property
    def shape(self) -> tuple[int, int]:
        """(cols, rows) of the grid."""
        return int(self.masks.shape[2]), int(self.masks.shape[1])

    def safe_intervals(self, cell: Cell) -> list[tuple[float, float]]:
        """Maximal ``(start, end)`` stretches during which ``cell`` is free, in time order.

        Cached: several searches run on the same masks.
        """
        cached = self._intervals.get(cell)
        if cached is not None:
            return cached
        forbidden: list[bool] = self.masks[:, cell[1], cell[0]].tolist()
        runs: list[tuple[float, float]] = []
        for k, blocked in enumerate(forbidden):
            if blocked:
                continue
            if runs and runs[-1][1] == self.edges[k]:
                runs[-1] = (runs[-1][0], self.edges[k + 1])
            else:
                runs.append((self.edges[k], self.edges[k + 1]))
        self._intervals[cell] = runs
        return runs


def disc_mask(grid: Grid, disc: Disc, frame: LocalFrame) -> Mask:
    """Cells that may reach the forbidden side of ``disc``, whose centre is given in ``frame``."""
    xs, ys = _offsets(grid, frame, disc.x, disc.y)
    distance: npt.NDArray[np.float64] = np.hypot(xs, ys)
    half_diagonal = _half_diagonal(grid)
    if disc.forbidden == "inside":
        return distance < disc.radius_m + half_diagonal
    return distance > disc.radius_m - half_diagonal


def shape_mask(grid: Grid, shape: ThreatShape, frame: LocalFrame) -> Mask:
    """Cells that may reach the zone of ``shape``.

    A cell is kept for the main disc if it may reach both that disc and the beam, and for the
    sidelobe disc if it may reach both that disc and the outside of the beam. With relays, a
    cell is kept if it may reach the zone against every connection point. Being near each of
    several sets does not mean being near their intersection, so this may forbid a few more
    cells than needed, never fewer.
    """
    mask = _part_mask(grid, shape, frame)
    for relay in shape.relays:
        mask &= _part_mask(grid, relay, frame)
    return mask


def _part_mask(grid: Grid, shape: ThreatShape, frame: LocalFrame) -> Mask:
    """Cells that may reach the zone of ``shape`` against its own connection point."""
    main = disc_mask(grid, shape.disc, frame)
    if shape.beam is None:
        return main
    in_beam, near_edge = _beam_masks(grid, shape.beam, frame)
    mask = main & (in_beam | near_edge)
    if shape.sidelobe is not None:
        mask |= disc_mask(grid, shape.sidelobe, frame) & (~in_beam | near_edge)
    return mask


def _beam_masks(grid: Grid, beam: Beam, frame: LocalFrame) -> tuple[Mask, Mask]:
    """Cell centres inside the beam, and cells within half a diagonal of one of its edges."""
    xs, ys = _offsets(grid, frame, beam.x, beam.y)
    off_axis = np.arctan2(ys, xs) - beam.axis_rad
    off_axis = np.abs(np.arctan2(np.sin(off_axis), np.cos(off_axis)))
    in_beam: Mask = off_axis <= beam.half_width_rad
    half_diagonal = _half_diagonal(grid)
    near_edge = np.zeros_like(in_beam)
    for edge in beam.edges():
        ex, ey = math.cos(edge), math.sin(edge)
        along = xs * ex + ys * ey
        # Distance to the ray: across it when ahead of the jammer, to the jammer when behind.
        distance = np.where(along >= 0, np.abs(xs * ey - ys * ex), np.hypot(xs, ys))
        near_edge |= distance < half_diagonal
    return in_beam, near_edge


def _offsets(
    grid: Grid, frame: LocalFrame, x: float, y: float
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]]:
    """Cell centres relative to the point (x, y) of ``frame``, broadcast to (rows, cols)."""
    cx, cy = grid.to_xy(frame.to_position(x, y))
    xs = (np.arange(grid.cols) + 0.5) * grid.cell_m - cx
    ys = (np.arange(grid.rows) + 0.5) * grid.cell_m - cy
    return np.broadcast_to(xs[np.newaxis, :], (grid.rows, grid.cols)), np.broadcast_to(
        ys[:, np.newaxis], (grid.rows, grid.cols)
    )


def _half_diagonal(grid: Grid) -> float:
    return grid.cell_m * math.sqrt(2) / 2


def layered_timed_masks(
    always: npt.NDArray[np.bool_],
    friendly: dict[str, npt.NDArray[np.bool_]],
    cuts: Iterable[Cut],
    start_s: float,
    end_s: float,
) -> list[TimedMasks]:
    """Masks of each flight level, all ``[level, row, col]`` arrays: ``always`` forbidden,
    plus each friendly jammer's cells unless it is cut. One ``TimedMasks`` per level, with
    the same time edges."""
    cuts = list(cuts)
    inner = sorted({t for c in cuts for t in (c.start_s, c.end_s) if start_s < t < end_s})
    edges = [start_s, *inner, end_s]
    segments = []
    for a, b in itertools.pairwise(edges):
        middle = (a + b) / 2
        cut_off = {c.jammer for c in cuts if c.start_s <= middle < c.end_s}
        mask = always.copy()
        for jammer, cells in friendly.items():
            if jammer not in cut_off:
                mask |= cells
        segments.append(mask)
    stack = np.stack(segments)  # (segments, levels, rows, cols)
    return [TimedMasks(edges=edges, masks=stack[:, level]) for level in range(stack.shape[1])]


def timed_masks(
    grid: Grid,
    zones: Iterable[ThreatShape],
    frame: LocalFrame,
    cuts: Iterable[Cut],
    start_s: float,
    end_s: float,
) -> TimedMasks:
    """Masks from ``start_s`` to ``end_s``. A friendly jammer zone is off during its cuts."""
    zones, cuts = list(zones), list(cuts)
    zone_masks = [shape_mask(grid, z, frame) for z in zones]
    inner = sorted({t for c in cuts for t in (c.start_s, c.end_s) if start_s < t < end_s})
    edges = [start_s, *inner, end_s]
    masks = []
    for a, b in itertools.pairwise(edges):
        middle = (a + b) / 2
        cut_off = {c.jammer for c in cuts if c.start_s <= middle < c.end_s}
        mask = np.zeros((grid.rows, grid.cols), dtype=np.bool_)
        for zone, zone_mask in zip(zones, zone_masks, strict=True):
            if not (zone.kind == "friendly_jammer" and zone.threat in cut_off):
                mask |= zone_mask
        masks.append(mask)
    return TimedMasks(edges=edges, masks=np.stack(masks))
