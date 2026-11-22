"""3D zones as map outlines: the cells the planner forbids, per flight level and per threat.

Cells come from ``corridor.domain.planner.layers`` (``cells_by_threat``, widened, margins
included, and ``link_cells``, threat ``LINK``), so the map shows what the planner avoids. Each
set of cells becomes GeoJSON polygons; the grid is an equirectangular projection, so cell
indices map to degrees linearly.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from rasterio.features import shapes
from rasterio.transform import Affine

from corridor.domain.geo import EARTH_RADIUS_M, Grid
from corridor.domain.planner.layers import LINK, cells_by_threat, link_cells
from corridor.domain.scenario import Mission, Scenario


def level_outlines(scenario: Scenario, mission: Mission, grid: Grid) -> dict[str, Any]:
    """FeatureCollection of polygons with ``threat``, ``kind`` (a zone kind, or ``link`` where
    the pilot's signal is too weak), ``level`` (index, lowest first) and ``level_m``. Empty in
    2D."""
    if scenario.terrain.mode != "3d":
        return {"type": "FeatureCollection", "features": []}
    origin = grid.origin
    deg_lat = math.degrees(grid.cell_m / EARTH_RADIUS_M)
    deg_lon = math.degrees(grid.cell_m / (EARTH_RADIUS_M * math.cos(math.radians(origin.lat))))
    # Row 0 is the southern row: latitude grows with the row index.
    transform = Affine(deg_lon, 0.0, origin.lon, 0.0, deg_lat, origin.lat)
    features = []
    by_threat: dict[str, tuple[str, Any]] = {
        LINK: ("link", link_cells(scenario, mission, grid)),
        **cells_by_threat(scenario, mission, grid),
    }
    for threat, (kind, cells) in by_threat.items():
        for level, level_m in enumerate(scenario.terrain.levels_m):
            mask = cells[level]
            if not mask.any():
                continue
            for geometry, _ in shapes(
                mask.astype(np.uint8), mask=mask, transform=transform, connectivity=4
            ):
                features.append(
                    {
                        "type": "Feature",
                        "properties": {
                            "threat": threat,
                            "kind": kind,
                            "level": level,
                            "level_m": level_m,
                        },
                        "geometry": geometry,
                    }
                )
    return {"type": "FeatureCollection", "features": features}
