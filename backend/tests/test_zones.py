import math

import pytest
from pydantic import ValidationError

from corridor.domain.geo import LocalFrame
from corridor.domain.planner.masks import shape_mask
from corridor.domain.scenario import Relay, Scenario
from corridor.domain.zones import (
    GRID_MARGIN_M,
    RING_VERTICES,
    Beam,
    ThreatShape,
    Zone,
    _ring,
    angle_off_axis,
    azimuth_to_angle,
    js_disc,
    mission_constraints,
    mission_shapes,
    nominal_zones,
)


def mission_zones(scenario: Scenario) -> list[Zone]:
    """Every zone of the first mission: on the demo, every threat concerns it."""
    return mission_constraints(scenario, scenario.missions[0], 100).zones


def js_db(
    pilot: tuple[float, float],
    jammer: tuple[float, float],
    at: tuple[float, float],
    link_w: float,
    jammer_w: float,
) -> float:
    """J/S at ``at`` computed directly, to check the closed form."""
    return 10 * math.log10(
        (jammer_w / math.dist(at, jammer) ** 2) / (link_w / math.dist(at, pilot) ** 2)
    )


@pytest.mark.parametrize(("link_w", "jammer_w"), [(10, 2), (1, 50)])
def test_disc_boundary_is_where_js_reaches_the_threshold(link_w: float, jammer_w: float) -> None:
    pilot, jammer, threshold = (0.0, 0.0), (4000.0, 0.0), 3.0
    disc = js_disc(pilot, jammer, link_w, jammer_w, threshold)
    for i in range(8):
        a = 2 * math.pi * i / 8
        on_ring = (disc.x + disc.radius_m * math.cos(a), disc.y + disc.radius_m * math.sin(a))
        assert js_db(pilot, jammer, on_ring, link_w, jammer_w) == pytest.approx(threshold)


def test_weak_jammer_denies_a_disc_around_itself() -> None:
    disc = js_disc((0.0, 0.0), (4000.0, 0.0), link_eirp_w=10, jammer_eirp_w=2, threshold_db=0)
    assert disc.forbidden == "inside"
    assert (disc.x, disc.y) == pytest.approx((5000.0, 0.0))  # pushed away from the pilot
    assert disc.radius_m == pytest.approx(4000 * math.sqrt(5) / 4)


def test_strong_jammer_leaves_only_a_bubble_around_the_pilot() -> None:
    disc = js_disc((0.0, 0.0), (4000.0, 0.0), link_eirp_w=1, jammer_eirp_w=50, threshold_db=0)
    assert disc.forbidden == "outside"
    assert math.hypot(disc.x, disc.y) < disc.radius_m  # the pilot is inside the bubble


@pytest.mark.parametrize(("link_w", "jammer_w"), [(1, 1), (1.0001, 1)])
def test_boundary_close_to_a_straight_line_is_rejected(link_w: float, jammer_w: float) -> None:
    with pytest.raises(ValueError, match="straight line"):
        js_disc((0.0, 0.0), (4000.0, 0.0), link_w, jammer_w, threshold_db=0)


def test_jammer_on_the_pilot_is_rejected() -> None:
    with pytest.raises(ValueError, match="pilot position"):
        js_disc((0.0, 0.0), (0.5, 0.0), 10, 2, 0)


def test_demo_zones(scenario: Scenario) -> None:
    zones = {z.threat: z for z in mission_zones(scenario)}
    assert set(zones) == {"J3", "E1", "R1"}
    assert zones["J3"].kind == "friendly_jammer"
    assert all(z.forbidden == "inside" for z in zones.values())
    assert all(len(z.ring) == RING_VERTICES for z in zones.values())

    frame = LocalFrame(scenario.missions[0].launch)
    target = frame.to_xy(scenario.missions[0].target)
    ring = [frame.to_xy(p) for p in zones["J3"].ring]
    assert max(x for x, _ in ring) < target[0]  # the target is reachable


def test_jammer_off_the_mission_band_has_no_zone(scenario: Scenario) -> None:
    data = scenario.model_dump(mode="json")
    data["missions"][0]["band"] = data["pilots"][0]["drone"]["band"] = "900MHz"
    zones = mission_zones(Scenario.model_validate(data))
    assert [z.threat for z in zones] == ["R1"]


def test_planning_grid_covers_mission_and_threats_with_margin(scenario: Scenario) -> None:
    grid = mission_constraints(scenario, scenario.missions[0], 100).grid
    x, _ = grid.to_xy(scenario.missions[0].launch)
    assert x == pytest.approx(GRID_MARGIN_M, abs=5)  # the launch point is the westernmost
    for threat in [*scenario.friendly_jammers, *scenario.enemy_threats]:
        assert grid.cell_of(threat.pos) is not None


# --- directional jammers -------------------------------------------------------------------


def with_antenna(scenario: Scenario, **overrides: float) -> Scenario:
    """The demo with J3 directional: 60 degree beam pointing north, -20 dB sidelobes."""
    data = scenario.model_dump(mode="json")
    antenna = {"azimuth_deg": 0, "beamwidth_deg": 60, "sidelobe_db": -20}
    data["friendly_jammers"][0]["antenna"] = antenna
    for key, value in overrides.items():
        if key in antenna:
            antenna[key] = value
        else:
            data["friendly_jammers"][0][key] = value
    return Scenario.model_validate(data)


@pytest.mark.parametrize(
    ("azimuth_deg", "direction"), [(0, (0, 1)), (90, (1, 0)), (180, (0, -1)), (270, (-1, 0))]
)
def test_azimuth_is_clockwise_from_north(
    azimuth_deg: float, direction: tuple[float, float]
) -> None:
    a = azimuth_to_angle(azimuth_deg)
    assert (math.cos(a), math.sin(a)) == pytest.approx(direction, abs=1e-12)


def test_beam_across_north_wraps_around() -> None:
    beam = Beam(x=0, y=0, axis_rad=azimuth_to_angle(350), half_width_rad=math.radians(20))
    at = [(math.sin(math.radians(az)), math.cos(math.radians(az))) for az in (5, 325, 340, 15)]
    assert [beam.contains(x, y) for x, y in at] == [True, False, True, False]


def test_directional_ring_follows_the_main_and_sidelobe_boundaries(scenario: Scenario) -> None:
    directional = with_antenna(scenario)
    mission = directional.missions[0]
    frame = LocalFrame(mission.launch)
    j3 = directional.friendly_jammers[0]
    jammer = frame.to_xy(j3.pos)
    zone = next(z for z in mission_zones(directional) if z.threat == "J3")
    assert zone.forbidden == "inside"
    side_w = j3.eirp_w * 10 ** (-20 / 10)
    for p in zone.ring:
        x, y = frame.to_xy(p)
        off_axis = math.degrees(angle_off_axis(x - jammer[0], y - jammer[1], math.pi / 2))
        if abs(off_axis - 30) < 0.1:
            continue  # on a beam edge, where the boundary steps from one disc to the other
        power = j3.eirp_w if off_axis < 30 else side_w
        js = js_db((0.0, 0.0), jammer, (x, y), mission.link_eirp_w, power)
        assert js == pytest.approx(directional.js_threshold_db, abs=1e-3)


def test_directional_zone_leaves_the_area_behind_the_beam(scenario: Scenario) -> None:
    frame = LocalFrame(scenario.missions[0].launch)
    ring = [
        frame.to_xy(p)
        for p in next(z for z in mission_zones(with_antenna(scenario)) if z.threat == "J3").ring
    ]
    _, jy = frame.to_xy(scenario.friendly_jammers[0].pos)
    assert max(y for _, y in ring) > jy + 2000  # the main beam reaches far north
    assert min(y for _, y in ring) > jy - 300  # only the sidelobe disc to the south


def test_directional_jammer_stronger_than_the_link_is_rejected(scenario: Scenario) -> None:
    with pytest.raises(ValueError, match="k < 1"):
        mission_zones(with_antenna(scenario, eirp_w=50))


@pytest.mark.parametrize(
    "antenna",
    [
        {"azimuth_deg": 0, "beamwidth_deg": 0, "sidelobe_db": -20},
        {"azimuth_deg": 0, "beamwidth_deg": 200, "sidelobe_db": -20},
        {"azimuth_deg": 360, "beamwidth_deg": 60, "sidelobe_db": -20},
        {"azimuth_deg": 0, "beamwidth_deg": 60, "sidelobe_db": 3},
        {"azimuth_deg": 0, "beamwidth_deg": 60},
    ],
)
def test_invalid_antenna_is_rejected(scenario: Scenario, antenna: dict[str, float]) -> None:
    data = scenario.model_dump(mode="json")
    data["friendly_jammers"][0]["antenna"] = antenna
    with pytest.raises(ValidationError):
        Scenario.model_validate(data)


# --- several missions, nominal footprints --------------------------------------------------


def test_a_mission_is_concerned_only_by_the_threats_near_it(two_missions: Scenario) -> None:
    m1, m2 = (mission_constraints(two_missions, m, 100) for m in two_missions.missions)
    assert (m1.jammers, sorted(m1.threats)) == (["J3"], ["E1", "R1"])
    assert (m2.jammers, m2.threats) == (["J4"], [])
    assert {z.threat for z in m2.zones} == {"J4"}
    j3 = two_missions.friendly_jammers[0]
    assert m2.grid.cell_of(j3.pos) is None  # M1's threats do not stretch M2's grid


def test_a_jammer_off_the_band_does_not_concern_the_mission(scenario: Scenario) -> None:
    data = scenario.model_dump(mode="json")
    data["friendly_jammers"][0]["bands"] = ["900MHz"]
    constraints = mission_constraints(Scenario.model_validate(data), scenario.missions[0], 100)
    assert constraints.jammers == []


def test_the_grid_grows_to_cover_a_concerned_threat(scenario: Scenario) -> None:
    # A radar whose disc reaches the launch-target box but whose centre lies past it.
    data = scenario.model_dump(mode="json")
    launch = scenario.missions[0].launch
    far = {"lat": launch.lat + 0.07, "lon": launch.lon}  # about 7.8 km north
    data["enemy_threats"] = [{"type": "radar", "id": "R9", "pos": far, "radius_m": 5000}]
    moved = Scenario.model_validate(data)
    constraints = mission_constraints(moved, moved.missions[0], 100)
    assert constraints.threats == ["R9"]
    assert constraints.grid.cell_of(moved.enemy_threats[0].pos) is not None


def test_nominal_zone_is_where_the_reference_link_is_lost(scenario: Scenario) -> None:
    zones = {z.threat: z for z in nominal_zones(scenario)}
    assert set(zones) == {"J3", "E1", "R1"}
    j3 = scenario.friendly_jammers[0]
    frame = LocalFrame(j3.pos)
    link = scenario.reference_link
    radius = link.range_m * math.sqrt(j3.eirp_w / link.eirp_w)  # 0 dB threshold
    for p in zones["J3"].ring:
        assert math.hypot(*frame.to_xy(p)) == pytest.approx(radius, rel=1e-6)
        # On the ring, a drone range_m from its pilot sees J/S at the threshold.
        js = (j3.eirp_w / math.hypot(*frame.to_xy(p)) ** 2) / (link.eirp_w / link.range_m**2)
        assert 10 * math.log10(js) == pytest.approx(0, abs=1e-6)


def test_nominal_zone_of_a_directional_jammer_follows_its_beam(scenario: Scenario) -> None:
    directional = with_antenna(scenario)
    zone = next(z for z in nominal_zones(directional) if z.threat == "J3")
    frame = LocalFrame(directional.friendly_jammers[0].pos)
    ring = [frame.to_xy(p) for p in zone.ring]
    main = max(y for _, y in ring)
    assert min(y for _, y in ring) == pytest.approx(-main / 10, rel=1e-3)  # -20 dB: radius / 10


# --- position error of enemy threats -------------------------------------------------------


def with_errors(scenario: Scenario, **errors: float) -> Scenario:
    """``scenario`` with ``position_error_m`` set on the enemy threats named."""
    data = scenario.model_dump(mode="json")
    for threat in data["enemy_threats"]:
        if threat["id"] in errors:
            threat["position_error_m"] = errors[threat["id"]]
    return Scenario.model_validate(data)


def shape_of(scenario: Scenario, threat: str) -> ThreatShape:
    return next(s for s in mission_shapes(scenario, scenario.missions[0]) if s.threat == threat)


def around(point: tuple[float, float], radius_m: float, n: int = 16) -> list[tuple[float, float]]:
    """``point`` and ``n`` points ``radius_m`` from it."""
    return [point] + [
        (
            point[0] + radius_m * math.cos(2 * math.pi * i / n),
            point[1] + radius_m * math.sin(2 * math.pi * i / n),
        )
        for i in range(n)
    ]


def test_position_error_defaults_to_zero(scenario: Scenario) -> None:
    assert all(t.position_error_m == 0 for t in scenario.enemy_threats)


def test_negative_position_error_is_rejected(scenario: Scenario) -> None:
    with pytest.raises(ValidationError):
        with_errors(scenario, R1=-1)


def test_radar_disc_grows_by_the_position_error(scenario: Scenario) -> None:
    exact, wide = shape_of(scenario, "R1").disc, shape_of(with_errors(scenario, R1=300), "R1").disc
    assert wide.radius_m == pytest.approx(exact.radius_m + 300)
    assert (wide.x, wide.y) == pytest.approx((exact.x, exact.y))


@pytest.mark.parametrize(("link_w", "jammer_w"), [(10, 2), (10, 1), (1, 50)])
def test_js_disc_holds_for_every_possible_jammer_position(link_w: float, jammer_w: float) -> None:
    pilot, jammer, error = (0.0, 0.0), (4000.0, 0.0), 300.0
    wide = js_disc(pilot, jammer, link_w, jammer_w, 0, error)
    for possible in around(jammer, error):
        d = js_disc(pilot, possible, link_w, jammer_w, 0)
        gap = math.dist((wide.x, wide.y), (d.x, d.y))
        if wide.forbidden == "inside":
            assert gap + d.radius_m <= wide.radius_m + 1e-6  # every possible zone is covered
        else:
            assert gap + wide.radius_m <= d.radius_m + 1e-6  # free from every possible zone


def test_js_disc_margin_is_exact_away_from_the_pilot() -> None:
    wide = js_disc((0.0, 0.0), (4000.0, 0.0), 10, 2, 0, position_error_m=300)
    farther = js_disc((0.0, 0.0), (4300.0, 0.0), 10, 2, 0)
    assert wide.x + wide.radius_m == pytest.approx(farther.x + farther.radius_m)


def test_too_uncertain_strong_jammer_is_rejected() -> None:
    with pytest.raises(ValueError, match="too uncertain"):
        js_disc((0.0, 0.0), (4000.0, 0.0), 1, 50, 0, position_error_m=4000)


def test_enemy_jammer_zone_includes_the_position_error(scenario: Scenario) -> None:
    uncertain = with_errors(scenario, E1=200)
    exact, wide = shape_of(scenario, "E1"), shape_of(uncertain, "E1")
    assert wide.kind == "enemy_jammer"
    assert wide.disc.radius_m > exact.disc.radius_m + 200
    assert shape_of(uncertain, "J3") == shape_of(scenario, "J3")  # friendly zones unchanged


def test_nominal_footprints_grow_by_the_position_error(scenario: Scenario) -> None:
    def radii(s: Scenario) -> dict[str, float]:
        positions = {t.id: t.pos for t in s.enemy_threats}
        return {
            z.threat: max(math.hypot(*LocalFrame(positions[z.threat]).to_xy(p)) for p in z.ring)
            for z in nominal_zones(s)
            if z.threat in positions
        }

    exact, wide = radii(scenario), radii(with_errors(scenario, R1=300, E1=200))
    assert wide["R1"] == pytest.approx(exact["R1"] + 300, abs=1)
    assert wide["E1"] == pytest.approx(exact["E1"] + 200, abs=1)


def with_relay(scenario: Scenario, x: float, y: float, eirp_w: float = 10.0) -> Scenario:
    """``scenario`` with one relay on 2.4 GHz at (x, y) in M1's frame."""
    pos = LocalFrame(scenario.missions[0].launch).to_position(x, y)
    relay = Relay(id="RL1", pos=pos, eirp_w=eirp_w, bands=["2.4GHz"])
    return scenario.model_copy(update={"relays": [relay]})


def test_a_relay_near_the_jammer_shrinks_its_zone(scenario: Scenario) -> None:
    """J/S is the minimum over the connection points: the zone is the intersection of the zone
    against the pilot and the zone against the relay."""
    alone = shape_of(scenario, "J3")
    jx, jy = LocalFrame(scenario.missions[0].launch).to_xy(scenario.friendly_jammers[0].pos)
    relayed = shape_of(with_relay(scenario, jx + 1500, jy), "J3")
    assert len(relayed.relays) == 1 and relayed.origin == (jx, jy)
    assert relayed.contains(jx, jy)
    # Between the jammer and the relay: jammed from the pilot, fine through the relay.
    probe = (jx + 600, jy)
    assert alone.contains(*probe) and not relayed.contains(*probe)
    # Everything the relayed zone forbids, the pilot-only zone forbids too.
    for point in around((jx, jy), alone.disc.radius_m, 64):
        if relayed.contains(*point):
            assert alone.contains(*point)


def test_the_ring_of_a_relayed_zone_lies_inside_both_zones(scenario: Scenario) -> None:
    jx, jy = LocalFrame(scenario.missions[0].launch).to_xy(scenario.friendly_jammers[0].pos)
    relayed = shape_of(with_relay(scenario, jx + 1500, jy), "J3")
    for x, y in _ring(relayed):
        # Just inside the boundary along the ray from the jammer: forbidden by every part.
        inward = (jx + 0.99 * (x - jx), jy + 0.99 * (y - jy))
        assert all(part.contains(*inward) for part in relayed.parts())
    assert len({round(math.hypot(x - jx, y - jy)) for x, y in _ring(relayed)}) > 2


def test_a_relay_off_the_band_changes_nothing(scenario: Scenario) -> None:
    relayed = with_relay(scenario, 0, 2000)
    relay = relayed.relays[0].model_copy(update={"bands": ["5.8GHz"]})
    assert shape_of(relayed.model_copy(update={"relays": [relay]}), "J3") == shape_of(
        scenario, "J3"
    )


def test_a_relay_on_the_jammer_is_left_out(scenario: Scenario) -> None:
    jx, jy = LocalFrame(scenario.missions[0].launch).to_xy(scenario.friendly_jammers[0].pos)
    assert shape_of(with_relay(scenario, jx, jy), "J3") == shape_of(scenario, "J3")


def test_the_relayed_mask_forbids_fewer_cells(scenario: Scenario) -> None:
    m1 = scenario.missions[0]
    jx, jy = LocalFrame(m1.launch).to_xy(scenario.friendly_jammers[0].pos)
    relayed = with_relay(scenario, jx + 1500, jy)
    grid = mission_constraints(scenario, m1, 100).grid
    frame = LocalFrame(m1.launch)
    alone = shape_mask(grid, shape_of(scenario, "J3"), frame)
    shrunk = shape_mask(grid, shape_of(relayed, "J3"), frame)
    assert shrunk.sum() < alone.sum()
    assert not (shrunk & ~alone).any()


def test_relay_ids_must_be_unique(scenario: Scenario) -> None:
    relayed = with_relay(scenario, 0, 0)
    data = relayed.model_dump(mode="json")
    data["relays"][0]["id"] = "J3"
    with pytest.raises(ValidationError, match="duplicate ids"):
        Scenario.model_validate(data)
