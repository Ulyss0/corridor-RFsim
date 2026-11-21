"""Vectorized 1D terrain elevation profile extraction along radio propagation paths.

Implements sub-pixel spatial sampling, geodesic ray interpolation, and
effective Earth radius curvature correction (k-factor atmospheric refraction).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from corridor.domain.geo import EARTH_RADIUS_M, Position
from corridor.domain.rf.dem import DemProvider, _haversine_distance

DEFAULT_K_FACTOR = 4.0 / 3.0  # Standard terrestrial atmosphere (effective Earth radius ~8495 km)


@dataclass(frozen=True)
class ProfilePoint:
    """A sample point along the 1D elevation profile."""

    d_m: float
    """Distance along the path from the transmitter in metres."""

    pos: Position
    """Geographic position of the sample point."""

    elevation_m: float
    """Raw ground elevation AMSL in metres."""

    earth_bulge_m: float
    """Apparent elevation increase due to Earth curvature and refractivity in metres."""

    @property
    def effective_elevation_m(self) -> float:
        """Effective obstacle height taking Earth curvature into account."""
        return self.elevation_m + self.earth_bulge_m


@dataclass(frozen=True)
class ElevationProfile:
    """1D terrain slice between a transmitter and a receiver."""

    tx_pos: Position
    rx_pos: Position
    total_distance_m: float
    step_m: float
    k_factor: float
    points: list[ProfilePoint]

    @property
    def distances_m(self) -> np.ndarray:
        return np.array([p.d_m for p in self.points], dtype=np.float64)

    @property
    def raw_elevations_m(self) -> np.ndarray:
        return np.array([p.elevation_m for p in self.points], dtype=np.float64)

    @property
    def earth_bulge_m(self) -> np.ndarray:
        return np.array([p.earth_bulge_m for p in self.points], dtype=np.float64)

    @property
    def effective_elevations_m(self) -> np.ndarray:
        return np.array([p.effective_elevation_m for p in self.points], dtype=np.float64)


def extract_elevation_profile(
    dem: DemProvider,
    tx_pos: Position,
    rx_pos: Position,
    step_m: float = 10.0,
    k_factor: float = DEFAULT_K_FACTOR,
) -> ElevationProfile:
    """Extract a 1D elevation profile sampled every ``step_m`` from TX to RX.

    Applies the 4/3 effective Earth radius bulge:
        delta_h(d) = d * (D - d) / (2 * k * R_0)
    """
    total_d_m = _haversine_distance(tx_pos, rx_pos)
    if total_d_m <= 1e-3:
        # Collocated points
        elev = dem.get_elevation(tx_pos)
        pt = ProfilePoint(
            d_m=0.0,
            pos=tx_pos,
            elevation_m=elev,
            earth_bulge_m=0.0,
        )
        return ElevationProfile(
            tx_pos=tx_pos,
            rx_pos=rx_pos,
            total_distance_m=0.0,
            step_m=step_m,
            k_factor=k_factor,
            points=[pt],
        )

    # Number of segments along the ray
    num_steps = max(2, math.ceil(total_d_m / step_m) + 1)
    d_vals = np.linspace(0.0, total_d_m, num_steps, dtype=np.float64)

    # Geodesic linear interpolation of coordinates along ray
    fractions = d_vals / total_d_m
    lats = tx_pos.lat + fractions * (rx_pos.lat - tx_pos.lat)
    lons = tx_pos.lon + fractions * (rx_pos.lon - tx_pos.lon)
    positions = [
        Position(lat=float(lat), lon=float(lon)) for lat, lon in zip(lats, lons, strict=True)
    ]

    # Vectorized elevation lookup from DEM
    raw_elevations = dem.get_elevations(positions)

    # Atmospheric curvature correction
    effective_earth_radius = k_factor * EARTH_RADIUS_M
    # Effective Earth curvature correction formula
    earth_bulge = (d_vals * (total_d_m - d_vals)) / (2.0 * effective_earth_radius)

    points = [
        ProfilePoint(
            d_m=float(d),
            pos=pos,
            elevation_m=float(elev),
            earth_bulge_m=float(bulge),
        )
        for d, pos, elev, bulge in zip(d_vals, positions, raw_elevations, earth_bulge, strict=True)
    ]

    return ElevationProfile(
        tx_pos=tx_pos,
        rx_pos=rx_pos,
        total_distance_m=total_d_m,
        step_m=step_m,
        k_factor=k_factor,
        points=points,
    )
