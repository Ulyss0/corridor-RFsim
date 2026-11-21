// EW cut form, over the sidebar panel: asks a friendly jammer for a cut (`cut.schedule`), for a
// mission or not. Its operator confirms it; the form closes when the cut is scheduled.
import { Button, Group, NumberInput, Select, Stack, Text } from '@mantine/core'
import { useState } from 'react'
import { send } from '../../api/client'
import { clockLabel } from '../../domain/view'
import { useLiveSimTime } from '../../hooks/useSimNow'
import { useStore, type CutDraft } from '../../store'
import { CutRow, PanelHeader, SectionTitle } from './parts'

const NO_MISSION = '-'

export function CutForm() {
  const draft = useStore((s) => s.cutDraft)
  if (!draft) return null
  // Keyed: a form opened from elsewhere starts over with its own fields.
  return <Form key={`${draft.jammer}/${draft.mission}`} draft={draft} />
}

function Form({ draft }: { readonly draft: CutDraft }) {
  const snapshot = useStore((s) => s.snapshot)
  const missions = useStore((s) => s.missions)
  const cuts = useStore((s) => s.cuts)
  const close = useStore((s) => s.closeCut)
  const now = useLiveSimTime(1)
  // A cut asked from a mission starts on the first jammer that concerns it.
  const fromMission = draft.mission ? missions[draft.mission]?.constraints.jammers[0] : undefined
  const [jammer, setJammer] = useState<string | null>(draft.jammer ?? fromMission ?? null)
  const [mission, setMission] = useState<string>(draft.mission ?? NO_MISSION)
  const [startMin, setStartMin] = useState<number | string>('')
  const [durationMin, setDurationMin] = useState<number | string>(8)
  if (!snapshot) return null
  const { friendly_jammers: jammers, start_time } = snapshot.scenario
  const label = (t: number) => clockLabel(start_time, t)

  const selected = jammer ?? jammers[0]?.id ?? null
  const operator = jammers.find((j) => j.id === selected)?.operator_callsign
  // Only a mission the jammer concerns can be the reason for its cut.
  const concerned = Object.values(missions)
    .filter((m) => selected && m.constraints.jammers.includes(selected))
    .map((m) => m.constraints.mission)
  const forMission = concerned.includes(mission) ? mission : null
  const defaultStart = Math.ceil(now / 60) + 2
  const start = typeof startMin === 'number' ? startMin : defaultStart
  const duration = typeof durationMin === 'number' ? durationMin : 8
  const existing = Object.values(cuts)
    .filter((c) => c.jammer === selected)
    .sort((a, b) => a.start_s - b.start_s)

  const submit = () => {
    if (!selected) return
    send({
      type: 'cut.schedule',
      jammer: selected,
      start_s: start * 60,
      end_s: (start + duration) * 60,
      mission: forMission,
    })
  }

  return (
    <Stack gap="sm">
      <PanelHeader title="EW cut" onClose={close} />
      <Select
        label="Jammer"
        description={operator && `Operator ${operator} confirms the cut.`}
        data={jammers.map((j) => j.id)}
        value={selected}
        onChange={setJammer}
        allowDeselect={false}
      />
      <Select
        label="For mission"
        data={[{ value: NO_MISSION, label: 'None' }, ...concerned.map((id) => ({ value: id, label: id }))]}
        value={forMission ?? NO_MISSION}
        onChange={(v) => setMission(v ?? NO_MISSION)}
        allowDeselect={false}
      />
      <Group gap="xs" grow>
        <NumberInput label="Start (min)" min={0} value={startMin} placeholder={String(defaultStart)} onChange={setStartMin} />
        <NumberInput label="Length (min)" min={1} value={durationMin} onChange={setDurationMin} />
      </Group>
      <Text size="sm">
        Jammer off <b>{label(start * 60)}</b> → <b>{label((start + duration) * 60)}</b>
      </Text>

      {existing.length > 0 && (
        <Stack gap={6}>
          <SectionTitle>Already on {selected}</SectionTitle>
          {existing.map((c) => (
            <CutRow key={c.id} cut={c} showJammer={false} />
          ))}
        </Stack>
      )}

      <Group justify="flex-end" gap="xs">
        <Button variant="subtle" color="gray" onClick={close}>Cancel</Button>
        <Button color="orange" disabled={!selected} onClick={submit}>Ask for the cut</Button>
      </Group>
    </Stack>
  )
}
