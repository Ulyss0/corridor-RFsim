// Default sidebar panel: missions or objects (friendly jammers, enemy threats) in view.
import { Badge, Group, Stack, Tabs, Text, UnstyledButton } from '@mantine/core'
import type { ReactNode } from 'react'
import { friendlyZoneState, missionOf } from '../../domain/view'
import { useLiveSimTime } from '../../hooks/useSimNow'
import { useStore } from '../../store'
import { PLAN_COLOR, STATUS_COLOR, THREAT_COLOR } from '../colors'
import { SectionTitle } from './parts'
import { threatSummary } from './threat'

const ZONE_BADGE = {
  on: { color: 'red', label: 'on' },
  cut: { color: 'green', label: 'cut' },
  pending: { color: 'orange', label: 'cut pending' },
} as const

export function ListView() {
  const tab = useStore((s) => s.listTab)
  const setTab = useStore((s) => s.setListTab)
  const missions = useStore((s) => Object.keys(s.missions).length)
  const scenario = useStore((s) => s.snapshot?.scenario)
  const objects = (scenario?.friendly_jammers.length ?? 0) + (scenario?.enemy_threats.length ?? 0)

  return (
    <Tabs value={tab} onChange={(v) => v && setTab(v as typeof tab)}>
      <Tabs.List grow>
        <Tabs.Tab value="missions">Missions ({missions})</Tabs.Tab>
        <Tabs.Tab value="objects">Objects ({objects})</Tabs.Tab>
      </Tabs.List>
      <Tabs.Panel value="missions" pt="xs">
        <MissionList />
      </Tabs.Panel>
      <Tabs.Panel value="objects" pt="xs">
        <ObjectList />
      </Tabs.Panel>
    </Tabs>
  )
}

function MissionList() {
  const snapshot = useStore((s) => s.snapshot)
  const missions = useStore((s) => s.missions)
  const selected = useStore((s) => s.selectedMission)
  const open = useStore((s) => s.openMission)
  if (!snapshot) return null
  const list = Object.values(missions)
  if (list.length === 0) return <Text size="sm" c="dimmed">No mission in view.</Text>

  return (
    <Stack gap={2}>
      {list.map((m) => {
        const id = m.constraints.mission
        const mission = missionOf(snapshot.scenario, id)
        return (
          <Row key={id} active={id === selected} onClick={() => open(id)}>
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
          </Row>
        )
      })}
    </Stack>
  )
}

function ObjectList() {
  const scenario = useStore((s) => s.snapshot?.scenario)
  const cuts = useStore((s) => s.cuts)
  const open = useStore((s) => s.openObject)
  const now = useLiveSimTime(1)
  if (!scenario) return null
  const cutList = Object.values(cuts)
  const { friendly_jammers: jammers, enemy_threats: threats } = scenario

  return (
    <Stack gap={2}>
      <SectionTitle>Friendly jammers</SectionTitle>
      {jammers.length === 0 && <Text size="sm" c="dimmed">None in view.</Text>}
      {jammers.map((j) => {
        const state = ZONE_BADGE[friendlyZoneState(j.id, cutList, now)]
        const toConfirm = cutList.filter((c) => c.jammer === j.id && c.status === 'scheduled').length
        return (
          <Row key={j.id} onClick={() => open('jammer', j.id)}>
            <div>
              <Text size="sm" fw={600}>{j.id}</Text>
              <Text size="xs" c="dimmed">
                {j.operator_callsign} · {j.bands.join(', ')} · {j.antenna ? 'directional' : 'omni'}
              </Text>
            </div>
            <Group gap={4} wrap="nowrap">
              {toConfirm > 0 && <Badge size="sm" color="orange" variant="light">{toConfirm} to confirm</Badge>}
              <Badge size="sm" color={state.color}>{state.label}</Badge>
            </Group>
          </Row>
        )
      })}

      <SectionTitle>Enemy threats</SectionTitle>
      {threats.length === 0 && <Text size="sm" c="dimmed">None in view.</Text>}
      {threats.map((t) => (
        <Row key={t.id} onClick={() => open('threat', t.id)}>
          <div>
            <Text size="sm" fw={600}>{t.id}</Text>
            <Text size="xs" c="dimmed">{threatSummary(t)}</Text>
          </div>
          <Badge size="sm" color={THREAT_COLOR[t.type]} variant="light">{t.type}</Badge>
        </Row>
      ))}
    </Stack>
  )
}

interface RowProps {
  readonly active?: boolean
  readonly onClick: () => void
  readonly children: ReactNode
}

function Row({ active = false, onClick, children }: RowProps) {
  return (
    <UnstyledButton className="side-row" data-active={active || undefined} onClick={onClick}>
      <Group justify="space-between" wrap="nowrap">{children}</Group>
    </UnstyledButton>
  )
}
