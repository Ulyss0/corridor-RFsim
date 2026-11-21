import { Badge, Button, Card, Group, NumberInput, Select, Stack, Text } from '@mantine/core'
import { useState } from 'react'
import { send } from '../api/client'
import { clockLabel } from '../domain/view'
import { useLiveSimTime } from '../hooks/useSimNow'
import { useSelectedMission, useStore } from '../store'

export function CutsCard() {
  const snapshot = useStore((s) => s.snapshot)
  const cuts = useStore((s) => s.cuts)
  const actor = useStore((s) => s.callsign)
  const mission = useSelectedMission()
  const now = useLiveSimTime(1)
  const [jammer, setJammer] = useState<string | null>(null)
  const [startMin, setStartMin] = useState<number | string>('')
  const [durationMin, setDurationMin] = useState<number | string>(8)
  if (!snapshot) return null
  const { friendly_jammers: jammers, start_time } = snapshot.scenario
  const operatorOf = (id: string) => jammers.find((j) => j.id === id)?.operator_callsign
  const list = Object.values(cuts).sort((a, b) => a.start_s - b.start_s)

  const defaultStart = Math.ceil(now / 60) + 2
  const start = typeof startMin === 'number' ? startMin : defaultStart
  const duration = typeof durationMin === 'number' ? durationMin : 8
  const selected = jammer ?? jammers[0]?.id ?? null
  // A cut asked from a mission's view is asked for that mission, when its jammer concerns it.
  const forMission =
    selected && mission?.constraints.jammers.includes(selected) ? mission.constraints.mission : null

  return (
    <Card withBorder padding="sm">
      <Text fw={700} mb="xs">EW cuts</Text>
      <Stack gap={6}>
        {list.length === 0 && <Text size="sm" c="dimmed">No cut scheduled.</Text>}
        {list.map((c) => (
          <Group key={c.id} justify="space-between" wrap="nowrap">
            <div>
              <Text size="sm" fw={600}>
                {c.jammer} {clockLabel(start_time, c.start_s)}–{clockLabel(start_time, c.end_s)}
              </Text>
              <Text size="xs" c="dimmed">
                {c.id} · by {c.requested_by}
                {c.requested_for && ` for ${c.requested_for}`}
              </Text>
            </div>
            <Group gap={4} wrap="nowrap">
              <Badge color={c.status === 'confirmed' ? 'green' : 'orange'}>{c.status}</Badge>
              {c.status === 'scheduled' && (
                <Button
                  size="compact-xs"
                  disabled={actor !== operatorOf(c.jammer)}
                  title={actor !== operatorOf(c.jammer) ? `Only ${operatorOf(c.jammer)} can confirm` : undefined}
                  onClick={() => send({ type: 'cut.confirm', cut: c.id })}
                >
                  Confirm
                </Button>
              )}
              <Button size="compact-xs" variant="subtle" color="red" onClick={() => send({ type: 'cut.cancel', cut: c.id })}>
                Cancel
              </Button>
            </Group>
          </Group>
        ))}
      </Stack>

      <Text size="sm" fw={600} mt="md" mb={4}>Schedule a cut</Text>
      <Group gap="xs" align="end" wrap="nowrap">
        <Select size="xs" w={80} label="Jammer" data={jammers.map((j) => j.id)} value={selected} onChange={setJammer} allowDeselect={false} />
        <NumberInput size="xs" w={90} label="Start (min)" min={0} value={startMin} placeholder={String(defaultStart)} onChange={setStartMin} />
        <NumberInput size="xs" w={90} label="Length (min)" min={1} value={durationMin} onChange={setDurationMin} />
        <Button
          size="xs"
          disabled={!selected}
          onClick={() =>
            selected &&
            send({
              type: 'cut.schedule',
              jammer: selected,
              start_s: start * 60,
              end_s: (start + duration) * 60,
              mission: forMission,
            })
          }
        >
          Add
        </Button>
      </Group>
      <Text size="xs" c="dimmed" mt={4}>
        {clockLabel(start_time, start * 60)} → {clockLabel(start_time, (start + duration) * 60)}
        {forMission && ` · for ${forMission}`}
      </Text>
    </Card>
  )
}
