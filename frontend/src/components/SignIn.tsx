// Who are you: the callsign decides what the server sends (command, pilot or jammer operator).
// No password: the proof of concept has no authentication.
import { Alert, Button, Card, Chip, Group, Stack, Text, TextInput, Title } from '@mantine/core'
import { useState } from 'react'
import { initialCallsign, signIn } from '../api/client'
import { useStore } from '../store'

const QUICK_ROLES = [
  { callsign: 'CMD', label: 'CMD (Poste de Commandement)', color: 'blue' },
  { callsign: 'PILOT-1', label: 'PILOT-1 (Pilote Drone)', color: 'cyan' },
  { callsign: 'EW-J3', label: 'EW-J3 (Équipe Sol Brouilleur)', color: 'teal' },
  { callsign: 'EW-ALPES', label: 'EW-ALPES (Équipe Sol Annecy)', color: 'teal' },
]

export function SignIn() {
  const error = useStore((s) => s.signInError)
  const [callsign, setCallsign] = useState(initialCallsign)
  const value = callsign.trim()

  return (
    <div className="signin">
      <Card withBorder padding="lg" w={380} className="tactical-signin-card">
        <form
          onSubmit={(e) => {
            e.preventDefault()
            if (value) signIn(value)
          }}
        >
          <Stack gap="sm">
            <Title order={3}>Corridor</Title>
            <Text size="sm" c="dimmed">
              Votre indicatif définit votre vue : le poste de commandement voit tout, un pilote
              voit son vol, et l&apos;équipe au sol accède à son terminal de brouilleur tactique.
            </Text>
            {error && <Alert color="red">{error}</Alert>}
            <TextInput
              label="Indicatif opérationnel (Callsign)"
              placeholder="CMD, PILOT-1, EW-J3…"
              value={callsign}
              onChange={(e) => setCallsign(e.currentTarget.value)}
              autoFocus
            />

            <div>
              <Text size="xs" c="dimmed" mb={4}>
                Sélection rapide pour test :
              </Text>
              <Group gap={6}>
                {QUICK_ROLES.map((r) => (
                  <Chip
                    key={r.callsign}
                    size="xs"
                    checked={callsign.trim().toUpperCase() === r.callsign}
                    onChange={() => {
                      setCallsign(r.callsign)
                      signIn(r.callsign)
                    }}
                  >
                    {r.callsign}
                  </Chip>
                ))}
              </Group>
            </div>

            <Button type="submit" disabled={!value} mt="xs">
              Se connecter au réseau
            </Button>
          </Stack>
        </form>
      </Card>
    </div>
  )
}
