import math
import struct
import zlib
from pathlib import Path

import numpy as np

from corridor.domain.geo import EARTH_RADIUS_M, Position
from corridor.domain.rf import (
    DEFAULT_CONTOURS,
    HillFeature,
    JsHeatmap,
    RasterDem,
    SyntheticDem,
    compute_js_heatmap,
    compute_los,
    compute_polar_coverage,
    compute_terrain_aware_jammer_ring,
    compute_terrain_aware_js,
    dbm_to_watts,
    extract_elevation_profile,
    extract_polar_contour_ring,
    free_space_path_loss_db,
    frequency_to_wavelength,
    fresnel_radius,
    heatmap_png,
    knife_edge_loss_db,
    parse_band_to_hz,
    polar_coverage_to_geojson,
    watts_to_dbm,
)
from corridor.domain.rf.profile import DEFAULT_K_FACTOR


def test_synthetic_dem_flat() -> None:
    dem = SyntheticDem(base_elevation_m=450.0)
    p = Position(lat=48.145, lon=11.567)
    assert dem.get_elevation(p) == 450.0


def test_synthetic_dem_hill() -> None:
    center = Position(lat=48.150, lon=11.550)
    hill = HillFeature(center=center, radius_m=500.0, height_m=100.0)
    dem = SyntheticDem(base_elevation_m=500.0, hills=[hill])

    # Exactly at hill center, elevation should be base + height = 600m
    assert math.isclose(dem.get_elevation(center), 600.0, abs_tol=1e-3)

    # 1000m away (beyond radius), elevation is base = 500m
    away = Position(lat=48.160, lon=11.550)
    assert math.isclose(dem.get_elevation(away), 500.0, abs_tol=1e-3)


def test_raster_dem_ingestion(tmp_path: Path) -> None:
    tif_path = tmp_path / "test_dem.tif"
    origin_lat, origin_lon = 48.20, 11.50

    # Create synthetic GeoTIFF with a 120m hill in center
    dem = RasterDem.create_test_raster(
        output_path=tif_path,
        origin_lat=origin_lat,
        origin_lon=origin_lon,
        width_px=60,
        height_px=60,
        pixel_size_deg=0.0003,
        base_elevation=400.0,
        hill_center_px=(30, 30),
        hill_radius_px=15.0,
        hill_height_m=120.0,
    )

    # Sample top-left corner
    tl = Position(lat=48.20, lon=11.50)
    assert math.isclose(dem.get_elevation(tl), 400.0, abs_tol=1.0)

    # Sample center where hill is located (row 30, col 30)
    # in geographic: lat = origin_lat - 30 * pixel_size, lon = origin_lon + 30 * pixel_size
    center = Position(lat=48.20 - 30 * 0.0003, lon=11.50 + 30 * 0.0003)
    elev_center = dem.get_elevation(center)
    assert elev_center > 510.0  # ~520m


def test_profile_earth_curvature() -> None:
    dem = SyntheticDem(base_elevation_m=500.0)
    tx = Position(lat=48.100, lon=11.500)
    # Target ~10 km away
    rx = Position(lat=48.190, lon=11.500)

    profile = extract_elevation_profile(dem, tx, rx, step_m=20.0, k_factor=DEFAULT_K_FACTOR)
    total_d = profile.total_distance_m
    assert 9000 < total_d < 11000

    # Midpoint Earth bulge: d * (D - d) / (2 * Re)
    # with D ~ 10,000m, Re = (4/3) * 6371000 ~ 8494666m
    # delta_h ~ 5000 * 5000 / (2 * 8494666) ~ 1.47m
    mid_idx = len(profile.points) // 2
    mid_pt = profile.points[mid_idx]
    expected_bulge = (mid_pt.d_m * (total_d - mid_pt.d_m)) / (
        2.0 * DEFAULT_K_FACTOR * EARTH_RADIUS_M
    )
    assert math.isclose(mid_pt.earth_bulge_m, expected_bulge, abs_tol=1e-3)
    assert math.isclose(mid_pt.effective_elevation_m, 500.0 + expected_bulge, abs_tol=1e-3)


def test_los_clear_flat_terrain() -> None:
    dem = SyntheticDem(base_elevation_m=500.0)
    tx = Position(lat=48.140, lon=11.500)
    rx = Position(lat=48.150, lon=11.500)

    # Over 1.1 km flat plain with 10m mast on TX and 50m drone altitude
    profile = extract_elevation_profile(dem, tx, rx, step_m=20.0)
    los = compute_los(profile, tx_h_agl_m=10.0, rx_h_agl_m=50.0)

    assert los.has_los is True
    assert los.min_clearance_m > 9.5
    assert len(los.obstruction_indices) == 0


def test_los_blocked_by_hill() -> None:
    # Transmitter at lat 48.140, Receiver at lat 48.160 (~2.2 km away)
    tx = Position(lat=48.140, lon=11.500)
    rx = Position(lat=48.160, lon=11.500)

    # 150m high hill placed directly at the midpoint (lat 48.150)
    mid_hill = Position(lat=48.150, lon=11.500)
    hill = HillFeature(center=mid_hill, radius_m=300.0, height_m=150.0)
    dem = SyntheticDem(base_elevation_m=500.0, hills=[hill])

    profile = extract_elevation_profile(dem, tx, rx, step_m=20.0)

    # Case A: Low drone altitude (20m AGL), TX mast 5m AGL -> Ray is obstructed by the 150m hill
    los_blocked = compute_los(profile, tx_h_agl_m=5.0, rx_h_agl_m=20.0)
    assert los_blocked.has_los is False
    assert los_blocked.min_clearance_m < -50.0  # Severely obstructed
    assert len(los_blocked.obstruction_indices) > 0

    # Case B: Drone climbs to 350m AGL -> Clears the hill with line of sight
    # At midpoint (d = D/2): ray = (505 + 850) / 2 = 677.5m > 650m hill
    los_cleared = compute_los(profile, tx_h_agl_m=5.0, rx_h_agl_m=350.0)
    assert los_cleared.has_los is True
    assert los_cleared.min_clearance_m > 0.0
    assert len(los_cleared.obstruction_indices) == 0


def test_los_collocated() -> None:
    dem = SyntheticDem(base_elevation_m=500.0)
    p = Position(lat=48.140, lon=11.500)
    profile = extract_elevation_profile(dem, p, p)
    los = compute_los(profile, tx_h_agl_m=5.0, rx_h_agl_m=10.0)
    assert los.has_los is True
    assert los.min_clearance_m >= 5.0


def test_frequency_and_wavelength() -> None:
    assert math.isclose(parse_band_to_hz("2.4GHz"), 2.4e9)
    assert math.isclose(parse_band_to_hz("868MHz"), 868e6)
    assert math.isclose(parse_band_to_hz("5.8 GHz"), 5.8e9)

    wl_24 = frequency_to_wavelength(2.4e9)
    assert 0.124 < wl_24 < 0.126  # ~0.125m


def test_fresnel_radius() -> None:
    wl = frequency_to_wavelength(2.4e9)  # ~0.1249 m
    # Midpoint of 2000m link: d1 = d2 = 1000m
    # r1 = sqrt(0.1249 * 1000 * 1000 / 2000) = sqrt(62.45) ~ 7.90m
    r1 = fresnel_radius(wavelength_m=wl, d1_m=1000.0, d2_m=1000.0)
    assert 7.8 < r1 < 8.0

    # At endpoint, radius is 0
    assert fresnel_radius(wavelength_m=wl, d1_m=0.0, d2_m=2000.0) == 0.0


def test_knife_edge_diffraction_itu() -> None:
    # Clear path (nu <= -0.78) has 0 dB loss
    assert knife_edge_loss_db(-1.0) == 0.0

    # Grazing incidence (nu = 0.0) standard knife edge is ~6.0 dB
    j_grazing = knife_edge_loss_db(0.0)
    assert 5.8 < j_grazing < 6.2

    # Obstructed paths
    j_1 = knife_edge_loss_db(1.0)
    assert 13.0 < j_1 < 14.0

    j_2 = knife_edge_loss_db(2.0)
    assert 17.5 < j_2 < 20.0


def test_power_conversions_and_fspl() -> None:
    # 1 Watt = 30 dBm, 10 Watts = 40 dBm, 100 mW = 20 dBm
    assert math.isclose(watts_to_dbm(1.0), 30.0, abs_tol=1e-3)
    assert math.isclose(watts_to_dbm(10.0), 40.0, abs_tol=1e-3)
    assert math.isclose(watts_to_dbm(0.1), 20.0, abs_tol=1e-3)

    assert math.isclose(dbm_to_watts(30.0), 1.0, abs_tol=1e-3)
    assert math.isclose(dbm_to_watts(40.0), 10.0, abs_tol=1e-3)

    # FSPL at 1000m, 2.4 GHz
    # FSPL = 20*log10(1000) + 20*log10(2.4e9) - 147.552 ~ 100.05 dB
    fspl = free_space_path_loss_db(1000.0, 2.4e9)
    assert 99.5 < fspl < 100.5


def test_terrain_diffraction_and_js_shielding() -> None:
    # Scenario:
    # Drone is at lat 48.170, lon 11.500 (Altitude 20m AGL)
    # Jammer is at lat 48.160, lon 11.500 (South of drone, EIRP = 50W)
    # Pilot is at lat 48.170, lon 11.530 (East of drone, ~2.2 km, EIRP = 10W)
    #
    # A 200m high hill is at lat 48.165, lon 11.500 (directly between Jammer and Drone).
    # The Pilot -> Drone link runs along lat 48.170 and is completely clear of the hill.
    pilot_pos = Position(lat=48.170, lon=11.530)
    jammer_pos = Position(lat=48.160, lon=11.500)
    drone_pos = Position(lat=48.170, lon=11.500)

    hill = HillFeature(
        center=Position(lat=48.165, lon=11.500),
        radius_m=300.0,
        height_m=200.0,
    )
    dem = SyntheticDem(base_elevation_m=500.0, hills=[hill])

    # Evaluate terrain-aware J/S
    result = compute_terrain_aware_js(
        dem=dem,
        drone_pos=drone_pos,
        drone_h_agl_m=20.0,
        pilot_pos=pilot_pos,
        pilot_eirp_w=10.0,
        jammer_pos=jammer_pos,
        jammer_eirp_w=50.0,
        band_or_freq_hz="2.4GHz",
        threshold_db=0.0,
        pilot_h_agl_m=2.0,
        jammer_h_agl_m=10.0,
        step_m=20.0,
    )

    # Pilot link is unobstructed across flat terrain
    assert result.pilot_link.diffraction_loss_db < 1.0

    # Jammer link is blocked by the 200m hill, incurring massive diffraction loss
    assert result.jammer_link.diffraction_loss_db > 20.0

    # Naive FSPL (Apollonius) would calculate J/S > +10 dB (heavily jammed)
    assert result.fspl_js_db > 10.0

    # But real terrain-aware J/S is heavily reduced by terrain shielding (>20 dB shadow)
    # Resulting in js_db < 0 dB (drone maintains link due to mountain shielding!)
    assert result.terrain_shielding_db > 20.0
    assert result.js_db < result.threshold_db
    assert result.is_jammed is False


def test_polar_coverage_computation() -> None:
    tx = Position(lat=48.140, lon=11.500)
    # 120m hill located 1 km to the north (azimuth 0 deg)
    hill_center = Position(lat=48.149, lon=11.500)
    hill = HillFeature(center=hill_center, radius_m=400.0, height_m=120.0)
    dem = SyntheticDem(base_elevation_m=500.0, hills=[hill])

    cov = compute_polar_coverage(
        dem=dem,
        tx_pos=tx,
        tx_eirp_w=20.0,
        band_or_freq_hz="2.4GHz",
        tx_h_agl_m=10.0,
        rx_h_agl_m=20.0,
        max_range_m=3000.0,
        azimuth_step_deg=10.0,  # 36 rays
        range_step_m=100.0,  # 30 steps
    )

    assert len(cov.azimuths_deg) == 36
    assert len(cov.radii_m) == 30
    assert cov.p_rx_matrix_dbm.shape == (36, 30)
    assert cov.loss_matrix_db.shape == (36, 30)

    # Azimuth 0 (facing north towards hill) should incur heavy shadow behind the hill:
    # Hill is at ~1000m (index ~9). At 2500m (index 24), signal behind hill should be
    # heavily attenuated compared to azimuth 90 (facing east across flat terrain).
    p_rx_behind_hill = cov.p_rx_matrix_dbm[0, 24]
    p_rx_clear_east = cov.p_rx_matrix_dbm[9, 24]  # 90 deg is index 9 (9 * 10 deg)
    assert p_rx_behind_hill < p_rx_clear_east - 15.0  # At least 15 dB shadow


def test_polar_contour_and_geojson_export() -> None:
    tx = Position(lat=48.140, lon=11.500)
    dem = SyntheticDem(base_elevation_m=500.0)

    cov = compute_polar_coverage(
        dem=dem,
        tx_pos=tx,
        tx_eirp_w=10.0,
        band_or_freq_hz="2.4GHz",
        max_range_m=2000.0,
        azimuth_step_deg=15.0,
        range_step_m=100.0,
    )

    # Extract ring at -80 dBm
    ring = extract_polar_contour_ring(cov, threshold_dbm=-80.0)
    assert len(ring) == len(cov.azimuths_deg)

    # Export to GeoJSON FeatureCollection
    geojson = polar_coverage_to_geojson(cov, DEFAULT_CONTOURS)
    assert geojson["type"] == "FeatureCollection"
    assert len(geojson["features"]) == len(DEFAULT_CONTOURS)

    feat0 = geojson["features"][0]
    assert feat0["type"] == "Feature"
    assert feat0["geometry"]["type"] == "Polygon"
    assert len(feat0["geometry"]["coordinates"][0]) == len(cov.azimuths_deg) + 1  # closed ring
    assert "fill" in feat0["properties"]
    assert "stroke" in feat0["properties"]


def test_terrain_aware_jammer_ring_warping() -> None:
    jammer_pos = Position(lat=48.140, lon=11.500)
    pilot_pos = Position(lat=48.140, lon=11.520)

    # Place a hill to the North of the jammer
    hill = HillFeature(center=Position(lat=48.148, lon=11.500), radius_m=300.0, height_m=150.0)
    dem = SyntheticDem(base_elevation_m=500.0, hills=[hill])

    ring = compute_terrain_aware_jammer_ring(
        dem=dem,
        jammer_pos=jammer_pos,
        pilot_pos=pilot_pos,
        jammer_eirp_w=10.0,
        pilot_eirp_w=10.0,
        band_or_freq_hz="2.4GHz",
        threshold_db=0.0,
        drone_h_agl_m=20.0,
        azimuth_step_deg=10.0,
        max_range_m=3000.0,
        range_step_m=100.0,
    )

    assert len(ring) == 36
    # Coordinates form a valid closed polygon loop
    assert all(-90 <= p.lat <= 90 and -180 <= p.lon <= 180 for p in ring)


def test_js_heatmap_raster_jammed_near_jammer_safe_near_pilot() -> None:
    pilot = Position(lat=48.10, lon=11.50)
    jammer = Position(lat=48.10, lon=11.60)
    heatmap = compute_js_heatmap(
        dem=SyntheticDem(base_elevation_m=500.0),
        pilot_pos=pilot,
        pilot_eirp_w=1.0,
        jammers=[("J1", jammer, 10.0)],
        band_or_freq_hz="2.4GHz",
        bounds=(48.08, 11.49, 48.12, 11.61),
        step_m=100.0,
    )
    rows, cols = heatmap.js_db.shape
    width_m = math.radians(0.12) * EARTH_RADIUS_M * math.cos(math.radians(48.08))
    assert cols == math.ceil(width_m / 100.0)
    assert rows == math.ceil(math.radians(0.04) * EARTH_RADIUS_M / 100.0)
    middle = heatmap.js_db[rows // 2]
    assert middle[0] < -6.0  # next to the pilot
    assert middle[-1] >= 6.0  # next to the jammer


def test_heatmap_png_has_one_pixel_per_value_colored_by_class() -> None:
    js = np.array([[-10.0, -6.0, 0.0], [5.9, 6.0, 30.0]])
    png = heatmap_png(JsHeatmap(js_db=js, west=0.0, south=0.0, east=1.0, north=1.0))

    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", png[16:24])
    assert (width, height) == (3, 2)
    idat_len = struct.unpack(">I", png[33:37])[0]
    raw = zlib.decompress(png[41 : 41 + idat_len])
    pixels = np.frombuffer(raw, dtype=np.uint8).reshape(2, 1 + 3 * 4)[:, 1:].reshape(2, 3, 4)
    safe, caution, contention, jammed = (32, 201, 151), (250, 176, 5), (253, 126, 20), (224, 49, 49)
    assert [tuple(p[:3]) for p in pixels[0]] == [safe, caution, contention]
    assert [tuple(p[:3]) for p in pixels[1]] == [contention, jammed, jammed]
