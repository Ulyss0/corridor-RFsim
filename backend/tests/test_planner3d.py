"""3D planning: SIPP across flight levels, layered masks, the safety rule per level."""

import itertools

import numpy as np
import pytest

from corridor.domain.events import RoutePoint
from corridor.domain.geo import Grid, LocalFrame, Position
from corridor.domain.planner.astar import earliest_path
from corridor.domain.planner.layers import (
    CLIMB_MPS,
    cells_by_threat,
    forbidden_cells,
    link_cells,
    mission_layers,
)
from corridor.domain.planner.masks import Mask, TimedMasks, layered_timed_masks
from corridor.domain.planner.plan import ENTRY_MARGIN_S, EXIT_MARGIN_S, plan_mission
from corridor.domain.safety import Flight, LevelCells, schedule_violation
from corridor.domain.scenario import Relay, Scenario, Terrain
from corridor.domain.state import Cut
from corridor.domain.zones import mission_constraints
from corridor.domain.zones3d import pilot_signal

FOREVER = 1e6


def static(*levels: Mask) -> list[TimedMasks]:
    return [TimedMasks(edges=[0.0, FOREVER], masks=m[np.newaxis]) for m in levels]


def empty(cols: int, rows: int) -> Mask:
    return np.zeros((rows, cols), dtype=np.bool_)


def cut(jammer: str, start_s: float, end_s: float) -> Cut:
    return Cut(
        id=f"cut-{jammer}",
        jammer=jammer,
        start_s=start_s,
        end_s=end_s,
        status="confirmed",
        requested_by="test",
    )


def three_d(scenario: Scenario) -> Scenario:
    return scenario.model_copy(
        update={"terrain": scenario.terrain.model_copy(update={"mode": "3d"})}
    )


# --- search --------------------------------------------------------------------------------


def test_climbs_over_a_wall_that_only_blocks_the_low_level() -> None:
    low, high = empty(10, 3), empty(10, 3)
    low[:, 5] = True  # a wall across the grid, at the low level only
    steps = earliest_path(static(low, high), (0, 1), (9, 1), 0.0, cell_s=1.0, climb_s=[2.0])
    assert steps is not None
    assert {s.level for s in steps} == {0, 1}
    assert all(s.level == 1 for s in steps if s.cell[0] == 5)
    assert steps[-1].arrive_s == pytest.approx(9 + 2.0)  # 9 cells and one climb


def test_ends_over_the_goal_at_any_level() -> None:
    low, high = empty(10, 3), empty(10, 3)
    low[1, 9] = True  # the goal itself is forbidden low down
    steps = earliest_path(static(low, high), (0, 1), (9, 1), 0.0, cell_s=1.0, climb_s=[2.0])
    assert steps is not None
    assert steps[-1].cell == (9, 1)
    assert steps[-1].level == 1


def test_stays_low_when_climbing_buys_nothing() -> None:
    steps = earliest_path(
        static(empty(10, 3), empty(10, 3)), (0, 1), (9, 1), 0.0, 1.0, climb_s=[2.0]
    )
    assert steps is not None
    assert {s.level for s in steps} == {0}


def test_a_climb_needs_both_levels_free_for_its_duration() -> None:
    low, high = empty(3, 1), empty(3, 1)
    low[0, 1] = True
    # The high level opens at 5 s only: the climb waits for it.
    high_masks = np.stack([np.ones((1, 3), dtype=np.bool_), high])
    layers = [
        TimedMasks(edges=[0.0, 5.0, FOREVER], masks=np.stack([low, low])),
        TimedMasks(edges=[0.0, 5.0, FOREVER], masks=high_masks),
    ]
    steps = earliest_path(layers, (0, 0), (2, 0), 0.0, cell_s=1.0, climb_s=[2.0])
    assert steps is not None
    climb = next(s for s in steps if s.level == 1)
    assert climb.arrive_s >= 5.0 + 2.0


def test_a_drone_in_flight_starts_at_its_level() -> None:
    low, high = empty(5, 1), empty(5, 1)
    low[0, 0] = True  # its cell is forbidden low down: it must not drop there
    steps = earliest_path(static(low, high), (0, 0), (4, 0), 0.0, 1.0, climb_s=[2.0], start_level=1)
    assert steps is not None
    assert steps[0].level == 1


def test_one_climb_time_per_pair_of_levels() -> None:
    with pytest.raises(ValueError, match="one climb time"):
        earliest_path(static(empty(2, 1), empty(2, 1)), (0, 0), (1, 0), 0.0, 1.0)


# --- masks ---------------------------------------------------------------------------------


def test_a_friendly_zone_is_off_during_its_cut_at_every_level() -> None:
    always = np.zeros((2, 1, 3), dtype=np.bool_)
    zone = np.zeros((2, 1, 3), dtype=np.bool_)
    zone[:, 0, 1] = True
    layers = layered_timed_masks(always, {"J1": zone}, [cut("J1", 10, 20)], 0.0, 100.0)
    assert len(layers) == 2
    for timed in layers:
        assert timed.edges == [0.0, 10.0, 20.0, 100.0]
        assert timed.safe_intervals((1, 0)) == [(10.0, 20.0)]
        assert timed.safe_intervals((0, 0)) == [(0.0, 100.0)]


def test_2d_mode_keeps_one_layer_and_no_heights(scenario: Scenario) -> None:
    m1 = scenario.missions[0]
    grid = mission_constraints(scenario, m1, 100).grid
    layers = mission_layers(scenario, m1, grid, [], 0.0, 100.0)
    assert (len(layers.timed), layers.climb_s, layers.altitudes) == (1, [], None)


def test_3d_layers_climb_at_the_climb_speed(scenario: Scenario) -> None:
    scenario = three_d(scenario)
    m1 = scenario.missions[0]
    grid = mission_constraints(scenario, m1, 100).grid
    layers = mission_layers(scenario, m1, grid, [], 0.0, 100.0)
    levels = scenario.terrain.levels_m
    assert len(layers.timed) == len(levels)
    assert layers.altitudes == levels
    assert layers.climb_s == pytest.approx(
        [(b - a) / CLIMB_MPS for a, b in itertools.pairwise(levels)]
    )


# --- planning on the Munich demo -----------------------------------------------------------


@pytest.mark.parametrize("cuts", [[], [cut("J3", 900, 1260)]], ids=["detour", "through J3"])
def test_3d_route_stays_out_of_every_zone_at_its_level(scenario: Scenario, cuts: list[Cut]) -> None:
    scenario = three_d(scenario)
    m1 = scenario.missions[0]
    grid = mission_constraints(scenario, m1, 100).grid
    plan = plan_mission(scenario, m1, grid, cuts)
    assert plan is not None
    route = plan.optimised_route
    levels = scenario.terrain.levels_m
    assert all(p.alt_m in (0.0, *levels) for p in route)
    friendly, always = forbidden_cells(scenario, m1, grid)
    cells = LevelCells(grid, levels, friendly)
    windows = [(c.jammer, c.start_s + ENTRY_MARGIN_S, c.end_s - EXIT_MARGIN_S) for c in cuts]
    frame = LocalFrame(grid.origin)
    for a, b in itertools.pairwise(route):
        if a.action == "ground":
            continue
        for i in range(21):
            t = a.t + (b.t - a.t) * i / 20
            lat, lon = a.lat + (b.lat - a.lat) * i / 20, a.lon + (b.lon - a.lon) * i / 20
            here = Position(lat=lat, lon=lon)
            col, row = (int(v // grid.cell_m) for v in frame.to_xy(here))
            for level in cells.levels_at(route, t):
                assert not always[level, row, col], (t, level)
                for jammer in cells.jammers(here, {level}):
                    assert any(j == jammer and s <= t <= e for j, s, e in windows), (t, jammer)


def test_cells_out_of_the_pilots_reach_are_always_forbidden(scenario: Scenario) -> None:
    scenario = three_d(scenario)
    m1 = scenario.missions[0]
    grid = mission_constraints(scenario, m1, 100).grid
    weak = float(np.median(pilot_signal(scenario, m1, grid)[0]))
    m1 = m1.model_copy(update={"link_min_dbm": weak})
    link = link_cells(scenario, m1, grid)
    assert link[0].mean() >= 0.5  # half the lowest level, widened
    assert link[-1].sum() < link[0].sum()  # higher up, the pilot reaches farther
    _, always = forbidden_cells(scenario, m1, grid)
    assert always[link].all()
    deaf = m1.model_copy(update={"link_min_dbm": -1000.0})
    assert not link_cells(scenario, deaf, grid).any()


def test_3d_uses_the_cut_too(scenario: Scenario) -> None:
    scenario = three_d(scenario)
    m1 = scenario.missions[0]
    grid = mission_constraints(scenario, m1, 100).grid
    plan = plan_mission(scenario, m1, grid, [cut("J3", 900, 1260)])
    assert plan is not None
    assert plan.status == "approved"
    assert plan.metrics.saved_s > 0


# --- safety rule ---------------------------------------------------------------------------


def test_safety_rule_looks_at_the_drone_level() -> None:
    grid = Grid(origin=Position(lat=48.0, lon=11.0), cell_m=100, cols=5, rows=1)
    zone = np.zeros((2, 1, 5), dtype=np.bool_)
    zone[1, 0, 2] = True  # J1 jams cell 2 at the high level only
    cells = LevelCells(grid, (30.0, 60.0), {"J1": zone})
    a, b = grid.to_position(50, 50), grid.to_position(450, 50)

    def flight(alt: float) -> Flight:
        route = [
            RoutePoint(lat=a.lat, lon=a.lon, t=0, action="fly", alt_m=alt),
            RoutePoint(lat=b.lat, lon=b.lon, t=40, action="fly", alt_m=alt),
        ]
        return Flight(route=route, revision=1)

    frame = LocalFrame(grid.origin)
    assert schedule_violation(flight(30.0), 0.0, frame, [], [], cells) is None
    assert schedule_violation(flight(60.0), 0.0, frame, [], [], cells) == "J1"


def test_an_uncertain_enemy_jammer_still_shrinks_with_a_relay(scenario: Scenario) -> None:
    """Each connection point gets its own position margin before the zones are intersected:
    the relayed zone stays within the zone against the pilot alone, and is smaller."""
    data = scenario.model_dump(mode="json")
    for threat in data["enemy_threats"]:
        if threat["id"] == "E1":
            threat["position_error_m"] = 300
    uncertain = Scenario.model_validate(data).model_copy(update={"terrain": Terrain(mode="3d")})
    m1 = uncertain.missions[0]
    grid = mission_constraints(uncertain, m1, 100).grid
    e1 = next(t for t in uncertain.enemy_threats if t.id == "E1")
    ex, ey = LocalFrame(e1.pos).to_xy(e1.pos)
    near = LocalFrame(e1.pos).to_position(ex + 1500, ey)
    relay = Relay(id="RL1", pos=near, eirp_w=m1.link_eirp_w, bands=[m1.band])
    alone = cells_by_threat(uncertain, m1, grid)["E1"][1]
    relayed = cells_by_threat(uncertain.model_copy(update={"relays": [relay]}), m1, grid)["E1"][1]
    assert relayed.sum() < alone.sum()
    assert not (relayed & ~alone).any()
