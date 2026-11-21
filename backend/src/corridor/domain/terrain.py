"""Terrain: the ground under the missions and the flight levels above it.

The scenario names an elevation file (``Terrain.dem``: a GeoTIFF in metres above sea level,
inside ``backend/data/``; a name that leads out of it is refused) and the flight levels
(``Terrain.levels_m``, metres above the ground). Without a file the ground is flat at 0 m, and
every zone stays the disc of the 2D model.

Each process reads an elevation file once (the server and the planner worker each keep their
copy). The ground of a planning grid, ``ground_cells``, is cached by file and grid: a mission's
grid only changes when a threat added or removed changes its area, and the new ground is
computed then, on first use.

One value per cell is not enough: a river reads at the bottom of its bed, a roof or a tree
stands above the ground next to it, and a cell centre misses both. So each cell has two grounds,
from the elevation file's own pixels (sampled every ``SAMPLE_M``, less than a pixel, without
interpolation, so a one-pixel roof or river keeps its height), each safe for its own use:

- ``high``, for clearance: the highest sample in the cell and in its neighbours. A flight level
  is measured from it, so a drone at 30 m is 30 m above everything within a cell around it:
  river banks, bridges, roofs, trees (the elevation file is a surface model).
- ``low``, for terrain masking: the lowest sample in the cell. A hill or a building that hides
  the drone from a jammer or a radar is never taken higher than it is, so no shadow is assumed
  where there is none.

Buildings (``Terrain.buildings``, footprints with ``height_m``) are raised into the elevation
file: each footprint, on every pixel it touches, becomes ground + height. The surface model
already holds buildings, smoothed over 30 m pixels; towers come out at their real height. The
ground under a building is the lowest elevation within ``GROUND_WINDOW_PX`` pixels of its
centre, so on a slope a roof may come out a little low.

``with_altitude`` gives each route point its altitude above sea level, and ``profile`` samples
the route every ``PROFILE_STEP_M``: position, ground surface, height and altitude, for the
altitude profile and a 3D view. A level is a height above the clearance ground (``high``), so
between two route points the drone follows that ground at its level: the altitude of the route
is in the profile, not a straight line between its points.
"""

from __future__ import annotations

import functools
import itertools
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt
from rasterio.features import rasterize
from scipy.ndimage import maximum_filter, minimum_filter

from corridor.domain.events import ProfilePoint, RoutePoint
from corridor.domain.geo import EARTH_RADIUS_M, Grid, LocalFrame, Position
from corridor.domain.rf.dem import DemProvider, RasterDem
from corridor.domain.scenario import Terrain

DATA_DIR = Path(__file__).resolve().parents[3] / "data"
"""``backend/data``: where elevation files named by a scenario live."""
PROFILE_STEP_M = 50.0
"""Spacing of the profile samples along a route."""
SAMPLE_M = 10.0
"""Spacing of the ground samples across a cell: a third of a Copernicus pixel or less."""
GROUND_WINDOW_PX = 5
"""Window, in elevation pixels, for the ground under a building (about 150 m)."""
CLEARANCE_CELLS = 1
"""``high`` takes the highest ground this many cells around, for a horizontal margin."""

Elevations = npt.NDArray[np.float64]


class FlatDem:
    """Flat ground at 0 m: no elevation file."""

    def get_elevation(self, pos: Position) -> float:
        return 0.0

    def get_elevations(self, positions: Sequence[Position]) -> Elevations:
        return np.zeros(len(positions), dtype=np.float64)

    def get_elevations_lonlat(
        self, lons: Elevations, lats: Elevations, nearest: bool = False
    ) -> Elevations:
        return np.zeros(np.broadcast(lons, lats).shape, dtype=np.float64)


FLAT = FlatDem()


def dem_path(name: str) -> Path:
    """``name`` inside ``DATA_DIR``; ValueError if it leads anywhere else (absolute path,
    ``..``): a scenario file must not make the server read other files."""
    root = DATA_DIR.resolve()
    path = (root / name).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"elevation file must be inside {root}: {name}")
    return path


def check_terrain(terrain: Terrain) -> None:
    """Raise ValueError if the elevation file is outside ``DATA_DIR`` or missing (at scenario
    load, not mid-run)."""
    for name in (terrain.dem, terrain.buildings):
        if name is not None and not dem_path(name).is_file():
            raise ValueError(f"elevation file not found: {dem_path(name)}")


def ground_model(terrain: Terrain) -> DemProvider:
    return FLAT if terrain.dem is None else _raster(*_paths(terrain))


def _paths(terrain: Terrain) -> tuple[Path, Path | None]:
    assert terrain.dem is not None
    buildings = None if terrain.buildings is None else dem_path(terrain.buildings)
    return dem_path(terrain.dem), buildings


@functools.cache
def _raster(path: Path, buildings: Path | None = None) -> RasterDem:
    raster = RasterDem(path)
    if buildings is not None:
        raster.raise_to(_roofs(raster, buildings))
    return raster


def _roofs(raster: RasterDem, path: Path) -> npt.NDArray[np.float64]:
    """Roof elevation on every pixel a building touches, -inf elsewhere."""
    data = raster._data  # elevation before buildings
    ground = minimum_filter(data, size=GROUND_WINDOW_PX, mode="nearest")
    inverse = ~raster.transform
    rows, cols = data.shape
    shapes = []
    for feature in json.loads(path.read_text(encoding="utf-8"))["features"]:
        ring = feature["geometry"]["coordinates"][0]
        lon = sum(p[0] for p in ring) / len(ring)
        lat = sum(p[1] for p in ring) / len(ring)
        col, row = (int(v) for v in inverse * (lon, lat))
        if not (0 <= row < rows and 0 <= col < cols):
            continue
        roof = float(ground[row, col]) + float(feature["properties"]["height_m"])
        shapes.append((feature["geometry"], roof))
    if not shapes:
        return np.full(data.shape, -np.inf)
    shapes.sort(key=lambda s: s[1])  # the highest roof wins where buildings overlap
    roofs: npt.NDArray[np.float64] = rasterize(
        shapes,
        out_shape=data.shape,
        transform=raster.transform,
        fill=-np.inf,
        all_touched=True,
        dtype="float64",
    )
    return roofs


@dataclass(frozen=True)
class GroundCells:
    """Ground of a planning grid, metres above sea level, each indexed ``[row, col]`` like the
    masks. Read-only arrays, shared by every caller."""

    centre: Elevations
    """At the cell centre: what a map shows."""
    high: Elevations
    """Highest in the cell and its ``CLEARANCE_CELLS`` neighbours: what the drone must clear."""
    low: Elevations
    """Lowest in the cell: what may hide the drone from an emitter."""


def ground_cells(terrain: Terrain, grid: Grid) -> GroundCells:
    return _ground_cells(None if terrain.dem is None else _paths(terrain), grid)


@functools.lru_cache(maxsize=64)
def _ground_cells(paths: tuple[Path, Path | None] | None, grid: Grid) -> GroundCells:
    dem = FLAT if paths is None else _raster(*paths)
    k = max(3, math.ceil(grid.cell_m / SAMPLE_M))
    inside = (np.arange(k) + 0.5) / k * grid.cell_m
    xs = (np.arange(grid.cols)[:, np.newaxis] * grid.cell_m + inside).ravel()
    ys = (np.arange(grid.rows)[:, np.newaxis] * grid.cell_m + inside).ravel()
    fine = dem.get_elevations_lonlat(*_lonlat(grid.origin, *np.meshgrid(xs, ys)), nearest=True)
    blocks = fine.reshape(grid.rows, k, grid.cols, k)
    centres_x = (np.arange(grid.cols) + 0.5) * grid.cell_m
    centres_y = (np.arange(grid.rows) + 0.5) * grid.cell_m
    centre = dem.get_elevations_lonlat(*_lonlat(grid.origin, *np.meshgrid(centres_x, centres_y)))
    size = 2 * CLEARANCE_CELLS + 1
    high = maximum_filter(np.maximum(blocks.max(axis=(1, 3)), centre), size=size, mode="nearest")
    low = np.minimum(blocks.min(axis=(1, 3)), centre)
    for array in (centre, high, low):
        array.setflags(write=False)
    return GroundCells(centre=centre, high=high, low=low)


def with_altitude(terrain: Terrain, grid: Grid, route: Sequence[RoutePoint]) -> list[RoutePoint]:
    """``route`` with ``amsl_m`` on each point that has a height: the clearance ground of its
    cell plus its height, the ground surface for a point on the ground. Unchanged in 2D."""
    if all(p.alt_m is None for p in route):
        return list(route)
    ground = ground_cells(terrain, grid)
    out = []
    for p in route:
        cell = grid.cell_of(Position(lat=p.lat, lon=p.lon))
        if p.alt_m is None or cell is None:
            out.append(p)
            continue
        base = ground.centre if p.alt_m == 0 else ground.high
        amsl = round(float(base[cell[1], cell[0]]) + p.alt_m, 1)
        out.append(p.model_copy(update={"amsl_m": amsl}))
    return out


def profile(
    terrain: Terrain, route: Sequence[RoutePoint], grid: Grid | None = None
) -> list[ProfilePoint]:
    """The route every ``PROFILE_STEP_M`` along its flown part, and at each of its points.
    Ground waits and hovers add no distance. With ``grid`` (the mission's), each sample with a
    height gets its altitude above sea level: clearance ground of its cell plus height."""
    flown = [p for p in route if p.action != "ground"] or list(route[:1])
    if not flown:
        return []
    frame = LocalFrame(Position(lat=flown[0].lat, lon=flown[0].lon))
    samples: list[tuple[float, Position, float | None]] = []
    distance = 0.0
    for a, b in itertools.pairwise(flown):
        ax, ay = frame.to_xy(Position(lat=a.lat, lon=a.lon))
        bx, by = frame.to_xy(Position(lat=b.lat, lon=b.lon))
        length = math.hypot(bx - ax, by - ay)
        steps = max(1, math.ceil(length / PROFILE_STEP_M))
        for i in range(steps):
            f = i / steps
            at = frame.to_position(ax + f * (bx - ax), ay + f * (by - ay))
            samples.append((distance + f * length, at, _height(a, b, f)))
        distance += length
    last = flown[-1]
    samples.append((distance, Position(lat=last.lat, lon=last.lon), last.alt_m))
    ground = ground_model(terrain).get_elevations([p for _, p, _ in samples])
    high = None if grid is None else ground_cells(terrain, grid).high

    def amsl(at: Position, alt: float | None) -> float | None:
        cell = None if grid is None else grid.cell_of(at)
        if alt is None or high is None or cell is None:
            return None
        return round(float(high[cell[1], cell[0]]) + alt, 1)

    return [
        ProfilePoint(
            distance_m=round(d, 1),
            lat=at.lat,
            lon=at.lon,
            ground_m=round(float(g), 1),
            alt_m=None if alt is None else round(alt, 1),
            amsl_m=amsl(at, alt),
        )
        for (d, at, alt), g in zip(samples, ground, strict=True)
    ]


def _height(a: RoutePoint, b: RoutePoint, f: float) -> float | None:
    if a.alt_m is None or b.alt_m is None:
        return None
    return a.alt_m + f * (b.alt_m - a.alt_m)


def _lonlat(origin: Position, xs: Elevations, ys: Elevations) -> tuple[Elevations, Elevations]:
    """Vectorised ``LocalFrame(origin).to_position``."""
    cos_lat = math.cos(math.radians(origin.lat))
    lats = origin.lat + np.degrees(ys / EARTH_RADIUS_M)
    lons = origin.lon + np.degrees(xs / (EARTH_RADIUS_M * cos_lat))
    return lons, lats
