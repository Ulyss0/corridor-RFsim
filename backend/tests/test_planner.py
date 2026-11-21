import itertools
import math

import numpy as np
import pytest

from corridor.domain.events import RoutePoint
from corridor.domain.geo import Cell, Grid, LocalFrame, Position
from corridor.domain.planner.astar import Step, earliest_path
from corridor.domain.planner.masks import Mask, TimedMasks, disc_mask, shape_mask, timed_masks
from corridor.domain.planner.plan import (
    ENTRY_MARGIN_S,
    EXIT_MARGIN_S,
    MissionPlan,
    flight_s,
    plan_mission,
)
from corridor.domain.scenario import Scenario
from corridor.domain.state import Cut, CutStatus
from corridor.domain.zones import (
    Beam,
    Disc,
    ThreatShape,
    azimuth_to_angle,
    mission_constraints,
    mission_shapes,
)

SQRT2 = math.sqrt(2)
FOREVER = 1e6
CELL_M = 100


def grid_of(scenario: Scenario) -> Grid:
    return mission_constraints(scenario, scenario.missions[0], CELL_M).grid


def shapes_of(scenario: Scenario) -> list[ThreatShape]:
    return mission_shapes(scenario, scenario.missions[0])


def empty(cols: int, rows: int) -> Mask:
    return np.zeros((rows, cols), dtype=np.bool_)


def static(mask: Mask) -> TimedMasks:
    return TimedMasks(edges=[0.0, FOREVER], masks=mask[np.newaxis])


def cut(jammer: str, start_s: float, end_s: float, status: CutStatus = "confirmed") -> Cut:
    return Cut(
        id=f"cut-{jammer}",
        jammer=jammer,
        start_s=start_s,
        end_s=end_s,
        status=status,
        requested_by="test",
    )


def cells(steps: list[Step] | None) -> list[Cell]:
    assert steps is not None
    return [s.cell for s in steps]


def assert_steps_are_safe(timed: TimedMasks, steps: list[Step], cell_s: float) -> None:
    """Every cell is free while the drone hovers in it or flies from or to it."""

    def free(cell: Cell, t1: float, t2: float) -> bool:
        for k, (a, b) in enumerate(itertools.pairwise(timed.edges)):
            if a < t2 and t1 < b and timed.masks[k, cell[1], cell[0]]:
                return False
        return True

    for step in steps:
        assert free(step.cell, step.arrive_s, step.depart_s)
    for a, b in itertools.pairwise(steps):
        assert b.arrive_s - a.depart_s == pytest.approx(cell_s * math.dist(a.cell, b.cell))
        assert free(a.cell, a.depart_s, b.arrive_s)
        assert free(b.cell, a.depart_s, b.arrive_s)


# --- masks ---------------------------------------------------------------------------------


def small_grid() -> tuple[Grid, LocalFrame]:
    grid = Grid(origin=Position(lat=48.0, lon=11.0), cell_m=100, cols=20, rows=20)
    return grid, grid.frame


def test_inside_disc_forbids_every_cell_it_may_touch() -> None:
    grid, frame = small_grid()
    mask = disc_mask(grid, Disc(x=1000, y=1000, radius_m=300, forbidden="inside"), frame)
    assert mask[9, 9]  # centre 50 m from the disc centre
    assert mask[9, 13]  # centre 350 m away: within half a diagonal (71 m) of the boundary
    assert not mask[9, 14]  # centre 450 m away
    assert mask.sum() == pytest.approx(math.pi * (300 + 71) ** 2 / 100**2, rel=0.15)


def test_outside_disc_forbids_everything_but_the_bubble_core() -> None:
    grid, frame = small_grid()
    mask = disc_mask(grid, Disc(x=1000, y=1000, radius_m=300, forbidden="outside"), frame)
    assert not mask[9, 9]
    assert mask[9, 12]  # centre 250 m away: the cell may reach past the 300 m boundary
    assert mask[0, 0]


@pytest.mark.parametrize("azimuth_deg", [0, 45, 200])
def test_directional_mask_forbids_every_cell_that_touches_the_zone(azimuth_deg: float) -> None:
    grid, frame = small_grid()
    beam = Beam(
        x=1010, y=980, axis_rad=azimuth_to_angle(azimuth_deg), half_width_rad=math.radians(25)
    )
    zone = ThreatShape(
        "J",
        "friendly_jammer",
        Disc(x=1100, y=1000, radius_m=700, forbidden="inside"),
        beam,
        Disc(x=1020, y=980, radius_m=150, forbidden="inside"),
    )
    mask = shape_mask(grid, zone, frame)
    touching = np.zeros_like(mask)
    samples = np.linspace(0, grid.cell_m, 11)
    for row, col in itertools.product(range(grid.rows), range(grid.cols)):
        touching[row, col] = any(
            zone.contains(col * grid.cell_m + dx, row * grid.cell_m + dy)
            for dx, dy in itertools.product(samples, samples)
        )
    assert not (touching & ~mask).any()  # safe: no touching cell is left free
    assert mask.sum() <= 1.6 * touching.sum()  # and not much more than needed


def test_friendly_zone_is_off_during_its_cut_only(scenario: Scenario) -> None:
    grid = grid_of(scenario)
    zones = shapes_of(scenario)
    frame = LocalFrame(scenario.missions[0].launch)
    timed = timed_masks(grid, zones, frame, [cut("J3", 900, 1260)], 600, 2400)
    assert timed.edges == [600, 900, 1260, 2400]
    j3 = grid.cell_of(scenario.friendly_jammers[0].pos)
    assert j3 is not None
    assert [bool(m[j3[1], j3[0]]) for m in timed.masks] == [True, False, True]
    assert timed.masks.sum(axis=(1, 2)).tolist()[0] == timed.masks.sum(axis=(1, 2)).tolist()[2]


# --- search, static obstacles ----------------------------------------------------------------


def test_open_grid_gives_the_octile_distance() -> None:
    steps = earliest_path(static(empty(10, 10)), (0, 0), (9, 4), 0.0, cell_s=1.0)
    path = cells(steps)
    assert (path[0], path[-1]) == ((0, 0), (9, 4))
    assert steps is not None
    assert steps[-1].arrive_s == pytest.approx(5 + 4 * SQRT2)
    assert all(max(abs(a[0] - b[0]), abs(a[1] - b[1])) == 1 for a, b in itertools.pairwise(path))


def test_path_goes_through_the_gap_in_a_wall() -> None:
    mask = empty(10, 10)
    mask[:, 5] = True
    mask[8, 5] = False
    path = cells(earliest_path(static(mask), (0, 0), (9, 0), 0.0, 1.0))
    assert (5, 8) in path
    assert not any(mask[r, c] for c, r in path)


def test_no_path_through_a_closed_wall() -> None:
    mask = empty(10, 10)
    mask[:, 5] = True
    assert earliest_path(static(mask), (0, 0), (9, 0), 0.0, 1.0) is None


def test_forbidden_start_or_goal_has_no_path() -> None:
    mask = empty(5, 5)
    mask[0, 0] = True
    assert earliest_path(static(mask), (0, 0), (4, 4), 0.0, 1.0) is None
    assert earliest_path(static(mask), (4, 4), (0, 0), 0.0, 1.0) is None


def test_diagonal_does_not_cut_a_forbidden_corner() -> None:
    mask = empty(3, 3)
    mask[1, 0] = True  # cell (0, 1)
    assert cells(earliest_path(static(mask), (0, 0), (1, 1), 0.0, 1.0)) == [
        (0, 0),
        (1, 0),
        (1, 1),
    ]


# --- search over time ----------------------------------------------------------------------


def wall_with_window(opens: float, closes: float) -> TimedMasks:
    """A 10 x 3 corridor cut by a wall at column 5, open between ``opens`` and ``closes``."""
    wall = empty(10, 3)
    wall[:, 5] = True
    return TimedMasks(
        edges=[0.0, opens, closes, FOREVER],
        masks=np.stack([wall, empty(10, 3), wall]),
    )


def test_drone_hovers_until_the_wall_opens() -> None:
    timed = wall_with_window(opens=20, closes=40)
    steps = earliest_path(timed, (0, 1), (9, 1), 0.0, cell_s=1.0)
    assert steps is not None
    assert_steps_are_safe(timed, steps, 1.0)
    assert steps[-1].arrive_s == pytest.approx(20 + 5)  # leaves (4, 1) as the wall opens
    hovering = [s for s in steps if s.depart_s > s.arrive_s]
    assert len(hovering) == 1


def test_window_too_short_to_cross_has_no_path() -> None:
    # Crossing the wall column takes two moves of 1 s, both inside the window.
    assert earliest_path(wall_with_window(20, 21.5), (0, 1), (9, 1), 0.0, 1.0) is None
    assert earliest_path(wall_with_window(20, 22), (0, 1), (9, 1), 0.0, 1.0) is not None


def test_no_path_after_the_masks_end() -> None:
    timed = wall_with_window(opens=20, closes=40)
    late = TimedMasks(edges=[0.0, 20.0, 23.0], masks=timed.masks[:2])
    assert earliest_path(late, (0, 1), (9, 1), 0.0, 1.0) is None


# --- routes on the demo scenario -----------------------------------------------------------


def assert_route_is_clear(scenario: Scenario, cuts: list[Cut], route: list[RoutePoint]) -> None:
    """Timing at cruise speed, and clearance of the exact zones at every instant.

    A friendly zone counts as off only inside its cut shrunk by the safety margins.
    """
    mission = scenario.missions[0]
    grid = grid_of(scenario)
    frame = LocalFrame(mission.launch)
    xy = [frame.to_xy(Position(lat=p.lat, lon=p.lon)) for p in route]
    assert xy[0] == pytest.approx((0, 0), abs=1)
    assert xy[-1] == pytest.approx(frame.to_xy(mission.target), abs=1)
    assert route[0].t == mission.departure_s
    assert all(p.action != "ground" for p in route[1:])
    assert flight_s(route) <= mission.battery_max_s

    grid_xy = [grid.to_xy(Position(lat=p.lat, lon=p.lon)) for p in route]  # frame of the timing
    for (a, b), (pa, pb) in zip(
        itertools.pairwise(grid_xy), itertools.pairwise(route), strict=True
    ):
        if pa.action in ("hover", "ground"):
            assert a == pytest.approx(b)
        else:
            assert pb.t - pa.t == pytest.approx(math.dist(a, b) / mission.cruise_speed_mps)

    zones = shapes_of(scenario)
    for (a, b), (pa, pb) in zip(itertools.pairwise(xy), itertools.pairwise(route), strict=True):
        if pa.action == "ground":
            continue
        samples = max(1, math.ceil(math.dist(a, b) / 10), math.ceil(pb.t - pa.t))
        for i in range(samples + 1):
            x = a[0] + (b[0] - a[0]) * i / samples
            y = a[1] + (b[1] - a[1]) * i / samples
            t = pa.t + (pb.t - pa.t) * i / samples
            for zone in zones:
                off = zone.kind == "friendly_jammer" and any(
                    c.jammer == zone.threat
                    and c.start_s + ENTRY_MARGIN_S <= t <= c.end_s - EXIT_MARGIN_S
                    for c in cuts
                )
                assert off or not zone.contains(x, y), (x, y, t, zone.threat)


def plan(scenario: Scenario, cuts: list[Cut]) -> MissionPlan:
    result = plan_mission(scenario, scenario.missions[0], grid_of(scenario), cuts)
    assert result is not None
    assert_route_is_clear(scenario, [], result.baseline_route)
    assert_route_is_clear(scenario, cuts, result.optimised_route)
    return result


def direct_s(scenario: Scenario) -> float:
    mission = scenario.missions[0]
    frame = LocalFrame(mission.launch)
    return math.hypot(*frame.to_xy(mission.target)) / mission.cruise_speed_mps


def test_no_cut_gives_the_detour(scenario: Scenario) -> None:
    result = plan(scenario, [])
    assert result.status == "baseline"
    assert result.optimised_route == result.baseline_route
    assert result.metrics.saved_s == 0
    assert flight_s(result.baseline_route) > 1.2 * direct_s(scenario)  # J3 is on the line


def test_confirmed_cut_waits_on_the_ground_and_crosses_j3(scenario: Scenario) -> None:
    result = plan(scenario, [cut("J3", 900, 1260)])
    assert result.status == "approved"
    assert result.optimised_route[0].action == "ground"
    assert result.metrics.saved_pct > 5

    frame = LocalFrame(scenario.missions[0].launch)
    j3 = next(z.disc for z in shapes_of(scenario) if z.threat == "J3")
    xy = [frame.to_xy(Position(lat=p.lat, lon=p.lon)) for p in result.optimised_route]
    samples = [
        (a[0] + (b[0] - a[0]) * i / 100, a[1] + (b[1] - a[1]) * i / 100)
        for a, b in itertools.pairwise(xy)
        for i in range(101)
    ]
    assert any(math.hypot(x - j3.x, y - j3.y) < j3.radius_m for x, y in samples)


def test_metrics_compare_flight_times(scenario: Scenario) -> None:
    result = plan(scenario, [cut("J3", 900, 1260)])
    baseline_s = flight_s(result.baseline_route)
    optimised_s = flight_s(result.optimised_route)
    battery_s = scenario.missions[0].battery_max_s
    metrics = result.metrics
    assert metrics.baseline_battery_used_pct == pytest.approx(100 * baseline_s / battery_s)
    assert metrics.optimised_battery_used_pct == pytest.approx(100 * optimised_s / battery_s)
    assert metrics.saved_s == pytest.approx(baseline_s - optimised_s)
    assert metrics.saved_pct == pytest.approx(100 * (baseline_s - optimised_s) / baseline_s)


def test_long_cut_gives_a_nearly_straight_route(scenario: Scenario) -> None:
    result = plan(scenario, [cut("J3", 900, 1500)])
    assert result.status == "approved"
    assert flight_s(result.optimised_route) < 1.05 * direct_s(scenario)


def test_scheduled_cut_gives_a_preview(scenario: Scenario) -> None:
    scheduled = plan(scenario, [cut("J3", 900, 1260, status="scheduled")])
    confirmed = plan(scenario, [cut("J3", 900, 1260)])
    assert scheduled.status == "preview"
    assert scheduled.metrics == confirmed.metrics


def test_cut_shorter_than_the_margins_is_ignored(scenario: Scenario) -> None:
    # 90 s of cut, minus 30 s to enter and 60 s to leave: nothing left.
    assert plan(scenario, [cut("J3", 900, 990)]).status == "baseline"


def test_cut_over_before_departure_is_ignored(scenario: Scenario) -> None:
    assert plan(scenario, [cut("J3", 0, 500)]).status == "baseline"


def test_target_inside_a_radar_has_no_plan(scenario: Scenario) -> None:
    data = scenario.model_dump(mode="json")
    data["missions"][0]["target"] = data["enemy_threats"][0]["pos"]  # R1
    blocked = Scenario.model_validate(data)
    assert plan_mission(blocked, blocked.missions[0], grid_of(blocked), []) is None


def test_directional_jammer_lets_the_route_pass_behind_its_beam(scenario: Scenario) -> None:
    data = scenario.model_dump(mode="json")
    data["friendly_jammers"][0]["antenna"] = {
        "azimuth_deg": 0,
        "beamwidth_deg": 60,
        "sidelobe_db": -20,
    }
    directional = Scenario.model_validate(data)
    result = plan(directional, [])
    assert flight_s(result.baseline_route) < 1.05 * direct_s(directional)
