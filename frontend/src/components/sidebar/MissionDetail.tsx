// One mission in detail: its plan, drone, the cuts of the jammers it concerns, and its actions.
import { Alert, Badge, Button, Group, Progress, Stack, Text } from '@mantine/core'
import { send } from '../../api/client'
import type { PlanUpdated } from '../../api/protocol'
import { mockPlan } from '../../dev/mockPlan'
import { clockLabel, missionOf, routePhases } from '../../domain/view'
import { ROUTE_COLORS } from '../../map/mapView'
import { useStore } from '../../store'
import { STATUS_COLOR } from '../colors'
import { AltitudeProfile } from './AltitudeProfile'
import { CutRow, PanelHeader, SectionTitle } from './parts'

const PLAN_TEXT: Record<PlanUpdated['status'], { title: string; color: string; detail: string }> = {
  baseline: { title: 'Detour', color: 'gray', detail: 'No usable cut: every friendly jammer is assumed on.' },
  preview: { title: 'Direct route, awaiting confirmation', color: 'orange', detail: 'Relies on a cut the EW operator has not confirmed. It cannot be flown yet.' },
  approved: { title: 'Direct route approved', color: 'green', detail: 'The cut is confirmed: ready to launch.' },
  fallback: { title: 'Fallback route', color: 'red', detail: 'The drone could not keep its crossing window: safe route, using no cut.' },
}

export function MissionDetail({ id }: { readonly id: string }) {
  const snapshot = useStore((s) => s.snapshot)
  const callsign = useStore((s) => s.callsign)
  const cuts = useStore((s) => s.cuts)
  const view = useStore((s) => s.missions[id])
  const openList = useStore((s) => s.openList)
  const m = snapshot ? missionOf(snapshot.scenario, id) : undefined
  if (!snapshot || !view || !m) {
    return (
      <Stack gap="xs">
        <PanelHeader title={`Mission ${id}`} onBack={openList} />
        <Text size="sm" c="dimmed">This mission is no longer in your view.</Text>
      </Stack>
    )
  }
  const { start_time, command_callsign } = snapshot.scenario
  const { status, plan, drone } = view
  const label = (t: number) => clockLabel(start_time, t)
  // The server enforces it too; the button only reflects the rule.
  const canLaunch = callsign === m.pilot_callsign || callsign === command_callsign

  const injectMock = () =>
    useStore.getState().apply({
      seq: useStore.getState().seq,
      sim_time_s: useStore.getState().clock.sim_time_s,
      payload: mockPlan(m, Object.values(cuts)),
    })

  const crossings = plan ? routePhases(plan.optimised_route, view.constraints.zones).filter((p) => p.kind === 'crossing') : []
  const compare = plan && (plan.status === 'preview' || plan.status === 'approved')
  const text = plan ? PLAN_TEXT[plan.status] : null
  const takeoff = plan?.optimised_route.find((p) => p.action !== 'ground')?.t
  const arrival = plan?.optimised_route.at(-1)?.t
  const missionCuts = Object.values(cuts)
    .filter((c) => view.constraints.jammers.includes(c.jammer))
    .sort((a, b) => a.start_s - b.start_s)

  return (
    <Stack gap={0}>
      <PanelHeader
        title={`Mission ${m.id} · ${m.drone_id}`}
        badge={<Badge color={STATUS_COLOR[status]}>{status}</Badge>}
        onBack={openList}
      />
      <Text size="xs" c="dimmed" mt={4}>
        Pilot {m.pilot_callsign} · {m.band} · {m.cruise_speed_mps} m/s · battery {Math.round(m.battery_max_s / 60)} min · departure from {label(m.departure_s)}
      </Text>
      <Text size="xs" c="dimmed" mt={4}>
        Concerned: jammers {view.constraints.jammers.join(', ') || 'none'} · threats{' '}
        {view.constraints.threats.join(', ') || 'none'}
      </Text>

      {plan && text ? (
        <Stack gap={6} mt="sm">
          <Alert color={text.color} variant="light" p="xs" title={`${text.title} · rev ${plan.revision}`}>
            <Text size="xs">{plan.status === 'approved' && status !== 'planned' ? 'The cut is confirmed: the drone flies this route.' : text.detail}</Text>
          </Alert>

          {takeoff !== undefined && arrival !== undefined && (
            <Text size="sm">
              {plan.status === 'fallback' ? 'Rerouted' : 'Take-off'} <b>{label(takeoff)}</b> · on target <b>{label(arrival)}</b>
            </Text>
          )}
          {crossings.map((c) => (
            <Text key={`${c.zone}-${c.start}`} size="sm">
              Cross {c.zone}: enter <b>{label(c.start)}</b>, exit by <b>{label(c.end)}</b>
            </Text>
          ))}

          {compare ? (
            <>
              <BatteryBar label="Detour (all jammers on)" pct={plan.metrics.baseline_battery_used_pct} color="gray" />
              <BatteryBar label="Planned route" pct={plan.metrics.optimised_battery_used_pct} color={ROUTE_COLORS[plan.status]} />
              <Text size="lg" fw={800} c="green">
                −{plan.metrics.saved_pct.toFixed(0)}% flight time · −{Math.round(plan.metrics.saved_s)} s
              </Text>
            </>
          ) : (
            <BatteryBar label="Route" pct={plan.metrics.optimised_battery_used_pct} color={ROUTE_COLORS[plan.status]} />
          )}
          <AltitudeProfile profile={plan.profile} levels={snapshot.scenario.terrain.levels_m} />
        </Stack>
      ) : (
        <Text size="sm" mt="sm" c="dimmed">Computing the first plan…</Text>
      )}

      {drone && (
        <Group gap="xs" mt="sm">
          <Badge variant="light">{drone.action}</Badge>
          <Text size="sm">battery used {drone.battery_used_pct.toFixed(1)}%</Text>
          {drone.delay_s > 0 && <Badge color="orange" variant="light">late {Math.round(drone.delay_s)} s</Badge>}
        </Group>
      )}

      <Group mt="sm">
        {canLaunch && (
          <Button
            size="xs"
            disabled={status !== 'planned' || !plan || plan.status === 'preview'}
            title={plan?.status === 'preview' ? 'The plan relies on a cut not confirmed yet' : undefined}
            onClick={() => send({ type: 'mission.launch', mission: m.id })}
          >
            Launch mission
          </Button>
        )}
        {canLaunch && status === 'planned' && (
          <Button
            size="xs"
            variant="light"
            onClick={() => useStore.getState().openEdit(m.id, m.launch, m.target)}
          >
            Change points
          </Button>
        )}
        {status === 'flying' && (
          <Button
            size="xs"
            variant="light"
            color="orange"
            onClick={() => send({ type: 'inject.delay', mission: m.id, delay_s: 120 })}
          >
            Hold drone 120 s (demo)
          </Button>
        )}
        {import.meta.env.DEV && (
          <Button size="xs" variant="subtle" color="gray" onClick={injectMock}>
            Mock plan (dev)
          </Button>
        )}
      </Group>

      {view.constraints.jammers.length > 0 && (
        <Stack gap={6} mt="sm">
          <SectionTitle>Cuts on its jammers</SectionTitle>
          {missionCuts.length === 0 && <Text size="sm" c="dimmed">No cut scheduled.</Text>}
          {missionCuts.map((c) => (
            <CutRow key={c.id} cut={c} showJammer />
          ))}
          <Button
            size="xs"
            variant="light"
            color="orange"
            onClick={() => useStore.getState().openCut({ jammer: null, mission: m.id })}
          >
            Ask for a cut for {m.id}
          </Button>
        </Stack>
      )}
    </Stack>
  )
}

function BatteryBar({ label, pct, color }: { label: string; pct: number; color: string }) {
  return (
    <div>
      <Group justify="space-between">
        <Text size="xs" c="dimmed">{label}</Text>
        <Text size="xs" fw={600}>{pct.toFixed(1)}% battery</Text>
      </Group>
      <Progress value={pct} color={color} />
    </div>
  )
}
