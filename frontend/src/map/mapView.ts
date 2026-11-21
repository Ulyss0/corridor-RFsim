// All map code lives here, framework-free. React only creates the view and calls its setters.
// Setters can be called before the style is loaded: the latest data is applied on load.
import * as maplibregl from 'maplibre-gl'
import type { GeoJSONSource, Map as MlMap, StyleSpecification } from 'maplibre-gl'
import type { Feature, FeatureCollection } from 'geojson'
import 'maplibre-gl/dist/maplibre-gl.css'
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url'
import type { Grid, PlanUpdated, Position, RoutePoint, Scenario, Zone } from '../api/protocol'
import { boundsOf, gridBounds, type ZoneState } from '../domain/view'

// Draft basemap: online OSM raster tiles. TODO: offline PMTiles extract of the demo area.
const STYLE: StyleSpecification = {
  version: 8,
  sources: {
    osm: {
      type: 'raster',
      tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
      tileSize: 256,
      maxzoom: 19,
      attribution: '© OpenStreetMap contributors',
    },
  },
  layers: [
    { id: 'bg', type: 'background', paint: { 'background-color': '#1a1b1e' } },
    {
      id: 'osm',
      type: 'raster',
      source: 'osm',
      paint: { 'raster-saturation': -0.7, 'raster-brightness-max': 0.65 },
    },
  ],
}

export const COLORS = {
  friendlyOn: '#e03131',
  friendlyCut: '#2f9e44',
  friendlyPending: '#f08c00',
  enemy: '#9c36b5',
  radar: '#fd7e14',
  baseline: '#adb5bd',
  optimised: '#22b8cf',
  preview: '#f08c00',
  fallback: '#fa5252',
  drone: '#ffffff',
}

/** Colour of the planned route for each plan status. */
export const ROUTE_COLORS: Record<PlanUpdated['status'], string> = {
  baseline: COLORS.optimised,
  approved: COLORS.optimised,
  preview: COLORS.preview,
  fallback: COLORS.fallback,
}

type FC = FeatureCollection
const empty = (): FC => ({ type: 'FeatureCollection', features: [] })

export interface LayerVisibility {
  zones?: boolean
  baseline?: boolean
  grid?: boolean
  rf?: boolean
  heatmap?: boolean
  relief?: boolean
}

export interface Scene {
  /** Only what this callsign may see. */
  scenario: Scenario
  /** Grid of every mission in view; the map fits them all. */
  grids: Grid[]
  /** Zones of the selected mission, for its pilot, and its grid (for `forbidden: outside`). */
  missionGrid: Grid | null
  missionZones: Zone[]
  /** Mission-independent footprints (command and operator views). */
  nominalZones: Zone[]
}

export interface MissionRoutes {
  mission: string
  baseline: RoutePoint[] | null
  optimised: RoutePoint[] | null
  /** The selected mission shows its detour too, and stands out. */
  selected: boolean
}

export interface DroneMark {
  mission: string
  pos: Position
  selected: boolean
}

export interface MapView {
  setScene(scene: Scene): void
  setZoneStates(states: Record<string, ZoneState>): void
  setRoutes(routes: MissionRoutes[]): void
  setDrones(drones: DroneMark[]): void
  setRfCoverage(coverage: FeatureCollection | null): void
  setRfHeatmap(heatmap: FeatureCollection | null): void
  onHover(cb: (pos: Position | null) => void): void
  setLayerVisibility(vis: LayerVisibility): void
  set3D(enabled: boolean): void
  is3D(): boolean
  fitScene(): void
  centerOnDrone(pos?: Position | null): void
  destroy(): void
}

// MapLibre resolves its worker relative to its own module, which breaks once Vite bundles it.
maplibregl.setWorkerUrl(workerUrl)

export function createMapView(container: HTMLElement): MapView {
  const map = new maplibregl.Map({
    container,
    style: STYLE,
    center: [0, 0],
    zoom: 1,
    attributionControl: { compact: true },
    maxPitch: 85,
  })
  map.addControl(new maplibregl.NavigationControl({ showCompass: true, visualizePitch: true }), 'top-left')
  map.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-left')

  let loaded = false
  let is3dMode = false
  const pending: Record<string, FC> = {}
  let scene: Scene | null = null
  let fitted = ''
  let states: Record<string, ZoneState> = {}
  let visibility: Required<LayerVisibility> = { grid: true, baseline: true, zones: true, rf: true, heatmap: false, relief: true }
  const markers: maplibregl.Marker[] = []
  const drones = new Map<string, maplibregl.Marker>()

  const setData = (source: string, data: FC) => {
    pending[source] = data
    if (loaded) (map.getSource(source) as GeoJSONSource | undefined)?.setData(data)
  }

  const applyVisibility = () => {
    if (!loaded) return
    const v = (on: boolean) => (on ? 'visible' : 'none')
    map.setLayoutProperty('hillshade-relief', 'visibility', v(visibility.relief))
    map.setLayoutProperty('grid-line', 'visibility', v(visibility.grid))
    map.setLayoutProperty('route-baseline', 'visibility', v(visibility.baseline))
    map.setLayoutProperty('zones-fill', 'visibility', v(visibility.zones))
    map.setLayoutProperty('zones-line', 'visibility', v(visibility.zones))
    map.setLayoutProperty('rf-coverage-fill', 'visibility', v(visibility.rf))
    map.setLayoutProperty('rf-coverage-line', 'visibility', v(visibility.rf))
    map.setLayoutProperty('rf-safe-baseline', 'visibility', v(visibility.heatmap))
    map.setLayoutProperty('rf-heatmap-cells', 'visibility', v(visibility.heatmap))
  }

  map.on('error', (e) => {
    // Suppress non-critical tile loading errors (e.g. offline fallback for remote terrain)
    if (e.error?.message?.includes('terrarium') || e.error?.message?.includes('tile')) {
      return
    }
    console.warn('MapLibre warning:', e.error || e)
  })

  map.on('load', () => {
    // 1. Add 3D Terrarium raster-dem source
    map.addSource('terrain-dem', {
      type: 'raster-dem',
      tiles: ['https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png'],
      tileSize: 256,
      encoding: 'terrarium',
      maxzoom: 15,
    })

    // 2. Global safe airspace baseline layer covering entire Earth in emerald green
    map.addSource('rf-safe-baseline', {
      type: 'geojson',
      data: {
        type: 'FeatureCollection',
        features: [
          {
            type: 'Feature',
            properties: {},
            geometry: {
              type: 'Polygon',
              coordinates: [
                [
                  [-180, -85],
                  [180, -85],
                  [180, 85],
                  [-180, 85],
                  [-180, -85],
                ],
              ],
            },
          },
        ],
      },
    })
    map.addLayer({
      id: 'rf-safe-baseline',
      type: 'fill',
      source: 'rf-safe-baseline',
      paint: {
        'fill-color': 'rgba(32, 201, 151, 0.35)', // Translucent emerald green
        'fill-opacity': 0.85,
      },
    })

    for (const id of ['grid', 'nominal', 'zones', 'routes', 'rf-coverage', 'rf-heatmap']) {
      map.addSource(id, { type: 'geojson', data: pending[id] ?? empty() })
    }

    // 3. Shaded relief (hillshade) layer: casts real sunlight and shadows on terrain features
    map.addLayer({
      id: 'hillshade-relief',
      type: 'hillshade',
      source: 'terrain-dem',
      paint: {
        'hillshade-exaggeration': 0.85,
        'hillshade-shadow-color': '#090d16',
        'hillshade-highlight-color': '#ffffff',
        'hillshade-accent-color': '#1e293b',
        'hillshade-illumination-direction': 315,
        'hillshade-illumination-altitude': 45,
      },
    })

    // 4. Continuous RF heatmap polygon cells: solid contiguous quads that NEVER dilute on zoom
    map.addLayer({
      id: 'rf-heatmap-cells',
      type: 'fill',
      source: 'rf-heatmap',
      paint: {
        'fill-color': ['coalesce', ['get', 'color'], '#20c997'],
        'fill-opacity': ['coalesce', ['get', 'opacity'], 0.55],
        'fill-antialias': true,
      },
    })

    map.addLayer({
      id: 'grid-line',
      type: 'line',
      source: 'grid',
      paint: {
        'line-color': '#868e96',
        'line-width': ['case', ['get', 'selected'], 1.5, 1],
        'line-opacity': ['case', ['get', 'selected'], 1, 0.4],
        'line-dasharray': [4, 4],
      },
    })
    map.addLayer({
      id: 'nominal-fill',
      type: 'fill',
      source: 'nominal',
      paint: { 'fill-color': ['get', 'color'], 'fill-opacity': ['*', 0.5, ['get', 'opacity']] },
    })
    map.addLayer({
      id: 'nominal-line',
      type: 'line',
      source: 'nominal',
      paint: { 'line-color': ['get', 'color'], 'line-width': 1.5, 'line-dasharray': [3, 2] },
    })
    map.addLayer({
      id: 'rf-coverage-fill',
      type: 'fill',
      source: 'rf-coverage',
      paint: { 'fill-color': ['get', 'fill'], 'fill-opacity': ['get', 'fill_opacity'] },
    })
    map.addLayer({
      id: 'rf-coverage-line',
      type: 'line',
      source: 'rf-coverage',
      paint: { 'line-color': ['get', 'stroke'], 'line-width': ['get', 'stroke_width'] },
    })
    map.addLayer({
      id: 'zones-fill',
      type: 'fill',
      source: 'zones',
      paint: { 'fill-color': ['get', 'color'], 'fill-opacity': ['get', 'opacity'] },
    })
    map.addLayer({
      id: 'zones-line',
      type: 'line',
      source: 'zones',
      paint: { 'line-color': ['get', 'color'], 'line-width': 2 },
    })
    map.addLayer({
      id: 'route-baseline',
      type: 'line',
      source: 'routes',
      filter: ['==', ['get', 'kind'], 'baseline'],
      paint: { 'line-color': COLORS.baseline, 'line-width': 3, 'line-dasharray': [2, 2] },
    })
    map.addLayer({
      id: 'route-optimised',
      type: 'line',
      source: 'routes',
      filter: ['==', ['get', 'kind'], 'optimised'],
      paint: {
        'line-color': COLORS.optimised,
        'line-width': ['case', ['get', 'selected'], 4, 2.5],
        'line-opacity': ['case', ['get', 'selected'], 1, 0.6],
      },
    })
    loaded = true
    if (is3dMode) {
      map.setTerrain({ source: 'terrain-dem', exaggeration: 2.8 })
    }
    applyVisibility()
  })

  const zoneFeatures = (zones: Zone[], outer: number[][] | null): Feature[] => {
    const worldOuter = [
      [-180, -85],
      [180, -85],
      [180, 85],
      [-180, 85],
      [-180, -85],
    ]
    return zones.map((z) => {
      const ring = [...z.ring.map((p) => [p.lon, p.lat]), [z.ring[0].lon, z.ring[0].lat]]
      const { color, opacity } = zoneStyle(z, states[z.threat] ?? 'on')
      // forbidden "outside": everything but the ring
      const coordinates = z.forbidden === 'outside' ? [outer ?? worldOuter, ring] : [ring]
      return {
        type: 'Feature',
        properties: { threat: z.threat, color, opacity },
        geometry: { type: 'Polygon', coordinates },
      }
    })
  }

  const renderZones = () => {
    if (!scene) return
    const g = scene.missionGrid
    const outer = g ? [...gridBounds(g), gridBounds(g)[0]] : null
    setData('zones', { type: 'FeatureCollection', features: zoneFeatures(scene.missionZones, outer) })
    setData('nominal', { type: 'FeatureCollection', features: zoneFeatures(scene.nominalZones, null) })
  }

  return {
    setScene(next) {
      scene = next
      const { scenario, grids, missionGrid } = next
      setData('grid', {
        type: 'FeatureCollection',
        features: grids.map((g) => {
          const b = gridBounds(g)
          return {
            type: 'Feature',
            properties: { selected: g === missionGrid },
            geometry: { type: 'LineString', coordinates: [...b, b[0]] },
          }
        }),
      })
      renderZones()
      // Fit once per set of grids, not on every mission selection.
      const box = boundsOf(grids)
      const key = JSON.stringify(box)
      if (box && key !== fitted) {
        fitted = key
        map.fitBounds(box, { padding: 30, duration: 0 })
      }

      markers.splice(0).forEach((m) => m.remove())
      for (const m of scenario.missions) {
        markers.push(label(map, m.launch, `${m.pilot_callsign} (${m.id})`, '#4dabf7'))
        markers.push(label(map, m.target, `TARGET ${m.id}`, '#ffd43b'))
      }
      for (const j of scenario.friendly_jammers) markers.push(label(map, j.pos, `${j.id} · ${j.operator_callsign}`, COLORS.friendlyOn))
      for (const t of scenario.enemy_threats) markers.push(label(map, t.pos, t.id, t.type === 'radar' ? COLORS.radar : COLORS.enemy))
    },
    setZoneStates(s) {
      states = s
      renderZones()
    },
    setRoutes(routes) {
      const line = (kind: string, r: RoutePoint[] | null, mission: string, selected: boolean): Feature[] =>
        r && r.length > 1
          ? [{ type: 'Feature', properties: { kind, mission, selected }, geometry: { type: 'LineString', coordinates: r.map((p) => [p.lon, p.lat]) } }]
          : []
      const features = routes.flatMap((r) => [
        ...(r.selected ? line('baseline', r.baseline, r.mission, true) : []),
        ...line('optimised', r.optimised, r.mission, r.selected),
      ])
      setData('routes', { type: 'FeatureCollection', features })
    },
    setDrones(next) {
      const seen = new Set<string>()
      for (const d of next) {
        seen.add(d.mission)
        let marker = drones.get(d.mission)
        if (!marker) {
          const el = document.createElement('div')
          el.className = 'drone-marker'
          el.title = d.mission
          const pulse = document.createElement('span')
          pulse.className = 'drone-pulse'
          const glyph = document.createElement('div')
          glyph.className = 'drone-glyph'
          glyph.innerHTML =
            '<svg width="22" height="22" viewBox="0 0 24 24" fill="#22b8cf" stroke="#0b0f17" stroke-width="2" stroke-linejoin="round"><path d="M12 2 20 20l-8-4-8 4 8-18z"/></svg>'
          el.append(pulse, glyph)
          marker = new maplibregl.Marker({ element: el }).setLngLat([d.pos.lon, d.pos.lat]).addTo(map)
          drones.set(d.mission, marker)
        }
        marker.getElement().classList.toggle('other', !d.selected)
        marker.getElement().classList.toggle('drone-live', true)
        marker.setLngLat([d.pos.lon, d.pos.lat])
      }
      for (const [mission, marker] of drones) {
        if (!seen.has(mission)) {
          marker.remove()
          drones.delete(mission)
        }
      }
    },
    setRfCoverage(coverage) {
      setData('rf-coverage', coverage ?? empty())
    },
    setRfHeatmap(heatmap) {
      setData('rf-heatmap', heatmap ?? empty())
    },
    onHover(cb) {
      map.on('mousemove', (e) => {
        cb({ lat: e.lngLat.lat, lon: e.lngLat.lng })
      })
      map.on('mouseout', () => cb(null))
    },
    setLayerVisibility(vis) {
      visibility = { ...visibility, ...vis }
      applyVisibility()
    },
    set3D(enabled: boolean) {
      is3dMode = enabled
      if (!loaded) return
      if (enabled) {
        map.setTerrain({ source: 'terrain-dem', exaggeration: 2.8 })
        map.easeTo({ pitch: 62, bearing: -20, duration: 800 })
      } else {
        map.setTerrain(null)
        map.easeTo({ pitch: 0, bearing: 0, duration: 600 })
      }
    },
    is3D() {
      return is3dMode
    },
    fitScene() {
      if (!scene || scene.grids.length === 0) return
      const box = boundsOf(scene.grids)
      if (box) {
        map.fitBounds(box, { padding: 48, duration: 450 })
      }
    },
    centerOnDrone(pos?: Position | null) {
      if (pos) {
        map.easeTo({ center: [pos.lon, pos.lat], duration: 450 })
        return
      }
      for (const [, marker] of drones) {
        if (!marker.getElement().classList.contains('other')) {
          const lngLat = marker.getLngLat()
          map.easeTo({ center: [lngLat.lng, lngLat.lat], duration: 450 })
          return
        }
      }
    },
    destroy() {
      map.remove()
    },
  }
}

function zoneStyle(z: Zone, state: ZoneState): { color: string; opacity: number } {
  if (z.kind === 'radar') return { color: COLORS.radar, opacity: 0.2 }
  if (z.kind === 'enemy_jammer') return { color: COLORS.enemy, opacity: 0.2 }
  if (state === 'cut') return { color: COLORS.friendlyCut, opacity: 0.08 }
  if (state === 'pending') return { color: COLORS.friendlyPending, opacity: 0.18 }
  return { color: COLORS.friendlyOn, opacity: 0.22 }
}

/** HTML marker with a dot and a text label (no glyph server needed, works offline). */
function label(map: MlMap, pos: Position, text: string, color: string): maplibregl.Marker {
  const el = document.createElement('div')
  el.className = 'map-label'
  const dot = document.createElement('span')
  dot.className = 'map-label-dot'
  dot.style.background = color
  const span = document.createElement('span')
  span.textContent = text
  el.append(dot, span)
  return new maplibregl.Marker({ element: el, anchor: 'left', offset: [-6, 0] }).setLngLat([pos.lon, pos.lat]).addTo(map)
}
