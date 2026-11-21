"""Link budget and Jammer-to-Signal (J/S) propagation modeling.

Computes Free-Space Path Loss (FSPL), total path loss including terrain
diffraction, received signal power, and terrain-aware Jammer-to-Signal ratios.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from corridor.domain.geo import Position
from corridor.domain.rf.dem import DemProvider
from corridor.domain.rf.diffraction import (
    SPEED_OF_LIGHT,
    DiffractionResult,
    compute_diffraction,
    frequency_to_wavelength,
    parse_band_to_hz,
)
from corridor.domain.rf.profile import (
    DEFAULT_K_FACTOR,
    ElevationProfile,
    extract_elevation_profile,
)


def watts_to_dbm(power_w: float) -> float:
    """Convert power in Watts to dBm."""
    if power_w <= 0:
        raise ValueError("Power must be strictly positive")
    return float(10.0 * math.log10(power_w) + 30.0)


def dbm_to_watts(power_dbm: float) -> float:
    """Convert power in dBm to Watts."""
    return float(10.0 ** ((power_dbm - 30.0) / 10.0))


def free_space_path_loss_db(distance_m: float, freq_hz: float) -> float:
    """Free Space Path Loss (FSPL) in dB.

    Formula: FSPL = 20*log10(d) + 20*log10(f) + 20*log10(4*pi/c)
    """
    if distance_m <= 1.0:
        return 0.0
    if freq_hz <= 0:
        raise ValueError("Frequency must be strictly positive")

    # 20 * log10(4 * pi / c) ~= -147.5522167
    c_term = 20.0 * math.log10(4.0 * math.pi / SPEED_OF_LIGHT)
    return 20.0 * math.log10(distance_m) + 20.0 * math.log10(freq_hz) + c_term


@dataclass(frozen=True)
class LinkBudgetResult:
    """Detailed link budget calculation along a terrain profile."""

    distance_m: float
    """Total 3D/geodesic path distance in metres."""

    fspl_db: float
    """Free Space Path Loss in dB."""

    diffraction_loss_db: float
    """Knife-edge diffraction loss over terrain obstacles in dB."""

    total_loss_db: float
    """Total path loss: FSPL + diffraction loss (dB)."""

    eirp_dbm: float
    """Transmitter EIRP in dBm."""

    p_rx_dbm: float
    """Received power at the receiver antenna in dBm."""

    p_rx_w: float
    """Received power in Watts."""

    diffraction: DiffractionResult
    """Detailed diffraction and Fresnel zone clearance evaluation."""


@dataclass(frozen=True)
class JsResult:
    """Terrain-aware Jammer-to-Signal (J/S) evaluation at a receiver position."""

    drone_pos: Position
    """Drone (receiver) location."""

    drone_h_agl_m: float
    """Drone altitude above ground level in metres."""

    pilot_link: LinkBudgetResult
    """Link budget from the ground pilot transmitter to the drone."""

    jammer_link: LinkBudgetResult
    """Link budget from the threat jammer to the drone."""

    js_db: float
    """Terrain-aware Jammer-to-Signal ratio: P_rx(jammer) - P_rx(pilot) in dB."""

    threshold_db: float
    """Mission jamming threshold in dB."""

    is_jammed: bool
    """True if js_db >= threshold_db."""

    fspl_js_db: float
    """Naive free-space J/S ratio without terrain diffraction (Apollonius baseline)."""

    terrain_shielding_db: float
    """Relative terrain attenuation benefit: (L_diff,jammer - L_diff,pilot) in dB.

    A positive value means the terrain shields the drone from the jammer more than from the pilot.
    """


def compute_link_budget(
    profile: ElevationProfile,
    tx_eirp_w: float,
    freq_hz: float,
    tx_h_agl_m: float = 2.0,
    rx_h_agl_m: float = 2.0,
    rx_gain_dbi: float = 0.0,
) -> LinkBudgetResult:
    """Calculate the RF link budget along a given elevation profile."""
    distance_m = max(1.0, profile.total_distance_m)
    wavelength_m = frequency_to_wavelength(freq_hz)

    fspl = free_space_path_loss_db(distance_m, freq_hz)
    diff = compute_diffraction(
        profile=profile,
        wavelength_m=wavelength_m,
        tx_h_agl_m=tx_h_agl_m,
        rx_h_agl_m=rx_h_agl_m,
    )

    total_loss = fspl + diff.loss_db
    eirp_dbm = watts_to_dbm(tx_eirp_w)
    p_rx_dbm = eirp_dbm - total_loss + rx_gain_dbi
    p_rx_w = dbm_to_watts(p_rx_dbm)

    return LinkBudgetResult(
        distance_m=distance_m,
        fspl_db=fspl,
        diffraction_loss_db=diff.loss_db,
        total_loss_db=total_loss,
        eirp_dbm=eirp_dbm,
        p_rx_dbm=p_rx_dbm,
        p_rx_w=p_rx_w,
        diffraction=diff,
    )


def compute_terrain_aware_js(
    dem: DemProvider,
    drone_pos: Position,
    drone_h_agl_m: float,
    pilot_pos: Position,
    pilot_eirp_w: float,
    jammer_pos: Position,
    jammer_eirp_w: float,
    band_or_freq_hz: str | float,
    threshold_db: float,
    pilot_h_agl_m: float = 2.0,
    jammer_h_agl_m: float = 10.0,
    step_m: float = 25.0,
    k_factor: float = DEFAULT_K_FACTOR,
) -> JsResult:
    """Evaluate Jammer-to-Signal ratio with real terrain diffraction.

    Parameters:
        dem: Digital Elevation Model provider.
        drone_pos: Geographic position of the drone.
        drone_h_agl_m: Drone altitude above ground level.
        pilot_pos: Position of the pilot ground station.
        pilot_eirp_w: Pilot transmitter EIRP in Watts.
        jammer_pos: Position of the hostile jammer.
        jammer_eirp_w: Jammer EIRP in Watts.
        band_or_freq_hz: RF frequency in Hz or standard string (e.g. '2.4GHz').
        threshold_db: Link cutoff threshold in dB.
        pilot_h_agl_m: Pilot antenna height AGL.
        jammer_h_agl_m: Jammer mast height AGL.
        step_m: Profile sampling resolution in metres.
        k_factor: Effective Earth radius factor (default 4/3).
    """
    freq_hz = (
        parse_band_to_hz(band_or_freq_hz)
        if isinstance(band_or_freq_hz, str)
        else float(band_or_freq_hz)
    )

    # 1. Profile from Pilot to Drone
    pilot_profile = extract_elevation_profile(
        dem=dem,
        tx_pos=pilot_pos,
        rx_pos=drone_pos,
        step_m=step_m,
        k_factor=k_factor,
    )
    pilot_link = compute_link_budget(
        profile=pilot_profile,
        tx_eirp_w=pilot_eirp_w,
        freq_hz=freq_hz,
        tx_h_agl_m=pilot_h_agl_m,
        rx_h_agl_m=drone_h_agl_m,
    )

    # 2. Profile from Jammer to Drone
    jammer_profile = extract_elevation_profile(
        dem=dem,
        tx_pos=jammer_pos,
        rx_pos=drone_pos,
        step_m=step_m,
        k_factor=k_factor,
    )
    jammer_link = compute_link_budget(
        profile=jammer_profile,
        tx_eirp_w=jammer_eirp_w,
        freq_hz=freq_hz,
        tx_h_agl_m=jammer_h_agl_m,
        rx_h_agl_m=drone_h_agl_m,
    )

    # 3. Terrain-aware J/S
    # J/S = P_rx(jammer) - P_rx(pilot) in dB
    js_db = jammer_link.p_rx_dbm - pilot_link.p_rx_dbm

    # 4. Naive flat-earth FSPL J/S (Apollonius)
    # J/S_fspl = (EIRP_j - FSPL_j) - (EIRP_s - FSPL_s)
    fspl_js_db = (jammer_link.eirp_dbm - jammer_link.fspl_db) - (
        pilot_link.eirp_dbm - pilot_link.fspl_db
    )

    # 5. Shielding benefit: how much more jammer is attenuated than pilot
    terrain_shielding_db = jammer_link.diffraction_loss_db - pilot_link.diffraction_loss_db

    is_jammed = js_db >= threshold_db

    return JsResult(
        drone_pos=drone_pos,
        drone_h_agl_m=drone_h_agl_m,
        pilot_link=pilot_link,
        jammer_link=jammer_link,
        js_db=js_db,
        threshold_db=threshold_db,
        is_jammed=is_jammed,
        fspl_js_db=fspl_js_db,
        terrain_shielding_db=terrain_shielding_db,
    )
