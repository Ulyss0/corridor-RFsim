"""Terrain-aware radio frequency propagation domain modules.

Phase 1 & 2: DEM ingestion, profile extraction, Earth curvature, and Line-of-Sight analysis.
Phase 3: Fresnel clearance, Knife-Edge diffraction, link budget, and terrain-aware J/S.
Phase 4: Raster coverage grid, polar ray-casting viewshed, and GeoJSON contour export.
Continuous Heatmap: GPU point heatmap grids and real-time hover probing.
"""

from corridor.domain.rf.coverage import (
    DEFAULT_CONTOURS,
    ContourLevel,
    PolarCoverageResult,
    compute_js_heatmap_geojson,
    compute_polar_coverage,
    compute_terrain_aware_jammer_ring,
    extract_polar_contour_ring,
    polar_coverage_to_geojson,
    probe_point_rf,
)
from corridor.domain.rf.dem import DemProvider, HillFeature, RasterDem, SyntheticDem
from corridor.domain.rf.diffraction import (
    DiffractionResult,
    compute_diffraction,
    frequency_to_wavelength,
    fresnel_radius,
    knife_edge_loss_db,
    parse_band_to_hz,
)
from corridor.domain.rf.los import LosResult, compute_los
from corridor.domain.rf.profile import ElevationProfile, ProfilePoint, extract_elevation_profile
from corridor.domain.rf.propagation import (
    JsResult,
    LinkBudgetResult,
    compute_link_budget,
    compute_terrain_aware_js,
    dbm_to_watts,
    free_space_path_loss_db,
    watts_to_dbm,
)

__all__ = [
    "DEFAULT_CONTOURS",
    "ContourLevel",
    "DemProvider",
    "DiffractionResult",
    "ElevationProfile",
    "HillFeature",
    "JsResult",
    "LinkBudgetResult",
    "LosResult",
    "PolarCoverageResult",
    "ProfilePoint",
    "RasterDem",
    "SyntheticDem",
    "compute_diffraction",
    "compute_js_heatmap_geojson",
    "compute_link_budget",
    "compute_los",
    "compute_polar_coverage",
    "compute_terrain_aware_jammer_ring",
    "compute_terrain_aware_js",
    "dbm_to_watts",
    "extract_elevation_profile",
    "extract_polar_contour_ring",
    "free_space_path_loss_db",
    "frequency_to_wavelength",
    "fresnel_radius",
    "knife_edge_loss_db",
    "parse_band_to_hz",
    "polar_coverage_to_geojson",
    "probe_point_rf",
    "watts_to_dbm",
]
