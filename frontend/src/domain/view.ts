// Pure helpers to render any instant from the snapshot, the cuts and the last plan
// (see "Rendering on the front" in docs/architecture.md). No React, no map.
import type { ChoiceKind, Cut, Grid, LostLinkAction, Mission, MissionView, Position, RouteChoice, RoutePoint, RouteReason, Scenario, Zone } from '../api/protocol'
import type { ObjectKind } from '../store'

export type ZoneState = 'on' | 'cut' | 'pending'

const REASON_KIND: Record<RouteReason['kind'], string> = {
  friendly_jammer: 'jammer',
  enemy_jammer: 'enemy jammer',
  radar: 'radar',
  avoid: 'zone to avoid',
  link: '',
}

/** One line on why the route goes where it goes, e.g. "Around enemy jammer E1". */
export function reasonText(r: RouteReason, label: (t: number) => string): string {
  const name = r.kind === 'link' ? "the area out of the pilot's reach" : `${REASON_KIND[r.kind]} ${r.threat}`
  const when = r.enter_s !== null && r.exit_s !== null ? `, ${label(r.enter_s)} to ${label(r.exit_s)}` : ''
  switch (r.effect) {
    case 'avoided':
      return `Around ${name}`
    case 'climbed_over':
      return `Over ${name} (flies above its zone)`
    case 'crossed_in_cut':
      return `Through ${name} during its cut${when}`
    case 'crossed':
      if (r.kind === 'radar') return `Through ${name}: seen${when}`
      if (r.kind === 'avoid') return `Through ${name}${when}`
      return `Through ${name} while it is on${when}: control link at risk`
  }
}

/** Short tag of a reason, for a route choice: "around E1", "J3 in cut", "over E2". */
export function reasonTag(r: RouteReason): string {
  const id = r.kind === 'link' ? 'no-link area' : r.threat
  switch (r.effect) {
    case 'avoided':
      return `around ${id}`
    case 'climbed_over':
      return `over ${id}`
    case 'crossed_in_cut':
      return `${id} in cut`
    case 'crossed':
      return `through ${id}`
  }
}

/** Friendly jammer state at `t`: a confirmed cut turns it off, a scheduled one is only pending. */
export function friendlyZoneState(jammerId: string, cuts: Cut[], t: number): ZoneState {
  const covering = cuts.filter((c) => c.jammer === jammerId && c.start_s <= t && t < c.end_s)
  if (covering.some((c) => c.status === 'confirmed')) return 'cut'
  if (covering.length > 0) return 'pending'
  return 'on'
}

function interpolateSegment(a: RoutePoint, b: RoutePoint, t: number) {
  const f = b.t === a.t ? 0 : (t - a.t) / (b.t - a.t)
  const moving = a.action === 'fly'
  return {
    pos: moving ? { lat: a.lat + f * (b.lat - a.lat), lon: a.lon + f * (b.lon - a.lon) } : a,
    action: a.action,
    headingDeg: moving ? bearingDeg(a, b) : null,
  }
}

/** Drone position along a timed route; the action holds until the next point. */
export function positionAt(
  route: RoutePoint[],
  t: number,
): { pos: Position; action: RoutePoint['action']; headingDeg?: number | null } | null {
  if (route.length === 0) return null
  if (t <= route[0].t) {
    const headingDeg = route.length > 1 ? bearingDeg(route[0], route[1]) : null
    return { pos: { lat: route[0].lat, lon: route[0].lon }, action: 'ground', headingDeg }
  }
  for (let i = 0; i < route.length - 1; i++) {
    const a = route[i]
    const b = route[i + 1]
    if (t >= a.t && t <= b.t) {
      return interpolateSegment(a, b, t)
    }
  }
  const last = route.at(-1)!
  const prev = route.at(-2) ?? null
  return {
    pos: { lat: last.lat, lon: last.lon },
    action: last.action,
    headingDeg: prev ? bearingDeg(prev, last) : null,
  }
}

/** Bearing in degrees clockwise from north (0..360) from `a` to `b`. */
export function bearingDeg(a: Position, b: Position): number | null {
  const dLat = b.lat - a.lat
  const dLon = (b.lon - a.lon) * Math.cos(((a.lat + b.lat) * Math.PI) / 360)
  if (Math.abs(dLat) < 1e-9 && Math.abs(dLon) < 1e-9) return null
  return ((Math.atan2(dLon, dLat) * 180) / Math.PI + 360) % 360
}

const M_PER_DEG_LAT = 111_320

/** Corners of the planning grid (origin = south-west corner), as [lon, lat] for GeoJSON. */
export function gridBounds(grid: Grid): [number, number][] {
  const { lat, lon } = grid.origin
  const dLat = (grid.rows * grid.cell_m) / M_PER_DEG_LAT
  const dLon = (grid.cols * grid.cell_m) / (M_PER_DEG_LAT * Math.cos((lat * Math.PI) / 180))
  return [
    [lon, lat],
    [lon + dLon, lat],
    [lon + dLon, lat + dLat],
    [lon, lat + dLat],
  ]
}

/** HH:MM:SS (UTC) of a simulated time, from the scenario start_time. */
export function clockLabel(startTime: string, t: number): string {
  return new Date(Date.parse(startTime) + t * 1000).toISOString().slice(11, 19)
}

export type PhaseKind = 'ground' | 'fly' | 'hover' | 'crossing'

/** A stretch of a route with one meaning for the timeline: on the ground, flying, hovering, or
 * inside a friendly jammer zone (`zone` set, during a cut). */
export interface Phase {
  kind: PhaseKind
  start: number
  end: number
  zone?: string
}

const PHASE_STEP_S = 2

/** Route cut into phases: consecutive segments with the same action are merged, so a turn does
 * not start a new block, and the time spent inside a friendly jammer zone is its own phase. */
export function routePhases(route: RoutePoint[], zones: Zone[]): Phase[] {
  if (route.length < 2) return []
  const friendly = zones.filter((z) => z.kind === 'friendly_jammer')
  const phases: Phase[] = []
  const t0 = route[0].t
  const t1 = route[route.length - 1].t
  for (let t = t0; t < t1; t = Math.min(t + PHASE_STEP_S, t1)) {
    const next = Math.min(t + PHASE_STEP_S, t1)
    // Sample mid-step: at a point's own time, positionAt reports the action that just ended.
    const at = positionAt(route, (t + next) / 2)
    if (!at) break
    const zone = at.action === 'ground' ? undefined : friendly.find((z) => inForbidden(z, at.pos))?.threat
    const kind: PhaseKind = zone ? 'crossing' : at.action
    const last = phases[phases.length - 1]
    if (last && last.kind === kind && last.zone === zone) last.end = next
    else phases.push({ kind, start: t, end: next, zone })
  }
  return phases
}

/** Whether `pos` lies on the forbidden side of `zone`. */
export function inForbidden(zone: Zone, pos: Position): boolean {
  const inside = inRing(zone.ring, pos)
  return zone.forbidden === 'inside' ? inside : !inside
}

/** Jammer zones at full size (the reference footprints) that the launch point lies in: the
 * pilot starts the mission inside the jamming. Radars are left out: they jam nothing. */
export function startsInside(nominal: Zone[], launch: Position): Zone[] {
  return nominal.filter((z) => z.kind !== 'radar' && z.forbidden === 'inside' && inRing(z.ring, launch))
}

/** Ray casting on lon/lat: fine at the scale of the demo area. */
function inRing(ring: Position[], p: Position): boolean {
  let inside = false
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const a = ring[i]
    const b = ring[j]
    if (a.lat > p.lat !== b.lat > p.lat && p.lon < ((b.lon - a.lon) * (p.lat - a.lat)) / (b.lat - a.lat) + a.lon) {
      inside = !inside
    }
  }
  return inside
}

/** The scenario entry of a mission in view. */
export function missionOf(scenario: Scenario, id: string): Mission | undefined {
  return scenario.missions.find((m) => m.id === id)
}

/** Points to frame on the map for an item clicked in the sidebar: a mission's launch, target and
 * planned route; an object's position. Empty when the item is not in the scenario. */
export function focusPoints(
  scenario: Scenario,
  kind: 'mission' | ObjectKind,
  id: string,
  route: RoutePoint[] = [],
): Position[] {
  if (kind === 'jammer') return scenario.friendly_jammers.filter((j) => j.id === id).map((j) => j.pos)
  if (kind === 'threat') return scenario.enemy_threats.filter((t) => t.id === id).map((t) => t.pos)
  if (kind === 'relay') return scenario.relays.filter((r) => r.id === id).map((r) => r.pos)
  const m = missionOf(scenario, id)
  if (!m) return []
  return [m.launch, m.target, ...route.map((p) => ({ lat: p.lat, lon: p.lon }))]
}

/** A mission's time span in sim seconds: its planned route, else departure plus full battery. */
export function missionSpan(mission: Mission, route: RoutePoint[] = []): [number, number] {
  const first = route.at(0)
  const last = route.at(-1)
  if (first && last && last.t > first.t) return [first.t, last.t]
  return [mission.departure_s, mission.departure_s + mission.battery_max_s]
}

/** Smallest box around several grids, as two [lon, lat] corners (south-west, north-east). */
export function boundsOf(grids: Grid[]): [[number, number], [number, number]] | null {
  const corners = grids.flatMap(gridBounds)
  if (corners.length === 0) return null
  const lons = corners.map(([lon]) => lon)
  const lats = corners.map(([, lat]) => lat)
  return [
    [Math.min(...lons), Math.min(...lats)],
    [Math.max(...lons), Math.max(...lats)],
  ]
}

/** Great-circle distance in metres. */
export function distanceM(a: Position, b: Position): number {
  const r = 6_371_000
  const rad = Math.PI / 180
  const dLat = (b.lat - a.lat) * rad
  const dLon = (b.lon - a.lon) * rad
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dLon / 2) ** 2
  return 2 * r * Math.asin(Math.sqrt(h))
}

/** "48.1234, 11.5678" (comma or space separated) to a position; null when not valid. */
export function parsePosition(text: string): Position | null {
  const parts = text.trim().split(/[\s,;]+/).filter(Boolean)
  if (parts.length !== 2) return null
  const [lat, lon] = parts.map(Number)
  if (!Number.isFinite(lat) || !Number.isFinite(lon) || Math.abs(lat) > 90 || Math.abs(lon) > 180) return null
  return { lat, lon }
}

export function formatPosition(p: Position): string {
  return `${p.lat.toFixed(5)}, ${p.lon.toFixed(5)}`
}

/** Simulated time of the next `HH:MM` (UTC) at or after `nowS`; null when `hhmm` is not valid. */
export function nextClockTime(startTime: string, nowS: number, hhmm: string): number | null {
  const match = /^(\d{1,2}):(\d{2})$/.exec(hhmm.trim())
  if (!match) return null
  const [h, m] = [Number(match[1]), Number(match[2])]
  if (h > 23 || m > 59) return null
  const day = 86_400
  const startMs = Date.parse(startTime)
  const startOfDayS = (Math.floor((startMs / 1000 + nowS) / day) * day * 1000 - startMs) / 1000
  const t = startOfDayS + h * 3600 + m * 60
  return t >= nowS ? t : t + day
}

/** The pilot may still pick a route: the mission waits on the ground with no route picked, or
 * the last pick was refused. */
export function choosing(view: MissionView): boolean {
  return view.status === 'planned' && (!view.request || view.request.status === 'refused')
}

/** Flight duration as `m min ss s`. */
export function durationLabel(s: number): string {
  const total = Math.round(s)
  return `${Math.floor(total / 60)} min ${String(total % 60).padStart(2, '0')} s`
}

/** Name of a route choice, in the words the app uses elsewhere: the detour goes around every
 * jammer, a direct route goes through jammers during their cuts. */
export function choiceTitle(c: Pick<RouteChoice, 'kind' | 'cuts'>): string {
  if (c.kind === 'safe') return 'Detour'
  const n = c.cuts.length
  return n > 0 ? `Direct route · ${n} cut${n > 1 ? 's' : ''}` : 'Direct route'
}

/** Same, from the kind alone (a request keeps the kind, not the cuts). */
export function kindTitle(kind: ChoiceKind): string {
  return { safe: 'Detour', one_cut: 'Direct route · 1 cut', fastest: 'Direct route · all cuts' }[kind]
}

/** Choice drawn on top: the one under the pointer, else the one clicked, else the first one
 * worth asking for and not refused (a direct route saving a few seconds is only there for
 * comparison; after a refusal the detour comes back on top). */
export function highlightedChoice(
  choices: RouteChoice[],
  focused: ChoiceKind | null,
  selected: ChoiceKind | null,
  refused: ChoiceKind | null = null,
): RouteChoice | null {
  const find = (k: ChoiceKind | null) => (k ? choices.find((c) => c.kind === k) : undefined)
  return (
    find(focused) ??
    find(selected) ??
    choices.find((c) => c.worth_asking && c.kind !== refused) ??
    choices[0] ??
    null
  )
}

/** Kind of route the command refused for this mission, if its last request was refused and
 * the pilot's plan B is the detour: that route is greyed out. */
export function refusedKind(view: MissionView): ChoiceKind | null {
  const r = view.request
  return r?.status === 'refused' && r.cuts.length > 0 && r.on_timeout === 'avoid' ? r.kind : null
}

/** Kind of route refused but flown anyway (plan B "straight anyway"): it stays on top. */
export function straightAnyway(view: MissionView): ChoiceKind | null {
  const r = view.request
  return r?.status === 'refused' && r.cuts.length > 0 && r.on_timeout === 'cross' ? r.kind : null
}

/** Point at ``fraction`` of the route length (flat-earth distances, fine at route scale). */
export function pointAlong(route: RoutePoint[], fraction: number): Position {
  const legs = route.slice(1).map((p, i) => Math.hypot(p.lat - route[i].lat, (p.lon - route[i].lon) * Math.cos((p.lat * Math.PI) / 180)))
  let left = legs.reduce((a, b) => a + b, 0) * fraction
  for (let i = 0; i < legs.length; i++) {
    if (left <= legs[i] && legs[i] > 0) {
      const f = left / legs[i]
      return { lat: route[i].lat + (route[i + 1].lat - route[i].lat) * f, lon: route[i].lon + (route[i + 1].lon - route[i].lon) * f }
    }
    left -= legs[i]
  }
  const last = route[route.length - 1]
  return { lat: last.lat, lon: last.lon }
}

/** Route the drone is set to fly: the plan's, or with plan B "straight anyway" after a
 * refusal, the direct route picked, through the jammers still on. */
export function flownRoute(view: MissionView): RoutePoint[] {
  const r = view.request
  if (r && straightAnyway(view) && r.route.length > 0) return r.route
  return view.plan?.optimised_route ?? []
}

export const LOST_LINK_LABELS: Record<LostLinkAction, { title: string; detail: string; short: string; color: string }> = {
  rth: {
    title: 'RTH (Return to Home)',
    detail: 'Backtrack along safe path to launch if control link is lost',
    short: 'RTH',
    color: 'blue',
  },
  continue: {
    title: 'Continue autonomous track',
    detail: 'Maintain flight plan to target even without link',
    short: 'Continue',
    color: 'teal',
  },
  land: {
    title: 'Emergency land',
    detail: 'Immediate precautionary landing on spot',
    short: 'Land',
    color: 'orange',
  },
}
