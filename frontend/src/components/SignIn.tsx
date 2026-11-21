// Who are you: the callsign decides what the server sends (command, pilot or jammer operator).
// No password: the proof of concept has no authentication.
import { Alert, Button, Card, Stack, Text, TextInput, Title } from '@mantine/core'
import { useState } from 'react'
import { initialCallsign, signIn } from '../api/client'
import { useStore } from '../store'

export function SignIn() {
  const error = useStore((s) => s.signInError)
  const [callsign, setCallsign] = useState(initialCallsign)
  const value = callsign.trim()

  return (
    <div className="signin">
      <Card withBorder padding="lg" w={360}>
        <form
          onSubmit={(e) => {
            e.preventDefault()
            if (value) signIn(value)
          }}
        >
          <Stack gap="sm">
            <Title order={3}>Corridor</Title>
            <Text size="sm" c="dimmed">
              Your callsign sets your view: the command post sees every mission, a pilot only
              theirs, a jammer operator only their jammers and the missions crossing them.
            </Text>
            {error && <Alert color="red">{error}</Alert>}
            <TextInput
              label="Callsign"
              placeholder="CMD, PILOT-1, EW-J3…"
              value={callsign}
              onChange={(e) => setCallsign(e.currentTarget.value)}
              autoFocus
            />
            <Button type="submit" disabled={!value}>
              Connect
            </Button>
          </Stack>
        </form>
      </Card>
    </div>
  )
}
