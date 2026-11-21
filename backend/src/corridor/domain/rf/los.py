"""Pure Geometric Line-of-Sight (LoS) and terrain clearance analysis.

Evaluates whether the direct optical ray between two antenna phase centers is obstructed
by terrain, strictly distinguishing geometric optical visibility from electromagnetic
diffraction (Fresnel zone clearance).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from corridor.domain.rf.profile import ElevationProfile


@dataclass(frozen=True)
class LosResult:
    """Result of a pure geometric Line-of-Sight calculation."""

    has_los: bool
    """True if direct optical ray clears all terrain along the path (strictly min_clearance > 0)."""

    min_clearance_m: float
    """Minimum clearance margin between the ray and the effective terrain in metres."""

    critical_distance_m: float
    """Distance from TX to the point of minimum clearance / worst obstruction in metres."""

    critical_obstacle_elevation_m: float
    """Effective terrain elevation (including Earth curvature) at the critical point in metres."""

    critical_ray_elevation_m: float
    """Altitude of the direct ray at the critical point in metres."""

    ray_elevations_m: np.ndarray
    """Ray altitude AMSL sampled at each profile point in metres."""

    clearances_m: np.ndarray
    """Clearance margin M(d) = z_ray(d) - h_eff(d) at each profile point in metres."""

    obstruction_indices: list[int]
    """Indices of profile points where terrain obstructs the ray (clearance <= 0)."""


def compute_los(
    profile: ElevationProfile,
    tx_h_agl_m: float = 2.0,
    rx_h_agl_m: float = 2.0,
) -> LosResult:
    """Compute pure geometric Line-of-Sight (optical clearance) over an elevation profile.

    Parameters:
        profile: The 1D elevation profile between TX and RX.
        tx_h_agl_m: Transmitter antenna height Above Ground Level in metres (e.g. mast height).
        rx_h_agl_m: Receiver antenna height Above Ground Level in metres (e.g. drone flight AGL).
    """
    pts = profile.points
    if len(pts) <= 1 or profile.total_distance_m <= 1e-3:
        # Collocated nodes
        base = pts[0].elevation_m if pts else 0.0
        return LosResult(
            has_los=True,
            min_clearance_m=max(tx_h_agl_m, rx_h_agl_m),
            critical_distance_m=0.0,
            critical_obstacle_elevation_m=base,
            critical_ray_elevation_m=base + max(tx_h_agl_m, rx_h_agl_m),
            ray_elevations_m=np.array([base + tx_h_agl_m]),
            clearances_m=np.array([tx_h_agl_m]),
            obstruction_indices=[],
        )

    # Antenna phase centers AMSL
    h_tx_amsl = pts[0].elevation_m + tx_h_agl_m
    h_rx_amsl = pts[-1].elevation_m + rx_h_agl_m

    d_arr = profile.distances_m
    total_d = profile.total_distance_m
    effective_h_arr = profile.effective_elevations_m

    # Direct ray altitude equation: z_ray(d) = H_TX + (d / D) * (H_RX - H_TX)
    ray_elevations = h_tx_amsl + (d_arr / total_d) * (h_rx_amsl - h_tx_amsl)

    # Clearance margin: M(d) = z_ray(d) - h_eff(d)
    clearances = ray_elevations - effective_h_arr

    # Exclude the exact endpoints (TX and RX base cells) when evaluating obstacle intrusion
    interior_slice = slice(1, -1) if len(pts) > 2 else slice(0, len(pts))
    interior_clearances = clearances[interior_slice]
    interior_d = d_arr[interior_slice]
    interior_effective_h = effective_h_arr[interior_slice]
    interior_ray_h = ray_elevations[interior_slice]

    min_idx_rel = int(np.argmin(interior_clearances))
    min_clearance = float(interior_clearances[min_idx_rel])
    critical_d = float(interior_d[min_idx_rel])
    critical_obst_elev = float(interior_effective_h[min_idx_rel])
    critical_ray_elev = float(interior_ray_h[min_idx_rel])

    # Ray is obstructed if any interior point has clearance <= 0.0
    obstruction_indices = [int(i) for i in range(1, len(pts) - 1) if clearances[i] <= 0.0]

    has_los = min_clearance > 0.0

    return LosResult(
        has_los=has_los,
        min_clearance_m=min_clearance,
        critical_distance_m=critical_d,
        critical_obstacle_elevation_m=critical_obst_elev,
        critical_ray_elevation_m=critical_ray_elev,
        ray_elevations_m=ray_elevations,
        clearances_m=clearances,
        obstruction_indices=obstruction_indices,
    )
