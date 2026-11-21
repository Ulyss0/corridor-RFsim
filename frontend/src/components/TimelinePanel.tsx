// Timeline: one row per friendly jammer in view (its cuts) + one row per drone in view (its
// planned route, by phase).
// The orange cursor follows live time; drag it to scrub (map follows), "Back to live" resets.
import { useEffect, useRef } from 'react'
import { ActionIcon, Button, Group, Text, Tooltip } from '@mantine/core'
import { Timeline } from 'vis-timeline/standalone'
import 'vis-timeline/styles/vis-timeline-graph2d.min.css'
import { clockLabel, type Phase, routePhases } from '../domain/view'
import { useLiveSimTime } from '../hooks/useSimNow'
import { useStore } from '../store'
import { CrosshairIcon, RotateCcwIcon, ZoomInIcon, ZoomOutIcon } from './Icons'

const CURSOR = 'cursor'

// vis-timeline labels use the browser's local time; the app shows UTC. Shift the dates so the
// axis reads UTC wall-clock time.
function toTl(startTime: string, t: number): Date {
  const ms = Date.parse(startTime) + t * 1000
  return new Date(ms + new Date(ms).getTimezoneOffset() * 60_000)
}
function fromTl(startTime: string, d: Date): number {
  const base = Date.parse(startTime)
  return (d.getTime() - new Date(base).getTimezoneOffset() * 60_000 - base) / 1000
}
const droneGroup = (mission: string) => `__drone__${mission}`

interface Item {
  id: string
  group: string
  start: Date
  end: Date
  content: string
  className: string
  title?: string
}

export function TimelinePanel() {
  const container = useRef<HTMLDivElement>(null)
  const timeline = useRef<Timeline | null>(null)
  const snapshot = useStore((s) => s.snapshot)
  const cuts = useStore((s) => s.cuts)
  const missions = useStore((s) => s.missions)
  const viewTimeS = useStore((s) => s.viewTimeS)
  const setViewTime = useStore((s) => s.setViewTime)
  const live = useLiveSimTime(4)
  const start = snapshot?.scenario.start_time

  const fitTimeline = () => {
    if (!snapshot) return
    const st = snapshot.scenario.start_time
    const endS = Math.max(
      1800,
      ...snapshot.scenario.missions.map((m) => m.battery_max_s + m.departure_s),
    )
    timeline.current?.setWindow(toTl(st, 0), toTl(st, endS), { animation: true })
  }

  // Create once the scenario is known (groups and time origin come from it).
  useEffect(() => {
    if (!container.current || !snapshot) return
    const st = snapshot.scenario.start_time
    const groups = [
      ...snapshot.scenario.friendly_jammers.map((j) => ({ id: j.id, content: `${j.id} · ${j.operator_callsign}` })),
      ...snapshot.scenario.missions.map((m) => ({ id: droneGroup(m.id), content: `${m.drone_id} (${m.id})` })),
    ]
    const horizon = Math.max(1800, ...snapshot.scenario.missions.map((m) => m.battery_max_s + m.departure_s))
    const tl = new Timeline(container.current, [], groups, {
      start: toTl(st, 0),
      end: toTl(st, horizon),
      stack: false,
      showCurrentTime: false,
      zoomMin: 60_000,
      orientation: 'top',
      margin: { item: 4 },
      editable: false,
      selectable: false,
    })
    tl.addCustomTime(toTl(st, 0), CURSOR)
    tl.on('timechange', (props: { id: string; time: Date }) => {
      if (props.id === CURSOR) useStore.getState().setViewTime(Math.max(0, fromTl(st, props.time)))
    })
    tl.on('click', (props: { time?: Date; what?: string }) => {
      if (props.time && (props.what === 'background' || props.what === 'axis' || props.what === 'item')) {
        useStore.getState().setViewTime(Math.max(0, fromTl(st, props.time)))
      }
    })
    timeline.current = tl
    return () => {
      tl.destroy()
      timeline.current = null
    }
  }, [snapshot])

  // Items: cuts, then the planned route and the detour by phase.
  useEffect(() => {
    if (!start) return
    const next: Item[] = Object.values(cuts).map((c) => ({
      id: c.id,
      group: c.jammer,
      start: toTl(start, c.start_s),
      end: toTl(start, c.end_s),
      content: c.status,
      className: `cut cut-${c.status}`,
      title: `${c.id} by ${c.requested_by}`,
    }))
    const phaseItems = (group: string, phases: Phase[], prefix: string): Item[] =>
      phases
        .filter((ph) => ph.end > ph.start)
        .map((ph, i) => {
          const label = ph.zone ? `${ph.kind} ${ph.zone}` : ph.kind
          return {
            id: `${prefix}-${i}`,
            group,
            start: toTl(start, ph.start),
            end: toTl(start, ph.end),
            content: label,
            className: `seg seg-${ph.kind}`,
            title: `${label}: ${clockLabel(start, ph.start)} → ${clockLabel(start, ph.end)}`,
          }
        })

    for (const [mission, view] of Object.entries(missions)) {
      const route = view.plan?.optimised_route ?? []
      if (route.length > 0) {
        const m = snapshot?.missions.find((sm) => sm.constraints.mission === mission)
        const zones = m?.constraints.zones ?? []
        next.push(...phaseItems(droneGroup(mission), routePhases(route, zones), `opt-${mission}`))
      }
    }
    timeline.current?.setItems(next)
  }, [cuts, missions, start, snapshot])

  // Cursor follows live time unless the user is scrubbing.
  useEffect(() => {
    if (!start || !timeline.current) return
    timeline.current.setCustomTime(toTl(start, viewTimeS ?? live), CURSOR)
  }, [live, viewTimeS, start])

  return (
    <div className="timeline-shell">
      <Group className="timeline-header" justify="space-between" wrap="nowrap">
        <Group gap="md" wrap="nowrap">
          <Text size="11px" fw={700} c="dimmed" style={{ letterSpacing: '0.06em' }}>
            4D SCHEDULE &amp; ROUTE TIMELINE
          </Text>
          <Group gap="xs" wrap="nowrap" visibleFrom="sm">
            <TimelineChip color="#f08c00" label="Scheduled cut" />
            <TimelineChip color="#2f9e44" label="Confirmed cut" />
            <TimelineChip color="#495057" label="Ground wait" />
            <TimelineChip color="#22b8cf" label="Fly" />
            <TimelineChip color="#1971c2" label="Hover" />
            <TimelineChip color="#2f9e44" borderColor="#8ce99a" label="Zone crossing" />
            <TimelineChip color="transparent" borderColor="#adb5bd" label="Detour" dashed />
          </Group>
        </Group>

        <Group gap={4} wrap="nowrap">
          {viewTimeS !== null && (
            <Button
              size="compact-xs"
              variant="light"
              color="orange"
              leftSection={<RotateCcwIcon size={11} />}
              onClick={() => setViewTime(null)}
            >
              Back to live
            </Button>
          )}
          <Tooltip label="Zoom in">
            <ActionIcon
              size="xs"
              variant="subtle"
              color="gray"
              aria-label="Zoom timeline in"
              onClick={() => timeline.current?.zoomIn(0.4)}
            >
              <ZoomInIcon size={13} />
            </ActionIcon>
          </Tooltip>
          <Tooltip label="Zoom out">
            <ActionIcon
              size="xs"
              variant="subtle"
              color="gray"
              aria-label="Zoom timeline out"
              onClick={() => timeline.current?.zoomOut(0.4)}
            >
              <ZoomOutIcon size={13} />
            </ActionIcon>
          </Tooltip>
          <Tooltip label="Fit full mission timeline">
            <ActionIcon
              size="xs"
              variant="subtle"
              color="gray"
              aria-label="Fit timeline"
              onClick={fitTimeline}
            >
              <CrosshairIcon size={13} />
            </ActionIcon>
          </Tooltip>
        </Group>
      </Group>

      <div ref={container} className="timeline" />
    </div>
  )
}

interface TimelineChipProps {
  readonly color: string
  readonly borderColor?: string
  readonly label: string
  readonly dashed?: boolean
}

function TimelineChip({ color, borderColor, label, dashed }: TimelineChipProps) {
  const borderStyle = dashed ? 'dashed' : 'solid'
  const border = borderColor ? `1px ${borderStyle} ${borderColor}` : undefined
  return (
    <Group gap={4} wrap="nowrap">
      <span
        style={{
          width: 8,
          height: 8,
          borderRadius: 2,
          background: color,
          border,
          display: 'inline-block',
          flexShrink: 0,
        }}
      />
      <Text size="10px" c="dimmed">
        {label}
      </Text>
    </Group>
  )
}
