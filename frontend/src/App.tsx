import { useState } from 'react'
import { GroundTeamScreen } from './components/GroundTeamScreen'
import { MapPanel } from './components/MapPanel'
import { Sidebar } from './components/sidebar/Sidebar'
import { SignIn } from './components/SignIn'
import { TimelinePanel } from './components/TimelinePanel'
import { TopBar } from './components/TopBar'
import { useStore } from './store'

export default function App() {
  const callsign = useStore((s) => s.callsign)
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
        <Sidebar />
      </div>
      <TimelinePanel />
    </div>
  )
}
