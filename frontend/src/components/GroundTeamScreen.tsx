import {
  Alert,
  Badge,
  Button,
  Card,
  Divider,
  Group,
  Modal,
  Progress,
  Radio,
  ScrollArea,
  Select,
  Stack,
  Text,
  TextInput,
  ThemeIcon,
  Title,
} from '@mantine/core'
import { useState } from 'react'
import { send, signOut } from '../api/client'
import { clockLabel, distanceM, friendlyZoneState, positionAt } from '../domain/view'
import { useLiveSimTime } from '../hooks/useSimNow'
import { useStore } from '../store'
import {
  AlertTriangleIcon,
  BellIcon,
  BombIcon,
  CheckIcon,
  ClockIcon,
  CrosshairIcon,
  DroneIcon,
  RadioIcon,
  ShieldCheckIcon,
  SignalIcon,
  XIcon,
} from './Icons'
import { MapPanel } from './MapPanel'

const REFUSAL_REASONS = [
  "Tirs d'artillerie / Prise d'obus (brouilleur maintenu actif)",
  'Équipe coupée du brouilleur / Liaison rompue',
  'Menace drone ennemie immédiate dans le secteur',
  'Refus tactique terrain / Position compromise',
  'Autre motif tactique',
]

function formatSeconds(totalS: number): string {
  const s = Math.max(0, Math.floor(totalS))
  const m = Math.floor(s / 60)
  const remS = s % 60
  return `${m.toString().padStart(2, '0')}:${remS.toString().padStart(2, '0')}`
}

export function GroundTeamScreen({ onSwitchToCommand }: { onSwitchToCommand?: () => void }) {
  const snapshot = useStore((s) => s.snapshot)
  const cuts = useStore((s) => s.cuts)
  const feed = useStore((s) => s.feed)
  const actor = useStore((s) => s.callsign)
  const missions = useStore((s) => s.missions)
  const now = useLiveSimTime(1)

  const [activeTab, setActiveTab] = useState<'terminal' | 'map'>('terminal')
  const [selectedJammerId, setSelectedJammerId] = useState<string | null>(null)

  // Refusal modal state
  const [refusingCutId, setRefusingCutId] = useState<string | null>(null)
  const [selectedReason, setSelectedReason] = useState<string>(REFUSAL_REASONS[0])
  const [customReason, setCustomReason] = useState<string>('')

  // Emergency override confirmation modal
  const [emergencyCutId, setEmergencyCutId] = useState<string | null>(null)

  if (!snapshot) return null

  const { friendly_jammers: jammers, start_time } = snapshot.scenario
  const currentJammer = jammers.find((j) => j.id === (selectedJammerId ?? jammers[0]?.id)) ?? jammers[0]

  if (!currentJammer) {
    return (
      <Card withBorder p="xl" m="md">
        <Text c="dimmed">Aucun brouilleur sous votre commandement dans ce scénario.</Text>
      </Card>
    )
  }

  const allCuts = Object.values(cuts)
  const jammerCuts = allCuts.filter((c) => c.jammer === currentJammer.id)
  const pendingCuts = jammerCuts.filter((c) => c.status === 'scheduled')
  const confirmedCuts = jammerCuts.filter((c) => c.status === 'confirmed')

  // Live state of current jammer
  const zoneState = friendlyZoneState(currentJammer.id, allCuts, now)

  // Active cut currently silencing the jammer, if any
  const activeCut = confirmedCuts.find((c) => c.start_s <= now && now < c.end_s)

  // Next upcoming confirmed cut
  const nextCut = confirmedCuts
    .filter((c) => c.start_s > now)
    .sort((a, b) => a.start_s - b.start_s)[0]

  // Submit refusal
  const handleConfirmRefusal = () => {
    if (!refusingCutId) return
    const finalReason = selectedReason === 'Autre motif tactique' ? customReason.trim() : selectedReason
    send({
      type: 'cut.cancel',
      cut: refusingCutId,
      reason: finalReason || "Refus de l'équipe au sol",
    })
    setRefusingCutId(null)
    setSelectedReason(REFUSAL_REASONS[0])
    setCustomReason('')
  }

  // Emergency override
  const handleEmergencyAbort = (cutId: string) => {
    send({
      type: 'cut.cancel',
      cut: cutId,
      reason: "Urgence terrain : Tirs d'artillerie / Prise d'obus - Rallumage immédiat du brouilleur",
    })
    setEmergencyCutId(null)
  }

  // Missions crossing this jammer
  const crossingMissions = Object.values(missions).filter((m) =>
    m.constraints.jammers.includes(currentJammer.id)
  )

  return (
    <div className="ground-terminal-root">
      {/* Refusal Modal */}
      <Modal
        opened={refusingCutId !== null}
        onClose={() => setRefusingCutId(null)}
        title={
          <Group gap="xs">
            <ThemeIcon color="red" variant="light" size="sm">
              <BombIcon size={16} />
            </ThemeIcon>
            <Text fw={700} c="red.4">
              Refuser la demande de coupure
            </Text>
          </Group>
        }
        centered
        styles={{
          content: {
            background: '#1a1b1e',
            border: '1px solid var(--mantine-color-red-9)',
          },
        }}
      >
        <Stack gap="md">
          <Text size="sm">
            Indiquez le motif tactique pour informer immédiatement le poste de commandement et le pilote du drone :
          </Text>

          <Radio.Group value={selectedReason} onChange={setSelectedReason}>
            <Stack gap="xs">
              {REFUSAL_REASONS.map((reason) => (
                <Radio key={reason} value={reason} label={reason} color="red" />
              ))}
            </Stack>
          </Radio.Group>

          {selectedReason === 'Autre motif tactique' && (
            <TextInput
              placeholder="Précisez la raison tactique…"
              value={customReason}
              onChange={(e) => setCustomReason(e.currentTarget.value)}
              autoFocus
            />
          )}

          <Group justify="flex-end" mt="sm">
            <Button variant="default" onClick={() => setRefusingCutId(null)}>
              Annuler
            </Button>
            <Button
              color="red"
              leftSection={<BombIcon size={15} />}
              onClick={handleConfirmRefusal}
            >
              Confirmer le refus
            </Button>
          </Group>
        </Stack>
      </Modal>

      {/* Emergency Abort Confirmation Modal */}
      <Modal
        opened={emergencyCutId !== null}
        onClose={() => setEmergencyCutId(null)}
        title={
          <Group gap="xs">
            <ThemeIcon color="red" variant="filled" size="sm">
              <AlertTriangleIcon size={16} />
            </ThemeIcon>
            <Text fw={700} c="red.4">
              🚨 Rallumage d&apos;Urgence du Brouilleur
            </Text>
          </Group>
        }
        centered
        styles={{
          content: {
            background: '#1e1414',
            border: '2px solid #e03131',
          },
        }}
      >
        <Stack gap="md">
          <Alert color="red" title="Alerte Tactique - Obus / Menace Immédiate">
            Vous êtes sur le point de couper la fenêtre de silence radio et de rallumer immédiatement le brouilleur.
            Le pilote et le commandement seront immédiatement notifiés de l&apos;urgence artillerie / rupture de sécurité.
          </Alert>
          <Text size="sm" c="dimmed">
            Action recommandée en cas de prise d&apos;obus, menace directe ou rupture de liaison.
          </Text>
          <Group justify="flex-end" mt="sm">
            <Button variant="default" onClick={() => setEmergencyCutId(null)}>
              Annuler
            </Button>
            <Button
              color="red"
              variant="filled"
              leftSection={<BombIcon size={16} />}
              onClick={() => emergencyCutId && handleEmergencyAbort(emergencyCutId)}
            >
              RALLUMER LE BROUILLEUR IMMÉDIATEMENT
            </Button>
          </Group>
        </Stack>
      </Modal>

      {/* Rugged Field Terminal Container */}
      <div className="ground-terminal-container">
        {/* Terminal Header */}
        <div className="ground-terminal-header">
          <Group justify="space-between" wrap="nowrap" align="center">
            <Group gap="xs" wrap="nowrap">
              <ThemeIcon color="cyan" variant="filled" size="md">
                <RadioIcon size={18} />
              </ThemeIcon>
              <div>
                <Group gap="xs" align="center">
                  <Title order={4} className="terminal-title">
                    TERMINAL TACTIQUE SOL
                  </Title>
                  <Badge color="cyan" variant="filled" size="sm">
                    {actor}
                  </Badge>
                </Group>
                <Text size="xs" c="dimmed">
                  Système de Guerre Électronique (EW) — Poste Émetteur
                </Text>
              </div>
            </Group>

            <Group gap="xs" wrap="nowrap">
              <Text ff="monospace" fw={700} size="md" c="cyan.3">
                {clockLabel(start_time, now)}
              </Text>
              {onSwitchToCommand && (
                <Button size="xs" variant="light" color="blue" onClick={onSwitchToCommand}>
                  Vue CMD
                </Button>
              )}
              <Button size="xs" variant="subtle" color="gray" onClick={() => signOut()}>
                Quitter
              </Button>
            </Group>
          </Group>

          {/* Jammer Selector if multiple exist */}
          {jammers.length > 1 && (
            <Group gap="xs" mt="xs">
              <Text size="xs" fw={600}>
                Brouilleur sélectionné :
              </Text>
              <Select
                size="xs"
                w={140}
                data={jammers.map((j) => ({ value: j.id, label: `${j.id} (${j.operator_callsign})` }))}
                value={currentJammer.id}
                onChange={setSelectedJammerId}
                allowDeselect={false}
              />
            </Group>
          )}

          {/* Mode Tabs */}
          <Group gap="xs" mt="sm">
            <Button
              size="xs"
              variant={activeTab === 'terminal' ? 'filled' : 'light'}
              color={activeTab === 'terminal' ? 'cyan' : 'gray'}
              leftSection={<SignalIcon size={14} />}
              onClick={() => setActiveTab('terminal')}
            >
              Commandes & Demandes
            </Button>
            <Button
              size="xs"
              variant={activeTab === 'map' ? 'filled' : 'light'}
              color={activeTab === 'map' ? 'cyan' : 'gray'}
              leftSection={<CrosshairIcon size={14} />}
              onClick={() => setActiveTab('map')}
            >
              Carte Tactique Secteur
            </Button>
          </Group>
        </div>

        {/* Tab 2: Sector Map view */}
        {activeTab === 'map' && (
          <div className="ground-terminal-map-pane">
            <div className="ground-terminal-map-wrap">
              <MapPanel />
            </div>
          </div>
        )}

        {/* Tab 1: Terminal Content */}
        {activeTab === 'terminal' && (
          <ScrollArea className="ground-terminal-scroll">
            <Stack gap="md" p="md">
              {/* Hardware Spec Card */}
              <Card withBorder padding="sm" className="tactical-card">
                <Group justify="space-between" align="center">
                  <div>
                    <Text size="xs" c="dimmed" tt="uppercase" fw={700}>
                      Dispositif Actif
                    </Text>
                    <Group gap="xs" align="baseline">
                      <Text size="lg" fw={800} c="white">
                        {currentJammer.id}
                      </Text>
                      <Badge variant="outline" color="cyan" size="sm">
                        {currentJammer.eirp_w} W EIRP
                      </Badge>
                      {currentJammer.bands.map((b) => (
                        <Badge key={b} variant="dot" color="blue" size="sm">
                          {b}
                        </Badge>
                      ))}
                    </Group>
                  </div>
                  <div style={{ textAlign: 'right' }}>
                    <Text size="xs" c="dimmed">
                      Position GPS
                    </Text>
                    <Text size="xs" ff="monospace" fw={600}>
                      {currentJammer.pos.lat.toFixed(4)}°N, {currentJammer.pos.lon.toFixed(4)}°E
                    </Text>
                  </div>
                </Group>
              </Card>

              {/* Dynamic Live Jammer Status Banner */}
              {zoneState === 'cut' && activeCut ? (
                /* STATE 1: RADIO SILENCE ACTIVE */
                <Card withBorder padding="md" className="status-banner-cut">
                  <Stack gap="xs">
                    <Group justify="space-between" align="center">
                      <Group gap="xs">
                        <ThemeIcon color="red" size="lg" variant="filled" className="pulse-danger">
                          <RadioIcon size={20} />
                        </ThemeIcon>
                        <div>
                          <Text fw={900} size="md" c="red.2" tt="uppercase">
                            SILENCE RADIO ACTIF — BROUILLEUR COUPÉ
                          </Text>
                          <Text size="xs" c="red.1">
                            Coupure effective en cours pour le passage drone
                            {activeCut.requested_for && ` (${activeCut.requested_for})`}
                          </Text>
                        </div>
                      </Group>
                      <Badge color="red" size="lg" variant="filled">
                        COUPURE EN COURS
                      </Badge>
                    </Group>

                    <Divider color="rgba(255,255,255,0.15)" my={4} />

                    <Group justify="space-between" align="baseline">
                      <Text size="xs" c="red.1">
                        Fin de la coupure à {clockLabel(start_time, activeCut.end_s)}
                      </Text>
                      <Text fw={800} ff="monospace" size="lg" c="white">
                        Rallumage auto dans {formatSeconds(activeCut.end_s - now)}
                      </Text>
                    </Group>

                    <Progress
                      value={Math.min(
                        100,
                        Math.max(
                          0,
                          ((now - activeCut.start_s) / (activeCut.end_s - activeCut.start_s)) * 100
                        )
                      )}
                      color="red"
                      size="sm"
                      animated
                    />

                    {/* Emergency override button */}
                    <Button
                      mt="xs"
                      color="red"
                      variant="filled"
                      size="md"
                      fullWidth
                      className="btn-emergency-override"
                      leftSection={<BombIcon size={20} />}
                      onClick={() => setEmergencyCutId(activeCut.id)}
                    >
                      🚨 URGENCE : RALLUMER LE BROUILLEUR (Prise d&apos;obus / Attaque)
                    </Button>
                  </Stack>
                </Card>
              ) : zoneState === 'pending' || nextCut ? (
                /* STATE 2: CUT SCHEDULED / APPROACHING */
                <Card withBorder padding="md" className="status-banner-pending">
                  <Stack gap="xs">
                    <Group justify="space-between" align="center">
                      <Group gap="xs">
                        <ThemeIcon color="orange" size="lg" variant="filled">
                          <ClockIcon size={20} />
                        </ThemeIcon>
                        <div>
                          <Text fw={800} size="md" c="orange.2" tt="uppercase">
                            COUPURE VALIDÉE — EN ATTENTE DE LA FENÊTRE
                          </Text>
                          <Text size="xs" c="orange.1">
                            Émetteur actuellement actif à 100%. Coupure imminente.
                          </Text>
                        </div>
                      </Group>
                      <Badge color="orange" size="lg" variant="filled">
                        {nextCut ? `T-${formatSeconds(nextCut.start_s - now)}` : 'PROGRAMMÉ'}
                      </Badge>
                    </Group>

                    {nextCut && (
                      <Group justify="space-between" align="baseline">
                        <Text size="xs" c="orange.1">
                          Fenêtre : {clockLabel(start_time, nextCut.start_s)} →{' '}
                          {clockLabel(start_time, nextCut.end_s)}
                          {nextCut.requested_for && ` (${nextCut.requested_for})`}
                        </Text>
                        <Button
                          size="compact-xs"
                          variant="light"
                          color="red"
                          onClick={() => setRefusingCutId(nextCut.id)}
                        >
                          Annuler (Prise d&apos;obus)
                        </Button>
                      </Group>
                    )}
                  </Stack>
                </Card>
              ) : (
                /* STATE 3: 100% TRANSMISSION ACTIVE */
                <Card withBorder padding="md" className="status-banner-active">
                  <Group justify="space-between" align="center">
                    <Group gap="xs">
                      <ThemeIcon color="teal" size="lg" variant="filled">
                        <ShieldCheckIcon size={20} />
                      </ThemeIcon>
                      <div>
                        <Text fw={800} size="md" c="teal.2" tt="uppercase">
                          ÉMETTEUR ACTIF — BROUILLAGE EN COURS (100%)
                        </Text>
                        <Text size="xs" c="teal.1">
                          Protection électronique opérationnelle. Bulle de défense active.
                        </Text>
                      </div>
                    </Group>
                    <Badge color="teal" size="lg" variant="filled">
                      ACTIF 100%
                    </Badge>
                  </Group>
                </Card>
              )}

              {/* DEMANDES REÇUES SECTION (Pending incoming requests) */}
              <Card withBorder padding="md" className="tactical-section-card">
                <Group justify="space-between" mb="xs">
                  <Group gap="xs">
                    <ThemeIcon color="yellow" variant="light" size="sm">
                      <BellIcon size={16} />
                    </ThemeIcon>
                    <Title order={5}>DEMANDES DE COUPURE REÇUES</Title>
                  </Group>
                  <Badge color={pendingCuts.length > 0 ? 'yellow' : 'gray'}>
                    {pendingCuts.length} en attente
                  </Badge>
                </Group>

                {pendingCuts.length === 0 ? (
                  <Text size="sm" c="dimmed" fs="italic">
                    Aucune nouvelle demande de coupure en attente pour ce brouilleur.
                  </Text>
                ) : (
                  <Stack gap="sm">
                    {pendingCuts.map((c) => {
                      const durationMin = Math.round((c.end_s - c.start_s) / 60)
                      const startsInS = c.start_s - now

                      return (
                        <Card key={c.id} withBorder padding="sm" className="request-card">
                          <Stack gap="xs">
                            <Group justify="space-between" wrap="nowrap">
                              <div>
                                <Group gap="xs" align="center">
                                  <Text fw={700} size="sm" c="white">
                                    Demande #{c.id}
                                  </Text>
                                  <Badge color="orange" size="xs">
                                    En attente de validation sol
                                  </Badge>
                                </Group>
                                <Text size="xs" c="dimmed">
                                  Demandé par <span style={{ color: '#fff' }}>{c.requested_by}</span>
                                  {c.requested_for && ` pour la mission ${c.requested_for}`}
                                </Text>
                              </div>
                              <div style={{ textAlign: 'right' }}>
                                <Text size="xs" fw={700} c="cyan.3">
                                  {clockLabel(start_time, c.start_s)} → {clockLabel(start_time, c.end_s)}
                                </Text>
                                <Text size="xs" c="dimmed">
                                  Durée : {durationMin} min
                                </Text>
                              </div>
                            </Group>

                            <Group justify="space-between" align="center">
                              <Text size="xs" fw={600} c={startsInS > 0 ? 'orange.3' : 'red.4'}>
                                {startsInS > 0
                                  ? `Début prévu dans ${formatSeconds(startsInS)}`
                                  : '⚠️ FENÊTRE IMMÉDIATE'}
                              </Text>

                              {/* Action buttons: Oui (Valider/Coupure effective) / Non (Refuser) */}
                              <Group gap="xs" wrap="nowrap">
                                <Button
                                  size="xs"
                                  color="red"
                                  variant="light"
                                  leftSection={<XIcon size={14} />}
                                  onClick={() => setRefusingCutId(c.id)}
                                >
                                  Refuser la coupure
                                </Button>
                                <Button
                                  size="xs"
                                  color="green"
                                  variant="filled"
                                  leftSection={<CheckIcon size={14} />}
                                  onClick={() => send({ type: 'cut.confirm', cut: c.id })}
                                >
                                  Coupure effective (Valider)
                                </Button>
                              </Group>
                            </Group>
                          </Stack>
                        </Card>
                      )
                    })}
                  </Stack>
                )}
              </Card>

              {/* CONFIRMED CUTS SECTION */}
              <Card withBorder padding="md" className="tactical-section-card">
                <Group justify="space-between" mb="xs">
                  <Group gap="xs">
                    <ThemeIcon color="green" variant="light" size="sm">
                      <CheckIcon size={16} />
                    </ThemeIcon>
                    <Title order={5}>COUPURES VALIDÉES & PLANIFIÉES</Title>
                  </Group>
                  <Badge color={confirmedCuts.length > 0 ? 'green' : 'gray'}>
                    {confirmedCuts.length}
                  </Badge>
                </Group>

                {confirmedCuts.length === 0 ? (
                  <Text size="sm" c="dimmed" fs="italic">
                    Aucune coupure validée actuellement.
                  </Text>
                ) : (
                  <Stack gap="xs">
                    {confirmedCuts.map((c) => {
                      const isOngoing = c.start_s <= now && now < c.end_s
                      const isPast = now >= c.end_s

                      return (
                        <Group key={c.id} justify="space-between" wrap="nowrap" className="tactical-subcard" p="xs">
                          <div>
                            <Group gap="xs" align="center">
                              <Text size="sm" fw={600}>
                                {clockLabel(start_time, c.start_s)} – {clockLabel(start_time, c.end_s)}
                              </Text>
                              <Badge color={isOngoing ? 'red' : isPast ? 'gray' : 'green'} size="xs">
                                {isOngoing ? 'EN COURS' : isPast ? 'TERMINÉE' : 'CONFIRMÉE'}
                              </Badge>
                            </Group>
                            <Text size="xs" c="dimmed">
                              {c.id} · Par {c.requested_by}
                              {c.requested_for && ` pour ${c.requested_for}`}
                            </Text>
                          </div>

                          <Group gap="xs">
                            {!isPast && (
                              <Button
                                size="compact-xs"
                                color="red"
                                variant="subtle"
                                onClick={() => setRefusingCutId(c.id)}
                              >
                                Annuler (Prise d&apos;obus)
                              </Button>
                            )}
                          </Group>
                        </Group>
                      )
                    })}
                  </Stack>
                )}
              </Card>

              {/* DRONE APPROACH & SECTOR SITUATION */}
              <Card withBorder padding="md" className="tactical-section-card">
                <Group justify="space-between" mb="xs">
                  <Group gap="xs">
                    <ThemeIcon color="cyan" variant="light" size="sm">
                      <DroneIcon size={16} />
                    </ThemeIcon>
                    <Title order={5}>APPROCHE DRONE & SITUATION DU SECTEUR</Title>
                  </Group>
                </Group>

                {crossingMissions.length === 0 ? (
                  <Text size="sm" c="dimmed" fs="italic">
                    Aucun vol ami ne traverse actuellement ce secteur de brouillage.
                  </Text>
                ) : (
                  <Stack gap="xs">
                    {crossingMissions.map((m) => {
                      const missionScenario = snapshot.scenario.missions.find(
                        (sc) => sc.id === m.constraints.mission
                      )
                      const pilotCallsign = missionScenario?.pilot_callsign ?? 'Inconnu'
                      const route = m.plan?.optimised_route ?? m.plan?.baseline_route ?? []
                      const currentPos = route.length > 0 ? positionAt(route, now) : null
                      const dronePos = m.drone?.position ?? currentPos?.pos
                      const distM = dronePos ? Math.round(distanceM(dronePos, currentJammer.pos)) : null

                      return (
                        <Card key={m.constraints.mission} withBorder padding="xs" className="tactical-subcard">
                          <Group justify="space-between" align="center">
                            <div>
                              <Group gap="xs" align="center">
                                <Text fw={700} size="sm">
                                  {m.constraints.mission}
                                </Text>
                                <Badge color="cyan" size="xs">
                                  Pilote : {pilotCallsign}
                                </Badge>
                                <Badge color={m.status === 'flying' ? 'green' : 'blue'} size="xs">
                                  {m.status.toUpperCase()}
                                </Badge>
                              </Group>
                              <Text size="xs" c="dimmed">
                                Action : {currentPos?.action ?? 'au sol'} · Batterie :{' '}
                                {m.drone ? `${m.drone.battery_used_pct}% consommée` : '100%'}
                              </Text>
                            </div>

                            {distM !== null && (
                              <div style={{ textAlign: 'right' }}>
                                <Text size="xs" c="dimmed">
                                  Distance au brouilleur
                                </Text>
                                <Text size="sm" fw={700} c="cyan.3">
                                  {distM > 1000 ? `${(distM / 1000).toFixed(1)} km` : `${distM} m`}
                                </Text>
                              </div>
                            )}
                          </Group>
                        </Card>
                      )
                    })}
                  </Stack>
                )}
              </Card>

              {/* TACTICAL COMMS / TRANSMISSIONS FEED */}
              <Card withBorder padding="md" className="tactical-section-card">
                <Group justify="space-between" mb="xs">
                  <Group gap="xs">
                    <ThemeIcon color="blue" variant="light" size="sm">
                      <BellIcon size={16} />
                    </ThemeIcon>
                    <Title order={5}>JOURNAL DES TRANSMISSIONS TACTIQUES</Title>
                  </Group>
                  <Text size="xs" c="dimmed">
                    Messages reçus
                  </Text>
                </Group>

                <Stack gap={6}>
                  {feed.length === 0 ? (
                    <Text size="xs" c="dimmed" fs="italic">
                      Aucune transmission reçue.
                    </Text>
                  ) : (
                    feed.slice(0, 5).map((f) => (
                      <Group key={f.id} justify="space-between" wrap="nowrap" align="flex-start">
                        <Text size="xs" c="dimmed" ff="monospace" style={{ flexShrink: 0 }}>
                          {clockLabel(start_time, f.atS)}
                        </Text>
                        <Text size="xs" style={{ flexGrow: 1 }}>
                          {f.notification.text}
                        </Text>
                      </Group>
                    ))
                  )}
                </Stack>
              </Card>
            </Stack>
          </ScrollArea>
        )}
      </div>
    </div>
  )
}
