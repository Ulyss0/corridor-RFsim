"""Digital Elevation Model (DEM) providers and raster ingestion.

Supports on-demand Cloud-Optimized GeoTIFF (COG) streaming via rasterio/GDAL,
local bounding box caching, and synthetic terrain generation for deterministic testing.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
import rasterio
from rasterio.windows import from_bounds
from scipy.ndimage import map_coordinates

from corridor.domain.geo import EARTH_RADIUS_M, Position


class DemProvider(Protocol):
    """Protocol for elevation querying over geographic coordinates."""

    def get_elevation(self, pos: Position) -> float:
        """Elevation in metres Above Mean Sea Level (AMSL) at ``pos``."""
        ...

    def get_elevations(self, positions: Sequence[Position]) -> np.ndarray:
        """Vectorized elevations in metres AMSL for an ordered sequence of positions."""
        ...

    def get_elevations_lonlat(
        self, lons: np.ndarray, lats: np.ndarray, nearest: bool = False
    ) -> np.ndarray:
        """Elevations in metres AMSL at arrays of longitudes and latitudes, any shape.

        ``nearest``: the value of the raster cell each point falls in, not interpolated, so a
        one-pixel peak or pit keeps its full height."""
        ...


@dataclass(frozen=True)
class HillFeature:
    """A geometric elevation obstacle (e.g. hill, ridge)."""

    center: Position
    radius_m: float
    height_m: float


class SyntheticDem:
    """Deterministic synthetic elevation model for unit testing and offline simulation."""

    def __init__(
        self,
        base_elevation_m: float = 500.0,
        hills: Sequence[HillFeature] | None = None,
    ) -> None:
        self.base_elevation_m = base_elevation_m
        self.hills = list(hills or [])

    def get_elevation(self, pos: Position) -> float:
        elev = self.base_elevation_m
        for hill in self.hills:
            d_m = _haversine_distance(pos, hill.center)
            if d_m < hill.radius_m:
                # Smooth cosine bell hill
                elev += hill.height_m * 0.5 * (1.0 + math.cos(math.pi * d_m / hill.radius_m))
        return elev

    def get_elevations(self, positions: Sequence[Position]) -> np.ndarray:
        return np.array([self.get_elevation(p) for p in positions], dtype=np.float64)

    def get_elevations_lonlat(
        self, lons: np.ndarray, lats: np.ndarray, nearest: bool = False
    ) -> np.ndarray:
        lons, lats = np.broadcast_arrays(lons, lats)
        flat = [
            self.get_elevation(Position(lat=float(lat), lon=float(lon)))
            for lon, lat in zip(lons.ravel(), lats.ravel(), strict=True)
        ]
        return np.asarray(flat, dtype=np.float64).reshape(lons.shape)


class RasterDem:
    """Raster-backed DEM provider (GeoTIFF / COG) with bilinear interpolation."""

    def __init__(self, raster_path: str | Path) -> None:
        self.path = Path(raster_path)
        if not self.path.exists():
            raise FileNotFoundError(f"DEM raster not found at {self.path}")

        with rasterio.open(self.path) as src:
            self.bounds = src.bounds
            self.transform = src.transform
            self.nodata = src.nodata if src.nodata is not None else -9999.0
            # Read 1st band into memory
            self._data: np.ndarray = src.read(1).astype(np.float64)
            self._inv_transform = ~self.transform

    def get_elevation(self, pos: Position) -> float:
        res = self.get_elevations([pos])
        return float(res[0])

    def raise_to(self, surface: np.ndarray) -> None:
        """Elevation becomes the higher of itself and ``surface`` (same shape as the raster),
        e.g. building roofs."""
        self._data = np.maximum(self._data, surface)

    def get_elevations(self, positions: Sequence[Position]) -> np.ndarray:
        if not positions:
            return np.empty(0, dtype=np.float64)

        xs = np.asarray([p.lon for p in positions], dtype=np.float64)
        ys = np.asarray([p.lat for p in positions], dtype=np.float64)
        return self.get_elevations_lonlat(xs, ys)

    def get_elevations_lonlat(
        self, lons: np.ndarray, lats: np.ndarray, nearest: bool = False
    ) -> np.ndarray:
        lons, lats = np.broadcast_arrays(lons, lats)
        shape = lons.shape
        xs_arr = np.asarray(lons, dtype=np.float64).ravel()
        ys_arr = np.asarray(lats, dtype=np.float64).ravel()

        # Vectorized transform from geographic (lon, lat) to pixel coordinates (col, row)
        # using the inverse affine transformation coefficients
        inv = self._inv_transform
        cols_arr = inv.a * xs_arr + inv.b * ys_arr + inv.c
        rows_arr = inv.d * xs_arr + inv.e * ys_arr + inv.f

        # Bilinear interpolation via scipy.ndimage.map_coordinates
        # order=1 is bilinear interpolation, mode='nearest' clamps to boundary.
        # The transform gives pixel corners; map_coordinates samples pixel centres.
        coords = np.vstack([rows_arr - 0.5, cols_arr - 0.5])
        order = 0 if nearest else 1
        elevations = map_coordinates(self._data, coords, order=order, mode="nearest")

        # Handle nodata if present
        elevations = np.where(elevations == self.nodata, 0.0, elevations)
        return elevations.reshape(shape)

    @classmethod
    def create_test_raster(
        cls,
        output_path: Path,
        origin_lat: float,
        origin_lon: float,
        width_px: int = 100,
        height_px: int = 100,
        pixel_size_deg: float = 0.000277777777778,  # ~30m
        base_elevation: float = 500.0,
        hill_center_px: tuple[int, int] | None = None,
        hill_radius_px: float = 20.0,
        hill_height_m: float = 120.0,
    ) -> RasterDem:
        """Create a valid GeoTIFF file for testing raster ingestion and interpolation."""
        from rasterio.transform import from_origin

        output_path.parent.mkdir(parents=True, exist_ok=True)
        transform = from_origin(origin_lon, origin_lat, pixel_size_deg, pixel_size_deg)

        # Generate elevation grid
        y, x = np.ogrid[:height_px, :width_px]
        data = np.full((height_px, width_px), base_elevation, dtype=np.float32)

        if hill_center_px is not None:
            cy, cx = hill_center_px
            dist = np.hypot(x - cx, y - cy)
            mask = dist < hill_radius_px
            data[mask] += (
                hill_height_m * 0.5 * (1.0 + np.cos(np.pi * dist[mask] / hill_radius_px))
            ).astype(np.float32)

        with rasterio.open(
            output_path,
            "w",
            driver="GTiff",
            height=height_px,
            width=width_px,
            count=1,
            dtype=rasterio.float32,
            crs="EPSG:4326",
            transform=transform,
        ) as dst:
            dst.write(data, 1)

        return cls(output_path)


def download_copernicus_dem_window(
    min_lat: float,
    min_lon: float,
    max_lat: float,
    max_lon: float,
    dest_tif_path: Path,
    margin_deg: float = 0.05,
) -> Path:
    """Crop and cache a window from the public Copernicus GLO-30 AWS COG dataset.

    Uses GDAL /vsicurl/ virtual filesystem to download only the tile blocks
    intersecting the requested bounding box.
    """
    dest_tif_path.parent.mkdir(parents=True, exist_ok=True)
    if dest_tif_path.exists():
        return dest_tif_path

    # Determine 1-degree tile identifier (Copernicus DEM naming convention)
    # e.g., Copernicus_DSM_COG_10_N48_00_E011_00_DEM
    lat_int = math.floor(min_lat)
    lon_int = math.floor(min_lon)
    lat_char = "N" if lat_int >= 0 else "S"
    lon_char = "E" if lon_int >= 0 else "W"
    tile_id = (
        f"Copernicus_DSM_COG_10_{lat_char}{abs(lat_int):02d}_00_{lon_char}{abs(lon_int):03d}_00_DEM"
    )
    cog_url = f"/vsicurl/https://copernicus-dem-30m.s3.amazonaws.com/{tile_id}/{tile_id}.tif"

    w_min_lon = min_lon - margin_deg
    w_min_lat = min_lat - margin_deg
    w_max_lon = max_lon + margin_deg
    w_max_lat = max_lat + margin_deg

    with rasterio.open(cog_url) as src:
        window = from_bounds(w_min_lon, w_min_lat, w_max_lon, w_max_lat, src.transform)
        transform = src.window_transform(window)
        out_data = src.read(1, window=window)

        profile = src.profile.copy()
        profile.update(
            {
                "driver": "GTiff",
                "height": out_data.shape[0],
                "width": out_data.shape[1],
                "transform": transform,
                "compress": "deflate",
            }
        )

        with rasterio.open(dest_tif_path, "w", **profile) as dst:
            dst.write(out_data, 1)

    return dest_tif_path


def _haversine_distance(p1: Position, p2: Position) -> float:
    """Great-circle distance between two positions in metres."""
    d_lat = math.radians(p2.lat - p1.lat)
    d_lon = math.radians(p2.lon - p1.lon)
    lat1 = math.radians(p1.lat)
    lat2 = math.radians(p2.lat)

    a = math.sin(d_lat / 2.0) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(d_lon / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return EARTH_RADIUS_M * c
