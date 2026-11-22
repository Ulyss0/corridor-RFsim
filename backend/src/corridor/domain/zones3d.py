"""3D jamming zones: J/S per cell and per flight level, with the terrain and its diffraction.

The 2D model (``corridor.domain.zones``) puts each jammer's zone on a disc from free-space
J/S. Here every cell of a mission grid gets, at each flight level of ``Terrain.levels_m``, the
power received from each jammer and from the pilot, through the terrain:

    received = EIRP toward the cell - free-space loss (slant distance) - diffraction loss

Diffraction: a terrain profile from the emitter to the cell centre, sampled every cell along
the way, Earth bulge included (k = 4/3); the obstacle that blocks the most sets a single
knife-edge loss (ITU-R P.526), as in ``corridor.domain.rf.coverage``. The link is lost where

    J/S = J received - S received >= scenario.js_threshold_db

Each choice errs on the side of a larger zone:

- the drone at level L flies L above the cell's ``high`` ground (what it must clear);
- obstacles on a jammer's path come from the ``low`` ground (never higher than they are), so
  jamming is never underestimated;
- obstacles on the pilot link come from the ``centre`` ground (the surface model as it is, roofs
  and trees included). Not ``high``: its one-cell margin is for clearance, and around a pilot
  standing 2 m off the ground it would block the link everywhere.

Antenna heights are not in the scenario yet: ``JAMMER_MAST_M`` and ``PILOT_ANTENNA_M``. The
pilot's matters most: on the Munich demo at 30 m, J3 denies 73 % of M1's grid with a 2 m
antenna (the link is shadowed by the city) and 13 % with a 10 m mast. A
directional jammer uses its main beam EIRP inside the beam and its sidelobe EIRP elsewhere,
with the hard edge of the 2D model.

Losses are cached per emitter, grid and terrain (``loss_field``): a cut changes nothing, a
threat added or removed only computes what is new. On flat ground with every path clear, the
zone at a level is the 2D disc, up to the altitude difference.

Relays (``Scenario.relays`` on the mission band) are connection points beside the pilot: the
drone uses the strongest control signal it receives (``link_signal``), so J/S is the minimum
of the J/S against each connection point. Relay paths use the ``centre`` ground like the pilot's,
with the relay mast (``Relay.mast_m``).

The minimum signal: the link is also lost, jammer or not, where the best control signal is
below ``Mission.link_min_dbm`` (``link_lost``): behind a hill or a tower block seen from the
pilot and every relay, or simply too far from all of them.

Not here: the margin for an enemy jammer's position error and the masks over time (3D masks),
radars (2D discs for now).
"""

from __future__ import annotations

import functools
import math
from dataclasses import dataclass
from typing import Literal

import numpy as np
import numpy.typing as npt
from scipy.ndimage import map_coordinates

from corridor.domain.geo import EARTH_RADIUS_M, Grid, Position
from corridor.domain.rf.diffraction import SPEED_OF_LIGHT, parse_band_to_hz
from corridor.domain.rf.profile import DEFAULT_K_FACTOR
from corridor.domain.scenario import Antenna, EnemyJammer, Mission, Scenario, Terrain
from corridor.domain.terrain import ground_cells, ground_model
from corridor.domain.zones import azimuth_to_angle

JAMMER_MAST_M = 10.0
"""Height of a jammer antenna above its ground, until the scenario gives one."""
PILOT_ANTENNA_M = 2.0
"""Height of the pilot's control antenna above its ground."""
CHUNK_CELLS = 4096
"""Cells per batch, so the profile arrays stay a few tens of MB."""

Field = npt.NDArray[np.float32]
"""Indexed ``[level, row, col]``."""
Obstacles = Literal["low", "centre"]


@dataclass(frozen=True)
class Emitter:
    pos: Position
    eirp_w: float
    mast_m: float
    antenna: Antenna | None = None


def jamming_zones(scenario: Scenario, mission: Mission, grid: Grid) -> dict[str, Field]:
    """J/S in dB per jammer on the mission band, friendly and enemy, ``[level, row, col]``.

    The zone of a jammer at a level is where its J/S reaches ``scenario.js_threshold_db``
    (``forbidden``). Jammers far from the grid are computed too; a caller keeps those that
    concern the mission.
    """
    signal = link_signal(scenario, mission, grid)
    return {j: power - signal for j, power in jammer_powers(scenario, mission, grid).items()}


def jammer_powers(scenario: Scenario, mission: Mission, grid: Grid) -> dict[str, Field]:
    """Power received from each jammer on the mission band, dBm, ``[level, row, col]``."""
    freq = parse_band_to_hz(mission.band)
    powers: dict[str, Field] = {}
    for j in scenario.friendly_jammers:
        if mission.band in j.bands:
            emitter = Emitter(j.pos, j.eirp_w, JAMMER_MAST_M, j.antenna)
            powers[j.id] = received(scenario.terrain, grid, emitter, freq, "low")
    for t in scenario.enemy_threats:
        if isinstance(t, EnemyJammer) and mission.band in t.bands:
            emitter = Emitter(t.pos, t.eirp_w, JAMMER_MAST_M)
            powers[t.id] = received(scenario.terrain, grid, emitter, freq, "low")
    return powers


def forbidden(scenario: Scenario, js_db: Field) -> npt.NDArray[np.bool_]:
    return js_db >= scenario.js_threshold_db


def link_lost(scenario: Scenario, mission: Mission, grid: Grid) -> npt.NDArray[np.bool_]:
    """Cells and levels where the best control signal is below ``mission.link_min_dbm``."""
    return link_signal(scenario, mission, grid) < mission.link_min_dbm


def link_signal(scenario: Scenario, mission: Mission, grid: Grid) -> Field:
    """Strongest control link power received from the pilot or a relay, dBm."""
    signals = [s for _, s in source_signals(scenario, mission, grid)]
    best: Field = functools.reduce(np.maximum, signals)
    return best


def source_signals(scenario: Scenario, mission: Mission, grid: Grid) -> list[tuple[float, Field]]:
    """Each connection point of the mission, pilot first then the relays on its band: its link
    EIRP (watts) and the power received from it, dBm, ``[level, row, col]``."""
    freq = parse_band_to_hz(mission.band)
    out = [(mission.link_eirp_w, pilot_signal(scenario, mission, grid))]
    for r in scenario.relays_on(mission.band):
        relay = Emitter(r.pos, r.eirp_w, r.mast_m)
        out.append((r.eirp_w, received(scenario.terrain, grid, relay, freq, "centre")))
    return out


def pilot_signal(scenario: Scenario, mission: Mission, grid: Grid) -> Field:
    """Control link power received from the pilot, dBm, ``[level, row, col]``."""
    pilot = Emitter(mission.launch, mission.link_eirp_w, PILOT_ANTENNA_M)
    return received(scenario.terrain, grid, pilot, parse_band_to_hz(mission.band), "centre")


def received(
    terrain: Terrain, grid: Grid, emitter: Emitter, freq_hz: float, obstacles: Obstacles
) -> Field:
    """Power received from ``emitter`` at each cell and level, dBm."""
    loss = loss_field(terrain, grid, emitter.pos, emitter.mast_m, freq_hz, obstacles)
    eirp_dbm = 10 * math.log10(emitter.eirp_w * 1000)
    if emitter.antenna is None:
        return (eirp_dbm - loss).astype(np.float32)
    return (_directional_eirp_dbm(grid, emitter, eirp_dbm) - loss).astype(np.float32)


@functools.lru_cache(maxsize=128)
def loss_field(
    terrain: Terrain,
    grid: Grid,
    pos: Position,
    mast_m: float,
    freq_hz: float,
    obstacles: Obstacles,
) -> Field:
    """Path loss from an antenna ``mast_m`` above the ground at ``pos`` to each cell centre,
    the drone at each level above the cell's ``high`` ground, dB. Read-only, shared."""
    ground = ground_cells(terrain, grid)
    terrain_field = ground.low if obstacles == "low" else ground.centre
    ex, ey = grid.to_xy(pos)
    tx_h = ground_model(terrain).get_elevation(pos) + mast_m
    wavelength = SPEED_OF_LIGHT / freq_hz
    cs = grid.cell_m
    cx, cy = np.meshgrid((np.arange(grid.cols) + 0.5) * cs, (np.arange(grid.rows) + 0.5) * cs)
    cx, cy = cx.ravel(), cy.ravel()
    rx_ground = ground.high.ravel()
    levels = np.asarray(terrain.levels_m, dtype=np.float64)
    far = float(np.hypot(cx - ex, cy - ey).max())
    samples = max(8, math.ceil(far / cs))
    f = (np.arange(1, samples) / samples)[np.newaxis, :]  # interior points, fractions
    out = np.empty((len(levels), cx.size), dtype=np.float32)
    for start in range(0, cx.size, CHUNK_CELLS):
        stop = min(start + CHUNK_CELLS, cx.size)
        out[:, start:stop] = _chunk_loss(
            terrain_field,
            cs,
            (ex, ey, tx_h),
            (cx[start:stop], cy[start:stop], rx_ground[start:stop]),
            levels,
            f,
            wavelength,
            freq_hz,
        )
    field = out.reshape(len(levels), grid.rows, grid.cols)
    field.setflags(write=False)
    return field


def _chunk_loss(
    terrain_field: npt.NDArray[np.float64],
    cs: float,
    tx: tuple[float, float, float],
    cells: tuple[npt.NDArray[np.float64], npt.NDArray[np.float64], npt.NDArray[np.float64]],
    levels: npt.NDArray[np.float64],
    f: npt.NDArray[np.float64],
    wavelength: float,
    freq_hz: float,
) -> npt.NDArray[np.float32]:
    ex, ey, tx_h = tx
    cx, cy, rx_ground = cells
    d = np.maximum(np.hypot(cx - ex, cy - ey), 1.0)[:, np.newaxis]
    xs = ex + f * (cx[:, np.newaxis] - ex)
    ys = ey + f * (cy[:, np.newaxis] - ey)
    coords = np.stack([ys / cs - 0.5, xs / cs - 0.5])
    profile = map_coordinates(terrain_field, coords, order=1, mode="nearest")
    d1, d2 = f * d, (1 - f) * d
    profile = profile + d1 * d2 / (2 * DEFAULT_K_FACTOR * EARTH_RADIUS_M)
    scale = np.sqrt(2 * d / (wavelength * d1 * d2))
    loss = np.empty((len(levels), cx.size), dtype=np.float32)
    for i, level in enumerate(levels):
        rx_h = (rx_ground + level)[:, np.newaxis]
        line = tx_h + f * (rx_h - tx_h)
        nu = ((profile - line) * scale).max(axis=1)
        slant = np.hypot(d[:, 0], rx_h[:, 0] - tx_h)
        loss[i] = _fspl_db(slant, freq_hz) + _knife_edge_db(nu)
    return loss


def _fspl_db(distance_m: npt.NDArray[np.float64], freq_hz: float) -> npt.NDArray[np.float64]:
    c_term = 20 * math.log10(4 * math.pi / SPEED_OF_LIGHT)
    return 20 * np.log10(np.maximum(distance_m, 1.0)) + 20 * math.log10(freq_hz) + c_term


def _knife_edge_db(nu: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
    """ITU-R P.526 J(nu), vectorised; 0 dB where the path is clear (nu <= -0.78)."""
    term = np.sqrt((nu - 0.1) ** 2 + 1) + nu - 0.1
    loss = 6.9 + 20 * np.log10(np.maximum(term, 1e-9))
    return np.where(nu > -0.78, loss, 0.0)


def _directional_eirp_dbm(grid: Grid, emitter: Emitter, eirp_dbm: float) -> Field:
    """Main beam EIRP inside the beam, sidelobe EIRP elsewhere, per cell (all levels)."""
    antenna = emitter.antenna
    assert antenna is not None
    ex, ey = grid.to_xy(emitter.pos)
    cs = grid.cell_m
    cx, cy = np.meshgrid((np.arange(grid.cols) + 0.5) * cs, (np.arange(grid.rows) + 0.5) * cs)
    turn = np.arctan2(cy - ey, cx - ex) - azimuth_to_angle(antenna.azimuth_deg)
    off = np.abs(np.arctan2(np.sin(turn), np.cos(turn)))  # as zones.angle_off_axis
    in_beam = off <= math.radians(antenna.beamwidth_deg) / 2
    return np.where(in_beam, eirp_dbm, eirp_dbm + antenna.sidelobe_db).astype(np.float32)
