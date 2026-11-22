// Notification feed, docked at the bottom of the sidebar whatever the panel: it carries the
// command's Confirm, Accept and Refuse buttons, which must stay at hand. Folds down to its
// header.
import { Badge, Button, Group, ScrollArea, Stack, Switch, Text, UnstyledButton } from '@mantine/core'
import { useState } from 'react'
import { send } from '../../api/client'
import { clockLabel } from '../../domain/view'
import { useStore } from '../../store'
import { BellIcon } from '../Icons'

export function Notifications() {
  const snapshot = useStore((s) => s.snapshot)
  const feed = useStore((s) => s.feed)
  const actor = useStore((s) => s.callsign)
  const cuts = useStore((s) => s.cuts)
  const missions = useStore((s) => s.missions)
  const [open, setOpen] = useState(true)
  const [mineOnly, setMineOnly] = useState(false)
  if (!snapshot) return null
  const items = mineOnly ? feed.filter((f) => f.notification.to === actor) : feed
  // An action stays open while what it answers does: a scheduled cut, a pending route request.
  const isOpen = (action: NonNullable<(typeof feed)[number]['notification']['action']>) =>
    action.command === 'cut.confirm'
      ? !!action.cut && cuts[action.cut]?.status === 'scheduled'
      : !!action.mission && missions[action.mission]?.request?.status === 'pending'
  const waiting = feed.filter((f) => f.notification.to === actor && f.notification.action && isOpen(f.notification.action)).length

  return (
    <section className="side-dock">
      <Group justify="space-between" px="sm" py={6} wrap="nowrap">
        <UnstyledButton onClick={() => setOpen((v) => !v)} aria-expanded={open}>
          <Group gap={6} wrap="nowrap">
            <BellIcon size={14} />
            <Text size="sm" fw={700}>Notifications</Text>
            {waiting > 0 && <Badge size="sm" color="orange">{waiting} to answer</Badge>}
            <Text size="xs" c="dimmed">{open ? '▾' : '▸'}</Text>
          </Group>
        </UnstyledButton>
        {open && (
          <Switch size="xs" label="Only mine" checked={mineOnly} onChange={(e) => setMineOnly(e.currentTarget.checked)} />
        )}
      </Group>
      {open && (
        <ScrollArea.Autosize mah={200} px="sm" pb="sm">
          <Stack gap={8}>
            {items.length === 0 && <Text size="sm" c="dimmed">Nothing yet.</Text>}
            {items.map(({ id, atS, notification: n }) => {
              const action = n.action
              const mine = actor === n.to
              const cutId = action?.command === 'cut.confirm' ? action.cut : null
              const missionId = action?.command === 'route.decide' ? action.mission : null
              return (
                <div key={id}>
                  <Text size="xs" c="dimmed">
                    {clockLabel(snapshot.scenario.start_time, atS)} → {n.to}
                  </Text>
                  <Text size="sm">{n.text}</Text>
                  {action && cutId && isOpen(action) && (
                    <Button
                      size="compact-xs"
                      mt={4}
                      disabled={!mine}
                      title={mine ? undefined : `Only ${n.to} can confirm`}
                      onClick={() => send({ type: 'cut.confirm', cut: cutId })}
                    >
                      Confirm {cutId}
                    </Button>
                  )}
                  {action && missionId && isOpen(action) && (
                    <Group gap={6} mt={4}>
                      <Button
                        size="compact-xs"
                        color="green"
                        disabled={!mine}
                        onClick={() => send({ type: 'route.decide', mission: missionId, accept: true })}
                      >
                        Accept
                      </Button>
                      <Button
                        size="compact-xs"
                        color="red"
                        variant="light"
                        disabled={!mine}
                        onClick={() => send({ type: 'route.decide', mission: missionId, accept: false })}
                      >
                        Refuse
                      </Button>
                    </Group>
                  )}
                </div>
              )
            })}
          </Stack>
        </ScrollArea.Autosize>
      )}
    </section>
  )
}
