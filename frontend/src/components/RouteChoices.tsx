import { Alert, Badge, Button, Group, Paper, Progress, SegmentedControl, Stack, Text, UnstyledButton } from '@mantine/core'
import { useState } from 'react'
import { send } from '../api/client'
import type { Cut, LostLinkAction, Mission, MissionView, OnTimeout, RouteChoice, RouteReason } from '../api/protocol'
import {
  choiceTitle,
  choosing,
  clockLabel,
  durationLabel,
  highlightedChoice,
  kindTitle,
  LOST_LINK_LABELS,
  reasonTag,
  reasonText,
  refusedKind,
  straightAnyway,
} from '../domain/view'
import { CHOICE_COLORS } from '../map/mapView'
import { useStore } from '../store'

/** Route choices of a mission waiting on the ground, laid out like Google Maps directions: one
 * row per route (time first), the selected one compared with the detour, one action. Then the
 * command's answer once a route is picked. The server enforces who may pick and answer. */
export function RouteChoices({ view, mission: m, startTime, command }: {
  view: MissionView
  mission: Mission
  startTime: string
  command: string
}) {
  const pilot = m.pilot_callsign
  const callsign = useStore((s) => s.callsign)
  const cuts = useStore((s) => s.cuts)
  const focused = useStore((s) => s.focusedChoice)
  const selected = useStore((s) => s.selectedChoice)
  const focus = useStore((s) => s.focusChoice)
  const select = useStore((s) => s.selectChoice)
  const [onTimeout, setOnTimeout] = useState<OnTimeout>('avoid')
  const label = (t: number) => clockLabel(startTime, t)
  const mission = view.constraints.mission
  // The pilot picks its route; the command answers the cuts a route needs, nothing more.
  const isPilot = callsign === pilot

  const choices = view.plan?.choices ?? []
  const open = isPilot && choosing(view) && choices.length > 0
  // After a refusal the detour is back on top and the refused route stays grey.
  const refused = refusedKind(view)
  // Refused but flown anyway (plan B "straight anyway"): that route stays the one on top.
  const straight = straightAnyway(view)
  const shown = highlightedChoice(choices, focused, selected ?? straight, refused)
  // The selection, not the hover, is what the button acts on.
  const picked = highlightedChoice(choices, null, selected ?? straight, refused)
  const detour = choices.find((c) => c.kind === 'safe') ?? null
  const fastestS = Math.min(...choices.map((c) => c.flight_s))

  return (
    <Stack gap={6} mt="sm">
      {view.request && <RequestStatus view={view} cuts={cuts} label={label} isCommand={callsign === command} />}
      {!isPilot && choosing(view) && choices.length > 0 && (
        <Text size="xs" c="dimmed">Waiting for {pilot} to pick a route.</Text>
      )}

      {open && (
        <>
          <Group justify="space-between" wrap="nowrap">
            <Text size="sm" fw={700}>Routes</Text>
            <SegmentedControl
              size="xs"
              value={m.cross_radars ? 'cross' : 'avoid'}
              onChange={(v) =>
                send({
                  type: 'mission.update',
                  mission: m.id,
                  launch: m.launch,
                  target: m.target,
                  departure_s: m.departure_s,
                  cross_radars: v === 'cross',
                  lost_link: m.lost_link,
                })
              }
              data={[
                { value: 'avoid', label: 'Avoid radars' },
                { value: 'cross', label: 'Fly through radars' },
              ]}
            />
          </Group>
          <Group justify="space-between" wrap="nowrap">
            <Text size="xs" c="dimmed">Lost-link failsafe</Text>
            <SegmentedControl
              size="xs"
              value={m.lost_link ?? 'rth'}
              onChange={(v) =>
                send({
                  type: 'mission.update',
                  mission: m.id,
                  launch: m.launch,
                  target: m.target,
                  departure_s: m.departure_s,
                  cross_radars: m.cross_radars,
                  lost_link: v as LostLinkAction,
                })
              }
              data={[
                { value: 'rth', label: 'RTH' },
                { value: 'continue', label: 'Continue' },
                { value: 'land', label: 'Land' },
              ]}
            />
          </Group>
          {choices.map((c) => (
            <ChoiceRow
              key={c.kind}
              choice={c}
              detour={detour}
              selected={c.kind === picked?.kind}
              refused={c.kind === refused}
              fastest={c.flight_s === fastestS && choices.length > 1}
              label={label}
              onFocus={focus}
              onSelect={() => select(c.kind)}
            />
          ))}

          {shown && detour && shown.saved_s > 0 && <Comparison route={shown} detour={detour} />}

          {picked && picked.cuts.length > 0 && picked.worth_asking && picked.kind !== refused && !detour && (
            <Text size="xs" c="dimmed">
              Needs {picked.cuts.map((p) => `${p.jammer} off ${label(p.start_s)}–${label(p.end_s)}`).join(', ')}. No
              route without this cut: the drone waits for the command's answer.
            </Text>
          )}
          {picked && picked.cuts.length > 0 && picked.worth_asking && picked.kind !== refused && detour && (
            <div>
              <Text size="xs" c="dimmed">
                Needs {picked.cuts.map((p) => `${p.jammer} off ${label(p.start_s)}–${label(p.end_s)}`).join(', ')}. Plan B
                if the command refuses or has not answered 2 min before take-off:
              </Text>
              <SegmentedControl
                size="xs"
                fullWidth
                value={onTimeout}
                onChange={(v) => setOnTimeout(v as OnTimeout)}
                data={[
                  { value: 'avoid', label: 'Fly the detour' },
                  { value: 'cross', label: 'Fly straight anyway' },
                ]}
              />
              {onTimeout === 'cross' && (
                <Text size="xs" c="orange" mt={4}>
                  Straight anyway: {picked.cuts.map((p) => p.jammer).join(', ')} stays on, the control link may be
                  lost while crossing.
                </Text>
              )}
            </div>
          )}
          {picked && (
            <Button
              fullWidth
              disabled={!picked.worth_asking || picked.kind === refused || picked.kind === straight}
              color={picked.cuts.length > 0 ? undefined : 'gray'}
              onClick={() => {
                focus(null)
                // Without a detour, no answer means no flight: never cut without the command.
                send({ type: 'route.choose', mission, kind: picked.kind, on_timeout: detour ? onTimeout : 'avoid' })
              }}
            >
              {picked.kind === straight
                ? 'Refused: flying straight anyway'
                : picked.kind === refused
                ? 'Refused by the command'
                : !picked.worth_asking
                ? 'Gain too small to ask the command'
                : picked.cuts.length > 0
                  ? `Request ${choiceTitle(picked).toLowerCase()}`
                  : 'Fly the detour'}
            </Button>
          )}
        </>
      )}
    </Stack>
  )
}

const REASON_COLOR: Record<RouteReason['effect'], string> = {
  avoided: 'gray',
  climbed_over: 'blue',
  crossed_in_cut: 'teal',
  crossed: 'red',
}

function ChoiceRow({ choice: c, detour, selected, refused, fastest, label, onFocus, onSelect }: {
  choice: RouteChoice
  detour: RouteChoice | null
  selected: boolean
  refused: boolean
  fastest: boolean
  label: (t: number) => string
  onFocus: (kind: RouteChoice['kind'] | null) => void
  onSelect: () => void
}) {
  const color = CHOICE_COLORS[c.kind]
  const batterySaved = detour ? detour.battery_used_pct - c.battery_used_pct : 0
  return (
    <UnstyledButton onClick={onSelect} onMouseEnter={() => onFocus(c.kind)} onMouseLeave={() => onFocus(null)}>
      <Paper
        withBorder
        p="xs"
        style={{
          borderLeft: `5px solid ${selected ? color : 'var(--mantine-color-dark-3)'}`,
          background: selected ? 'var(--mantine-color-dark-5)' : undefined,
          // Shown for comparison only: the gain does not justify asking for a cut.
          opacity: c.worth_asking && !refused ? 1 : 0.55,
        }}
      >
        <Group justify="space-between" wrap="nowrap" align="flex-start">
          <div>
            <Text fw={800} size="lg" c={fastest && c.worth_asking ? 'green' : undefined} lh={1.1}>
              {durationLabel(c.flight_s)}
            </Text>
            <Text size="xs" c="dimmed">
              {choiceTitle(c)} · on target {label(c.route[c.route.length - 1].t)}
            </Text>
            <Group gap={4} mt={2}>
              {/* Radars crossed have their own badge below. */}
              {c.reasons.filter((r) => !(r.kind === 'radar' && r.effect === 'crossed')).map((r) => (
                <Badge key={`${r.threat}-${r.effect}`} size="xs" color={REASON_COLOR[r.effect]} variant="light" title={reasonText(r, label)}>
                  {reasonTag(r)}
                </Badge>
              ))}
              {c.radars.length > 0 && (
                <Badge size="xs" color="orange" variant="light">
                  Seen by {c.radars.join(', ')}
                </Badge>
              )}
              {!c.worth_asking && (
                <Badge size="xs" color="gray" variant="light">
                  Gain too small to ask
                </Badge>
              )}
              <Badge size="xs" color={LOST_LINK_LABELS[c.lost_link].color} variant="outline" title={LOST_LINK_LABELS[c.lost_link].detail}>
                {LOST_LINK_LABELS[c.lost_link].short}
              </Badge>
              {refused && (
                <Badge size="xs" color="red" variant="light">
                  Refused by the command
                </Badge>
              )}
            </Group>
          </div>
          <Stack gap={2} align="flex-end">
            {c.saved_s > 0 ? (
              <>
                <Text size="sm" fw={700} c="green" style={{ whiteSpace: 'nowrap' }}>−{durationLabel(c.saved_s)}</Text>
                <Text size="xs" fw={600} c="teal" style={{ whiteSpace: 'nowrap' }}>−{batterySaved.toFixed(1)}% battery</Text>
              </>
            ) : c.cuts.length > 0 ? (
              <Badge color="orange" variant="light">needs {c.cuts.map((p) => p.jammer).join(', ')} cut</Badge>
            ) : (
              <Badge color="gray" variant="light">no cut</Badge>
            )}
          </Stack>
        </Group>
      </Paper>
    </UnstyledButton>
  )
}

/** Side by side bars, route against detour, for flight time and battery: what Google Maps
 * shows as "x min faster", made visible. */
function Comparison({ route, detour }: { route: RouteChoice; detour: RouteChoice }) {
  const color = CHOICE_COLORS[route.kind]
  const timeMax = Math.max(route.flight_s, detour.flight_s)
  const batterySaved = detour.battery_used_pct - route.battery_used_pct
  return (
    <Paper p="xs" radius="md" bg="dark.6">
      <Text size="xs" c="dimmed" mb={4}>{choiceTitle(route)} against the detour</Text>
      <Group gap="md" grow align="flex-start">
        <div>
          <Text size="xl" fw={900} c="green" lh={1}>−{durationLabel(route.saved_s)}</Text>
          <Text size="xs" c="dimmed">flight time (−{route.saved_pct.toFixed(0)}%)</Text>
        </div>
        <div>
          <Text size="xl" fw={900} c="teal" lh={1}>−{batterySaved.toFixed(1)}%</Text>
          <Text size="xs" c="dimmed">battery used</Text>
        </div>
      </Group>
      <Stack gap={4} mt="xs">
        <Bar label="Time" value={route.flight_s} max={timeMax} text={durationLabel(route.flight_s)} color={color} />
        <Bar label="" value={detour.flight_s} max={timeMax} text={durationLabel(detour.flight_s)} color="gray" />
        <Bar label="Battery" value={route.battery_used_pct} max={100} text={`${route.battery_used_pct.toFixed(1)}%`} color={color} />
        <Bar label="" value={detour.battery_used_pct} max={100} text={`${detour.battery_used_pct.toFixed(1)}%`} color="gray" />
      </Stack>
      <Group gap={12} mt={4}>
        <Legend color={color} text={choiceTitle(route)} />
        <Legend color="gray" text="Detour" />
      </Group>
    </Paper>
  )
}

function Bar({ label, value, max, text, color }: { label: string; value: number; max: number; text: string; color: string }) {
  return (
    <Group gap={6} wrap="nowrap">
      <Text size="xs" c="dimmed" w={48}>{label}</Text>
      <Progress value={(100 * value) / max} color={color} size="lg" style={{ flex: 1 }} />
      <Text size="xs" w={70} ta="right">{text}</Text>
    </Group>
  )
}

function Legend({ color, text }: { color: string; text: string }) {
  return (
    <Group gap={4}>
      <span style={{ width: 10, height: 10, borderRadius: 2, background: color === 'gray' ? 'var(--mantine-color-gray-6)' : color }} />
      <Text size="xs" c="dimmed">{text}</Text>
    </Group>
  )
}

function RequestStatus({ view, cuts, label, isCommand }: {
  view: MissionView
  cuts: Record<string, Cut>
  label: (t: number) => string
  isCommand: boolean
}) {
  const r = view.request
  if (!r) return null
  const mission = view.constraints.mission
  const asked = r.cuts.map((id) => cuts[id]).filter((c): c is Cut => Boolean(c))
  const what = asked.map((c) => `${c.jammer} off ${label(c.start_s)}–${label(c.end_s)}`).join(', ')
  const by = r.decided_by === 'timeout' ? 'no answer in time' : r.decided_by === 'launch' ? 'take-off before an answer' : r.decided_by
  const title = kindTitle(r.kind)

  if (r.status === 'pending') {
    return (
      <Alert color="orange" variant="light" p="xs" title={`${title} · waiting for the command`}>
        <Text size="xs">Asks {what} (−{durationLabel(r.saved_s)}, −{r.saved_pct.toFixed(0)}% flight time).</Text>
        {r.deadline_s !== null && (
          <Text size="xs">
            Answer by <b>{label(r.deadline_s)}</b>. If refused or unanswered:{' '}
            {r.on_timeout === 'cross' ? 'fly straight anyway, jammer on' : 'fly the detour'}.
          </Text>
        )}
        {isCommand && (
          <Group gap={6} mt={6}>
            <Button size="compact-sm" color="green" onClick={() => send({ type: 'route.decide', mission, accept: true })}>
              Accept
            </Button>
            <Button size="compact-sm" color="red" variant="light" onClick={() => send({ type: 'route.decide', mission, accept: false })}>
              Refuse
            </Button>
          </Group>
        )}
      </Alert>
    )
  }
  if (r.status === 'accepted') {
    return (
      <Alert color="green" variant="light" p="xs" title={`${title} · go`}>
        <Text size="xs">{r.cuts.length === 0 ? 'No cut needed.' : `Cuts confirmed (${by}): ${what || 'see the timeline'}.`}</Text>
      </Alert>
    )
  }
  if (r.on_timeout === 'cross' && r.cuts.length > 0) {
    return (
      <Alert color="orange" variant="light" p="xs" title={`${title} refused · flying straight anyway`}>
        <Text size="xs">
          Refused ({by}). As chosen, the drone keeps the direct route through {r.jammers.join(', ')}: the
          jammer stays on, the control link may be lost while crossing.
        </Text>
      </Alert>
    )
  }
  return (
    <Alert color="red" variant="light" p="xs" title={`${title} refused`}>
      <Text size="xs">
        Refused ({by}).{' '}
        {view.plan?.choices.some((c) => c.kind === 'safe')
          ? 'The drone flies the detour, now selected below.'
          : 'No route without a cut: the mission cannot start. Ask again later or move the launch point.'}
      </Text>
    </Alert>
  )
}
