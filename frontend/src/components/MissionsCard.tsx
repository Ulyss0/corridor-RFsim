// Missions in view, to pick the one shown in detail. Hidden for a pilot (one mission).
import { Badge, Card, Group, Stack, Text, UnstyledButton } from '@mantine/core'
import { missionOf } from '../domain/view'
import { useStore } from '../store'
import { PLAN_COLOR, STATUS_COLOR } from './colors'

export function MissionsCard() {
  const snapshot = useStore((s) => s.snapshot)
  const missions = useStore((s) => s.missions)
  const selected = useStore((s) => s.selectedMission)
  const select = useStore((s) => s.selectMission)
  const list = Object.values(missions)
  if (!snapshot || list.length < 2) return null

  return (
    <Card withBorder padding="sm">
      <Text fw={700} mb="xs">Missions</Text>
      <Stack gap={4}>
        {list.map((m) => {
          const id = m.constraints.mission
          const mission = missionOf(snapshot.scenario, id)
          const active = id === selected
          return (
            <UnstyledButton
              key={id}
              onClick={() => select(id)}
              p={6}
              style={{
                borderRadius: 4,
                background: active ? 'var(--mantine-color-dark-5)' : undefined,
              }}
            >
              <Group justify="space-between" wrap="nowrap">
                <div>
                  <Text size="sm" fw={600}>
                    {id} · {mission?.drone_id}
                  </Text>
                  <Text size="xs" c="dimmed">
                    {mission?.pilot_callsign} · jammers {m.constraints.jammers.join(', ') || 'none'}
                  </Text>
                </div>
                <Group gap={4} wrap="nowrap">
                  {m.plan && <Badge size="sm" color={PLAN_COLOR[m.plan.status]}>{m.plan.status}</Badge>}
                  <Badge size="sm" color={STATUS_COLOR[m.status]}>{m.status}</Badge>
                </Group>
              </Group>
            </UnstyledButton>
          )
        })}
      </Stack>
    </Card>
  )
}
