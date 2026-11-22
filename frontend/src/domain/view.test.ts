import { describe, expect, it } from 'vitest'
import type { Cut, Grid, Mission, Position, RouteReason, RoutePoint, Zone } from '../api/protocol'
import {
  LOST_LINK_LABELS,
  reasonTag,
  reasonText,
  bearingDeg,
  clockLabel,
  distanceM,
  formatPosition,
  friendlyZoneState,
  gridBounds,
  inForbidden,
  missionSpan,
  nextClockTime,
  parsePosition,
  positionAt,
  routePhases,
  startsInside,
} from './view'

describe('domain/view', () => {
  const cuts: Cut[] = [
    {
      id: 'c1',
      jammer: 'J3',
      start_s: 900,
      end_s: 1380,
      status: 'scheduled',
      requested_by: 'HQ',
      requested_for: 'M1',
    },
    {
      id: 'c2',
      jammer: 'J4',
      start_s: 1000,
      end_s: 1500,
      status: 'confirmed',
      requested_by: 'HQ',
      requested_for: 'M1',
    },
  ]

  describe('friendlyZoneState', () => {
    it('returns "on" when no cut is active', () => {
      expect(friendlyZoneState('J3', cuts, 500)).toBe('on')
      expect(friendlyZoneState('J3', cuts, 1400)).toBe('on')
    })

    it('returns "pending" when a scheduled cut is active', () => {
      expect(friendlyZoneState('J3', cuts, 950)).toBe('pending')
    })

    it('returns "cut" when a confirmed cut is active', () => {
      expect(friendlyZoneState('J4', cuts, 1100)).toBe('cut')
    })
  })

  describe('bearingDeg', () => {
    it('computes cardinal directions correctly', () => {
      const origin: Position = { lat: 48.0, lon: 11.0 }
      const north: Position = { lat: 48.1, lon: 11.0 }
      const east: Position = { lat: 48.0, lon: 11.1 }
      const south: Position = { lat: 47.9, lon: 11.0 }
      const west: Position = { lat: 48.0, lon: 10.9 }

      expect(bearingDeg(origin, north)).toBeCloseTo(0, 0)
      expect(bearingDeg(origin, east)).toBeCloseTo(90, 0)
      expect(bearingDeg(origin, south)).toBeCloseTo(180, 0)
      expect(bearingDeg(origin, west)).toBeCloseTo(270, 0)
    })

    it('returns null for identical positions', () => {
      const p: Position = { lat: 48.0, lon: 11.0 }
      expect(bearingDeg(p, p)).toBeNull()
    })
  })

  describe('positionAt', () => {
    const route: RoutePoint[] = [
      { lat: 48.0, lon: 11.0, t: 600, action: 'ground', alt_m: null, amsl_m: null },
      { lat: 48.0, lon: 11.0, t: 700, action: 'fly', alt_m: null, amsl_m: null },
      { lat: 48.1, lon: 11.0, t: 800, action: 'hover', alt_m: null, amsl_m: null },
      { lat: 48.1, lon: 11.0, t: 900, action: 'fly', alt_m: null, amsl_m: null },
      { lat: 48.1, lon: 11.1, t: 1000, action: 'hover', alt_m: null, amsl_m: null },
    ]

    it('returns ground action and launch position before takeoff', () => {
      const res = positionAt(route, 500)
      expect(res).not.toBeNull()
      expect(res?.pos).toEqual({ lat: 48.0, lon: 11.0 })
      expect(res?.action).toBe('ground')
    })

    it('interpolates position and heading during fly leg', () => {
      const res = positionAt(route, 750)
      expect(res).not.toBeNull()
      expect(res?.pos.lat).toBeCloseTo(48.05, 4)
      expect(res?.pos.lon).toBeCloseTo(11.0, 4)
      expect(res?.action).toBe('fly')
      expect(res?.headingDeg).toBeCloseTo(0, 0)
    })

    it('returns final position and heading at or after end of route', () => {
      const res = positionAt(route, 1200)
      expect(res).not.toBeNull()
      expect(res?.pos).toEqual({ lat: 48.1, lon: 11.1 })
      expect(res?.action).toBe('hover')
    })
  })

  describe('gridBounds', () => {
    it('returns 4 corners in GeoJSON [lon, lat] order', () => {
      const grid: Grid = {
        origin: { lat: 48.0, lon: 11.0 },
        cell_m: 100,
        cols: 100,
        rows: 100,
      }
      const bounds = gridBounds(grid)
      expect(bounds).toHaveLength(4)
      expect(bounds[0]).toEqual([11.0, 48.0])
      expect(bounds[1][0]).toBeGreaterThan(11.0)
      expect(bounds[2][1]).toBeGreaterThan(48.0)
    })
  })

  describe('clockLabel', () => {
    it('formats ISO start time + seconds offset to HH:MM:SS', () => {
      expect(clockLabel('2026-11-21T14:00:00Z', 0)).toBe('14:00:00')
      expect(clockLabel('2026-11-21T14:00:00Z', 3661)).toBe('15:01:01')
    })
  })

  describe('inForbidden', () => {
    const ring: Position[] = [
      { lat: 48.0, lon: 11.0 },
      { lat: 48.0, lon: 11.2 },
      { lat: 48.2, lon: 11.2 },
      { lat: 48.2, lon: 11.0 },
    ]

    it('checks inside forbidden zone', () => {
      const z: Zone = { threat: 'T1', kind: 'radar', forbidden: 'inside', ring }
      expect(inForbidden(z, { lat: 48.1, lon: 11.1 })).toBe(true)
      expect(inForbidden(z, { lat: 48.5, lon: 11.5 })).toBe(false)
    })

    it('checks outside forbidden zone', () => {
      const z: Zone = { threat: 'T2', kind: 'friendly_jammer', forbidden: 'outside', ring }
      expect(inForbidden(z, { lat: 48.1, lon: 11.1 })).toBe(false)
      expect(inForbidden(z, { lat: 48.5, lon: 11.5 })).toBe(true)
    })
  })

  describe('routePhases', () => {
    it('splits route into ground and flight phases', () => {
      const route: RoutePoint[] = [
        { lat: 48.0, lon: 11.0, t: 600, action: 'ground', alt_m: null, amsl_m: null },
        { lat: 48.0, lon: 11.0, t: 700, action: 'fly', alt_m: null, amsl_m: null },
        { lat: 48.1, lon: 11.0, t: 800, action: 'hover', alt_m: null, amsl_m: null },
      ]
      const phases = routePhases(route, [])
      expect(phases.length).toBeGreaterThan(0)
      expect(phases[0].kind).toBe('ground')
    })
  })
})

describe('mission request helpers', () => {
  const start = '2026-11-21T14:00:00Z'

  it('measures great-circle distance', () => {
    // 0.1 degree of latitude is about 11.1 km anywhere.
    expect(distanceM({ lat: 48.0, lon: 11.0 }, { lat: 48.1, lon: 11.0 })).toBeCloseTo(11_119, -1)
  })

  it('parses pasted coordinates and rejects the rest', () => {
    expect(parsePosition(' 48.1234, 11.5678 ')).toEqual({ lat: 48.1234, lon: 11.5678 })
    expect(parsePosition('48.1 11.5')).toEqual({ lat: 48.1, lon: 11.5 })
    expect(parsePosition('48.1')).toBeNull()
    expect(parsePosition('95, 11')).toBeNull()
    expect(parsePosition('abc, 11')).toBeNull()
    expect(formatPosition({ lat: 48.1, lon: 11.5 })).toBe('48.10000, 11.50000')
  })

  it('turns a clock time into the next simulated time', () => {
    expect(nextClockTime(start, 600, '14:30')).toBe(1800)
    // 14:05 has passed at 14:10: it means tomorrow.
    expect(nextClockTime(start, 600, '14:05')).toBe(300 + 86_400)
    expect(nextClockTime(start, 600, '9:00')).toBe(19 * 3600)
    expect(nextClockTime(start, 600, '25:00')).toBeNull()
    expect(nextClockTime(start, 600, 'soon')).toBeNull()
  })

  it('startsInside: jammer zones holding the launch, radars left out', () => {
    const square: Position[] = [
      { lat: 0, lon: 0 },
      { lat: 0, lon: 1 },
      { lat: 1, lon: 1 },
      { lat: 1, lon: 0 },
    ]
    const jammer: Zone = { threat: 'J1', kind: 'friendly_jammer', forbidden: 'inside', ring: square }
    const radar: Zone = { threat: 'R1', kind: 'radar', forbidden: 'inside', ring: square }
    expect(startsInside([jammer, radar], { lat: 0.5, lon: 0.5 }).map((z) => z.threat)).toEqual(['J1'])
    expect(startsInside([jammer, radar], { lat: 2, lon: 2 })).toEqual([])
  })
})

describe('missionSpan', () => {
  const mission = { departure_s: 600, battery_max_s: 1800 } as Mission

  it('spans the planned route', () => {
    const route: RoutePoint[] = [
      { lat: 48.0, lon: 11.0, alt_m: 0, t: 650, action: 'ground', amsl_m: null },
      { lat: 48.1, lon: 11.0, alt_m: 120, t: 1400, action: 'hover', amsl_m: null },
    ]
    expect(missionSpan(mission, route)).toEqual([650, 1400])
  })

  it('falls back to departure plus full battery without a plan', () => {
    expect(missionSpan(mission)).toEqual([600, 2400])
  })
})

describe('route reasons', () => {
  const label = (t: number) => `T${t}`
  const reason = (r: Partial<RouteReason>): RouteReason => ({
    threat: 'J3',
    kind: 'friendly_jammer',
    effect: 'avoided',
    enter_s: null,
    exit_s: null,
    ...r,
  })

  it('says what the route does about each zone', () => {
    expect(reasonText(reason({ kind: 'enemy_jammer', threat: 'E1' }), label)).toBe('Around enemy jammer E1')
    expect(reasonText(reason({ effect: 'crossed_in_cut', enter_s: 10, exit_s: 20 }), label)).toBe(
      'Through jammer J3 during its cut, T10 to T20',
    )
    expect(reasonText(reason({ effect: 'crossed', enter_s: 1, exit_s: 2 }), label)).toContain('control link at risk')
    expect(reasonText(reason({ kind: 'link', threat: 'LINK', effect: 'climbed_over' }), label)).toBe(
      "Over the area out of the pilot's reach (flies above its zone)",
    )
    expect(reasonTag(reason({ effect: 'crossed_in_cut' }))).toBe('J3 in cut')
  })
})

describe('lost link labels', () => {
  it('defines metadata for all failsafe actions', () => {
    expect(LOST_LINK_LABELS.rth.short).toBe('RTH')
    expect(LOST_LINK_LABELS.continue.short).toBe('Continue')
    expect(LOST_LINK_LABELS.land.short).toBe('Land')
    expect(LOST_LINK_LABELS.rth.color).toBe('blue')
  })
})
