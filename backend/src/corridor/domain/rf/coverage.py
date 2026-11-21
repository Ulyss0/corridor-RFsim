"""Raster coverage grid and polar ray-casting propagation engine.

Generates radial coverage matrices (polar ray casting), computes terrain-aware
viewsheds, received power grids, and exports styled GeoJSON contour polygons
for MapLibre GL, Cesium, and Leaflet map layers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from corridor.domain.geo import EARTH_RADIUS_M, LocalFrame, Position
from corridor.domain.rf.dem import DemProvider
from corridor.domain.rf.diffraction import (
    SPEED_OF_LIGHT,
    frequency_to_wavelength,
    knife_edge_loss_db,
    parse_band_to_hz,
)
from corridor.domain.rf.profile import DEFAULT_K_FACTOR, extract_elevation_profile
from corridor.domain.rf.propagation import (
    compute_link_budget,
    free_space_path_loss_db,
    watts_to_dbm,
)


@dataclass(frozen=True)
class ContourLevel:
    """Styling specification for an RF power contour."""

    level_dbm: float
    name: str
    fill_color: str
    fill_opacity: float
    stroke_color: str
    stroke_width: int


DEFAULT_CONTOURS: list[ContourLevel] = [
    ContourLevel(
        level_dbm=-70.0,
        name="Strong Coverage (-70 dBm)",
        fill_color="#40c057",
        fill_opacity=0.30,
        stroke_color="#2f9e44",
        stroke_width=2,
    ),
    ContourLevel(
        level_dbm=-85.0,
        name="Usable Coverage (-85 dBm)",
        fill_color="#fab005",
        fill_opacity=0.22,
        stroke_color="#f59f00",
        stroke_width=2,
    ),
    ContourLevel(
        level_dbm=-100.0,
        name="Fringe / Jamming Boundary (-100 dBm)",
        fill_color="#fa5252",
        fill_opacity=0.15,
        stroke_color="#e03131",
        stroke_width=2,
    ),
]


@dataclass(frozen=True)
class PolarCoverageResult:
    """Result of radial polar ray-casting around a transmitter."""

    tx_pos: Position
    tx_eirp_w: float
    freq_hz: float
    azimuths_deg: list[float]
    radii_m: list[float]
    p_rx_matrix_dbm: np.ndarray
    """Shape (len(azimuths), len(radii))."""
    loss_matrix_db: np.ndarray
    """Shape (len(azimuths), len(radii))."""
    los_matrix: np.ndarray
    """Shape (len(azimuths), len(radii)) of bool (pure geometric optical line-of-sight)."""


def _eval_ray_clearance(
    r_idx: int,
    radii_arr: np.ndarray,
    tx_abs_h: float,
    rx_abs_h: np.ndarray,
    eff_terrain: np.ndarray,
    wavelength_m: float,
) -> tuple[float, bool]:
    """Calculate single knife-edge diffraction loss and line-of-sight for a radial ray segment."""
    d_curr = radii_arr[r_idx]
    rx_h = rx_abs_h[r_idx]
    sub_d = radii_arr[: r_idx + 1]
    ray_line = tx_abs_h + (sub_d / d_curr) * (rx_h - tx_abs_h)
    clearance = ray_line - eff_terrain[: r_idx + 1]

    interior_clearance = clearance[1:-1] if r_idx > 1 else np.array([clearance[0]])
    min_clearance = float(np.min(interior_clearance)) if len(interior_clearance) > 0 else 10.0
    has_los = min_clearance >= 0.0

    if has_los and min_clearance > 15.0:
        return 0.0, True

    d1 = sub_d[1:-1] if r_idx > 1 else np.array([d_curr / 2.0])
    d2 = d_curr - d1
    factor = np.sqrt((2.0 * d_curr) / (wavelength_m * np.maximum(1.0, d1 * d2)))
    nu_arr = -interior_clearance * factor
    dominant_nu = float(np.max(nu_arr)) if len(nu_arr) > 0 else -1.0
    return knife_edge_loss_db(dominant_nu), has_los


def compute_polar_coverage(
    dem: DemProvider,
    tx_pos: Position,
    tx_eirp_w: float,
    band_or_freq_hz: str | float,
    tx_h_agl_m: float = 10.0,
    rx_h_agl_m: float = 20.0,
    max_range_m: float = 6000.0,
    azimuth_step_deg: float = 5.0,
    range_step_m: float = 50.0,
    k_factor: float = DEFAULT_K_FACTOR,
) -> PolarCoverageResult:
    """Cast radial elevation profiles around TX to compute 360-degree RF coverage.

    Parameters:
        dem: Digital elevation model provider.
        tx_pos: Transmitter geographic coordinates.
        tx_eirp_w: Transmitter EIRP in Watts.
        band_or_freq_hz: RF frequency in Hz or standard band name.
        tx_h_agl_m: Transmitter mast height AGL in metres.
        rx_h_agl_m: Receiver / drone altitude AGL in metres.
        max_range_m: Maximum radial coverage analysis radius in metres.
        azimuth_step_deg: Azimuth sampling resolution (e.g. 5 degrees -> 72 rays).
        range_step_m: Radial step resolution along each ray.
        k_factor: Atmospheric refraction effective Earth radius factor (default 4/3).
    """
    freq_hz = (
        parse_band_to_hz(band_or_freq_hz)
        if isinstance(band_or_freq_hz, str)
        else float(band_or_freq_hz)
    )
    wavelength_m = frequency_to_wavelength(freq_hz)
    eirp_dbm = watts_to_dbm(tx_eirp_w)

    frame = LocalFrame(tx_pos)
    tx_base_elev = dem.get_elevation(tx_pos)
    tx_abs_h = tx_base_elev + tx_h_agl_m

    azimuths = [float(a) for a in np.arange(0.0, 360.0, azimuth_step_deg)]
    radii = [float(r) for r in np.arange(range_step_m, max_range_m + 1e-3, range_step_m)]

    num_az = len(azimuths)
    num_r = len(radii)

    p_rx_matrix = np.zeros((num_az, num_r), dtype=np.float64)
    loss_matrix = np.zeros((num_az, num_r), dtype=np.float64)
    los_matrix = np.zeros((num_az, num_r), dtype=np.bool_)

    radii_arr = np.array(radii, dtype=np.float64)
    earth_r_eff = k_factor * 6_371_000.0

    # Pre-compute FSPL along radii
    c_term = 20.0 * math.log10(4.0 * math.pi / SPEED_OF_LIGHT)
    fspl_arr = 20.0 * np.log10(np.maximum(1.0, radii_arr)) + 20.0 * math.log10(freq_hz) + c_term

    for az_idx, az_deg in enumerate(azimuths):
        rad = math.radians(az_deg)
        sin_az = math.sin(rad)
        cos_az = math.cos(rad)

        xs = radii_arr * sin_az
        ys = radii_arr * cos_az

        positions = [frame.to_position(x, y) for x, y in zip(xs, ys, strict=True)]
        elevations = np.array(dem.get_elevations(positions), dtype=np.float64)

        # Earth curvature bulge along ray: d * (D - d) / (2 * Re)
        # For a ray emanating from TX (d=0), the horizon drop at distance d is d^2 / (2 * Re)
        horizon_drop = (radii_arr**2) / (2.0 * earth_r_eff)
        eff_terrain = elevations + horizon_drop

        # Geometric ray elevation from TX antenna to RX antenna at distance d
        # RX is at eff_terrain[i] + rx_h_agl_m
        rx_abs_h = eff_terrain + rx_h_agl_m
        # Direct ray line altitude at intervening points:
        # A fast, physically accurate single-knife-edge estimation for radial coverage:
        for r_idx in range(num_r):
            fspl = float(fspl_arr[r_idx])
            if r_idx == 0:
                loss_matrix[az_idx, 0] = fspl
                p_rx_matrix[az_idx, 0] = eirp_dbm - fspl
                los_matrix[az_idx, 0] = True
                continue

            diff_loss, has_los = _eval_ray_clearance(
                r_idx=r_idx,
                radii_arr=radii_arr,
                tx_abs_h=tx_abs_h,
                rx_abs_h=rx_abs_h,
                eff_terrain=eff_terrain,
                wavelength_m=wavelength_m,
            )
            los_matrix[az_idx, r_idx] = has_los
            tot_loss = fspl + diff_loss
            loss_matrix[az_idx, r_idx] = tot_loss
            p_rx_matrix[az_idx, r_idx] = eirp_dbm - tot_loss

    return PolarCoverageResult(
        tx_pos=tx_pos,
        tx_eirp_w=tx_eirp_w,
        freq_hz=freq_hz,
        azimuths_deg=azimuths,
        radii_m=radii,
        p_rx_matrix_dbm=p_rx_matrix,
        loss_matrix_db=loss_matrix,
        los_matrix=los_matrix,
    )


def extract_polar_contour_ring(
    coverage: PolarCoverageResult,
    threshold_dbm: float,
) -> list[Position]:
    """Extract a closed boundary polygon ring where P_rx reaches ``threshold_dbm``.

    Along each azimuth ray, finds the outermost distance where P_rx >= threshold_dbm.
    If the signal drops behind an obstacle, the contour pulls inward, reflecting terrain shadow.
    """
    frame = LocalFrame(coverage.tx_pos)
    ring: list[Position] = []

    radii = coverage.radii_m
    min_r = radii[0]

    for az_idx, az_deg in enumerate(coverage.azimuths_deg):
        rad = math.radians(az_deg)
        powers = coverage.p_rx_matrix_dbm[az_idx, :]

        # Find furthest distance where P_rx >= threshold_dbm
        valid_indices = np.nonzero(powers >= threshold_dbm)[0]
        if len(valid_indices) == 0:
            target_r = min_r
        else:
            # Outermost contiguous range or max range with threshold
            last_idx = int(valid_indices[-1])
            target_r = radii[last_idx]

        x = target_r * math.sin(rad)
        y = target_r * math.cos(rad)
        ring.append(frame.to_position(x, y))

    return ring


def polar_coverage_to_geojson(
    coverage: PolarCoverageResult,
    contours: list[ContourLevel] | None = None,
) -> dict[str, Any]:
    """Export polar coverage contours to standard RFC 7946 GeoJSON FeatureCollection.

    Outputs styled GeoJSON Polygons that directly plug into MapLibre GL, Leaflet, or Cesium.
    """
    levels = contours if contours is not None else DEFAULT_CONTOURS
    features: list[dict[str, Any]] = []

    for c in sorted(levels, key=lambda x: x.level_dbm):
        ring = extract_polar_contour_ring(coverage, c.level_dbm)
        if len(ring) < 3:
            continue

        # Close the ring coordinates [lon, lat]
        coords = [[p.lon, p.lat] for p in ring]
        coords.append([ring[0].lon, ring[0].lat])

        feature = {
            "type": "Feature",
            "properties": {
                "name": c.name,
                "level_dbm": c.level_dbm,
                "fill": c.fill_color,
                "fill_opacity": c.fill_opacity,
                "stroke": c.stroke_color,
                "stroke_width": c.stroke_width,
                "tx_lat": coverage.tx_pos.lat,
                "tx_lon": coverage.tx_pos.lon,
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [coords],
            },
        }
        features.append(feature)

    return {
        "type": "FeatureCollection",
        "features": features,
    }


def compute_terrain_aware_jammer_ring(
    dem: DemProvider,
    jammer_pos: Position,
    pilot_pos: Position,
    jammer_eirp_w: float,
    pilot_eirp_w: float,
    band_or_freq_hz: str | float,
    threshold_db: float,
    drone_h_agl_m: float = 20.0,
    jammer_h_agl_m: float = 10.0,
    azimuth_step_deg: float = 5.0,
    max_range_m: float = 8000.0,
    range_step_m: float = 50.0,
) -> list[Position]:
    """Compute the terrain-aware boundary ring where J/S == threshold_db.

    Replaces flat-earth Apollonius circles with terrain-shielded boundary contours.
    """
    freq_hz = (
        parse_band_to_hz(band_or_freq_hz)
        if isinstance(band_or_freq_hz, str)
        else float(band_or_freq_hz)
    )
    frame = LocalFrame(jammer_pos)
    pilot_eirp_dbm = watts_to_dbm(pilot_eirp_w)

    # 1. Compute jammer radial coverage
    jammer_cov = compute_polar_coverage(
        dem=dem,
        tx_pos=jammer_pos,
        tx_eirp_w=jammer_eirp_w,
        band_or_freq_hz=freq_hz,
        tx_h_agl_m=jammer_h_agl_m,
        rx_h_agl_m=drone_h_agl_m,
        max_range_m=max_range_m,
        azimuth_step_deg=azimuth_step_deg,
        range_step_m=range_step_m,
    )

    ring: list[Position] = []

    for az_idx, az_deg in enumerate(jammer_cov.azimuths_deg):
        rad = math.radians(az_deg)
        jammer_powers = jammer_cov.p_rx_matrix_dbm[az_idx, :]

        # Along this ray, find distance where J/S >= threshold_db
        # J/S = P_rx(jammer) - P_rx(pilot)
        boundary_r = jammer_cov.radii_m[0]
        for r_idx, r_m in enumerate(jammer_cov.radii_m):
            p_j = jammer_powers[r_idx]

            # Approximate drone position along ray
            x = r_m * math.sin(rad)
            y = r_m * math.cos(rad)
            drone_p = frame.to_position(x, y)

            # Pilot link distance
            pilot_frame = LocalFrame(pilot_pos)
            px, py = pilot_frame.to_xy(drone_p)
            d_pilot = max(1.0, math.hypot(px, py))
            fspl_pilot = free_space_path_loss_db(d_pilot, freq_hz)
            p_s = pilot_eirp_dbm - fspl_pilot

            js = p_j - p_s
            if js >= threshold_db:
                boundary_r = r_m

        x_b = boundary_r * math.sin(rad)
        y_b = boundary_r * math.cos(rad)
        ring.append(frame.to_position(x_b, y_b))

    return ring


def _heatmap_status_style(js_val: float) -> tuple[str, str, float]:
    """Map J/S ratio in dB to status text, hex color, and opacity."""
    if js_val < -6.0:
        return "SAFE", "#20c997", 0.35
    if js_val < 0.0:
        return "CAUTION", "#fab005", 0.55
    if js_val < 6.0:
        return "CONTENTION", "#fd7e14", 0.65
    return "JAMMED", "#e03131", 0.75


def _make_heatmap_geometry(
    p: Position,
    d_lon: float,
    d_lat: float,
    cells: bool,
) -> dict[str, Any]:
    """Build GeoJSON Point or quad Polygon geometry for a heatmap cell."""
    if cells:
        lon, lat = p.lon, p.lat
        return {
            "type": "Polygon",
            "coordinates": [
                [
                    [round(lon - d_lon, 6), round(lat - d_lat, 6)],
                    [round(lon + d_lon, 6), round(lat - d_lat, 6)],
                    [round(lon + d_lon, 6), round(lat + d_lat, 6)],
                    [round(lon - d_lon, 6), round(lat + d_lat, 6)],
                    [round(lon - d_lon, 6), round(lat - d_lat, 6)],
                ]
            ],
        }
    return {
        "type": "Point",
        "coordinates": [round(p.lon, 6), round(p.lat, 6)],
    }


def compute_js_heatmap_geojson(
    dem: DemProvider,
    pilot_pos: Position,
    pilot_eirp_w: float,
    jammers: list[tuple[str, Position, float]],
    band_or_freq_hz: str | float,
    bounds: tuple[float, float, float, float],
    step_m: float = 250.0,
    drone_h_agl_m: float = 20.0,
    jammer_h_agl_m: float = 10.0,
    cells: bool = False,
) -> dict[str, Any]:
    """Generate a dense regular grid of J/S jamming degree points for GPU heatmap rendering.

    Parameters:
        dem: Digital elevation model provider.
        pilot_pos: Position of ground pilot station.
        pilot_eirp_w: Pilot transmitter EIRP in Watts.
        jammers: Active jammers as (id, position, eirp_w).
        band_or_freq_hz: Link carrier frequency.
        bounds: (min_lat, min_lon, max_lat, max_lon) bounding box.
        step_m: Grid spacing in metres.
        drone_h_agl_m: Evaluated flight altitude above ground level.
    """
    freq_hz = (
        parse_band_to_hz(band_or_freq_hz)
        if isinstance(band_or_freq_hz, str)
        else float(band_or_freq_hz)
    )
    pilot_eirp_dbm = watts_to_dbm(pilot_eirp_w)

    min_lat, min_lon, max_lat, max_lon = bounds
    origin = Position(lat=min_lat, lon=min_lon)
    frame = LocalFrame(origin)

    corner_x, corner_y = frame.to_xy(Position(lat=max_lat, lon=max_lon))
    xs = np.arange(0.0, corner_x + 1e-3, step_m)
    ys = np.arange(0.0, corner_y + 1e-3, step_m)

    grid_pts: list[Position] = []
    xy_pts: list[tuple[float, float]] = []
    for y in ys:
        for x in xs:
            grid_pts.append(frame.to_position(x, y))
            xy_pts.append((float(x), float(y)))

    if not grid_pts:
        return {"type": "FeatureCollection", "features": []}

    elevs = np.array(dem.get_elevations(grid_pts), dtype=np.float64)

    # Pilot link distances and free space path loss
    p_frame = LocalFrame(pilot_pos)
    p_coords = [p_frame.to_xy(p) for p in grid_pts]
    p_dists = np.array([max(1.0, math.hypot(x, y)) for x, y in p_coords], dtype=np.float64)
    c_term = 20.0 * math.log10(4.0 * math.pi / SPEED_OF_LIGHT)
    fspl_const = 20.0 * math.log10(freq_hz) + c_term

    fspl_pilot = 20.0 * np.log10(np.maximum(1.0, p_dists)) + fspl_const
    p_rx_pilot = pilot_eirp_dbm - fspl_pilot

    # For each jammer, evaluate received power at all grid points
    jammer_prx_list: list[np.ndarray] = []
    for _j_id, j_pos, j_eirp_w in jammers:
        j_eirp_dbm = watts_to_dbm(j_eirp_w)
        j_frame = LocalFrame(j_pos)
        j_coords = [j_frame.to_xy(p) for p in grid_pts]
        j_dists = np.array([max(1.0, math.hypot(x, y)) for x, y in j_coords], dtype=np.float64)
        fspl_j = 20.0 * np.log10(np.maximum(1.0, j_dists)) + fspl_const

        # Multi-point terrain diffraction profiling along each ray (t = 0.2, 0.4, 0.6, 0.8)
        j_elev = dem.get_elevation(j_pos)
        t_fractions = [0.2, 0.4, 0.6, 0.8]
        obs_list: list[np.ndarray] = []
        for t in t_fractions:
            t_coords = [((x * t), (y * t)) for x, y in j_coords]
            t_pts = [j_frame.to_position(tx, ty) for tx, ty in t_coords]
            t_elevs = np.array(dem.get_elevations(t_pts), dtype=np.float64)
            ray_h = (
                j_elev + jammer_h_agl_m + t * (elevs + drone_h_agl_m - (j_elev + jammer_h_agl_m))
            )
            obs_list.append(t_elevs - ray_h)

        max_obs = np.maximum.reduce(obs_list)
        # Compute ITU-R knife-edge diffraction loss
        wl_m = SPEED_OF_LIGHT / freq_hz
        geom_factor = np.sqrt(2.0 / (wl_m * np.maximum(1.0, j_dists * 0.25)))
        nu_arr = np.maximum(0.0, max_obs) * geom_factor
        term = np.sqrt(np.maximum(0.0, (nu_arr - 0.1) ** 2) + 1.0) + nu_arr - 0.1
        diff_loss = np.where(max_obs > 0, 6.9 + 20.0 * np.log10(np.maximum(1e-4, term)), 0.0)

        p_rx_j = j_eirp_dbm - (fspl_j + diff_loss)
        jammer_prx_list.append(p_rx_j)

    if jammer_prx_list:
        max_jammer_prx = np.maximum.reduce(jammer_prx_list)
    else:
        max_jammer_prx = np.full(len(grid_pts), -140.0)

    # Physical thermal noise floor for receiver (-105 dBm).
    # Beyond the jammer's physical footprint, jammer power is negligible
    negligible_mask = max_jammer_prx < -105.0
    effective_j_prx = np.where(negligible_mask, -150.0, max_jammer_prx)

    # J/S ratio in dB: where jammer is negligible, J/S is clamped to -50 dB (safe green)
    js_arr = np.where(negligible_mask, -50.0, effective_j_prx - p_rx_pilot)

    # Weight mapping: base 0.12 (Safe J/S <= -6 dB) up to 1.0 (Deadly J/S >= +12 dB)
    # Safe zones behind mountains and far from jammers have base weight that renders as solid GREEN
    weights = 0.12 + 0.88 * np.clip((js_arr + 6.0) / 18.0, 0.0, 1.0)

    half = step_m / 2.0
    d_lat = math.degrees(half / EARTH_RADIUS_M)
    d_lon = math.degrees(half / (EARTH_RADIUS_M * max(1e-6, frame._cos_lat)))

    features: list[dict[str, Any]] = []
    for i, p in enumerate(grid_pts):
        js_val = float(js_arr[i])
        status, color, opacity = _heatmap_status_style(js_val)
        geom = _make_heatmap_geometry(p, d_lon, d_lat, cells)
        features.append(
            {
                "type": "Feature",
                "geometry": geom,
                "properties": {
                    "weight": round(float(weights[i]), 3),
                    "js_db": round(js_val, 1),
                    "status": status,
                    "color": color,
                    "opacity": opacity,
                    "p_jammer_dbm": round(float(max_jammer_prx[i]), 1),
                    "p_pilot_dbm": round(float(p_rx_pilot[i]), 1),
                    "elev_m": round(float(elevs[i]), 0),
                },
            }
        )

    return {"type": "FeatureCollection", "features": features}


def _probe_status(js_db: float) -> str:
    """Map probe J/S ratio to tactical threat status."""
    if js_db < -6.0:
        return "SAFE"
    if js_db < 0.0:
        return "CAUTION"
    if js_db < 6.0:
        return "CONTENTION"
    return "JAMMED"


def _probe_details(shielding_db: float, diff_loss_db: float, jammer_id: str) -> str:
    """Generate human-readable tactical terrain shielding explanation."""
    if shielding_db > 10.0:
        return f"Terrain ridge provides {shielding_db:.1f} dB diffraction shadow from {jammer_id}"
    if diff_loss_db > 0.0:
        return f"Partial terrain obstruction ({diff_loss_db:.1f} dB loss from {jammer_id})"
    return f"Direct line of sight to {jammer_id}"


def probe_point_rf(
    dem: DemProvider,
    target_pos: Position,
    pilot_pos: Position,
    pilot_eirp_w: float,
    jammers: list[tuple[str, Position, float]],
    band_or_freq_hz: str | float,
    drone_h_agl_m: float = 20.0,
    pilot_h_agl_m: float = 2.0,
    jammer_h_agl_m: float = 10.0,
) -> dict[str, Any]:
    """Perform rigorous physical link budget probe at a single target coordinate.

    Computes 1D elevation profiles, ITU-R P.526 knife-edge diffractions, and terrain shielding
    for real-time mouse hover inspection.
    """
    freq_hz = (
        parse_band_to_hz(band_or_freq_hz)
        if isinstance(band_or_freq_hz, str)
        else float(band_or_freq_hz)
    )

    # 1. Pilot link budget
    pilot_prof = extract_elevation_profile(
        dem=dem,
        tx_pos=pilot_pos,
        rx_pos=target_pos,
        step_m=20.0,
    )
    pilot_lb = compute_link_budget(
        profile=pilot_prof,
        tx_eirp_w=pilot_eirp_w,
        freq_hz=freq_hz,
        tx_h_agl_m=pilot_h_agl_m,
        rx_h_agl_m=drone_h_agl_m,
    )

    ground_elev = dem.get_elevation(target_pos)

    # 2. Evaluate all jammers
    worst_jammer_id = "None"
    worst_jammer_lb = None
    max_j_prx = -999.0

    for j_id, j_pos, j_eirp_w in jammers:
        j_prof = extract_elevation_profile(
            dem=dem,
            tx_pos=j_pos,
            rx_pos=target_pos,
            step_m=20.0,
        )
        j_lb = compute_link_budget(
            profile=j_prof,
            tx_eirp_w=j_eirp_w,
            freq_hz=freq_hz,
            tx_h_agl_m=jammer_h_agl_m,
            rx_h_agl_m=drone_h_agl_m,
        )
        if j_lb.p_rx_dbm > max_j_prx:
            max_j_prx = j_lb.p_rx_dbm
            worst_jammer_id = j_id
            worst_jammer_lb = j_lb

    NOISE_FLOOR_DBM = -105.0

    if worst_jammer_lb is None or max_j_prx < NOISE_FLOOR_DBM:
        return {
            "lat": target_pos.lat,
            "lon": target_pos.lon,
            "elev_m": round(ground_elev, 1),
            "status": "SAFE",
            "js_db": -50.0,
            "p_pilot_dbm": round(max(-120.0, pilot_lb.p_rx_dbm), 1),
            "p_jammer_dbm": -150.0,
            "shielding_db": 0.0,
            "worst_jammer_id": worst_jammer_id if worst_jammer_lb else "None",
            "pilot_dist_m": round(pilot_lb.distance_m, 0),
            "jammer_dist_m": (round(worst_jammer_lb.distance_m, 0) if worst_jammer_lb else 0.0),
            "details": "Clear spectrum · Outside jammer reach (0.0 W interference)",
        }

    js_db = worst_jammer_lb.p_rx_dbm - pilot_lb.p_rx_dbm
    shielding_db = worst_jammer_lb.diffraction_loss_db - pilot_lb.diffraction_loss_db

    status = _probe_status(js_db)
    details = _probe_details(shielding_db, worst_jammer_lb.diffraction_loss_db, worst_jammer_id)

    return {
        "lat": target_pos.lat,
        "lon": target_pos.lon,
        "elev_m": round(ground_elev, 1),
        "status": status,
        "js_db": round(js_db, 1),
        "p_pilot_dbm": round(pilot_lb.p_rx_dbm, 1),
        "p_jammer_dbm": round(worst_jammer_lb.p_rx_dbm, 1),
        "shielding_db": round(shielding_db, 1),
        "worst_jammer_id": worst_jammer_id,
        "pilot_dist_m": round(pilot_lb.distance_m, 0),
        "jammer_dist_m": round(worst_jammer_lb.distance_m, 0),
        "details": details,
    }
