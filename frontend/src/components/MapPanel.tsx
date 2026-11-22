import { type CSSProperties, useEffect, useMemo, useRef, useState } from 'react'
import { ActionIcon, Button, SegmentedControl, Tooltip } from '@mantine/core'
import type { FeatureCollection } from 'geojson'
import {
  choosing,
  focusPoints,
  friendlyZoneState,
  highlightedChoice,
  positionAt,
  refusedKind,
  straightAnyway,
} from '../domain/view'
import { useViewTime } from '../hooks/useSimNow'
import { levelColor } from '../map/levels'
import { COLORS, createMapView, ROUTE_COLORS, type DroneMark, type MapView } from '../map/mapView'
import { useSelectedMission, useStore } from '../store'
import { CrosshairIcon, DroneIcon, LayersIcon } from './Icons'
import { MissionForm } from './sidebar/MissionForm'

interface ProbeData {
  lat: number
  lon: number
  elev_m: number
  status: 'SAFE' | 'CAUTION' | 'CONTENTION' | 'JAMMED'
  js_db: number
  p_pilot_dbm: number
  p_jammer_dbm: number
  shielding_db: number
  worst_jammer_id?: string
  pilot_dist_m?: number
  jammer_dist_m?: number
  details: string
}

export function MapPanel() {
  const container = useRef<HTMLDivElement>(null)
  const view = useRef<MapView | null>(null)

  const snapshot = useStore((s) => s.snapshot)
  const cuts = useStore((s) => s.cuts)
  const missions = useStore((s) => s.missions)
  const selected = useStore((s) => s.selectedMission)
  const focusedChoice = useStore((s) => s.focusedChoice)
  const selectedChoice = useStore((s) => s.selectedChoice)
  const scrubbing = useStore((s) => s.viewTimeS !== null)
  const t = useViewTime()
  const list = useMemo(() => Object.values(missions), [missions])
  const callsign = useStore((s) => s.callsign)
  // Missions this viewer may change, as a string: plan and drone events must not redraw the
  // markers, only a status or ownership change.
  const movableKey = list
    .filter((m) => m.status === 'planned')
    .map((m) => m.constraints.mission)
    .filter((id) => {
      const scenario = snapshot?.scenario
      const pilot = scenario?.missions.find((x) => x.id === id)?.pilot_callsign
      // The pilot decides its mission: only it moves the points (the command answers cuts).
      return callsign === pilot
    })
    .join(',')
  const movable = useMemo(() => (movableKey ? movableKey.split(',') : []), [movableKey])
  const selectedView = useSelectedMission()
  const drone = selectedView?.drone ?? null

  const [showZones, setShowZones] = useState(true)
  const [showBaseline, setShowBaseline] = useState(true)
  const [showRf, setShowRf] = useState(false)
  const [showHeatmap, setShowHeatmap] = useState(false)
  const [showRelief, setShowRelief] = useState(true)
  const [showBuildings, setShowBuildings] = useState(true)
  // 3D: the selected mission's zones per flight level, from the server; one level shown.
  const [zones3d, setZones3d] = useState<{ mission: string; zones: FeatureCollection } | null>(null)
  const [level, setLevel] = useState(0)
  const [is3D, setIs3D] = useState(false)
  const [legendOpen, setLegendOpen] = useState(false)
  const [probe, setProbe] = useState<ProbeData | null>(null)
  const draft = useStore((s) => s.requestDraft)

  useEffect(() => {
    if (!container.current) return
    view.current = createMapView(container.current)
    view.current.onClick((pos) => useStore.getState().pickPoint(pos))
    view.current.onMissionClick((mission) => {
      const state = useStore.getState()
      const m = state.snapshot?.scenario.missions.find((x) => x.id === mission)
      if (!m) return
      state.selectMission(mission)
      state.openEdit(mission, m.launch, m.target)
    })
    return () => {
      view.current?.destroy()
      view.current = null
    }
  }, [])

  const is3dScenario = snapshot?.scenario.terrain.mode === '3d'
  const levels = snapshot?.scenario.terrain.levels_m ?? []

  // 3D zones of the selected mission: fetched again with each snapshot (threats added or removed
  // come with one), shown one level at a time.
  useEffect(() => {
    if (!is3dScenario || !selected) return
    let active = true
    fetch(`/api/zones3d?mission_id=${encodeURIComponent(selected)}`)
      .then((r) => (r.ok ? (r.json() as Promise<FeatureCollection>) : null))
      .then((zones) => {
        if (active && zones) setZones3d({ mission: selected, zones })
      })
      .catch(() => undefined)
    return () => {
      active = false
    }
  }, [is3dScenario, selected, snapshot])

  const levelZones = useMemo<FeatureCollection | null>(() => {
    if (!is3dScenario || !zones3d || zones3d.mission !== selected) return null
    const features = zones3d.zones.features.filter((f) => f.properties?.level === level)
    return { ...zones3d.zones, features }
  }, [is3dScenario, zones3d, selected, level])

  // Scene: every grid in view, the selected mission's zones over the nominal footprints. Grids
  // and zones only come with the snapshot, so plans and drones do not redraw it.
  useEffect(() => {
    if (!snapshot) return
    const constraints = snapshot.missions.map((m) => m.constraints)
    const mission = constraints.find((c) => c.mission === selected) ?? null
    // A pilot sees one circle per jammer, its own; the command, the reference footprints too.
    const pilot = snapshot.viewer.role === 'pilot'
    view.current?.setScene({
      scenario: snapshot.scenario,
      grids: constraints.map((c) => c.grid),
      missionGrid: mission?.grid ?? null,
      missionZones: mission?.zones ?? [],
      nominalZones: pilot ? [] : snapshot.nominal_zones,
      movable,
      levelZones,
    })
  }, [snapshot, selected, movable, levelZones])

  useEffect(() => {
    view.current?.setLayerVisibility({
      zones: showZones,
      baseline: showBaseline,
      rf: showRf,
      heatmap: showHeatmap,
      relief: showRelief,
      buildings: showBuildings,
    })
  }, [showZones, showBaseline, showRf, showHeatmap, showRelief, showBuildings])

  useEffect(() => {
    if (!showRf) return
    let active = true
    fetch('/api/rf/coverage')
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (active && data) view.current?.setRfCoverage(data)
      })
      .catch((err) => console.warn('RF coverage load error:', err))
    return () => {
      active = false
    }
  }, [showRf, snapshot])

  useEffect(() => {
    if (!showHeatmap) {
      view.current?.setRfHeatmap(null)
      return
    }
    // J/S is against one pilot link: the selected mission's (the server falls back to the first)
    let active = true
    const query = selected ? `?mission_id=${encodeURIComponent(selected)}` : ''
    fetch(`/api/rf/heatmap${query}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (active && data) view.current?.setRfHeatmap(data)
      })
      .catch((err) => console.warn('RF heatmap load error:', err))
    return () => {
      active = false
    }
  }, [showHeatmap, snapshot, selected])

  useEffect(() => {
    if (!view.current) return
    let timeoutId: number | undefined
    let abortCtrl: AbortController | null = null

    view.current.onChoice(
      (kind) => useStore.getState().focusChoice(kind),
      (kind) => useStore.getState().selectChoice(kind),
    )
    view.current.onHover((pos) => {
      window.clearTimeout(timeoutId)
      abortCtrl?.abort()
      if (!pos) {
        setProbe(null)
        return
      }
      timeoutId = window.setTimeout(() => {
        abortCtrl = new AbortController()
        fetch(`/api/rf/probe?lat=${pos.lat.toFixed(5)}&lon=${pos.lon.toFixed(5)}`, {
          signal: abortCtrl.signal,
        })
          .then((r) => (r.ok ? r.json() : null))
          .then((data) => {
            if (data) setProbe(data)
          })
          .catch((err) => {
            if (err?.name !== 'AbortError') console.warn('Probe error:', err)
          })
      }, 70)
    })

    return () => {
      window.clearTimeout(timeoutId)
      abortCtrl?.abort()
    }
  }, [])

  useEffect(() => {
    if (!snapshot) return
    const all = Object.values(cuts)
    const states = Object.fromEntries(
      snapshot.scenario.friendly_jammers.map((j) => [j.id, friendlyZoneState(j.id, all, t)]),
    )
    view.current?.setZoneStates(states)
  }, [snapshot, cuts, t])

  useEffect(() => {
    view.current?.setRoutes(
      list.map((m) => ({
        mission: m.constraints.mission,
        baseline: m.plan?.baseline_route ?? null,
        optimised: m.plan?.optimised_route ?? null,
        selected: m.constraints.mission === selected,
        // The pilot of the selected mission sees every route it may pick until it picks one.
        choices:
          m.constraints.mission === selected &&
          choosing(m) &&
          snapshot?.scenario.missions.find((x) => x.id === m.constraints.mission)?.pilot_callsign === callsign
            ? (m.plan?.choices ?? [])
            : [],
        highlight:
          highlightedChoice(m.plan?.choices ?? [], focusedChoice, selectedChoice ?? straightAnyway(m), refusedKind(m))
            ?.kind ?? null,
        refused: refusedKind(m),
      })),
    )
  }, [list, selected, focusedChoice, selectedChoice, callsign, snapshot])

  // Live: the server's simulated drones. Scrubbing: where each plan puts its drone at that instant.
  useEffect(() => {
    const marks: DroneMark[] = []
    for (const m of list) {
      const mission = m.constraints.mission
      const pos = scrubbing
        ? (m.plan ? positionAt(m.plan.optimised_route, t)?.pos : undefined)
        : m.drone?.position
      if (pos) marks.push({ mission, pos, selected: mission === selected })
    }
    view.current?.setDrones(marks)
  }, [list, selected, scrubbing, t])

  useEffect(() => {
    view.current?.setRequest({ launch: draft.launch, target: draft.target, picking: draft.picking !== null })
  }, [draft])

  // A click in the sidebar frames the mission or object. Read once per click: later snapshots
  // or plans must not move the map.
  const focus = useStore((s) => s.focus)
  useEffect(() => {
    if (!focus) return
    const state = useStore.getState()
    if (!state.snapshot) return
    const route = focus.kind === 'mission' ? state.missions[focus.id]?.plan?.optimised_route : undefined
    view.current?.frame(focusPoints(state.snapshot.scenario, focus.kind, focus.id, route))
  }, [focus])

  // Escape stops picking a point, leaving the form unchanged.
  useEffect(() => {
    if (!draft.picking) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') useStore.getState().setRequestDraft({ picking: null })
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [draft.picking])

  return (
    <div className="map-wrap">
      <div ref={container} className="map" />

      <div className="map-form">
        <MissionForm />
      </div>

      {draft.picking && (
        <div className="map-pick-banner">
          Click on the map to set the {draft.picking === 'launch' ? 'launch point' : 'target'} · Esc to cancel
        </div>
      )}

      {/* Floating Tactical Inspector Probe */}
      {probe && <TacticalInspectorProbe probe={probe} />}

      {/* Top-Right Floating Tactical Toolbar and Legend */}
      <div className="map-hud-topright">
        <div className="map-hud-bar">
          <Button
            size="compact-xs"
            variant={showZones ? 'light' : 'subtle'}
            color={showZones ? 'cyan' : 'gray'}
            onClick={() => setShowZones((v) => !v)}
            title="Toggle jammer and radar zones"
          >
            Zones
          </Button>
          <Button
            size="compact-xs"
            variant={showRf ? 'light' : 'subtle'}
            color={showRf ? 'teal' : 'gray'}
            onClick={() => setShowRf((v) => !v)}
            title="Toggle terrain-aware RF propagation contours"
          >
            Terrain RF
          </Button>
          <Button
            size="compact-xs"
            variant={showHeatmap ? 'light' : 'subtle'}
            color={showHeatmap ? 'grape' : 'gray'}
            onClick={() => setShowHeatmap((v) => !v)}
            title="Toggle continuous Jammer-to-Signal (J/S) heatmap"
          >
            Heatmap
          </Button>
          <Button
            size="compact-xs"
            variant={showBaseline ? 'light' : 'subtle'}
            color={showBaseline ? 'cyan' : 'gray'}
            onClick={() => setShowBaseline((v) => !v)}
            title="Toggle baseline detour route"
          >
            Detour
          </Button>
          <Button
            size="compact-xs"
            variant={showRelief ? 'light' : 'subtle'}
            color={showRelief ? 'indigo' : 'gray'}
            onClick={() => setShowRelief((v) => !v)}
            title="Toggle physical shaded relief and topography"
          >
            Relief
          </Button>
          <Button
            size="compact-xs"
            variant={showBuildings ? 'light' : 'subtle'}
            color="gray"
            onClick={() => setShowBuildings((v) => !v)}
            title="Toggle buildings, brighter when taller"
          >
            Buildings
          </Button>
          <Button
            size="compact-xs"
            variant={is3D ? 'filled' : 'subtle'}
            color={is3D ? 'teal' : 'gray'}
            onClick={() => {
              const next = !is3D
              setIs3D(next)
              view.current?.set3D(next)
            }}
            title="Toggle 3D Terrain view (pitch and elevation relief)"
          >
            {is3D ? '3D Active' : '3D View'}
          </Button>
          <Tooltip label="Fit scenario bounds" openDelay={300}>
            <ActionIcon
              size="sm"
              variant="subtle"
              color="gray"
              aria-label="Fit scenario bounds"
              onClick={() => view.current?.fitScene()}
            >
              <CrosshairIcon size={14} />
            </ActionIcon>
          </Tooltip>
          {drone && (
            <Tooltip label="Center on drone" openDelay={300}>
              <ActionIcon
                size="sm"
                variant="subtle"
                color="cyan"
                aria-label="Center on drone"
                onClick={() => view.current?.centerOnDrone()}
              >
                <DroneIcon size={14} />
              </ActionIcon>
            </Tooltip>
          )}
          <Button
            size="compact-xs"
            variant={legendOpen ? 'filled' : 'subtle'}
            color={legendOpen ? 'cyan' : 'gray'}
            leftSection={<LayersIcon size={12} />}
            onClick={() => setLegendOpen((v) => !v)}
            title="Toggle tactical legend"
          >
            Legend
          </Button>
        </div>

        {is3dScenario && levels.length > 0 && (
          <div className="map-hud-bar" title="3D: zones at one flight level; the route is coloured by level">
            <span style={{ fontSize: 11, color: '#adb5bd', padding: '0 4px' }}>Zones at</span>
            <SegmentedControl
              size="xs"
              value={String(level)}
              onChange={(v) => setLevel(Number(v))}
              data={levels.map((m, i) => ({
                value: String(i),
                label: <span style={{ color: levelColor(i), fontWeight: 600 }}>{m} m</span>,
              }))}
            />
          </div>
        )}

        {legendOpen && <TacticalLegend onClose={() => setLegendOpen(false)} />}
      </div>
    </div>
  )
}

function TacticalInspectorProbe({ probe }: { readonly probe: ProbeData }) {
  const jsLabel = probe.js_db >= 0 ? `+${probe.js_db}` : `${probe.js_db}`
  const shieldLabel = probe.shielding_db > 0 ? `+${probe.shielding_db} dB` : '0 dB'

  return (
    <div className="tactical-inspector">
      <div className="tactical-inspector-header">
        <span className="tactical-inspector-title">TACTICAL PROBE</span>
        <span className={`tactical-badge tactical-badge-${probe.status.toLowerCase()}`}>
          {probe.status}: {jsLabel} dB J/S
        </span>
      </div>
      <div className="tactical-grid">
        <div className="tactical-row"><span className="tactical-label">Pilot (S):</span> <span className="tactical-val">{probe.p_pilot_dbm} dBm</span></div>
        <div className="tactical-row"><span className="tactical-label">Jammer (J):</span> <span className="tactical-val">{probe.p_jammer_dbm} dBm</span></div>
        <div className="tactical-row"><span className="tactical-label">Ground:</span> <span className="tactical-val">{probe.elev_m} m</span></div>
        <div className="tactical-row"><span className="tactical-label">Shielding:</span> <span className="tactical-val">{shieldLabel}</span></div>
      </div>
      <div className="tactical-details">{probe.details}</div>
    </div>
  )
}

function TacticalLegend({ onClose }: { readonly onClose: () => void }) {
  return (
    <div className="map-legend">
      <div className="map-legend-header">
        <span style={{ fontWeight: 700, fontSize: '11px', letterSpacing: '0.04em' }}>TACTICAL LEGEND</span>
        <button
          type="button"
          className="map-legend-close"
          onClick={onClose}
          aria-label="Close legend"
        >
          ×
        </button>
      </div>
      {LEGEND.map((l) => (
        <div key={l.label} className="map-legend-row">
          <span className={`map-legend-key map-legend-${l.kind}`} style={{ '--c': l.color } as CSSProperties} />
          {l.label}
        </div>
      ))}

    </div>
  )
}

const LEGEND: { label: string; color: string; kind: 'area' | 'line' | 'dash' }[] = [
  { label: 'Friendly jammer on', color: COLORS.friendlyOn, kind: 'area' },
  { label: 'Cut pending confirmation', color: COLORS.friendlyPending, kind: 'area' },
  { label: 'Cut confirmed (passable)', color: COLORS.friendlyCut, kind: 'area' },
  { label: 'Enemy jammer', color: COLORS.enemy, kind: 'area' },
  { label: 'Radar', color: COLORS.radar, kind: 'area' },
  { label: 'Building (brighter = taller)', color: '#ced4da', kind: 'area' },
  { label: 'Zone to avoid (not enforced yet)', color: COLORS.avoid, kind: 'area' },
  { label: "Out of the pilot's reach (3D, at the level)", color: COLORS.link, kind: 'area' },
  { label: 'Detour (all jammers on)', color: COLORS.baseline, kind: 'dash' },
  { label: 'Planned route', color: ROUTE_COLORS.approved, kind: 'line' },
  { label: 'Route awaiting confirmation', color: ROUTE_COLORS.preview, kind: 'line' },
  { label: 'Fallback route', color: ROUTE_COLORS.fallback, kind: 'line' },
  { label: 'Friendly Jammer RF field', color: '#e03131', kind: 'area' },
  { label: 'Enemy Jammer RF field', color: '#9c36b5', kind: 'area' },
  { label: 'J/S Heatmap (Safe → Denied)', color: '#20c997', kind: 'area' },
]
