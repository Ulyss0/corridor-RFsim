"""Mission heatmap: the planner's J/S and denied cells, drawn per level."""

import numpy as np
from fastapi.testclient import TestClient

from corridor.adapters.ws.app import create_app
from corridor.domain.geo import Grid, LocalFrame
from corridor.domain.heatmap import (
    CAUTION_RGBA,
    DENIED_RGBA,
    RADAR_RGBA,
    SAFE_RGBA,
    UPSAMPLE,
    MissionHeatmap,
    heatmap_rgba,
    mission_heatmap,
)
from corridor.domain.planner.layers import cells_by_threat
from corridor.domain.planner.masks import shape_mask
from corridor.domain.scenario import Scenario
from corridor.domain.zones import forbidden_shapes, mission_constraints


def grid_of(scenario: Scenario) -> Grid:
    return mission_constraints(scenario, scenario.missions[0], 100).grid


def in_3d(scenario: Scenario) -> Scenario:
    terrain = scenario.terrain.model_copy(update={"mode": "3d"})
    return scenario.model_copy(update={"terrain": terrain})


def test_2d_denied_cells_are_the_planner_masks_of_the_jammers(scenario: Scenario) -> None:
    m1, grid = scenario.missions[0], grid_of(scenario)
    frame = LocalFrame(m1.launch)
    masks = {
        z.threat: shape_mask(grid, z, frame)
        for z in forbidden_shapes(scenario, m1)
        if z.kind != "radar"
    }
    heatmap = mission_heatmap(scenario, m1, grid)
    assert heatmap.denied.shape == (1, grid.rows, grid.cols)
    assert np.array_equal(heatmap.denied[0], masks["J3"] | masks["E1"])
    cut = mission_heatmap(scenario, m1, grid, frozenset({"J3"}))
    assert np.array_equal(cut.denied[0], masks["E1"])
    assert cut.js_db[0].max() < heatmap.js_db[0].max()


def test_3d_denied_cells_are_the_planner_cells_of_the_jammers(scenario: Scenario) -> None:
    scenario = in_3d(scenario)
    m1, grid = scenario.missions[0], grid_of(scenario)
    cells = cells_by_threat(scenario, m1, grid)
    heatmap = mission_heatmap(scenario, m1, grid, frozenset({"J3"}))
    assert heatmap.denied.shape == (len(scenario.terrain.levels_m), grid.rows, grid.cols)
    assert np.array_equal(heatmap.denied, cells["E1"][1])


def test_radars_stay_shown_when_the_pilot_crosses_them(scenario: Scenario) -> None:
    m1, grid = scenario.missions[0], grid_of(scenario)
    r1 = next(z for z in forbidden_shapes(scenario, m1) if z.kind == "radar")
    crossing = m1.model_copy(update={"cross_radars": True})
    heatmap = mission_heatmap(scenario, crossing, grid)
    assert np.array_equal(heatmap.radar[0], shape_mask(grid, r1, LocalFrame(m1.launch)))


def test_image_is_north_up_with_classes_against_the_threshold() -> None:
    rows, cols = 6, 8
    js = np.full((1, rows, cols), -20.0, dtype=np.float32)
    js[0, 0, :] = -3.0  # southern row: caution with a 0 dB threshold
    denied = np.zeros((1, rows, cols), dtype=np.bool_)
    denied[0, rows - 2 :, : cols // 2] = True  # two northern rows, western half
    radar = np.zeros_like(denied)
    radar[0, rows - 2 :, cols - 2 :] = True  # same rows, eastern corner
    radar[0, rows - 1, 0] = True  # under a denied cell: denied wins
    rgba = heatmap_rgba(MissionHeatmap(js, denied, radar), 0, 0.0)
    assert rgba.shape == (rows * UPSAMPLE, cols * UPSAMPLE, 4)
    assert tuple(rgba[0, 0]) == DENIED_RGBA
    assert tuple(rgba[1, 1]) == DENIED_RGBA
    assert tuple(rgba[0, -1]) == RADAR_RGBA
    assert tuple(rgba[-1, 0]) == CAUTION_RGBA
    assert tuple(rgba[rows * UPSAMPLE // 2, cols * UPSAMPLE - 1]) == SAFE_RGBA


def test_heatmap_endpoint_drapes_the_mission_grid(scenario: Scenario) -> None:
    client = TestClient(create_app(scenario, clock_factor=20, cell_m=100))
    response = client.get("/api/heatmap", params={"mission_id": "M1", "off": "J3"})
    assert response.status_code == 200
    data = response.json()
    assert data["image"].startswith("data:image/png;base64,")
    grid = grid_of(scenario)
    (w, n), (e, _), (_, s), _ = data["coordinates"]
    assert (w, s) == (grid.origin.lon, grid.origin.lat)
    assert w < e and s < n
    assert client.get("/api/heatmap", params={"mission_id": "M1", "off": "J3"}).json() == data
    assert client.get("/api/heatmap", params={"level": 1}).status_code == 422
