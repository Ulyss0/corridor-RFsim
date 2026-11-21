import { Button, Card, Group, ScrollArea, Stack, Switch, Text } from '@mantine/core'
import { useState } from 'react'
import { send } from '../api/client'
import { clockLabel } from '../domain/view'
import { useStore } from '../store'

export function FeedCard() {
  const snapshot = useStore((s) => s.snapshot)
  const feed = useStore((s) => s.feed)
  const actor = useStore((s) => s.callsign)
  const cuts = useStore((s) => s.cuts)
  const [mineOnly, setMineOnly] = useState(false)
  if (!snapshot) return null
  const items = mineOnly ? feed.filter((f) => f.notification.to === actor) : feed

  return (
    <Card withBorder padding="sm">
      <Group justify="space-between" mb="xs">
        <Text fw={700}>Notifications</Text>
        <Switch size="xs" label="Only mine" checked={mineOnly} onChange={(e) => setMineOnly(e.currentTarget.checked)} />
      </Group>
      <ScrollArea.Autosize mah={260}>
        <Stack gap={8}>
          {items.length === 0 && <Text size="sm" c="dimmed">Nothing yet.</Text>}
          {items.map(({ id, atS, notification: n }) => {
            const action = n.action
            const pending = action && cuts[action.cut]?.status === 'scheduled'
            return (
              <div key={id}>
                <Text size="xs" c="dimmed">
                  {clockLabel(snapshot.scenario.start_time, atS)} → {n.to}
                </Text>
                <Text size="sm">{n.text}</Text>
                {action && pending && (
                  <Button
                    size="compact-xs"
                    mt={4}
                    disabled={actor !== n.to}
                    title={actor !== n.to ? `Only ${n.to} can confirm` : undefined}
                    onClick={() => send({ type: action.command, cut: action.cut })}
                  >
                    Confirm {action.cut}
                  </Button>
                )}
              </div>
            )
          })}
        </Stack>
      </ScrollArea.Autosize>
    </Card>
  )
}
