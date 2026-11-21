import { describe, expect, it } from 'vitest'
import type { Cut, Grid, Position, RoutePoint, Zone } from '../api/protocol'
import {
  bearingDeg,
  clockLabel,
  friendlyZoneState,
  gridBounds,
  inForbidden,
  positionAt,
  routePhases,
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
      { lat: 48.0, lon: 11.0, t: 600, action: 'ground' },
      { lat: 48.0, lon: 11.0, t: 700, action: 'fly' },
      { lat: 48.1, lon: 11.0, t: 800, action: 'hover' },
      { lat: 48.1, lon: 11.0, t: 900, action: 'fly' },
      { lat: 48.1, lon: 11.1, t: 1000, action: 'hover' },
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
        { lat: 48.0, lon: 11.0, t: 600, action: 'ground' },
        { lat: 48.0, lon: 11.0, t: 700, action: 'fly' },
        { lat: 48.1, lon: 11.0, t: 800, action: 'hover' },
      ]
      const phases = routePhases(route, [])
      expect(phases.length).toBeGreaterThan(0)
      expect(phases[0].kind).toBe('ground')
    })
  })
})
