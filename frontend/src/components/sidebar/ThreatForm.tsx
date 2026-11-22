// Threat creation form for the command role: adds an enemy radar, jammer or zone to avoid (`threat.add`).
// Position can be typed, pasted or picked on the map with a click.
import {
  ActionIcon,
  Badge,
  Button,
  Chip,
  Group,
  NumberInput,
  SegmentedControl,
  Stack,
  Text,
  TextInput,
  Tooltip,
} from '@mantine/core'
import { useState } from 'react'
import { send } from '../../api/client'
import type { Position } from '../../api/protocol'
import { formatPosition, parsePosition } from '../../domain/view'
import { useStore, type ThreatKind } from '../../store'
import { THREAT_COLOR } from '../colors'
import { CrosshairIcon } from '../Icons'
import { PanelHeader } from './parts'

const KIND_DATA = [
  { value: 'radar', label: 'Radar' },
  { value: 'jammer', label: 'Brouilleur' },
  { value: 'avoid', label: 'Zone à éviter' },
]

const RADAR_PRESETS = [1000, 2000, 3000, 5000]
const JAMMER_PRESETS = [10, 25, 50, 100]
const AVOID_PRESETS = [500, 1000, 1500, 2500]
const AVAILABLE_BANDS = ['2.4GHz', '5.8GHz']

export function ThreatForm() {
  const draft = useStore((s) => s.threatDraft)
  const setDraft = useStore((s) => s.setThreatDraft)
  const close = useStore((s) => s.closeThreat)

  if (!draft.open) return null

  const { kind, pos, radius_m, eirp_w, bands, position_error_m, picking } = draft

  const ready =
    pos !== null &&
    (kind !== 'jammer' || bands.length > 0) &&
    (kind === 'jammer' ? eirp_w > 0 : radius_m > 0)

  const submit = () => {
    if (!ready || !pos) return
    send({
      type: 'threat.add',
      kind,
      pos,
      radius_m: kind === 'jammer' ? undefined : radius_m,
      eirp_w: kind === 'jammer' ? eirp_w : undefined,
      bands: kind === 'jammer' && bands.length > 0 ? (bands as [string, ...string[]]) : undefined,
      position_error_m: kind === 'avoid' ? 0 : position_error_m,
    })
    close()
  }

  return (
    <Stack gap="sm">
      <PanelHeader
        title="Nouvelle menace"
        badge={<Badge color={THREAT_COLOR[kind]}>{kind}</Badge>}
        onClose={close}
      />

      <div>
        <Text size="xs" fw={500} mb={4} c="dimmed">
          Type de menace
        </Text>
        <SegmentedControl
          fullWidth
          size="xs"
          data={KIND_DATA}
          value={kind}
          onChange={(v) => setDraft({ kind: v as ThreatKind })}
        />
      </div>

      <PointField
        label="Position de la menace"
        hint="Cliquez sur la carte ou saisissez les coordonnées"
        value={pos}
        picking={picking}
        onChange={(p) => setDraft({ pos: p })}
        onPick={() => setDraft({ picking: !picking })}
      />

      {kind === 'radar' && (
        <>
          <NumberInput
            label="Rayon de détection (m)"
            description="Portée nominale du radar en mètres"
            min={100}
            step={100}
            value={radius_m}
            onChange={(v) => setDraft({ radius_m: typeof v === 'number' ? v : 2000 })}
          />
          <div>
            <Text size="xs" c="dimmed" mb={4}>
              Préréglages :
            </Text>
            <Group gap={6}>
              {RADAR_PRESETS.map((p) => (
                <Chip
                  key={p}
                  size="xs"
                  checked={radius_m === p}
                  onChange={() => setDraft({ radius_m: p })}
                >
                  {p >= 1000 ? `${p / 1000} km` : `${p} m`}
                </Chip>
              ))}
            </Group>
          </div>
          <NumberInput
            label="Incertitude de position (m)"
            description="Marge d'erreur sur la position GPS estimée"
            min={0}
            step={50}
            value={position_error_m}
            onChange={(v) => setDraft({ position_error_m: typeof v === 'number' ? v : 0 })}
          />
        </>
      )}

      {kind === 'jammer' && (
        <>
          <NumberInput
            label="Puissance d'émission (W EIRP)"
            description="Puissance apparente rayonnée du brouilleur ennemi"
            min={1}
            step={5}
            value={eirp_w}
            onChange={(v) => setDraft({ eirp_w: typeof v === 'number' ? v : 25 })}
          />
          <div>
            <Text size="xs" c="dimmed" mb={4}>
              Préréglages de puissance :
            </Text>
            <Group gap={6}>
              {JAMMER_PRESETS.map((p) => (
                <Chip
                  key={p}
                  size="xs"
                  checked={eirp_w === p}
                  onChange={() => setDraft({ eirp_w: p })}
                >
                  {p} W
                </Chip>
              ))}
            </Group>
          </div>
          <div>
            <Text size="xs" fw={500} mb={4}>
              Bandes de fréquences brouillées
            </Text>
            <Group gap={6}>
              {AVAILABLE_BANDS.map((b) => (
                <Chip
                  key={b}
                  size="xs"
                  checked={bands.includes(b)}
                  onChange={() => {
                    const next = bands.includes(b)
                      ? bands.filter((x) => x !== b)
                      : [...bands, b]
                    setDraft({ bands: next })
                  }}
                >
                  {b}
                </Chip>
              ))}
            </Group>
            {bands.length === 0 && (
              <Text size="xs" c="red" mt={2}>
                Sélectionnez au moins une bande
              </Text>
            )}
          </div>
          <NumberInput
            label="Incertitude de position (m)"
            description="Marge d'erreur sur la position GPS estimée"
            min={0}
            step={50}
            value={position_error_m}
            onChange={(v) => setDraft({ position_error_m: typeof v === 'number' ? v : 0 })}
          />
        </>
      )}

      {kind === 'avoid' && (
        <>
          <NumberInput
            label="Rayon de la zone à éviter (m)"
            description="Rayon de la zone déconseillée aux vols"
            min={100}
            step={100}
            value={radius_m}
            onChange={(v) => setDraft({ radius_m: typeof v === 'number' ? v : 1500 })}
          />
          <div>
            <Text size="xs" c="dimmed" mb={4}>
              Préréglages de rayon :
            </Text>
            <Group gap={6}>
              {AVOID_PRESETS.map((p) => (
                <Chip
                  key={p}
                  size="xs"
                  checked={radius_m === p}
                  onChange={() => setDraft({ radius_m: p })}
                >
                  {p >= 1000 ? `${p / 1000} km` : `${p} m`}
                </Chip>
              ))}
            </Group>
          </div>
        </>
      )}

      <Group justify="flex-end" gap="xs" mt="sm">
        <Button variant="subtle" color="gray" onClick={close}>
          Annuler
        </Button>
        <Button color="red" disabled={!ready} onClick={submit}>
          Ajouter la menace
        </Button>
      </Group>
    </Stack>
  )
}

interface PointFieldProps {
  readonly label: string
  readonly hint?: string
  readonly value: Position | null
  readonly picking: boolean
  readonly onChange: (p: Position | null) => void
  readonly onPick: () => void
}

function PointField({ label, hint, value, picking, onChange, onPick }: PointFieldProps) {
  const [text, setText] = useState(value ? formatPosition(value) : '')
  const [shown, setShown] = useState(value)

  if (value !== shown) {
    setShown(value)
    const typed = parsePosition(text)
    if (!(typed && value && typed.lat === value.lat && typed.lon === value.lon)) {
      setText(value ? formatPosition(value) : '')
    }
  }
  const invalid = text.trim() !== '' && parsePosition(text) === null

  return (
    <TextInput
      label={label}
      description={picking ? 'Cliquez sur la carte…' : hint}
      placeholder="48.14500, 11.51300"
      value={text}
      onChange={(e) => {
        setText(e.currentTarget.value)
        onChange(parsePosition(e.currentTarget.value))
      }}
      error={invalid ? 'Format "lat, lon" en degrés décimaux' : undefined}
      rightSection={
        <Tooltip label={picking ? 'Arrêter la sélection' : 'Pointer sur la carte'} openDelay={300}>
          <ActionIcon
            variant={picking ? 'filled' : 'light'}
            color={picking ? 'red' : 'blue'}
            aria-label={`Sélectionner ${label.toLowerCase()} sur la carte`}
            onClick={onPick}
          >
            <CrosshairIcon size={14} />
          </ActionIcon>
        </Tooltip>
      }
    />
  )
}
