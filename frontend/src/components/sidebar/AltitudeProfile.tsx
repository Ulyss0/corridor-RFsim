// Altitude profile under the planned route (3D): the ground surface and the drone, along the way.
import { Group, Text } from '@mantine/core'
import type { ProfilePoint } from '../../api/protocol'
import { levelColor, levelIndex } from '../../map/levels'

const WIDTH = 300
const HEIGHT = 110
const PAD = { left: 34, right: 6, top: 6, bottom: 18 }

export function AltitudeProfile({ profile, levels }: { readonly profile: ProfilePoint[]; readonly levels: readonly number[] }) {
  const points = profile.filter((p) => p.amsl_m !== null)
  if (points.length < 2) return null
  const total = profile[profile.length - 1].distance_m || 1
  const low = Math.min(...profile.map((p) => p.ground_m))
  const high = Math.max(...points.map((p) => p.amsl_m as number))
  const span = Math.max(high - low, 1)
  const x = (d: number) => PAD.left + (d / total) * (WIDTH - PAD.left - PAD.right)
  const y = (h: number) => PAD.top + (1 - (h - low) / span) * (HEIGHT - PAD.top - PAD.bottom)
  const ground = [
    `M ${x(profile[0].distance_m)} ${y(low)}`,
    ...profile.map((p) => `L ${x(p.distance_m)} ${y(p.ground_m)}`),
    `L ${x(total)} ${y(low)} Z`,
  ].join(' ')
  // The drone line, one stroke per leg so each takes the colour of its level.
  const legs = points.slice(1).map((b, i) => {
    const a = points[i]
    const index = Math.max(levelIndex(levels, a.alt_m), levelIndex(levels, b.alt_m))
    return { a, b, color: index >= 0 ? levelColor(index) : '#22b8cf' }
  })
  const used = [...new Set(points.map((p) => levelIndex(levels, p.alt_m)).filter((i) => i >= 0))].sort((a, b) => a - b)
  return (
    <div>
      <Text size="xs" c="dimmed" mb={2}>Altitude above sea level along the route</Text>
      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} width="100%" role="img" aria-label="Altitude profile">
        <path d={ground} fill="#495057" opacity={0.85} />
        {legs.map(({ a, b, color }) => (
          <line key={a.distance_m} x1={x(a.distance_m)} y1={y(a.amsl_m as number)} x2={x(b.distance_m)} y2={y(b.amsl_m as number)} stroke={color} strokeWidth={2} />
        ))}
        <text x={PAD.left - 4} y={y(high) + 4} fontSize={9} fill="#adb5bd" textAnchor="end">{Math.round(high)} m</text>
        <text x={PAD.left - 4} y={y(low)} fontSize={9} fill="#adb5bd" textAnchor="end">{Math.round(low)} m</text>
        <text x={WIDTH - PAD.right} y={HEIGHT - 4} fontSize={9} fill="#adb5bd" textAnchor="end">{(total / 1000).toFixed(1)} km</text>
      </svg>
      <Group gap={8}>
        {used.map((i) => (
          <Text key={i} size="xs" style={{ color: levelColor(i) }}>■ {levels[i]} m</Text>
        ))}
        <Text size="xs" c="dimmed">■ ground</Text>
      </Group>
    </div>
  )
}
