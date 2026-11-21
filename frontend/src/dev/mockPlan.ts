// DEV ONLY: fake `plan.updated` so the map, timeline and cards can be built before the planner
// exists. Straight route that waits on the ground for the first cut; baseline bends north.
import type { Cut, Mission, PlanUpdated, RoutePoint } from '../api/protocol'

export function mockPlan(m: Mission, cuts: Cut[]): PlanUpdated {
  const distM = haversine(m.launch, m.target)
  const flyS = distM / m.cruise_speed_mps
  const cut = [...cuts].sort((a, b) => a.start_s - b.start_s)[0]
  const takeoff = cut ? Math.max(m.departure_s, cut.start_s + 30 - flyS / 2) : m.departure_s
  const optimised: RoutePoint[] = [
    { ...m.launch, t: m.departure_s, action: 'ground', alt_m: null },
    { ...m.launch, t: takeoff, action: 'fly', alt_m: null },
    { ...m.target, t: takeoff + flyS, action: 'hover', alt_m: null },
  ]
  const mid = { lat: (m.launch.lat + m.target.lat) / 2 + 0.035, lon: (m.launch.lon + m.target.lon) / 2 }
  const legS = (haversine(m.launch, mid) + haversine(mid, m.target)) / m.cruise_speed_mps
  const baseline: RoutePoint[] = [
    { ...m.launch, t: m.departure_s, action: 'fly', alt_m: null },
    { ...mid, t: m.departure_s + legS / 2, action: 'fly', alt_m: null },
    { ...m.target, t: m.departure_s + legS, action: 'hover', alt_m: null },
  ]
  const base = (100 * legS) / m.battery_max_s
  const opt = (100 * flyS) / m.battery_max_s
  return {
    type: 'plan.updated',
    mission: m.id,
    revision: 0,
    status: cut?.status === 'confirmed' ? 'approved' : cut ? 'preview' : 'baseline',
    metrics: {
      baseline_battery_used_pct: round(base),
      optimised_battery_used_pct: round(opt),
      saved_pct: round((100 * (base - opt)) / base),
      saved_s: Math.round(legS - flyS),
    },
    baseline_route: baseline,
    optimised_route: optimised,
    profile: [],
  }
}

const round = (x: number) => Math.round(x * 10) / 10

function haversine(a: { lat: number; lon: number }, b: { lat: number; lon: number }): number {
  const r = 6_371_000
  const dLat = ((b.lat - a.lat) * Math.PI) / 180
  const dLon = ((b.lon - a.lon) * Math.PI) / 180
  const h = Math.sin(dLat / 2) ** 2 + Math.cos((a.lat * Math.PI) / 180) * Math.cos((b.lat * Math.PI) / 180) * Math.sin(dLon / 2) ** 2
  return 2 * r * Math.asin(Math.sqrt(h))
}
