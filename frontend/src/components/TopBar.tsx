import { Badge, Button, Group, SegmentedControl, Text, Title } from '@mantine/core'
import { send, signOut } from '../api/client'
import { clockLabel } from '../domain/view'
import { useViewTime } from '../hooks/useSimNow'
import { useStore } from '../store'
import { ROLE_COLOR } from './colors'

const SPEEDS = ['10', '20', '30', '60']
const CONN_COLOR = { idle: 'gray', open: 'green', connecting: 'yellow', closed: 'red' } as const

export function TopBar() {
  const conn = useStore((s) => s.conn)
  const snapshot = useStore((s) => s.snapshot)
  const clock = useStore((s) => s.clock)
  const callsign = useStore((s) => s.callsign)
  const viewTimeS = useStore((s) => s.viewTimeS)
  const setViewTime = useStore((s) => s.setViewTime)
  const t = useViewTime()

  const role = snapshot?.viewer.role

  return (
    <Group className="topbar" justify="space-between" px="md" wrap="nowrap">
      <Group gap="sm" wrap="nowrap">
        <Title order={4}>Corridor</Title>
        <Badge color={CONN_COLOR[conn]} variant="dot">{conn}</Badge>
      </Group>

      <Group gap="sm" wrap="nowrap">
        <Text ff="monospace" fw={700} size="lg">
          {snapshot ? clockLabel(snapshot.scenario.start_time, t) : '--:--:--'}
        </Text>
        {viewTimeS !== null ? (
          <Button size="xs" color="orange" onClick={() => setViewTime(null)}>
            Back to live
          </Button>
        ) : (
          <Badge color={clock.paused ? 'gray' : 'green'}>{clock.paused ? 'paused' : 'live'}</Badge>
        )}
        <Button size="xs" variant="light" onClick={() => send({ type: 'clock.set', paused: !clock.paused })}>
          {clock.paused ? 'Start' : 'Pause'}
        </Button>
        <SegmentedControl
          size="xs"
          data={SPEEDS.map((s) => ({ value: s, label: `x${s}` }))}
          value={String(clock.factor)}
          onChange={(v) => send({ type: 'clock.set', factor: Number(v) })}
        />
      </Group>

      <Group gap="xs" wrap="nowrap">
        <Text size="sm" fw={600}>{callsign}</Text>
        {role && <Badge color={ROLE_COLOR[role]}>{role}</Badge>}
        <Button size="xs" variant="subtle" onClick={signOut}>
          Switch
        </Button>
      </Group>
    </Group>
  )
}
