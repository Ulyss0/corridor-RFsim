import { Alert, ScrollArea, Stack } from '@mantine/core'
import { useState } from 'react'
import { CutsCard } from './components/CutsCard'
import { FeedCard } from './components/FeedCard'
import { GroundTeamScreen } from './components/GroundTeamScreen'
import { MapPanel } from './components/MapPanel'
import { MissionCard } from './components/MissionCard'
import { MissionsCard } from './components/MissionsCard'
import { NewMissionCard } from './components/NewMissionCard'
import { SignIn } from './components/SignIn'
import { TimelinePanel } from './components/TimelinePanel'
import { TopBar } from './components/TopBar'
import { useStore } from './store'

export default function App() {
  const callsign = useStore((s) => s.callsign)
  const lastError = useStore((s) => s.lastError)
  const setError = useStore((s) => s.setError)
  const snapshot = useStore((s) => s.snapshot)
  const role = snapshot?.viewer.role
  const [viewModeOverride, setViewModeOverride] = useState<'command' | 'ground-team' | null>(null)

  if (!callsign) return <SignIn />

  const isGroundTeam =
    (role === 'operator' && viewModeOverride !== 'command') ||
    (role === 'command' && viewModeOverride === 'ground-team')

  if (isGroundTeam) {
    return (
      <GroundTeamScreen
        onSwitchToCommand={
          role === 'command' ? () => setViewModeOverride('command') : undefined
        }
      />
    )
  }

  return (
    <div className="layout">
      <TopBar
        isGroundTeamView={false}
        onToggleGroundTeam={
          role === 'command' ? () => setViewModeOverride('ground-team') : undefined
        }
      />
      <div className="main">
        <MapPanel />
        <ScrollArea className="side">
          <Stack gap="sm" p="sm">
            {lastError && (
              <Alert color="red" withCloseButton onClose={() => setError(null)} title="Server">
                {lastError}
              </Alert>
            )}
            {!snapshot && <Alert color="yellow">Waiting for corridor-server on /ws…</Alert>}
            <NewMissionCard />
            <MissionsCard />
            <MissionCard />
            <CutsCard />
            <FeedCard />
          </Stack>
        </ScrollArea>
      </div>
      <TimelinePanel />
    </div>
  )
}
