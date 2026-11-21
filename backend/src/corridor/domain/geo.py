"""Positions, local metric frame and the planning grid.

The grid is not part of the scenario: the server derives one per mission from its zones
(``corridor.domain.zones.mission_constraints``) and sends it in the snapshot. The area is about
10 x 10 km, so an equirectangular projection around a local origin is accurate to well under
a cell.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import Field

from corridor.domain.model import Model

EARTH_RADIUS_M = 6_371_000.0

Cell = tuple[int, int]
"""(col, row), col grows east, row grows north, (0, 0) is the south-west cell."""


class Position(Model):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


@dataclass(frozen=True)
class LocalFrame:
    """Metres east (x) and north (y) of ``origin``."""

    origin: Position

    def to_xy(self, p: Position) -> tuple[float, float]:
        x = math.radians(p.lon - self.origin.lon) * EARTH_RADIUS_M * self._cos_lat
        y = math.radians(p.lat - self.origin.lat) * EARTH_RADIUS_M
        return x, y

    def to_position(self, x: float, y: float) -> Position:
        lat = self.origin.lat + math.degrees(y / EARTH_RADIUS_M)
        lon = self.origin.lon + math.degrees(x / (EARTH_RADIUS_M * self._cos_lat))
        return Position(lat=lat, lon=lon)

    @property
    def _cos_lat(self) -> float:
        return math.cos(math.radians(self.origin.lat))


class Grid(Model):
    origin: Position = Field(description="South-west corner of the grid.")
    cell_m: float = Field(gt=0)
    cols: int = Field(gt=0)
    rows: int = Field(gt=0)

    @classmethod
    def covering(cls, points: Sequence[Position], margin_m: float, cell_m: float) -> Grid:
        """Smallest grid holding every point with ``margin_m`` on each side."""
        if not points:
            raise ValueError("a grid needs at least one point")
        south_west = Position(lat=min(p.lat for p in points), lon=min(p.lon for p in points))
        probe = LocalFrame(south_west)
        xs, ys = zip(*(probe.to_xy(p) for p in points), strict=True)
        return cls(
            origin=probe.to_position(-margin_m, -margin_m),
            cell_m=cell_m,
            cols=math.ceil((max(xs) + 2 * margin_m) / cell_m),
            rows=math.ceil((max(ys) + 2 * margin_m) / cell_m),
        )

    @property
    def frame(self) -> LocalFrame:
        return LocalFrame(self.origin)

    def to_xy(self, p: Position) -> tuple[float, float]:
        """Metres east and north of the grid origin."""
        return self.frame.to_xy(p)

    def to_position(self, x: float, y: float) -> Position:
        return self.frame.to_position(x, y)

    def cell_of(self, p: Position) -> Cell | None:
        """Cell containing ``p``, or None outside the grid."""
        x, y = self.to_xy(p)
        col, row = math.floor(x / self.cell_m), math.floor(y / self.cell_m)
        if 0 <= col < self.cols and 0 <= row < self.rows:
            return col, row
        return None
