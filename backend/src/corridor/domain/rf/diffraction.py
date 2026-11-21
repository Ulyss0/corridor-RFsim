"""Electromagnetic diffraction modeling over terrain profiles.

Implements ITU-R P.526 Knife-Edge Diffraction, Fresnel Zone clearance radius,
and Deygout multiple-obstacle diffraction decomposition.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from corridor.domain.rf.profile import ElevationProfile

SPEED_OF_LIGHT = 299_792_458.0


@dataclass(frozen=True)
class DiffractionResult:
    """Diffraction evaluation over an elevation profile."""

    loss_db: float
    """Total diffraction loss in dB (>= 0)."""

    fresnel_radius_m: np.ndarray
    """1st Fresnel zone radius r_1(d) along the profile in metres."""

    fresnel_clearance_ratio: np.ndarray
    """Clearance ratio M(d) / r_1(d). Free space requires ratio >= 0.6."""

    min_clearance_ratio: float
    """Minimum clearance ratio across the path."""

    dominant_nu: float
    """Fresnel-Kirchhoff parameter of the primary obstacle."""

    num_obstructing_peaks: int
    """Number of significant terrain peaks penetrating the 1st Fresnel zone."""


def frequency_to_wavelength(freq_hz: float) -> float:
    """Convert frequency in Hz to wavelength in metres."""
    if freq_hz <= 0:
        raise ValueError("Frequency must be strictly positive")
    return SPEED_OF_LIGHT / freq_hz


def parse_band_to_hz(band: str) -> float:
    """Parse standard RF band designations to frequency in Hz.

    e.g. '2.4GHz' -> 2.4e9, '5.8GHz' -> 5.8e9, '868MHz' -> 868e6, '915MHz' -> 915e6.
    """
    clean = band.strip().upper().replace(" ", "")
    if clean.endswith("GHZ"):
        return float(clean[:-3]) * 1e9
    if clean.endswith("MHZ"):
        return float(clean[:-3]) * 1e6
    if clean.endswith("KHZ"):
        return float(clean[:-3]) * 1e3
    if clean.endswith("HZ"):
        return float(clean[:-2])
    # Fallback to float Hz
    return float(clean)


def fresnel_radius(wavelength_m: float, d1_m: float, d2_m: float) -> float:
    """1st Fresnel zone radius in metres at distance d1 from TX and d2 from RX.

    Formula: r_1 = sqrt(lambda * d1 * d2 / (d1 + d2))
    """
    total = d1_m + d2_m
    if total <= 1e-6 or d1_m <= 0 or d2_m <= 0:
        return 0.0
    return math.sqrt((wavelength_m * d1_m * d2_m) / total)


def knife_edge_loss_db(nu: float) -> float:
    """Single knife-edge diffraction loss J(nu) in dB (ITU-R P.526-15, Section 4.1).

    Parameters:
        nu: Fresnel-Kirchhoff dimensionless diffraction parameter.
            nu <= -0.78: Free-space clearance, J(nu) = 0 dB.
            nu = 0.0: Grazing incidence along the obstacle edge, J(nu) ~ 6.0 dB.
            nu > 0.0: Ray is physically obstructed, J(nu) increases logarithmically.
    """
    if nu <= -0.78:
        return 0.0
    term = math.sqrt((nu - 0.1) ** 2 + 1.0) + nu - 0.1
    return 6.9 + 20.0 * math.log10(max(1e-9, term))


def compute_diffraction(
    profile: ElevationProfile,
    wavelength_m: float,
    tx_h_agl_m: float = 2.0,
    rx_h_agl_m: float = 2.0,
) -> DiffractionResult:
    """Compute diffraction loss and Fresnel clearance using Deygout multi-obstacle method.

    Parameters:
        profile: 1D elevation profile between TX and RX.
        wavelength_m: Electromagnetic carrier wavelength in metres.
        tx_h_agl_m: TX antenna height AGL in metres.
        rx_h_agl_m: RX antenna height AGL in metres.
    """
    pts = profile.points
    n = len(pts)
    total_d = profile.total_distance_m

    if n <= 2 or total_d <= 1e-3:
        return DiffractionResult(
            loss_db=0.0,
            fresnel_radius_m=np.zeros(n),
            fresnel_clearance_ratio=np.full(n, 10.0),
            min_clearance_ratio=10.0,
            dominant_nu=-5.0,
            num_obstructing_peaks=0,
        )

    # Coordinates
    d_arr = profile.distances_m
    h_eff_arr = profile.effective_elevations_m

    h_tx = pts[0].elevation_m + tx_h_agl_m
    h_rx = pts[-1].elevation_m + rx_h_agl_m

    # Direct line altitude
    ray_elevations = h_tx + (d_arr / total_d) * (h_rx - h_tx)
    clearances = ray_elevations - h_eff_arr

    # Vectorized 1st Fresnel zone radius
    d1 = d_arr
    d2 = total_d - d_arr
    denom = total_d
    fresnel_r = np.sqrt(np.maximum(0.0, (wavelength_m * d1 * d2) / denom))

    # Safe clearance ratio M(d) / r_1(d)
    # At endpoints r_1 is 0; avoid division by zero
    interior = slice(1, -1)
    safe_r = np.where(fresnel_r[interior] > 1e-4, fresnel_r[interior], 1e-4)
    clearance_ratio_interior = clearances[interior] / safe_r
    min_ratio = float(np.min(clearance_ratio_interior))

    clearance_ratios = np.full(n, 10.0)
    clearance_ratios[interior] = clearance_ratio_interior

    # Compute Fresnel parameter nu(i) for all interior points
    # nu = -M(d) * sqrt(2 * D / (lambda * d1 * d2))
    factor = np.sqrt(
        (2.0 * total_d) / (wavelength_m * np.maximum(1.0, d1[interior] * d2[interior]))
    )
    nu_interior = -clearances[interior] * factor

    dominant_idx_rel = int(np.argmax(nu_interior))
    dominant_idx = dominant_idx_rel + 1  # Offset by 1 for interior slice
    dominant_nu = float(nu_interior[dominant_idx_rel])

    # If the dominant obstacle doesn't penetrate 60% of the 1st Fresnel zone (nu <= -0.78),
    # diffraction loss is negligible.
    if dominant_nu <= -0.78:
        return DiffractionResult(
            loss_db=0.0,
            fresnel_radius_m=fresnel_r,
            fresnel_clearance_ratio=clearance_ratios,
            min_clearance_ratio=min_ratio,
            dominant_nu=dominant_nu,
            num_obstructing_peaks=0,
        )

    # Dominant knife edge loss
    loss_main = knife_edge_loss_db(dominant_nu)

    # Deygout sub-obstacle decomposition:
    # Sub-obstacle 1: between TX (index 0) and Dominant (dominant_idx)
    loss_sub1 = 0.0
    if dominant_idx > 2:
        loss_sub1 = _sub_obstacle_loss(
            profile,
            wavelength_m,
            start_idx=0,
            end_idx=dominant_idx,
            h_start=h_tx,
            h_end=h_eff_arr[dominant_idx],
        )

    # Sub-obstacle 2: between Dominant (dominant_idx) and RX (n - 1)
    loss_sub2 = 0.0
    if (n - 1) - dominant_idx > 2:
        loss_sub2 = _sub_obstacle_loss(
            profile,
            wavelength_m,
            start_idx=dominant_idx,
            end_idx=n - 1,
            h_start=h_eff_arr[dominant_idx],
            h_end=h_rx,
        )

    total_diffraction_loss = loss_main + loss_sub1 + loss_sub2
    num_peaks = int(np.sum(nu_interior > -0.78))

    return DiffractionResult(
        loss_db=total_diffraction_loss,
        fresnel_radius_m=fresnel_r,
        fresnel_clearance_ratio=clearance_ratios,
        min_clearance_ratio=min_ratio,
        dominant_nu=dominant_nu,
        num_obstructing_peaks=num_peaks,
    )


def _sub_obstacle_loss(
    profile: ElevationProfile,
    wavelength_m: float,
    start_idx: int,
    end_idx: int,
    h_start: float,
    h_end: float,
) -> float:
    """Compute diffraction loss for a Deygout sub-path between two nodes."""
    d_arr = profile.distances_m
    h_eff_arr = profile.effective_elevations_m

    sub_d = d_arr[start_idx : end_idx + 1] - d_arr[start_idx]
    sub_total_d = sub_d[-1]
    if sub_total_d <= 1e-3 or len(sub_d) <= 2:
        return 0.0

    sub_h_eff = h_eff_arr[start_idx : end_idx + 1]
    sub_ray = h_start + (sub_d / sub_total_d) * (h_end - h_start)
    sub_clearance = sub_ray - sub_h_eff

    # Interior of sub-path
    sub_int = slice(1, -1)
    d1 = sub_d[sub_int]
    d2 = sub_total_d - sub_d[sub_int]
    factor = np.sqrt((2.0 * sub_total_d) / (wavelength_m * np.maximum(1.0, d1 * d2)))
    nu_sub = -sub_clearance[sub_int] * factor

    max_nu = float(np.max(nu_sub))
    return knife_edge_loss_db(max_nu)
