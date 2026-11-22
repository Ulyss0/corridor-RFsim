"""3D jamming zones: J/S per cell and level, through the terrain."""

import math
from pathlib import Path

import numpy as np
import pytest

from corridor.domain import terrain, zones3d
from corridor.domain.geo import Grid, LocalFrame, Position
from corridor.domain.rf.diffraction import knife_edge_loss_db
from corridor.domain.scenario import Antenna, Scenario, Terrain
from corridor.domain.zones import mission_constraints, mission_shapes
from corridor.domain.zones3d import (
    Emitter,
    forbidden,
    jamming_zones,
    link_lost,
    loss_field,
    pilot_signal,
    received,
)

ORIGIN = Position(lat=48.0, lon=11.0)
PIXEL_DEG = 1 / 3600
FREQ = 2.4e9


def flat(scenario: Scenario) -> Scenario:
    return scenario.model_copy(update={"terrain": Terrain()})


def test_on_flat_ground_the_zone_is_the_2d_disc(scenario: Scenario) -> None:
    """At the top level every path is clear. Lower down, flat ground already reaches into the
    first Fresnel zone near a 2 m pilot antenna: the zone grows a little past the disc."""
    scenario = flat(scenario)
    m1 = scenario.missions[0]
    grid = mission_constraints(scenario, m1, 100).grid
    js = jamming_zones(scenario, m1, grid)
    disc = next(z for z in mission_shapes(scenario, m1) if z.threat == "J3").disc
    zone = forbidden(scenario, js["J3"])
    top = zone[-1]
    assert zone[0].sum() >= top.sum()
    frame, cs = LocalFrame(m1.launch), grid.cell_m
    ox, oy = frame.to_xy(grid.origin)
    checked = 0
    for row in range(grid.rows):
        for col in range(grid.cols):
            x, y = ox + (col + 0.5) * cs, oy + (row + 0.5) * cs
            depth = disc.radius_m - math.hypot(x - disc.x, y - disc.y)  # > 0 inside
            if abs(depth) > 2 * cs:
                assert top[row, col] == (depth > 0), (row, col, depth)
                checked += 1
    assert checked > 0.9 * grid.rows * grid.cols


def test_only_jammers_on_the_mission_band(scenario: Scenario) -> None:
    m1 = scenario.missions[0]
    grid = mission_constraints(scenario, m1, 100).grid
    assert set(jamming_zones(flat(scenario), m1, grid)) == {"J3", "E1"}  # not the radar R1


def write_raster(path: Path, data: np.ndarray) -> None:
    import rasterio
    from rasterio.transform import from_origin

    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=data.shape[0],
        width=data.shape[1],
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=from_origin(ORIGIN.lon, ORIGIN.lat, PIXEL_DEG, PIXEL_DEG),
    ) as dst:
        dst.write(data.astype(np.float32), 1)


@pytest.fixture
def ridge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Terrain, Grid]:
    """Flat at 500 m with a ridge 100 m high and about 400 m wide, running north to south
    1.0 to 1.4 km east of the west edge; the grid covers the raster."""
    monkeypatch.setattr(terrain, "DATA_DIR", tmp_path)
    data = np.full((120, 120), 500.0)
    data[:, 50:70] = 600.0
    write_raster(tmp_path / "ridge.tif", data)
    south_west = Position(lat=ORIGIN.lat - 120 * PIXEL_DEG, lon=ORIGIN.lon)
    return Terrain(dem="ridge.tif"), Grid(origin=south_west, cell_m=100, cols=24, rows=37)


def test_a_ridge_shields_a_low_drone_not_a_high_one(ridge: tuple[Terrain, Grid]) -> None:
    hilly, grid = ridge
    frame = LocalFrame(grid.origin)
    emitter = Emitter(frame.to_position(450, 1850), eirp_w=2, mast_m=10)
    behind = (18, 19)  # row, col: 1.9 km east, past the ridge
    shielded = loss_field(hilly, grid, emitter.pos, 10, FREQ, "low")
    open_ground = loss_field(Terrain(), grid, emitter.pos, 10, FREQ, "low")
    lowest, highest = 0, len(hilly.levels_m) - 1
    assert shielded[lowest][behind] > open_ground[lowest][behind] + 15
    assert shielded[highest][behind] == pytest.approx(open_ground[highest][behind], abs=1)
    # In front of the ridge nothing changes.
    assert shielded[lowest][18, 2] == pytest.approx(open_ground[lowest][18, 2], abs=1)


def test_the_pilot_link_is_lost_behind_a_ridge_at_low_level(
    scenario: Scenario, ridge: tuple[Terrain, Grid]
) -> None:
    hilly, grid = ridge
    pilot = LocalFrame(grid.origin).to_position(450, 1850)
    mission = scenario.missions[0].model_copy(update={"launch": pilot})
    behind = (18, 19)
    lowest, highest = 0, len(hilly.levels_m) - 1
    on_flat = scenario.model_copy(update={"terrain": Terrain(mode="3d")})
    open_dbm = float(pilot_signal(on_flat, mission, grid)[lowest][behind])
    # A threshold a little below the signal on open ground: only the ridge's shadow falls short.
    mission = mission.model_copy(update={"link_min_dbm": open_dbm - 8})
    on_ridge = scenario.model_copy(update={"terrain": hilly.model_copy(update={"mode": "3d"})})
    lost = link_lost(on_ridge, mission, grid)
    assert lost[lowest][behind]
    assert not lost[highest][behind]
    assert not lost[lowest][18, 2]  # in front of the ridge
    assert not link_lost(on_flat, mission, grid).any()


def test_a_mission_takes_the_link_threshold_of_its_drone(scenario: Scenario) -> None:
    pilot = scenario.pilots[0]
    drone = pilot.drone.model_copy(update={"link_min_dbm": -92.0})
    m = pilot.model_copy(update={"drone": drone}).mission("M9", ORIGIN, ORIGIN, 0)
    assert m.link_min_dbm == -92.0
    assert scenario.missions[0].link_min_dbm == -100.0


def test_loss_fields_are_cached_and_read_only(ridge: tuple[Terrain, Grid]) -> None:
    hilly, grid = ridge
    pos = LocalFrame(grid.origin).to_position(450, 1850)
    field = loss_field(hilly, grid, pos, 10, FREQ, "low")
    assert field.shape == (len(hilly.levels_m), grid.rows, grid.cols)
    assert loss_field(hilly, grid, pos, 10, FREQ, "low") is field
    assert not field.flags.writeable


def test_a_directional_jammer_is_weaker_behind_its_beam() -> None:
    grid = Grid(origin=ORIGIN, cell_m=100, cols=21, rows=21)
    centre = LocalFrame(ORIGIN).to_position(1050, 1050)
    north = Antenna(azimuth_deg=0, beamwidth_deg=60, sidelobe_db=-20)
    power = received(Terrain(), grid, Emitter(centre, 2, 10, north), FREQ, "low")
    ahead, behind = power[0][18, 10], power[0][2, 10]  # 800 m north and 800 m south
    assert ahead - behind == pytest.approx(20, abs=0.01)


@pytest.mark.parametrize("nu", [-2.0, -0.78, -0.5, 0.0, 0.7, 2.4, 10.0])
def test_knife_edge_matches_the_scalar_model(nu: float) -> None:
    vector = zones3d._knife_edge_db(np.array([nu]))[0]
    assert vector == pytest.approx(knife_edge_loss_db(nu), abs=1e-9)


def test_the_demo_zones_take_well_under_a_second_per_mission(scenario: Scenario) -> None:
    import time

    m1 = scenario.missions[0]
    grid = mission_constraints(scenario, m1, 100).grid
    zones3d.loss_field.cache_clear()
    start = time.perf_counter()
    js = jamming_zones(scenario, m1, grid)
    assert time.perf_counter() - start < 3  # about 0.5 s on a laptop; slack for CI
    # Munich at 30 m: the city shadows a 2 m pilot antenna, the zone is wider than the disc.
    per_level = forbidden(scenario, js["J3"]).sum(axis=(1, 2))
    assert per_level[0] > per_level[-1]
