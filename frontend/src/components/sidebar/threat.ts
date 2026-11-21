// Text for an enemy threat in the sidebar.
import type { Scenario } from '../../api/protocol'

type Threat = Scenario['enemy_threats'][number]

/** One line on an enemy threat: its reach, and how well its position is known. */
export function threatSummary(t: Threat): string {
  if (t.type === 'avoid') return `zone to avoid, radius ${(t.radius_m / 1000).toFixed(1)} km`
  const reach =
    t.type === 'radar' ? `range ${(t.radius_m / 1000).toFixed(1)} km` : `${t.bands.join(', ')} · ${t.eirp_w} W EIRP`
  return `${reach} · ±${t.position_error_m} m`
}
